"""Sign-up, resend and "forgot password" reveal nothing about an address (docs/briefs/signup-auth.md S6, 8.2): for a new
address, an active account's, a Google-only account's, a turned-off account's and a pending sign-up's, the request
answers the same status, headers (but the cookie's random value) and bytes, never reads `users`, and adds one job of
the same class, whatever name it gives; only the worker's result differs."""

import re
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
from conftest import ApiClient, drain_mail, open_signup, signup
from fake_mail import FakeMail
from fake_turnstile import FakeTurnstile, turnstile_token
from signup_support import give_email, jobs, set_user
from sqlalchemy import event

from app import db
from app.models import UserIdentity
from app.services.auth import throttle
from app.services.auth.throttle import Window

CreateUser = Callable[..., Awaitable[ApiClient]]
NewClient = Callable[[], ApiClient]

NEW, ACTIVE, GOOGLE_ONLY, OFF, PENDING = (
    "new@example.com",
    "active@example.com",
    "google-only@example.com",
    "off@example.com",
    "pending@example.com",
)
ADDRESSES = (NEW, ACTIVE, GOOGLE_ONLY, OFF, PENDING)
_USERS = re.compile(r"\busers\b", re.IGNORECASE)


@pytest.fixture
async def accounts(
    create_user: CreateUser,
    new_client: NewClient,
    fake_mail: FakeMail,
    fake_turnstile: FakeTurnstile,
    monkeypatch: pytest.MonkeyPatch,
) -> FakeMail:
    """The five kinds of address, sign-up open, the silent per-address limit lifted (the pending address signed up a
    moment ago) and the outbox emptied."""
    monkeypatch.setattr(throttle, "ADDRESS_WINDOWS", (Window(60, 100),))
    await open_signup()
    for name, address, fields in (
        ("active", ACTIVE, {}),
        ("googleonly", GOOGLE_ONLY, {}),
        ("off", OFF, {"is_active": False}),
    ):
        user = await create_user(name, login=False, **fields)
        assert user.user_id is not None
        await give_email(user.user_id, address)
        if address == GOOGLE_ONLY:
            await set_user(user.user_id, password_hash=None)
            async with db.SessionLocal() as session:
                session.add(
                    UserIdentity(
                        user_id=user.user_id,
                        provider="google",
                        issuer="https://accounts.google.com",
                        subject="100000000000000000042",
                        email_at_link=GOOGLE_ONLY,
                        email_kind="workspace",
                    )
                )
                await session.commit()
    assert (await signup(new_client(), PENDING)).status_code == 202
    await drain_mail()
    fake_mail.sent.clear()
    return fake_mail


@contextmanager
def statements() -> Iterator[list[str]]:
    """The SQL statements run while the block runs."""
    seen: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        seen.append(statement)

    event.listen(db.engine.sync_engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(db.engine.sync_engine, "before_cursor_execute", record)


def comparable(response: httpx.Response) -> tuple[int, list[tuple[str, str]], bytes]:
    """Status, headers (the sign-up cookie's random value blanked) and body."""
    headers = []
    for name, value in response.headers.multi_items():
        if name == "set-cookie":
            value = re.sub(r"^(rag_signup=)[^;]*", r"\1…", value)
        headers.append((name, value))
    return response.status_code, sorted(headers), response.content


async def answers(new_client: NewClient, send: Callable[[ApiClient, str], Awaitable[httpx.Response]]) -> list[Any]:
    found = []
    for address in ADDRESSES:
        before = len(await jobs())
        with statements() as seen:
            response = await send(new_client(), address)
        assert response.status_code == 202, (address, response.text)
        assert seen and not [statement for statement in seen if _USERS.search(statement)], address
        added = (await jobs())[before:]
        assert len(added) == 1, address
        found.append((comparable(response), added[0].kind, added[0].mail_class))
    return found


async def test_sign_up_answers_every_address_the_same(new_client: NewClient, accounts: FakeMail) -> None:
    found = await answers(new_client, lambda client, address: signup(client, address, name=f"Person {address[:4]}"))

    assert all(entry == found[0] for entry in found), found
    assert found[0][1:] == ("signup", "public")
    assert await drain_mail() == len(ADDRESSES)
    links = {address: [message for message in accounts.to(address)] for address in ADDRESSES}
    assert all(len(messages) == 1 for messages in links.values()), links
    assert "/verify-email#t=" in accounts.last_link(NEW)
    assert "/verify-email#t=" in accounts.last_link(PENDING)
    for address in (ACTIVE, GOOGLE_ONLY, OFF):  # "you already have an account": no link to spend
        with pytest.raises(AssertionError):
            accounts.last_link(address)


async def test_resend_answers_every_address_the_same(new_client: NewClient, accounts: FakeMail) -> None:
    async def resend(client: ApiClient, address: str) -> httpx.Response:
        body = {"email": address, "turnstile_token": turnstile_token("resend")}
        return await client.post("/api/auth/signup/resend", json=body)

    found = await answers(new_client, resend)

    assert all(entry == found[0] for entry in found), found
    await drain_mail()
    assert accounts.to(NEW) == []  # nothing pending: nothing to send
    assert "/verify-email#t=" in accounts.last_link(PENDING)


async def test_forgot_answers_every_address_the_same(new_client: NewClient, accounts: FakeMail) -> None:
    async def forgot(client: ApiClient, address: str) -> httpx.Response:
        body = {"email": address, "turnstile_token": turnstile_token("reset")}
        return await client.post("/api/auth/password/forgot", json=body)

    found = await answers(new_client, forgot)

    assert all(entry == found[0] for entry in found), found
    assert found[0][1:] == ("password_reset", "reset")
    await drain_mail()
    assert accounts.to(NEW) == [] and accounts.to(OFF) == []
    assert "/reset-password#t=" in accounts.last_link(ACTIVE)
    assert "/reset-password#t=" in accounts.last_link(GOOGLE_ONLY)  # sets a first password
    assert "/verify-email#t=" in accounts.last_link(PENDING)  # "finish creating your account"
