"""Resend's bounce and complaint webhook (routers/mail_events.py, services/mail/webhook.py; docs/briefs/signup-auth.md
6.6): 404 without MAIL_WEBHOOK_SECRET; the Svix signature (good, another secret, an old or future timestamp, several
signatures, missing headers); each delivery once; permanent bounces and complaints suppress the recipients, transient
bounces and other types are ignored; the 64 KB limit; logs with masked addresses; a failure lets the retry through."""

import base64
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
from conftest import ApiClient
from fake_mail import svix_sign
from sqlalchemy import select

from app import db
from app.config import settings
from app.models import MailSuppression
from app.services.auth import emails
from app.services.mail import outbox, webhook

URL = "/api/mail/resend-events"
SECRET = "whsec_" + base64.b64encode(b"resend-webhook-signing-key-0001").decode()
OTHER_SECRET = "whsec_" + base64.b64encode(b"another-key-entirely-0000000000").decode()


@pytest.fixture(autouse=True)
def secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mail_webhook_secret", SECRET)


def event(
    kind: str = "email.bounced", to: Any = ("Bad.Address@Example.com",), bounce: str | None = "Permanent"
) -> bytes:
    data: dict[str, Any] = {
        "email_id": "4ef9a417",
        "from": "no-reply@example.test",
        "to": list(to) if isinstance(to, tuple) else to,
    }
    if bounce is not None:
        data["bounce"] = {"message": "The recipient's mailbox doesn't exist", "subType": "General", "type": bounce}
    return json.dumps({"type": kind, "created_at": "2026-11-01T10:00:00.000Z", "data": data}).encode()


async def post(client: ApiClient, body: bytes, headers: dict[str, str] | None = None, **sign: Any) -> int:
    signed = headers if headers is not None else svix_sign(SECRET, body, **sign)
    response = await client.post(URL, content=body, headers={"content-type": "application/json", **signed})
    return response.status_code


async def suppressions() -> dict[str, tuple[str, str]]:
    async with db.SessionLocal() as session:
        rows = await session.scalars(select(MailSuppression))
        return {row.recipient_sha256: (row.reason, row.source) for row in rows}


def hashed(address: str) -> str:
    return emails.digest(address.lower())


async def test_without_a_secret_the_endpoint_isnt_there(client: ApiClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mail_webhook_secret", "")

    assert await post(client, event()) == 404
    assert await suppressions() == {}


async def test_a_permanent_bounce_suppresses_each_recipient(
    client: ApiClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="app.services.mail")

    assert await post(client, event(to=("Bad.Address@Example.com", "Jo <other@example.org>", "not an address"))) == 200

    assert await suppressions() == {
        hashed("bad.address@example.com"): ("bounce", "webhook"),
        hashed("other@example.org"): ("bounce", "webhook"),
    }
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "email.bounced for B***@example.com" in logged and "Bad.Address" not in logged
    async with db.SessionLocal() as session:
        assert (await outbox.mail_status(session)).bounces_today == 2


async def test_a_complaint_suppresses_until_the_address_is_confirmed_again(client: ApiClient) -> None:
    assert await post(client, event("email.complained", to="Fan@example.com", bounce=None)) == 200

    assert await suppressions() == {hashed("fan@example.com"): ("complaint", "webhook")}
    async with db.SessionLocal() as session:
        row = await session.get(MailSuppression, hashed("fan@example.com"))
        assert row is not None and row.expires_at is None
        assert (await outbox.mail_status(session)).complaints_7d == 1


@pytest.mark.parametrize("kind", ["Transient", "undetermined"])
async def test_a_transient_bounce_is_ignored(client: ApiClient, kind: str) -> None:
    assert await post(client, event(bounce=kind)) == 200
    assert await suppressions() == {}


@pytest.mark.parametrize("kind", ["email.sent", "email.delivered", "email.opened", "contact.created"])
async def test_other_types_are_acknowledged_and_ignored(client: ApiClient, kind: str) -> None:
    assert await post(client, event(kind)) == 200
    assert await suppressions() == {}


async def test_a_bounce_without_its_type_counts_as_permanent(client: ApiClient) -> None:
    assert await post(client, event(bounce=None)) == 200
    assert await suppressions() == {hashed("bad.address@example.com"): ("bounce", "webhook")}


async def test_a_signed_body_that_isnt_an_event_is_ignored(client: ApiClient) -> None:
    assert await post(client, b"not json") == 200
    assert await post(client, b"[1, 2]") == 200
    assert await post(client, json.dumps({"type": "email.bounced", "data": "x"}).encode()) == 200
    assert await suppressions() == {}


@pytest.mark.parametrize(
    "headers",
    [
        svix_sign(OTHER_SECRET, event()),
        {**svix_sign(SECRET, event()), "svix-signature": "v1,AAAA"},
        {**svix_sign(SECRET, event()), "svix-signature": "v2," + svix_sign(SECRET, event())["svix-signature"][3:]},
        {key: value for key, value in svix_sign(SECRET, event()).items() if key != "svix-id"},
        {key: value for key, value in svix_sign(SECRET, event()).items() if key != "svix-timestamp"},
        {key: value for key, value in svix_sign(SECRET, event()).items() if key != "svix-signature"},
        {**svix_sign(SECRET, event()), "svix-timestamp": "yesterday"},
    ],
)
async def test_a_bad_signature_is_401(client: ApiClient, headers: dict[str, str]) -> None:
    assert await post(client, event(), headers) == 401
    assert await suppressions() == {}


@pytest.mark.parametrize("offset", [-301, 301])
async def test_a_timestamp_over_5_minutes_away_is_401(client: ApiClient, offset: int) -> None:
    assert await post(client, event(), timestamp=int(time.time()) + offset) == 401


async def test_a_body_changed_after_signing_is_401(client: ApiClient) -> None:
    headers = svix_sign(SECRET, event(to=("a@example.com",)))

    assert await post(client, event(to=("victim@example.com",)), headers) == 401


async def test_one_of_several_signatures_is_enough(client: ApiClient) -> None:
    body = event()
    good = svix_sign(SECRET, body, msg_id="msg_rotation")
    stale = svix_sign(OTHER_SECRET, body, msg_id="msg_rotation", timestamp=int(good["svix-timestamp"]))
    headers = {**good, "svix-signature": f"{stale['svix-signature']} {good['svix-signature']}"}

    assert await post(client, body, headers) == 200
    assert len(await suppressions()) == 1


async def test_a_delivery_is_handled_once(client: ApiClient) -> None:
    body = event()
    headers = svix_sign(SECRET, body, msg_id="msg_once")
    assert await post(client, body, headers) == 200
    async with db.SessionLocal() as session:
        await outbox.unsuppress(session, hashed("bad.address@example.com"))
        await session.commit()

    assert await post(client, body, headers) == 200

    assert await suppressions() == {}


async def test_a_body_over_64_kb_is_413(client: ApiClient) -> None:
    body = event(to=("a@example.com",)) + b" " * webhook.MAX_BODY

    async def chunks() -> AsyncIterator[bytes]:  # no content-length: counted as it arrives
        yield b"x" * 40_000
        yield b"y" * 40_000

    assert await post(client, body) == 413
    response = await client.post(URL, content=chunks(), headers=svix_sign(SECRET, b""))
    assert response.status_code == 413


async def test_a_failure_lets_resends_retry_through(client: ApiClient, monkeypatch: pytest.MonkeyPatch) -> None:
    body = event()
    headers = svix_sign(SECRET, body, msg_id="msg_retry")
    original = outbox.suppress

    async def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("database gone")

    monkeypatch.setattr(outbox, "suppress", broken)
    with pytest.raises(RuntimeError):
        await post(client, body, headers)
    monkeypatch.setattr(outbox, "suppress", original)

    assert await post(client, body, headers) == 200
    assert len(await suppressions()) == 1


def test_verify_accepts_svix_s_scheme_and_rejects_the_rest() -> None:
    body = b'{"type":"email.bounced"}'
    headers = svix_sign(SECRET, body, msg_id="msg_1", timestamp=1_790_000_000)

    assert webhook.verify(headers, body, now=1_790_000_100) == "msg_1"
    with pytest.raises(webhook.SignatureError):
        webhook.verify(headers, body, now=1_790_000_400)
    with pytest.raises(webhook.SignatureError):
        webhook.verify({**headers, "svix-id": "m" * 257}, body, now=1_790_000_000)
