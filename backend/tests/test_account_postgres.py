"""The account queries on Postgres (docs/briefs/ui-shell-settings.md, section 4): the sign-in history's prune, the
usage report's UTC days, the first-insert race of preferences across two engines (as two backend processes), and the
export's reads. The other tests run on SQLite. Run against a throwaway Postgres:
TEST_POSTGRES_URL=postgresql+asyncpg://... pytest -m postgres"""

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db import Base, make_engine
from app.models import Conversation, Document, LLMModel, LoginEvent, Message, UsageEvent, User, UserPreference
from app.schemas_account import PreferencesUpdate
from app.services import account, export

pytestmark = pytest.mark.postgres


@pytest.fixture
async def engines() -> AsyncIterator[list[AsyncEngine]]:
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL isn't set")
    found = [make_engine(url), make_engine(url)]
    async with found[0].begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield found
    finally:
        for engine in found:
            await engine.dispose()


async def new_user(engine: AsyncEngine, username: str = "smoke-pg") -> int:
    async with AsyncSession(engine, expire_on_commit=False) as db:
        user = User(username=username, password_hash="x", is_admin=False, restrict_models=False)
        db.add(user)
        await db.commit()
        return user.id


async def test_the_sign_in_history_keeps_the_newest_20(engines: list[AsyncEngine]) -> None:
    user_id = await new_user(engines[0])
    ids = []
    for _ in range(25):
        async with AsyncSession(engines[0], expire_on_commit=False) as db:
            user = await db.get(User, user_id)
            assert user is not None
            event = await account.record_login(db, user, "81.2.69.142", "curl/8.7.1")
            await db.commit()
            ids.append(event.id)

    async with AsyncSession(engines[0], expire_on_commit=False) as db:
        kept = list(
            await db.scalars(select(LoginEvent.id).where(LoginEvent.user_id == user_id).order_by(LoginEvent.id))
        )
        user = await db.get(User, user_id)
        assert user is not None
        listed = await account.recent_logins(db, user, ids[-1])

    assert kept == ids[-20:]
    assert [entry.id for entry in listed] == list(reversed(ids[-20:])) and listed[0].current


async def test_usage_days_are_utc_days(engines: list[AsyncEngine], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(account, "current_time", lambda: datetime(2026, 10, 18, 12, tzinfo=timezone.utc))
    user_id = await new_user(engines[0])
    nepal = timezone(timedelta(hours=5, minutes=45))
    async with AsyncSession(engines[0], expire_on_commit=False) as db:
        db.add(LLMModel(display_name="Haiku", litellm_model="anthropic/claude-haiku-5-5", kind="chat"))
        db.add_all(
            [
                UsageEvent(
                    user_id=user_id,
                    model_name="anthropic/claude-haiku-5-5",
                    kind="chat",
                    total_tokens=10,
                    prompt_tokens=6,
                    completion_tokens=4,
                    cost_usd=0.5,
                    created_at=datetime(2026, 10, 6, 0, 30, tzinfo=nepal),  # 18:45 UTC on the 5th
                ),
                UsageEvent(
                    user_id=user_id, model_name="x", kind="image", created_at=datetime(2026, 10, 18, 1, tzinfo=nepal)
                ),
            ]
        )
        await db.commit()

    async with AsyncSession(engines[0], expire_on_commit=False) as db:
        user = await db.get(User, user_id)
        assert user is not None
        usage = await account.my_usage(db, user)

    days = {day.date: day for day in usage.daily}
    assert len(days) == 18 and days["2026-10-05"].chat_tokens == 10 and days["2026-10-06"].chat_tokens == 0
    assert days["2026-10-17"].images == 1  # 01:00 in Kathmandu on the 18th is the 17th in UTC
    assert [(model.model_name, model.tokens) for model in usage.by_model] == [("Haiku", 10), ("x", 0)]


async def test_concurrent_first_preference_saves_both_land(engines: list[AsyncEngine]) -> None:
    user_id = await new_user(engines[0])
    for attempt in range(5):
        async with AsyncSession(engines[0], expire_on_commit=False) as db:
            row = await db.get(UserPreference, user_id)
            if row is not None:
                await db.delete(row)
                await db.commit()

        async def save(engine: AsyncEngine, update: PreferencesUpdate) -> None:
            async with AsyncSession(engine, expire_on_commit=False) as db:
                user = await db.get(User, user_id)
                assert user is not None
                await account.update_preferences(db, user, update)

        await asyncio.gather(
            save(engines[0], PreferencesUpdate(theme="dark")), save(engines[1], PreferencesUpdate(accent="sage"))
        )
        async with AsyncSession(engines[0], expire_on_commit=False) as db:
            row = await db.get(UserPreference, user_id)
            assert row is not None and row.data == {"theme": "dark", "accent": "sage"}, (attempt, row and row.data)


async def test_the_export_reads(engines: list[AsyncEngine]) -> None:
    user_id = await new_user(engines[0])
    other_id = await new_user(engines[0], "smoke-pg-other")
    async with AsyncSession(engines[0], expire_on_commit=False) as db:
        conversation = Conversation(user_id=user_id, title="Chat")
        db.add_all(
            [
                conversation,
                Conversation(user_id=other_id, title="Theirs"),
                Document(owner_id=user_id, scope="private", filename="a.txt", stored_name="a", sha256="0" * 64),
                Document(owner_id=other_id, scope="shared", filename="b.txt", stored_name="b", sha256="1" * 64),
            ]
        )
        await db.flush()
        db.add_all(
            [
                Message(conversation_id=conversation.id, role="user", content="Q"),
                Message(conversation_id=conversation.id, role="assistant", content="A"),
            ]
        )
        await db.commit()
        conversation_id = conversation.id

    async with AsyncSession(engines[0], expire_on_commit=False) as db:
        user = await db.get(User, user_id)
        assert user is not None
        records = await export._conversation_page(db, user, [conversation_id, conversation_id + 1])
        documents = await export._documents(db, user)
        profile = await export._account(db, user, datetime.now(timezone.utc))

    assert list(records) == [conversation_id] and [m["content"] for m in records[conversation_id]["messages"]] == [
        "Q",
        "A",
    ]
    assert [document["filename"] for document in documents] == ["a.txt"]
    assert profile["username"] == "smoke-pg" and profile["plugin_connections"] == []
