"""Admin API: access control, users, models, stats, re-indexing, usage reports and documents."""

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import litellm
import pytest
from conftest import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    USER_PASSWORD,
    ApiClient,
    FakeLLM,
    chat,
    ingest,
    md_bytes,
    pdf_bytes,
    process_queue,
    txt_bytes,
    upload,
)
from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from qdrant_client import models as qm
from sqlalchemy import event, select, update

from app import db
from app.config import settings
from app.db import SessionLocal
from app.main import app as fastapi_app
from app.models import Conversation, Document, LLMModel, Message, UsageEvent, User, UserImageLimit, user_allowed_models
from app.redis_client import INGEST_QUEUE, get_redis
from app.routers import admin as admin_router
from app.schemas import MAX_INT
from app.services import vectorstore
from app.services.storage import stored_path
from app.services.usage import month_start
from app.services.vectorstore import collection_name, get_qdrant

CreateUser = Callable[..., Awaitable[ApiClient]]

OPUS = "Claude Opus 5.5"
SONNET = "Claude Sonnet 5.5"
HAIKU = "Claude Haiku 5.5"
GPT = "GPT-5.5"
GPT_MINI = "GPT-5.4 mini"
SMALL = "OpenAI text-embedding-3-small"
LARGE = "OpenAI text-embedding-3-large"
SONNET_VISION = "Claude Sonnet 5.5 (vision)"
GPT_IMAGE = "GPT Image 2"

ACTIVE_EMBEDDING = "This is the active embedding model. Make another embedding model the default first."
ENV_ADMIN = "The admin account is managed in .env"
MISSING = object()

ADMIN_USER_KEYS = {
    "id",
    "username",
    "is_admin",
    "is_active",
    "monthly_token_limit",
    "rate_limit_per_min",
    "max_upload_mb",
    "max_documents",
    "max_storage_mb",
    "monthly_image_limit",
    "restrict_models",
    "allowed_model_ids",
    "created_at",
    "last_login_at",
    "usage",
    "document_count",
    "storage_bytes",
}
DOCUMENT_ITEM_KEYS = {
    "id",
    "filename",
    "scope",
    "status",
    "error",
    "size_bytes",
    "content_type",
    "num_chunks",
    "num_pages",
    "owner_id",
    "owner_username",
    "can_delete",
    "is_stale",
    "vision_pages",
    "generated",
    "image",
    "imported_from",
    "created_at",
    "updated_at",
}

# Every /api/admin route with a request body that would change state if the caller were allowed.
ADMIN_ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/api/admin/config", None),
    ("GET", "/api/admin/stats", None),
    ("GET", "/api/admin/users", None),
    ("POST", "/api/admin/users", {"username": "intruder", "password": USER_PASSWORD}),
    ("PATCH", "/api/admin/users/{user_id}", {"monthly_token_limit": 0, "rate_limit_per_min": 0}),
    ("DELETE", "/api/admin/users/{user_id}", None),
    ("GET", "/api/admin/models", None),
    ("POST", "/api/admin/models", {"display_name": "Rogue", "litellm_model": "openai/rogue", "kind": "chat"}),
    ("PATCH", "/api/admin/models/{model_id}", {"enabled": False}),
    ("DELETE", "/api/admin/models/{model_id}", None),
    ("POST", "/api/admin/models/{model_id}/test", None),
    ("POST", "/api/admin/reindex", {"only_stale": False}),
    ("GET", "/api/admin/usage", None),
    ("GET", "/api/admin/documents", None),
    ("DELETE", "/api/admin/documents/{document_id}", None),
    ("GET", "/api/admin/plugins", None),
    ("PATCH", "/api/admin/plugins/{plugin_id}", {"enabled": True}),
    ("GET", "/api/admin/plugins/{plugin_id}/activity", None),
    ("POST", "/api/admin/plugins/{plugin_id}/disconnect-all", None),
    ("POST", "/api/admin/plugins/request-check/test", {"model_id": 1}),
    ("GET", "/api/admin/system", None),
    ("POST", "/api/admin/system/test-alert", None),
]


# ---------------- Helpers ----------------


@pytest.fixture
async def model_ids(app: FastAPI) -> dict[str, int]:
    """display_name -> id of every seeded model."""
    async with SessionLocal() as session:
        return {name: model_id for name, model_id in await session.execute(select(LLMModel.display_name, LLMModel.id))}


def with_changes(base: dict[str, Any], field: str, value: object) -> dict[str, Any]:
    """`base` with `field` set to `value`, or removed when `value` is MISSING."""
    body = {key: item for key, item in base.items() if key != field}
    if value is not MISSING:
        body[field] = value
    return body


async def queue() -> list[str]:
    return await get_redis().lrange(INGEST_QUEUE, 0, -1)


async def load(entity: type[Any], entity_id: int) -> Any:
    async with SessionLocal() as session:
        return await session.get(entity, entity_id)


async def add_rows(*rows: object) -> None:
    async with SessionLocal() as session:
        session.add_all(rows)
        await session.commit()


async def point_count(model_id: int, doc_id: int) -> int:
    """Vectors of the document in the model's Qdrant collection (0 when the collection doesn't exist)."""
    client = get_qdrant()
    name = collection_name(model_id)
    if not await client.collection_exists(name):
        return 0
    doc_filter = qm.Filter(must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))])
    return (await client.count(name, count_filter=doc_filter, exact=True)).count


async def snapshot() -> tuple[list[Any], ...]:
    """Everything the admin API can change, to prove a refused request changed nothing."""
    async with SessionLocal() as session:
        users = await session.execute(
            select(
                User.id,
                User.username,
                User.password_hash,
                User.is_active,
                User.token_version,
                User.monthly_token_limit,
                User.rate_limit_per_min,
            ).order_by(User.id)
        )
        models = await session.execute(
            select(
                LLMModel.id, LLMModel.display_name, LLMModel.enabled, LLMModel.is_default, LLMModel.embedding_dim
            ).order_by(LLMModel.id)
        )
        documents = await session.execute(select(Document.id, Document.status).order_by(Document.id))
        return (
            [tuple(row) for row in users],
            [tuple(row) for row in models],
            [tuple(row) for row in documents],
            await queue(),
        )


async def activate(admin: ApiClient, model_id: int) -> None:
    """Enable an embedding model and make it the active one."""
    response = await admin.patch(f"/api/admin/models/{model_id}", json={"enabled": True, "is_default": True})
    assert response.status_code == 200, response.text


async def remove_active_embedding_model() -> None:
    """Leave no active embedding model, as on an install without provider keys (the API refuses this)."""
    async with SessionLocal() as session:
        await session.execute(update(LLMModel).where(LLMModel.kind == "embedding").values(enabled=False))
        await session.commit()


async def admin_model(admin: ApiClient, model_id: int) -> dict[str, Any]:
    response = await admin.get("/api/admin/models")
    assert response.status_code == 200, response.text
    return next(model for model in response.json() if model["id"] == model_id)


async def admin_user(admin: ApiClient, user_id: int) -> dict[str, Any]:
    response = await admin.get("/api/admin/users")
    assert response.status_code == 200, response.text
    return next(user for user in response.json() if user["id"] == user_id)


async def stats(admin: ApiClient) -> dict[str, Any]:
    response = await admin.get("/api/admin/stats")
    assert response.status_code == 200, response.text
    return response.json()


@contextmanager
def recorded_queries() -> Iterator[list[str]]:
    """SQL statements the app runs on the test database inside the block."""
    statements: list[str] = []

    def record(_connection: Any, _cursor: Any, statement: str, *_: Any) -> None:
        statements.append(statement)

    event.listen(db.engine.sync_engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(db.engine.sync_engine, "before_cursor_execute", record)


def embedded_words(fake_llm: FakeLLM) -> int:
    """Embedding tokens FakeLLM reported so far (one per word of every embedded text)."""
    return sum(len(text.split()) for call in fake_llm.embedding_calls for text in call["input"])


# ---------------- Access control ----------------


def test_route_table_lists_every_admin_route() -> None:
    """From the app's route table, so a route hidden from the OpenAPI schema is listed too."""
    registered = {
        (method, route.path)
        for route in iter_route_contexts(fastapi_app.routes)
        if isinstance(route.original_route, APIRoute) and route.path.startswith("/api/admin")
        for method in route.methods
    }

    assert registered == {(method, path) for method, path, _ in ADMIN_ROUTES}


async def _route_targets(create_user: CreateUser, model_ids: dict[str, int]) -> tuple[ApiClient, dict[str, int]]:
    """A regular user with a queued document; path parameters point at their own account and document."""
    user = await create_user("mallory")
    document = await upload(user, "notes.txt", txt_bytes("Quarterly planning notes for the platform team."))
    assert document.status_code == 201, document.text
    assert user.user_id is not None
    targets = {"user_id": user.user_id, "model_id": model_ids[OPUS], "document_id": document.json()["id"]}
    return user, {**targets, "plugin_id": "gmail"}


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ROUTES, ids=[f"{m} {p}" for m, p, _ in ADMIN_ROUTES])
async def test_admin_routes_reject_regular_users(
    method: str,
    path: str,
    body: dict[str, Any] | None,
    create_user: CreateUser,
    model_ids: dict[str, int],
    fake_llm: FakeLLM,
) -> None:
    user, targets = await _route_targets(create_user, model_ids)
    before = await snapshot()

    response = await user.request(method, path.format(**targets), json=body)

    assert response.status_code == 403
    assert response.json() == {"detail": "Admin access required"}
    assert await snapshot() == before
    assert fake_llm.chat_calls == [] and fake_llm.embedding_calls == []


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ROUTES, ids=[f"{m} {p}" for m, p, _ in ADMIN_ROUTES])
async def test_admin_routes_require_a_session(
    method: str,
    path: str,
    body: dict[str, Any] | None,
    client: ApiClient,
    create_user: CreateUser,
    model_ids: dict[str, int],
) -> None:
    _, targets = await _route_targets(create_user, model_ids)
    before = await snapshot()

    response = await client.request(method, path.format(**targets), json=body)

    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}
    assert await snapshot() == before


# ---------------- Config ----------------


async def test_config_reports_the_defaults_and_the_admin(admin_client: ApiClient) -> None:
    response = await admin_client.get("/api/admin/config")

    assert response.status_code == 200
    assert response.json() == {
        "defaults": {
            "monthly_token_limit": 1_000_000,
            "rate_limit_per_min": 20,
            "max_upload_mb": 25,
            "max_documents": 100,
            "max_storage_mb": 500,
            "monthly_image_limit": 30,
        },
        "max_upload_mb_hard": settings.max_upload_mb_hard,
        "admin_username": ADMIN_USERNAME,
    }


# ---------------- Users ----------------


async def test_list_users_puts_the_admin_first_then_sorts_by_username(
    admin_client: ApiClient, create_user: CreateUser
) -> None:
    for name in ("zed", "Bob", "alice"):
        await create_user(name, login=False)

    response = await admin_client.get("/api/admin/users")

    assert response.status_code == 200
    users = response.json()
    assert [user["username"] for user in users] == [ADMIN_USERNAME, "alice", "bob", "zed"]
    assert all(set(user) == ADMIN_USER_KEYS for user in users)
    admin = users[0]
    assert admin["is_admin"] is True and admin["is_active"] is True
    assert admin["last_login_at"] is not None and admin["last_login_at"].endswith("Z")
    assert [user["is_admin"] for user in users[1:]] == [False, False, False]


async def test_create_user_applies_the_defaults(admin_client: ApiClient, new_client: Callable[[], ApiClient]) -> None:
    response = await admin_client.post(
        "/api/admin/users", json={"username": "  Alice.Smith ", "password": USER_PASSWORD}
    )

    assert response.status_code == 201, response.text
    created = response.json()
    assert set(created) == ADMIN_USER_KEYS
    assert created == {
        **created,
        "username": "alice.smith",
        "is_admin": False,
        "is_active": True,
        "monthly_token_limit": settings.default_monthly_token_limit,
        "rate_limit_per_min": settings.default_rate_limit_per_min,
        "max_upload_mb": settings.default_max_upload_mb,
        "max_documents": settings.default_max_documents,
        "max_storage_mb": settings.default_max_storage_mb,
        "monthly_image_limit": settings.default_monthly_image_limit,
        "restrict_models": False,
        "allowed_model_ids": [],
        "last_login_at": None,
        "usage": {"chat_tokens": 0, "embedding_tokens": 0, "vision_tokens": 0, "images": 0, "cost_usd": 0.0},
        "document_count": 0,
        "storage_bytes": 0,
    }
    assert created["created_at"].endswith("Z")
    assert await admin_user(admin_client, created["id"]) == created

    row = await load(User, created["id"])
    assert row.password_hash.startswith("$argon2") and USER_PASSWORD not in row.password_hash
    assert row.token_version == 0

    login = await new_client().login("alice.smith", USER_PASSWORD)
    assert login.status_code == 200
    assert (await admin_user(admin_client, created["id"]))["last_login_at"] is not None


async def test_create_user_with_every_field(admin_client: ApiClient, model_ids: dict[str, int]) -> None:
    body = {
        "username": "carol",
        "password": USER_PASSWORD,
        "is_active": False,
        "monthly_token_limit": 0,
        "rate_limit_per_min": 5,
        "max_upload_mb": settings.max_upload_mb_hard,
        "max_documents": 0,
        "max_storage_mb": 1,
        "restrict_models": True,
        "allowed_model_ids": [model_ids[SONNET], model_ids[HAIKU], model_ids[SONNET]],
    }

    response = await admin_client.post("/api/admin/users", json=body)

    assert response.status_code == 201, response.text
    created = response.json()
    expected = {key: value for key, value in body.items() if key != "password"}
    assert created == {**created, **expected, "allowed_model_ids": sorted([model_ids[SONNET], model_ids[HAIKU]])}
    async with SessionLocal() as session:
        grants = set(
            await session.scalars(
                select(user_allowed_models.c.model_id).where(user_allowed_models.c.user_id == created["id"])
            )
        )
    assert grants == {model_ids[SONNET], model_ids[HAIKU]}


@pytest.mark.parametrize("username", ["alice", "ALICE", " Alice ", ADMIN_USERNAME, ADMIN_USERNAME.upper()])
async def test_create_user_rejects_a_taken_username(
    username: str, admin_client: ApiClient, create_user: CreateUser
) -> None:
    await create_user("alice", login=False)
    before = await snapshot()

    response = await admin_client.post("/api/admin/users", json={"username": username, "password": USER_PASSWORD})

    assert response.status_code == 409
    assert response.json() == {"detail": "This username is already taken"}
    assert await snapshot() == before


async def test_concurrent_creates_of_one_username_make_one_user_and_a_409(
    admin_client: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both requests pass the username check before either inserts (they meet while hashing), so the second one is
    stopped by the unique index and rolled back."""
    hashing = admin_router.hash_password_async
    both_checked = asyncio.Barrier(2)

    async def hash_after_both_checks(password: str) -> str:
        await both_checked.wait()
        return await hashing(password)

    monkeypatch.setattr(admin_router, "hash_password_async", hash_after_both_checks)
    body = {"username": "twin", "password": USER_PASSWORD, "monthly_image_limit": 3}

    responses = await asyncio.wait_for(
        asyncio.gather(*(admin_client.post("/api/admin/users", json=body) for _ in range(2))), 30
    )

    assert sorted(response.status_code for response in responses) == [201, 409]
    [refused] = [response for response in responses if response.status_code == 409]
    assert refused.json() == {"detail": "This username is already taken"}
    async with SessionLocal() as session:
        [user_id] = await session.scalars(select(User.id).where(User.username == "twin"))
        limits = list(await session.scalars(select(UserImageLimit.user_id)))
    assert limits == [user_id]
    listed = (await admin_client.get("/api/admin/users")).json()
    assert [user["username"] for user in listed] == [ADMIN_USERNAME, "twin"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("username", MISSING),
        ("username", "ab"),
        ("username", "a" * 65),
        ("username", "two words"),
        ("username", "bad@name"),
        ("username", "émile"),
        ("username", 12345),
        ("password", MISSING),
        ("password", "seven77"),
        ("password", "x" * 257),
        ("is_active", "sometimes"),
        ("monthly_token_limit", -1),
        ("monthly_token_limit", "lots"),
        ("rate_limit_per_min", -1),
        ("rate_limit_per_min", MAX_INT + 1),
        ("max_upload_mb", 0),
        ("max_upload_mb", settings.max_upload_mb_hard + 1),
        ("max_documents", -1),
        ("max_storage_mb", -1),
        ("restrict_models", "maybe"),
        ("allowed_model_ids", [0]),
        ("allowed_model_ids", "1,2"),
    ],
)
async def test_create_user_validates_the_body(field: str, value: object, admin_client: ApiClient) -> None:
    before = await snapshot()
    body = with_changes({"username": "valid.user", "password": USER_PASSWORD}, field, value)

    response = await admin_client.post("/api/admin/users", json=body)

    assert response.status_code == 422
    assert any(field in error["loc"] for error in response.json()["detail"])
    assert await snapshot() == before


@pytest.mark.parametrize("model", [SMALL, "unknown"])
async def test_allowed_models_must_be_existing_chat_models(
    model: str, admin_client: ApiClient, create_user: CreateUser, model_ids: dict[str, int]
) -> None:
    bad_id = model_ids.get(model, 999_999)
    user = await create_user("dana", restrict_models=True, allowed_model_ids=[model_ids[HAIKU]], login=False)
    before = await snapshot()

    created = await admin_client.post(
        "/api/admin/users",
        json={"username": "erin", "password": USER_PASSWORD, "allowed_model_ids": [model_ids[OPUS], bad_id]},
    )
    updated = await admin_client.patch(
        f"/api/admin/users/{user.user_id}", json={"allowed_model_ids": [model_ids[OPUS], bad_id]}
    )

    expected = {"detail": f"allowed_model_ids must reference existing chat models (unknown: {bad_id})"}
    assert (created.status_code, created.json()) == (422, expected)
    assert (updated.status_code, updated.json()) == (422, expected)
    assert await snapshot() == before
    assert user.user_id is not None
    assert (await admin_user(admin_client, user.user_id))["allowed_model_ids"] == [model_ids[HAIKU]]


async def test_update_user_changes_limits_and_keeps_sessions(
    admin_client: ApiClient, create_user: CreateUser, model_ids: dict[str, int]
) -> None:
    user = await create_user("frank")
    changes = {
        "monthly_token_limit": 5000,
        "rate_limit_per_min": 0,
        "max_upload_mb": 2,
        "max_documents": 3,
        "max_storage_mb": 0,
        "monthly_image_limit": 9,
        "restrict_models": True,
        "allowed_model_ids": [model_ids[GPT], model_ids[HAIKU]],
    }

    response = await admin_client.patch(
        f"/api/admin/users/{user.user_id}", json={**changes, "username": "renamed", "is_admin": True}
    )

    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated == {
        **updated,
        **changes,
        "allowed_model_ids": sorted(changes["allowed_model_ids"]),
        "username": "frank",
        "is_admin": False,
        "is_active": True,
    }
    assert user.user_id is not None
    assert await admin_user(admin_client, user.user_id) == updated
    assert (await load(User, user.user_id)).token_version == 0
    me = await user.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["limits"] == {key: changes[key] for key in me.json()["limits"]}


async def test_update_user_leaves_omitted_and_null_fields_unchanged(
    admin_client: ApiClient, create_user: CreateUser, model_ids: dict[str, int]
) -> None:
    user = await create_user("gina", monthly_token_limit=777, restrict_models=True, allowed_model_ids=[model_ids[GPT]])
    assert user.user_id is not None
    original = await admin_user(admin_client, user.user_id)

    response = await admin_client.patch(
        f"/api/admin/users/{user.user_id}",
        json={"monthly_token_limit": None, "allowed_model_ids": None, "restrict_models": None, "is_active": None},
    )

    assert response.status_code == 200
    assert response.json() == original


async def test_update_user_replaces_and_clears_allowed_models(
    admin_client: ApiClient, create_user: CreateUser, model_ids: dict[str, int]
) -> None:
    user = await create_user("hank", restrict_models=True, allowed_model_ids=[model_ids[OPUS], model_ids[SONNET]])
    url = f"/api/admin/users/{user.user_id}"

    replaced = await admin_client.patch(url, json={"allowed_model_ids": [model_ids[HAIKU]]})
    cleared = await admin_client.patch(url, json={"allowed_model_ids": []})

    assert replaced.json()["allowed_model_ids"] == [model_ids[HAIKU]]
    assert cleared.status_code == 200
    assert cleared.json()["allowed_model_ids"] == []
    assert cleared.json()["restrict_models"] is True
    async with SessionLocal() as session:
        grants = list(
            await session.scalars(
                select(user_allowed_models.c.model_id).where(user_allowed_models.c.user_id == user.user_id)
            )
        )
    assert grants == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("password", "short"),
        ("password", "x" * 257),
        ("is_active", "nope"),
        ("monthly_token_limit", -5),
        ("rate_limit_per_min", -1),
        ("max_upload_mb", 0),
        ("max_upload_mb", settings.max_upload_mb_hard + 1),
        ("max_documents", MAX_INT + 1),
        ("max_storage_mb", -1),
        ("allowed_model_ids", [-1]),
    ],
)
async def test_update_user_validates_the_body(
    field: str, value: object, admin_client: ApiClient, create_user: CreateUser
) -> None:
    user = await create_user("ivy")
    before = await snapshot()

    response = await admin_client.patch(f"/api/admin/users/{user.user_id}", json={field: value})

    assert response.status_code == 422
    assert any(field in error["loc"] for error in response.json()["detail"])
    assert await snapshot() == before
    assert (await user.get("/api/auth/me")).status_code == 200


async def test_password_reset_ends_every_session(
    admin_client: ApiClient, create_user: CreateUser, new_client: Callable[[], ApiClient]
) -> None:
    user = await create_user("jack")
    second_session = new_client()
    assert (await second_session.login("jack", USER_PASSWORD)).status_code == 200

    response = await admin_client.patch(f"/api/admin/users/{user.user_id}", json={"password": "brand-new-password"})

    assert response.status_code == 200
    assert user.user_id is not None
    assert (await load(User, user.user_id)).token_version == 1
    assert (await user.get("/api/auth/me")).status_code == 401
    assert (await second_session.get("/api/auth/me")).status_code == 401
    assert (await new_client().login("jack", USER_PASSWORD)).status_code == 401
    fresh = new_client()
    assert (await fresh.login("jack", "brand-new-password")).status_code == 200
    assert (await fresh.get("/api/auth/me")).status_code == 200


async def test_blank_password_keeps_the_current_one(admin_client: ApiClient, create_user: CreateUser) -> None:
    user = await create_user("kate")
    assert user.user_id is not None
    hash_before = (await load(User, user.user_id)).password_hash

    response = await admin_client.patch(f"/api/admin/users/{user.user_id}", json={"password": "   "})

    assert response.status_code == 200
    row = await load(User, user.user_id)
    assert (row.password_hash, row.token_version) == (hash_before, 0)
    assert (await user.get("/api/auth/me")).status_code == 200


async def test_a_reset_password_is_kept_exactly_as_typed(
    admin_client: ApiClient, create_user: CreateUser, new_client: Callable[[], ApiClient]
) -> None:
    user = await create_user("kurt", login=False)

    url = f"/api/admin/users/{user.user_id}"

    assert (await admin_client.patch(url, json={"password": " Winter2026! "})).status_code == 200
    assert (await new_client().login("kurt", " Winter2026! ")).status_code == 200
    assert (await new_client().login("kurt", "Winter2026!")).status_code == 401
    assert (await admin_client.patch(url, json={"password": "  1234567"})).status_code == 200  # 9 characters
    assert (await new_client().login("kurt", "  1234567")).status_code == 200
    assert (await new_client().login("kurt", "1234567")).status_code == 401


async def test_deactivating_a_user_ends_their_sessions_and_blocks_login(
    admin_client: ApiClient, create_user: CreateUser, new_client: Callable[[], ApiClient]
) -> None:
    user = await create_user("liam")
    url = f"/api/admin/users/{user.user_id}"

    deactivated = await admin_client.patch(url, json={"is_active": False})

    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False
    assert user.user_id is not None
    assert (await load(User, user.user_id)).token_version == 1
    assert (await user.get("/api/auth/me")).status_code == 401
    blocked = await new_client().login("liam", USER_PASSWORD)
    assert (blocked.status_code, blocked.json()) == (401, {"detail": "Invalid username or password"})

    reactivated = await admin_client.patch(url, json={"is_active": True})

    assert reactivated.json()["is_active"] is True
    assert (await load(User, user.user_id)).token_version == 1
    assert (await user.get("/api/auth/me")).status_code == 401
    assert (await new_client().login("liam", USER_PASSWORD)).status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {"is_active": False},
        {"password": "another-admin-password"},
        {"monthly_token_limit": 10},
        {"restrict_models": True, "allowed_model_ids": []},
        {},
    ],
)
async def test_env_admin_cannot_be_modified(
    body: dict[str, Any], admin_client: ApiClient, new_client: Callable[[], ApiClient]
) -> None:
    before = await snapshot()

    response = await admin_client.patch(f"/api/admin/users/{admin_client.user_id}", json=body)

    assert response.status_code == 400
    assert response.json() == {"detail": ENV_ADMIN}
    assert await snapshot() == before
    assert (await admin_client.get("/api/auth/me")).status_code == 200
    assert (await new_client().login(ADMIN_USERNAME, ADMIN_PASSWORD)).status_code == 200


async def test_env_admin_cannot_be_deleted(admin_client: ApiClient) -> None:
    before = await snapshot()

    response = await admin_client.delete(f"/api/admin/users/{admin_client.user_id}")

    assert response.status_code == 400
    assert response.json() == {"detail": ENV_ADMIN}
    assert await snapshot() == before
    assert (await admin_client.get("/api/auth/me")).status_code == 200


@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
async def test_unknown_user_is_not_found(method: str, admin_client: ApiClient) -> None:
    body = {"monthly_token_limit": 1} if method == "PATCH" else None

    response = await admin_client.request(method, "/api/admin/users/999999", json=body)

    assert response.status_code == 404
    assert response.json() == {"detail": "User not found"}


@pytest.mark.parametrize(
    "path",
    [
        "/api/admin/users/0",
        f"/api/admin/users/{MAX_INT + 1}",
        "/api/admin/users/abc",
        "/api/admin/models/-1",
        "/api/admin/documents/0",
    ],
)
async def test_invalid_ids_in_the_path_are_rejected(path: str, admin_client: ApiClient) -> None:
    response = await admin_client.delete(path)

    assert response.status_code == 422


async def test_delete_user_removes_their_documents_conversations_and_usage(
    admin_client: ApiClient, create_user: CreateUser, model_ids: dict[str, int]
) -> None:
    small = model_ids[SMALL]
    alice = await create_user("alice")
    bob = await create_user("bob")
    alice_docs = [
        await ingest(alice, "alpha.txt", txt_bytes("Alpha project budget review for the northern region office.")),
        await ingest(alice, "beta.md", md_bytes("Beta rollout checklist covering staging and production steps.")),
    ]
    queued = await upload(alice, "gamma.txt", txt_bytes("Gamma draft that is still waiting in the ingestion queue."))
    bob_doc = await ingest(bob, "bob.txt", txt_bytes("Bob keeps the warehouse inventory spreadsheet summary here."))
    shared_doc = await ingest(
        admin_client, "handbook.txt", txt_bytes("Company handbook covering travel and expenses."), "shared"
    )
    _, alice_events = await chat(alice, "Summarise the alpha project budget review.")
    _, bob_events = await chat(bob, "What does the warehouse inventory say?")
    assert queued.status_code == 201
    assert all(doc["status"] == "ready" for doc in [*alice_docs, bob_doc, shared_doc])
    alice_doc_ids = [doc["id"] for doc in alice_docs] + [queued.json()["id"]]
    async with SessionLocal() as session:
        alice_files = [
            stored_path(name)
            for name in await session.scalars(select(Document.stored_name).where(Document.id.in_(alice_doc_ids)))
        ]
    assert len(alice_files) == 3 and all(path.exists() for path in alice_files)
    assert all([await point_count(small, doc["id"]) > 0 for doc in alice_docs])
    alice_conversation = alice_events[0][1]["conversation_id"]

    response = await admin_client.delete(f"/api/admin/users/{alice.user_id}")

    assert response.status_code == 204
    assert await load(User, alice.user_id) is None
    async with SessionLocal() as session:
        assert list(await session.scalars(select(Document.id).where(Document.id.in_(alice_doc_ids)))) == []
        assert await session.get(Conversation, alice_conversation) is None
        assert (
            list(await session.scalars(select(Message.id).where(Message.conversation_id == alice_conversation))) == []
        )
        assert list(await session.scalars(select(UsageEvent.id).where(UsageEvent.user_id == alice.user_id))) == []
        assert list(await session.scalars(select(UsageEvent.id).where(UsageEvent.user_id == bob.user_id))) != []
    assert not any(path.exists() for path in alice_files)
    assert [await point_count(small, doc_id) for doc_id in alice_doc_ids] == [0, 0, 0]
    assert (await alice.get("/api/auth/me")).status_code == 401
    assert (await alice.login("alice", USER_PASSWORD)).status_code == 401
    assert [user["username"] for user in (await admin_client.get("/api/admin/users")).json()] == [ADMIN_USERNAME, "bob"]

    assert await point_count(small, bob_doc["id"]) > 0
    assert await point_count(small, shared_doc["id"]) > 0
    remaining = (await admin_client.get("/api/admin/documents")).json()
    assert sorted(doc["id"] for doc in remaining) == sorted([bob_doc["id"], shared_doc["id"]])
    bob_conversation = await bob.get(f"/api/conversations/{bob_events[0][1]['conversation_id']}")
    assert bob_conversation.status_code == 200

    # The deleted user's queued document is skipped harmlessly when the worker reaches it.
    assert await process_queue() == [queued.json()["id"]]
    assert await point_count(small, queued.json()["id"]) == 0


async def test_delete_user_keeps_their_documents_while_vectors_cannot_be_removed(
    admin_client: ApiClient, create_user: CreateUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = await create_user("alice")
    alpha = await ingest(alice, "alpha.txt", txt_bytes("Alpha project budget review for the northern region office."))
    beta = await ingest(alice, "beta.txt", txt_bytes("Beta rollout checklist covering staging and production steps."))
    doc_ids = [alpha["id"], beta["id"]]
    async with SessionLocal() as session:
        files = [
            stored_path(name)
            for name in await session.scalars(select(Document.stored_name).where(Document.id.in_(doc_ids)))
        ]
    remove_points = vectorstore.delete_document_points

    async def fail_after_the_first_document(name: str, doc_id: int) -> None:
        if doc_id != doc_ids[0]:
            raise ConnectionError("Qdrant is unreachable")
        await remove_points(name, doc_id)

    with monkeypatch.context() as patch:
        patch.setattr(vectorstore, "delete_document_points", fail_after_the_first_document)
        failed = await admin_client.delete(f"/api/admin/users/{alice.user_id}")

    assert failed.status_code == 503
    assert await load(User, alice.user_id) is not None
    assert sorted(doc["id"] for doc in (await alice.get("/api/documents")).json()) == sorted(doc_ids)
    assert len(files) == 2 and all(path.exists() for path in files)

    retried = await admin_client.delete(f"/api/admin/users/{alice.user_id}")

    assert retried.status_code == 204
    assert await load(User, alice.user_id) is None
    assert not any(path.exists() for path in files)


async def test_list_users_reports_this_months_usage_and_documents(
    admin_client: ApiClient, create_user: CreateUser, model_ids: dict[str, int], fake_llm: FakeLLM
) -> None:
    # $1 per 1M embedding tokens, so the embedding cost is visible in cost_usd.
    priced = await admin_client.patch(f"/api/admin/models/{model_ids[SMALL]}", json={"input_cost_per_mtok": 1.0})
    assert priced.status_code == 200
    alice = await create_user("alice")
    bob = await create_user("bob")
    carol = await create_user("carol")
    files = {
        "report.txt": txt_bytes("The annual report describes revenue growth across every region."),
        "minutes.md": md_bytes("Meeting minutes about hiring plans and the new office lease."),
    }
    for filename, data in files.items():
        assert (await ingest(alice, filename, data))["status"] == "ready"
    response, _ = await chat(alice, "How did revenue grow according to the annual report?")
    assert response.status_code == 200
    alice_embedding_tokens = embedded_words(fake_llm)
    now = datetime.now(timezone.utc)
    last_month = month_start() - timedelta(seconds=1)
    await add_rows(
        UsageEvent(
            user_id=alice.user_id,
            model_name="anthropic/claude-sonnet-5-5",
            kind="chat",
            prompt_tokens=1000,
            completion_tokens=200,
            total_tokens=1200,
            cost_usd=0.5,
            created_at=now,
        ),
        UsageEvent(
            user_id=alice.user_id,
            model_name="anthropic/claude-sonnet-5-5",
            kind="chat",
            prompt_tokens=9000,
            completion_tokens=0,
            total_tokens=9000,
            cost_usd=9.0,
            created_at=last_month,
        ),
        UsageEvent(
            user_id=bob.user_id,
            model_name="openai/text-embedding-3-small",
            kind="embedding",
            prompt_tokens=70,
            completion_tokens=0,
            total_tokens=70,
            cost_usd=0.25,
            created_at=now,
        ),
        UsageEvent(
            user_id=bob.user_id,
            model_name="openai/gpt-5.5",
            kind="chat",
            prompt_tokens=500,
            completion_tokens=500,
            total_tokens=1000,
            cost_usd=3.0,
            created_at=last_month,
        ),
        UsageEvent(
            user_id=carol.user_id,
            model_name="anthropic/claude-sonnet-5-5",
            kind="vision",
            prompt_tokens=2500,
            completion_tokens=500,
            total_tokens=3000,
            cost_usd=0.01,
            created_at=now,
        ),
    )

    listing = await admin_client.get("/api/admin/users")

    assert listing.status_code == 200
    users = {user["username"]: user for user in listing.json()}
    chat_cost = (42 * 4.0 + 5 * 20.0) / 1_000_000
    assert users["alice"]["usage"] == {
        "chat_tokens": 47 + 1200,
        "embedding_tokens": alice_embedding_tokens,
        "vision_tokens": 0,
        "images": 0,
        "cost_usd": pytest.approx(0.5 + chat_cost + alice_embedding_tokens / 1_000_000, abs=1e-6),
    }
    assert users["alice"]["document_count"] == 2
    assert users["alice"]["storage_bytes"] == sum(len(data) for data in files.values())
    assert users["bob"]["usage"] == {
        "chat_tokens": 0,
        "embedding_tokens": 70,
        "vision_tokens": 0,
        "images": 0,
        "cost_usd": 0.25,
    }
    assert (users["bob"]["document_count"], users["bob"]["storage_bytes"]) == (0, 0)
    assert users["carol"]["usage"] == {
        "chat_tokens": 0,
        "embedding_tokens": 0,
        "vision_tokens": 3000,
        "images": 0,
        "cost_usd": 0.01,
    }
    assert (users["carol"]["document_count"], users["carol"]["storage_bytes"]) == (0, 0)
    assert users[ADMIN_USERNAME]["usage"] == {
        "chat_tokens": 0,
        "embedding_tokens": 0,
        "vision_tokens": 0,
        "images": 0,
        "cost_usd": 0.0,
    }
    assert {user["id"] for user in users.values()} == {
        admin_client.user_id,
        alice.user_id,
        bob.user_id,
        carol.user_id,
    }


async def test_list_users_query_count_does_not_grow_with_users(
    admin_client: ApiClient, create_user: CreateUser, model_ids: dict[str, int]
) -> None:
    async def listing_queries() -> int:
        with recorded_queries() as statements:
            response = await admin_client.get("/api/admin/users")
        assert response.status_code == 200
        return len(statements)

    first = await create_user(restrict_models=True, allowed_model_ids=[model_ids[HAIKU]])
    await ingest(first, "first.txt", txt_bytes("The first user's notes about the design review."))
    few = await listing_queries()
    for number in range(4):
        user = await create_user(restrict_models=True, allowed_model_ids=[model_ids[OPUS], model_ids[GPT]])
        await ingest(user, "notes.txt", txt_bytes(f"Notes number {number} about the quarterly design review."))

    assert await listing_queries() == few


# ---------------- Models ----------------


async def test_list_models_returns_every_model_by_kind_and_name(
    admin_client: ApiClient, model_ids: dict[str, int]
) -> None:
    response = await admin_client.get("/api/admin/models")

    assert response.status_code == 200
    models = response.json()
    by_kind_and_name = [HAIKU, OPUS, SONNET, GPT_MINI, GPT, LARGE, SMALL, GPT_IMAGE, SONNET_VISION]
    assert [model["display_name"] for model in models] == by_kind_and_name
    opus = next(model for model in models if model["display_name"] == OPUS)
    assert opus == {
        "id": model_ids[OPUS],
        "display_name": OPUS,
        "litellm_model": "anthropic/claude-opus-5-5",
        "kind": "chat",
        "enabled": True,
        "is_default": True,
        "api_base": None,
        "api_key_env": None,
        "max_output_tokens": None,
        "temperature": None,
        "input_cost_per_mtok": 4.0,
        "output_cost_per_mtok": 20.0,
        "embedding_dim": None,
        "created_at": opus["created_at"],
        "updated_at": opus["updated_at"],
    }
    assert opus["created_at"].endswith("Z") and opus["updated_at"].endswith("Z")
    assert {model["display_name"]: (model["kind"], model["enabled"], model["is_default"]) for model in models} == {
        HAIKU: ("chat", True, False),
        OPUS: ("chat", True, True),
        SONNET: ("chat", True, False),
        GPT_MINI: ("chat", True, False),
        GPT: ("chat", True, False),
        LARGE: ("embedding", False, False),
        SMALL: ("embedding", True, True),
        GPT_IMAGE: ("image", True, True),
        SONNET_VISION: ("vision", True, True),
    }


async def test_create_model_stores_every_field(admin_client: ApiClient) -> None:
    body = {
        "display_name": "  Local Llama  ",
        "litellm_model": " ollama/llama3.1 ",
        "kind": "chat",
        "enabled": True,
        "is_default": False,
        "api_base": "http://host.docker.internal:11434",
        "api_key_env": "OLLAMA_API_KEY",
        "max_output_tokens": 4096,
        "temperature": 0.2,
        "input_cost_per_mtok": 0,
        "output_cost_per_mtok": 0.5,
    }

    response = await admin_client.post("/api/admin/models", json=body)

    assert response.status_code == 201, response.text
    created = response.json()
    assert created == {
        **body,
        "display_name": "Local Llama",
        "litellm_model": "ollama/llama3.1",
        "input_cost_per_mtok": 0.0,
        "id": created["id"],
        "embedding_dim": None,
        "created_at": created["created_at"],
        "updated_at": created["updated_at"],
    }
    assert await admin_model(admin_client, created["id"]) == created
    row = await load(LLMModel, created["id"])
    assert (row.display_name, row.litellm_model, row.api_key_env) == (
        "Local Llama",
        "ollama/llama3.1",
        "OLLAMA_API_KEY",
    )


async def test_create_model_defaults_and_blank_optional_fields(admin_client: ApiClient) -> None:
    response = await admin_client.post(
        "/api/admin/models",
        json={
            "display_name": "Ada embeddings",
            "litellm_model": "openai/text-embedding-ada-002",
            "kind": "embedding",
            "api_base": "",
            "api_key_env": "   ",
        },
    )

    assert response.status_code == 201, response.text
    created = response.json()
    assert {key: created[key] for key in ("enabled", "is_default", "api_base", "api_key_env", "temperature")} == {
        "enabled": True,
        "is_default": False,
        "api_base": None,
        "api_key_env": None,
        "temperature": None,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("api_base", "http://host.docker.internal:11434"),
        ("api_base", "https://api.example.com/v1"),
        ("api_base", "http://127.0.0.1:4000/"),
        ("api_key_env", "GROQ_API_KEY"),
        ("api_key_env", "A_API_KEY"),
        ("api_key_env", "MY_PROVIDER_2_API_KEY"),
        ("temperature", 0),
        ("temperature", 2),
        ("max_output_tokens", 1),
        ("max_output_tokens", 1_000_000),
        ("litellm_model", "azure/my-deployment"),
    ],
)
async def test_create_model_accepts_valid_settings(field: str, value: object, admin_client: ApiClient) -> None:
    body = {"display_name": "Valid", "litellm_model": "openai/gpt-5.5", "kind": "chat", field: value}

    response = await admin_client.post("/api/admin/models", json=body)

    assert response.status_code == 201, response.text
    assert response.json()[field] == value


MODEL_VALIDATION_CASES = [
    ("display_name", ""),
    ("display_name", "   "),
    ("display_name", "x" * 101),
    ("display_name", "line\nbreak"),
    ("litellm_model", ""),
    ("litellm_model", "openai/gpt 5"),
    ("litellm_model", "openai/\tgpt-5"),
    ("litellm_model", "x" * 201),
    ("api_base", "ftp://example.com"),
    ("api_base", "localhost:11434"),
    ("api_base", "http://"),
    ("api_base", "http://exa mple.com"),
    ("api_base", "javascript:alert(1)"),
    ("api_base", "https://example.com/" + "a" * 500),
    ("api_key_env", "openai_api_key"),
    ("api_key_env", "OPENAI_KEY"),
    ("api_key_env", "1PROVIDER_API_KEY"),
    ("api_key_env", "MY-PROVIDER_API_KEY"),
    ("api_key_env", "SECRET_KEY"),
    ("api_key_env", "QDRANT_API_KEY"),
    ("max_output_tokens", 0),
    ("max_output_tokens", 1_000_001),
    ("temperature", -0.1),
    ("temperature", 2.01),
    ("input_cost_per_mtok", -1),
    ("output_cost_per_mtok", -0.01),
]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        *MODEL_VALIDATION_CASES,
        ("display_name", MISSING),
        ("litellm_model", MISSING),
        ("kind", MISSING),
        ("kind", "rerank"),
        ("enabled", "perhaps"),
    ],
)
async def test_create_model_validates_the_body(field: str, value: object, admin_client: ApiClient) -> None:
    before = await snapshot()
    body = with_changes({"display_name": "Candidate", "litellm_model": "openai/gpt-5.5", "kind": "chat"}, field, value)

    response = await admin_client.post("/api/admin/models", json=body)

    assert response.status_code == 422
    assert any(field in error["loc"] for error in response.json()["detail"])
    assert await snapshot() == before


@pytest.mark.parametrize(("field", "value"), MODEL_VALIDATION_CASES)
async def test_update_model_validates_the_body(
    field: str, value: object, admin_client: ApiClient, model_ids: dict[str, int]
) -> None:
    original = await admin_model(admin_client, model_ids[SMALL])

    response = await admin_client.patch(f"/api/admin/models/{model_ids[SMALL]}", json={field: value})

    assert response.status_code == 422
    assert any(field in error["loc"] for error in response.json()["detail"])
    assert await admin_model(admin_client, model_ids[SMALL]) == original


async def test_update_model_null_clears_settings_but_keeps_required_fields(
    admin_client: ApiClient, model_ids: dict[str, int]
) -> None:
    url = f"/api/admin/models/{model_ids[HAIKU]}"
    configured = await admin_client.patch(
        url,
        json={
            "api_base": "http://proxy.internal:4000",
            "api_key_env": "PROXY_API_KEY",
            "temperature": 0.5,
            "max_output_tokens": 1000,
            "display_name": "Haiku (proxied)",
            "kind": "embedding",
        },
    )
    assert configured.status_code == 200, configured.text
    assert configured.json()["kind"] == "chat"

    response = await admin_client.patch(
        url,
        json={
            "api_base": None,
            "api_key_env": "",
            "temperature": None,
            "max_output_tokens": None,
            "input_cost_per_mtok": None,
            "display_name": None,
            "litellm_model": None,
            "enabled": None,
            "is_default": None,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        **configured.json(),
        "api_base": None,
        "api_key_env": None,
        "temperature": None,
        "max_output_tokens": None,
        "input_cost_per_mtok": None,
        "updated_at": response.json()["updated_at"],
    }
    assert response.json()["display_name"] == "Haiku (proxied)"


async def test_making_a_model_the_default_clears_the_previous_default(
    admin_client: ApiClient, model_ids: dict[str, int]
) -> None:
    response = await admin_client.patch(f"/api/admin/models/{model_ids[SONNET]}", json={"is_default": True})

    assert response.status_code == 200
    assert response.json()["is_default"] is True
    models = (await admin_client.get("/api/admin/models")).json()
    assert [model["display_name"] for model in models if model["is_default"]] == [
        SONNET,
        SMALL,
        GPT_IMAGE,
        SONNET_VISION,
    ]

    created = await admin_client.post(
        "/api/admin/models",
        json={"display_name": "House model", "litellm_model": "openai/house", "kind": "chat", "is_default": True},
    )

    assert created.status_code == 201
    models = (await admin_client.get("/api/admin/models")).json()
    assert [model["display_name"] for model in models if model["is_default"]] == [
        "House model",
        SMALL,
        GPT_IMAGE,
        SONNET_VISION,
    ]
    listed = (await admin_client.get("/api/models")).json()
    assert listed[0] == {
        "id": created.json()["id"],
        "display_name": "House model",
        "provider": "openai",
        "is_default": True,
        "can_create_files": False,  # LiteLLM doesn't know the model
        "can_create_images": False,
        "supports_tools": False,
    }


async def test_new_default_embedding_model_becomes_the_active_one(
    admin_client: ApiClient, model_ids: dict[str, int]
) -> None:
    created = await admin_client.post(
        "/api/admin/models",
        json={
            "display_name": "Local embeddings",
            "litellm_model": "ollama/nomic-embed-text",
            "kind": "embedding",
            "is_default": True,
        },
    )

    assert created.status_code == 201
    assert (await stats(admin_client))["active_embedding_model"] == {
        "id": created.json()["id"],
        "display_name": "Local embeddings",
    }
    assert (await admin_model(admin_client, model_ids[SMALL]))["is_default"] is False
    assert (await admin_model(admin_client, model_ids[OPUS]))["is_default"] is True


async def test_disabled_model_cannot_become_the_default(admin_client: ApiClient, model_ids: dict[str, int]) -> None:
    disabled = await admin_client.patch(f"/api/admin/models/{model_ids[GPT]}", json={"enabled": False})
    assert disabled.status_code == 200
    before = await snapshot()
    refused = {"detail": "Enable the model before making it the default"}

    patched = await admin_client.patch(f"/api/admin/models/{model_ids[GPT]}", json={"is_default": True})
    disabling = await admin_client.patch(
        f"/api/admin/models/{model_ids[SONNET]}", json={"is_default": True, "enabled": False}
    )
    embedding = await admin_client.patch(f"/api/admin/models/{model_ids[LARGE]}", json={"is_default": True})
    created = await admin_client.post(
        "/api/admin/models",
        json={
            "display_name": "Off",
            "litellm_model": "openai/off",
            "kind": "chat",
            "enabled": False,
            "is_default": True,
        },
    )

    for response in (patched, disabling, embedding, created):
        assert (response.status_code, response.json()) == (400, refused)
    assert await snapshot() == before


async def test_enabling_and_defaulting_in_one_request(admin_client: ApiClient, model_ids: dict[str, int]) -> None:
    await admin_client.patch(f"/api/admin/models/{model_ids[GPT]}", json={"enabled": False})

    chat_model = await admin_client.patch(
        f"/api/admin/models/{model_ids[GPT]}", json={"enabled": True, "is_default": True}
    )
    embedding = await admin_client.patch(
        f"/api/admin/models/{model_ids[LARGE]}", json={"enabled": True, "is_default": True}
    )

    assert chat_model.status_code == 200 and embedding.status_code == 200
    models = (await admin_client.get("/api/admin/models")).json()
    assert [model["display_name"] for model in models if model["is_default"]] == [GPT, LARGE, GPT_IMAGE, SONNET_VISION]
    assert (await stats(admin_client))["active_embedding_model"] == {"id": model_ids[LARGE], "display_name": LARGE}


@pytest.mark.parametrize(
    ("method", "body"),
    [
        ("PATCH", {"enabled": False}),
        ("PATCH", {"is_default": False}),
        ("PATCH", {"enabled": False, "is_default": False, "display_name": "Renamed"}),
        ("DELETE", None),
    ],
)
async def test_active_embedding_model_cannot_be_disabled_unset_or_deleted(
    method: str, body: dict[str, Any] | None, admin_client: ApiClient, model_ids: dict[str, int]
) -> None:
    before = await snapshot()

    response = await admin_client.request(method, f"/api/admin/models/{model_ids[SMALL]}", json=body)

    assert response.status_code == 409
    assert response.json() == {"detail": ACTIVE_EMBEDDING}
    assert await snapshot() == before
    assert (await stats(admin_client))["active_embedding_model"] == {"id": model_ids[SMALL], "display_name": SMALL}


async def test_active_embedding_model_can_still_be_edited(admin_client: ApiClient, model_ids: dict[str, int]) -> None:
    response = await admin_client.patch(
        f"/api/admin/models/{model_ids[SMALL]}",
        json={"enabled": True, "is_default": True, "display_name": "Small embeddings", "input_cost_per_mtok": 0.02},
    )

    assert response.status_code == 200, response.text
    assert {key: response.json()[key] for key in ("enabled", "is_default", "display_name", "input_cost_per_mtok")} == {
        "enabled": True,
        "is_default": True,
        "display_name": "Small embeddings",
        "input_cost_per_mtok": 0.02,
    }


async def test_deleting_an_inactive_embedding_model_drops_its_vectors(
    admin_client: ApiClient, create_user: CreateUser, model_ids: dict[str, int]
) -> None:
    small, large = model_ids[SMALL], model_ids[LARGE]
    alice = await create_user("alice")
    document = await ingest(alice, "plan.txt", txt_bytes("The migration plan moves billing to the new cluster."))
    assert await point_count(small, document["id"]) > 0
    await activate(admin_client, large)

    response = await admin_client.delete(f"/api/admin/models/{small}")

    assert response.status_code == 204
    assert await load(LLMModel, small) is None
    assert not await get_qdrant().collection_exists(collection_name(small))
    row = await load(Document, document["id"])
    assert (row.status, row.embedding_model_id, row.num_chunks) == ("ready", None, document["num_chunks"])
    [listed] = (await alice.get("/api/documents")).json()
    assert listed["is_stale"] is True
    assert (await stats(admin_client))["stale_documents"] == 1

    assert (await admin_client.post("/api/admin/reindex")).json() == {"queued": 1}
    assert await process_queue() == [document["id"]]
    assert (await load(Document, document["id"])).embedding_model_id == large
    assert await point_count(large, document["id"]) > 0


@pytest.mark.parametrize("change", ["edit", "delete"])
async def test_embedding_model_changes_that_drop_vectors_change_nothing_while_qdrant_is_down(
    change: str,
    admin_client: ApiClient,
    create_user: CreateUser,
    model_ids: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    small, large = model_ids[SMALL], model_ids[LARGE]
    alice = await create_user("alice")
    document = await ingest(alice, "plan.txt", txt_bytes("The migration plan moves billing to the new cluster."))
    if change == "delete":
        await activate(admin_client, large)
    before = await snapshot()

    async def qdrant_down(name: str) -> None:
        raise ConnectionError("Qdrant is unreachable")

    monkeypatch.setattr(vectorstore, "drop_collection", qdrant_down)
    if change == "edit":
        response = await admin_client.patch(
            f"/api/admin/models/{small}", json={"litellm_model": "openai/text-embedding-3-large"}
        )
    else:
        response = await admin_client.delete(f"/api/admin/models/{small}")

    assert response.status_code == 503
    assert response.json() == {"detail": "The vector database is unavailable. Try again shortly."}
    assert await snapshot() == before
    model = await load(LLMModel, small)
    assert model is not None and model.litellm_model == "openai/text-embedding-3-small"
    assert (await load(Document, document["id"])).embedding_model_id == small
    assert await point_count(small, document["id"]) == document["num_chunks"]


async def test_deleting_an_unused_embedding_model(admin_client: ApiClient, model_ids: dict[str, int]) -> None:
    response = await admin_client.delete(f"/api/admin/models/{model_ids[LARGE]}")

    assert response.status_code == 204
    assert [model["display_name"] for model in (await admin_client.get("/api/admin/models")).json()] == [
        HAIKU,
        OPUS,
        SONNET,
        GPT_MINI,
        GPT,
        SMALL,
        GPT_IMAGE,
        SONNET_VISION,
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [("litellm_model", "openai/text-embedding-3-large"), ("api_base", "http://embeddings.internal:4000")],
)
async def test_changing_where_embeddings_come_from_drops_vectors_and_marks_documents_stale(
    field: str, value: str, admin_client: ApiClient, create_user: CreateUser, model_ids: dict[str, int]
) -> None:
    small = model_ids[SMALL]
    alice = await create_user("alice")
    document = await ingest(alice, "faq.txt", txt_bytes("Frequently asked questions about the expense policy."))
    assert document["status"] == "ready" and document["is_stale"] is False
    assert (await admin_model(admin_client, small))["embedding_dim"] == 64
    assert await point_count(small, document["id"]) > 0

    response = await admin_client.patch(f"/api/admin/models/{small}", json={field: value})

    assert response.status_code == 200, response.text
    assert response.json()[field] == value
    assert response.json()["embedding_dim"] is None
    assert response.json()["is_default"] is True
    assert not await get_qdrant().collection_exists(collection_name(small))
    assert (await load(Document, document["id"])).embedding_model_id is None
    [listed] = (await alice.get("/api/documents")).json()
    assert (listed["status"], listed["is_stale"]) == ("ready", True)
    assert (await stats(admin_client))["stale_documents"] == 1

    assert (await admin_client.post("/api/admin/reindex", json={"only_stale": True})).json() == {"queued": 1}
    assert await process_queue() == [document["id"]]
    [listed] = (await alice.get("/api/documents")).json()
    assert (listed["status"], listed["is_stale"]) == ("ready", False)
    assert await point_count(small, document["id"]) > 0
    assert (await admin_model(admin_client, small))["embedding_dim"] == 64


async def test_unrelated_model_changes_keep_vectors(
    admin_client: ApiClient, create_user: CreateUser, model_ids: dict[str, int]
) -> None:
    small = model_ids[SMALL]
    alice = await create_user("alice")
    document = await ingest(alice, "faq.txt", txt_bytes("Frequently asked questions about the expense policy."))
    points = await point_count(small, document["id"])

    embedding = await admin_client.patch(
        f"/api/admin/models/{small}",
        json={
            "litellm_model": "openai/text-embedding-3-small",
            "api_base": None,
            "display_name": "Small",
            "input_cost_per_mtok": 0.02,
        },
    )
    chat_model = await admin_client.patch(
        f"/api/admin/models/{model_ids[OPUS]}",
        json={"litellm_model": "anthropic/claude-opus-5-5-latest", "api_base": "http://proxy.internal:4000"},
    )

    assert embedding.status_code == 200 and chat_model.status_code == 200
    assert embedding.json()["embedding_dim"] == 64
    assert await point_count(small, document["id"]) == points > 0
    assert (await load(Document, document["id"])).embedding_model_id == small
    assert (await stats(admin_client))["stale_documents"] == 0


async def test_deleting_a_chat_model_removes_access_but_keeps_history(
    admin_client: ApiClient, create_user: CreateUser, model_ids: dict[str, int]
) -> None:
    haiku, sonnet = model_ids[HAIKU], model_ids[SONNET]
    alice = await create_user("alice", restrict_models=True, allowed_model_ids=[haiku, sonnet])
    response, events = await chat(alice, "Say hello please.", model_id=haiku, document_ids=[])
    assert response.status_code == 200
    assistant_id = events[-1][1]["assistant_message_id"]

    deleted = await admin_client.delete(f"/api/admin/models/{haiku}")

    assert deleted.status_code == 204
    assert await load(LLMModel, haiku) is None
    assert alice.user_id is not None
    assert (await admin_user(admin_client, alice.user_id))["allowed_model_ids"] == [sonnet]
    assert [model["id"] for model in (await alice.get("/api/models")).json()] == [sonnet]
    async with SessionLocal() as session:
        grants = list(
            await session.scalars(select(user_allowed_models.c.user_id).where(user_allowed_models.c.model_id == haiku))
        )
        usage = list(await session.scalars(select(UsageEvent).where(UsageEvent.kind == "chat")))
    assert grants == []
    assert [(event.model_id, event.model_name, event.total_tokens) for event in usage] == [
        (None, "anthropic/claude-haiku-5-5", 47)
    ]
    message = await load(Message, assistant_id)
    assert (message.model_id, message.content) == (None, "Hello from the fake model.")

    default_deleted = await admin_client.delete(f"/api/admin/models/{model_ids[OPUS]}")

    assert default_deleted.status_code == 204


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [("PATCH", "", {"display_name": "Ghost"}), ("DELETE", "", None), ("POST", "/test", None)],
)
async def test_unknown_model_is_not_found(
    method: str, suffix: str, body: dict[str, Any] | None, admin_client: ApiClient, fake_llm: FakeLLM
) -> None:
    response = await admin_client.request(method, f"/api/admin/models/999999{suffix}", json=body)

    assert response.status_code == 404
    assert response.json() == {"detail": "Model not found"}
    assert fake_llm.chat_calls == [] and fake_llm.embedding_calls == []


async def test_model_test_calls_a_chat_model_with_its_settings(
    admin_client: ApiClient, model_ids: dict[str, int], fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROXY_API_KEY", "proxy-secret-value")
    haiku = model_ids[HAIKU]
    configured = await admin_client.patch(
        f"/api/admin/models/{haiku}",
        json={"api_base": "https://llm-proxy.example.com/v1", "api_key_env": "PROXY_API_KEY", "enabled": False},
    )
    assert configured.status_code == 200
    fake_llm.reply = "Hello there"

    response = await admin_client.post(f"/api/admin/models/{haiku}/test")

    assert response.status_code == 200
    result = response.json()
    assert result == {
        "ok": True,
        "message": "OK: model replied 'Hello there'",
        "latency_ms": result["latency_ms"],
        "embedding_dim": None,
        "image_data_url": None,
    }
    assert isinstance(result["latency_ms"], int) and result["latency_ms"] >= 0
    [call] = fake_llm.chat_calls
    assert {key: call.get(key) for key in ("model", "api_base", "api_key", "stream")} == {
        "model": "anthropic/claude-haiku-5-5",
        "api_base": "https://llm-proxy.example.com/v1",
        "api_key": "proxy-secret-value",
        "stream": None,
    }
    assert (await admin_model(admin_client, haiku))["embedding_dim"] is None


async def test_model_test_holds_no_database_connection_during_the_call(
    admin_client: ApiClient, model_ids: dict[str, int], fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    held: list[int] = []

    async def acompletion(**kwargs: Any) -> Any:
        held.append(db.engine.pool.checkedout())  # type: ignore[attr-defined]
        return await fake_llm.acompletion(**kwargs)

    monkeypatch.setattr(litellm, "acompletion", acompletion)

    response = await admin_client.post(f"/api/admin/models/{model_ids[OPUS]}/test")

    assert response.json()["ok"] is True
    assert held == [0]  # a slow provider would otherwise keep a pooled connection idle in a transaction


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (
            litellm.AuthenticationError(message="invalid x-api-key", llm_provider="anthropic", model="claude"),
            "The model provider rejected the API key. Check the provider key in .env.",
        ),
        (
            litellm.RateLimitError(message="slow down", llm_provider="anthropic", model="claude"),
            "The model provider is rate limiting requests. Please try again shortly.",
        ),
        (RuntimeError("socket closed"), "Model call failed: socket closed"),
    ],
)
async def test_model_test_reports_provider_errors(
    error: BaseException, message: str, admin_client: ApiClient, model_ids: dict[str, int], fake_llm: FakeLLM
) -> None:
    fake_llm.fail_after = 0
    fake_llm.error = error

    response = await admin_client.post(f"/api/admin/models/{model_ids[OPUS]}/test")

    assert response.status_code == 200
    result = response.json()
    assert result == {
        "ok": False,
        "message": message,
        "latency_ms": result["latency_ms"],
        "embedding_dim": None,
        "image_data_url": None,
    }


async def test_model_test_stores_the_embedding_dimension(
    admin_client: ApiClient, model_ids: dict[str, int], fake_llm: FakeLLM
) -> None:
    fake_llm.embedding_dim = 32
    large = model_ids[LARGE]

    response = await admin_client.post(f"/api/admin/models/{large}/test")

    assert response.status_code == 200
    result = response.json()
    assert result == {
        "ok": True,
        "message": "OK: returned 32-dimensional embeddings",
        "latency_ms": result["latency_ms"],
        "embedding_dim": 32,
        "image_data_url": None,
    }
    assert [(call["model"], call["input"]) for call in fake_llm.embedding_calls] == [
        ("openai/text-embedding-3-large", ["connection test"])
    ]
    model = await admin_model(admin_client, large)
    assert (model["embedding_dim"], model["enabled"]) == (32, False)


async def test_failed_embedding_test_keeps_the_dimension(
    admin_client: ApiClient, model_ids: dict[str, int], fake_llm: FakeLLM
) -> None:
    large = model_ids[LARGE]
    await admin_client.post(f"/api/admin/models/{large}/test")
    fake_llm.embedding_error = litellm.APIConnectionError(message="refused", llm_provider="openai", model="x")

    response = await admin_client.post(f"/api/admin/models/{large}/test")

    assert response.status_code == 200
    result = response.json()
    assert result == {
        "ok": False,
        "message": "Could not connect to the model provider.",
        "latency_ms": result["latency_ms"],
        "embedding_dim": None,
        "image_data_url": None,
    }
    assert (await admin_model(admin_client, large))["embedding_dim"] == 64


# ---------------- Stats ----------------


async def test_stats_on_a_fresh_install(admin_client: ApiClient, model_ids: dict[str, int]) -> None:
    assert await stats(admin_client) == {
        "users": 1,
        "documents": 0,
        "stale_documents": 0,
        "failed_documents": 0,
        "active_embedding_model": {"id": model_ids[SMALL], "display_name": SMALL},
        "active_vision_model": {"id": model_ids[SONNET_VISION], "display_name": SONNET_VISION},
        "active_image_model": {"id": model_ids[GPT_IMAGE], "display_name": GPT_IMAGE},
        "queue_length": 0,
    }


async def test_stats_count_documents_by_state_and_the_queue(
    admin_client: ApiClient, create_user: CreateUser, model_ids: dict[str, int]
) -> None:
    alice = await create_user("alice")
    bob = await create_user("bob")
    ready = await ingest(alice, "ready.txt", txt_bytes("Ready document about the onboarding programme."))
    failed = await ingest(bob, "scan.pdf", pdf_bytes(""))
    pending = await upload(bob, "later.txt", txt_bytes("Pending document waiting for the ingestion worker."))
    await ingest(admin_client, "shared.txt", txt_bytes("Shared policy about remote work and travel."), "shared")
    assert (ready["status"], failed["status"], pending.status_code) == ("ready", "failed", 201)

    before_switch = await stats(admin_client)
    await activate(admin_client, model_ids[LARGE])
    after_switch = await stats(admin_client)

    assert before_switch == {
        "users": 3,
        "documents": 4,
        "stale_documents": 0,
        "failed_documents": 1,
        "active_embedding_model": {"id": model_ids[SMALL], "display_name": SMALL},
        "active_vision_model": {"id": model_ids[SONNET_VISION], "display_name": SONNET_VISION},
        "active_image_model": {"id": model_ids[GPT_IMAGE], "display_name": GPT_IMAGE},
        "queue_length": 1,
    }
    assert after_switch == {
        **before_switch,
        "stale_documents": 2,
        "active_embedding_model": {"id": model_ids[LARGE], "display_name": LARGE},
    }


async def test_stats_without_an_active_embedding_model(admin_client: ApiClient, create_user: CreateUser) -> None:
    alice = await create_user("alice")
    await ingest(alice, "ready.txt", txt_bytes("Ready document about the onboarding programme."))
    await remove_active_embedding_model()

    result = await stats(admin_client)

    assert result["active_embedding_model"] is None
    assert (result["documents"], result["stale_documents"]) == (1, 1)


# ---------------- Re-indexing ----------------


async def _documents_in_every_state(
    admin_client: ApiClient, create_user: CreateUser, model_ids: dict[str, int]
) -> dict[str, int]:
    """Document ids by state: stale (embedded with the old model), current, failed and pending (queued)."""
    alice = await create_user("alice")
    stale = await ingest(alice, "old.txt", txt_bytes("Old onboarding guide indexed with the previous model."))
    await activate(admin_client, model_ids[LARGE])
    current = await ingest(alice, "new.txt", txt_bytes("New onboarding guide indexed with the active model."))
    failed = await ingest(alice, "scan.pdf", pdf_bytes(""))
    pending = await upload(alice, "queued.txt", txt_bytes("Queued onboarding guide that has not been processed."))
    assert pending.status_code == 201
    ids = {"stale": stale["id"], "current": current["id"], "failed": failed["id"], "pending": pending.json()["id"]}
    listed = {doc["id"]: (doc["status"], doc["is_stale"]) for doc in (await alice.get("/api/documents")).json()}
    assert {state: listed[doc_id] for state, doc_id in ids.items()} == {
        "stale": ("ready", True),
        "current": ("ready", False),
        "failed": ("failed", False),
        "pending": ("pending", False),
    }
    assert await queue() == [str(ids["pending"])]
    return ids


async def _statuses(ids: dict[str, int]) -> dict[str, tuple[str, str | None]]:
    async with SessionLocal() as session:
        documents = {doc.id: doc for doc in await session.scalars(select(Document))}
    return {state: (documents[doc_id].status, documents[doc_id].error) for state, doc_id in ids.items()}


@pytest.mark.parametrize("body", [None, {}, {"only_stale": True}])
async def test_reindex_queues_stale_and_failed_documents(
    body: dict[str, Any] | None, admin_client: ApiClient, create_user: CreateUser, model_ids: dict[str, int]
) -> None:
    ids = await _documents_in_every_state(admin_client, create_user, model_ids)

    response = await admin_client.post("/api/admin/reindex", json=body)

    assert response.status_code == 200
    assert response.json() == {"queued": 2}
    assert await queue() == [str(ids["pending"]), str(ids["stale"]), str(ids["failed"])]
    assert await _statuses(ids) == {
        "stale": ("pending", None),
        "current": ("ready", None),
        "failed": ("pending", None),
        "pending": ("pending", None),
    }
    assert (await stats(admin_client))["queue_length"] == 3

    assert await process_queue() == [ids["pending"], ids["stale"], ids["failed"]]
    result = await stats(admin_client)
    assert (result["stale_documents"], result["failed_documents"], result["queue_length"]) == (0, 1, 0)
    assert (await load(Document, ids["stale"])).embedding_model_id == model_ids[LARGE]


async def test_reindex_everything(admin_client: ApiClient, create_user: CreateUser, model_ids: dict[str, int]) -> None:
    ids = await _documents_in_every_state(admin_client, create_user, model_ids)

    response = await admin_client.post("/api/admin/reindex", json={"only_stale": False})

    assert response.status_code == 200
    assert response.json() == {"queued": 4}
    assert await queue() == [str(ids["pending"]), *(str(doc_id) for doc_id in sorted(ids.values()))]
    assert set((await _statuses(ids)).values()) == {("pending", None)}

    await process_queue()
    statuses = await _statuses(ids)
    assert statuses["failed"][0] == "failed"
    assert {state: status for state, (status, _) in statuses.items() if state != "failed"} == {
        "stale": "ready",
        "current": "ready",
        "pending": "ready",
    }


async def test_reindex_with_nothing_stale(admin_client: ApiClient, create_user: CreateUser) -> None:
    alice = await create_user("alice")
    document = await ingest(alice, "fresh.txt", txt_bytes("Fresh document indexed with the active model."))

    response = await admin_client.post("/api/admin/reindex", json={"only_stale": True})

    assert response.json() == {"queued": 0}
    assert await queue() == []
    assert (await load(Document, document["id"])).status == "ready"


async def test_reindex_needs_an_active_embedding_model(admin_client: ApiClient, create_user: CreateUser) -> None:
    alice = await create_user("alice")
    await ingest(alice, "ready.txt", txt_bytes("Ready document about the onboarding programme."))
    await remove_active_embedding_model()
    before = await snapshot()

    response = await admin_client.post("/api/admin/reindex", json={"only_stale": False})

    assert response.status_code == 400
    assert response.json() == {
        "detail": "There is no active embedding model. Enable an embedding model and make it the default first."
    }
    assert await snapshot() == before


async def test_reindex_validates_the_body(admin_client: ApiClient) -> None:
    response = await admin_client.post("/api/admin/reindex", json={"only_stale": "sometimes"})

    assert response.status_code == 422
    assert await queue() == []


# ---------------- Usage report ----------------


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0, us: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, us, tzinfo=timezone.utc)


async def test_usage_report_for_a_month(admin_client: ApiClient, create_user: CreateUser) -> None:
    alice = await create_user("alice", login=False)
    bob = await create_user("bob", login=False)
    opus, haiku, small = "anthropic/claude-opus-5-5", "anthropic/claude-haiku-5-5", "openai/text-embedding-3-small"
    sonnet, gpt_image = "anthropic/claude-sonnet-5-5", "openai/gpt-image-2"

    def event(
        user: ApiClient, model: str, kind: str, prompt: int, completion: int, cost: float, at: datetime
    ) -> UsageEvent:
        return UsageEvent(
            user_id=user.user_id,
            model_name=model,
            kind=kind,
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
            cost_usd=cost,
            created_at=at,
        )

    await add_rows(
        event(alice, opus, "chat", 100, 20, 0.0008, _utc(2026, 3, 1)),
        event(alice, opus, "chat", 200, 50, 0.0018, _utc(2026, 3, 15, 12, 30)),
        event(alice, small, "embedding", 300, 0, 0.000006, _utc(2026, 3, 15, 8)),
        event(bob, haiku, "chat", 1000, 500, 0.00035, _utc(2026, 3, 31, 23, 59, 59, 999999)),
        event(bob, small, "embedding", 40, 0, 0.0000008, _utc(2026, 3, 31, 10)),
        event(bob, sonnet, "vision", 2000, 400, 0.008, _utc(2026, 3, 15, 9)),
        event(alice, gpt_image, "image", 50, 272, 0.00841, _utc(2026, 3, 15, 13)),
        event(alice, gpt_image, "image", 0, 0, 0.0, _utc(2026, 3, 1, 5)),  # a reservation still in flight
        event(alice, opus, "chat", 9999, 1, 5.0, _utc(2026, 2, 28, 23, 59, 59)),
        event(bob, haiku, "chat", 7777, 3, 5.0, _utc(2026, 4, 1)),
    )

    response = await admin_client.get("/api/admin/usage", params={"month": "2026-03"})

    assert response.status_code == 200
    assert response.json() == {
        "month": "2026-03",
        "totals": {
            "chat_tokens": 1870,
            "embedding_tokens": 340,
            "vision_tokens": 2400,
            "images": 2,
            "cost_usd": 0.019367,
            "requests": 3,  # chat answers only
        },
        "by_user": [
            {
                "user_id": bob.user_id,
                "username": "bob",
                "chat_tokens": 1500,
                "embedding_tokens": 40,
                "vision_tokens": 2400,
                "images": 0,
                "cost_usd": 0.008351,
                "requests": 1,
            },
            {
                "user_id": alice.user_id,
                "username": "alice",
                "chat_tokens": 370,
                "embedding_tokens": 300,
                "vision_tokens": 0,
                "images": 2,
                "cost_usd": 0.011016,
                "requests": 2,
            },
        ],
        "by_model": [
            {
                "model_name": haiku,
                "kind": "chat",
                "prompt_tokens": 1000,
                "completion_tokens": 500,
                "cost_usd": 0.00035,
                "requests": 1,
            },
            {
                "model_name": opus,
                "kind": "chat",
                "prompt_tokens": 300,
                "completion_tokens": 70,
                "cost_usd": 0.0026,
                "requests": 2,
            },
            {
                "model_name": small,
                "kind": "embedding",
                "prompt_tokens": 340,
                "completion_tokens": 0,
                "cost_usd": 0.000007,
                "requests": 2,
            },
            {
                "model_name": gpt_image,
                "kind": "image",
                "prompt_tokens": 50,
                "completion_tokens": 272,
                "cost_usd": 0.00841,
                "requests": 2,  # images
            },
            {
                "model_name": sonnet,
                "kind": "vision",
                "prompt_tokens": 2000,
                "completion_tokens": 400,
                "cost_usd": 0.008,
                "requests": 1,
            },
        ],
        "daily": [
            {
                "date": "2026-03-01",
                "chat_tokens": 120,
                "embedding_tokens": 0,
                "vision_tokens": 0,
                "images": 1,
                "cost_usd": 0.0008,
            },
            {
                "date": "2026-03-15",
                "chat_tokens": 250,
                "embedding_tokens": 300,
                "vision_tokens": 2400,
                "images": 1,
                "cost_usd": 0.018216,
            },
            {
                "date": "2026-03-31",
                "chat_tokens": 1500,
                "embedding_tokens": 40,
                "vision_tokens": 0,
                "images": 0,
                "cost_usd": 0.000351,
            },
        ],
    }


async def test_usage_report_defaults_to_the_current_month(
    admin_client: ApiClient, create_user: CreateUser, fake_llm: FakeLLM
) -> None:
    alice = await create_user("alice")
    await ingest(alice, "notes.txt", txt_bytes("Release notes describing the new search feature."))
    response, _ = await chat(alice, "What does the new search feature do?")
    assert response.status_code == 200
    async with SessionLocal() as session:
        days = sorted({event.created_at.date().isoformat() for event in await session.scalars(select(UsageEvent))})

    report = await admin_client.get("/api/admin/usage")

    assert report.status_code == 200
    body = report.json()
    words = embedded_words(fake_llm)
    assert body["month"] == month_start().strftime("%Y-%m")
    assert {key: body["totals"][key] for key in ("chat_tokens", "embedding_tokens", "requests")} == {
        "chat_tokens": 47,
        "embedding_tokens": words,
        "requests": 1,
    }
    assert body["totals"]["cost_usd"] == pytest.approx((42 * 4.0 + 5 * 20.0) / 1_000_000, abs=1e-6)
    assert [
        (row["username"], row["chat_tokens"], row["embedding_tokens"], row["requests"]) for row in body["by_user"]
    ] == [("alice", 47, words, 1)]
    assert [
        (row["model_name"], row["kind"], row["prompt_tokens"], row["completion_tokens"], row["requests"])
        for row in body["by_model"]
    ] == [
        ("anthropic/claude-opus-5-5", "chat", 42, 5, 1),
        ("openai/text-embedding-3-small", "embedding", words, 0, 2),
    ]
    assert [row["date"] for row in body["daily"]] == days
    assert sum(row["chat_tokens"] for row in body["daily"]) == 47


async def test_usage_report_for_a_month_without_usage(admin_client: ApiClient) -> None:
    response = await admin_client.get("/api/admin/usage", params={"month": "1999-12"})

    assert response.status_code == 200
    assert response.json() == {
        "month": "1999-12",
        "totals": {
            "chat_tokens": 0,
            "embedding_tokens": 0,
            "vision_tokens": 0,
            "images": 0,
            "cost_usd": 0.0,
            "requests": 0,
        },
        "by_user": [],
        "by_model": [],
        "daily": [],
    }


@pytest.mark.parametrize("month", ["2026-13", "2026-00", "2026-3", "March", "2026-03-01", "", "20260-1"])
async def test_usage_report_rejects_a_bad_month(month: str, admin_client: ApiClient) -> None:
    response = await admin_client.get("/api/admin/usage", params={"month": month})

    assert response.status_code == 422


# ---------------- Documents ----------------


async def test_admin_lists_every_document_newest_first(
    admin_client: ApiClient, create_user: CreateUser, model_ids: dict[str, int]
) -> None:
    alice = await create_user("alice")
    bob = await create_user("bob")
    alice_doc = await ingest(alice, "alice.txt", txt_bytes("Alice keeps notes about the product roadmap."))
    bob_doc = await ingest(bob, "bob.pdf", pdf_bytes("Bob documents the support escalation process."))
    shared_doc = await ingest(admin_client, "policy.txt", txt_bytes("Shared policy for remote work."), "shared")
    failed_doc = await ingest(bob, "scan.pdf", pdf_bytes(""))
    await activate(admin_client, model_ids[LARGE])

    response = await admin_client.get("/api/admin/documents")

    assert response.status_code == 200
    documents = response.json()
    assert [doc["id"] for doc in documents] == [failed_doc["id"], shared_doc["id"], bob_doc["id"], alice_doc["id"]]
    assert all(set(doc) == DOCUMENT_ITEM_KEYS for doc in documents)
    assert all(doc["can_delete"] and not doc["generated"] for doc in documents)
    assert [(doc["owner_username"], doc["scope"], doc["status"], doc["is_stale"]) for doc in documents] == [
        ("bob", "private", "failed", False),
        (ADMIN_USERNAME, "shared", "ready", True),
        ("bob", "private", "ready", True),
        ("alice", "private", "ready", True),
    ]
    assert documents[0]["error"] == "No extractable text: this PDF has neither a text layer nor page images to read."
    assert documents[2] == {**bob_doc, "is_stale": True, "can_delete": True}


async def test_admin_deletes_any_document(
    admin_client: ApiClient, create_user: CreateUser, model_ids: dict[str, int]
) -> None:
    small = model_ids[SMALL]
    alice = await create_user("alice")
    private_doc = await ingest(alice, "private.txt", txt_bytes("Alice's private salary negotiation notes."))
    shared_doc = await ingest(admin_client, "shared.txt", txt_bytes("Shared guidelines for code review."), "shared")
    files = {doc["id"]: stored_path((await load(Document, doc["id"])).stored_name) for doc in (private_doc, shared_doc)}
    assert all(path.exists() for path in files.values())

    for doc_id in files:
        response = await admin_client.delete(f"/api/admin/documents/{doc_id}")
        assert response.status_code == 204

    for doc_id, path in files.items():
        assert await load(Document, doc_id) is None
        assert not path.exists()
        assert await point_count(small, doc_id) == 0
    assert (await alice.get("/api/documents")).json() == []
    assert (await admin_client.get("/api/admin/documents")).json() == []
    assert (await admin_client.delete(f"/api/admin/documents/{private_doc['id']}")).status_code == 404


async def test_admin_delete_unknown_document(admin_client: ApiClient) -> None:
    response = await admin_client.delete("/api/admin/documents/999999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Document not found"}
