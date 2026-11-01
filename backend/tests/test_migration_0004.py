"""Revision 0004 (sign-up) on a database of revision 0003 with the env admin, an admin-created user, billing profile
emails and the billing records of a deleted user, and the first start after it (migrations/versions/
0004_signup_auth.py, app/bootstrap.py; docs/briefs/signup-auth.md 9.2). Tests marked `both` run on SQLite and, marked
postgres, on the TEST_POSTGRES_URL database."""

import logging
from datetime import datetime, timezone
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, MetaData, insert, inspect, select, text
from sqlalchemy.exc import IntegrityError

from app import db
from app.bootstrap import bootstrap
from app.db import Base, SessionLocal
from app.models import LoginEvent, User, UserBillingProfile, UsernameTombstone, utcnow

both = pytest.mark.usefixtures("on_both")
WHEN = datetime(2026, 10, 1, tzinfo=timezone.utc)
ADDRESS = "owner.private@example.com"


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
def on_both(request: pytest.FixtureRequest) -> str:
    """The test runs on SQLite and, marked postgres, on TEST_POSTGRES_URL (isolated_state reads the mark)."""
    return request.param


@pytest.fixture(autouse=True)
def migration_logs(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.INFO, logger="alembic.runtime.migration")
    return caplog


async def run(function: Any) -> Any:
    async with db.engine.begin() as connection:
        return await connection.run_sync(function)


def tables_at_0003(connection: Connection) -> MetaData:
    metadata = MetaData()
    metadata.reflect(connection)
    return metadata


async def at_0003() -> dict[str, int]:
    """The schema of revision 0003 with the rows that release wrote; returns the users' ids by name."""
    async with db.schema_transaction(db.engine) as connection:
        await connection.run_sync(lambda sync: command.upgrade(db._alembic_config(sync), "0003"))
    tables = (await run(tables_at_0003)).tables
    user = {"password_hash": "$argon2id$v=19$m=65536,t=3,p=4$legacy", "token_version": 0, "restrict_models": False}
    ids: dict[str, int] = {}
    async with db.engine.begin() as connection:
        for name, is_admin, is_active in (
            ("admin", True, True),
            ("bikash", False, True),
            ("smoke-inactive", False, False),
        ):
            ids[name] = await connection.scalar(
                insert(tables["users"])
                .values(
                    username=name,
                    is_admin=is_admin,
                    is_active=is_active,
                    billing_tester=False,
                    created_at=WHEN,
                    updated_at=WHEN,
                    **user,
                )
                .returning(tables["users"].c.id)
            )
        for name, email in (("bikash", ADDRESS), ("smoke-inactive", ""), ("admin", None)):
            await connection.execute(
                insert(tables["user_billing_profiles"]).values(
                    user_id=ids[name], name=f"{name} name", email=email, updated_at=WHEN
                )
            )
        await connection.execute(
            insert(tables["login_events"]).values(user_id=ids["bikash"], created_at=WHEN, device="desktop")
        )
        order = {
            "plan_snapshot": {},
            "kind": "period",
            "credit_paise": 0,
            "amount_paise": 50_000,
            "currency": "INR",
            "mode": "live",
            "status": "paid",
            "origin": "https://asknuvexusdoc.com",
            "expires_at": WHEN,
            "watch_until": WHEN,
            "check_step": 0,
            "checks": 0,
            "created_at": WHEN,
            "updated_at": WHEN,
        }
        payment = {
            "status": "captured",
            "amount_paise": 50_000,
            "currency": "INR",
            "refunded_paise": 0,
            "mode": "live",
            "created_at": WHEN,
            "updated_at": WHEN,
        }
        # Deleted users keep their username on billing records; "bikash" also appears with user_id NULL, as a
        # deleted user of that name before the current one could have.
        for number, (username, user_id) in enumerate(
            (("smoke-deleted", None), ("smoke-deleted", None), ("bikash", None), ("bikash", ids["bikash"]))
        ):
            await connection.execute(
                insert(tables["billing_orders"]).values(
                    public_id=f"order{number}", username=username, user_id=user_id, **order
                )
            )
        for username in ("smoke-deleted", "smoke-gone-payment"):
            await connection.execute(insert(tables["billing_payments"]).values(username=username, **payment))
    return ids


def row_counts(connection: Connection) -> dict[str, int]:
    names = set(inspect(connection).get_table_names()) - {"alembic_version"}
    return {name: connection.scalar(text(f'SELECT count(*) FROM "{name}"')) for name in sorted(names)}


NEW_TABLES = {
    "auth_events",
    "auth_tokens",
    "email_outbox",
    "mail_suppressions",
    "pending_emails",
    "pending_signups",
    "user_identities",
    "username_tombstones",
}


@both
async def test_the_migration_adds_the_sign_up_data_and_keeps_every_row(
    migration_logs: pytest.LogCaptureFixture,
) -> None:
    ids = await at_0003()
    before = await run(row_counts)

    await db.init_db()

    after = await run(row_counts)
    assert set(after) - set(before) == NEW_TABLES
    assert {name: after[name] for name in before} == before
    assert {name: after[name] for name in NEW_TABLES} == {
        **dict.fromkeys(NEW_TABLES, 0),
        "username_tombstones": 2,
    }
    async with SessionLocal() as session:
        users = {user.username: user for user in await session.scalars(select(User))}
        tombstones = list(await session.scalars(select(UsernameTombstone).order_by(UsernameTombstone.username)))
        profiles = {row.user_id: row.name for row in await session.scalars(select(UserBillingProfile))}
        methods = list(await session.scalars(select(LoginEvent.method)))
    assert {name: user.signup_source for name, user in users.items()} == {
        "admin": "env",
        "bikash": "admin",
        "smoke-inactive": "admin",
    }
    assert {
        (user.email, user.email_canonical, user.email_verified_at, user.terms_version, user.terms_accepted_at)
        for user in users.values()
    } == {(None, None, None, None, None)}, "no email is invented"
    assert {user.password_hash for user in users.values()} == {"$argon2id$v=19$m=65536,t=3,p=4$legacy"}
    assert [(row.username, row.deleted_at is not None) for row in tombstones] == [
        ("smoke-deleted", True),
        ("smoke-gone-payment", True),
    ], "names a current user has are skipped"
    assert profiles == {ids[name]: f"{name} name" for name in ("admin", "bikash", "smoke-inactive")}
    assert methods == ["password"]
    columns = await run(lambda connection: inspect(connection).get_columns("user_billing_profiles"))
    assert [column["name"] for column in columns] == ["user_id", "name", "updated_at"]
    assert "Revision 0004: 2 username(s) of deleted users tombstoned" in migration_logs.text
    assert "Revision 0004: 1 billing profile email(s) dropped" in migration_logs.text
    assert ADDRESS not in migration_logs.text and "owner.private" not in migration_logs.text


@both
async def test_the_migrated_schema_matches_the_models() -> None:
    await at_0003()

    await db.init_db()

    differences = await run(
        lambda connection: compare_metadata(
            MigrationContext.configure(connection, opts=db.COMPARE_OPTIONS), Base.metadata
        )
    )
    assert differences == []


@both
async def test_passwords_may_be_missing_and_a_canonical_email_belongs_to_one_user() -> None:
    await at_0003()
    await db.init_db()
    now = utcnow()

    async with SessionLocal() as session:
        session.add_all(
            [
                User(username="smoke-google", password_hash=None, signup_source="google"),
                User(username="smoke-none-1"),
                User(username="smoke-none-2"),  # several accounts without an email
                User(username="smoke-a", email="A@example.com", email_canonical="a@example.com", email_verified_at=now),
            ]
        )
        await session.commit()
    async with SessionLocal() as session:
        session.add(User(username="smoke-b", email="a@example.com", email_canonical="a@example.com"))
        with pytest.raises(IntegrityError):
            await session.commit()
    async with SessionLocal() as session:
        google = await session.scalar(select(User).where(User.username == "smoke-google"))
        assert google is not None and (google.password_hash, google.signup_source) == (None, "google")
        member = await session.scalar(select(User).where(User.username == "smoke-none-1"))
        assert member is not None and member.signup_source == "admin"


@both
async def test_the_first_start_after_it_keeps_the_admin_and_its_password() -> None:
    ids = await at_0003()
    await db.init_db()
    async with db.engine.begin() as connection:
        await connection.execute(text("UPDATE users SET password_hash = :hash WHERE username = 'admin'"), {"hash": "x"})

    async with SessionLocal() as session:
        await bootstrap(session)

    async with SessionLocal() as session:
        admin = await session.get(User, ids["admin"])
        member = await session.get(User, ids["bikash"])
        assert admin is not None and member is not None
        assert (admin.is_admin, admin.signup_source, admin.email) == (True, "env", None)
        assert (member.is_admin, member.signup_source, member.password_hash) == (
            False,
            "admin",
            "$argon2id$v=19$m=65536,t=3,p=4$legacy",
        )


async def test_the_migration_is_one_revision_after_plans_and_billing() -> None:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory(str(db.MIGRATIONS_DIR)).get_revision("0004")

    assert script is not None and script.down_revision == "0003"
    with pytest.raises(NotImplementedError, match="restore the backup"):
        script.module.downgrade()
