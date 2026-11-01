"""Link tokens and step-up grants (app.services.auth.tokens and stepup; docs/briefs/signup-auth.md S7, 4.5, 8.3):
256-bit tokens of which only hashes are stored, their lifetimes, single use, the binding to the address they were
mailed to, revocation, the fragment links on the first https origin, and the 5-minute grant of one session. The flows
that spend and revoke them are tested with their routes."""

import hashlib
import re
from datetime import timedelta

import httpx
import jwt
import pytest
from fastapi import FastAPI
from google_oidc import GoogleOidc
from sqlalchemy import select, text, update

from app import db
from app.config import settings
from app.models import AuthToken, PendingEmail, PendingSignup, User, utcnow
from app.redis_client import get_redis
from app.services.auth import emails, stepup, tokens

ADDRESS = "alice@example.com"


async def add_user(username: str = "smoke-alice") -> int:
    async with db.SessionLocal() as session:
        user = User(username=username, password_hash="x", is_admin=False)
        session.add(user)
        await session.commit()
        return user.id


async def add_pending(user_id: int) -> tuple[int, int]:
    """A pending sign-up for ADDRESS and a pending email of the user; their ids."""
    now = utcnow()
    async with db.SessionLocal() as session:
        signup = PendingSignup(
            address=ADDRESS,
            canonical=ADDRESS,
            password_hash="x",
            terms_version="2026-11-01",
            terms_accepted_at=now,
            created_at=now,
            expires_at=now + timedelta(days=7),
        )
        pending = PendingEmail(
            user_id=user_id, address=ADDRESS, canonical=ADDRESS, source="user", expires_at=now + timedelta(days=1)
        )
        session.add_all([signup, pending])
        await session.commit()
        return signup.id, pending.id


async def mint(purpose: tokens.TokenPurpose, canonical: str = ADDRESS, **owners: int) -> str:
    async with db.SessionLocal() as session:
        token = await tokens.mint(session, purpose, address_canonical=canonical, **owners)
        await session.commit()
        return token


async def find(token: str, *purposes: tokens.TokenPurpose) -> AuthToken | None:
    async with db.SessionLocal() as session:
        return await tokens.find(session, purposes or tuple(tokens.TTL), token)


async def rows() -> list[AuthToken]:
    async with db.SessionLocal() as session:
        return list(await session.scalars(select(AuthToken).order_by(AuthToken.id)))


async def test_a_token_is_256_random_bits_of_which_only_hashes_are_kept(app: FastAPI) -> None:
    user_id = await add_user()

    token = await mint("reset", user_id=user_id)
    other = await mint("reset", user_id=user_id)

    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", token) and token != other
    stored = (await rows())[0]
    assert stored.token_sha256 == hashlib.sha256(token.encode()).hexdigest()
    assert stored.address_sha256 == emails.digest(ADDRESS)
    async with db.engine.connect() as connection:
        dumped = str((await connection.execute(text("SELECT * FROM auth_tokens"))).all())
    assert token not in dumped and ADDRESS not in dumped


@pytest.mark.parametrize(
    ("purpose", "lifetime"),
    [
        ("signup", timedelta(hours=24)),
        ("email", timedelta(hours=24)),
        ("reset", timedelta(minutes=30)),
        ("email_undo", timedelta(days=7)),
    ],
)
async def test_each_purpose_has_its_lifetime(app: FastAPI, purpose: tokens.TokenPurpose, lifetime: timedelta) -> None:
    user_id = await add_user()

    await mint(purpose, user_id=user_id)

    stored = (await rows())[0]
    assert stored.purpose == purpose and stored.used_at is None
    assert stored.expires_at - stored.created_at == lifetime


async def test_find_takes_only_an_unused_unexpired_token_of_the_purposes_asked(app: FastAPI) -> None:
    user_id = await add_user()
    token = await mint("reset", user_id=user_id)

    found = await find(token, "reset")
    assert found is not None and found.user_id == user_id
    assert await find(token, "email", "signup") is None
    assert await find("x" * 43) is None and await find("") is None
    async with db.SessionLocal() as session:
        locked = await tokens.find(session, ["reset", "email"], token, lock=True)
        assert locked is not None and locked.id == found.id

    async with db.SessionLocal() as session:
        await session.execute(update(AuthToken).values(expires_at=utcnow() - timedelta(seconds=1)))
        await session.commit()
    assert await find(token) is None


async def test_a_token_is_spent_once(app: FastAPI) -> None:
    user_id = await add_user()
    token = await mint("reset", user_id=user_id)

    async with db.SessionLocal() as session:
        row = await tokens.find(session, ["reset"], token, lock=True)
        assert row is not None
        await tokens.spend(session, row)
        await session.commit()

    assert await find(token) is None
    assert (await rows())[0].used_at is not None


async def test_a_token_is_bound_to_the_address_it_was_mailed_to(app: FastAPI) -> None:
    user_id = await add_user()
    token = await mint("reset", user_id=user_id)
    row = await find(token)
    assert row is not None

    assert tokens.address_matches(row, ADDRESS)
    assert not tokens.address_matches(row, "Alice@example.com")  # canonical forms are compared
    assert not tokens.address_matches(row, "alice@example.org")


async def test_revoke_ends_the_owners_unused_tokens_of_the_purposes_given(app: FastAPI) -> None:
    alice, bob = await add_user(), await add_user("smoke-bob")
    signup_id, pending_email_id = await add_pending(alice)
    reset = await mint("reset", user_id=alice)
    confirm = await mint("email", user_id=alice, pending_email_id=pending_email_id)
    undo = await mint("email_undo", "alice@old.example", user_id=alice)
    bobs = await mint("reset", user_id=bob)
    verification = await mint("signup", pending_signup_id=signup_id)

    async with db.SessionLocal() as session:
        await tokens.revoke(session, ["reset", "email"], user_id=alice)
        await session.commit()

    assert await find(reset) is None and await find(confirm) is None
    assert await find(undo) is not None, "an undo link survives later changes and resets"
    assert await find(bobs) is not None and await find(verification) is not None

    async with db.SessionLocal() as session:
        await tokens.revoke(session, ["signup"], pending_signup_id=signup_id)
        await tokens.revoke(session, ["email_undo"], user_id=alice, pending_email_id=pending_email_id)
        await session.commit()
    assert await find(verification) is None
    assert await find(undo) is not None, "every owner given must match"


async def test_revoke_needs_an_owner(app: FastAPI) -> None:
    async with db.SessionLocal() as session:
        with pytest.raises(ValueError):
            await tokens.revoke(session, ["reset"])


async def test_has_live_finds_an_unused_unexpired_token_of_the_user(app: FastAPI) -> None:
    alice = await add_user()

    async with db.SessionLocal() as session:
        assert not await tokens.has_live(session, "email_undo", user_id=alice)
    undo = await mint("email_undo", user_id=alice)
    async with db.SessionLocal() as session:
        assert await tokens.has_live(session, "email_undo", user_id=alice)
        assert not await tokens.has_live(session, "reset", user_id=alice)
        row = await tokens.find(session, ["email_undo"], undo)
        assert row is not None
        await tokens.spend(session, row)
        await session.commit()
        assert not await tokens.has_live(session, "email_undo", user_id=alice)


async def test_tokens_go_with_their_user_and_pending_rows(app: FastAPI) -> None:
    alice = await add_user()
    signup_id, pending_email_id = await add_pending(alice)
    await mint("reset", user_id=alice)
    await mint("signup", pending_signup_id=signup_id)
    await mint("email", pending_email_id=pending_email_id)

    async with db.SessionLocal() as session:
        await session.delete(await session.get(PendingSignup, signup_id))
        await session.commit()
    assert [row.purpose for row in await rows()] == ["reset", "email"]
    async with db.SessionLocal() as session:
        await session.delete(await session.get(User, alice))
        await session.commit()
    assert await rows() == []


@pytest.mark.parametrize(
    ("bases", "base"),
    [
        ("https://asknuvexusdoc.com,http://localhost:8080", "https://asknuvexusdoc.com"),
        ("http://localhost:8080,https://asknuvexusdoc.com", "https://asknuvexusdoc.com"),  # https first, any order
        ("http://localhost:8080, http://127.0.0.1:8080", "http://localhost:8080"),
        ("", "http://localhost:8080"),
    ],
)
def test_links_carry_the_token_in_the_fragment_on_the_first_https_origin(
    monkeypatch: pytest.MonkeyPatch, bases: str, base: str
) -> None:
    monkeypatch.setattr(settings, "public_base_urls", bases)

    assert tokens.base_url() == base
    assert {purpose: tokens.link(purpose, "T0k3n") for purpose in tokens.TTL} == {
        "signup": f"{base}/verify-email#t=T0k3n",
        "email": f"{base}/confirm-email#t=T0k3n",
        "reset": f"{base}/reset-password#t=T0k3n",
        "email_undo": f"{base}/undo-email#t=T0k3n",
    }


# ---------------- Step-up grants (app.services.auth.stepup, 4.5) ----------------


async def test_a_step_up_grant_lasts_5_minutes_for_one_session_and_is_spent_once() -> None:
    expires = await stepup.grant(7, 41)

    assert timedelta(seconds=299) < expires - utcnow() <= timedelta(seconds=300)
    until = await stepup.until(7, 41)
    assert until is not None and abs((until - expires).total_seconds()) < 2
    assert await stepup.until(7, 42) is None and await stepup.until(8, 41) is None, "only the session that asked"
    assert not await stepup.spend(7, 42)

    assert await stepup.spend(7, 41)
    assert not await stepup.spend(7, 41)
    assert await stepup.until(7, 41) is None


async def test_a_session_without_a_sid_never_holds_a_grant() -> None:
    await stepup.grant(7, 41)

    assert await stepup.until(7, None) is None
    assert not await stepup.spend(7, None)
    assert await get_redis().ttl("auth:stepup:7:41") > 0


# ---------------- Google's ID tokens as the test fake mints them (tests/google_oidc.py) ----------------


async def test_the_fakes_id_tokens_verify_against_its_jwks(google_oidc: GoogleOidc) -> None:
    async with httpx.AsyncClient() as client:
        jwks = (await client.get(settings.google_signin_jwks_url)).json()
    token = google_oidc.id_token(nonce="n-1", email="someone@example.com", hd="example.com")

    key = jwt.PyJWKSet.from_dict(jwks)[jwt.get_unverified_header(token)["kid"]]
    claims = jwt.decode(token, key.key, algorithms=["RS256"], audience=settings.google_signin_client_id)

    assert (claims["iss"], claims["sub"], claims["nonce"], claims["hd"]) == (
        "https://accounts.google.com",
        "100000000000000000001",
        "n-1",
        "example.com",
    )
    assert google_oidc.jwks_requests == 1
    assert "email_verified" not in google_oidc.claims(email_verified=None)


async def test_a_rotated_key_is_published_beside_the_old_one(google_oidc: GoogleOidc) -> None:
    google_oidc.rotate("key-2")

    assert [key["kid"] for key in google_oidc.jwks()["keys"]] == ["key-1", "key-2"]
    assert jwt.get_unverified_header(google_oidc.id_token())["kid"] == "key-2"
    forged = google_oidc.id_token(key="a-shared-secret-of-32-characters!", algorithm="HS256")
    assert jwt.get_unverified_header(forged)["alg"] == "HS256"
