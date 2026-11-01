"""Google sign-in (docs/briefs/signup-auth.md 4.5): the start (origin, next, the limit, the cookie, the authorize URL),
the callback (state, binding cookie, Google's errors, the code exchange, every ID-token check, the kinds and each row
of the decision table), the pending sign-up's ticket, finish and cancel, and that no Google token is kept."""

import base64
import json
import re
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

import httpx
import pytest
from conftest import ApiClient, OutsideHosts, open_signup, signup
from cryptography.hazmat.primitives.asymmetric import rsa
from fake_google import FakeGoogleSignin
from fake_turnstile import FakeTurnstile
from google_oidc import GoogleOidc
from signup_support import (
    ORIGIN,
    event_types,
    give_email,
    google_callback,
    google_fake,
    google_start,
    identity_of,
    jobs,
    landed,
    pending_email,
    pending_signups,
    query,
    set_cookies,
    user_row,
)
from sqlalchemy import event, select

from app import db
from app.config import settings
from app.db import Base
from app.deps import session_cookie_name
from app.models import LoginEvent, User, UserIdentity
from app.redis_client import get_redis
from app.services.auth import throttle
from app.services.auth.throttle import Limit, Window

CreateUser = Callable[..., Awaitable[ApiClient]]
NewClient = Callable[[], ApiClient]

GMAIL = "it-person@gmail.com"


@pytest.fixture
def google(google_oidc: GoogleOidc, outside_hosts: OutsideHosts) -> FakeGoogleSignin:
    return google_fake(google_oidc, outside_hosts)


@pytest.fixture
async def google_signup(google: FakeGoogleSignin) -> FakeGoogleSignin:
    """Google sign-in configured and Google sign-up open."""
    await open_signup(email=False)
    return google


async def linked_user(create_user: CreateUser, name: str, subject: str, address: str, **fields: Any) -> ApiClient:
    """A user with a verified email and a linked Google identity."""
    client = await create_user(name, **fields)
    assert client.user_id is not None
    await give_email(client.user_id, address)
    async with db.SessionLocal() as session:
        session.add(
            UserIdentity(
                user_id=client.user_id,
                provider="google",
                issuer="https://accounts.google.com",
                subject=subject,
                email_at_link=address,
                email_kind="gmail",
            )
        )
        await session.commit()
    return client


@contextmanager
def statements() -> Iterator[list[str]]:
    seen: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        seen.append(statement)

    event.listen(db.engine.sync_engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(db.engine.sync_engine, "before_cursor_execute", record)


# ---------------- Start ----------------


async def test_start_answers_googles_authorize_url_and_the_binding_cookie(
    client: ApiClient, google: FakeGoogleSignin
) -> None:
    response = await google_start(client, next="/plans", **{"from": "login"})

    assert response.status_code == 200, response.text
    url = response.json()["authorize_url"]
    assert url.startswith(settings.google_signin_authorize_url + "?")
    params = query(url)
    assert params["scope"] == "openid email"
    assert (params["response_type"], params["prompt"], params["code_challenge_method"]) == (
        "code",
        "select_account",
        "S256",
    )
    assert params["client_id"] == settings.google_signin_client_id
    assert params["redirect_uri"] == f"{ORIGIN}/api/auth/google/callback"
    assert len(params["state"]) == len(params["nonce"]) == 43 and len(params["code_challenge"]) == 43
    assert not {"access_type", "login_hint", "max_age", "include_granted_scopes"} & set(params)
    cookie = set_cookies(response)["rag_gsignin"]
    assert (cookie["path"], cookie["httponly"], cookie["samesite"].lower()) == (
        "/api/auth/google/callback",
        True,
        "lax",
    )
    assert cookie["max-age"] == "600" and len(cookie.value) == 43
    stored = await get_redis().keys("auth:google:*")
    assert len([key for key in stored if "jwks" not in key]) == 1


async def test_with_cookie_secure_the_binding_cookie_has_the_secure_prefix(
    client: ApiClient, google: FakeGoogleSignin, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "cookie_secure", True)

    cookie = set_cookies(await google_start(client))["__Secure-rag_gsignin"]

    assert cookie["secure"] is True


async def test_start_needs_google_sign_in_configured(client: ApiClient) -> None:
    response = await google_start(client)

    assert response.status_code == 503
    assert response.json() == {"detail": "Google sign-in isn't set up.", "code": "google_unavailable"}


@pytest.mark.parametrize("origin", [None, "null", "https://evil.example", "http://localhost:8081"])
async def test_start_needs_an_origin_of_public_base_urls(
    origin: str | None, client: ApiClient, google: FakeGoogleSignin
) -> None:
    headers = {"Origin": origin} if origin else {}

    response = await client.post("/api/auth/google/start", json={"intent": "signin"}, headers=headers)

    assert (response.status_code, response.json()) == (400, {"detail": f"Use {ORIGIN} to sign in with Google."})


async def test_start_is_limited_per_network(
    client: ApiClient, google: FakeGoogleSignin, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(throttle.RULES, "google_start", Limit(True, (Window(60, 2),)))
    for _ in range(2):
        assert (await google_start(client)).status_code == 200

    response = await google_start(client)

    assert (response.status_code, response.json()["code"]) == (429, "too_many_attempts")


async def test_link_and_reauth_need_a_session(client: ApiClient, google: FakeGoogleSignin) -> None:
    for intent in ("link", "reauth"):
        response = await google_start(client, intent=intent, password="x")
        assert (response.status_code, response.json()) == (401, {"detail": "Not authenticated"}), intent


async def test_the_env_admin_cant_link_or_step_up(admin_client: ApiClient, google: FakeGoogleSignin) -> None:
    for intent in ("link", "reauth"):
        response = await google_start(admin_client, intent=intent, password="x")
        assert (response.status_code, response.json()) == (400, {"detail": "The admin account is managed in .env"})


@pytest.mark.parametrize(
    "body", [{"intent": "other"}, {"intent": "signin", "from": "elsewhere"}, {"intent": "signin", "extra": 1}]
)
async def test_invalid_starts_get_422(body: dict[str, Any], client: ApiClient, google: FakeGoogleSignin) -> None:
    response = await client.post("/api/auth/google/start", json=body, headers={"Origin": ORIGIN})

    assert response.status_code == 422


# ---------------- The callback: the state, the cookie, Google's answer ----------------


async def test_a_callback_without_a_known_state_says_expired(client: ApiClient, google: FakeGoogleSignin) -> None:
    for path in ("/api/auth/google/callback", "/api/auth/google/callback?state=unknown&code=x"):
        response = await client.get(path)
        assert landed(response) == "/login?google=expired"
        assert set_cookies(response)["rag_gsignin"]["max-age"] == "0"


async def test_a_state_is_spent_once(client: ApiClient, google_signup: FakeGoogleSignin) -> None:
    started = await google_start(client)
    back = google_signup.consent(started.json()["authorize_url"])
    path = back.removeprefix(ORIGIN)

    first = await client.get(path)
    replayed = await client.get(path)

    assert landed(first) == "/signup/google"
    assert landed(replayed) == "/login?google=expired"


async def test_another_browser_cant_finish_the_flow(
    client: ApiClient, new_client: NewClient, google_signup: FakeGoogleSignin
) -> None:
    """Login CSRF: an attacker's callback URL fails in the victim's browser (no binding cookie)."""
    started = await google_start(client)
    back = google_signup.consent(started.json()["authorize_url"]).removeprefix(ORIGIN)

    response = await new_client().get(back)

    assert landed(response) == "/login?google=error"


async def test_access_denied_and_other_errors(client: ApiClient, google: FakeGoogleSignin) -> None:
    google.deny_next = True
    denied = await google_callback(client, google)
    started = await google_start(client)
    state = query(started.json()["authorize_url"])["state"]
    other = await client.get(f"/api/auth/google/callback?error=server_error&state={state}")

    assert landed(denied) == "/login?google=denied"
    assert landed(other) == "/login?google=error"


@pytest.mark.parametrize("status", [400, 401, 500])
async def test_a_failed_code_exchange_says_error(
    status: int, client: ApiClient, google_signup: FakeGoogleSignin
) -> None:
    google_signup.token_failures.append(status)

    assert landed(await google_callback(client, google_signup)) == "/login?google=error"


async def test_an_exchange_that_cant_reach_google_says_error(
    client: ApiClient, google_signup: FakeGoogleSignin, outside_hosts: OutsideHosts
) -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("fake", request=request)

    outside_hosts.route(settings.google_signin_token_url, timeout)

    assert landed(await google_callback(client, google_signup)) == "/login?google=error"


# ---------------- The ID token ----------------


def unsigned(claims: dict[str, Any]) -> str:
    def part(value: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'none', 'kid': 'key-1'})}.{part(claims)}."


@pytest.mark.parametrize(
    "mint",
    [
        pytest.param(lambda oidc, claims: oidc.id_token(key=rsa.generate_private_key(65537, 2048), **claims), id="key"),
        pytest.param(lambda oidc, claims: unsigned(oidc.claims(**claims)), id="alg none"),
        pytest.param(lambda oidc, claims: oidc.id_token(algorithm="HS256", key="a" * 32, **claims), id="HS256"),
        pytest.param(lambda oidc, claims: oidc.id_token(kid="never-published", **claims), id="unknown kid"),
        pytest.param(lambda oidc, claims: "not.a.jwt", id="garbage"),
    ],
)
async def test_tokens_not_signed_by_googles_published_keys_are_refused(
    mint: Callable[[GoogleOidc, dict[str, Any]], str],
    client: ApiClient,
    google_signup: FakeGoogleSignin,
    google_oidc: GoogleOidc,
) -> None:
    google_signup.mint = lambda claims: mint(google_oidc, claims)

    assert landed(await google_callback(client, google_signup)) == "/login?google=error"


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "https://evil.example"},
        {"aud": "another-client"},
        {"aud": ["another-client"]},
        {"aud": [], "azp": None},
        {"exp": -120},
        {"iat": 900},
        {"nonce": "another-nonce"},
        {"nonce": None},
        {"sub": None},
        {"sub": "x" * 256},
        {"email": None},
        {"email": "not an address"},
    ],
    ids=lambda claims: ",".join(f"{key}={str(value)[:12]}" for key, value in claims.items()),
)
async def test_every_id_token_check(claims: dict[str, Any], client: ApiClient, google_signup: FakeGoogleSignin) -> None:
    import time

    now = int(time.time())
    if "exp" in claims:
        claims = {"exp": now + claims["exp"], "iat": now - 3600}
    if "iat" in claims:
        claims = {"iat": now + claims["iat"]}
    google_signup.claims = claims

    assert landed(await google_callback(client, google_signup)) == "/login?google=error"


async def test_an_audience_list_needs_azp_to_be_us(
    client: ApiClient, google_signup: FakeGoogleSignin, google_oidc: GoogleOidc
) -> None:
    google_signup.claims = {"aud": [google_oidc.client_id, "another"], "azp": "another"}
    refused = await google_callback(client, google_signup)
    google_signup.claims = {"aud": [google_oidc.client_id, "another"], "azp": google_oidc.client_id}
    accepted = await google_callback(client, google_signup)

    assert landed(refused) == "/login?google=error"
    assert landed(accepted) == "/signup/google"


async def test_the_short_issuer_form_a_little_clock_skew_and_a_string_email_verified_pass(
    client: ApiClient, google_signup: FakeGoogleSignin
) -> None:
    import time

    google_signup.claims = {"iss": "accounts.google.com", "iat": int(time.time()) + 300, "email_verified": "true"}

    assert landed(await google_callback(client, google_signup)) == "/signup/google"
    assert (await client.get("/api/auth/google/pending")).json()["email_verified"] is True


async def test_the_keys_are_cached_and_fetched_again_for_an_unknown_kid(
    new_client: NewClient, google_signup: FakeGoogleSignin, google_oidc: GoogleOidc
) -> None:
    assert landed(await google_callback(new_client(), google_signup)) == "/signup/google"
    assert landed(await google_callback(new_client(), google_signup)) == "/signup/google"
    assert google_oidc.jwks_requests == 1

    google_oidc.rotate("key-2")

    assert landed(await google_callback(new_client(), google_signup)) == "/signup/google"
    assert google_oidc.jwks_requests == 2
    assert 3500 < await get_redis().ttl(next(key for key in await get_redis().keys("auth:google:jwks:*"))) <= 3600


async def test_unreachable_keys_say_error(
    client: ApiClient, google_signup: FakeGoogleSignin, outside_hosts: OutsideHosts
) -> None:
    outside_hosts.route(settings.google_signin_jwks_url, lambda request: httpx.Response(500, request=request))

    assert landed(await google_callback(client, google_signup)) == "/login?google=error"


@pytest.mark.parametrize(
    ("account", "verified"),
    [
        ({"email": "Someone@GMail.com", "email_verified": True}, True),
        ({"email": "someone@company.example", "email_verified": True, "hd": "company.example"}, True),
        ({"email": "someone@company.example", "email_verified": True}, False),
        ({"email": "someone@gmail.com", "email_verified": False}, False),
        ({"email": "someone@gmail.com", "email_verified": "yes"}, False),
        ({"email": "someone@gmail.com", "email_verified": None}, False),
    ],
    ids=["gmail", "workspace", "verified elsewhere", "unverified gmail", "odd value", "no claim"],
)
async def test_the_kind_of_address_decides_whether_google_vouches_for_it(
    account: dict[str, Any], verified: bool, client: ApiClient, google_signup: FakeGoogleSignin
) -> None:
    google_signup.choose(**account)

    assert landed(await google_callback(client, google_signup)) == "/signup/google"
    pending = (await client.get("/api/auth/google/pending")).json()
    assert pending["email_verified"] is verified
    assert pending["email"] == account["email"].split("@")[0] + "@" + account["email"].split("@")[1].lower()


# ---------------- The decision (intent signin) ----------------


async def test_a_linked_identity_signs_in(
    create_user: CreateUser, new_client: NewClient, google: FakeGoogleSignin
) -> None:
    alice = await linked_user(create_user, "alice", google.account.sub, GMAIL)
    browser = new_client()

    response = await google_callback(browser, google, next="/plans")

    assert landed(response) == "/plans"
    assert session_cookie_name() in set_cookies(response)
    assert (await browser.get("/api/auth/me")).json()["id"] == alice.user_id
    async with db.SessionLocal() as session:
        methods = list(await session.scalars(select(LoginEvent.method).where(LoginEvent.user_id == alice.user_id)))
    assert methods == ["password", "google"]
    identity = await identity_of(alice.user_id)  # type: ignore[arg-type]
    assert identity is not None and identity.last_used_at is not None


async def test_a_linked_identity_signs_in_whatever_its_address_now(
    create_user: CreateUser, client: ApiClient, google: FakeGoogleSignin
) -> None:
    alice = await linked_user(create_user, "alice", google.account.sub, GMAIL)
    google.choose(email="renamed@company.example", email_verified=False)

    response = await google_callback(client, google)

    assert landed(response) == "/"
    assert (await client.get("/api/auth/me")).json()["id"] == alice.user_id


async def test_an_inactive_users_identity_is_unavailable(
    create_user: CreateUser, client: ApiClient, google: FakeGoogleSignin
) -> None:
    await linked_user(create_user, "alice", google.account.sub, GMAIL, is_active=False, login=False)

    response = await google_callback(client, google)

    assert landed(response) == "/login?google=unavailable"
    assert session_cookie_name() not in set_cookies(response)


async def test_a_gmail_address_of_an_account_without_google_links_and_signs_in(
    create_user: CreateUser, new_client: NewClient, google: FakeGoogleSignin, mailer: None
) -> None:
    alice = await create_user("alice")
    assert alice.user_id is not None
    await give_email(alice.user_id, GMAIL)
    browser = new_client()

    response = await google_callback(browser, google)

    assert landed(response) == "/"
    assert (await browser.get("/api/auth/me")).json()["id"] == alice.user_id
    identity = await identity_of(alice.user_id)
    assert identity is not None
    assert (identity.subject, identity.email_kind, identity.email_at_link) == (google.account.sub, "gmail", GMAIL)
    assert "google.linked" in await event_types(alice.user_id)
    assert [(job.kind, job.to_address, job.params) for job in await jobs()] == [
        ("google_linked", GMAIL, {"google_email": "i***@gmail.com"})
    ]


@pytest.mark.parametrize("case", ["workspace", "has another google", "inactive"])
async def test_link_required_when_an_account_holds_the_address_otherwise(
    case: str, create_user: CreateUser, client: ApiClient, google_signup: FakeGoogleSignin
) -> None:
    address = "person@company.example" if case == "workspace" else GMAIL
    if case == "workspace":
        google_signup.choose(email=address, hd="company.example", sub="100000000000000000999")
    alice = await create_user("alice", is_active=case != "inactive", login=False)
    assert alice.user_id is not None
    await give_email(alice.user_id, address)
    if case == "has another google":
        await linked_user(create_user, "bob", "100000000000000000555", "bob@gmail.com")
        async with db.SessionLocal() as session:
            identity = await session.scalar(select(UserIdentity).where(UserIdentity.subject == "100000000000000000555"))
            assert identity is not None
            identity.user_id = alice.user_id
            await session.commit()

    response = await google_callback(client, google_signup)

    assert landed(response) == "/login?google=link_required"
    assert session_cookie_name() not in set_cookies(response)
    assert (await identity_of(alice.user_id)) is None or case == "has another google"


async def test_an_unverified_claim_is_never_looked_up_among_users(
    create_user: CreateUser, client: ApiClient, google_signup: FakeGoogleSignin
) -> None:
    alice = await create_user("alice")
    assert alice.user_id is not None
    await give_email(alice.user_id, "person@company.example")
    google_signup.choose(email="person@company.example", email_verified=True)  # no hd: unverified

    with statements() as seen:
        response = await google_callback(client, google_signup)

    assert landed(response) == "/signup/google"
    assert not [statement for statement in seen if "email_canonical" in statement]


async def test_google_sign_up_closed_says_so(
    create_user: CreateUser, client: ApiClient, google: FakeGoogleSignin
) -> None:
    await open_signup(google=False)

    assert landed(await google_callback(client, google)) == "/login?google=signup_closed"


# ---------------- The ticket: pending, finish, cancel ----------------


async def test_a_new_gmail_account_finishes_the_sign_up(
    client: ApiClient, google_signup: FakeGoogleSignin, mailer: None, fake_turnstile: FakeTurnstile
) -> None:
    await open_signup()
    assert (await signup(client, GMAIL)).status_code == 202  # an earlier email sign-up for the address
    response = await google_callback(client, google_signup, next="/plans/checkout/standard", **{"from": "login"})
    ticket = set_cookies(response)["rag_gticket"]
    assert landed(response) == "/signup/google"
    assert (ticket["path"], ticket["httponly"], ticket["samesite"].lower(), ticket["max-age"]) == (
        "/api/auth/google/",
        True,
        "strict",
        "900",
    )

    pending = await client.get("/api/auth/google/pending")
    finished = await client.post("/api/auth/google/finish", json={"accept_terms": True})

    assert pending.status_code == 200
    body = pending.json()
    assert (body["email"], body["email_verified"], body["from"]) == (GMAIL, True, "login")
    assert finished.status_code == 200, finished.text
    me = finished.json()
    assert (me["email"], me["plan"]["state"]) == (GMAIL, "none") and me["username"].startswith("user-")
    assert set_cookies(finished)["rag_gticket"]["max-age"] == "0"
    user = await user_row(me["id"])
    assert (user.signup_source, user.password_hash, user.email_canonical) == ("google", None, GMAIL)
    assert user.terms_version == settings.terms_version and user.terms_accepted_at is not None
    identity = await identity_of(me["id"])
    assert identity is not None and (identity.subject, identity.email_kind) == (google_signup.account.sub, "gmail")
    assert await pending_signups(GMAIL) == []
    assert "signup.completed" in await event_types(me["id"])
    async with db.SessionLocal() as session:
        [method] = await session.scalars(select(LoginEvent.method).where(LoginEvent.user_id == me["id"]))
    assert method == "google"
    assert (await client.get("/api/auth/google/pending")).json()["code"] == "link_invalid"


async def test_an_unverified_address_becomes_a_pending_email_with_a_confirmation(
    client: ApiClient, google_signup: FakeGoogleSignin, mailer: None
) -> None:
    google_signup.choose(email="Person@Company.example", email_verified=False)
    await google_callback(client, google_signup)

    finished = await client.post("/api/auth/google/finish", json={"accept_terms": True})

    assert finished.status_code == 200
    me = finished.json()
    assert me["email"] is None
    pending = await pending_email(me["id"])
    assert pending is not None
    assert (pending.address, pending.canonical, pending.source) == (
        "Person@company.example",
        "person@company.example",
        "google",
    )
    [job] = await jobs()
    assert (job.kind, job.to_address, job.user_id, job.pending_email_id) == (
        "email_confirm",
        "Person@company.example",
        me["id"],
        pending.id,
    )


async def test_an_unverified_addresss_confirmation_needs_the_days_room_and_keeps_the_per_address_limit(
    new_client: NewClient, google_signup: FakeGoogleSignin, mailer: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def sign_up(sub: str, email: str) -> int:
        google_signup.choose(sub=sub, email=email, email_verified=False)
        browser = new_client()
        await google_callback(browser, google_signup)
        finished = await browser.post("/api/auth/google/finish", json={"accept_terms": True})
        assert finished.status_code == 200, finished.text
        user_id: int = finished.json()["id"]
        assert await pending_email(user_id) is not None  # Settings → Account resends it later
        return user_id

    first = await sign_up("100000000000000000801", "person@company.example")
    await sign_up("100000000000000000802", "person@company.example")  # another Google account, the same mailbox
    monkeypatch.setattr(settings, "mail_daily_limit", 2)  # no room for `public` mail today
    await sign_up("100000000000000000803", "other@company.example")

    assert [(job.kind, job.user_id) for job in await jobs()] == [("email_confirm", first)]


async def test_finish_checks_everything_again(
    create_user: CreateUser, new_client: NewClient, google_signup: FakeGoogleSignin
) -> None:
    taken, closed, used = new_client(), new_client(), new_client()
    for browser in (taken, closed, used):
        assert landed(await google_callback(browser, google_signup)) == "/signup/google"
    assert (await used.post("/api/auth/google/finish", json={"accept_terms": True})).status_code == 200

    duplicate = await taken.post("/api/auth/google/finish", json={"accept_terms": True})

    assert duplicate.json()["code"] == "link_invalid"  # the identity has an account now

    google_signup.choose(sub="100000000000000000888", email="second@gmail.com")
    second = new_client()
    assert landed(await google_callback(second, google_signup)) == "/signup/google"
    bob = await create_user("bob")
    assert bob.user_id is not None
    await give_email(bob.user_id, "second@gmail.com")
    response = await second.post("/api/auth/google/finish", json={"accept_terms": True})
    assert response.status_code == 409
    assert response.json() == {
        "detail": "This address already has an account. Sign in with your password, then connect Google in Settings.",
        "code": "email_taken",
    }

    google_signup.choose(sub="100000000000000000666", email="third@gmail.com")
    assert landed(await google_callback(closed, google_signup)) == "/signup/google"
    await open_signup(google=False)
    response = await closed.post("/api/auth/google/finish", json={"accept_terms": True})
    assert (response.status_code, response.json()["code"]) == (403, "signup_closed")


async def test_finish_needs_the_terms_and_a_live_ticket(client: ApiClient, google_signup: FakeGoogleSignin) -> None:
    assert (await client.post("/api/auth/google/finish", json={"accept_terms": False})).status_code == 422
    assert (await client.post("/api/auth/google/finish", json={"accept_terms": True})).json()["code"] == "link_invalid"
    await google_callback(client, google_signup)
    for key in await get_redis().keys("auth:gticket:*"):
        await get_redis().delete(key)

    expired = await client.post("/api/auth/google/finish", json={"accept_terms": True})

    assert expired.json()["code"] == "link_invalid"


async def test_cancel_drops_the_ticket(client: ApiClient, google_signup: FakeGoogleSignin) -> None:
    await google_callback(client, google_signup)

    response = await client.post("/api/auth/google/cancel")

    assert response.status_code == 204
    assert set_cookies(response)["rag_gticket"]["max-age"] == "0"
    assert await get_redis().keys("auth:gticket:*") == []
    assert (await client.post("/api/auth/google/finish", json={"accept_terms": True})).json()["code"] == "link_invalid"


async def test_the_ticket_lives_fifteen_minutes(client: ApiClient, google_signup: FakeGoogleSignin) -> None:
    await google_callback(client, google_signup)

    [key] = await get_redis().keys("auth:gticket:*")

    assert 890 < await get_redis().ttl(key) <= 900
    pending = (await client.get("/api/auth/google/pending")).json()
    assert pending["from"] == "signup"


async def test_no_google_token_is_kept_anywhere(
    create_user: CreateUser, new_client: NewClient, google_signup: FakeGoogleSignin
) -> None:
    issued: list[str] = []
    mint = google_signup.mint

    def recording(claims: dict[str, Any]) -> str:
        issued.append(mint(claims))
        return issued[-1]

    google_signup.mint = recording
    browser = new_client()
    await google_callback(browser, google_signup)
    await browser.post("/api/auth/google/finish", json={"accept_terms": True})
    await google_callback(new_client(), google_signup)

    secrets = [*issued, *(request["code"] for request in google_signup.token_requests)]
    secrets += [answer for answer in ("ya29.fake-signin-",)]
    async with db.SessionLocal() as session:
        dump = ""
        for table in Base.metadata.sorted_tables:
            dump += json.dumps([list(map(str, row)) for row in await session.execute(select(table))])
    redis = get_redis()
    dump += json.dumps([await redis.get(key) for key in await redis.keys("*") if await redis.type(key) == "string"])
    assert issued and not [secret for secret in secrets if secret and secret in dump]
    assert not re.search(r"eyJ[A-Za-z0-9_-]{20,}\.", dump)


async def test_a_session_from_before_doesnt_survive_signing_in_as_someone_else(
    create_user: CreateUser, google: FakeGoogleSignin
) -> None:
    alice = await create_user("alice")
    bob = await linked_user(create_user, "bob", google.account.sub, GMAIL)

    response = await google_callback(alice, google)

    assert landed(response) == "/"
    assert (await alice.get("/api/auth/me")).json()["id"] == bob.user_id


async def test_the_callback_never_shows_an_error_page(
    client: ApiClient, google: FakeGoogleSignin, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.auth import google as google_service

    async def broken(*args: Any) -> None:
        raise RuntimeError("database down")

    monkeypatch.setattr(google_service, "_sign_in", broken)

    response = await google_callback(client, google)

    assert landed(response) == "/login?google=error"
    assert set_cookies(response)["rag_gsignin"]["max-age"] == "0"


async def test_the_pending_email_of_a_google_sign_up_expires_in_a_day(
    client: ApiClient, google_signup: FakeGoogleSignin
) -> None:
    google_signup.choose(email="someone@company.example", email_verified=False)
    await google_callback(client, google_signup)
    me = (await client.post("/api/auth/google/finish", json={"accept_terms": True})).json()

    pending = await pending_email(me["id"])

    assert pending is not None and pending.expires_at - pending.created_at == timedelta(hours=24)


async def test_users_signed_up_with_google_have_no_password_to_sign_in_with(
    client: ApiClient, new_client: NewClient, google_signup: FakeGoogleSignin
) -> None:
    await google_callback(client, google_signup)
    me = (await client.post("/api/auth/google/finish", json={"accept_terms": True})).json()

    response = await new_client().post("/api/auth/login", json={"identifier": GMAIL, "password": "anything-at-all"})

    assert response.status_code == 401
    async with db.SessionLocal() as session:
        user = await session.get(User, me["id"])
    assert user is not None and user.password_hash is None
