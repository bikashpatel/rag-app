"""Flow 19: the mailer against the fake SMTP server (integration/fake_mail, run as the fake-mail service: SMTP the
worker sends to, an HTTP side the tests read and control, and Resend's signed webhook events; docs/briefs/signup-auth.md
15.2 items 9 to 13). The stack sends through MAIL_SMTP_* to the fake with MAIL_SMTP_SECURITY=none; no real provider is
reachable.

Covered: a mail deferred twice (451) delivered once; a refused recipient (550) failed and suppressed; a connection lost
after the data retried and kept once by its idempotency key; a mail the worker was sending when it was stopped (a
deploy, a crash) put back and sent once after the restart; the day's cap refusing sign-up mail while a reset and a
receipt still go out; a receipt with its PDF after a fake Razorpay payment, a plan reminder, and checkout refused
without a verified email; bounce reports pausing sign-up mail (Google sign-up unaffected); the clean-up, finished jobs
without addresses and a deleted user's rows; the `mail` monitor check alerting the fake receiver."""

import hashlib
import json
from typing import Any

import httpx
import pytest
from harness import (
    APP_URL,
    ENV,
    alerts_received,
    check,
    client,
    compose,
    login,
    python_in,
    redis_cli,
    services,
    sql,
    sql_value,
    unique,
    wait_until,
)
from test_17_billing import buy, new_user, own_address, plan_ids

FAKE_MAIL_URL = f"http://127.0.0.1:{ENV['IT_FAKE_MAIL_PORT']}"
DAILY_LIMIT = int(ENV.get("MAIL_DAILY_LIMIT", "80"))
MAIL_WAIT = 60
PROBLEM_WAIT = 60  # MONITOR_INTERVAL_SECONDS=2 and ALERT_CONFIRM_CHECKS=2

_ENQUEUE = """
import asyncio, json, sys
from app.db import SessionLocal
from app.services.mail import outbox
job = json.loads(sys.stdin.read())
async def main():
    async with SessionLocal() as db:
        await outbox.enqueue(db, job.pop("kind"), **job)
        await db.commit()
    await outbox.wake()
asyncio.run(main())
"""
_WORKER_CALL = """
import asyncio, json
from app.services.billing import reminders
from app.services.mail import sweeps
print(json.dumps(asyncio.run({call})))
"""


# ---------------- The fake and the outbox ----------------


def fake(method: str, path: str, **kwargs: Any) -> Any:
    return check(httpx.request(method, f"{FAKE_MAIL_URL}{path}", timeout=30, **kwargs)).json()


def mails(to: str) -> list[dict[str, Any]]:
    return fake("GET", "/_mail", params={"to": to})["messages"]


def control(**body: Any) -> dict[str, Any]:
    return fake("POST", "/_control", json=body)


def wait_mail(to: str, count: int = 1) -> list[dict[str, Any]]:
    return wait_until(lambda: (found := mails(to)) and len(found) >= count and found, MAIL_WAIT, f"mail to {to}")


def address(name: str) -> str:
    return f"{unique(f'it-{name}-')}@example.test"


def digest(value: str) -> str:
    return hashlib.sha256(value.lower().encode()).hexdigest()


def enqueue(kind: str, to: str, **fields: Any) -> None:
    """A job added the way the app's requests add them (outbox.enqueue), from the worker's container."""
    python_in("worker", _ENQUEUE, stdin=json.dumps({"kind": kind, "to": to, **fields}))


def worker_call(call: str) -> Any:
    return json.loads(python_in("worker", _WORKER_CALL.format(call=call)))


def job(to: str) -> dict[str, str]:
    """The newest job to `to`: its status, attempts and last error."""
    rows = sql(
        "SELECT status, attempts, coalesce(last_error, '') FROM email_outbox "
        f"WHERE recipient_sha256 = '{digest(to)}' ORDER BY id DESC LIMIT 1"
    )
    assert rows, f"no job to {to}"
    status, attempts, error = rows[0]
    return {"status": status, "attempts": attempts, "last_error": error}


def waiting(attempts: str) -> dict[str, str]:
    return {"status": "queued", "attempts": attempts, "last_error": ""}


def retry_now() -> None:
    """Queued retries are due now; the worker is woken."""
    sql("UPDATE email_outbox SET next_attempt_at = now() WHERE status = 'queued'")
    redis_cli("RPUSH", "rag:mail_wake", "1")


def turnstile(action: str) -> str:
    """A token the fake siteverify passes for `action`."""
    return f"pass-{action}-{unique('it')}"


def browser() -> httpx.Client:
    """A visitor from an address of its own."""
    visitor = client()
    visitor.headers["CF-Connecting-IP"] = own_address()
    return visitor


def sign_up(email: str) -> httpx.Response:
    body = {
        "name": "Mail Tester",
        "email": email,
        "password": f"Wide-{unique('')}-lantern",
        "accept_terms": True,
        "turnstile_token": turnstile("signup"),
    }
    return browser().post("/api/auth/signup", json=body)


@pytest.fixture(scope="module", autouse=True)
def mail_ready(admin: httpx.Client) -> Any:
    """Sign-up open (both methods), the fake reset; afterwards no failure mode left on and no suppression kept."""
    control(reset=True)
    check(admin.put("/api/admin/signup-settings", json={"open": True, "email": True, "google": True}))
    yield
    control(reset=True)
    sql("DELETE FROM mail_suppressions")


# ---------------- 9: failures ----------------


def test_a_deferred_mail_is_retried_and_delivered_once() -> None:
    to = address("deferred")
    control(fail_code=451, fail_count=2)

    enqueue("test", to)
    for attempt in ("1", "2"):
        wait_until(lambda n=attempt: job(to) | {"last_error": ""} == waiting(n), MAIL_WAIT, "a retry")
        assert job(to)["last_error"] == "recipient deferred (451)"
        retry_now()
    (message,) = wait_mail(to)

    assert job(to) == {"status": "sent", "attempts": "3", "last_error": ""}
    assert message["subject"] == "Test email from AskNuvexusDoc"
    assert message["headers"]["Auto-Submitted"] == "auto-generated"
    assert message["headers"]["Resend-Idempotency-Key"].startswith("mail-")
    assert "Mail from AskNuvexusDoc reaches this address." in message["text"]
    kept = f"SELECT count(*) FROM email_outbox WHERE recipient_sha256 = '{digest(to)}' AND to_address IS NOT NULL"
    assert sql_value(kept) == "0"


def test_a_refused_recipient_fails_and_is_never_mailed_again() -> None:
    to = address("refused")
    control(fail_code=550, fail_count=1)

    enqueue("test", to)
    wait_until(lambda: job(to)["status"] == "failed", MAIL_WAIT, "the failure")
    assert job(to)["last_error"] == "recipient refused (550)"
    assert sql(f"SELECT reason, source FROM mail_suppressions WHERE recipient_sha256 = '{digest(to)}'") == [
        ["bounce", "smtp"]
    ]
    enqueue("test", to)
    wait_until(lambda: job(to)["status"] == "cancelled", MAIL_WAIT, "the suppressed job")

    assert job(to)["last_error"] == "suppressed" and mails(to) == []
    sql(f"DELETE FROM email_outbox WHERE recipient_sha256 = '{digest(to)}'")  # leaves the `mail` check clean
    sql(f"DELETE FROM mail_suppressions WHERE recipient_sha256 = '{digest(to)}'")


def test_a_connection_lost_after_the_data_is_kept_once_by_its_idempotency_key() -> None:
    to = address("dropped")
    control(drop_after_data=1)
    before = control()["counters"]["duplicates"]

    enqueue("test", to)
    wait_until(lambda: job(to)["status"] == "queued" and job(to)["attempts"] == "1", MAIL_WAIT, "the lost answer")
    assert job(to)["last_error"] == "connection failed" and len(mails(to)) == 1  # it arrived
    retry_now()
    wait_until(lambda: job(to)["status"] == "sent", MAIL_WAIT, "the retry")

    assert len(mails(to)) == 1 and control()["counters"]["duplicates"] == before + 1


@pytest.mark.deadline(300)
def test_a_mail_the_worker_was_sending_when_it_stopped_goes_out_once_after_the_restart() -> None:
    to = address("restart")
    held = control(hold_data=True)["counters"]["held"]
    try:
        enqueue("test", to)
        wait_until(lambda: control()["counters"]["held"] > held, MAIL_WAIT, "the conversation to hang")
        assert job(to)["status"] == "sending"
        compose("stop", "worker", timeout=150)  # SIGTERM mid-send
        assert job(to)["status"] == "queued"  # put back with its address, not left "sending" for good
        kept = f"SELECT count(*) FROM email_outbox WHERE recipient_sha256 = '{digest(to)}' AND to_address IS NOT NULL"
        assert sql_value(kept) == "1"
    finally:
        control(hold_data=False)
        compose("start", "worker", timeout=150)
    wait_until(lambda: services()["worker"]["state"] == "running", 60, "the worker to run again")
    retry_now()

    wait_until(lambda: job(to)["status"] == "sent", MAIL_WAIT, "the mail after the restart")
    assert len(mails(to)) == 1  # a copy the hung conversation delivered is dropped by the idempotency key


def test_the_days_cap_refuses_sign_up_mail_while_resets_and_receipts_go_out(admin: httpx.Client) -> None:
    today = "date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'"
    used = int(sql_value(f"SELECT count(*) FROM email_outbox WHERE mail_class = 'public' AND created_at >= {today}"))
    filler = max(0, DAILY_LIMIT * 40 // 100 - used)  # the public class's share, used up
    sql(
        "INSERT INTO email_outbox (public_id, kind, priority, mail_class, recipient_sha256, params, status, attempts, "
        "next_attempt_at, not_after, created_at, finished_at) SELECT 'it-fill-' || g, 'signup', 1, 'public', "
        f"md5(g::text), '{{}}', 'cancelled', 0, now(), now(), now(), now() FROM generate_series(1, {filler}) g"
    )
    try:
        refused = sign_up(address("capped"))
        assert refused.status_code == 503 and refused.json()["code"] == "mail_unavailable", refused.text

        member = new_user(admin, "cap-reset")
        email = sql_value(f"SELECT email FROM users WHERE id = {member.id}")
        forgot = browser().post(
            "/api/auth/password/forgot", json={"email": email, "turnstile_token": turnstile("reset")}
        )
        assert forgot.status_code == 202, forgot.text
        (reset,) = wait_mail(email)
        assert reset["subject"] == "Reset your AskNuvexusDoc password" and "/reset-password#t=" in reset["text"]

        buy(member, admin, "starter")
        receipts = wait_until(
            lambda: [item for item in mails(email) if item["subject"].startswith("Receipt ")], MAIL_WAIT, "the receipt"
        )
        assert len(receipts) == 1
    finally:
        sql("DELETE FROM email_outbox WHERE public_id LIKE 'it-fill-%'")


# ---------------- 10: billing mail ----------------


def test_checkout_needs_a_verified_email(admin: httpx.Client) -> None:
    username = unique("it-noemail-")
    password = f"pw-{unique('')}-secret"
    body = {"username": username, "password": password, "billing_tester": True}
    check(admin.post("/api/admin/users", json=body), 201)
    session = login(username, password)
    session.headers["CF-Connecting-IP"] = own_address()

    refused = session.post(
        "/api/billing/checkout",
        json={"plan_id": plan_ids(admin)["starter"], "kind": "period", "accept_terms": True},
        headers={"Origin": APP_URL},
    )

    assert refused.status_code == 409 and refused.json()["code"] == "email_required"


def test_a_receipt_mail_carries_the_pdf(admin: httpx.Client) -> None:
    member = new_user(admin, "receipt")
    email = sql_value(f"SELECT email FROM users WHERE id = {member.id}")

    paid = buy(member, admin, "starter")
    (message,) = wait_mail(email)

    number = paid["receipt"]["number"]
    assert message["subject"] == f"Receipt {number} for your Starter plan"
    (attachment,) = message["attachments"]
    assert attachment["filename"] == f"receipt-{number.replace('/', '-')}.pdf"
    assert attachment["content_type"] == "application/pdf" and attachment["size"] > 1000
    assert "Method: Card" in message["text"] and "Turn receipt emails off in Settings" in message["text"]
    assert message["envelope"]["to"] == [email]


def test_a_plan_reminder(admin: httpx.Client) -> None:
    member = new_user(admin, "reminder")
    email = sql_value(f"SELECT email FROM users WHERE id = {member.id}")
    standard = plan_ids(admin)["standard"]
    check(
        admin.post(
            f"/api/admin/billing/users/{member.id}/grants",
            json={"plan_id": standard, "days": 3, "reason": "Integration reminder", "start": "now"},
        )
    )

    assert worker_call("reminders.send_due()") >= 1
    (soon,) = wait_mail(email)
    sql(f"UPDATE plan_periods SET ends_at = now() + interval '20 hours' WHERE user_id = {member.id}")
    assert worker_call("reminders.send_due()") >= 1
    tomorrow = wait_mail(email, 2)[1]

    assert soon["subject"] == "Your Standard plan ends in 5 days"
    assert f"{APP_URL}/plans" in soon["text"] and "Turn plan reminder emails off" in soon["text"]
    assert tomorrow["subject"] == "Your Standard plan ends tomorrow"
    assert worker_call("reminders.send_due()") == 0  # once each


# ---------------- 11: bounces ----------------


def test_bounce_reports_pause_sign_up_mail_but_not_google_sign_up() -> None:
    bounced = [address("bounced") for _ in range(3)]
    for value in bounced:
        assert fake("POST", "/_control/event", json={"type": "email.bounced", "to": [value]}) == {"status": 200}
    try:
        hashes = ", ".join(f"'{digest(value)}'" for value in bounced)
        rows = sql(f"SELECT reason, source FROM mail_suppressions WHERE recipient_sha256 IN ({hashes})")
        assert rows == [["bounce", "webhook"]] * 3

        refused = sign_up(address("after-bounces"))
        assert refused.status_code == 503 and refused.json()["code"] == "mail_unavailable"
        config = check(client().get("/api/auth/config")).json()
        assert config["signup"]["google"] is True
        started = browser().post(
            "/api/auth/google/start", json={"intent": "signin", "from": "signup"}, headers={"Origin": APP_URL}
        )
        assert started.status_code == 200 and started.json()["authorize_url"], started.text

        transient = address("transient")
        assert fake("POST", "/_control/event", json={"to": transient, "bounce_type": "Transient"}) == {"status": 200}
        assert (
            sql_value(f"SELECT count(*) FROM mail_suppressions WHERE recipient_sha256 = '{digest(transient)}'") == "0"
        )
    finally:
        sql("DELETE FROM mail_suppressions")
    assert sign_up(address("after-pause")).status_code == 202


# ---------------- 12: clean-up and deletion ----------------


def test_the_clean_up_and_a_deleted_users_rows(admin: httpx.Client) -> None:
    member = new_user(admin, "deleted")
    stale = address("stale")
    sql(
        "INSERT INTO pending_signups (address, canonical, password_hash, terms_version, terms_accepted_at, "
        f"password_failures, created_at, expires_at) VALUES ('{stale}', '{stale}', 'x', 'it', now(), 0, "
        "now() - interval '8 days', now() - interval '1 day')"
    )
    sql(
        "INSERT INTO auth_tokens (purpose, token_sha256, address_sha256, user_id, created_at, expires_at, used_at) "
        f"VALUES ('reset', '{unique('it-token-')}', 'x', {member.id}, now() - interval '3 days', "
        "now() - interval '2 days', now() - interval '2 days'), "
        f"('reset', '{unique('it-token-')}', 'x', {member.id}, now(), now() + interval '30 minutes', NULL)"
    )
    sql(
        "INSERT INTO user_identities (user_id, provider, issuer, subject, email_at_link, email_kind, created_at) "
        f"VALUES ({member.id}, 'google', 'https://accounts.google.com', '{unique('it-sub-')}', 'it@gmail.com', "
        "'gmail', now())"
    )
    sql(
        "INSERT INTO auth_events (created_at, user_id, type, actor, ip, detail) VALUES "
        f"(now() - interval '100 days', {member.id}, 'password.reset', 'self', '203.0.113.x', '{{}}')"
    )
    enqueue("password_added", sql_value(f"SELECT email FROM users WHERE id = {member.id}"), user_id=member.id)
    sql(
        "UPDATE email_outbox SET finished_at = now() - interval '49 hours' "
        "WHERE id = (SELECT min(id) FROM email_outbox WHERE status = 'sent')"
    )

    swept = worker_call("sweeps.run()")

    assert swept["pending_signups"] >= 1 and swept["auth_tokens"] >= 1 and swept["email_outbox"] >= 1
    assert swept["auth_event_ips"] >= 1
    assert sql_value(f"SELECT count(*) FROM pending_signups WHERE canonical = '{stale}'") == "0"
    assert sql_value(f"SELECT count(*) FROM auth_tokens WHERE user_id = {member.id}") == "1"
    assert sql_value(f"SELECT count(*) FROM auth_events WHERE user_id = {member.id} AND ip IS NOT NULL") == "0"
    assert (
        sql_value(
            "SELECT count(*) FROM email_outbox WHERE status IN ('sent', 'failed', 'cancelled', 'expired') "
            "AND (to_address IS NOT NULL OR canonical IS NOT NULL)"
        )
        == "0"
    )

    check(admin.delete(f"/api/admin/users/{member.id}"), 204)
    for table in ("user_identities", "auth_tokens", "email_outbox", "auth_events", "pending_emails"):
        assert sql_value(f"SELECT count(*) FROM {table} WHERE user_id = {member.id}") == "0", table
    assert sql_value(f"SELECT count(*) FROM username_tombstones WHERE username = '{member.username}'") == "1"


# ---------------- 13: the monitor ----------------


def email_alert_lines(after: int) -> list[str]:
    return [
        line
        for alert in alerts_received()[after:]
        for line in alert["text"].splitlines()
        if line.startswith(("PROBLEM Email", "RESOLVED Email"))
    ]


def mail_check(admin: httpx.Client) -> dict[str, Any]:
    checks = check(admin.get("/api/admin/system")).json()["checks"]
    return next(item for item in checks if item["id"] == "mail")


def test_a_complaint_alerts_through_the_mail_check(admin: httpx.Client) -> None:
    wait_until(lambda: mail_check(admin)["status"] == "ok", 30, "the mail check to be ok first")
    lines = email_alert_lines(0)
    if lines and lines[-1].startswith("PROBLEM"):
        start = len(alerts_received())
        wait_until(lambda: email_alert_lines(start), PROBLEM_WAIT, "the earlier Email problem to resolve")
    start = len(alerts_received())

    complained = address("complained")
    assert fake("POST", "/_control/event", json={"type": "email.complained", "to": complained}) == {"status": 200}
    problem = wait_until(
        lambda: [line for line in email_alert_lines(start) if line.startswith("PROBLEM") and "spam complaint" in line],
        PROBLEM_WAIT,
        "the Email problem alert",
    )
    sql("DELETE FROM mail_suppressions")
    wait_until(
        lambda: [line for line in email_alert_lines(start) if line.startswith("RESOLVED")], PROBLEM_WAIT, "the recovery"
    )

    assert "1 spam complaint in the last 7 days" in problem[0] and "@" not in " ".join(problem)
    assert mail_check(admin)["status"] == "ok"
