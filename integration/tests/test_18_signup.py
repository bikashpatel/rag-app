"""Flow 18: self-service sign-up, sign-in by email, Google sign-in, password reset and the account's email against the
fakes (docs/briefs/signup-auth.md 15.2 items 1 to 8): the worker mails through the fake SMTP server (fake-mail),
Turnstile and the breached-password check are the fake-turnstile service, and Google sign-in is the fake Google's
OpenID Connect side, whose consent the tests play as the browser. Sign-up is opened through the admin API first.
Requests carry a CF-Connecting-IP (the stack trusts it) so each test comes from its own networks.

Covered: an email sign-up finished in the same browser and one finished elsewhere with the password; an existing
address's "you already have an account" mail; a stranger's sign-up leaving the first link working; resend and its
limits; sign-in by email and username, the challenge and its outage fallback; forgot and reset (sessions ended, the
notice, Google disconnected) and forgot for a pending sign-up; Google sign-up, the automatic link of a Gmail address,
link_required for a Workspace address, an unverified address confirmed only with the session, linking and unlinking
by hand, the step-up and a first password; an email change, its notice and its undo; the admin's password policy and
retired usernames; the signed-out pages' headers and nginx's per-address zones (in dry-run mode here)."""

import itertools
import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from harness import APP_URL, ENV, FAKE_GOOGLE_URL, check, compose, redis_cli, sql, sql_value, unique, wait_until

FAKE_MAIL_URL = f"http://127.0.0.1:{ENV['IT_FAKE_MAIL_PORT']}"
FAKE_TURNSTILE_URL = f"http://127.0.0.1:{ENV['IT_FAKE_TURNSTILE_PORT']}"
MAIL_WAIT = 60
_LINK = re.compile(r"(https?://[^\s\"'<>]+)#t=([A-Za-z0-9_-]+)")
_NETWORKS = itertools.count(10)


# ---------------- Browsers, the fakes and the mail ----------------


def network() -> str:
    """A public IPv4 address no other test uses: its own sign-in, sign-up and nginx counters."""
    number = next(_NETWORKS)
    return f"81.2.{number // 250}.{number % 250 + 1}"


def browser(ip: str | None = None) -> httpx.Client:
    """A browser of its own (a cookie jar) from a network of its own."""
    headers = {"User-Agent": "rag-it-integration-tests", "CF-Connecting-IP": ip or network()}
    return httpx.Client(base_url=APP_URL, timeout=60, headers=headers)


def turnstile(action: str) -> str:
    return f"pass-{action}-{unique('')}"


def address(name: str) -> str:
    return f"{unique(f'it-{name}-')}@example.test"


def mails(to: str) -> list[dict[str, Any]]:
    return check(httpx.get(f"{FAKE_MAIL_URL}/_mail", params={"to": to}, timeout=30)).json()["messages"]


def wait_mail(to: str, count: int = 1) -> list[dict[str, Any]]:
    found = wait_until(lambda: len(mails(to)) >= count and mails(to), MAIL_WAIT, f"{count} mail(s) to {to}")
    return found


def link_in(message: dict[str, Any]) -> tuple[str, str] | None:
    """(the page, the token) of a mail's link, or None."""
    found = _LINK.search(message["text"] or "")
    return (urlsplit(found.group(1)).path, found.group(2)) if found else None


def token_in(message: dict[str, Any], page: str) -> str:
    found = link_in(message)
    assert found is not None and found[0] == page, (page, message["subject"], found)
    return found[1]


def settled(to: str) -> None:
    """The worker has finished every job to the address (sent, cancelled or expired)."""
    digest = sql_value(f"SELECT encode(sha256(convert_to(lower('{to}'), 'UTF8')), 'hex')")
    wait_until(
        lambda: sql_value(
            f"SELECT count(*) FROM email_outbox WHERE recipient_sha256 = '{digest}' AND status IN ('queued','sending')"
        )
        == "0",
        MAIL_WAIT,
        f"the jobs to {to}",
    )


def signup(client: httpx.Client, email: str, password: str, **extra: Any) -> httpx.Response:
    body = {"email": email, "password": password, "accept_terms": True, "turnstile_token": turnstile("signup")}
    return client.post("/api/auth/signup", json={**body, **extra})


def password() -> str:
    return f"Wide-{unique('')}-lantern"


def choose_google(**fields: Any) -> dict[str, Any]:
    """The Google account the fake's next sign-in consents use."""
    return check(httpx.post(f"{FAKE_GOOGLE_URL}/_control/signin", json=fields, timeout=10)).json()


def google_flow(client: httpx.Client, **start: Any) -> str:
    """Start (intent signin unless given) → the fake's consent → the app's callback; the path the 303 lands on."""
    body = {"intent": "signin", **start}
    started = check(client.post("/api/auth/google/start", json=body, headers={"Origin": APP_URL})).json()
    authorize = urlsplit(started["authorize_url"])
    assert f"{authorize.scheme}://{authorize.netloc}" == FAKE_GOOGLE_URL
    consent = httpx.get(started["authorize_url"], follow_redirects=False, timeout=10)
    assert consent.status_code == 302, consent.text
    callback = urlsplit(consent.headers["location"])
    assert f"{callback.scheme}://{callback.netloc}" == APP_URL and callback.path == "/api/auth/google/callback"
    landed = client.get(f"{callback.path}?{callback.query}", follow_redirects=False)
    assert landed.status_code == 303, landed.text
    location = landed.headers["location"]
    assert location.startswith(APP_URL), location
    return location.removeprefix(APP_URL)


def me(client: httpx.Client) -> httpx.Response:
    return client.get("/api/auth/me")


def sign_in(client: httpx.Client, identifier: str, secret: str, **extra: Any) -> httpx.Response:
    return client.post("/api/auth/login", json={"identifier": identifier, "password": secret, **extra})


def email_signed_up(email: str) -> tuple[httpx.Client, str, dict[str, Any]]:
    """A finished email sign-up: its browser (signed in), password and Me."""
    client, secret = browser(), password()
    assert check(signup(client, email, secret), 202).json() == {"status": "accepted"}
    token = token_in(wait_mail(email)[-1], "/verify-email")
    finished = check(client.post("/api/auth/signup/complete", json={"token": token})).json()
    return client, secret, finished


def google_signed_up(client: httpx.Client, **account: Any) -> dict[str, Any]:
    choose_google(**account)
    assert google_flow(client, **{"from": "signup"}) == "/signup/google"
    return check(client.post("/api/auth/google/finish", json={"accept_terms": True})).json()


@pytest.fixture(scope="module", autouse=True)
def signup_open(admin: httpx.Client) -> Any:
    """Sign-up open (email and Google) for this file; the fakes back to normal afterwards."""
    check(admin.put("/api/admin/signup-settings", json={"open": True, "email": True, "google": True}))
    config = check(httpx.get(f"{APP_URL}/api/auth/config", timeout=10)).json()
    assert config["signup"] == {"open": True, "email": True, "google": True}, config
    assert config["password_reset"] is True and config["google_signin"] is True
    yield
    httpx.post(f"{FAKE_TURNSTILE_URL}/_control", json={"reset": True}, timeout=10)
    check(admin.put("/api/admin/signup-settings", json={"open": False}))


# ---------------- 1 and 2: email sign-up ----------------


def test_an_email_sign_up_finishes_in_the_same_browser_and_on_another_device() -> None:
    email = address("signup")
    client, _, finished = email_signed_up(email)

    assert (finished["email"], finished["plan"]["state"]) == (email, "none")
    assert check(me(client)).json()["id"] == finished["id"]
    settled(email)
    assert len(mails(email)) == 1  # no welcome mail
    assert sql_value(f"SELECT signup_source FROM users WHERE id = {finished['id']}") == "email"

    other = address("phone")
    first, secret = browser(), password()
    check(signup(first, other, secret), 202)
    token = token_in(wait_mail(other)[-1], "/verify-email")
    phone = browser()
    checked = check(phone.post("/api/auth/signup/check", json={"token": token})).json()
    assert checked["needs_password"] is True and checked["email"] == other
    wrong = phone.post("/api/auth/signup/complete", json={"token": token, "password": "not-the-password"})
    assert (wrong.status_code, wrong.json()["code"]) == (400, "wrong_password")
    done = check(phone.post("/api/auth/signup/complete", json={"token": token, "password": secret})).json()
    assert done["email"] == other and check(me(phone)).json()["id"] == done["id"]


def test_an_existing_address_gets_a_you_have_an_account_mail_and_a_stranger_kills_no_link() -> None:
    email = address("existing")
    _, _, existing = email_signed_up(email)
    users_before = sql_value("SELECT count(*) FROM users")
    forget_address_limits()  # the address's sign-up mail a moment ago used its minute

    again = signup(browser(), email, password())

    assert (again.status_code, again.json()) == (202, {"status": "accepted"})
    [first, notice] = wait_mail(email, 2)
    assert link_in(notice) is None and "already" in notice["subject"].lower()
    assert sql_value("SELECT count(*) FROM users") == users_before
    assert sql_value(f"SELECT email FROM users WHERE id = {existing['id']}") == email

    pending = address("pending")
    owner = browser()
    check(signup(owner, pending, password()), 202)
    token = token_in(wait_mail(pending)[-1], "/verify-email")
    stranger = browser()
    forget_address_limits()
    check(signup(stranger, pending, password()), 202)
    wait_mail(pending, 2)

    finished = check(owner.post("/api/auth/signup/complete", json={"token": token})).json()
    assert finished["email"] == pending


def forget_address_limits() -> None:
    """Forgets the silent per-address minutes, as if a minute had passed."""
    for key in redis_cli("--scan", "--pattern", "auth:addr:*:60:*").split():
        redis_cli("DEL", key)


# ---------------- 3: resend ----------------


def test_resend_and_its_limits() -> None:
    email = address("resend")
    client = browser()
    check(signup(client, email, password()), 202)
    wait_mail(email)

    silent = client.post("/api/auth/signup/resend", json={"email": email, "turnstile_token": turnstile("resend")})
    assert (silent.status_code, silent.json()) == (202, {"status": "accepted"})
    settled(email)
    assert len(mails(email)) == 1  # within the minute: nothing more
    forget_address_limits()
    check(client.post("/api/auth/signup/resend", json={"email": email, "turnstile_token": turnstile("resend")}), 202)
    [_, second] = wait_mail(email, 2)
    assert token_in(second, "/verify-email")

    crowd = browser()
    answers = [
        crowd.post("/api/auth/signup/resend", json={"email": address("crowd"), "turnstile_token": turnstile("resend")})
        for _ in range(11)
    ]
    assert [answer.status_code for answer in answers] == [202] * 10 + [429]
    assert answers[-1].json()["code"] == "too_many_attempts" and int(answers[-1].headers["retry-after"]) > 0


# ---------------- 4: sign-in ----------------


def test_sign_in_by_email_and_username_with_the_challenge_and_its_fallback() -> None:
    email = address("signin")
    _, secret, user = email_signed_up(email)

    assert check(sign_in(browser(), email.upper(), secret)).json()["id"] == user["id"]
    assert check(sign_in(browser(), user["username"], secret)).json()["id"] == user["id"]

    here = network()
    for _ in range(3):
        assert sign_in(browser(here), email, "not-the-password").status_code == 401
    challenged = sign_in(browser(here), email, secret)
    assert (challenged.status_code, challenged.json()["code"]) == (401, "challenge_required")
    passed = sign_in(browser(here), email, secret, turnstile_token=turnstile("login"))
    assert passed.status_code == 200
    elsewhere = network()
    for _ in range(3):
        sign_in(browser(here), email, "not-the-password")
    assert sign_in(browser(elsewhere), email, secret).status_code == 200  # another network isn't challenged

    there = network()
    for _ in range(3):
        sign_in(browser(there), email, "not-the-password")
    check(httpx.post(f"{FAKE_TURNSTILE_URL}/_control", json={"outage": 2, "mode": "500"}, timeout=10))
    fallback = sign_in(browser(there), email, secret, turnstile_token=turnstile("login"))
    assert fallback.status_code == 200, fallback.text


# ---------------- 5: forgot and reset ----------------


def test_forgot_and_reset_end_every_session_and_can_disconnect_google() -> None:
    gmail = f"{unique('it-reset-')}@gmail.com"
    session = browser()
    created = google_signed_up(session, sub=unique("1"), email=gmail, email_verified=True, clear=["hd", "auth_time"])
    sql(f"UPDATE user_identities SET email_at_link = 'intruder@gmail.com' WHERE user_id = {created['id']}")
    first_secret = password()
    asked = browser().post("/api/auth/password/forgot", json={"email": gmail, "turnstile_token": turnstile("reset")})
    assert (asked.status_code, asked.json()) == (202, {"status": "accepted"})
    token = token_in(wait_mail(gmail)[-1], "/reset-password")

    info = check(browser().post("/api/auth/password/check", json={"token": token})).json()
    assert (info["has_password"], info["google"]) == (False, {"email": "i***@gmail.com", "suggest_disconnect": True})
    page = browser()
    reset = page.post(
        "/api/auth/password/reset", json={"token": token, "new_password": first_secret, "disconnect_google": True}
    )

    assert reset.status_code == 204 and "rag_session" not in reset.headers.get("set-cookie", "")
    assert me(session).status_code == 401 and me(page).status_code == 401
    assert sql_value(f"SELECT count(*) FROM user_identities WHERE user_id = {created['id']}") == "0"
    [*_, notice] = wait_mail(gmail, 2)
    assert "password" in notice["subject"].lower() and link_in(notice) is None
    assert check(sign_in(browser(), gmail, first_secret)).json()["id"] == created["id"]

    pending = address("pending-reset")
    check(signup(browser(), pending, password()), 202)
    wait_mail(pending)
    forget_address_limits()
    body = {"email": pending, "turnstile_token": turnstile("reset")}
    check(browser().post("/api/auth/password/forgot", json=body), 202)
    [_, finish] = wait_mail(pending, 2)
    assert token_in(finish, "/verify-email")


# ---------------- 6: Google ----------------


def test_google_sign_up_auto_link_link_required_and_an_unverified_address() -> None:
    gmail = f"{unique('it-google-')}@gmail.com"
    created = google_signed_up(browser(), sub=unique("2"), email=gmail, email_verified=True, clear=["hd", "auth_time"])
    assert (created["email"], created["plan"]["state"]) == (gmail, "none")
    assert sql_value(
        f"SELECT signup_source || ':' || (password_hash IS NULL) FROM users WHERE id = {created['id']}"
    ) == ("google:true")

    linked_address = f"{unique('it-auto-')}@gmail.com"
    _, _, account = email_signed_up(linked_address)
    choose_google(sub=unique("3"), email=linked_address, email_verified=True, clear=["hd", "auth_time"])
    elsewhere = browser()
    assert google_flow(elsewhere) == "/"
    assert check(me(elsewhere)).json()["id"] == account["id"]
    assert sql_value(f"SELECT email_kind FROM user_identities WHERE user_id = {account['id']}") == "gmail"

    workspace = address("workspace")
    _, _, holder = email_signed_up(workspace)
    choose_google(sub=unique("4"), email=workspace, email_verified=True, hd="example.test")
    assert google_flow(browser()) == "/login?google=link_required"
    assert sql_value(f"SELECT count(*) FROM user_identities WHERE user_id = {holder['id']}") == "0"

    unverified = address("unverified")
    session = browser()
    choose_google(sub=unique("5"), email=unverified, email_verified=True, clear=["hd"])
    assert google_flow(session) == "/signup/google"
    pending = check(session.get("/api/auth/google/pending")).json()
    assert (pending["email"], pending["email_verified"]) == (unverified, False)
    user = check(session.post("/api/auth/google/finish", json={"accept_terms": True})).json()
    assert user["email"] is None
    token = token_in(wait_mail(unverified)[-1], "/confirm-email")
    refused = browser().post("/api/auth/email/verify", json={"token": token})
    assert refused.status_code == 401
    assert check(session.post("/api/auth/email/verify", json={"token": token})).json() == {"email": unverified}
    assert check(me(session)).json()["email"] == unverified


def test_linking_and_unlinking_by_hand_and_the_step_up_then_a_first_password() -> None:
    email = address("link")
    client, secret, _ = email_signed_up(email)
    choose_google(
        sub=unique("6"), email=f"{unique('it-linked-')}@gmail.com", email_verified=True, clear=["hd", "auth_time"]
    )

    assert google_flow(client, intent="link", password=secret) == "/?settings=account&google=linked"
    account = check(client.get("/api/me/account")).json()
    assert account["google"] is not None and account["email"] == email
    [*_, notice] = wait_mail(email, 2)
    assert "google" in notice["subject"].lower()
    check(client.request("DELETE", "/api/me/google", json={"password": secret}), 204)
    assert check(client.get("/api/me/account")).json()["google"] is None

    session = browser()
    google_only = google_signed_up(
        session, sub=unique("7"), email=f"{unique('it-stepup-')}@gmail.com", email_verified=True, clear=["hd"]
    )
    no_grant = session.post("/api/me/password/setup", json={"new_password": password()})
    assert (no_grant.status_code, no_grant.json()["code"]) == (403, "reauth_required")
    landed = google_flow(session, intent="reauth", next="/?settings=account&setup=password")
    assert landed == "/?settings=account&setup=password&google=reauth_ok"
    authorize = check(httpx.get(f"{FAKE_GOOGLE_URL}/_state", timeout=10)).json()["signin"]["authorize"][-1]
    assert authorize["max_age"] == "0" and authorize["login_hint"]
    first = password()
    check(session.post("/api/me/password/setup", json={"new_password": first}), 204)
    assert check(me(session)).json()["id"] == google_only["id"]  # no session ended
    assert check(sign_in(browser(), google_only["username"], first)).json()["id"] == google_only["id"]


# ---------------- 7: changing an email and undoing it ----------------


def test_an_email_change_its_confirmation_its_notice_and_its_undo() -> None:
    old = address("old")
    client, secret, user = email_signed_up(old)
    new = address("new")

    check(client.post("/api/me/email", json={"email": new, "password": secret}), 202)
    token = token_in(wait_mail(new)[-1], "/confirm-email")
    assert browser().post("/api/auth/email/verify", json={"token": token}).status_code == 401
    assert check(client.post("/api/auth/email/verify", json={"token": token})).json() == {"email": new}
    [*_, notice] = wait_mail(old, 2)
    undo = token_in(notice, "/undo-email")

    info = check(browser().post("/api/auth/email/undo-check", json={"token": undo})).json()
    assert info["old_email"] == old and info["new_email"].endswith(new.split("@")[1])
    check(browser().post("/api/auth/email/undo", json={"token": undo}), 204)

    assert me(client).status_code == 401
    assert sql_value(f"SELECT email || ':' || (password_hash IS NULL) FROM users WHERE id = {user['id']}") == (
        f"{old}:true"
    )
    [*_, reset] = wait_mail(old, 3)
    assert token_in(reset, "/reset-password")
    assert wait_mail(new, 2)[-1]["subject"]  # the address the undo replaced is told


# ---------------- 8: the admin's forms ----------------


def test_the_admin_refuses_a_common_password_and_a_retired_username(admin: httpx.Client) -> None:
    name = unique("it-retired-")
    common = admin.post("/api/admin/users", json={"username": name, "password": "password123"})
    assert (common.status_code, common.json()["code"], common.json()["reason"]) == (422, "password_rejected", "common")
    created = check(admin.post("/api/admin/users", json={"username": name, "password": password()}), 201).json()
    check(admin.delete(f"/api/admin/users/{created['id']}"), 204)

    retired = admin.post("/api/admin/users", json={"username": name, "password": password()})

    assert (retired.status_code, retired.json()["code"]) == (409, "username_retired")
    assert sql_value(f"SELECT count(*) FROM username_tombstones WHERE username = '{name}'") == "1"


def test_the_signed_out_pages_send_their_headers() -> None:
    """nginx (section 12): the Turnstile CSP on the three pages only, no Referer from the token pages, and HSTS."""
    plain = httpx.get(f"{APP_URL}/", timeout=10)
    for page in ("/login", "/signup", "/forgot-password"):
        response = httpx.get(f"{APP_URL}{page}", timeout=10)
        assert "https://challenges.cloudflare.com" in response.headers["content-security-policy"], page
        assert response.headers["strict-transport-security"] == "max-age=31536000"
    for page in ("/verify-email", "/confirm-email", "/reset-password", "/undo-email"):
        response = httpx.get(f"{APP_URL}{page}", timeout=10)
        assert response.headers["referrer-policy"] == "no-referrer", page
        assert "challenges.cloudflare.com" not in response.headers["content-security-policy"]
    for response in (
        plain,
        httpx.get(f"{APP_URL}/plans", timeout=10),
        httpx.get(f"{APP_URL}/signup/google", timeout=10),
    ):
        assert "challenges.cloudflare.com" not in response.headers["content-security-policy"]
        assert response.headers["strict-transport-security"] == "max-age=31536000"
    assert httpx.get(f"{APP_URL}/api/health", timeout=10).headers["strict-transport-security"] == "max-age=31536000"
    callback = httpx.get(f"{APP_URL}/api/auth/google/callback?state=x&code=y", follow_redirects=False, timeout=10)
    assert callback.status_code == 303 and callback.headers["location"].endswith("/login?google=expired")
    assert parse_qs(urlsplit(callback.headers["location"]).query) == {"google": ["expired"]}


def test_nginx_backstops_cover_the_public_auth_endpoints_and_checkout() -> None:
    """nginx's per-address zones (section 12) take in the token pages' POSTs and checkout. The test stack runs them in
    dry-run mode (compose.it.yml), so nginx logs the requests it would refuse instead of refusing them."""
    ip = network()
    client = browser(ip)
    for _ in range(60):
        client.post("/api/auth/signup/check", json={"token": "x"})
    for _ in range(20):
        client.post("/api/billing/checkout", json={"plan": "starter"})
    logs = compose("logs", "--no-color", "frontend", timeout=120)
    for zone in ("auth_public", "billing_checkout"):
        refused = rf'limiting requests, dry run, excess: [\d.]+ by zone "{zone}", client: {re.escape(ip)},'
        assert re.search(refused, logs), zone
