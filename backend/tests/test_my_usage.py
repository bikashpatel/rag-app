"""GET /me/usage (docs/briefs/ui-shell-settings.md, 4.5): the caller's current UTC month against their limits, per day
(every day from the 1st, zeros included) and per model, with the clock pinned."""

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from conftest import ApiClient, txt_bytes, upload
from sqlalchemy import delete

from app.config import settings
from app.db import SessionLocal
from app.models import UsageEvent, UserImageLimit
from app.services import account

CreateUser = Callable[..., Awaitable[ApiClient]]

NOW = datetime(2026, 10, 18, 15, 30, tzinfo=timezone.utc)
UTC = timezone.utc


@pytest.fixture(autouse=True)
def pinned_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(account, "current_time", lambda: NOW)


async def usage_of(client: ApiClient) -> dict[str, Any]:
    response = await client.get("/api/me/usage")
    assert response.status_code == 200, response.text
    return response.json()


async def add_events(*events: UsageEvent) -> None:
    async with SessionLocal() as session:
        session.add_all(events)
        await session.commit()


def event(
    user_id: int | None,
    kind: str,
    when: datetime,
    *,
    model: str = "anthropic/claude-haiku-5-5",
    prompt: int = 0,
    completion: int = 0,
    total: int | None = None,
    cost: float = 0.0,
) -> UsageEvent:
    return UsageEvent(
        user_id=user_id,
        model_name=model,
        kind=kind,
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion if total is None else total,
        cost_usd=cost,
        created_at=when,
    )


def day(number: int, hour: int = 12) -> datetime:
    return datetime(2026, 10, number, hour, tzinfo=UTC)


async def test_an_empty_month(create_user: CreateUser) -> None:
    alice = await create_user("alice", monthly_token_limit=5000, rate_limit_per_min=7, monthly_image_limit=6)

    usage = await usage_of(alice)

    assert usage["month"] == "2026-10"
    assert usage["period_start"] == "2026-10-01T00:00:00Z"
    assert usage["resets_at"] == "2026-11-01T00:00:00Z"
    assert usage["chat_tokens"] == {"used": 0, "limit": 5000}
    assert usage["images"] == {"used": 0, "limit": 6}
    assert usage["vision_tokens"] == {"used": 0, "limit": settings.vision_monthly_token_limit}
    assert (usage["embedding_tokens"], usage["requests"], usage["rate_limit_per_min"]) == (0, 0, 7)
    assert usage["cost_usd"] == {"total": 0.0, "chat": 0.0, "embedding": 0.0, "vision": 0.0, "image": 0.0}
    assert [entry["date"] for entry in usage["daily"]] == [f"2026-10-{n:02d}" for n in range(1, 19)]
    assert all(
        entry
        == {
            "date": entry["date"],
            "chat_tokens": 0,
            "requests": 0,
            "images": 0,
            "vision_tokens": 0,
            "embedding_tokens": 0,
            "cost_usd": 0.0,
        }
        for entry in usage["daily"]
    )
    assert usage["by_model"] == []


async def test_sums_counts_and_costs_per_kind_day_and_model(create_user: CreateUser) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")
    nepal = timezone(timedelta(hours=5, minutes=45))
    await add_events(
        event(alice.user_id, "chat", day(2), prompt=100, completion=20, cost=0.25),
        event(alice.user_id, "chat", day(2, 18), prompt=50, completion=30, cost=0.5, model="anthropic/claude-opus-5-5"),
        # 00:30 in Kathmandu on the 6th is 18:45 UTC on the 5th
        event(alice.user_id, "chat", datetime(2026, 10, 6, 0, 30, tzinfo=nepal), prompt=10, completion=10),
        event(alice.user_id, "embedding", day(5), prompt=400, cost=0.01, model="openai/text-embedding-3-small"),
        event(
            alice.user_id,
            "vision",
            day(9),
            prompt=2000,
            completion=1000,
            cost=0.02,
            model="anthropic/claude-sonnet-5-5",
        ),
        event(alice.user_id, "image", day(9), prompt=30, completion=1000, cost=0.04, model="openai/gpt-image-2"),
        event(alice.user_id, "image", day(18, 1), cost=0.04, model="openai/gpt-image-2"),
        event(alice.user_id, "chat", day(18, 2), prompt=5, completion=5, model="custom/deleted-model", cost=1.5),
        # Not this month, or not alice's
        event(alice.user_id, "chat", datetime(2026, 9, 30, 23, 59, tzinfo=UTC), prompt=9000, cost=9.0),
        event(bob.user_id, "chat", day(2), prompt=7000, cost=7.0),
    )

    usage = await usage_of(alice)

    assert usage["chat_tokens"]["used"] == 120 + 80 + 20 + 10
    assert (usage["requests"], usage["images"]["used"], usage["vision_tokens"]["used"]) == (4, 2, 3000)
    assert usage["embedding_tokens"] == 400
    assert usage["cost_usd"] == {
        "total": pytest.approx(2.36),
        "chat": pytest.approx(2.25),
        "embedding": pytest.approx(0.01),
        "vision": pytest.approx(0.02),
        "image": pytest.approx(0.08),
    }
    daily = {entry["date"]: entry for entry in usage["daily"]}
    assert len(daily) == 18
    assert daily["2026-10-02"] == {
        "date": "2026-10-02",
        "chat_tokens": 200,
        "requests": 2,
        "images": 0,
        "vision_tokens": 0,
        "embedding_tokens": 0,
        "cost_usd": 0.75,
    }
    assert (daily["2026-10-05"]["chat_tokens"], daily["2026-10-05"]["embedding_tokens"]) == (20, 400)
    assert daily["2026-10-06"]["chat_tokens"] == 0
    assert (daily["2026-10-09"]["images"], daily["2026-10-09"]["vision_tokens"]) == (1, 3000)
    assert (daily["2026-10-18"]["images"], daily["2026-10-18"]["requests"]) == (1, 1)
    assert [
        (entry["model_name"], entry["kind"], entry["tokens"], entry["requests"]) for entry in usage["by_model"]
    ] == [
        ("custom/deleted-model", "chat", 10, 1),  # no model has that id any more: the stored id
        ("Claude Opus 5.5", "chat", 80, 1),
        ("Claude Haiku 5.5", "chat", 140, 2),
        ("GPT Image 2", "image", 1030, 2),
        ("Claude Sonnet 5.5", "vision", 3000, 1),
        ("OpenAI text-embedding-3-small", "embedding", 400, 1),
    ]
    assert [entry["cost_usd"] for entry in usage["by_model"]] == [1.5, 0.5, 0.25, 0.08, 0.02, 0.01]


async def test_limits_follow_the_users_settings_and_the_admin_has_none(
    create_user: CreateUser, admin_client: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "vision_monthly_token_limit", 12345)
    alice = await create_user("alice", monthly_token_limit=777, monthly_image_limit=3)
    bob = await create_user("bob")
    async with SessionLocal() as session:  # no row of their own: the default
        await session.execute(delete(UserImageLimit).where(UserImageLimit.user_id == bob.user_id))
        await session.commit()

    mine, theirs, admin = await usage_of(alice), await usage_of(bob), await usage_of(admin_client)

    assert (mine["chat_tokens"]["limit"], mine["images"]["limit"], mine["vision_tokens"]["limit"]) == (777, 3, 12345)
    assert theirs["images"]["limit"] == settings.default_monthly_image_limit
    assert (admin["chat_tokens"]["limit"], admin["images"]["limit"], admin["vision_tokens"]["limit"]) == (0, 0, 0)
    assert admin["rate_limit_per_min"] == 0
    assert admin["documents"] == {
        "count": 0,
        "max_documents": 0,
        "max_storage_mb": 0,
        "storage_bytes": 0,
        "max_upload_mb": settings.max_upload_mb_hard,
    }


async def test_documents_count_only_what_the_user_owns(create_user: CreateUser, admin_client: ApiClient) -> None:
    alice = await create_user("alice", max_documents=4, max_storage_mb=50, max_upload_mb=3)
    mine = txt_bytes("Alice's notes. " * 20)
    assert (await upload(alice, "notes.txt", mine)).status_code == 201
    assert (
        await upload(admin_client, "handbook.txt", txt_bytes("Shared handbook. " * 50), scope="shared")
    ).status_code == 201

    documents = (await usage_of(alice))["documents"]

    assert documents == {
        "count": 1,
        "max_documents": 4,
        "storage_bytes": len(mine),
        "max_storage_mb": 50,
        "max_upload_mb": 3,
    }
    config = (await alice.get("/api/documents/config")).json()  # the same numbers
    assert (config["document_count"], config["storage_bytes"], config["max_documents"]) == (1, len(mine), 4)


async def test_december_resets_on_the_first_of_january(
    create_user: CreateUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = await create_user("alice")
    monkeypatch.setattr(account, "current_time", lambda: datetime(2026, 12, 31, 23, 59, tzinfo=UTC))
    await add_events(event(alice.user_id, "chat", datetime(2026, 12, 31, 23, 0, tzinfo=UTC), prompt=5))

    usage = await usage_of(alice)

    assert (usage["month"], usage["resets_at"]) == ("2026-12", "2027-01-01T00:00:00Z")
    assert len(usage["daily"]) == 31 and usage["daily"][-1] == {**usage["daily"][-1], "chat_tokens": 5}


async def test_usage_needs_a_session(client: ApiClient) -> None:
    response = await client.get("/api/me/usage")

    assert (response.status_code, response.json()) == (401, {"detail": "Not authenticated"})
