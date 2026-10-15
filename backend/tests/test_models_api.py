"""GET /api/models: the enabled chat models a user may pick, and chat requests honouring the same rules."""

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from conftest import ApiClient, chat
from fastapi import FastAPI
from sqlalchemy import select, update

from app.db import SessionLocal
from app.models import LLMModel, User

CreateUser = Callable[..., Awaitable[ApiClient]]

OPUS = "Claude Opus 5.5"
SONNET = "Claude Sonnet 5.5"
HAIKU = "Claude Haiku 5.5"
GPT = "GPT-5.5"
GPT_MINI = "GPT-5.4 mini"
SMALL = "OpenAI text-embedding-3-small"
LARGE = "OpenAI text-embedding-3-large"

NOT_ALLOWED = {"detail": "You are not allowed to use this model."}
NOT_AVAILABLE = {"detail": "This model is not available."}


@pytest.fixture
async def model_ids(app: FastAPI) -> dict[str, int]:
    """display_name -> id of every seeded model."""
    async with SessionLocal() as session:
        return {name: model_id for name, model_id in await session.execute(select(LLMModel.display_name, LLMModel.id))}


async def listed(client: ApiClient) -> list[dict[str, Any]]:
    response = await client.get("/api/models")
    assert response.status_code == 200, response.text
    return response.json()


async def names(client: ApiClient) -> list[str]:
    return [model["display_name"] for model in await listed(client)]


async def patch_model(admin: ApiClient, model_id: int, **changes: Any) -> None:
    response = await admin.patch(f"/api/admin/models/{model_id}", json=changes)
    assert response.status_code == 200, response.text


async def test_models_require_a_session(client: ApiClient) -> None:
    response = await client.get("/api/models")

    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}


async def test_unrestricted_user_gets_every_enabled_chat_model_default_first(
    create_user: CreateUser, model_ids: dict[str, int]
) -> None:
    user = await create_user()

    models = await listed(user)

    files = {"can_create_files": True}  # every seeded chat model supports function calling
    assert models == [
        {"id": model_ids[OPUS], "display_name": OPUS, "provider": "anthropic", "is_default": True, **files},
        {"id": model_ids[HAIKU], "display_name": HAIKU, "provider": "anthropic", "is_default": False, **files},
        {"id": model_ids[SONNET], "display_name": SONNET, "provider": "anthropic", "is_default": False, **files},
        {"id": model_ids[GPT_MINI], "display_name": GPT_MINI, "provider": "openai", "is_default": False, **files},
        {"id": model_ids[GPT], "display_name": GPT, "provider": "openai", "is_default": False, **files},
    ]


async def test_disabled_and_embedding_models_are_never_listed(
    admin_client: ApiClient, create_user: CreateUser, model_ids: dict[str, int]
) -> None:
    user = await create_user()
    await patch_model(admin_client, model_ids[GPT], enabled=False)
    await patch_model(admin_client, model_ids[HAIKU], enabled=False)
    await patch_model(admin_client, model_ids[LARGE], enabled=True)

    assert await names(user) == [OPUS, SONNET, GPT_MINI]
    assert await names(admin_client) == [OPUS, SONNET, GPT_MINI]

    await patch_model(admin_client, model_ids[HAIKU], enabled=True)

    assert await names(user) == [OPUS, HAIKU, SONNET, GPT_MINI]


async def test_default_model_comes_first_and_the_rest_sort_by_name_ignoring_case(
    admin_client: ApiClient, create_user: CreateUser, model_ids: dict[str, int]
) -> None:
    user = await create_user()
    created = await admin_client.post(
        "/api/admin/models", json={"display_name": "aardvark local", "litellm_model": "ollama/llama3.1", "kind": "chat"}
    )
    assert created.status_code == 201
    await patch_model(admin_client, model_ids[GPT_MINI], is_default=True)

    models = await listed(user)

    assert [model["display_name"] for model in models] == [GPT_MINI, "aardvark local", HAIKU, OPUS, SONNET, GPT]
    assert [model["is_default"] for model in models] == [True, False, False, False, False, False]


@pytest.mark.parametrize(
    ("litellm_model", "provider", "can_create_files"),
    [
        ("ollama/llama3.1", "ollama", True),  # LiteLLM's model map says these two support function calling
        ("gpt-4o-mini", "openai", True),
        ("azure/my-deployment/v2", "azure", False),
        ("openrouter/anthropic/claude-sonnet", "openrouter", False),
    ],
)
async def test_provider_is_the_prefix_of_the_litellm_model(
    litellm_model: str, provider: str, can_create_files: bool, admin_client: ApiClient, create_user: CreateUser
) -> None:
    user = await create_user()
    created = await admin_client.post(
        "/api/admin/models", json={"display_name": "Custom", "litellm_model": litellm_model, "kind": "chat"}
    )
    assert created.status_code == 201

    custom = next(model for model in await listed(user) if model["display_name"] == "Custom")

    assert custom == {
        "id": created.json()["id"],
        "display_name": "Custom",
        "provider": provider,
        "is_default": False,
        "can_create_files": can_create_files,
    }


async def test_restricted_user_gets_only_allowed_enabled_models(
    admin_client: ApiClient, create_user: CreateUser, model_ids: dict[str, int]
) -> None:
    allowed = [model_ids[GPT], model_ids[HAIKU], model_ids[SONNET]]
    user = await create_user(restrict_models=True, allowed_model_ids=allowed)
    await patch_model(admin_client, model_ids[SONNET], enabled=False)

    files = {"can_create_files": True}
    assert await listed(user) == [
        {"id": model_ids[HAIKU], "display_name": HAIKU, "provider": "anthropic", "is_default": False, **files},
        {"id": model_ids[GPT], "display_name": GPT, "provider": "openai", "is_default": False, **files},
    ]

    await patch_model(admin_client, model_ids[GPT], is_default=True)

    assert [(model["display_name"], model["is_default"]) for model in await listed(user)] == [
        (GPT, True),
        (HAIKU, False),
    ]


async def test_restricted_user_without_allowed_models_gets_none(create_user: CreateUser) -> None:
    user = await create_user(restrict_models=True, allowed_model_ids=[])

    assert await listed(user) == []


async def test_allowed_models_only_apply_when_restricted(create_user: CreateUser, model_ids: dict[str, int]) -> None:
    user = await create_user(restrict_models=False, allowed_model_ids=[model_ids[HAIKU]])

    assert await names(user) == [OPUS, HAIKU, SONNET, GPT_MINI, GPT]


async def test_admin_changes_apply_to_existing_sessions(
    admin_client: ApiClient, create_user: CreateUser, model_ids: dict[str, int]
) -> None:
    user = await create_user()
    url = f"/api/admin/users/{user.user_id}"

    restricted = await admin_client.patch(url, json={"restrict_models": True, "allowed_model_ids": [model_ids[HAIKU]]})

    assert restricted.status_code == 200
    assert await names(user) == [HAIKU]

    widened = await admin_client.patch(url, json={"allowed_model_ids": [model_ids[HAIKU], model_ids[OPUS]]})

    assert widened.status_code == 200
    assert await names(user) == [OPUS, HAIKU]

    unrestricted = await admin_client.patch(url, json={"restrict_models": False})

    assert unrestricted.status_code == 200
    assert await names(user) == [OPUS, HAIKU, SONNET, GPT_MINI, GPT]


async def test_admin_is_never_restricted(admin_client: ApiClient) -> None:
    async with SessionLocal() as session:
        await session.execute(update(User).where(User.id == admin_client.user_id).values(restrict_models=True))
        await session.commit()

    assert await names(admin_client) == [OPUS, HAIKU, SONNET, GPT_MINI, GPT]


async def test_chat_uses_the_first_listed_model_by_default(create_user: CreateUser, model_ids: dict[str, int]) -> None:
    user = await create_user(restrict_models=True, allowed_model_ids=[model_ids[SONNET], model_ids[HAIKU]])

    response, events = await chat(user, "Which model answers this?", document_ids=[])

    assert response.status_code == 200
    assert events[0][1]["model"] == {"id": model_ids[HAIKU], "display_name": HAIKU}
    assert (await listed(user))[0]["id"] == model_ids[HAIKU]


@pytest.mark.parametrize(
    ("model", "status_code", "detail"),
    [
        (HAIKU, 200, None),
        (OPUS, 403, NOT_ALLOWED),
        (GPT, 400, NOT_AVAILABLE),
        (SMALL, 400, NOT_AVAILABLE),
        ("unknown", 400, NOT_AVAILABLE),
    ],
)
async def test_chat_accepts_only_listed_models(
    model: str,
    status_code: int,
    detail: dict[str, str] | None,
    admin_client: ApiClient,
    create_user: CreateUser,
    model_ids: dict[str, int],
) -> None:
    user = await create_user(restrict_models=True, allowed_model_ids=[model_ids[HAIKU], model_ids[GPT]])
    await patch_model(admin_client, model_ids[GPT], enabled=False)
    model_id = model_ids.get(model, 999_999)

    response, events = await chat(user, "Hello there, model.", model_id=model_id, document_ids=[])

    assert response.status_code == status_code
    if detail is None:
        assert events[0][1]["model"] == {"id": model_id, "display_name": model}
        assert events[-1][0] == "done"
    else:
        assert response.json() == detail
    assert (model_id in {item["id"] for item in await listed(user)}) is (status_code == 200)


async def test_chat_without_any_available_model(create_user: CreateUser) -> None:
    user = await create_user(restrict_models=True, allowed_model_ids=[])

    response, _ = await chat(user, "Is anyone there?", document_ids=[])

    assert response.status_code == 400
    assert response.json() == {"detail": "No chat model is available to you. Ask the admin to enable one."}
