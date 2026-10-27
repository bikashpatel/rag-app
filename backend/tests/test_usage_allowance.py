"""Usage counted by allowance (docs/briefs/plans-billing.md 3.3 and 5.3): chat answers recorded in standard tokens,
the chat allowance, images and vision tokens counted in the entitlements' window (the current period, or the UTC
calendar month of an open-ended plan and the admin), the 429 allowance_used messages, and the usage reports
(`Me.usage`, `GET /me/usage`) that show the same counts. Also: the settings that plans replaced are ignored with a
warning."""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

import pytest
from conftest import ApiClient, chat, grant_plan
from sqlalchemy import select

from app.config import REMOVED_SETTINGS, warn_removed_settings
from app.db import SessionLocal
from app.models import LLMModel, PlanPeriod, UsageEvent, User, utcnow
from app.services import usage
from app.services.billing import entitlements, periods
from app.services.billing.entitlements import Entitlements
from app.services.billing.money import format_ist_date, ist_date
from app.services.llm import Usage

CreateUser = Callable[..., Awaitable[ApiClient]]

OPUS = "anthropic/claude-opus-5-5"
STANDARD_ALLOWANCE = 2_000_000


async def add_usage(user_id: int, kind: str, *, at: datetime, total: int = 0, allowance: int = 0) -> None:
    async with SessionLocal() as db:
        db.add(
            UsageEvent(
                user_id=user_id,
                model_name=OPUS,
                kind=kind,
                prompt_tokens=total,
                total_tokens=total,
                allowance_tokens=allowance,
                created_at=at,
            )
        )
        await db.commit()


async def entitlements_of(user_id: int) -> Entitlements:
    async with SessionLocal() as db:
        user = await db.get(User, user_id)
        assert user is not None
        return await entitlements.load(db, user)


async def current_period(user_id: int) -> PlanPeriod:
    async with SessionLocal() as db:
        found = periods.current_period(await periods.user_periods(db, user_id), utcnow())
        assert found is not None
        return found


def ist(instant: datetime) -> str:
    return format_ist_date(ist_date(instant))


async def dated_user(create_user: CreateUser, name: str, code: str = "standard", days: int = 30) -> ApiClient:
    """A user whose plan is a complimentary period from now (no open-ended plan)."""
    client = await create_user(name, plan=None)
    assert client.user_id is not None
    await grant_plan(client.user_id, code, days=days)
    return client


# ---------------- Recording ----------------


async def chat_events(user_id: int) -> list[UsageEvent]:
    async with SessionLocal() as db:
        return list(
            await db.scalars(
                select(UsageEvent)
                .where(UsageEvent.user_id == user_id, UsageEvent.kind == "chat")
                .order_by(UsageEvent.id)
            )
        )


async def test_chat_events_record_their_cost_in_standard_tokens(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    assert alice.user_id is not None
    async with SessionLocal() as db:
        model = await db.scalar(select(LLMModel).where(LLMModel.litellm_model == OPUS))
        for kind, cost in (("chat", 0.0032), ("chat", 0.00000321), ("chat", 0.0), ("vision", 0.5), ("image", 0.04)):
            await usage.record_usage(db, user_id=alice.user_id, model=model, kind=kind, usage=Usage(900, 100, cost))
        rows = [
            (row.kind, row.allowance_tokens)
            for row in await db.scalars(select(UsageEvent).where(UsageEvent.user_id == alice.user_id))
        ]

    # $0.0032 is 1,000 standard tokens ($3.20 per million); a part of one counts a whole; no cost: the plain tokens
    assert rows == [("chat", 1000), ("chat", 2), ("chat", 1000), ("vision", 0), ("image", 0)]


async def test_an_answer_records_its_allowance_tokens(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    response, _ = await chat(alice, "Hello?", document_ids=[])
    assert response.status_code == 200

    assert alice.user_id is not None
    [event] = await chat_events(alice.user_id)
    # FakeLLM's 42 + 5 tokens of Claude Opus 5.5 ($4 / $20 per 1M) cost $0.000268: 83.75 standard tokens
    assert (event.total_tokens, event.allowance_tokens) == (47, 84)


# ---------------- The window and the 429s ----------------


async def test_the_chat_allowance_counts_in_the_current_period_only(create_user: CreateUser) -> None:
    alice = await dated_user(create_user, "alice")
    assert alice.user_id is not None
    period = await current_period(alice.user_id)
    await add_usage(alice.user_id, "chat", at=period.starts_at - timedelta(seconds=1), allowance=STANDARD_ALLOWANCE)
    await add_usage(alice.user_id, "chat", at=period.starts_at, total=500, allowance=1_999_990)

    ent = await entitlements_of(alice.user_id)
    async with SessionLocal() as db:
        assert await usage.chat_allowance_used(db, alice.user_id, ent) == 1_999_990
        user = await db.get(User, alice.user_id)
        assert user is not None
        assert await usage.check_budget(db, user, ent) == 10

    allowed, _ = await chat(alice, "Within the allowance?", document_ids=[])
    refused, _ = await chat(alice, "And now?", document_ids=[])

    assert allowed.status_code == 200
    assert refused.status_code == 429
    answer = (await chat_events(alice.user_id))[-1].allowance_tokens  # Claude Sonnet 5.5, Standard's default: ×1
    assert answer == 42
    valid_through = format_ist_date(periods.valid_through(period.ends_at))
    assert refused.json() == {
        "detail": f"You've used this period's chat allowance ({1_999_990 + answer:,} of 2,000,000). Your Standard plan "
        f"is valid through {valid_through}.",
        "code": "allowance_used",
        "feature": "chat_tokens",
    }


async def test_the_message_names_the_next_period_when_one_is_scheduled(create_user: CreateUser) -> None:
    alice = await dated_user(create_user, "alice", days=60)  # two rows of 30 days, the second right after the first
    assert alice.user_id is not None
    period = await current_period(alice.user_id)
    await add_usage(alice.user_id, "chat", at=period.starts_at, allowance=STANDARD_ALLOWANCE)

    refused, _ = await chat(alice, "More?", document_ids=[])

    assert refused.status_code == 429
    assert refused.json()["detail"] == (
        "You've used this period's chat allowance (2,000,000 of 2,000,000). It starts again with your next period on "
        f"{ist(period.ends_at)}."
    )


async def test_an_open_ended_plan_counts_the_utc_month(create_user: CreateUser) -> None:
    alice = await create_user("alice", monthly_token_limit=1000)
    assert alice.user_id is not None
    start = usage.month_start()
    await add_usage(alice.user_id, "chat", at=start - timedelta(seconds=1), allowance=5000)
    await add_usage(alice.user_id, "chat", at=start, allowance=1000)

    refused, _ = await chat(alice, "More?", document_ids=[])

    assert refused.status_code == 429
    assert refused.json()["detail"] == (
        f"You've used this period's chat allowance (1,000 of 1,000). It starts again on "
        f"{ist(usage.next_month(start))}."
    )


async def test_a_period_followed_by_an_open_ended_plan_starts_again_when_the_period_ends(
    create_user: CreateUser,
) -> None:
    alice = await create_user("alice")  # an open-ended Professional plan
    assert alice.user_id is not None
    await grant_plan(alice.user_id, "starter", days=30)  # a Starter period takes precedence while it runs
    period = await current_period(alice.user_id)
    await add_usage(alice.user_id, "chat", at=period.starts_at, allowance=500_000)

    refused, _ = await chat(alice, "More?", document_ids=[])

    assert refused.json()["detail"] == (
        f"You've used this period's chat allowance (500,000 of 500,000). It starts again on {ist(period.ends_at)}."
    )


async def test_images_and_vision_tokens_count_in_the_period(create_user: CreateUser) -> None:
    alice = await dated_user(create_user, "alice", code="professional")
    assert alice.user_id is not None
    period = await current_period(alice.user_id)
    before, inside = period.starts_at - timedelta(minutes=1), period.starts_at + timedelta(minutes=1)
    for at in (before, inside, inside):
        await add_usage(alice.user_id, "image", at=at)
    await add_usage(alice.user_id, "vision", at=before, total=9000)
    await add_usage(alice.user_id, "vision", at=inside, total=700)

    ent = await entitlements_of(alice.user_id)
    async with SessionLocal() as db:
        assert await usage.images_in_window(db, alice.user_id, ent) == 2
        assert await usage.vision_tokens_in_window(db, alice.user_id, ent) == 700


async def test_the_admin_has_no_allowance_and_no_answer_slots(admin_client: ApiClient) -> None:
    assert admin_client.user_id is not None
    ent = await entitlements_of(admin_client.user_id)
    async with SessionLocal() as db:
        admin = await db.get(User, admin_client.user_id)
        assert admin is not None
        await add_usage(admin.id, "chat", at=utcnow(), allowance=10**12)
        assert await usage.check_budget(db, admin, ent) is None
    assert await usage.acquire_chat_slot(admin, ent) is None
    await usage.check_rate_limit(admin, ent)


# ---------------- The reports ----------------


async def test_me_and_my_usage_report_the_period(create_user: CreateUser) -> None:
    alice = await dated_user(create_user, "alice")
    assert alice.user_id is not None
    period = await current_period(alice.user_id)
    await add_usage(alice.user_id, "chat", at=period.starts_at - timedelta(seconds=1), total=50, allowance=60)
    response, _ = await chat(alice, "Hello?", document_ids=[])
    assert response.status_code == 200

    me = (await alice.get("/api/auth/me")).json()
    mine = (await alice.get("/api/me/usage")).json()

    start, end = (value.isoformat().replace("+00:00", "Z") for value in (period.starts_at, period.ends_at))
    answer = (await chat_events(alice.user_id))[-1].allowance_tokens  # 42 + 5 Claude Sonnet 5.5 tokens, $0.000134
    assert answer == 42
    assert me["usage"] == {
        **me["usage"],
        "month": period.starts_at.strftime("%Y-%m"),
        "chat_tokens": 47,
        "chat_allowance_used": answer,
        "monthly_token_limit": STANDARD_ALLOWANCE,
        "window_start": start,
        "window_end": end,
    }
    assert (mine["period_start"], mine["resets_at"], mine["month"]) == (start, end, me["usage"]["month"])
    assert mine["chat_allowance"] == {"used": answer, "limit": STANDARD_ALLOWANCE}
    assert (mine["chat_tokens"], mine["requests"]) == (47, 1)
    assert mine["images"]["limit"] == 30 and mine["vision_tokens"]["limit"] == 500_000  # Standard's
    [today] = mine["daily"]  # the period started today
    assert (today["chat_allowance"], today["chat_tokens"], today["requests"]) == (answer, 47, 1)
    [model] = mine["by_model"]
    assert (model["kind"], model["tokens"], model["allowance_tokens"]) == ("chat", 47, answer)


async def test_usage_without_access_is_reported_for_the_calendar_month(create_user: CreateUser) -> None:
    alice = await create_user("alice", plan=None)
    assert alice.user_id is not None
    start = usage.month_start()

    me = (await alice.get("/api/auth/me")).json()
    mine = (await alice.get("/api/me/usage")).json()

    window = (start.isoformat().replace("+00:00", "Z"), usage.next_month(start).isoformat().replace("+00:00", "Z"))
    assert (me["usage"]["window_start"], me["usage"]["window_end"]) == window
    assert (mine["period_start"], mine["resets_at"]) == window
    assert me["plan"]["state"] == "none"
    assert me["limits"]["monthly_token_limit"] == 500_000  # Starter's, for information


# ---------------- Settings ----------------


def test_the_settings_plans_replaced_are_ignored_with_a_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    for name in REMOVED_SETTINGS:
        monkeypatch.delenv(name, raising=False)
    assert warn_removed_settings() == []

    monkeypatch.setenv("DEFAULT_MONTHLY_TOKEN_LIMIT", "1000000")
    monkeypatch.setenv("vision_monthly_token_limit", "5")
    with caplog.at_level(logging.WARNING, logger="app.config"):
        found = warn_removed_settings()

    assert found == ["DEFAULT_MONTHLY_TOKEN_LIMIT", "VISION_MONTHLY_TOKEN_LIMIT"]
    assert [record.getMessage() for record in caplog.records] == [
        "DEFAULT_MONTHLY_TOKEN_LIMIT is ignored: it is now set per plan in Admin → Plans.",
        "VISION_MONTHLY_TOKEN_LIMIT is ignored: it is now set per plan in Admin → Plans.",
    ]
