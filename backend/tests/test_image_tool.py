"""Units of images from chat (docs/briefs/image-generation.md, section 18.2): the create_image schema and argument
rules, which image models are supported, llm.generate_image's request, errors, usage and cost, the race-safe
reservation of the monthly image count, and the unusable marks."""

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import litellm
import pytest
from conftest import ApiClient, FakeLLM, fake_image, refusal
from markdown_it import MarkdownIt
from sqlalchemy import select, update

from app.config import settings
from app.db import SessionLocal, make_engine
from app.models import LLMModel, UsageEvent, User
from app.services import filegen, image_tool, llm, storage
from app.services.billing import entitlements
from app.services.billing.entitlements import Entitlements
from app.services.document_tool import ToolInputError
from app.services.usage import images_in_window, month_start

CreateUser = Callable[..., Awaitable[ApiClient]]

NOT_GPT_IMAGE = "This isn't a GPT image model. dall-e models aren't supported: their sizes and qualities differ."
OTHER_PROVIDER = (
    "Image models must be OpenAI or Azure OpenAI GPT image models, e.g. openai/gpt-image-2. Other providers can't be "
    "stopped or cost-capped reliably yet."
)
UNKNOWN = "LiteLLM doesn't recognize this model name."


def image_model(litellm_model: str = "openai/gpt-image-2", **fields: Any) -> LLMModel:
    return LLMModel(
        id=fields.pop("id", 99),
        display_name="Images",
        litellm_model=litellm_model,
        kind="image",
        enabled=True,
        is_default=True,
        updated_at=fields.pop("updated_at", datetime(2026, 10, 1, tzinfo=timezone.utc)),
        **fields,
    )


def arguments(**fields: Any) -> str:
    return json.dumps({"prompt": "A fox head logo, flat vector, orange.", "title": "Fox logo", **fields})


async def generate(model: LLMModel | None = None, **options: Any) -> llm.ImageReply:
    settings_ = {"size": "1024x1024", "quality": "medium", "output_format": "png", "timeout": 30.0} | options
    return await llm.generate_image(model or image_model(), "A fox", **settings_)


# ---------------- The tool's schema and arguments ----------------


def test_the_tool_schema() -> None:
    spec = image_tool.tool_spec()
    function = spec["function"]
    assert spec["type"] == "function" and function["name"] == "create_image" == image_tool.TOOL_NAME
    parameters = function["parameters"]
    assert parameters["required"] == ["prompt", "title", "alt_text"]
    assert list(parameters["properties"]) == ["prompt", "title", "alt_text", "aspect", "quality"]
    assert parameters["properties"]["aspect"]["enum"] == ["square", "landscape", "portrait"]
    assert parameters["properties"]["quality"]["enum"] == ["low", "medium", "high"]
    assert "eager_input_streaming" not in spec
    assert all(set(value) <= {"type", "enum", "description"} for value in parameters["properties"].values())


def test_valid_arguments_get_their_size() -> None:
    args = image_tool.parse_arguments(
        arguments(alt_text="An orange fox head.", aspect="landscape", quality="low", title="  Fox\nlogo  ")
    )
    assert args == image_tool.CreateImageArgs(
        prompt="A fox head logo, flat vector, orange.",
        title="Fox logo",
        alt_text="An orange fox head.",
        aspect="landscape",
        size="1536x1024",
        width=1536,
        height=1024,
        quality="low",
        quality_lowered=False,
    )


@pytest.mark.parametrize(
    ("aspect", "expected"),
    [
        (None, "square"),
        ("", "square"),
        ("SQUARE ", "square"),
        ("1:1", "square"),
        ("3:2", "landscape"),
        ("16:9", "landscape"),
        ("wide", "landscape"),
        ("Horizontal", "landscape"),
        ("2:3", "portrait"),
        ("9:16", "portrait"),
        ("tall", "portrait"),
        ("vertical", "portrait"),
    ],
)
def test_aspect_aliases(aspect: str | None, expected: str) -> None:
    fields = {} if aspect is None else {"aspect": aspect}
    args = image_tool.parse_arguments(arguments(**fields))
    assert args.aspect == expected
    assert (args.width, args.height) == {"square": (1024, 1024), "landscape": (1536, 1024), "portrait": (1024, 1536)}[
        expected
    ]


def test_missing_title_and_alt_text_come_from_the_prompt() -> None:
    prompt = "A watercolor illustration of a lighthouse at dawn with seagulls circling above the rocky shore"
    args = image_tool.parse_arguments(json.dumps({"prompt": prompt, "title": "  ", "alt_text": "​"}))
    assert args.title == "A watercolor illustration of a lighthouse at dawn with"
    assert len(args.title) <= 60 and prompt.startswith(args.title)
    assert args.alt_text == args.title
    long_alt = image_tool.parse_arguments(arguments(alt_text="w" * 400)).alt_text
    assert long_alt == "w" * 300


@pytest.mark.parametrize(
    ("maximum", "asked", "expected", "lowered"),
    [
        ("medium", None, "medium", False),
        ("low", None, "low", False),
        ("high", None, "medium", False),
        ("medium", "HIGH", "medium", True),
        ("low", "medium", "low", True),
        ("high", "high", "high", False),
        ("medium", "low", "low", False),
    ],
)
def test_quality_is_clamped_to_the_maximum(
    monkeypatch: pytest.MonkeyPatch, maximum: str, asked: str | None, expected: str, lowered: bool
) -> None:
    monkeypatch.setattr(settings, "image_max_quality", maximum)
    fields = {} if asked is None else {"quality": asked}
    args = image_tool.parse_arguments(arguments(**fields))
    assert (args.quality, args.quality_lowered) == (expected, lowered)


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        ("[1, 2]", "The arguments weren't valid JSON."),
        ('{"prompt": "x"', "The arguments weren't valid JSON."),
        ('{"prompt": NaN}', "The arguments weren't valid JSON."),
        (json.dumps({"title": "x"}), "prompt is required"),
        (json.dumps({"prompt": "  \x00 "}), "prompt is required"),
        (json.dumps({"prompt": 5}), "prompt is required"),
        (json.dumps({"prompt": "p" * 4001}), "The prompt is 4,001 characters; the limit is 4,000. Shorten it."),
        (arguments(aspect="panorama"), "aspect must be one of square, landscape, portrait"),
        (arguments(aspect=3), "aspect must be one of square, landscape, portrait"),
        (arguments(quality="ultra"), "quality must be one of low, medium, high"),
        (json.dumps({"prompt": "p", "pad": "x" * 70_000}), "The arguments are too large."),
    ],
)
def test_invalid_arguments(raw: str, error: str) -> None:
    with pytest.raises(ToolInputError) as caught:
        image_tool.parse_arguments(raw)
    assert str(caught.value) == error


def test_a_4000_character_prompt_is_accepted_and_cleaned() -> None:
    args = image_tool.parse_arguments(json.dumps({"prompt": " a\x0bb " + "p" * 3996, "title": "t"}))
    assert args.prompt.startswith("a\nb") and len(args.prompt) == 4000


@pytest.mark.parametrize("title", ["Logo [v2]", "back\\slash *bold* _x_ `code`", "]](image:1)", "a ![b](image:2) c"])
def test_the_markdown_reference_parses_back_as_one_image_with_the_title(title: str) -> None:
    reference = filegen.image_markdown(title, 812)
    tokens = MarkdownIt("commonmark").parseInline(reference)[0].children or []
    images = [token for token in tokens if token.type == "image"]
    assert len(images) == 1 and images[0].attrs["src"] == "image:812"
    assert "".join(child.content for child in images[0].children or []) == title
    assert filegen.image_refs(reference) == [812]


# ---------------- Supported image models (I21) ----------------


@pytest.mark.parametrize(
    ("litellm_model", "api_base", "problem"),
    [
        ("openai/gpt-image-2", None, None),
        ("gpt-image-2", None, None),
        ("openai/gpt-image-1-mini", None, None),
        ("azure/my-images", None, None),
        ("openai/flux", "http://127.0.0.1:9/v1", None),
        ("openai/dall-e-3", None, NOT_GPT_IMAGE),
        ("gemini/imagen-4", None, OTHER_PROVIDER),
        ("vertex_ai/imagen-4", None, OTHER_PROVIDER),
        ("openrouter/openai/gpt-image-1", None, OTHER_PROVIDER),
        ("nosuchprovider/model", None, UNKNOWN),
    ],
)
def test_image_model_problem(litellm_model: str, api_base: str | None, problem: str | None) -> None:
    assert llm.image_model_problem(litellm_model, api_base) == problem


# ---------------- llm.generate_image ----------------


async def test_the_request_for_an_openai_model(fake_llm: FakeLLM) -> None:
    reply = await generate(timeout=42.0)
    [call] = fake_llm.image_calls
    assert call == {
        "model": "openai/gpt-image-2",
        "prompt": "A fox",
        "n": 1,
        "size": "1024x1024",
        "quality": "medium",
        "output_format": "png",
        "timeout": 72.0,
    }
    assert reply.data == fake_image(64, 64) and reply.revised_prompt is None
    assert (reply.usage.prompt_tokens, reply.usage.completion_tokens, reply.usage.estimated) == (50, 272, False)
    assert reply.usage.cost_usd == pytest.approx(50 * 5e-6 + 272 * 3e-5)


@pytest.mark.parametrize("output_format", ["jpeg", "webp"])
async def test_jpeg_and_webp_ask_for_compression_90(fake_llm: FakeLLM, output_format: str) -> None:
    await generate(image_model("azure/my-images", api_key_env="AZURE_API_KEY"), output_format=output_format)
    [call] = fake_llm.image_calls
    assert call["model"] == "azure/my-images" and call["api_key"] == os.environ["AZURE_API_KEY"]
    assert (call["output_format"], call["output_compression"]) == (output_format, 90)
    assert "response_format" not in call and "max_retries" not in call


@pytest.fixture(autouse=True)
def azure_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_API_KEY", "test-azure-key")


async def test_a_custom_host_gets_a_placeholder_key(fake_llm: FakeLLM) -> None:
    await generate(image_model("openai/flux", api_base="http://127.0.0.1:9/v1"))
    [call] = fake_llm.image_calls
    assert (call["api_base"], call["api_key"]) == ("http://127.0.0.1:9/v1", "unused")


async def test_an_unsupported_model_is_refused_without_a_call(fake_llm: FakeLLM) -> None:
    with pytest.raises(llm.ImageCallError) as caught:
        await generate(image_model("openai/dall-e-3"))
    assert str(caught.value) == NOT_GPT_IMAGE
    assert caught.value.unusable and not caught.value.billed and caught.value.usage is None
    assert fake_llm.image_calls == []


async def test_a_missing_key_is_an_error_without_a_call(fake_llm: FakeLLM) -> None:
    with pytest.raises(llm.ImageCallError) as caught:
        await generate(image_model(api_key_env="MISSING_IMAGE_API_KEY"))
    assert "MISSING_IMAGE_API_KEY" in str(caught.value) and not caught.value.billed
    assert fake_llm.image_calls == []


@pytest.mark.parametrize("code", ["moderation_blocked", "content_policy_violation"])
async def test_refusals_are_recognized(fake_llm: FakeLLM, code: str) -> None:
    fake_llm.image_error = refusal(code)
    with pytest.raises(llm.ImageCallError) as caught:
        await generate()
    error = caught.value
    assert str(error) == "The image model's safety system declined this request, so no image was generated."
    assert error.refused and not error.billed and not error.unusable and error.usage is None


async def test_a_message_naming_the_safety_system_is_a_refusal(fake_llm: FakeLLM) -> None:
    fake_llm.image_error = litellm.BadRequestError(
        message="Your request was rejected by the safety system.", model="gpt-image-2", llm_provider="openai"
    )
    with pytest.raises(llm.ImageCallError) as caught:
        await generate()
    assert caught.value.refused


async def test_timeouts_may_have_been_billed(fake_llm: FakeLLM) -> None:
    fake_llm.image_error = litellm.Timeout(message="slow", model="gpt-image-2", llm_provider="openai")
    with pytest.raises(llm.ImageCallError) as caught:
        await generate(timeout=60.0)
    assert str(caught.value) == "Generating the image took longer than 60 seconds, so it was abandoned."
    assert caught.value.billed and caught.value.usage is None and not caught.value.unusable

    fake_llm.image_error, fake_llm.image_delay = None, 5.0
    started = asyncio.get_running_loop().time()
    with pytest.raises(llm.ImageCallError) as caught:
        await generate(timeout=0.2)
    assert asyncio.get_running_loop().time() - started < 2
    assert caught.value.billed and "longer than 0 seconds" in str(caught.value)


async def test_rate_limits_and_other_errors_are_not_billed(fake_llm: FakeLLM) -> None:
    fake_llm.image_error = litellm.RateLimitError(message="slow down", model="gpt-image-2", llm_provider="openai")
    with pytest.raises(llm.ImageCallError) as caught:
        await generate()
    assert str(caught.value) == "The image model is busy right now (rate limited), so no image was generated."
    assert caught.value.rate_limited and not caught.value.billed

    fake_llm.image_error = litellm.AuthenticationError(message="bad key", model="gpt-image-2", llm_provider="openai")
    with pytest.raises(llm.ImageCallError) as caught:
        await generate()
    assert str(caught.value) == (
        "Image generation failed: The model provider rejected the API key. Check the provider key in .env."
    )
    assert not (caught.value.billed or caught.value.refused or caught.value.unusable)


async def test_permission_denied_carries_the_organization_hint_for_openai(fake_llm: FakeLLM) -> None:
    response = httpx.Response(403, request=httpx.Request("POST", "https://api.openai.com/v1/images/generations"))
    fake_llm.image_error = litellm.PermissionDeniedError(
        message="organization must be verified", llm_provider="openai", model="gpt-image-2", response=response
    )
    access = "The model provider refused access to this model. Check the account's access with the provider."
    with pytest.raises(llm.ImageCallError) as caught:
        await generate()
    assert str(caught.value) == (
        f"Image generation failed: {access} OpenAI requires a verified organization for its image models."
    )
    with pytest.raises(llm.ImageCallError) as caught:
        await generate(image_model("azure/my-images", api_key_env="AZURE_API_KEY"))
    assert str(caught.value) == f"Image generation failed: {access}"


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        ({"image_url_only": True}, "The image model returned no image data."),
        ({"image_reply": b""}, "The image model returned no image data."),
        ({"image_reply": b"\x00" * (32 * 1024 * 1024 + 16)}, "larger than 32 MB"),
    ],
)
async def test_responses_without_usable_data_are_billed_and_unusable(
    fake_llm: FakeLLM, caplog: pytest.LogCaptureFixture, setup: dict[str, Any], message: str
) -> None:
    for name, value in setup.items():
        setattr(fake_llm, name, value)
    with caplog.at_level(logging.WARNING, logger="app.services.llm"):
        with pytest.raises(llm.ImageCallError) as caught:
            await generate()
    error = caught.value
    assert message in str(error) and error.billed and error.unusable
    assert error.usage is not None and error.usage.completion_tokens == 272
    assert "example.invalid" not in caplog.text  # never the URL


async def test_base64_that_doesnt_decode_is_not_an_image(fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(**kwargs: Any) -> Any:
        return litellm.ImageResponse(created=1, data=[{"b64_json": "not base64!"}])

    monkeypatch.setattr(litellm, "aimage_generation", broken)
    with pytest.raises(llm.ImageCallError) as caught:
        await generate()
    assert str(caught.value) == "The image model returned data that isn't a PNG, JPEG or WebP image."
    assert caught.value.billed and caught.value.unusable
    usage = caught.value.usage
    assert usage is not None and usage.estimated and usage.completion_tokens == 1056  # no usage reported: estimated


async def test_empty_data_is_no_image(monkeypatch: pytest.MonkeyPatch) -> None:
    async def empty(**kwargs: Any) -> Any:
        return litellm.ImageResponse(created=1, data=[])

    monkeypatch.setattr(litellm, "aimage_generation", empty)
    with pytest.raises(llm.ImageCallError) as caught:
        await generate()
    assert str(caught.value) == "The image model returned no image data." and caught.value.unusable


async def test_usage_cost_and_revised_prompt(fake_llm: FakeLLM, caplog: pytest.LogCaptureFixture) -> None:
    fake_llm.image_revised_prompt = "  A fox, revised  "
    priced = image_model(input_cost_per_mtok=10.0, output_cost_per_mtok=40.0)
    reply = await generate(priced)
    assert reply.revised_prompt == "A fox, revised"
    assert reply.usage.cost_usd == pytest.approx((50 * 10 + 272 * 40) / 1_000_000)

    mini = await generate(image_model("openai/gpt-image-1-mini"))
    assert mini.usage.cost_usd == pytest.approx(0.002276)

    fake_llm.image_revised_prompt = "r" * 5000
    with caplog.at_level(logging.WARNING, logger="app.services.llm"):
        first = await generate(image_model("openai/flux", api_base="http://127.0.0.1:9/v1", id=5))
        second = await generate(image_model("openai/flux", api_base="http://127.0.0.1:9/v1", id=5))
    assert first.usage.cost_usd == second.usage.cost_usd == 0.0
    assert caplog.text.count("No price for the image model openai/flux") == 1
    assert first.revised_prompt == "r" * 4000

    fake_llm.image_usage = None
    estimated = await generate(quality="high")
    assert estimated.usage.estimated and estimated.usage.completion_tokens == 4160
    assert estimated.usage.cost_usd == pytest.approx(estimated.usage.prompt_tokens * 5e-6 + 4160 * 3e-5)


@pytest.mark.parametrize(
    ("size", "quality", "tokens"),
    [
        ("1024x1024", "low", 272),
        ("1024x1024", "medium", 1056),
        ("1024x1024", "high", 4160),
        ("1536x1024", "low", 408),
        ("1536x1024", "medium", 1584),
        ("1024x1536", "high", 6240),
    ],
)
def test_estimated_image_usage(size: str, quality: str, tokens: int) -> None:
    usage = llm.estimate_image_usage(image_model(), "A fox head logo", size, quality)
    assert usage.estimated and usage.completion_tokens == tokens and usage.prompt_tokens > 0
    assert usage.cost_usd == pytest.approx(usage.prompt_tokens * 5e-6 + tokens * 3e-5)


def test_a_403_is_reported_as_refused_access() -> None:
    response = httpx.Response(403, request=httpx.Request("POST", "https://example.invalid"))
    error = litellm.PermissionDeniedError(message="no", llm_provider="openai", model="x", response=response)
    assert llm.friendly_error(error) == (
        "The model provider refused access to this model. Check the account's access with the provider."
    )


# ---------------- The monthly image count (section 10) ----------------


async def user_row(username: str) -> User:
    async with SessionLocal() as db:
        user = await db.scalar(select(User).where(User.username == username))
        assert user is not None
        return user


async def active_image_model() -> LLMModel:
    async with SessionLocal() as db:
        model = await db.scalar(select(LLMModel).where(LLMModel.kind == "image"))
        assert model is not None
        return model


async def entitlements_of(user: User) -> Entitlements:
    async with SessionLocal() as db:
        return await entitlements.load(db, user)


async def image_events(user_id: int) -> list[UsageEvent]:
    async with SessionLocal() as db:
        return list(
            await db.scalars(select(UsageEvent).where(UsageEvent.user_id == user_id, UsageEvent.kind == "image"))
        )


async def test_concurrent_reservations_with_one_image_left(create_user: CreateUser) -> None:
    await create_user("alice", monthly_image_limit=2)
    alice, model = await user_row("alice"), await active_image_model()
    ent = await entitlements_of(alice)
    first = await image_tool.reserve(alice, model, ent)
    assert first.left == 1

    results = await asyncio.gather(*(image_tool.reserve(alice, model, ent) for _ in range(2)), return_exceptions=True)
    reserved = [result for result in results if isinstance(result, image_tool.Reservation)]
    refused = [result for result in results if isinstance(result, image_tool.ImageLimitReached)]
    assert len(reserved) == 1 and len(refused) == 1 and refused[0].limit == 2 and reserved[0].left == 0
    events = await image_events(alice.id)
    assert len(events) == 2 and all(
        (event.model_id, event.model_name, event.total_tokens, event.cost_usd)
        == (model.id, "openai/gpt-image-2", 0, 0.0)
        for event in events
    )


async def test_the_admin_and_unlimited_users_are_never_refused(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    await create_user("bob", monthly_image_limit=0)
    bob, admin, model = await user_row("bob"), await user_row("admin"), await active_image_model()
    for user in (bob, admin):
        ent = await entitlements_of(user)
        for _ in range(3):
            assert (await image_tool.reserve(user, model, ent)).left is None
    assert len(await image_events(admin.id)) == 3


async def test_settle_and_release(create_user: CreateUser) -> None:
    await create_user("alice")
    alice, model = await user_row("alice"), await active_image_model()
    ent = await entitlements_of(alice)
    kept, dropped = await image_tool.reserve(alice, model, ent), await image_tool.reserve(alice, model, ent)
    await image_tool.settle(kept.event_id, llm.Usage(50, 272, 0.00841))
    await image_tool.release(dropped.event_id)
    [event] = await image_events(alice.id)
    assert (event.id, event.prompt_tokens, event.completion_tokens, event.total_tokens) == (kept.event_id, 50, 272, 322)
    assert event.cost_usd == pytest.approx(0.00841)


async def test_the_count_follows_the_utc_month_of_an_open_ended_plan(create_user: CreateUser) -> None:
    await create_user("alice")
    alice = await user_row("alice")
    start = month_start()
    async with SessionLocal() as db:
        for created in (start - timedelta(seconds=1), start, start + timedelta(days=1)):
            db.add(UsageEvent(user_id=alice.id, model_name="m", kind="image", created_at=created))
        db.add(UsageEvent(user_id=alice.id, model_name="m", kind="chat", created_at=start))
        await db.commit()
        assert await images_in_window(db, alice.id, await entitlements.load(db, alice)) == 2


@pytest.mark.postgres
async def test_the_row_lock_alone_serializes_reservations_across_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two engines (as two backend processes) and no shared asyncio lock: only the FOR NO KEY UPDATE on the user row
    keeps both from passing the count. Run against a throwaway Postgres: TEST_POSTGRES_URL=postgresql+asyncpg://..."""
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL isn't set")

    class FreshLocks(dict[int, asyncio.Lock]):
        """A new lock for every caller: the asyncio lock covers only one process."""

        def setdefault(self, key: int, default: asyncio.Lock | None = None) -> asyncio.Lock:  # type: ignore[override]
            return asyncio.Lock()

        def __delitem__(self, key: int) -> None:
            pass

    monkeypatch.setattr(storage, "_upload_locks", FreshLocks())
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.db import Base

    engines = [make_engine(url), make_engine(url)]
    try:
        async with engines[0].begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
        async with AsyncSession(engines[0], expire_on_commit=False) as db:
            user = User(username="smoke-pg", password_hash="x", is_admin=False)
            model = LLMModel(display_name="GPT Image 2", litellm_model="openai/gpt-image-2", kind="image")
            db.add_all([user, model])
            user.monthly_image_limit = 1
            await db.commit()
        for attempt in range(5):
            async with AsyncSession(engines[0]) as db:
                await db.execute(update(UsageEvent).values(kind="chat"))  # each round starts with no images
                await db.commit()

            async def one(engine_index: int) -> image_tool.Reservation | BaseException:
                async with AsyncSession(engines[engine_index], expire_on_commit=False) as db:
                    try:
                        # No plan in this database: the override of 1 is the limit
                        return await image_tool._reserve(db, user, model, await entitlements.load(db, user))
                    except image_tool.ImageLimitReached as exc:
                        return exc

            results = await asyncio.gather(one(0), one(1))
            assert sum(isinstance(result, image_tool.Reservation) for result in results) == 1, (attempt, results)
    finally:
        for engine in engines:
            await engine.dispose()


# ---------------- Unusable models (I22) ----------------


async def test_unusable_marks_follow_the_model_version(admin_client: ApiClient, fake_llm: FakeLLM) -> None:
    model = await active_image_model()
    image_tool.mark_unusable(model, "The image model returned no image data.")
    assert image_tool.is_unusable(model)
    async with SessionLocal() as db:
        assert await image_tool.usable_image_model(db) is None

    edited = await admin_client.patch(f"/api/admin/models/{model.id}", json={"display_name": "GPT Image 2 (edited)"})
    assert edited.status_code == 200
    async with SessionLocal() as db:
        current = await image_tool.usable_image_model(db)
    assert current is not None and current.updated_at != model.updated_at and not image_tool.is_unusable(current)

    image_tool.mark_unusable(current, "no data")
    response = await admin_client.post(f"/api/admin/models/{model.id}/test")
    assert response.json()["ok"] is True and not image_tool.is_unusable(current)
