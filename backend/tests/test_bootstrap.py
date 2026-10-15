"""Startup sync (app.bootstrap): the admin account from .env and the initial model catalogue."""

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, USER_PASSWORD, ApiClient
from sqlalchemy import delete, select, update

from app.bootstrap import bootstrap
from app.config import Settings, settings
from app.db import SessionLocal, init_db
from app.models import AppState, LLMModel, User
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


async def vision_marker() -> str | None:
    async with SessionLocal() as session:
        marker = await session.get(AppState, "vision_seeded")
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
    }
    assert len(models) == len(CATALOGUE) + 1
    assert await vision_marker() == vision_model
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
    assert len(edited) == len(CATALOGUE)  # one deleted, plus the vision model

    async with SessionLocal() as session:
        await session.execute(delete(LLMModel))
        await session.commit()

    await run_bootstrap()

    # The vision model was seeded once already ("vision_seeded"), so an emptied catalogue doesn't get it again.
    reseeded = await all_models()
    assert {model.litellm_model for model in reseeded} == set(CATALOGUE)
    assert {model.litellm_model for model in reseeded if model.enabled} == ANTHROPIC_CHAT
    assert {(model.kind, model.litellm_model) for model in reseeded if model.is_default} == {
        ("chat", OPUS),
        ("embedding", SMALL_EMBEDDING),
    }
    assert await vision_marker() == SONNET


async def test_repeated_starts_change_nothing(create_user: CreateUser, admin_client: ApiClient) -> None:
    alice = await create_user("alice")
    before = await snapshot()

    await run_bootstrap()
    await run_bootstrap()

    assert await snapshot() == before
    assert len(before[0]) == 2
    assert len(before[1]) == len(CATALOGUE) + 1
    assert await vision_marker() == SONNET
    assert (await admin_client.get("/api/auth/me")).status_code == 200
    assert (await alice.get("/api/auth/me")).status_code == 200
