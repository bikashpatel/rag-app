"""Identifiers (app.services.auth.emails and usernames; docs/briefs/signup-auth.md 3.2 and 3.3): parsing addresses
and their canonical form, the delivery key that groups a mailbox's variants for throttles, digests and masking; random
usernames and the tombstones of deleted ones."""

import hashlib
import re

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app import db
from app.models import User, UsernameTombstone
from app.services.auth import emails, usernames
from app.services.auth.emails import Email, EmailError


@pytest.mark.parametrize(
    ("value", "address", "canonical"),
    [
        ("john@example.com", "john@example.com", "john@example.com"),
        ("  John.Smith@Example.COM \n", "John.Smith@example.com", "john.smith@example.com"),
        ("o'neil+news@sub.example.co.uk", "o'neil+news@sub.example.co.uk", "o'neil+news@sub.example.co.uk"),
        ("a!#$%&'*+/=?^_`{|}~-@example.org", "a!#$%&'*+/=?^_`{|}~-@example.org", "a!#$%&'*+/=?^_`{|}~-@example.org"),
        ("me@bücher.de", "me@xn--bcher-kva.de", "me@xn--bcher-kva.de"),
        ("me@ＥＸＡＭＰＬＥ.com", "me@example.com", "me@example.com"),
        ("me@straße.de", "me@xn--strae-oqa.de", "me@xn--strae-oqa.de"),  # UTS 46 without the transitional mapping
        ("me@xn--bcher-kva.de", "me@xn--bcher-kva.de", "me@xn--bcher-kva.de"),
        ("ivan@почта.рф", "ivan@xn--80a1acny.xn--p1ai", "ivan@xn--80a1acny.xn--p1ai"),
        ("x@a-b.example", "x@a-b.example", "x@a-b.example"),
    ],
)
def test_an_address_keeps_its_local_part_and_gets_an_ascii_domain(value: str, address: str, canonical: str) -> None:
    parsed = emails.parse(value)

    assert (parsed.address, parsed.canonical) == (address, canonical)
    local, _, domain = address.partition("@")
    assert (parsed.local, parsed.domain) == (local, domain)


def test_the_parse_result_is_complete() -> None:
    assert emails.parse("J.Doe+Shop@GMail.com") == Email(
        address="J.Doe+Shop@gmail.com",
        canonical="j.doe+shop@gmail.com",
        local="J.Doe+Shop",
        domain="gmail.com",
        delivery_key="jdoe@gmail.com",
    )


def test_unicode_input_is_nfc_normalized_before_the_domain_is_converted() -> None:
    decomposed = "me@bu\u0308cher.de"  # "ü" as u + combining diaeresis

    assert emails.parse(decomposed).address == "me@xn--bcher-kva.de"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "plain",
        "two@@example.com",
        "a@b@example.com",
        "@example.com",
        "john@",
        ".john@example.com",
        "john.@example.com",
        "jo..hn@example.com",
        '"john smith"@example.com',  # no quoted strings
        "jöhn@example.com",  # ASCII local parts only
        "john doe@example.com",
        "john@localhost",  # one label
        "john@example",
        "john@example.123",  # the top-level label must be alphabetic
        "john@127.0.0.1",
        "john@[127.0.0.1]",
        "john@[IPv6:::1]",
        "john@-example.com",
        "john@example-.com",
        "john@exa_mple.com",
        "john@example..com",
        "john@example.com.",
        "john@.example.com",
        "john@ex ample.com",
        "john@example.com\r\nBcc: x@evil.test",
        "jo\x00hn@example.com",
        "john@exam\tple.com",
        "j" * 65 + "@example.com",
        "john@" + "a" * 64 + ".com",
    ],
)
def test_invalid_addresses_are_refused_with_one_sentence(value: str) -> None:
    with pytest.raises(EmailError) as refused:
        emails.parse(value)

    assert str(refused.value) == "Enter a valid email address."
    assert isinstance(refused.value, ValueError)


def test_the_longest_address_fits_254_characters() -> None:
    def address(third_label: int) -> str:
        return "l" * 64 + "@" + ".".join(["d" * 63, "d" * 63, "d" * third_label]) + ".io"

    assert len(emails.parse(address(58)).address) == 254
    with pytest.raises(EmailError):
        emails.parse(address(59))


def test_an_internationalized_domain_counts_its_ascii_length() -> None:
    value = "a" * 64 + "@" + ".".join(["ü" * 40] * 4) + ".de"  # 231 characters as typed, 255 converted

    with pytest.raises(EmailError):
        emails.parse(value)
    assert len(emails.parse(value.replace("ü", "", 1)).address) == 254


@pytest.mark.parametrize(
    ("value", "key"),
    [
        ("john@example.com", "john@example.com"),
        ("John+Shopping@Example.com", "john@example.com"),
        ("john+a+b@example.com", "john@example.com"),
        ("j.o.h.n@example.com", "j.o.h.n@example.com"),  # dots fold only at Gmail
        ("J.Ohn+1@GMail.com", "john@gmail.com"),
        ("john@googlemail.com", "john@gmail.com"),
        ("jo.hn+x@googlemail.com", "john@gmail.com"),
    ],
)
def test_the_delivery_key_folds_a_mailboxs_variants(value: str, key: str) -> None:
    assert emails.parse(value).delivery_key == key


def test_gmail_variants_share_one_delivery_key_but_stay_distinct_accounts() -> None:
    variants = [emails.parse(value) for value in ("j.ohn+1@GMail.com", "john@googlemail.com", "JOHN@gmail.com")]

    assert {variant.delivery_key for variant in variants} == {"john@gmail.com"}
    assert len({variant.canonical for variant in variants}) == 3


def test_digest_is_the_sha256_of_the_value() -> None:
    assert emails.digest("john@example.com") == hashlib.sha256(b"john@example.com").hexdigest()
    assert emails.digest("john@example.com") != emails.digest("John@example.com")
    assert len(emails.digest("x")) == 64


@pytest.mark.parametrize(
    ("address", "masked"),
    [
        ("john@example.com", "j***@example.com"),
        ("J.Smith@gmail.com", "J***@gmail.com"),
        ("a@b.io", "a***@b.io"),
        ("no-at-sign", "n***"),
        ("", "***"),
    ],
)
def test_mask_keeps_the_first_character_and_the_domain(address: str, masked: str) -> None:
    assert emails.mask(address) == masked


# ---------------- Usernames (app.services.auth.usernames, 3.3) ----------------


async def test_a_generated_username_is_a_random_handle(app: FastAPI) -> None:
    async with db.SessionLocal() as session:
        names = {await usernames.generate(session) for _ in range(20)}

    assert len(names) == 20
    assert all(re.fullmatch(r"user-[a-z2-7]{8}", name) for name in names)


async def test_generation_skips_taken_and_retired_names(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    draws = iter("aaaaaaaa" "bbbbbbbb" "cccccccc")
    monkeypatch.setattr(usernames.secrets, "choice", lambda alphabet: next(draws))
    async with db.SessionLocal() as session:
        session.add(User(username="user-aaaaaaaa", password_hash="x"))
        await usernames.tombstone(session, "user-bbbbbbbb")
        await session.commit()

        assert await usernames.generate(session) == "user-cccccccc"


async def test_generation_gives_up_after_five_draws(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(usernames.secrets, "choice", lambda alphabet: "a")
    async with db.SessionLocal() as session:
        session.add(User(username="user-aaaaaaaa", password_hash="x"))
        await session.commit()

        with pytest.raises(RuntimeError, match="No free username in 5 draws"):
            await usernames.generate(session)


async def test_a_tombstone_retires_a_username_for_good(app: FastAPI) -> None:
    async with db.SessionLocal() as session:
        assert not await usernames.retired(session, "smoke-gone")
        await usernames.tombstone(session, "smoke-gone")
        await usernames.tombstone(session, "smoke-gone")  # a second delete of the name changes nothing
        await session.rollback()
        assert not await usernames.retired(session, "smoke-gone"), "it is in the caller's transaction"

        await usernames.tombstone(session, "smoke-gone")
        await session.commit()

    async with db.SessionLocal() as session:
        assert await usernames.retired(session, "smoke-gone")
        rows = list(await session.scalars(select(UsernameTombstone)))
    assert [(row.username, row.deleted_at is not None) for row in rows] == [("smoke-gone", True)]
