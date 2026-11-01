"""Throttles and the robot check (app.services.auth.throttle and turnstile; docs/briefs/signup-auth.md 7.2 and 8.1):
networks and wide networks, local requests, the per-network and per-user limits, the silent per-recipient limits by
delivery key, the sign-in counters with their challenge, and Turnstile's siteverify. The routes that apply them are
tested with the flows."""

import logging
import time

import pytest
from fake_turnstile import REAL_SECRET, REAL_SITE_KEY, FakeTurnstile, turnstile_token
from fastapi import Request

from app.config import settings
from app.errors import AppError
from app.redis_client import get_redis
from app.services.auth import emails, throttle, turnstile

PUBLIC = "https://asknuvexusdoc.com,http://localhost:8080"


@pytest.fixture(autouse=True)
def bases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "public_base_urls", PUBLIC)


def request(ip: str | None = "203.0.113.7", host: str = "asknuvexusdoc.com") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/auth/login",
            "query_string": b"",
            "headers": [(b"host", host.encode())],
            "client": (ip, 50_000) if ip is not None else None,
        }
    )


@pytest.fixture
def turnstile_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "turnstile_site_key", "0x4AAAAAAAsite")
    monkeypatch.setattr(settings, "turnstile_secret_key", "0x4AAAAAAAsecret")


async def refused(coro: object) -> AppError:
    with pytest.raises(AppError) as error:
        await coro  # type: ignore[misc]
    assert (error.value.status_code, error.value.code) == (429, "too_many_attempts")
    return error.value


# ---------------- Networks ----------------


@pytest.mark.parametrize(
    ("ip", "net", "wide"),
    [
        ("203.0.113.7", "203.0.113.7", "203.0.113.0/24"),
        ("2001:db8:1:2:3:4:5:6", "2001:db8:1:2::/64", "2001:db8:1::/48"),
        ("2001:db8:1:2:ffff::1", "2001:db8:1:2::/64", "2001:db8:1::/48"),
        ("::ffff:198.51.100.9", "198.51.100.9", "198.51.100.0/24"),  # IPv4-mapped counts as IPv4
        ("testclient", "testclient", "testclient"),
        ("unknown", "unknown", "unknown"),
    ],
)
def test_networks_and_wide_networks(ip: str, net: str, wide: str) -> None:
    assert (throttle.network(ip), throttle.wide_network(ip)) == (net, wide)


def test_the_client_address_is_the_one_nginx_passed_on() -> None:
    assert throttle.client_ip(request("198.51.100.4")) == "198.51.100.4"
    assert throttle.client_ip(request(None)) == "unknown"


@pytest.mark.parametrize(
    ("ip", "host", "local"),
    [
        ("127.0.0.1", "localhost:8080", True),
        ("127.0.0.1", "localhost", True),  # nginx passes $host, without the port
        ("172.18.0.1", "localhost", True),  # Docker's network
        ("::1", "localhost:8080", True),
        ("172.18.0.1", "asknuvexusdoc.com", False),  # the tunnel without NGINX_REAL_IP_FROM: the public host
        ("93.184.215.14", "localhost:8080", False),  # a public address
        ("127.0.0.1", "evil.example", False),
        ("127.0.0.1", "", False),
        ("testclient", "localhost", False),
    ],
)
def test_a_local_request_is_a_private_client_asking_for_a_non_https_base(ip: str, host: str, local: bool) -> None:
    assert throttle.is_local(request(ip, host)) is local


def test_an_https_only_base_has_no_local_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "public_base_urls", "https://asknuvexusdoc.com")

    assert not throttle.is_local(request("127.0.0.1", "asknuvexusdoc.com"))


# ---------------- Limits per network and per user ----------------


async def test_a_network_limit_answers_429_with_retry_after_and_doesnt_count_refusals() -> None:
    for _ in range(10):
        await throttle.check(request(), "forgot")

    error = await refused(throttle.check(request(), "forgot"))

    assert error.detail == "Too many attempts. Try again in 60 minutes."
    assert 3590 <= int(error.headers["Retry-After"]) <= 3600  # type: ignore[index]
    assert await get_redis().get("auth:limit:forgot:3600:net:203.0.113.7") == "10"
    await throttle.check(request("203.0.113.8"), "forgot")  # another network of the same /24
    await throttle.check(request(), "resend")  # another rule


async def test_the_wide_network_allows_four_times_the_limit() -> None:
    for host in range(1, 5):
        for _ in range(10):
            await throttle.check(request(f"203.0.113.{host}"), "resend")

    await refused(throttle.check(request("203.0.113.99"), "resend"))
    await throttle.check(request("203.0.114.1"), "resend")


async def test_ipv6_addresses_of_one_64_share_a_limit() -> None:
    for suffix in range(30):
        await throttle.check(request(f"2001:db8:1:2::{suffix:x}"), "token")

    await refused(throttle.check(request("2001:db8:1:2:aaaa::1"), "token"))
    await throttle.check(request("2001:db8:1:3::1"), "token")


async def test_signup_has_an_hourly_and_a_daily_window() -> None:
    redis = get_redis()
    for _ in range(20):
        await throttle.check(request(), "signup")
    error = await refused(throttle.check(request(), "signup"))
    assert int(error.headers["Retry-After"]) <= 3600  # type: ignore[index]

    await redis.delete("auth:limit:signup:3600:net:203.0.113.7")
    await redis.set("auth:limit:signup:86400:net:203.0.113.7", "50", ex=86_400)
    error = await refused(throttle.check(request(), "signup"))
    assert error.detail == "Too many attempts. Try again in 24 hours."


async def test_google_start_is_limited_per_minute() -> None:
    for _ in range(20):
        await throttle.check(request(), "google_start")

    error = await refused(throttle.check(request(), "google_start"))

    assert error.detail == "Too many attempts. Try again in 1 minute."


async def test_email_changes_are_limited_per_user_with_less_for_unpaid_accounts() -> None:
    for _ in range(3):
        await throttle.check(request(), "email_change", subject="7", unpaid=True)
    await refused(throttle.check(request(), "email_change", subject="7", unpaid=True))

    await throttle.check(request(), "email_change", subject="7")  # once it has paid: 5
    await throttle.check(request(), "email_change", subject="7")
    await refused(throttle.check(request(), "email_change", subject="7"))
    await throttle.check(request(), "email_change", subject="8")


async def test_the_admin_test_mail_is_limited_per_hour() -> None:
    for _ in range(5):
        await throttle.check(request(), "mail_test", subject="admin")

    await refused(throttle.check(request("198.51.100.1"), "mail_test", subject="admin"))


async def test_a_rule_is_either_per_network_or_per_user() -> None:
    with pytest.raises(ValueError):
        await throttle.check(request(), "signup", subject="7")
    with pytest.raises(ValueError):
        await throttle.check(request(), "email_change")


# ---------------- Per recipient (silent) ----------------


async def test_mails_per_recipient_one_a_minute_three_an_hour_six_a_day() -> None:
    redis = get_redis()
    key = emails.parse("john@example.com").delivery_key
    hashed = emails.digest(key)

    assert await throttle.address_allowed("signup", key)
    assert not await throttle.address_allowed("signup", key)  # within the minute
    for _ in range(2):
        await redis.delete(f"auth:addr:signup:60:{hashed}")
        assert await throttle.address_allowed("signup", key)
    await redis.delete(f"auth:addr:signup:60:{hashed}")
    assert not await throttle.address_allowed("signup", key)  # the hour's 3
    assert await redis.get(f"auth:addr:signup:3600:{hashed}") == "3", "a refusal isn't counted"

    await redis.set(f"auth:addr:signup:3600:{hashed}", "0")
    await redis.set(f"auth:addr:signup:86400:{hashed}", "6")
    assert not await throttle.address_allowed("signup", key)  # the day's 6


async def test_each_kind_has_its_own_bucket_and_variants_share_one() -> None:
    first = emails.parse("j.ohn+1@GMail.com").delivery_key
    variant = emails.parse("john@googlemail.com").delivery_key

    assert await throttle.address_allowed("signup", first)
    assert not await throttle.address_allowed("signup", variant)
    assert await throttle.address_allowed("reset", variant)  # a stranger's sign-ups don't use up the resets
    assert await throttle.address_allowed("confirm", variant)
    assert await throttle.address_allowed("signup", emails.parse("jane@gmail.com").delivery_key)


async def test_recipient_keys_hold_hashes_never_addresses() -> None:
    await throttle.address_allowed("reset", "john@example.com")

    keys = [key async for key in get_redis().scan_iter("auth:addr:*")]
    assert keys and not any("john" in key or "example" in key for key in keys)


# ---------------- Sign-in ----------------


async def fail(req: Request, identifier: str, times: int = 1) -> None:
    for _ in range(times):
        assert await throttle.login_wait(req, identifier) is None
        await throttle.login_failed(req, identifier)


async def test_the_identifier_gets_the_hard_limit_of_failures() -> None:
    account = throttle.account_key(7)
    await fail(request(), account, settings.login_max_attempts)

    wait = await throttle.login_wait(request("198.51.100.1"), account)  # from any network

    assert wait is not None and 0 < wait <= settings.login_window_seconds
    assert await throttle.login_wait(request(), "user:someone-else") is None


async def test_the_network_and_the_wide_network_get_their_hard_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "login_max_attempts_per_ip", 4)
    monkeypatch.setattr(settings, "login_challenge_after_per_ip", 4)
    for number in range(4):
        await fail(request(), f"user:guess{number}")
    assert await throttle.login_wait(request(), "user:another") is not None
    assert await throttle.login_wait(request("203.0.113.8"), "user:another") is None

    for host in range(8, 20):
        await fail(request(f"203.0.113.{host}"), f"user:guess{host}")
    assert await throttle.login_wait(request("203.0.113.200"), "user:another") is not None  # 16 in the /24
    assert await throttle.login_wait(request("203.0.114.1"), "user:another") is None


async def test_parallel_attempts_past_the_limit_are_refused_and_taken_back() -> None:
    account = throttle.account_key(7)
    await get_redis().set(f"auth:login:id:{account}", str(settings.login_max_attempts), ex=900)

    error = await refused(throttle.login_failed(request(), account))

    assert int(error.headers["Retry-After"]) > 0  # type: ignore[index]
    redis = get_redis()
    assert await redis.get(f"auth:login:id:{account}") == str(settings.login_max_attempts)
    assert await redis.get("auth:login:net:203.0.113.7") == "0"
    assert await redis.hget(f"auth:login:idnet:{account}", "203.0.113.7") == "0"


async def test_success_clears_the_identifier_and_takes_the_attempt_back_from_the_network() -> None:
    account, req = throttle.account_key(7), request()
    await fail(req, account, 2)
    await fail(req, "user:other")
    await throttle.login_failed(req, account)  # the successful attempt, counted before its password was checked

    await throttle.login_cleared(req, account)

    redis = get_redis()
    assert await redis.get(f"auth:login:id:{account}") is None
    assert not await redis.exists(f"auth:login:idnet:{account}")
    assert await redis.get("auth:login:net:203.0.113.7") == "3"  # the earlier failures stay
    assert await redis.get("auth:login:site") == "3"


async def test_forgetting_failures_clears_every_network(turnstile_on: None) -> None:
    account = throttle.account_key(7)
    await fail(request(), account, 3)
    await fail(request("198.51.100.1"), account, 3)

    await throttle.forget_login_failures(account)

    assert await throttle.login_wait(request(), account) is None
    assert not await throttle.login_challenge_due(request(), account)
    assert not await throttle.login_challenge_due(request("198.51.100.1"), account)


async def test_the_challenge_counts_the_identifiers_failures_per_network(turnstile_on: None) -> None:
    account = throttle.account_key(7)
    owner, stranger = request("198.51.100.1"), request()

    await fail(stranger, account, settings.login_challenge_after - 1)
    assert not await throttle.login_challenge_due(stranger, account)
    await fail(stranger, account)

    assert await throttle.login_challenge_due(stranger, account)
    assert not await throttle.login_challenge_due(owner, account), "a stranger's failures don't challenge the owner"


async def test_the_challenge_after_a_networks_failures(turnstile_on: None) -> None:
    for number in range(settings.login_challenge_after_per_ip):
        await fail(request(), f"user:guess{number}")

    assert await throttle.login_challenge_due(request(), "user:new")
    assert not await throttle.login_challenge_due(request("198.51.100.1"), "user:new")


async def test_the_challenge_after_a_wide_networks_failures(turnstile_on: None) -> None:
    for host in range(settings.login_challenge_after_per_ip * throttle.WIDE):
        await fail(request(f"203.0.113.{host}"), f"user:guess{host}")

    assert await throttle.login_challenge_due(request("203.0.113.250"), "user:new")
    assert not await throttle.login_challenge_due(request("203.0.114.1"), "user:new")


async def test_a_site_wide_surge_challenges_everyone_and_tells_the_monitor(
    turnstile_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "login_site_challenge_after", 10)
    assert not await throttle.site_challenge_recent()
    for host in range(10):
        await fail(request(f"198.51.{host}.1"), f"user:guess{host}")
    assert not await throttle.login_challenge_due(request("192.0.2.1"), "user:new")

    await fail(request("198.51.100.200"), "user:one-more")

    assert await throttle.login_challenge_due(request("192.0.2.1"), "user:new")
    assert await throttle.site_challenge_recent()
    assert 0 < await get_redis().ttl("auth:login:site") <= throttle.SITE_WINDOW


async def test_counters_exist_for_unknown_identifiers(turnstile_on: None) -> None:
    unknown = f"email:{emails.digest('nobody@example.com')}"

    await fail(request(), unknown, settings.login_challenge_after)

    assert await throttle.login_challenge_due(request(), unknown)


async def test_no_challenge_without_turnstile_or_for_local_requests(
    turnstile_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = throttle.account_key(7)
    local = request("127.0.0.1", "localhost")
    await fail(local, account, settings.login_challenge_after)
    assert not await throttle.login_challenge_due(local, account)

    await fail(request(), account, settings.login_challenge_after)
    assert await throttle.login_challenge_due(request(), account)
    monkeypatch.setattr(settings, "turnstile_secret_key", "")
    assert not await throttle.login_challenge_due(request(), account)


async def test_login_keys_hold_network_prefixes_and_identifier_keys_only() -> None:
    hashed = emails.digest("john@example.com")
    await fail(request("2001:db8:1:2::7"), f"email:{hashed}")

    keys = {key async for key in get_redis().scan_iter("auth:login:*")}

    assert keys == {
        f"auth:login:id:email:{hashed}",
        f"auth:login:idnet:email:{hashed}",
        "auth:login:net:2001:db8:1:2::/64",
        "auth:login:wide:2001:db8:1::/48",
        "auth:login:site",
    }


# ---------------- The robot check (app.services.auth.turnstile, 7.2) ----------------


async def challenge(token: str | None, action: str = "signup", remote_ip: str | None = "203.0.113.7") -> AppError:
    with pytest.raises(AppError) as error:
        await turnstile.verify(token, action, remote_ip)  # type: ignore[arg-type]
    return error.value


@pytest.fixture
def real_keys(monkeypatch: pytest.MonkeyPatch, fake_turnstile: FakeTurnstile) -> FakeTurnstile:
    monkeypatch.setattr(settings, "turnstile_site_key", REAL_SITE_KEY)
    monkeypatch.setattr(settings, "turnstile_secret_key", REAL_SECRET)
    return fake_turnstile


async def test_a_good_token_passes_once(fake_turnstile: FakeTurnstile) -> None:
    token = turnstile_token("signup")

    await turnstile.verify(token, "signup", "203.0.113.7")

    [form] = fake_turnstile.requests
    assert form["secret"] == settings.turnstile_secret_key and form["response"] == token
    assert form["remoteip"] == "203.0.113.7" and len(form["idempotency_key"]) == 36
    error = await challenge(token)  # Cloudflare's timeout-or-duplicate
    assert (error.status_code, error.code) == (400, "challenge_failed")
    assert error.detail == "The check expired or failed. Try again."


@pytest.mark.parametrize("token", ["fail:signup", "", None])
async def test_a_bad_or_missing_token_fails(fake_turnstile: FakeTurnstile, token: str | None) -> None:
    error = await challenge(token)

    assert (error.status_code, error.code) == (400, "challenge_failed")


async def test_no_remote_ip_is_sent_when_unknown(fake_turnstile: FakeTurnstile) -> None:
    await turnstile.verify(turnstile_token("reset"), "reset", None)

    assert "remoteip" not in fake_turnstile.requests[0]


async def test_with_real_keys_the_action_and_hostname_must_match(real_keys: FakeTurnstile) -> None:
    await turnstile.verify(turnstile_token("login"), "login", None)  # hostname: PUBLIC_BASE_URLS's first host

    assert (await challenge(turnstile_token("signup"), "resend")).code == "challenge_failed"
    assert (await challenge(real_keys.token("signup", hostname="evil.example"))).code == "challenge_failed"
    await turnstile.verify(real_keys.token("signup", hostname="localhost"), "signup", None)


async def test_test_secrets_skip_the_action_and_hostname(fake_turnstile: FakeTurnstile) -> None:
    await turnstile.verify(fake_turnstile.token("other", hostname="example.com"), "signup", None)


@pytest.mark.parametrize("outage", ["timeout", "500", "internal-error"])
async def test_one_retry_with_the_same_key_then_unavailable(
    fake_turnstile: FakeTurnstile, outage: str, caplog: pytest.LogCaptureFixture
) -> None:
    fake_turnstile.outage = outage  # type: ignore[assignment]
    token = turnstile_token("signup")

    with caplog.at_level(logging.WARNING, logger="app.services.auth.turnstile"):
        error = await challenge(token)

    assert (error.status_code, error.code, error.detail) == (
        503,
        "challenge_unavailable",
        "We couldn't check that you're not a robot. Try again in a minute.",
    )
    first, second = fake_turnstile.requests
    assert first == second, "the retry carries the same idempotency key"
    assert await turnstile.unavailable_last_hour() == 1
    assert token not in caplog.text


async def test_a_retry_that_gets_through_passes(fake_turnstile: FakeTurnstile) -> None:
    fake_turnstile.outage, fake_turnstile.outage_count = "500", 1

    await turnstile.verify(turnstile_token("signup"), "signup", None)

    assert len(fake_turnstile.requests) == 2
    assert await turnstile.unavailable_last_hour() == 0


async def test_a_refused_secret_is_our_problem_not_the_visitors(
    real_keys: FakeTurnstile, caplog: pytest.LogCaptureFixture
) -> None:
    real_keys.secret = "0x4AAAAAAArotated"  # rotated in Cloudflare's dashboard, not in .env

    with caplog.at_level(logging.ERROR, logger="app.services.auth.turnstile"):
        error = await challenge(turnstile_token("signup"))

    assert error.code == "challenge_unavailable"
    assert "Turnstile refused TURNSTILE_SECRET_KEY (invalid-input-secret)" in caplog.text
    assert REAL_SECRET not in caplog.text
    assert await turnstile.unavailable_last_hour() == 1


async def test_without_keys_nothing_is_asked(fake_turnstile: FakeTurnstile, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "turnstile_secret_key", "")

    assert not turnstile.configured()
    assert (await challenge(turnstile_token("signup"))).code == "challenge_unavailable"
    assert fake_turnstile.requests == []


async def test_the_monitor_counts_the_last_hour_only(fake_turnstile: FakeTurnstile) -> None:
    fake_turnstile.outage = "timeout"
    for _ in range(3):
        await challenge(turnstile_token("signup"))
    await get_redis().zadd("auth:turnstile:unavailable", {"old": time.time() - 3601})

    assert await turnstile.unavailable_last_hour() == 3
