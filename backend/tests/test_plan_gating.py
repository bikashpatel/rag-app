"""Access by plan in the existing features (docs/briefs/plans-billing.md 5.4 to 5.7; SPEC.md, "Access by plan"): what a
user without a plan, or whose plan ended, can still reach (their own documents, conversation titles, deletes, Settings,
Disconnect); the plan's chat models (402 outside the plan, 403 outside the user's allowed list); files, images and
Gmail offered to the model only when the plan includes them, with a note naming the plan that does; Gmail's routes and
a kept, untouched connection; limits from the plan with documents over a lowered limit kept; and the vision allowance
of an owner without access. Every route's class is in test_route_guards.py."""

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from conftest import (
    ADMIN_USERNAME,
    ApiClient,
    FakeLLM,
    PluginHarness,
    chat,
    end_plan,
    fake_image,
    grant_plan,
    ingest,
    md_bytes,
    scanned_pdf_bytes,
    txt_bytes,
    upload,
)
from fake_plugin import FakePlugin, insert_connection
from gmail_support import Gmail, connection_of, gmail  # noqa: F401
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Document, GeneratedImage, LLMModel, Plan, User
from app.plugins import registry
from app.services import ingestion, storage
from app.services.billing import entitlements

CreateUser = Callable[..., Awaitable[ApiClient]]

OPUS = "Claude Opus 5.5"
SONNET = "Claude Sonnet 5.5"
HAIKU = "Claude Haiku 5.5"
GPT_MINI = "GPT-5.4 mini"
FILES_NOTE = "The user's plan doesn't include creating files."
IMAGES_NOTE = "The user's plan doesn't include generating images."
GMAIL_NOTE = (
    "The user's plan doesn't include the Gmail plugin. If the user asks about their email, tell them their plan "
    "doesn't include Gmail and that the Professional plan does; you can't search, read, draft or send email in this "
    "conversation."
)
PLAN_REQUIRED = {"detail": "Choose a plan to start using the assistant.", "code": "plan_required"}


@pytest.fixture
async def model_ids(app: object) -> dict[str, int]:
    async with SessionLocal() as db:
        return {name: model_id for name, model_id in await db.execute(select(LLMModel.display_name, LLMModel.id))}


def system_prompt(call: dict[str, Any]) -> str:
    return call["messages"][0]["content"]


def offered(call: dict[str, Any]) -> list[str]:
    return [tool["function"]["name"] for tool in call.get("tools") or []]


async def on_plan(create_user: CreateUser, name: str, code: str, **fields: Any) -> ApiClient:
    """A user with an open-ended plan `code`."""
    return await create_user(name, plan=code, **fields)


async def expired(create_user: CreateUser, name: str) -> ApiClient:
    """A user whose Starter period ended."""
    client = await create_user(name, plan=None)
    assert client.user_id is not None
    await grant_plan(client.user_id, "starter", days=30)
    await end_plan(client.user_id)
    return client


async def generated_image(owner_id: int) -> int:
    """A generated image owned by `owner_id`, as the image tool stores one."""
    stored_name = storage.new_stored_name(".png")
    path = storage.stored_path(stored_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = fake_image()
    path.write_bytes(data)
    async with SessionLocal() as db:
        document = Document(
            owner_id=owner_id,
            scope="private",
            filename="fox.png",
            stored_name=stored_name,
            content_type="image/png",
            size_bytes=len(data),
            sha256="b" * 64,
            status="ready",
        )
        db.add(document)
        await db.flush()
        db.add(
            GeneratedImage(
                document_id=document.id,
                model_name="openai/gpt-image-2",
                prompt="A fox",
                alt_text="A fox",
                format="png",
                quality="low",
                width=64,
                height=64,
            )
        )
        await db.commit()
        return document.id


# ---------------- Users without a plan ----------------


async def test_nginx_asks_whether_the_session_may_upload(
    client: ApiClient, create_user: CreateUser, admin_client: ApiClient
) -> None:
    active = await create_user("active")
    never = await create_user("never", plan=None)
    ended = await expired(create_user, "ended")

    assert (await client.get("/api/auth/upload-allowed")).status_code == 401
    for lapsed in (never, ended):
        refused = await lapsed.get("/api/auth/upload-allowed")
        assert refused.status_code == 403, refused.text
    for allowed in (active, admin_client):
        response = await allowed.get("/api/auth/upload-allowed")
        assert (response.status_code, response.content) == (204, b"")


async def test_a_user_without_a_plan_keeps_their_own_documents_but_not_the_shared_ones(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    alice = await create_user("alice")
    assert alice.user_id is not None
    own = await ingest(alice, "diary.txt", txt_bytes("Alice's private diary about the garden."))
    shared = await ingest(admin_client, "policy.txt", txt_bytes("The company travel policy."), "shared")
    image_id = await generated_image(alice.user_id)
    await end_plan(alice.user_id)  # the open-ended plan ends: no plan at all

    listed = await alice.get("/api/documents")
    assert listed.status_code == 200
    assert sorted(item["id"] for item in listed.json()) == sorted([own["id"], image_id])

    assert (await alice.get(f"/api/documents/{own['id']}/download")).content.startswith(b"Alice's private diary")
    assert (await alice.get(f"/api/documents/{image_id}/image?size=thumb")).status_code == 200
    for path in (f"{shared['id']}/download", f"{shared['id']}/image", f"{image_id}/image/details"):
        response = await alice.get(f"/api/documents/{path}")
        assert (response.status_code, response.json()) == (402, PLAN_REQUIRED), path
    assert (await alice.get("/api/documents/10000/download")).status_code == 404  # missing stays 404

    assert (await alice.delete(f"/api/documents/{own['id']}")).status_code == 204
    assert [item["id"] for item in (await alice.get("/api/documents")).json()] == [image_id]


async def test_an_ended_plan_keeps_the_conversations_listed_and_deletable_but_closed(
    create_user: CreateUser, fake_llm: FakeLLM
) -> None:
    alice = await create_user("alice", plan=None)
    assert alice.user_id is not None
    await grant_plan(alice.user_id, "starter", days=30)
    _, events = await chat(alice, "What is in my notes?", document_ids=[])
    conversation_id = events[0][1]["conversation_id"]
    await end_plan(alice.user_id)

    titles = await alice.get("/api/conversations")
    reading = await alice.get(f"/api/conversations/{conversation_id}")
    renaming = await alice.patch(f"/api/conversations/{conversation_id}", json={"title": "Renamed"})
    continuing, _ = await chat(alice, "And then?", conversation_id=conversation_id, document_ids=[])
    me = (await alice.get("/api/auth/me")).json()

    assert [item["id"] for item in titles.json()] == [conversation_id]
    for response in (reading, renaming, continuing):
        body = response.json()
        assert (response.status_code, body["code"], body["plan"]) == (402, "plan_expired", "Starter")
        assert body["detail"].startswith("Your Starter plan ended on ") and body["detail"].endswith(
            "Renew it to continue; your chats and documents are kept."
        )
    assert (me["plan"]["state"], me["plan"]["access"], me["plan"]["plan"]["code"]) == ("expired", False, "starter")
    assert len(fake_llm.chat_calls) == 1
    assert (await alice.delete(f"/api/conversations/{conversation_id}")).status_code == 204


async def test_a_new_user_has_no_plan_until_one_is_granted(admin_client: ApiClient, create_user: CreateUser) -> None:
    bob = await create_user("bob", plan=None)
    assert bob.user_id is not None

    me = (await bob.get("/api/auth/me")).json()
    refused, _ = await chat(bob, "Hello?", document_ids=[])

    assert (me["plan"]["state"], me["plan"]["access"]) == ("none", False)
    assert me["plan"]["features"] == {"files": False, "images": False, "plugins": []}
    assert (refused.status_code, refused.json()) == (402, PLAN_REQUIRED)
    await grant_plan(bob.user_id, "starter", days=1)
    allowed, _ = await chat(bob, "Hello again?", document_ids=[])
    assert allowed.status_code == 200


# ---------------- Chat models ----------------


async def test_the_plans_models_are_listed_with_its_default_first(
    create_user: CreateUser, model_ids: dict[str, int]
) -> None:
    starter = await on_plan(create_user, "sam", "starter")
    standard = await on_plan(create_user, "stan", "standard")

    assert [(model["display_name"], model["is_default"]) for model in (await starter.get("/api/models")).json()] == [
        (HAIKU, True),
        (GPT_MINI, False),
    ]
    assert [model["display_name"] for model in (await standard.get("/api/models")).json()] == [
        SONNET,  # Standard's default
        HAIKU,
        GPT_MINI,
    ]


async def test_a_model_outside_the_plan_is_402_and_outside_the_allowed_list_403(
    create_user: CreateUser, model_ids: dict[str, int], fake_llm: FakeLLM
) -> None:
    # The allowed list only narrows the plan: Opus stays out on Starter
    starter = await on_plan(
        create_user, "sam", "starter", restrict_models=True, allowed_model_ids=[model_ids[HAIKU], model_ids[OPUS]]
    )

    listed = [model["display_name"] for model in (await starter.get("/api/models")).json()]
    outside_plan, _ = await chat(starter, "Hi", model_id=model_ids[OPUS], document_ids=[])
    outside_list, _ = await chat(starter, "Hi", model_id=model_ids[GPT_MINI], document_ids=[])
    default, _ = await chat(starter, "Hi", document_ids=[])

    assert listed == [HAIKU]
    assert (outside_plan.status_code, outside_plan.json()) == (
        402,
        {
            "detail": f"Your Starter plan doesn't include {OPUS}. It's included in Professional.",
            "code": "feature_not_in_plan",
            "feature": "model",
            "plan": "Professional",
        },
    )
    assert (outside_list.status_code, outside_list.json()["detail"]) == (403, "You are not allowed to use this model.")
    assert default.status_code == 200 and fake_llm.chat_calls[-1]["model"] == "anthropic/claude-haiku-5-5"


async def test_no_model_is_left_when_the_allowed_list_and_the_plan_dont_overlap(
    create_user: CreateUser, model_ids: dict[str, int], admin_client: ApiClient
) -> None:
    starter = await on_plan(create_user, "sam", "starter", restrict_models=True, allowed_model_ids=[model_ids[OPUS]])

    response, _ = await chat(starter, "Hi", document_ids=[])
    users = {user["username"]: user for user in (await admin_client.get("/api/admin/users")).json()}

    assert (await starter.get("/api/models")).json() == []
    assert (response.status_code, response.json()["detail"]) == (
        400,
        "No chat model is available to you. Ask the admin to enable one.",
    )
    assert users["sam"]["no_models"] is True


async def test_a_default_model_preference_must_be_one_of_the_plans(
    create_user: CreateUser, model_ids: dict[str, int]
) -> None:
    alice = await create_user("alice")  # Professional: every model
    assert alice.user_id is not None
    saved = await alice.patch("/api/me/preferences", json={"default_model_id": model_ids[OPUS]})
    assert saved.json()["default_model_id"] == model_ids[OPUS]

    await grant_plan(alice.user_id, "starter", days=None)  # moved to Starter
    assert (await alice.get("/api/me/preferences")).json()["default_model_id"] is None  # Opus isn't usable now
    refused = await alice.patch("/api/me/preferences", json={"default_model_id": model_ids[OPUS]})
    allowed = await alice.patch("/api/me/preferences", json={"default_model_id": model_ids[HAIKU]})
    assert (refused.status_code, refused.json()) == (422, {"detail": "That model isn't available to you."})
    assert allowed.json()["default_model_id"] == model_ids[HAIKU]

    await end_plan(alice.user_id)
    lapsed = await alice.patch("/api/me/preferences", json={"default_model_id": model_ids[HAIKU]})
    cleared = await alice.patch("/api/me/preferences", json={"default_model_id": None, "theme": "dark"})
    assert lapsed.status_code == 422
    assert (cleared.status_code, cleared.json()["theme"], cleared.json()["default_model_id"]) == (200, "dark", None)


# ---------------- Files and images in chat ----------------


async def test_tools_are_offered_only_for_the_plans_features(create_user: CreateUser, image_tools: FakeLLM) -> None:
    for name, code in (("sam", "starter"), ("stan", "standard"), ("pro", "professional")):
        await chat(await on_plan(create_user, name, code), "Hello", document_ids=[])
    starter, standard, professional = image_tools.chat_calls

    assert offered(starter) == []
    assert "files from chat and that the Standard plan does" in system_prompt(starter)
    assert "images from chat and that the Professional plan does" in system_prompt(starter)
    assert FILES_NOTE in system_prompt(starter) and IMAGES_NOTE in system_prompt(starter)
    assert offered(standard) == ["create_document"]
    assert FILES_NOTE not in system_prompt(standard)
    assert "images from chat and that the Professional plan does" in system_prompt(standard)
    assert offered(professional) == ["create_document", "create_image"]
    assert IMAGES_NOTE not in system_prompt(professional) and FILES_NOTE not in system_prompt(professional)


async def test_a_retired_feature_names_no_plan(create_user: CreateUser, fake_llm: FakeLLM) -> None:
    starter = await on_plan(create_user, "sam", "starter")
    async with SessionLocal() as db:
        for plan in await db.scalars(select(Plan).where(Plan.code != "starter")):
            plan.status = "retired"
        await db.commit()
        entitlements.invalidate_unlock()

    await chat(starter, "Hello", document_ids=[])

    assert "files from chat and that a higher plan does" in system_prompt(fake_llm.chat_calls[-1])


# ---------------- Limits ----------------


async def test_documents_over_a_lowered_limit_are_kept_and_nothing_is_added(
    create_user: CreateUser, admin_client: ApiClient, fake_llm: FakeLLM
) -> None:
    alice = await create_user("alice")
    assert alice.user_id is not None
    for number in range(3):
        assert (await upload(alice, f"{number}.md", md_bytes(f"Note {number}"))).status_code == 201
    lowered = await admin_client.patch(f"/api/admin/users/{alice.user_id}", json={"overrides": {"max_documents": 2}})
    assert lowered.status_code == 200

    refused = await upload(alice, "3.md", md_bytes("Note 3"))
    await chat(alice, "Make me a report file", document_ids=[])

    assert (refused.status_code, refused.json()) == (
        409,
        {"detail": "You've reached your limit of 2 documents. Delete some documents or upgrade your plan."},
    )
    assert len((await alice.get("/api/documents")).json()) == 3
    assert offered(fake_llm.chat_calls[-1]) == []
    assert "they have reached their limit of 2 documents" in system_prompt(fake_llm.chat_calls[-1])


async def test_reprocessing_doesnt_check_the_file_size_again(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    alice = await create_user("alice")
    assert alice.user_id is not None
    document = await ingest(alice, "big.txt", txt_bytes("word " * 300_000))  # 1.5 MB
    await admin_client.patch(f"/api/admin/users/{alice.user_id}", json={"overrides": {"max_upload_mb": 1}})

    again = await alice.post(f"/api/documents/{document['id']}/reprocess")
    too_large = await upload(alice, "other.txt", txt_bytes("other " * 300_000))

    assert again.status_code == 200 and again.json()["status"] == "pending"
    assert too_large.status_code == 413


async def test_scanned_pages_of_an_owner_without_access_arent_read(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    alice = await create_user("alice")
    assert alice.user_id is not None
    private = await upload(alice, "scan.pdf", scanned_pdf_bytes("Invoice"))
    shared = await upload(admin_client, "shared.pdf", scanned_pdf_bytes("Policy"), scope="shared")
    await end_plan(alice.user_id)

    async with SessionLocal() as db:
        blocked = await ingestion._vision_allowance(db, await db.get(Document, private.json()["id"]))
        exempt = await ingestion._vision_allowance(db, await db.get(Document, shared.json()["id"]))

    assert blocked == (
        "This PDF has scanned pages, but your plan has ended, so they weren't read. Renew your plan and reprocess the "
        "document.",
        None,
    )
    assert exempt == (None, None)


# ---------------- Gmail ----------------


async def test_gmail_outside_the_plan_keeps_the_connection_untouched(
    gmail: Gmail,  # noqa: F811
    create_user: CreateUser,
    fake_llm: FakeLLM,
) -> None:
    alice = await create_user("alice")
    assert alice.user_id is not None
    connection = await gmail.connect(alice)
    await grant_plan(alice.user_id, "standard", days=None)  # moved to Standard: no Gmail
    calls_before = len(gmail.mailbox.requests)

    [info] = (await alice.get("/api/plugins")).json()
    connect = await alice.post("/api/plugins/gmail/connect", json={}, headers={"origin": "http://testserver"})
    search = await alice.post("/api/plugins/gmail/threads/search", json={})
    send = await alice.get("/api/plugins/gmail/sends/1")
    await chat(alice, "Any new email?", document_ids=[])

    assert (info["in_plan"], info["enabled"], info["connection"]["status"]) == (False, True, "connected")
    not_in_plan = {
        "detail": "Your Standard plan doesn't include Gmail. It's included in Professional.",
        "code": "feature_not_in_plan",
        "feature": "plugin:gmail",
        "plan": "Professional",
    }
    for response in (connect, search, send):
        assert (response.status_code, response.json()) == (402, not_in_plan)
    call = fake_llm.chat_calls[-1]
    assert not any(name.startswith("gmail_") for name in offered(call))
    assert GMAIL_NOTE in system_prompt(call)
    assert len(gmail.mailbox.requests) == calls_before  # no Google call while outside the plan
    assert await connection_of(alice.user_id) == connection
    turned_off = await alice.patch("/api/plugins/gmail/connection", json={"chat_enabled": False})
    assert (turned_off.status_code, turned_off.json()["chat_enabled"]) == (200, False)

    await grant_plan(alice.user_id, "professional", days=None)  # back on Professional: no reconnecting
    await alice.patch("/api/plugins/gmail/connection", json={"chat_enabled": True})
    assert (await alice.post("/api/plugins/gmail/threads/search", json={})).status_code == 200
    assert (await alice.get("/api/plugins")).json()[0]["in_plan"] is True


async def test_gmail_turned_off_by_the_admin_wins_over_the_plan(
    gmail: Gmail,  # noqa: F811
    create_user: CreateUser,
) -> None:
    alice = await create_user("alice")
    assert alice.user_id is not None
    await gmail.connect(alice)
    await grant_plan(alice.user_id, "starter", days=None)
    await gmail.set_state(enabled=False)

    search = await alice.post("/api/plugins/gmail/threads/search", json={})
    connect = await alice.post("/api/plugins/gmail/connect", json={}, headers={"origin": "http://testserver"})
    disconnect = await alice.delete("/api/plugins/gmail/connection")

    assert (search.status_code, search.json()) == (403, {"detail": "Gmail is turned off."})
    assert connect.status_code == 403
    assert disconnect.status_code == 204 and await connection_of(alice.user_id) is None


async def test_the_admin_is_exempt_with_every_plugin(
    gmail: Gmail,  # noqa: F811
    admin_client: ApiClient,
) -> None:
    [info] = (await admin_client.get("/api/plugins")).json()
    users = {user["username"]: user for user in (await admin_client.get("/api/admin/users")).json()}

    assert info["in_plan"] is True
    admin = users[ADMIN_USERNAME]
    assert (admin["plan"]["state"], admin["limits"]["monthly_token_limit"], admin["above_plan"]) == ("exempt", 0, [])


async def test_a_plugin_without_its_own_wording_gets_the_generic_note(
    plugins: PluginHarness, create_user: CreateUser
) -> None:
    starter = await on_plan(create_user, "sam", "starter")
    assert starter.user_id is not None
    plugin = FakePlugin()
    with registry.use_plugins([plugin]):
        async with SessionLocal() as db:
            await registry.set_state(db, plugin, enabled=True, capabilities=None)
        await insert_connection(starter.user_id)
        async with SessionLocal() as db:
            user = await db.get(User, starter.user_id)
            model = await db.scalar(select(LLMModel).where(LLMModel.display_name == HAIKU))
            assert user is not None and model is not None
            plan = await registry.plan_chat(db, user, model, await entitlements.load(db, user))

    assert plan.toolsets == []
    assert plan.notes == [
        "The user's plan doesn't include the Fake Mail plugin. If the user asks for something that needs Fake Mail, "
        "tell them their plan doesn't include it and that a higher plan does; you can't use Fake Mail in this "
        "conversation."
    ]
