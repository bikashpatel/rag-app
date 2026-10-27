"""What a user may do (app/services/billing/entitlements.py; docs/briefs/plans-billing.md 4.1 and 5): the admin, a
user active by a period or by an open-ended plan, one with no plan and one whose plan ended; overrides, model narrowing,
windows, the plan that unlocks a feature, "start a new period now", and Me.plan."""

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from conftest import ADMIN_USERNAME, ApiClient, end_plan, grant_plan
from fastapi import Request
from sqlalchemy import select, update

from app.config import settings
from app.db import SessionLocal
from app.errors import AppError
from app.main import app as fastapi_app
from app.models import BillingOrder, LLMModel, Plan, PlanPeriod, UsageEvent, User, utcnow
from app.plugins import registry
from app.plugins.gmail.plugin import GmailPlugin
from app.schemas_billing import PlanAccess
from app.services.billing import entitlements, periods
from app.services.billing.entitlements import Entitlements, Limits

CreateUser = Callable[..., Awaitable[ApiClient]]

HAIKU, GPT_MINI, SONNET, OPUS, GPT = (
    "anthropic/claude-haiku-5-5",
    "openai/gpt-5.4-mini",
    "anthropic/claude-sonnet-5-5",
    "anthropic/claude-opus-5-5",
    "openai/gpt-5.5",
)
STARTER = Limits(500_000, 10, 10, 50, 200, 30, 150_000)
PROFESSIONAL = Limits(5_000_000, 30, 50, 500, 3000, 60, 1_000_000)
OVERRIDES = (
    "monthly_token_limit",
    "rate_limit_per_min",
    "max_upload_mb",
    "max_documents",
    "max_storage_mb",
    "monthly_image_limit",
    "vision_monthly_token_limit",
)


@pytest.fixture(autouse=True)
def gmail_registered() -> Iterator[None]:
    with registry.use_plugins([GmailPlugin()]):
        yield


@pytest.fixture
async def models(app: Any) -> dict[str, int]:
    async with SessionLocal() as db:
        rows = await db.execute(select(LLMModel.litellm_model, LLMModel.id).where(LLMModel.kind == "chat"))
        return {name: model_id for name, model_id in rows.tuples()}


@pytest.fixture
async def user(create_user: CreateUser) -> AsyncIterator[Callable[..., Awaitable[int]]]:
    """Factory: a user of the plan (open-ended unless `days`), without limit overrides; returns the id."""

    async def make(name: str, plan: str | None = "professional", days: int | None = None) -> int:
        client = await create_user(name, plan=plan if days is None else None, login=False)
        assert client.user_id is not None
        await set_user(client.user_id, **dict.fromkeys(OVERRIDES))
        if plan is not None and days is not None:
            await grant_plan(client.user_id, plan, days=days)
        return client.user_id

    yield make


async def set_user(user_id: int, **values: Any) -> None:
    async with SessionLocal() as db:
        await db.execute(update(User).where(User.id == user_id).values(**values))
        await db.commit()


async def allow(user_id: int, *model_ids: int) -> None:
    async with SessionLocal() as db:
        row = await db.get(User, user_id)
        assert row is not None
        row.restrict_models = True
        row.allowed_models = list(await db.scalars(select(LLMModel).where(LLMModel.id.in_(model_ids))))
        await db.commit()


async def ent_of(user_id: int) -> Entitlements:
    async with SessionLocal() as db:
        row = await db.get(User, user_id)
        assert row is not None
        return await entitlements.load(db, row)


async def access_of(user_id: int) -> PlanAccess:
    async with SessionLocal() as db:
        row = await db.get(User, user_id)
        assert row is not None
        return await entitlements.plan_access(db, row, await entitlements.load(db, row))


async def admin_id() -> int:
    async with SessionLocal() as db:
        found = await db.scalar(select(User.id).where(User.username == ADMIN_USERNAME))
    assert found is not None
    return found


async def user_periods(user_id: int) -> list[PlanPeriod]:
    async with SessionLocal() as db:
        return list(await db.scalars(select(PlanPeriod).where(PlanPeriod.user_id == user_id).order_by(PlanPeriod.id)))


async def add_usage(user_id: int, kind: str, *, allowance: int = 0, tokens: int = 0, count: int = 1) -> None:
    async with SessionLocal() as db:
        db.add_all(
            UsageEvent(
                user_id=user_id, model_name="m", kind=kind, total_tokens=tokens, allowance_tokens=allowance
            )
            for _ in range(count)
        )
        await db.commit()


async def edit_plan(code: str, **values: Any) -> None:
    async with SessionLocal() as db:
        await db.execute(update(Plan).where(Plan.code == code).values(**values))
        await db.commit()


def calendar_month(now: datetime) -> tuple[datetime, datetime]:
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start, (start + timedelta(days=32)).replace(day=1)


async def raised(error: AppError) -> tuple[int, dict[str, Any]]:
    """How main.py renders the error: its status and body."""
    handler = fastapi_app.exception_handlers[AppError]
    response = await handler(Request({"type": "http", "headers": []}), error)
    return response.status_code, json.loads(bytes(response.body))


# ---------------- The admin, open-ended plans and periods ----------------


async def test_the_admin_is_exempt_from_every_plan(app: Any, models: dict[str, int]) -> None:
    ent = await ent_of(await admin_id())

    assert (ent.state, ent.access, ent.is_admin, ent.plan_name) == ("exempt", True, True, None)
    assert (ent.files, ent.images, ent.plugins) == (True, True, frozenset({"gmail"}))
    assert ent.chat_model_ids is None and ent.plan_model_ids is None
    assert ent.limits == Limits(0, 0, settings.max_upload_mb_hard, 0, 0, 0, 0)
    assert (ent.window_start, ent.window_end) == calendar_month(utcnow())
    assert ent.paid_through is None and ent.valid_through is None
    entitlements.require_access(ent)
    assert entitlements.allows_model(ent, models[OPUS]) and entitlements.allows_plugin(ent, "gmail")
    plan = await access_of(await admin_id())
    assert (plan.state, plan.access, plan.plan, plan.pending_order) == ("exempt", True, None, None)
    assert plan.features.model_dump() == {"files": True, "images": True, "plugins": ["gmail"]}
    assert plan.unlock.models is None


async def test_an_open_ended_plan_gives_the_plans_live_values_by_calendar_month(
    user: Callable[..., Awaitable[int]],
) -> None:
    alice = await user("alice")

    ent = await ent_of(alice)

    assert (ent.state, ent.access, ent.plan_name, ent.period_id) == ("active", True, "Professional", None)
    assert (ent.complimentary, ent.open_ended, ent.test_mode) == (True, True, False)
    assert (ent.files, ent.images, ent.plugins) == (True, True, frozenset({"gmail"}))
    assert ent.chat_model_ids is None and ent.plan_model_ids is None and ent.default_chat_model_id is None
    assert ent.limits == PROFESSIONAL
    assert (ent.window_start, ent.window_end) == calendar_month(utcnow())
    assert ent.paid_through is None and ent.valid_through is None
    entitlements.require_access(ent)

    features = {"files": True, "images": False, "plugins": []}
    await edit_plan("professional", monthly_token_limit=7_000_000, features=features)
    entitlements.invalidate_unlock()  # every plan change does

    changed = await ent_of(alice)
    assert (changed.limits.monthly_token_limit, changed.images, changed.plugins) == (7_000_000, False, frozenset())


async def test_a_period_takes_precedence_with_the_snapshot_it_got(
    user: Callable[..., Awaitable[int]], models: dict[str, int]
) -> None:
    alice = await user("alice")
    await grant_plan(alice, "starter", days=30)
    [period] = await user_periods(alice)

    await edit_plan("starter", monthly_token_limit=1)  # after the period got its snapshot
    entitlements.invalidate_unlock()
    ent = await ent_of(alice)

    assert (ent.state, ent.plan_name, ent.period_id, ent.complimentary, ent.open_ended) == (
        "active",
        "Starter",
        period.id,
        True,
        False,
    )
    assert (ent.files, ent.images, ent.plugins) == (False, False, frozenset())
    assert ent.chat_model_ids == ent.plan_model_ids == frozenset({models[HAIKU], models[GPT_MINI]})
    assert ent.default_chat_model_id == models[HAIKU]
    assert ent.limits == STARTER
    assert (ent.window_start, ent.window_end) == (period.starts_at, period.ends_at)
    assert ent.paid_through == period.ends_at and ent.valid_through == periods.valid_through(period.ends_at)
    plan = await access_of(alice)
    assert plan.plan is not None and plan.plan.code == "starter"
    assert plan.next_plan is not None and plan.next_plan.code == "professional"  # the open-ended plan follows
    assert (plan.period_start, plan.period_end) == (period.starts_at, period.ends_at)
    assert plan.expiring_soon is False


async def test_without_a_plan_nothing_is_open(user: Callable[..., Awaitable[int]]) -> None:
    bob = await user("bob", plan=None)

    ent = await ent_of(bob)

    assert (ent.state, ent.access, ent.plan_name, ent.plan_id) == ("none", False, None, None)
    assert (ent.files, ent.images, ent.plugins, ent.chat_model_ids, ent.plan_model_ids) == (
        False,
        False,
        frozenset(),
        frozenset(),
        frozenset(),
    )
    assert ent.limits == STARTER  # the cheapest active plan's, for information
    assert (ent.window_start, ent.window_end) == calendar_month(utcnow())
    with pytest.raises(AppError) as refused:
        entitlements.require_access(ent)
    assert await raised(refused.value) == (
        402,
        {"detail": "Choose a plan to start using the assistant.", "code": "plan_required"},
    )
    assert not entitlements.allows_model(ent, 1) and not entitlements.allows_plugin(ent, "gmail")
    plan = await access_of(bob)
    assert (plan.state, plan.access, plan.plan, plan.ended_at, plan.can_start_new_period) == (
        "none",
        False,
        None,
        None,
        False,
    )
    assert plan.features.model_dump() == {"files": False, "images": False, "plugins": []}
    assert plan.unlock.model_dump() == {
        "files": "Standard",
        "images": "Professional",
        "plugins": {"gmail": "Professional"},
        "models": "Starter",
    }


async def test_an_ended_plan_is_expired_with_the_last_periods_values(user: Callable[..., Awaitable[int]]) -> None:
    bob = await user("bob", "standard", days=30)
    await end_plan(bob)
    [period] = await user_periods(bob)

    ent = await ent_of(bob)

    assert (ent.state, ent.access, ent.plan_name, ent.period_id) == ("expired", False, "Standard", None)
    assert ent.paid_through == period.ends_at and ent.valid_through == periods.valid_through(period.ends_at)
    assert (ent.window_start, ent.window_end) == (period.starts_at, period.ends_at)
    assert ent.limits.monthly_token_limit == 2_000_000 and ent.files is False
    with pytest.raises(AppError) as refused:
        entitlements.require_access(ent)
    day = periods.valid_through(period.ends_at)
    assert await raised(refused.value) == (
        402,
        {
            "detail": f"Your Standard plan ended on {day.day} {day:%b %Y}. Renew it to continue; your chats and "
            "documents are kept.",
            "code": "plan_expired",
            "plan": "Standard",
        },
    )
    plan = await access_of(bob)
    assert (plan.state, plan.ended_at, plan.paid_through) == ("expired", period.ends_at, period.ends_at)
    assert plan.plan is not None and plan.plan.code == "standard"


async def test_a_revoked_period_ends_access_when_it_was_revoked(user: Callable[..., Awaitable[int]]) -> None:
    bob = await user("bob", "starter", days=30)
    async with SessionLocal() as db:
        [period] = await periods.user_periods(db, bob)
        await periods.revoke(db, period, reason="Refunded", actor=ADMIN_USERNAME, source="admin")
        await db.commit()
        revoked_at = period.revoked_at

    ent = await ent_of(bob)

    assert (ent.state, ent.plan_name, ent.paid_through) == ("expired", "Starter", revoked_at)


# ---------------- Overrides and models ----------------


@pytest.mark.parametrize("name", OVERRIDES)
async def test_an_override_replaces_the_plans_value(user: Callable[..., Awaitable[int]], name: str) -> None:
    alice = await user("alice", "starter", days=30)
    await set_user(alice, **{name: 7})

    ent = await ent_of(alice)

    assert ent.limits == Limits(**{**STARTER.__dict__, name: 7})
    assert entitlements.user_limits(ent).model_dump() == ent.limits.__dict__


async def test_an_override_of_0_is_unlimited_and_the_upload_size_stays_capped(
    user: Callable[..., Awaitable[int]],
) -> None:
    alice = await user("alice", "starter", days=30)
    await set_user(alice, monthly_token_limit=0, max_upload_mb=settings.max_upload_mb_hard + 50)

    ent = await ent_of(alice)

    assert (ent.limits.monthly_token_limit, ent.limits.max_upload_mb) == (0, settings.max_upload_mb_hard)
    assert "vision_monthly_token_limit" in entitlements.user_limits(ent).model_dump(mode="json")


async def test_the_users_list_only_narrows_the_plans_models(
    user: Callable[..., Awaitable[int]], models: dict[str, int]
) -> None:
    alice = await user("alice", "starter", days=30)
    await allow(alice, models[OPUS], models[HAIKU])

    ent = await ent_of(alice)

    assert ent.chat_model_ids == frozenset({models[HAIKU]})  # Opus is in her list, not in Starter
    assert ent.plan_model_ids == frozenset({models[HAIKU], models[GPT_MINI]})
    assert entitlements.allows_model(ent, models[HAIKU])
    assert not entitlements.allows_model(ent, models[OPUS])  # outside the plan: 402
    assert not entitlements.allows_model(ent, models[GPT_MINI])  # in the plan, not in her list: 403
    assert models[OPUS] not in (ent.plan_model_ids or set())
    assert models[GPT_MINI] in (ent.plan_model_ids or set()) and models[GPT_MINI] not in (ent.chat_model_ids or set())

    await allow(alice, models[OPUS])
    assert (await ent_of(alice)).chat_model_ids == frozenset()  # no model left


async def test_with_every_model_in_the_plan_the_users_list_decides(
    user: Callable[..., Awaitable[int]], models: dict[str, int]
) -> None:
    alice = await user("alice")
    await allow(alice, models[OPUS])

    ent = await ent_of(alice)

    assert (ent.chat_model_ids, ent.plan_model_ids) == (frozenset({models[OPUS]}), None)


# ---------------- Unlocking features ----------------


async def test_a_locked_feature_names_the_cheapest_plan_with_it(
    app: Any, user: Callable[..., Awaitable[int]], models: dict[str, int]
) -> None:
    alice = await user("alice", "starter", days=30)
    ent = await ent_of(alice)

    async with SessionLocal() as db:
        gmail = await entitlements.feature_error(db, ent, "plugin:gmail", "Gmail")
        files = await entitlements.feature_error(db, ent, "files", "files from chat")
        sonnet = await entitlements.feature_error(db, ent, f"model:{models[SONNET]}", "Claude Sonnet 5.5")
        opus = await entitlements.feature_error(db, ent, f"model:{models[OPUS]}", "Claude Opus 5.5")
        other = await entitlements.feature_error(db, ent, "plugin:calendar", "Calendar")

    assert await raised(gmail) == (
        402,
        {
            "detail": "Your Starter plan doesn't include Gmail. It's included in Professional.",
            "code": "feature_not_in_plan",
            "feature": "plugin:gmail",
            "plan": "Professional",
        },
    )
    assert (files.feature, files.plan) == ("files", "Standard")
    assert (sonnet.feature, sonnet.plan) == ("model", "Standard")
    assert (opus.feature, opus.plan) == ("model", "Professional")
    assert (other.detail, other.plan) == ("Your Starter plan doesn't include Calendar.", None)


async def test_unlock_reads_the_active_plans_cached_for_a_minute(
    app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unlocked() -> dict[str, Any]:
        async with SessionLocal() as db:
            return (await entitlements.unlock(db)).model_dump()

    assert await unlocked() == {
        "files": "Standard",
        "images": "Professional",
        "plugins": {"gmail": "Professional"},
        "models": "Professional",
    }
    await edit_plan("standard", status="retired", recommended=False)
    assert (await unlocked())["files"] == "Standard"  # cached
    clock = entitlements.time.monotonic() + 61
    monkeypatch.setattr(entitlements.time, "monotonic", lambda: clock)
    assert (await unlocked())["files"] == "Professional"  # a minute later
    await edit_plan("professional", features={"files": True, "images": True, "plugins": []})
    entitlements.invalidate_unlock()
    assert (await unlocked())["plugins"] == {"gmail": None}


@pytest.mark.parametrize(("plan", "unlocks"), [("starter", "Standard"), ("standard", "Professional"), (None, None)])
async def test_more_models_are_unlocked_by_the_next_plan_that_has_them(
    user: Callable[..., Awaitable[int]], plan: str | None, unlocks: str | None
) -> None:
    alice = await user("alice") if plan is None else await user("alice", plan, days=30)

    assert (await access_of(alice)).unlock.models == unlocks


async def test_an_app_error_keeps_its_headers(app: Any) -> None:
    error = AppError(429, "Try again in an hour.", "too_many_checkouts", headers={"Retry-After": "3600"})
    handler = fastapi_app.exception_handlers[AppError]

    response = await handler(Request({"type": "http", "headers": []}), error)

    assert (response.status_code, response.headers["retry-after"]) == (429, "3600")
    assert json.loads(bytes(response.body)) == {"detail": error.detail, "code": "too_many_checkouts"}


# ---------------- Me.plan ----------------


@pytest.mark.parametrize(
    ("kind", "used", "can"),
    [
        ("chat", 445_000, False),  # 89% of 500,000
        ("chat", 450_000, True),  # 90%
        ("vision", 134_999, False),
        ("vision", 135_000, True),  # 90% of 150,000
        ("image", 30, False),  # Starter has no images: its image limit doesn't count
    ],
)
async def test_a_new_period_can_start_once_90_percent_of_an_allowance_is_used(
    user: Callable[..., Awaitable[int]], kind: str, used: int, can: bool
) -> None:
    alice = await user("alice", "starter", days=30)
    if kind == "image":
        await add_usage(alice, "image", count=used)
    else:
        await add_usage(alice, kind, allowance=used if kind == "chat" else 0, tokens=used)

    assert (await access_of(alice)).can_start_new_period is can


async def test_an_open_ended_plan_never_offers_a_new_period(user: Callable[..., Awaitable[int]]) -> None:
    alice = await user("alice")
    await add_usage(alice, "chat", allowance=5_000_000)

    assert (await access_of(alice)).can_start_new_period is False


async def test_a_plan_ending_soon_with_nothing_after_it_is_expiring(user: Callable[..., Awaitable[int]]) -> None:
    alice = await user("alice", "starter", days=3)
    assert (await access_of(alice)).expiring_soon is True

    await grant_plan(alice, "standard", days=30, start="after_current")

    plan = await access_of(alice)
    first, second = await user_periods(alice)
    assert (plan.expiring_soon, plan.paid_through, plan.valid_through) == (
        False,
        second.ends_at,
        periods.valid_through(second.ends_at),
    )
    assert plan.next_plan is not None and plan.next_plan.code == "standard"
    assert (plan.period_start, plan.period_end) == (first.starts_at, first.ends_at)


async def test_an_open_checkout_is_shown_with_its_link_until_it_expires(user: Callable[..., Awaitable[int]]) -> None:
    alice = await user("alice", plan=None)
    now = utcnow()
    async with SessionLocal() as db:
        plan = await db.scalar(select(Plan).where(Plan.code == "standard"))
        assert plan is not None
        db.add(
            BillingOrder(
                public_id="ord-1",
                user_id=alice,
                username="alice",
                plan_snapshot=periods.snapshot_of(plan).model_dump(mode="json"),
                kind="period",
                amount_paise=200_000,
                mode="test",
                status="pending",
                pay_url="https://rzp.io/i/abc",
                origin="http://testserver",
                expires_at=now + timedelta(minutes=20),
                watch_until=now + timedelta(days=5),
            )
        )
        await db.commit()

    pending = (await access_of(alice)).pending_order
    assert pending is not None
    assert (pending.public_id, pending.kind, pending.plan.code, pending.amount_paise, pending.pay_url) == (
        "ord-1",
        "period",
        "standard",
        200_000,
        "https://rzp.io/i/abc",
    )

    async with SessionLocal() as db:
        await db.execute(update(BillingOrder).values(expires_at=now - timedelta(minutes=1)))
        await db.commit()
    expired = (await access_of(alice)).pending_order
    assert expired is not None and expired.pay_url is None

    async with SessionLocal() as db:
        await db.execute(update(BillingOrder).values(status="expired"))
        await db.commit()
    assert (await access_of(alice)).pending_order is None


# ---------------- Test mode and many users ----------------


@pytest.mark.parametrize(("key_id", "counts"), [("rzp_test_abc", True), ("rzp_live_abc", False), ("", False)])
async def test_a_test_mode_period_counts_only_under_test_keys(
    user: Callable[..., Awaitable[int]], monkeypatch: pytest.MonkeyPatch, key_id: str, counts: bool
) -> None:
    alice = await user("alice", plan=None)
    async with SessionLocal() as db:
        plan = await db.scalar(select(Plan).where(Plan.code == "standard"))
        assert plan is not None
        monkeypatch.setattr(settings, "razorpay_key_id", "rzp_test_abc")
        monkeypatch.setattr(settings, "razorpay_key_secret", "secret")
        await periods.add_period(
            db, alice, snapshot=periods.snapshot_of(plan), days=1, source="payment", mode="test", basis_paise=200_000
        )
        await db.commit()
    monkeypatch.setattr(settings, "razorpay_key_id", key_id)

    ent = await ent_of(alice)

    assert (ent.state, ent.test_mode) == (("active", True) if counts else ("none", False))


async def test_many_users_load_as_one_by_one(user: Callable[..., Awaitable[int]]) -> None:
    ids = [
        await admin_id(),
        await user("alice"),
        await user("bob", "starter", days=30),
        await user("carol", plan=None),
        await user("dave", "standard", days=30),
    ]
    await end_plan(ids[-1])

    async with SessionLocal() as db:
        users = list(await db.scalars(select(User).where(User.id.in_(ids))))
        together = await entitlements.load_many(db, users)

    assert together == {user_id: await ent_of(user_id) for user_id in ids}
    assert [together[user_id].state for user_id in ids] == ["exempt", "active", "active", "none", "expired"]
