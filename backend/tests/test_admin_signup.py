"""The admin's sign-up controls (docs/briefs/signup-auth.md S27, 13.9): the settings and their audit event, what is
effectively available, every SignupStatus field and warning, the counts, the test mail; and in Admin → Users the
password policy, retired usernames, the tombstone a deletion writes and the new AdminUser fields."""

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import timedelta

import httpx
import pytest
from conftest import ADMIN_USERNAME, USER_PASSWORD, ApiClient, OutsideHosts
from fake_hibp import FakeHibp
from fake_turnstile import FakeTurnstile
from fastapi import FastAPI
from google_oidc import GoogleOidc
from signup_support import give_email, jobs, live_tokens, reset_token, set_user
from sqlalchemy import select

from app import db
from app.config import settings
from app.deps import session_cookie_name
from app.models import AuthEvent, BillingPayment, PendingEmail, UserIdentity, UsernameTombstone, utcnow
from app.redis_client import get_redis

CreateUser = Callable[..., Awaitable[ApiClient]]
ClientAt = Callable[..., httpx.AsyncClient]

PUBLIC = "https://asknuvexusdoc.example"
CLOSED = {"open": False, "email": True, "google": True}


@pytest.fixture
async def admin_at(app: FastAPI, admin_client: ApiClient) -> AsyncIterator[ClientAt]:
    """The admin's session from a given address, asking for a given host."""
    clients: list[httpx.AsyncClient] = []

    def factory(ip: str, host: str = "testserver") -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=app, client=(ip, 50000))
        token = admin_client.cookies.get(session_cookie_name())
        headers = {"Authorization": f"Bearer {token}"}
        client = httpx.AsyncClient(transport=transport, base_url=f"http://{host}", headers=headers)
        clients.append(client)
        return client

    yield factory
    for client in clients:
        await client.aclose()


async def status_of(client: httpx.AsyncClient) -> dict:
    response = await client.get("/api/admin/signup-settings")
    assert response.status_code == 200, response.text
    return response.json()


# ---------------- Settings ----------------


async def test_sign_up_is_closed_by_default_and_nothing_is_configured(admin_client: ApiClient) -> None:
    status = await status_of(admin_client)

    assert status["settings"] == CLOSED
    assert status["effective"] == {"open": False, "email": False, "google": False}
    assert {key: status[key] for key in ("mail_configured", "turnstile_configured", "google_configured")} == {
        "mail_configured": False,
        "turnstile_configured": False,
        "google_configured": False,
    }
    assert (status["mail_webhook_configured"], status["payments_live"]) == (False, False)
    assert (status["mail_sent_today"], status["mail_queued"], status["mail_daily_limit"]) == (0, 0, 80)
    assert (status["public_mail_left"], status["public_paused"], status["mail_last_error"]) == (0, False, None)
    assert (status["signups_today"], status["signups_7d"], status["paid_7d"]) == (0, 0, 0)
    assert set(status["warnings"]) == {"payments_not_live", "admin_password_weak"}


async def test_opening_sign_up_records_who_changed_what(admin_client: ApiClient) -> None:
    response = await admin_client.put("/api/admin/signup-settings", json={"open": True, "google": False})

    assert response.status_code == 200
    assert response.json()["settings"] == {"open": True, "email": True, "google": False}
    async with db.SessionLocal() as session:
        [row] = await session.scalars(select(AuthEvent).where(AuthEvent.type == "signup.settings"))
    assert (row.actor, row.user_id) == (f"admin:{ADMIN_USERNAME}", None)
    assert row.detail == {"before": CLOSED, "after": {"open": True, "email": True, "google": False}}
    assert (await admin_client.put("/api/admin/signup-settings", json={})).json()["settings"] == CLOSED


@pytest.mark.parametrize("body", [{"open": "yes"}, {"open": 1}, {"open": True, "domains": []}, {"open": None}])
async def test_invalid_settings_get_422(body: dict, admin_client: ApiClient) -> None:
    assert (await admin_client.put("/api/admin/signup-settings", json=body)).status_code == 422


async def test_effective_availability_needs_every_prerequisite(
    admin_client: ApiClient,
    mailer: None,
    fake_turnstile: FakeTurnstile,
    google_oidc: GoogleOidc,
    client: ApiClient,
) -> None:
    await admin_client.put("/api/admin/signup-settings", json={"open": True})

    status = await status_of(admin_client)
    config = (await client.get("/api/auth/config")).json()

    assert status["effective"] == config["signup"] == {"open": True, "email": True, "google": True}
    assert (status["mail_configured"], status["turnstile_configured"], status["google_configured"]) == (True,) * 3
    assert status["public_mail_left"] == 32
    assert "mail_webhook_missing" in status["warnings"]


async def test_the_sign_up_routes_are_the_admins(create_user: CreateUser, client: ApiClient) -> None:
    alice = await create_user("alice")
    for who, expected in ((client, 401), (alice, 403)):
        for response in (
            await who.get("/api/admin/signup-settings"),
            await who.put("/api/admin/signup-settings", json={"open": True}),
            await who.post("/api/admin/mail/test", json={"to": "x@example.com"}),
        ):
            assert response.status_code == expected


# ---------------- Warnings ----------------


async def test_real_ip_warns_for_the_admin_at_the_public_host_from_a_private_address(
    admin_at: ClientAt, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "public_base_urls", f"{PUBLIC},http://localhost:8080")
    host = PUBLIC.removeprefix("https://")

    tunnelled = await status_of(admin_at("172.18.0.3", host=host))
    real = await status_of(admin_at("81.2.69.142", host=host))
    local = await status_of(admin_at("172.18.0.3", host="localhost:8080"))

    assert "real_ip" in tunnelled["warnings"]
    assert "real_ip" not in real["warnings"] and "real_ip" not in local["warnings"]


async def test_the_webhook_warning_stands_while_sign_up_is_open_without_its_secret(
    admin_client: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert "mail_webhook_missing" not in (await status_of(admin_client))["warnings"]
    await admin_client.put("/api/admin/signup-settings", json={"open": True})
    assert "mail_webhook_missing" in (await status_of(admin_client))["warnings"]

    monkeypatch.setattr(settings, "mail_webhook_secret", "whsec_c2VjcmV0LWtleS1mb3ItdGVzdHM=")

    status = await status_of(admin_client)
    assert "mail_webhook_missing" not in status["warnings"] and status["mail_webhook_configured"] is True


async def test_a_strong_admin_password_warns_only_when_breached_checked_once_a_day(
    admin_client: ApiClient, fake_hibp: FakeHibp, monkeypatch: pytest.MonkeyPatch
) -> None:
    strong = "correct-horse-battery-staple-galaxy"
    monkeypatch.setattr(settings, "admin_password", strong)

    assert "admin_password_weak" not in (await status_of(admin_client))["warnings"]
    assert "admin_password_weak" not in (await status_of(admin_client))["warnings"]
    assert len(fake_hibp.requests) == 1  # the answer is kept a day
    for key in await get_redis().keys("auth:admin_breach:*"):
        assert 86_000 < await get_redis().ttl(key) <= 86_400
        await get_redis().delete(key)
    fake_hibp.add(strong)

    assert "admin_password_weak" in (await status_of(admin_client))["warnings"]


async def test_a_failed_breach_check_doesnt_warn(
    admin_client: ApiClient, fake_hibp: FakeHibp, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "admin_password", "correct-horse-battery-staple-galaxy")
    fake_hibp.failure = "timeout"

    assert "admin_password_weak" not in (await status_of(admin_client))["warnings"]


# ---------------- Counts ----------------


async def test_the_sign_up_counts(admin_client: ApiClient, create_user: CreateUser) -> None:
    now = utcnow()
    ids = []
    for name, source, age in (
        ("todays", "email", timedelta(0)),
        ("weeks", "google", timedelta(days=3)),
        ("old", "email", timedelta(days=9)),
        ("admins", "admin", timedelta(0)),
    ):
        user = await create_user(name, login=False, plan=None)
        assert user.user_id is not None
        await set_user(user.user_id, signup_source=source, created_at=now - age)
        ids.append(user.user_id)
    async with db.SessionLocal() as session:
        session.add(
            BillingPayment(
                user_id=ids[1],
                username="weeks",
                status="captured",
                amount_paise=100,
                currency="INR",
                mode="live",
                applied_at=now,
            )
        )
        await session.commit()

    status = await status_of(admin_client)

    assert (status["signups_today"], status["signups_7d"], status["paid_7d"]) == (1, 2, 1)


# ---------------- The test mail ----------------


async def test_the_test_mail(admin_client: ApiClient, mailer: None, monkeypatch: pytest.MonkeyPatch) -> None:
    response = await admin_client.post("/api/admin/mail/test", json={"to": "Owner@Example.com"})

    assert (response.status_code, response.json()) == (202, {"status": "accepted"})
    [job] = await jobs()
    assert (job.kind, job.mail_class, job.to_address) == ("test", "account", "Owner@example.com")
    assert (await status_of(admin_client))["mail_queued"] == 1
    for _ in range(4):
        assert (await admin_client.post("/api/admin/mail/test", json={"to": "owner@example.com"})).status_code == 202
    limited = await admin_client.post("/api/admin/mail/test", json={"to": "owner@example.com"})
    assert (limited.status_code, limited.json()["code"]) == (429, "too_many_attempts")
    invalid = await admin_client.post("/api/admin/mail/test", json={"to": "nobody"})
    assert (invalid.status_code, invalid.json()) == (422, {"detail": "Enter a valid email address."})


async def test_the_test_mail_needs_the_mailer(admin_client: ApiClient) -> None:
    response = await admin_client.post("/api/admin/mail/test", json={"to": "owner@example.com"})

    assert (response.status_code, response.json()["code"]) == (503, "mail_unavailable")


# ---------------- Admin → Users ----------------


@pytest.mark.parametrize(
    ("password", "reason"), [("short", "too_short"), ("password123", "common"), ("Carol-2026", "context")]
)
async def test_creating_a_user_applies_the_password_policy(password: str, reason: str, admin_client: ApiClient) -> None:
    response = await admin_client.post("/api/admin/users", json={"username": "carol", "password": password})

    assert response.status_code == 422
    assert (response.json()["code"], response.json()["reason"]) == ("password_rejected", reason)


async def test_a_breached_password_is_refused_on_create(admin_client: ApiClient, fake_hibp: FakeHibp) -> None:
    fake_hibp.add("breached-but-long-password")

    response = await admin_client.post(
        "/api/admin/users", json={"username": "carol", "password": "breached-but-long-password"}
    )

    assert (response.status_code, response.json()["reason"]) == (422, "breached")


async def test_deleting_a_user_retires_the_username(admin_client: ApiClient, create_user: CreateUser) -> None:
    alice = await create_user("alice")

    assert (await admin_client.delete(f"/api/admin/users/{alice.user_id}")).status_code == 204

    async with db.SessionLocal() as session:
        assert await session.get(UsernameTombstone, "alice") is not None
    again = await admin_client.post("/api/admin/users", json={"username": "Alice", "password": USER_PASSWORD})
    assert again.status_code == 409
    assert again.json() == {
        "detail": "That username belonged to a deleted account. Choose another.",
        "code": "username_retired",
    }


async def test_the_admins_reset_applies_the_policy_revokes_reset_links_and_clears_failures(
    admin_client: ApiClient, create_user: CreateUser, client: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "login_max_attempts", 3)
    alice = await create_user("alice", login=False)
    assert alice.user_id is not None
    await give_email(alice.user_id, "alice@example.com")
    link = await reset_token(alice.user_id)
    for _ in range(3):
        await client.post("/api/auth/login", json={"identifier": "alice", "password": "wrong-password"})
    assert (
        await client.post("/api/auth/login", json={"identifier": "alice", "password": USER_PASSWORD})
    ).status_code == 429

    weak = await admin_client.patch(f"/api/admin/users/{alice.user_id}", json={"password": "password123"})
    done = await admin_client.patch(f"/api/admin/users/{alice.user_id}", json={"password": "set-by-the-admin-2026"})

    assert (weak.status_code, weak.json()["reason"]) == (422, "common")
    assert done.status_code == 200
    assert await live_tokens(alice.user_id, "reset") == [] and link
    signed_in = await client.post("/api/auth/login", json={"identifier": "alice", "password": "set-by-the-admin-2026"})
    assert signed_in.status_code == 200


async def test_admin_users_show_how_each_account_signs_in(admin_client: ApiClient, create_user: CreateUser) -> None:
    alice = await create_user("alice")
    bob = await create_user("bob")
    assert alice.user_id is not None and bob.user_id is not None
    now = utcnow()
    await give_email(alice.user_id, "alice@gmail.com")
    await set_user(alice.user_id, signup_source="google", password_hash=None, terms_version="t", terms_accepted_at=now)
    async with db.SessionLocal() as session:
        session.add(
            UserIdentity(
                user_id=alice.user_id,
                provider="google",
                issuer="https://accounts.google.com",
                subject="700000000000000000001",
                email_at_link="alice@gmail.com",
                email_kind="gmail",
            )
        )
        session.add(
            PendingEmail(
                user_id=bob.user_id,
                address="bob@example.com",
                canonical="bob@example.com",
                source="user",
                created_at=now,
                expires_at=now + timedelta(hours=24),
            )
        )
        await session.commit()

    users = {user["username"]: user for user in (await admin_client.get("/api/admin/users")).json()}

    first, second, admin = users["alice"], users["bob"], users[ADMIN_USERNAME]
    assert (first["email"], first["signup_source"], first["has_password"], first["google_linked"]) == (
        "alice@gmail.com",
        "google",
        False,
        True,
    )
    assert first["email_verified_at"] is not None and first["terms_accepted_at"] is not None
    assert (first["terms_version"], first["google_email"]) == ("t", "alice@gmail.com")
    assert first["pending_email"] is None
    assert (second["email"], second["pending_email"]) == (None, {"address": "bob@example.com", "sent_at": None})
    assert (second["signup_source"], second["has_password"], second["google_linked"]) == ("admin", True, False)
    assert (second["terms_version"], second["google_email"], second["terms_accepted_at"]) == (None, None, None)
    assert (admin["signup_source"], admin["email"]) == ("env", None)
    created = await admin_client.post("/api/admin/users", json={"username": "carol", "password": USER_PASSWORD})
    assert created.status_code == 201 and created.json()["signup_source"] == "admin"
    assert created.json()["google_linked"] is False


async def test_the_config_reports_what_the_pages_may_offer(
    client: ApiClient, outside_hosts: OutsideHosts, admin_client: ApiClient
) -> None:
    response = await client.get("/api/auth/config")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "signup": {"open": False, "email": False, "google": False},
        "google_signin": False,
        "password_reset": False,
        "turnstile_site_key": None,
        "password_min_length": 8,
        "password_max_length": 256,
        "terms_version": "2026-11-01",
        "contact_email": None,
    }
