"""Revision 0003 (plans and billing) on a database with the users and usage of revision 0002, and the first start after
it: seeding the plans and the receipt counters, and giving existing users a plan once (migrations/versions/
0003_plans_billing.py, app/bootstrap.py; docs/briefs/plans-billing.md 4.11 and 11.1). Tests marked `both` run on SQLite
and, marked postgres, on the TEST_POSTGRES_URL database."""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from conftest import ApiClient, grant_plan
from sqlalchemy import Connection, delete, func, insert, inspect, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import db
from app.bootstrap import bootstrap
from app.config import settings
from app.db import Base, SessionLocal
from app.models import AppState, BillingEvent, LLMModel, Plan, PlanPeriod, UsageEvent, User
from app.services.billing.money import financial_year
from app.services.billing.periods import snapshot_of

both = pytest.mark.usefixtures("on_both")
CreateUser = Callable[..., Awaitable[ApiClient]]
WHEN = datetime(2026, 10, 1, tzinfo=timezone.utc)
LIMITS = ("monthly_token_limit", "rate_limit_per_min", "max_upload_mb", "max_documents", "max_storage_mb")
OVERRIDES = (*LIMITS, "monthly_image_limit", "vision_monthly_token_limit")
# username -> (is_admin, is_active, the five limits, the user_image_limits row or None)
LEGACY_USERS: dict[str, tuple[bool, bool, tuple[int, ...], int | None]] = {
    "admin": (True, True, (0, 0, 25, 0, 0), None),
    "smoke-default": (False, True, (1_000_000, 20, 25, 100, 500), 30),
    "smoke-generous": (False, True, (50_000_000, 100, 100, 10_000, 50_000), 500),
    "smoke-unlimited": (False, True, (0, 0, 100, 0, 0), 0),
    "smoke-no-image-row": (False, True, (1_000_000, 20, 25, 100, 500), None),
    "smoke-inactive": (False, False, (1_000_000, 20, 25, 100, 500), 30),
}


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
def on_both(request: pytest.FixtureRequest) -> str:
    """The test runs on SQLite and, marked postgres, on TEST_POSTGRES_URL (isolated_state reads the mark)."""
    return request.param


async def at_0002() -> None:
    """The schema of revision 0002, with users, their image limits and usage as that release wrote them."""
    async with db.schema_transaction(db.engine) as connection:
        await connection.run_sync(lambda sync: command.upgrade(db._alembic_config(sync), "0002"))
    tables = db.baseline_metadata().tables
    async with db.engine.begin() as connection:
        ids: dict[str, int] = {}
        for name, (is_admin, is_active, limits, _) in LEGACY_USERS.items():
            ids[name] = await connection.scalar(
                insert(tables["users"])
                .values(
                    username=name,
                    password_hash="x",
                    is_admin=is_admin,
                    is_active=is_active,
                    token_version=0,
                    restrict_models=False,
                    created_at=WHEN,
                    updated_at=WHEN,
                    **dict(zip(LIMITS, limits, strict=True)),
                )
                .returning(tables["users"].c.id)
            )
        for name, (*_, image_limit) in LEGACY_USERS.items():
            if image_limit is not None:
                await connection.execute(
                    insert(tables["user_image_limits"]).values(
                        user_id=ids[name], monthly_image_limit=image_limit, updated_at=WHEN
                    )
                )
        for kind, total in (("chat", 1200), ("chat", 34), ("vision", 8632), ("embedding", 400), ("image", 322)):
            await connection.execute(
                insert(tables["usage_events"]).values(
                    user_id=ids["smoke-default"],
                    model_name="m",
                    kind=kind,
                    prompt_tokens=total,
                    completion_tokens=0,
                    total_tokens=total,
                    cost_usd=0.01,
                    created_at=WHEN,
                )
            )


async def run(function: Callable[[Connection], Any]) -> Any:
    async with db.engine.begin() as connection:
        return await connection.run_sync(function)


async def users() -> dict[str, User]:
    async with SessionLocal() as session:
        return {user.username: user for user in await session.scalars(select(User))}


async def billing_events(kind: str) -> list[BillingEvent]:
    async with SessionLocal() as session:
        found = await session.scalars(select(BillingEvent).where(BillingEvent.type == kind).order_by(BillingEvent.id))
        return list(found)


async def start() -> None:
    async with SessionLocal() as session:
        await bootstrap(session)


async def state(key: str) -> str | None:
    async with SessionLocal() as session:
        row = await session.get(AppState, key)
        return None if row is None else row.value


# ---------------- The migration ----------------


@both
async def test_the_limits_move_into_the_audit_trail_and_become_the_plans() -> None:
    await at_0002()

    await db.init_db()

    found = await users()
    assert {name: [getattr(user, column) for column in OVERRIDES] for name, user in found.items()} == {
        name: [None] * 7 for name in LEGACY_USERS
    }
    assert {(user.billing_tester, user.comp_plan_id) for user in found.values()} == {(False, None)}
    cleared = await billing_events("rollout.limits_cleared")
    assert [(event.source, event.actor, event.processed) for event in cleared] == [("system", "system", True)] * 5
    assert {event.detail["username"]: event.detail for event in cleared} == {
        name: {
            "username": name,
            **dict(zip(LIMITS, limits, strict=True)),
            "monthly_image_limit": image_limit,
        }
        for name, (is_admin, _, limits, image_limit) in LEGACY_USERS.items()
        if not is_admin
    }
    assert {event.user_id for event in cleared} == {found[event.detail["username"]].id for event in cleared}
    assert "user_image_limits" not in await run(lambda connection: inspect(connection).get_table_names())


@both
async def test_chat_events_count_their_tokens_against_the_allowance() -> None:
    await at_0002()

    await db.init_db()

    async with SessionLocal() as session:
        rows = await session.execute(select(UsageEvent.kind, UsageEvent.total_tokens, UsageEvent.allowance_tokens))
        assert sorted(rows.tuples()) == [
            ("chat", 34, 34),
            ("chat", 1200, 1200),
            ("embedding", 400, 0),
            ("image", 322, 0),
            ("vision", 8632, 0),
        ]


@both
async def test_the_migrated_schema_matches_the_models() -> None:
    await at_0002()

    await db.init_db()

    differences = await run(
        lambda connection: compare_metadata(
            MigrationContext.configure(connection, opts=db.COMPARE_OPTIONS), Base.metadata
        )
    )
    assert differences == []


@pytest.mark.postgres
async def test_on_postgres_a_users_counting_periods_never_overlap() -> None:
    await db.init_db()
    await start()
    async with SessionLocal() as session:
        member = User(username="smoke-pg", password_hash="x")
        session.add(member)
        await session.flush()
        user_id = member.id
        plan = await session.scalar(select(Plan).where(Plan.code == "starter"))
        assert plan is not None
        snapshot = snapshot_of(plan).model_dump(mode="json")
        await session.commit()
    names = await run(
        lambda connection: connection.execute(
            text("SELECT conname FROM pg_constraint WHERE conrelid = 'plan_periods'::regclass AND contype = 'x'")
        ).scalars().all()
    )
    assert names == ["ex_plan_periods_no_overlap"]

    def period(start: datetime, end: datetime, **fields: Any) -> PlanPeriod:
        values = {"source": "payment", "mode": "live", "snapshot": snapshot, "charge_basis_paise": 0, "days": 30}
        return PlanPeriod(user_id=user_id, plan_id=plan.id, starts_at=start, ends_at=end, **{**values, **fields})

    async def add(*rows: PlanPeriod) -> None:
        async with SessionLocal() as session:
            session.add_all(rows)
            await session.commit()

    one, two, three = WHEN, WHEN + timedelta(days=30), WHEN + timedelta(days=60)
    await add(period(one, two), period(two, three))  # back to back
    await add(period(one, three, mode="test"), period(one, three, revoked_at=two))  # test-mode and revoked ones may
    with pytest.raises(IntegrityError, match="ex_plan_periods_no_overlap"):
        await add(period(one + timedelta(days=1), two))
    async with SessionLocal() as session:  # checked at commit: a transaction may move periods through an overlap
        counting = select(PlanPeriod).where(PlanPeriod.mode == "live", PlanPeriod.revoked_at.is_(None))
        rows = list(await session.scalars(counting))
        first, second = sorted(rows, key=lambda row: row.starts_at)
        second.starts_at, second.ends_at = one, two
        await session.flush()
        first.starts_at, first.ends_at = two, three
        await session.commit()


# ---------------- The first start ----------------


@both
async def test_the_first_start_seeds_the_plans_the_counters_and_gives_existing_users_a_plan(
    caplog: pytest.LogCaptureFixture,
) -> None:
    await at_0002()
    await db.init_db()

    with caplog.at_level(logging.INFO, logger="app.bootstrap"):
        await start()

    async with SessionLocal() as session:
        plans = list(await session.scalars(select(Plan).order_by(Plan.rank)))
        models = {model.id: model.litellm_model for model in await session.scalars(select(LLMModel))}
    assert [
        (
            plan.code,
            plan.name,
            plan.price_paise,
            plan.rank,
            plan.status,
            plan.recommended,
            plan.features,
            [getattr(plan, name) for name in OVERRIDES],
            plan.restrict_models,
            sorted(models[model.id] for model in plan.chat_models),
            models.get(plan.default_chat_model_id or 0),
        )
        for plan in plans
    ] == [
        (
            "starter",
            "Starter",
            50_000,
            10,
            "active",
            False,
            {"files": False, "images": False, "plugins": []},
            [500_000, 10, 10, 50, 200, 30, 150_000],
            True,
            ["anthropic/claude-haiku-5-5", "openai/gpt-5.4-mini"],
            "anthropic/claude-haiku-5-5",
        ),
        (
            "standard",
            "Standard",
            200_000,
            20,
            "active",
            True,
            {"files": True, "images": False, "plugins": []},
            [2_000_000, 20, 25, 200, 1000, 30, 500_000],
            True,
            ["anthropic/claude-haiku-5-5", "anthropic/claude-sonnet-5-5", "openai/gpt-5.4-mini"],
            "anthropic/claude-sonnet-5-5",
        ),
        (
            "professional",
            "Professional",
            500_000,
            30,
            "active",
            False,
            {"files": True, "images": True, "plugins": ["gmail"]},
            [5_000_000, 30, 50, 500, 3000, 60, 1_000_000],
            False,
            [],
            None,
        ),
    ]
    assert plans[0].description == "Ask questions across your documents, for everyday use."
    now = datetime.now(timezone.utc)
    years = {financial_year(now), financial_year(now + timedelta(days=366))}
    async with SessionLocal() as session:
        counters = await session.execute(select(AppState.key, AppState.value).where(AppState.key.like("receipt_seq:%")))
        expected = {f"receipt_seq:{mode}:{year}": "0" for mode in ("live", "test") for year in years}
        assert dict(counters.all()) == expected

    found = await users()
    professional = plans[2].id
    assert {name: user.comp_plan_id for name, user in found.items()} == {
        "admin": None,
        "smoke-default": professional,
        "smoke-generous": professional,
        "smoke-unlimited": professional,
        "smoke-no-image-row": professional,
        "smoke-inactive": None,  # the admin grants a plan on reactivation
    }
    rolled_out = found["smoke-default"]
    assert rolled_out.comp_reason == "Existing account when plans were introduced"
    assert rolled_out.comp_granted_by == "system"
    assert rolled_out.comp_granted_at is not None
    assert await state("plans_seeded") == "starter,standard,professional" and await state("plans_rollout") == "4"
    granted = await billing_events("complimentary.granted")
    active = [found[name].id for name in LEGACY_USERS if name not in ("admin", "smoke-inactive")]
    assert sorted(event.user_id for event in granted) == sorted(active)
    assert {(event.source, event.actor) for event in granted} == {("system", "system")}
    assert "Seeded the plans Starter, Standard, Professional" in caplog.text
    assert "Gave 4 existing user(s) the open-ended Professional plan" in caplog.text


@both
async def test_later_starts_change_nothing() -> None:
    await at_0002()
    await db.init_db()
    await start()

    async def everything() -> tuple[Any, ...]:
        async with SessionLocal() as session:
            plans = [(plan.id, plan.code, plan.updated_at) for plan in await session.scalars(select(Plan))]
            flags = dict((await session.execute(select(AppState.key, AppState.value))).all())
            events = await session.scalar(select(func.count()).select_from(BillingEvent))
            comps = [(user.id, user.comp_plan_id, user.comp_granted_at) for user in await session.scalars(select(User))]
        return plans, flags, events, comps

    before = await everything()
    async with SessionLocal() as session:  # a user added afterwards gets no plan from the rollout
        session.add(User(username="smoke-new", password_hash="x"))
        await session.commit()
    await start()

    after = await everything()
    assert after[:3] == before[:3]
    assert [comp for comp in after[3] if comp[0] != (await users())["smoke-new"].id] == before[3]
    assert (await users())["smoke-new"].comp_plan_id is None


async def test_the_rollout_passes_over_users_who_already_have_a_plan(create_user: CreateUser) -> None:
    plain = await create_user("plain", plan=None, login=False)
    dated = await create_user("dated", plan=None, login=False)
    open_ended = await create_user("open-ended", plan="starter", login=False)
    assert dated.user_id is not None
    await grant_plan(dated.user_id, "standard", days=10)
    async with SessionLocal() as session:
        await session.execute(delete(AppState).where(AppState.key == "plans_rollout"))
        await session.commit()

    await start()

    found = await users()
    async with SessionLocal() as session:
        codes = {plan.id: plan.code for plan in await session.scalars(select(Plan))}
    assert {name: codes.get(found[name].comp_plan_id or 0) for name in ("plain", "dated", "open-ended")} == {
        "plain": "professional",
        "dated": None,
        "open-ended": "starter",
    }
    assert plain.user_id == found["plain"].id and open_ended.user_id == found["open-ended"].id
    assert await state("plans_rollout") == "1"


async def test_without_a_professional_plan_the_rollout_waits(app: Any, caplog: pytest.LogCaptureFixture) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(Plan).where(Plan.code == "professional"))
        await session.execute(delete(AppState).where(AppState.key == "plans_rollout"))
        await session.commit()

    with caplog.at_level(logging.WARNING, logger="app.bootstrap"):
        await start()

    assert await state("plans_rollout") is None
    assert "No 'professional' plan to give existing users" in caplog.text


async def forget_plans() -> None:
    """The database before plans were seeded: no plans, no flag."""
    async with SessionLocal() as session:
        await session.execute(delete(Plan))
        await session.execute(delete(AppState).where(AppState.key == "plans_seeded"))
        await session.commit()


async def test_seeding_skips_models_that_dont_exist_and_caps_the_upload_size(
    app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await forget_plans()
    async with SessionLocal() as session:
        await session.execute(delete(LLMModel).where(LLMModel.litellm_model.in_(["anthropic/claude-haiku-5-5"])))
        await session.commit()
    monkeypatch.setattr(settings, "max_upload_mb_hard", 20)

    await start()

    async with SessionLocal() as session:
        plans = {plan.code: plan for plan in await session.scalars(select(Plan))}
        assert [model.litellm_model for model in plans["starter"].chat_models] == ["openai/gpt-5.4-mini"]
        assert plans["starter"].default_chat_model_id is None  # Claude Haiku 5.5 isn't there
        default = await session.get(LLMModel, plans["standard"].default_chat_model_id)
    assert default is not None and default.litellm_model == "anthropic/claude-sonnet-5-5"
    uploads = {code: plan.max_upload_mb for code, plan in plans.items()}
    assert uploads == {"starter": 10, "standard": 20, "professional": 20}


async def test_plans_that_exist_are_kept(app: Any) -> None:
    async with SessionLocal() as session:
        await session.execute(update(Plan).where(Plan.code == "starter").values(name="Basic"))
        await session.execute(delete(AppState).where(AppState.key == "plans_seeded"))
        await session.commit()

    await start()

    async with SessionLocal() as session:
        names = sorted(await session.scalars(select(Plan.name)))
    assert names == ["Basic", "Professional", "Standard"]
    assert await state("plans_seeded") == "existing"


async def test_two_backends_seeding_at_once_add_the_plans_once(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    await forget_plans()
    commit = AsyncSession.commit
    raced: list[bool] = []

    async def racing_commit(self: AsyncSession) -> None:
        if not raced and any(isinstance(row, AppState) and row.key == "plans_seeded" for row in self.new):
            raced.append(True)
            async with SessionLocal() as other:  # the other backend commits first
                other.add(AppState(key="plans_seeded", value="starter,standard,professional"))
                await commit(other)
        await commit(self)

    monkeypatch.setattr(AsyncSession, "commit", racing_commit)

    await start()

    assert raced == [True]
    async with SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(Plan)) == 0  # this start's plans rolled back
