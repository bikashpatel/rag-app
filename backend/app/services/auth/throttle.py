"""Throttles (docs/briefs/signup-auth.md 8.1): Redis fixed windows per network (an IPv4 address, an IPv6 /64; the wide
network, an IPv4 /24 or IPv6 /48, at four times the limit), per user, per recipient (silent, by the delivery key's
hash), the security notices per account (silent) and the sign-in counters with their challenge. Keys hold network
prefixes, user ids and hashes, never an address."""

import ipaddress
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal
from urllib.parse import urlsplit

from fastapi import Request

from app.config import settings
from app.errors import AppError
from app.redis_client import get_redis
from app.services.auth import emails

Rule = Literal[
    "signup", "resend", "forgot", "token", "google_start", "email_change", "mail_test", "upi_unpaid", "name_change"
]
AddressKind = Literal["signup", "reset", "confirm"]

MINUTE, HOUR, DAY = 60, 3600, 86_400
WIDE: Final = 4  # a wide network gets four times a network's limit
SITE_WINDOW: Final = 300  # the site-wide sign-in failures that turn the challenge on for everyone
SITE_CHALLENGE_SEEN: Final = 3600  # how long the monitor hears that the site-wide challenge was on


@dataclass(frozen=True)
class Window:
    seconds: int
    limit: int


@dataclass(frozen=True)
class Limit:
    per_network: bool  # else per `subject` (a user)
    windows: tuple[Window, ...]


RULES: Final[dict[Rule, Limit]] = {
    "signup": Limit(True, (Window(HOUR, 20), Window(DAY, 50))),  # sign-up, Google finish, unpaid email changes
    "resend": Limit(True, (Window(HOUR, 10),)),
    "forgot": Limit(True, (Window(HOUR, 10),)),
    "token": Limit(True, (Window(HOUR, 30),)),  # the token pages' POSTs and the Google ticket
    "google_start": Limit(True, (Window(MINUTE, 20),)),
    "email_change": Limit(False, (Window(DAY, 5),)),
    "mail_test": Limit(False, (Window(HOUR, 5),)),
    "upi_unpaid": Limit(True, (Window(HOUR, 10), Window(DAY, 30))),  # direct-UPI orders of unpaid accounts
    "name_change": Limit(False, (Window(HOUR, 10), Window(DAY, 30))),  # PUT /me/name
}
UNPAID_EMAIL_CHANGES: Final = 3  # a day, for an account that has never paid
ADDRESS_WINDOWS: Final = (Window(MINUTE, 1), Window(HOUR, 3), Window(DAY, 6))  # mails per recipient and kind
NOTICE_WINDOWS: Final = (Window(HOUR, 3), Window(DAY, 10))  # security notices per account


# ---------------- Addresses and networks ----------------


def client_ip(request: Request) -> str:
    """The client as nginx saw it (CF-Connecting-IP through the tunnel, with NGINX_REAL_IP_FROM)."""
    return request.client.host if request.client else "unknown"


def _address(ip: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        address = ipaddress.ip_address(ip.strip())
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def network(ip: str) -> str:
    """An IPv4 address itself, an IPv6 address's /64 (an IPv4-mapped one counts as IPv4); anything else as given."""
    address = _address(ip)
    if address is None:
        return ip
    if isinstance(address, ipaddress.IPv4Address):
        return str(address)
    return str(ipaddress.IPv6Network((int(address), 64), strict=False))


def wide_network(ip: str) -> str:
    """The IPv4 /24 or the IPv6 /48 around the address."""
    address = _address(ip)
    if address is None:
        return ip
    if isinstance(address, ipaddress.IPv4Address):
        return str(ipaddress.IPv4Network((int(address), 24), strict=False))
    return str(ipaddress.IPv6Network((int(address), 48), strict=False))


def _host(header: str) -> str | None:
    try:
        return urlsplit(f"//{header.strip()}").hostname
    except ValueError:
        return None


def is_local(request: Request) -> bool:
    """A loopback or private client that asked for a PUBLIC_BASE_URLS host that isn't https (localhost:8080). Through
    the tunnel the host is the public one, so a visitor can't pass as local."""
    from app.plugins import oauth  # it imports the plugin registry, which imports app.deps

    address = _address(client_ip(request))
    if address is None or not (address.is_loopback or address.is_private):
        return False
    host = _host(request.headers.get("host", ""))
    return host is not None and any(
        origin.startswith("http://") and urlsplit(origin).hostname == host for origin in oauth.public_base_urls()
    )


# ---------------- Counters ----------------


def _wait(seconds: int) -> str:
    minutes = math.ceil(seconds / 60)
    if minutes < 120:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    return f"{math.ceil(seconds / 3600)} hours"


def too_many_attempts(seconds: int) -> AppError:
    """429 too_many_attempts with Retry-After."""
    seconds = max(seconds, 1)
    return AppError(
        429,
        f"Too many attempts. Try again in {_wait(seconds)}.",
        "too_many_attempts",
        headers={"Retry-After": str(seconds)},
    )


async def _count(keys: Sequence[tuple[str, int, int]], amount: int = 1) -> list[tuple[int, int]]:
    """Adds `amount` to each (key, window, limit) counter; returns (count, seconds left) per key. A window starts with
    its counter's first change; later changes don't extend it."""
    async with get_redis().pipeline(transaction=True) as pipe:
        for key, seconds, _ in keys:
            pipe.incrby(key, amount)
            pipe.expire(key, seconds, nx=True)
            pipe.ttl(key)
        results = await pipe.execute()
    return list(zip(results[0::3], results[2::3], strict=True))


async def _take(keys: Sequence[tuple[str, int, int]]) -> int | None:
    """Counts one more on every key; over any limit, takes it back and returns the seconds to wait."""
    counted = await _count(keys)
    waits = [ttl for (_, _, limit), (count, ttl) in zip(keys, counted, strict=True) if count > limit]
    if not waits:
        return None
    await _count(keys, -1)
    return max(waits)


def _rule_keys(request: Request, rule: Rule, subject: str | None, unpaid: bool) -> list[tuple[str, int, int]]:
    limit = RULES[rule]
    if limit.per_network != (subject is None):
        raise ValueError(f"The {rule} limit is per {'network' if limit.per_network else 'user'}")
    keys: list[tuple[str, int, int]] = []
    ip = client_ip(request)
    for window in limit.windows:
        prefix = f"auth:limit:{rule}:{window.seconds}"
        if limit.per_network:
            keys.append((f"{prefix}:net:{network(ip)}", window.seconds, window.limit))
            keys.append((f"{prefix}:wide:{wide_network(ip)}", window.seconds, window.limit * WIDE))
        else:
            allowed = UNPAID_EMAIL_CHANGES if unpaid and rule == "email_change" else window.limit
            keys.append((f"{prefix}:user:{subject}", window.seconds, allowed))
    return keys


async def check(request: Request, rule: Rule, *, subject: str | None = None, unpaid: bool = False) -> None:
    """Counts the request under `rule` (per network, or per `subject`, a user id, for email_change, mail_test and
    name_change;
    `unpaid` gives an account that never paid its smaller email_change limit). Over the limit: 429 too_many_attempts
    with Retry-After, and the request isn't counted."""
    wait = await _take(_rule_keys(request, rule, subject, unpaid))
    if wait is not None:
        raise too_many_attempts(wait)


async def address_allowed(kind: AddressKind, delivery_key: str) -> bool:
    """Silent: whether one more mail of `kind` may go to this mailbox (1 a minute, 3 an hour, 6 a day); counts it when
    allowed. Callers answer as usual either way and only skip the job."""
    hashed = emails.digest(delivery_key)
    keys = [(f"auth:addr:{kind}:{w.seconds}:{hashed}", w.seconds, w.limit) for w in ADDRESS_WINDOWS]
    return await _take(keys) is None


async def notice_allowed(user_id: int) -> bool:
    """Silent: whether one more security notice may be queued for the account (3 an hour, 10 a day); counts it when
    allowed. The action it reports goes ahead either way."""
    keys = [(f"auth:notice:{w.seconds}:user:{user_id}", w.seconds, w.limit) for w in NOTICE_WINDOWS]
    return await _take(keys) is None


# ---------------- Sign-in (4.4) ----------------
# identifier_key: account_key(user id) when the identifier found a user (its email and username share it), else
# "email:<sha256 of the canonical address>" or "user:<username>". The attempt is counted before the password is checked
# (login_failed) and taken back from the network's counters on success (login_cleared).


def account_key(user_id: int) -> str:
    return f"acct:{user_id}"


def _login_keys(request: Request, identifier_key: str) -> tuple[str, str, str, str, str]:
    ip = client_ip(request)
    return (
        f"auth:login:id:{identifier_key}",
        f"auth:login:idnet:{identifier_key}",  # a hash: network -> failures, so one delete clears them all
        network(ip),
        f"auth:login:net:{network(ip)}",
        f"auth:login:wide:{wide_network(ip)}",
    )


_SITE = "auth:login:site"
_SITE_SEEN = "auth:login:site_challenge"


def _hard_limits() -> tuple[int, int, int]:
    return settings.login_max_attempts, settings.login_max_attempts_per_ip, settings.login_max_attempts_per_ip * WIDE


async def login_wait(request: Request, identifier_key: str) -> int | None:
    """Seconds until the identifier, the network or the wide network may try again (the hard limits), or None."""
    id_key, _, _, net_key, wide_key = _login_keys(request, identifier_key)
    async with get_redis().pipeline(transaction=False) as pipe:
        for key in (id_key, net_key, wide_key):
            pipe.get(key)
            pipe.ttl(key)
        results = await pipe.execute()
    waits = [
        ttl
        for count, ttl, limit in zip(results[0::2], results[1::2], _hard_limits(), strict=True)
        if int(count or 0) >= limit
    ]
    return max(*waits, 1) if waits else None


async def login_challenge_due(request: Request, identifier_key: str) -> bool:
    """Turnstile is configured, the request isn't local, and the identifier's failures from this network, the
    network's (or wide network's), or the site's in the last 5 minutes say a robot check comes first."""
    if not settings.turnstile_configured or is_local(request):
        return False
    _, idnet_key, net, net_key, wide_key = _login_keys(request, identifier_key)
    async with get_redis().pipeline(transaction=False) as pipe:
        pipe.hget(idnet_key, net)
        pipe.get(net_key)
        pipe.get(wide_key)
        pipe.get(_SITE)
        identifier, network_failures, wide_failures, site = (int(value or 0) for value in await pipe.execute())
    per_ip = settings.login_challenge_after_per_ip
    return (
        identifier >= settings.login_challenge_after
        or network_failures >= per_ip
        or wide_failures >= per_ip * WIDE
        or site > settings.login_site_challenge_after
    )


async def login_failed(request: Request, identifier_key: str) -> None:
    """Counts the attempt as failed before its password is checked (identifier, identifier from this network,
    network, wide network, site), so parallel guesses can't all pass the limit. If a parallel attempt got there first
    and this one is over a hard limit, it is taken back and 429 too_many_attempts raised."""
    id_key, idnet_key, net, net_key, wide_key = _login_keys(request, identifier_key)
    window = settings.login_window_seconds
    redis = get_redis()
    async with redis.pipeline(transaction=True) as pipe:
        pipe.hincrby(idnet_key, net, 1)
        pipe.expire(idnet_key, window, nx=True)
        pipe.incrby(_SITE, 1)
        pipe.expire(_SITE, SITE_WINDOW, nx=True)
        _, _, site, _ = await pipe.execute()
    if site > settings.login_site_challenge_after:
        await redis.set(_SITE_SEEN, "1", ex=SITE_CHALLENGE_SEEN)
    hard = list(zip((id_key, net_key, wide_key), (window,) * 3, _hard_limits(), strict=True))
    wait = await _take(hard)
    if wait is not None:
        async with redis.pipeline(transaction=True) as pipe:
            pipe.hincrby(idnet_key, net, -1)
            pipe.incrby(_SITE, -1)
            await pipe.execute()
        raise too_many_attempts(wait)


async def login_cleared(request: Request, identifier_key: str) -> None:
    """After a successful sign-in: the identifier's failures are forgotten, and this attempt is taken back from the
    network's and the site's counters (their earlier failures stay)."""
    _, _, _, net_key, wide_key = _login_keys(request, identifier_key)
    await forget_login_failures(identifier_key)
    window = settings.login_window_seconds
    await _count([(net_key, window, 0), (wide_key, window, 0), (_SITE, SITE_WINDOW, 0)], -1)


async def forget_login_failures(identifier_key: str) -> None:
    """Clears the identifier's failures from every network (a password reset, the admin's reset, a first password)."""
    await get_redis().delete(f"auth:login:id:{identifier_key}", f"auth:login:idnet:{identifier_key}")


async def site_challenge_recent() -> bool:
    """The site-wide sign-in challenge was on in the last hour (the `mail` monitor check warns)."""
    return bool(await get_redis().exists(_SITE_SEEN))
