"""The caller's preferences (docs/briefs/ui-shell-settings.md, 4.1): defaults, GET/PATCH /me/preferences and Me, the
lenient read and the strict write, the default model rule, isolation and races."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from conftest import USER_PASSWORD, ApiClient
from fastapi import FastAPI
from sqlalchemy import select

from app.db import SessionLocal
from app.models import LLMModel, UserPreference
from app.schemas import MAX_INT
from app.services import account

CreateUser = Callable[..., Awaitable[ApiClient]]

DEFAULTS = {
    "theme": "system",
    "accent": "clay",
    "chat_font_size": "default",
    "enter_to_send": True,
    "default_model_id": None,
}
UNAVAILABLE = {"detail": "That model isn't available to you."}


@pytest.fixture
async def model_ids(app: FastAPI) -> dict[str, int]:
    """display_name -> id of every seeded model."""
    async with SessionLocal() as session:
        return {name: model_id for name, model_id in await session.execute(select(LLMModel.display_name, LLMModel.id))}


async def patch(client: ApiClient, **fields: Any) -> Any:
    return await client.patch("/api/me/preferences", json=fields)


async def preferences(client: ApiClient) -> dict[str, Any]:
    response = await client.get("/api/me/preferences")
    assert response.status_code == 200, response.text
    return response.json()


async def stored(user_id: int | None) -> dict[str, Any] | None:
    async with SessionLocal() as session:
        row = await session.get(UserPreference, user_id)
        return row.data if row is not None else None


async def store(user_id: int | None, data: Any) -> None:
    async with SessionLocal() as session:
        session.add(UserPreference(user_id=user_id, data=data))
        await session.commit()


async def test_a_user_without_a_row_gets_the_defaults_everywhere(create_user: CreateUser, client: ApiClient) -> None:
    alice = await create_user("alice")

    login = await client.login("alice", USER_PASSWORD)

    assert login.json()["preferences"] == DEFAULTS
    assert (await alice.get("/api/auth/me")).json()["preferences"] == DEFAULTS
    assert await preferences(alice) == DEFAULTS
    assert await stored(alice.user_id) is None


async def test_each_field_is_saved_and_returned_and_a_partial_patch_keeps_the_rest(
    create_user: CreateUser, model_ids: dict[str, int]
) -> None:
    alice = await create_user("alice")
    haiku = model_ids["Claude Haiku 5.5"]
    changes: list[dict[str, Any]] = [
        {"theme": "dark"},
        {"accent": "ocean"},
        {"chat_font_size": "large"},
        {"enter_to_send": False},
        {"default_model_id": haiku},
    ]
    expected = dict(DEFAULTS)
    for change in changes:
        response = await patch(alice, **change)
        expected.update(change)
        assert response.status_code == 200, response.text
        assert response.json() == expected
    assert await preferences(alice) == expected
    assert (await alice.get("/api/auth/me")).json()["preferences"] == expected

    response = await patch(alice, default_model_id=None, theme="light")

    assert response.json() == {**expected, "default_model_id": None, "theme": "light"}
    assert (await stored(alice.user_id) or {})["default_model_id"] is None
    assert (await patch(alice)).json() == response.json()  # an empty patch changes nothing


@pytest.mark.parametrize(
    "body",
    [
        {"theme": None},
        {"accent": None},
        {"chat_font_size": None},
        {"enter_to_send": None},
        {"theme": "sepia"},
        {"accent": "red"},
        {"chat_font_size": "huge"},
        {"enter_to_send": "maybe"},
        {"colour": "red"},
        {"theme": "dark", "unknown": 1},
        {"default_model_id": 0},
        {"default_model_id": MAX_INT + 1},
        {"default_model_id": "first"},
    ],
)
async def test_invalid_patches_get_422_and_change_nothing(create_user: CreateUser, body: dict[str, Any]) -> None:
    alice = await create_user("alice")

    response = await alice.patch("/api/me/preferences", json=body)

    assert response.status_code == 422
    assert await preferences(alice) == DEFAULTS
    assert await stored(alice.user_id) is None


@pytest.mark.parametrize(
    "name",
    ["OpenAI text-embedding-3-small", "Claude Sonnet 5.5 (vision)", "GPT Image 2", "GPT-5.5 (disabled)", "missing"],
)
async def test_the_default_model_must_be_an_enabled_chat_model(
    name: str, create_user: CreateUser, admin_client: ApiClient, model_ids: dict[str, int]
) -> None:
    alice = await create_user("alice")
    if name == "GPT-5.5 (disabled)":
        model_id = model_ids["GPT-5.5"]
        assert (await admin_client.patch(f"/api/admin/models/{model_id}", json={"enabled": False})).status_code == 200
    else:
        model_id = model_ids.get(name, 999_999)

    response = await patch(alice, default_model_id=model_id)

    assert (response.status_code, response.json()) == (422, UNAVAILABLE)
    assert await stored(alice.user_id) is None


async def test_a_restricted_user_can_only_pick_an_allowed_model(
    create_user: CreateUser, model_ids: dict[str, int]
) -> None:
    alice = await create_user("alice", restrict_models=True, allowed_model_ids=[model_ids["Claude Haiku 5.5"]])

    refused = await patch(alice, default_model_id=model_ids["Claude Opus 5.5"])
    allowed = await patch(alice, default_model_id=model_ids["Claude Haiku 5.5"])

    assert (refused.status_code, refused.json()) == (422, UNAVAILABLE)
    assert allowed.status_code == 200 and allowed.json()["default_model_id"] == model_ids["Claude Haiku 5.5"]


async def test_a_default_that_becomes_unusable_reads_as_null_and_comes_back(
    create_user: CreateUser, admin_client: ApiClient, model_ids: dict[str, int]
) -> None:
    alice = await create_user("alice")
    haiku, opus = model_ids["Claude Haiku 5.5"], model_ids["Claude Opus 5.5"]
    assert (await patch(alice, default_model_id=haiku)).status_code == 200
    users = f"/api/admin/users/{alice.user_id}"

    await admin_client.patch(f"/api/admin/models/{haiku}", json={"enabled": False})
    assert (await preferences(alice))["default_model_id"] is None
    assert (await alice.get("/api/auth/me")).json()["preferences"]["default_model_id"] is None
    assert (await stored(alice.user_id) or {})["default_model_id"] == haiku  # kept

    await admin_client.patch(f"/api/admin/models/{haiku}", json={"enabled": True})
    assert (await preferences(alice))["default_model_id"] == haiku

    await admin_client.patch(users, json={"restrict_models": True, "allowed_model_ids": [opus]})
    assert (await preferences(alice))["default_model_id"] is None
    await admin_client.patch(users, json={"restrict_models": False})
    assert (await preferences(alice))["default_model_id"] == haiku


@pytest.mark.parametrize(
    "data",
    [
        {"theme": "sepia", "accent": 7, "chat_font_size": None, "enter_to_send": "yes", "default_model_id": "x"},
        {"theme": ["dark"], "enter_to_send": 1, "default_model_id": True},
        {"default_model_id": -3, "future_setting": {"a": 1}},
        ["not", "an", "object"],
        "garbage",
    ],
)
async def test_a_corrupted_row_reads_as_the_defaults(create_user: CreateUser, data: Any) -> None:
    alice = await create_user("alice")
    await store(alice.user_id, data)

    assert await preferences(alice) == DEFAULTS
    assert (await alice.get("/api/auth/me")).status_code == 200


async def test_valid_stored_fields_survive_next_to_invalid_ones_and_patches_keep_unknown_keys(
    create_user: CreateUser,
) -> None:
    alice = await create_user("alice")
    await store(alice.user_id, {"theme": "dark", "accent": "neon", "future_setting": {"a": 1}})

    assert await preferences(alice) == {**DEFAULTS, "theme": "dark"}
    response = await patch(alice, accent="sage")

    assert response.json() == {**DEFAULTS, "theme": "dark", "accent": "sage"}
    assert await stored(alice.user_id) == {"theme": "dark", "accent": "sage", "future_setting": {"a": 1}}


async def test_users_never_see_or_change_each_others(create_user: CreateUser, admin_client: ApiClient) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")

    await patch(alice, theme="dark", accent="plum")
    await patch(admin_client, chat_font_size="small")  # the admin has preferences like anyone

    assert await preferences(bob) == DEFAULTS
    assert await preferences(alice) == {**DEFAULTS, "theme": "dark", "accent": "plum"}
    assert await preferences(admin_client) == {**DEFAULTS, "chat_font_size": "small"}
    await patch(bob, theme="light")
    assert (await preferences(alice))["theme"] == "dark"


@pytest.mark.parametrize(("method", "body"), [("GET", None), ("PATCH", {"theme": "dark"})])
async def test_preferences_need_a_session(client: ApiClient, method: str, body: Any) -> None:
    response = await client.request(method, "/api/me/preferences", json=body)

    assert (response.status_code, response.json()) == (401, {"detail": "Not authenticated"})


async def test_concurrent_first_patches_both_land(create_user: CreateUser) -> None:
    alice = await create_user("alice")

    first, second = await asyncio.gather(patch(alice, theme="dark"), patch(alice, accent="slate"))

    assert (first.status_code, second.status_code) == (200, 200)
    assert await preferences(alice) == {**DEFAULTS, "theme": "dark", "accent": "slate"}


async def test_a_first_insert_that_loses_the_race_merges_into_the_winner(
    create_user: CreateUser, model_ids: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Restricted, so reading the result after the rollback needs the refreshed allowed models too
    alice = await create_user("alice", restrict_models=True, allowed_model_ids=[model_ids["Claude Haiku 5.5"]])
    await store(alice.user_id, {"theme": "dark"})  # the other request's row
    locked_row = account._locked_row
    calls: list[int] = []

    async def missed_once(db: Any, user_id: int) -> UserPreference | None:
        calls.append(user_id)
        return None if len(calls) == 1 else await locked_row(db, user_id)

    monkeypatch.setattr(account, "_locked_row", missed_once)

    response = await patch(alice, default_model_id=model_ids["Claude Haiku 5.5"])

    assert response.status_code == 200, response.text
    assert response.json() == {**DEFAULTS, "theme": "dark", "default_model_id": model_ids["Claude Haiku 5.5"]}
    assert len(calls) == 2


async def test_deleting_the_user_deletes_their_preferences(create_user: CreateUser, admin_client: ApiClient) -> None:
    alice = await create_user("alice")
    await patch(alice, theme="dark")
    assert await stored(alice.user_id) is not None

    assert (await admin_client.delete(f"/api/admin/users/{alice.user_id}")).status_code == 204

    assert await stored(alice.user_id) is None
