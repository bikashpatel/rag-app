"""Startup sync (app.bootstrap): the admin account from .env and the initial model catalogue."""

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, USER_PASSWORD, ApiClient
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.bootstrap import bootstrap
from app.config import Settings, settings
from app.db import SessionLocal, init_db
from app.models import AppState, LLMModel, User, UserImageLimit
from app.security import verify_password

CreateUser = Callable[..., Awaitable[ApiClient]]

NEW_ADMIN_PASSWORD = "rotated-admin-password-456"
OPUS = "anthropic/claude-opus-5-5"
GPT_MINI = "openai/gpt-5.4-mini"
SMALL_EMBEDDING = "openai/text-embedding-3-small"
LARGE_EMBEDDING = "openai/text-embedding-3-large"
SONNET = "anthropic/claude-sonnet-5-5"
ANTHROPIC_CHAT = {OPUS, SONNET, "anthropic/claude-haiku-5-5"}

# litellm_model -> (display_name, kind, provider key env var, input and output USD per 1M tokens): the chat and
# embedding models seeded into an empty table.
CATALOGUE: dict[str, tuple[str, str, str, float | None, float | None]] = {
    OPUS: ("Claude Opus 5.5", "chat", "ANTHROPIC_API_KEY", 4.0, 20.0),
    SONNET: ("Claude Sonnet 5.5", "chat", "ANTHROPIC_API_KEY", 2.0, 10.0),
    "anthropic/claude-haiku-5-5": ("Claude Haiku 5.5", "chat", "ANTHROPIC_API_KEY", 0.10, 0.50),
    "openai/gpt-5.5": ("GPT-5.5", "chat", "OPENAI_API_KEY", None, None),
    GPT_MINI: ("GPT-5.4 mini", "chat", "OPENAI_API_KEY", None, None),
    SMALL_EMBEDDING: ("OpenAI text-embedding-3-small", "embedding", "OPENAI_API_KEY", None, None),
    LARGE_EMBEDDING: ("OpenAI text-embedding-3-large", "embedding", "OPENAI_API_KEY", None, None),
}
# The vision model seeded once per database (recorded as app_state "vision_seeded"): (litellm_model, display_name,
# input and output USD per 1M tokens), by which provider keys are set.
SONNET_VISION = (SONNET, "Claude Sonnet 5.5 (vision)", 2.0, 10.0)
GPT_MINI_VISION = (GPT_MINI, "GPT-5.4 mini (vision)", None, None)
# The image model seeded once per database ("image_seeded"), enabled when OPENAI_API_KEY is set; priced by LiteLLM.
GPT_IMAGE = "openai/gpt-image-2"


@pytest.fixture
async def tables(isolated_state: Path) -> None:
    """Empty tables, without the startup sync that the `app` fixture runs."""
    await init_db()


async def run_bootstrap() -> None:
    async with SessionLocal() as session:
        await bootstrap(session)


async def get_user(username: str) -> User:
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.username == username))
    assert user is not None, username
    return user


async def all_users() -> list[User]:
    async with SessionLocal() as session:
        return list(await session.scalars(select(User).order_by(User.id)))


async def all_models() -> list[LLMModel]:
    async with SessionLocal() as session:
        return list(await session.scalars(select(LLMModel).order_by(LLMModel.id)))


async def admin_usernames() -> list[str]:
    return [user.username for user in await all_users() if user.is_admin]


def model_rows(models: list[LLMModel]) -> list[tuple[Any, ...]]:
    return [
        (
            model.id,
            model.display_name,
            model.litellm_model,
            model.kind,
            model.enabled,
            model.is_default,
            model.updated_at,
        )
        for model in models
    ]


async def snapshot() -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    users = [
        (user.id, user.username, user.password_hash, user.is_admin, user.is_active, user.token_version, user.updated_at)
        for user in await all_users()
    ]
    return users, model_rows(await all_models())


# ---------------- Admin account ----------------


async def test_first_start_creates_the_env_admin(tables: None) -> None:
    await run_bootstrap()

    users = await all_users()
    assert [(user.username, user.is_admin, user.is_active, user.token_version) for user in users] == [
        (ADMIN_USERNAME, True, True, 0)
    ]
    assert verify_password(ADMIN_PASSWORD, users[0].password_hash)
    assert users[0].last_login_at is None


async def test_env_admin_username_is_matched_in_lowercase(
    app: object, client: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = Settings(admin_username="Root.Admin")  # type: ignore[call-arg]
    assert configured.admin_username == "root.admin"
    monkeypatch.setattr(settings, "admin_username", configured.admin_username)

    await run_bootstrap()

    response = await client.login("ROOT.Admin", ADMIN_PASSWORD)
    assert response.status_code == 200
    assert (response.json()["username"], response.json()["is_admin"]) == ("root.admin", True)
    assert await admin_usernames() == ["root.admin"]


async def test_env_password_change_rehashes_and_signs_out_admin_sessions(
    admin_client: ApiClient, client: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = await get_user(ADMIN_USERNAME)
    monkeypatch.setattr(settings, "admin_password", NEW_ADMIN_PASSWORD)

    await run_bootstrap()

    after = await get_user(ADMIN_USERNAME)
    assert (after.id, after.is_admin, after.is_active) == (before.id, True, True)
    assert after.token_version == before.token_version + 1
    assert verify_password(NEW_ADMIN_PASSWORD, after.password_hash)
    assert not verify_password(ADMIN_PASSWORD, after.password_hash)
    assert (await admin_client.get("/api/auth/me")).status_code == 401
    assert (await client.login(ADMIN_USERNAME, ADMIN_PASSWORD)).status_code == 401
    response = await client.login(ADMIN_USERNAME, NEW_ADMIN_PASSWORD)
    assert response.status_code == 200
    assert response.json()["is_admin"] is True


async def test_unchanged_env_password_keeps_admin_sessions(admin_client: ApiClient) -> None:
    before = await get_user(ADMIN_USERNAME)

    await run_bootstrap()

    after = await get_user(ADMIN_USERNAME)
    assert (after.password_hash, after.token_version) == (before.password_hash, before.token_version)
    assert (await admin_client.get("/api/auth/me")).status_code == 200


async def test_start_reactivates_a_deactivated_env_admin(app: object, client: ApiClient) -> None:
    async with SessionLocal() as session:
        await session.execute(update(User).where(User.username == ADMIN_USERNAME).values(is_active=False))
        await session.commit()
    assert (await client.login(ADMIN_USERNAME, ADMIN_PASSWORD)).status_code == 401

    await run_bootstrap()

    assert (await get_user(ADMIN_USERNAME)).is_active
    assert (await client.login(ADMIN_USERNAME, ADMIN_PASSWORD)).status_code == 200


async def test_renamed_env_admin_demotes_and_deactivates_every_previous_admin(
    create_user: CreateUser,
    admin_client: ApiClient,
    client: ApiClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    alice = await create_user("alice")
    await create_user("mallory", login=False)
    async with SessionLocal() as session:
        await session.execute(update(User).where(User.username == "mallory").values(is_admin=True))
        await session.commit()
    before = {user.username: user for user in await all_users()}
    monkeypatch.setattr(settings, "admin_username", "root")

    with caplog.at_level(logging.WARNING, logger="app.bootstrap"):
        await run_bootstrap()

    after = {user.username: user for user in await all_users()}
    root = after["root"]
    assert (root.is_admin, root.is_active, root.token_version) == (True, True, 0)
    assert verify_password(ADMIN_PASSWORD, root.password_hash)
    for name in (ADMIN_USERNAME, "mallory"):
        assert (after[name].is_admin, after[name].is_active) == (False, False), name
        assert after[name].token_version == before[name].token_version + 1, name
    assert (after["alice"].is_admin, after["alice"].is_active) == (False, True)
    assert after["alice"].token_version == before["alice"].token_version
    assert await admin_usernames() == ["root"]

    warnings = [record.getMessage() for record in caplog.records if record.name == "app.bootstrap"]
    assert len(warnings) == 2
    assert any("'admin'" in message and "'root'" in message for message in warnings)
    assert any("'mallory'" in message for message in warnings)

    assert (await admin_client.get("/api/auth/me")).status_code == 401
    assert (await alice.get("/api/auth/me")).status_code == 200
    assert (await client.login(ADMIN_USERNAME, ADMIN_PASSWORD)).status_code == 401
    response = await client.login("root", ADMIN_PASSWORD)
    assert response.status_code == 200
    assert response.json()["is_admin"] is True
    assert (await client.get("/api/admin/config")).json()["admin_username"] == "root"


@pytest.mark.parametrize("password", [USER_PASSWORD, ADMIN_PASSWORD], ids=["own password", "same password as env"])
async def test_existing_user_named_in_env_becomes_the_admin_with_fresh_sessions(
    password: str,
    create_user: CreateUser,
    admin_client: ApiClient,
    client: ApiClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = await create_user("root", password)
    before = await get_user("root")
    monkeypatch.setattr(settings, "admin_username", "root")

    await run_bootstrap()

    after = await get_user("root")
    assert (after.id, after.is_admin, after.is_active) == (before.id, True, True)
    assert after.token_version == before.token_version + 1
    assert verify_password(ADMIN_PASSWORD, after.password_hash)
    assert verify_password(password, after.password_hash) is (password == ADMIN_PASSWORD)
    assert (await root.get("/api/auth/me")).status_code == 401
    assert (await admin_client.get("/api/auth/me")).status_code == 401
    response = await client.login("root", ADMIN_PASSWORD)
    assert response.status_code == 200
    assert response.json()["is_admin"] is True
    assert await admin_usernames() == ["root"]


# ---------------- Model catalogue ----------------


async def vision_marker(key: str = "vision_seeded") -> str | None:
    async with SessionLocal() as session:
        marker = await session.get(AppState, key)
    return marker.value if marker is not None else None


@pytest.mark.parametrize(
    ("openai_key", "anthropic_key", "default_chat", "vision", "vision_enabled"),
    [
        ("sk-test-openai", "sk-test-anthropic", OPUS, SONNET_VISION, True),
        ("sk-test-openai", None, GPT_MINI, GPT_MINI_VISION, True),
        ("sk-test-openai", "   ", GPT_MINI, GPT_MINI_VISION, True),
        (None, "sk-test-anthropic", OPUS, SONNET_VISION, True),
        ("", None, OPUS, SONNET_VISION, False),
    ],
    ids=["both keys", "openai only", "blank anthropic key", "anthropic only", "no keys"],
)
async def test_seeded_catalogue_follows_the_provider_keys(
    openai_key: str | None,
    anthropic_key: str | None,
    default_chat: str,
    vision: tuple[str, str, float | None, float | None],
    vision_enabled: bool,
    tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in (("OPENAI_API_KEY", openai_key), ("ANTHROPIC_API_KEY", anthropic_key)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    has_key = {
        "OPENAI_API_KEY": bool(openai_key and openai_key.strip()),
        "ANTHROPIC_API_KEY": bool(anthropic_key and anthropic_key.strip()),
    }

    await run_bootstrap()

    models = await all_models()
    vision_model, vision_name, vision_input, vision_output = vision
    assert {
        (model.kind, model.litellm_model): (
            model.display_name,
            model.enabled,
            model.is_default,
            model.input_cost_per_mtok,
            model.output_cost_per_mtok,
        )
        for model in models
    } == {
        **{
            (kind, litellm_model): (
                display_name,
                has_key[key_env] and litellm_model != LARGE_EMBEDDING,
                litellm_model in (default_chat, SMALL_EMBEDDING),
                input_cost,
                output_cost,
            )
            for litellm_model, (display_name, kind, key_env, input_cost, output_cost) in CATALOGUE.items()
        },
        ("vision", vision_model): (vision_name, vision_enabled, True, vision_input, vision_output),
        ("image", GPT_IMAGE): ("GPT Image 2", has_key["OPENAI_API_KEY"], True, None, None),
    }
    assert len(models) == len(CATALOGUE) + 2
    assert await vision_marker() == vision_model
    assert await vision_marker("image_seeded") == GPT_IMAGE
    assert all(
        (model.api_base, model.api_key_env, model.max_output_tokens, model.temperature, model.embedding_dim)
        == (None, None, None, None, None)
        for model in models
    )


async def test_models_are_seeded_only_into_an_empty_table(app: object, monkeypatch: pytest.MonkeyPatch) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(LLMModel).where(LLMModel.litellm_model == "anthropic/claude-haiku-5-5"))
        await session.execute(
            update(LLMModel).where(LLMModel.litellm_model == OPUS).values(display_name="Opus (team)", enabled=False)
        )
        await session.commit()
    edited = model_rows(await all_models())
    monkeypatch.delenv("OPENAI_API_KEY")

    await run_bootstrap()

    assert model_rows(await all_models()) == edited
    assert len(edited) == len(CATALOGUE) + 1  # one deleted, plus the vision and image models

    async with SessionLocal() as session:
        await session.execute(delete(LLMModel))
        await session.commit()

    await run_bootstrap()

    # The vision and image models were seeded once already ("vision_seeded", "image_seeded"), so an emptied
    # catalogue doesn't get them again.
    reseeded = await all_models()
    assert {model.litellm_model for model in reseeded} == set(CATALOGUE)
    assert {model.litellm_model for model in reseeded if model.enabled} == ANTHROPIC_CHAT
    assert {(model.kind, model.litellm_model) for model in reseeded if model.is_default} == {
        ("chat", OPUS),
        ("embedding", SMALL_EMBEDDING),
    }
    assert await vision_marker() == SONNET
    assert await vision_marker("image_seeded") == GPT_IMAGE


async def test_repeated_starts_change_nothing(create_user: CreateUser, admin_client: ApiClient) -> None:
    alice = await create_user("alice")
    before = await snapshot()

    await run_bootstrap()
    await run_bootstrap()

    assert await snapshot() == before
    assert len(before[0]) == 2
    assert len(before[1]) == len(CATALOGUE) + 2
    assert await vision_marker() == SONNET
    assert await vision_marker("image_seeded") == GPT_IMAGE
    assert (await admin_client.get("/api/auth/me")).status_code == 200
    assert (await alice.get("/api/auth/me")).status_code == 200


# ---------------- Images: the seeded model and the monthly image limits ----------------


async def image_rows() -> list[tuple[str, str, bool, bool]]:
    async with SessionLocal() as session:
        rows = await session.execute(
            select(LLMModel.display_name, LLMModel.litellm_model, LLMModel.enabled, LLMModel.is_default)
            .where(LLMModel.kind == "image")
            .order_by(LLMModel.id)
        )
        return list(rows.tuples())


async def image_limits() -> dict[str, int]:
    async with SessionLocal() as session:
        rows = await session.execute(
            select(User.username, UserImageLimit.monthly_image_limit).join(User, User.id == UserImageLimit.user_id)
        )
        return {username: limit for username, limit in rows.tuples()}


async def forget_image_state() -> None:
    """The database as the release before images left it: no image model, no limits, no image flags."""
    async with SessionLocal() as session:
        await session.execute(delete(LLMModel).where(LLMModel.kind == "image"))
        await session.execute(delete(UserImageLimit))
        await session.execute(delete(AppState).where(AppState.key.in_(["image_seeded", "image_limits_backfilled"])))
        await session.commit()


@pytest.mark.parametrize("openai_key", ["sk-test-openai", None])
async def test_an_existing_database_gets_the_image_model_once(
    app: object, monkeypatch: pytest.MonkeyPatch, openai_key: str | None
) -> None:
    await forget_image_state()
    if openai_key is None:
        monkeypatch.delenv("OPENAI_API_KEY")
    before = model_rows([model for model in await all_models() if model.kind != "image"])

    await run_bootstrap()
    await run_bootstrap()

    assert await image_rows() == [("GPT Image 2", GPT_IMAGE, openai_key is not None, True)]
    assert model_rows([model for model in await all_models() if model.kind != "image"]) == before
    assert await vision_marker("image_seeded") == GPT_IMAGE


async def test_an_existing_image_model_is_kept_and_a_deleted_one_is_not_added_again(
    app: object, admin_client: ApiClient
) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(AppState).where(AppState.key == "image_seeded"))
        await session.execute(update(LLMModel).where(LLMModel.kind == "image").values(display_name="Our images"))
        await session.commit()

    await run_bootstrap()

    assert [row[0] for row in await image_rows()] == ["Our images"]
    assert await vision_marker("image_seeded") == "existing"
    [(model_id,)] = [(model.id,) for model in await all_models() if model.kind == "image"]
    assert (await admin_client.delete(f"/api/admin/models/{model_id}")).status_code == 204

    await run_bootstrap()

    assert await image_rows() == []
    assert (await admin_client.get("/api/admin/stats")).json()["active_image_model"] is None


async def test_two_backends_seeding_at_once_add_one_image_model(app: object, monkeypatch: pytest.MonkeyPatch) -> None:
    await forget_image_state()
    commit = AsyncSession.commit
    raced: list[bool] = []

    async def racing_commit(self: AsyncSession) -> None:
        if not raced and any(isinstance(row, AppState) and row.key == "image_seeded" for row in self.new):
            raced.append(True)
            async with SessionLocal() as other:  # the other backend commits first
                other.add(LLMModel(display_name="Other", litellm_model=GPT_IMAGE, kind="image", is_default=True))
                other.add(AppState(key="image_seeded", value=GPT_IMAGE))
                await commit(other)
        await commit(self)

    monkeypatch.setattr(AsyncSession, "commit", racing_commit)

    await run_bootstrap()

    assert raced == [True]
    assert [row[0] for row in await image_rows()] == ["Other"]
    assert await vision_marker("image_limits_backfilled") == "0"  # the backfill still ran (only the admin exists)


async def test_existing_users_get_the_default_image_limit_once(
    create_user: CreateUser, admin_client: ApiClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    await create_user("alice", monthly_image_limit=7)
    await create_user("bob")
    await create_user("carol")
    await forget_image_state()
    async with SessionLocal() as session:  # carol already had her own value
        carol = await session.scalar(select(User.id).where(User.username == "carol"))
        session.add(UserImageLimit(user_id=carol, monthly_image_limit=3))
        await session.commit()
    monkeypatch.setattr(settings, "default_monthly_image_limit", 12)

    with caplog.at_level(logging.INFO, logger="app.bootstrap"):
        await run_bootstrap()

    assert await image_limits() == {"alice": 12, "bob": 12, "carol": 3}  # the admin gets no row
    assert await vision_marker("image_limits_backfilled") == "2"
    assert "Gave 2 existing user(s) the default monthly image limit of 12" in caplog.text

    monkeypatch.setattr(settings, "default_monthly_image_limit", 50)
    async with SessionLocal() as session:
        await session.execute(delete(UserImageLimit).where(UserImageLimit.user_id == carol))
        await session.commit()

    await run_bootstrap()

    assert await image_limits() == {"alice": 12, "bob": 12}  # once only; a later env change moves nobody
    users = {user["username"]: user for user in (await admin_client.get("/api/admin/users")).json()}
    assert [users[name]["monthly_image_limit"] for name in ("admin", "alice", "bob", "carol")] == [0, 12, 12, 50]


async def test_a_backfill_racing_another_backend_rolls_back(app: object, monkeypatch: pytest.MonkeyPatch) -> None:
    await forget_image_state()
    async with SessionLocal() as session:
        session.add(User(username="dave", password_hash="x"))
        await session.commit()
    execute = AsyncSession.execute
    raced: list[bool] = []

    async def racing_execute(self: AsyncSession, statement: Any, *args: Any, **kwargs: Any) -> Any:
        if not raced and "INSERT INTO user_image_limits" in str(statement):
            raced.append(True)
            async with SessionLocal() as other:  # the other backend backfilled first
                dave = await other.scalar(select(User.id).where(User.username == "dave"))
                other.add(UserImageLimit(user_id=dave, monthly_image_limit=30))
                other.add(AppState(key="image_limits_backfilled", value="1"))
                await other.commit()
        return await execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "execute", racing_execute)

    await run_bootstrap()

    assert raced == [True]
    assert await image_limits() == {"dave": 30}
    assert await vision_marker("image_limits_backfilled") == "1"  # this start's own marker was rolled back
    async with SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(AppState)) == 3
