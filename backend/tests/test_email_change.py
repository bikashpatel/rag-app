"""Adding, changing, confirming and undoing an email (docs/briefs/signup-auth.md 4.8): re-authentication by password
or a Google step-up, the limits, the pending email and its resend and cancel, the confirmation only by the signed-in
owner of the account that asked (SEC-01's chain B), a suppressed address confirmed again, the notice and its undo (for
its 7 days even when the notice went late), and a chain of changes keeping the first address's undo."""

from collections.abc import Awaitable, Callable
from datetime import timedelta

import httpx
import pytest
from conftest import USER_PASSWORD, ApiClient, drain_mail
from fake_mail import FakeMail
from signup_support import (
    confirm_token,
    event_types,
    give_email,
    jobs,
    live_tokens,
    pending_email,
    pending_signups,
    reset_token,
    set_user,
    undo_token,
    user_row,
)
from sqlalchemy import select, update

from app import db
from app.models import AuthEvent, AuthToken, BillingPayment, MailSuppression, PendingSignup, utcnow
from app.redis_client import get_redis
from app.services.auth import emails, stepup
from app.services.mail import outbox, sweeps

CreateUser = Callable[..., Awaitable[ApiClient]]
NewClient = Callable[[], ApiClient]

OLD, NEW = "alice@old.example", "alice@new.example"
INVALID = {"detail": "This link has expired or was already used.", "code": "link_invalid"}


@pytest.fixture
async def alice(create_user: CreateUser, mailer: None) -> ApiClient:
    """A user with a password and the verified email OLD; the mailer configured."""
    client = await create_user("alice")
    assert client.user_id is not None
    await give_email(client.user_id, OLD)
    return client


async def change(client: ApiClient, address: str = NEW, password: str | None = USER_PASSWORD) -> httpx.Response:
    body = {"email": address} if password is None else {"email": address, "password": password}
    return await client.post("/api/me/email", json=body)


async def confirm(client: httpx.AsyncClient, token: str) -> httpx.Response:
    return await client.post("/api/auth/email/verify", json={"token": token})


async def changed(client: ApiClient, address: str) -> None:
    """The change asked for and confirmed by the account's own session."""
    assert client.user_id is not None
    assert (await change(client, address)).status_code == 202
    response = await confirm(client, await confirm_token(client.user_id))
    assert response.status_code == 200, response.text


async def session_id(client: ApiClient) -> int:
    account = (await client.get("/api/me/account")).json()
    return next(entry["id"] for entry in account["recent_logins"] if entry["current"])


# ---------------- The request ----------------


async def test_a_change_queues_a_confirmation_to_the_new_address(alice: ApiClient) -> None:
    response = await change(alice, "Alice@New.Example")

    assert (response.status_code, response.json()) == (202, {"status": "accepted"})
    pending = await pending_email(alice.user_id)  # type: ignore[arg-type]
    assert pending is not None
    assert (pending.address, pending.canonical, pending.source, pending.sent_at) == (
        "Alice@new.example",
        NEW,
        "user",
        None,
    )
    [job] = await jobs()
    assert (job.kind, job.mail_class, job.to_address, job.user_id, job.pending_email_id) == (
        "email_confirm",
        "public",
        "Alice@new.example",
        alice.user_id,
        pending.id,
    )
    assert "email.pending" in await event_types(alice.user_id)
    assert (await user_row(alice.user_id)).email == OLD  # nothing changes before the confirmation
    account = (await alice.get("/api/me/account")).json()
    assert account["pending_email"]["address"] == "Alice@new.example"


@pytest.mark.parametrize(
    ("password", "code"), [(None, "wrong_password"), ("not-the-password", "wrong_password")], ids=["missing", "wrong"]
)
async def test_an_account_with_a_password_must_send_it(password: str | None, code: str, alice: ApiClient) -> None:
    response = await change(alice, password=password)

    assert (response.status_code, response.json()) == (400, {"detail": "Your password is incorrect.", "code": code})
    assert await pending_email(alice.user_id) is None  # type: ignore[arg-type]


async def test_an_account_without_a_password_needs_this_sessions_step_up(alice: ApiClient) -> None:
    assert alice.user_id is not None
    await set_user(alice.user_id, password_hash=None)

    without = await change(alice, password=None)
    await stepup.grant(alice.user_id, await session_id(alice) + 1000)  # another session's grant
    elsewhere = await change(alice, password=None)
    await stepup.grant(alice.user_id, await session_id(alice))
    granted = await change(alice, password=None)
    spent = await change(alice, "alice@third.example", password=None)

    for response in (without, elsewhere, spent):
        assert response.status_code == 403
        assert response.json() == {"detail": "Confirm it's you with Google first.", "code": "reauth_required"}
    assert granted.status_code == 202


async def test_the_env_admin_has_no_email(admin_client: ApiClient, mailer: None) -> None:
    for response in (
        await admin_client.post("/api/me/email", json={"email": NEW, "password": "x"}),
        await admin_client.post("/api/me/email/resend"),
        await admin_client.delete("/api/me/email/pending"),
    ):
        assert (response.status_code, response.json()) == (400, {"detail": "The admin account is managed in .env"})


@pytest.mark.parametrize(
    ("address", "detail"),
    [("ALICE@old.example", "That's already your email."), ("not an address", "Enter a valid email address.")],
)
async def test_the_same_or_an_invalid_address_gets_422(address: str, detail: str, alice: ApiClient) -> None:
    response = await change(alice, address)

    assert (response.status_code, response.json()) == (422, {"detail": detail})


async def test_an_unpaid_account_gets_three_changes_a_day(alice: ApiClient) -> None:
    for number in range(3):
        assert (await change(alice, f"alice{number}@new.example")).status_code == 202

    response = await change(alice, "alice9@new.example")

    assert (response.status_code, response.json()["code"]) == (429, "too_many_attempts")


async def test_a_paid_account_gets_five_and_its_mail_is_in_the_account_class(alice: ApiClient) -> None:
    async with db.SessionLocal() as session:
        session.add(
            BillingPayment(
                user_id=alice.user_id,
                username="alice",
                status="captured",
                amount_paise=100,
                currency="INR",
                mode="live",
                applied_at=utcnow(),
            )
        )
        await session.commit()
    for number in range(5):
        assert (await change(alice, f"alice{number}@new.example")).status_code == 202

    assert (await change(alice, "alice9@new.example")).status_code == 429
    assert {job.mail_class for job in await jobs()} == {"account"}


async def test_without_a_mailer_a_change_answers_mail_unavailable(create_user: CreateUser) -> None:
    alice = await create_user("alice")

    response = await change(alice)

    assert (response.status_code, response.json()["code"]) == (503, "mail_unavailable")


async def test_a_new_request_replaces_the_pending_email_and_its_links(alice: ApiClient) -> None:
    assert alice.user_id is not None
    assert (await change(alice)).status_code == 202
    first = await confirm_token(alice.user_id)
    reset = await reset_token(alice.user_id)

    assert (await change(alice, "alice@other.example")).status_code == 202

    assert (await pending_email(alice.user_id)).address == "alice@other.example"  # type: ignore[union-attr]
    assert (await confirm(alice, first)).json() == INVALID
    assert await live_tokens(alice.user_id, "reset") == [] and reset


async def test_resend_and_cancel(alice: ApiClient) -> None:
    assert alice.user_id is not None
    nothing = await alice.post("/api/me/email/resend")
    await change(alice)
    token = await confirm_token(alice.user_id)
    await forget_address_limits()

    resent = await alice.post("/api/me/email/resend")
    cancelled = await alice.delete("/api/me/email/pending")

    assert (nothing.status_code, nothing.json()) == (409, {"detail": "No email is waiting for confirmation."})
    assert resent.status_code == 202
    assert [job.kind for job in await jobs()] == ["email_confirm", "email_confirm"]
    assert cancelled.status_code == 204
    assert await pending_email(alice.user_id) is None
    assert (await confirm(alice, token)).json() == INVALID
    assert (await alice.delete("/api/me/email/pending")).status_code == 204


async def forget_address_limits() -> None:
    """Lifts the silent per-address minute between two confirmation mails."""
    redis = get_redis()
    for key in await redis.keys("auth:addr:confirm:*"):
        await redis.delete(key)


# ---------------- Confirming ----------------


async def test_only_the_asking_accounts_session_confirms(
    alice: ApiClient, create_user: CreateUser, client: ApiClient
) -> None:
    """SEC-01's chain B: the holder of a typo'd address can't confirm it."""
    assert alice.user_id is not None
    await change(alice, "alice@typo.example")
    token = await confirm_token(alice.user_id)
    holder = await create_user("holder")

    checked_out = await client.post("/api/auth/email/check", json={"token": token})
    checked_in = await alice.post("/api/auth/email/check", json={"token": token})
    signed_out = await confirm(client, token)
    other_account = await confirm(holder, token)

    assert checked_out.json() == {"email": "alice@typo.example", "account": "a***@old.example", "signed_in": False}
    assert checked_in.json()["signed_in"] is True
    assert (signed_out.status_code, signed_out.json()) == (401, {"detail": "Not authenticated"})
    assert other_account.status_code == 403
    assert other_account.json() == {
        "detail": "Sign in to the account that asked for this change.",
        "code": "wrong_account",
    }
    assert (await user_row(alice.user_id)).email == OLD
    assert (await confirm(alice, token)).status_code == 200  # still good for its owner


async def test_a_confirmation_sets_the_verified_email_and_tells_the_old_address(alice: ApiClient) -> None:
    assert alice.user_id is not None
    await change(alice)
    token = await confirm_token(alice.user_id)
    reset = await reset_token(alice.user_id)
    async with db.SessionLocal() as session:
        session.add(MailSuppression(recipient_sha256=emails.digest(NEW), reason="complaint", source="webhook"))
        now = utcnow()
        session.add(
            PendingSignup(
                address=NEW,
                canonical=NEW,
                password_hash="x",
                terms_version="t",
                terms_accepted_at=now,
                created_at=now,
                expires_at=now + timedelta(days=7),
            )
        )
        await session.commit()

    response = await confirm(alice, token)

    assert (response.status_code, response.json()) == (200, {"email": NEW})
    user = await user_row(alice.user_id)
    assert (user.email, user.email_canonical) == (NEW, NEW) and user.email_verified_at is not None
    assert await pending_email(alice.user_id) is None and await pending_signups(NEW) == []
    assert await live_tokens(alice.user_id, "reset") == [] and reset
    async with db.SessionLocal() as session:
        assert await session.get(MailSuppression, emails.digest(NEW)) is None
    assert "email.changed" in await event_types(alice.user_id)
    notices = [(job.kind, job.to_address) for job in await jobs() if job.kind == "email_changed"]
    assert notices == [("email_changed", OLD)]
    assert (await alice.get("/api/auth/me")).status_code == 200  # the session is kept
    signed_in = await alice.post("/api/auth/login", json={"identifier": NEW, "password": USER_PASSWORD})
    assert signed_in.status_code == 200


async def test_a_complained_address_can_be_confirmed_again(alice: ApiClient, fake_mail: FakeMail) -> None:
    assert alice.user_id is not None
    async with db.SessionLocal() as session:  # "Report spam" on a plan reminder
        await outbox.suppress(session, emails.digest(OLD), reason="complaint", source="webhook")
        await session.commit()
    await changed(alice, NEW)  # the brief's way out (4.8, 6.6): away and back
    await drain_mail()

    assert (await change(alice, OLD)).status_code == 202
    await drain_mail()
    confirmation = [job for job in await jobs() if job.kind == "email_confirm"][-1]
    assert (confirmation.status, confirmation.last_error) == ("sent", None)
    assert (await confirm(alice, fake_mail.last_token(OLD))).status_code == 200

    async with db.SessionLocal() as session:
        assert await session.get(MailSuppression, emails.digest(OLD)) is None
    assert (await alice.post("/api/me/email/resend")).status_code == 409  # nothing waits: OLD is the email again


async def test_a_first_email_is_confirmed_without_a_notice(create_user: CreateUser, mailer: None) -> None:
    bob = await create_user("bob")
    assert bob.user_id is not None
    await change(bob, "bob@example.com")

    assert (await confirm(bob, await confirm_token(bob.user_id))).status_code == 200

    assert "email.confirmed" in await event_types(bob.user_id)
    assert [job.kind for job in await jobs()] == ["email_confirm"]


async def test_an_address_taken_meanwhile_is_refused_at_confirmation(alice: ApiClient, create_user: CreateUser) -> None:
    assert alice.user_id is not None
    await change(alice)
    token = await confirm_token(alice.user_id)
    bob = await create_user("bob")
    assert bob.user_id is not None
    await give_email(bob.user_id, NEW)

    response = await confirm(alice, token)

    assert response.status_code == 409 and response.json()["code"] == "email_taken"
    assert await pending_email(alice.user_id) is None
    assert (await user_row(alice.user_id)).email == OLD


async def test_a_confirmation_link_works_once_and_for_a_day(alice: ApiClient) -> None:
    assert alice.user_id is not None
    await change(alice)
    used = await confirm_token(alice.user_id)
    assert (await confirm(alice, used)).status_code == 200
    await change(alice, "alice@third.example")
    expired = await confirm_token(alice.user_id)
    async with db.SessionLocal() as session:
        await session.execute(update(AuthToken).values(expires_at=utcnow() - timedelta(seconds=1)))
        await session.commit()

    for token in (used, expired):
        assert (await confirm(alice, token)).json() == INVALID
        assert (await alice.post("/api/auth/email/check", json={"token": token})).json() == INVALID


# ---------------- Undo ----------------


async def test_undo_puts_the_old_address_back_and_ends_every_session(
    alice: ApiClient, client: ApiClient, new_client: NewClient
) -> None:
    assert alice.user_id is not None
    await changed(alice, NEW)
    token = await undo_token(alice.user_id, OLD)
    stale_reset = await reset_token(alice.user_id)

    check = await client.post("/api/auth/email/undo-check", json={"token": token})
    response = await client.post("/api/auth/email/undo", json={"token": token})

    assert check.json() == {"old_email": OLD, "new_email": "a***@new.example"}
    assert response.status_code == 204
    user = await user_row(alice.user_id)
    assert (user.email, user.email_canonical, user.password_hash) == (OLD, OLD, None)
    assert (await alice.get("/api/auth/me")).status_code == 401
    assert await live_tokens(alice.user_id, "reset") == [] and stale_reset
    assert await live_tokens(alice.user_id, "email_undo") == []
    assert "email.change_undone" in await event_types(alice.user_id)
    queued = [(job.kind, job.to_address, job.user_id) for job in await jobs()]
    assert ("password_reset", OLD, alice.user_id) in queued and ("email_undone", NEW, alice.user_id) in queued
    assert (await client.post("/api/auth/email/undo", json={"token": token})).json() == INVALID  # once
    signed_in = await new_client().post("/api/auth/login", json={"identifier": OLD, "password": USER_PASSWORD})
    assert signed_in.status_code == 401  # the password is gone; the reset mail sets a new one


async def test_undo_refuses_an_address_another_account_holds_now(alice: ApiClient, create_user: CreateUser) -> None:
    assert alice.user_id is not None
    await changed(alice, NEW)
    token = await undo_token(alice.user_id, OLD)
    bob = await create_user("bob")
    assert bob.user_id is not None
    await give_email(bob.user_id, OLD)

    response = await alice.post("/api/auth/email/undo", json={"token": token})

    assert response.status_code == 409
    assert response.json() == {
        "detail": "That address is now used by another account. Contact us.",
        "code": "email_taken",
    }
    assert (await user_row(alice.user_id)).email == NEW


async def test_an_undo_link_lasts_seven_days(alice: ApiClient, client: ApiClient) -> None:
    assert alice.user_id is not None
    await changed(alice, NEW)
    token = await undo_token(alice.user_id, OLD)
    async with db.SessionLocal() as session:
        await session.execute(update(AuthToken).values(expires_at=utcnow() - timedelta(seconds=1)))
        await session.commit()

    assert (await client.post("/api/auth/email/undo-check", json={"token": token})).json() == INVALID


async def test_an_undo_link_from_a_late_notice_lasts_its_seven_days(
    alice: ApiClient, client: ApiClient, fake_mail: FakeMail
) -> None:
    assert alice.user_id is not None
    await changed(alice, NEW)
    await drain_mail()  # the notice, as it goes out 3 days after the change (the day's cap, a provider outage)
    token = fake_mail.last_token(OLD)
    late, then = timedelta(days=3), timedelta(days=6, hours=12)  # clicked with half a day of the link left
    async with db.SessionLocal() as session:
        await session.execute(
            update(AuthEvent).where(AuthEvent.type == "email.changed").values(created_at=utcnow() - late - then)
        )
        undo = await session.scalar(select(AuthToken).where(AuthToken.purpose == "email_undo"))
        assert undo is not None
        undo.created_at, undo.expires_at = undo.created_at - then, undo.expires_at - then
        await session.commit()

    await sweeps.run()  # the hourly clean-up keeps the address the link puts back

    assert (await client.post("/api/auth/email/undo-check", json={"token": token})).status_code == 200
    assert (await client.post("/api/auth/email/undo", json={"token": token})).status_code == 204
    assert (await user_row(alice.user_id)).email == OLD


async def test_an_undo_link_for_an_address_never_replaced_is_invalid(alice: ApiClient, client: ApiClient) -> None:
    assert alice.user_id is not None
    await changed(alice, NEW)
    token = await undo_token(alice.user_id, "someone@else.example")

    assert (await client.post("/api/auth/email/undo", json={"token": token})).json() == INVALID


async def test_an_intruders_further_change_or_a_reset_doesnt_cancel_the_first_undo(
    alice: ApiClient, client: ApiClient, fake_mail: FakeMail
) -> None:
    assert alice.user_id is not None
    await changed(alice, NEW)  # A -> B
    await drain_mail()
    first_notice = fake_mail.last(OLD)
    await changed(alice, "alice@third.example")  # B -> C
    await drain_mail()
    assert (
        await client.post(
            "/api/auth/password/reset",
            json={"token": await reset_token(alice.user_id), "new_password": "an-intruders-password"},
        )
    ).status_code == 204

    undo_link = fake_mail.last_link(OLD)
    assert "/undo-email#t=" in undo_link and fake_mail.last(OLD) is first_notice
    with pytest.raises(AssertionError):
        fake_mail.last_link(NEW)  # B's notice has no undo link
    response = await client.post("/api/auth/email/undo", json={"token": fake_mail.last_token(OLD)})

    assert response.status_code == 204
    assert (await user_row(alice.user_id)).email == OLD
    assert "email_undone" in [job.kind for job in await jobs()]
