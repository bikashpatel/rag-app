"""The password policy (app.services.auth.passwords; docs/briefs/signup-auth.md 5): length in code points after NFC,
the bundled blocklist, context words, Have I Been Pwned's range check (failing open), NFC hashing, and the warning
about a weak ADMIN_PASSWORD. The forms that apply the policy are tested with their routes."""

import hashlib
import logging

import pytest
from fake_hibp import FakeHibp

from app.config import settings
from app.errors import AppError
from app.security import hash_password, needs_rehash, verify_password, verify_password_async
from app.services.auth import passwords

GOOD = "plum-kettle-orbit-91"


async def refusal(password: str, **context: list[str]) -> AppError:
    with pytest.raises(AppError) as refused:
        await passwords.check(password, **context)  # type: ignore[arg-type]
    return refused.value


async def reason(password: str, **context: list[str]) -> str | None:
    error = await refusal(password, **context)
    assert (error.status_code, error.code) == (422, "password_rejected")
    return error.reason


async def test_a_good_password_passes() -> None:
    await passwords.check(GOOD)
    await passwords.check("correct horse battery staple 42")
    await passwords.check("  spaces count, any characters too: ✓ ☃ 漢字  ")


# ---------------- Length ----------------


async def test_the_minimum_length_counts_code_points_after_nfc() -> None:
    assert await reason("Ab3$fgh") == "too_short"
    await passwords.check("Ab3$fghj")
    decomposed = "e\u0301" * 7  # 14 code points as typed, 7 after NFC ("é")
    assert await reason(decomposed) == "too_short"
    await passwords.check("e\u0301" * 8)


async def test_the_minimum_follows_the_setting_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "password_min_length", 15)

    error = await refusal("plum-kettle-12")

    assert (error.reason, error.detail) == ("too_short", "Use at least 15 characters.")
    await passwords.check("plum-kettle-123")


async def test_at_most_256_characters() -> None:
    await passwords.check("q7" * 128)

    error = await refusal("q7" * 128 + "x")

    assert (error.reason, error.detail) == ("too_long", "Use at most 256 characters.")


async def test_a_password_is_never_trimmed() -> None:
    await passwords.check("  short  ")  # 9 characters with its spaces
    await passwords.check(" " * 8)


# ---------------- Blocklist and context ----------------


@pytest.mark.parametrize("password", ["password", "PASSWORD", "BaseBall", "qazwsxedcrfvtgb", "1qaz2wsx3edc4rfv"])
async def test_common_passwords_are_refused_whatever_their_case(password: str) -> None:
    error = await refusal(password)

    assert (error.reason, error.detail) == ("common", "This password is too common. Choose one that's harder to guess.")


def test_the_blocklist_is_a_lowercased_list_of_at_least_3000_long_entries() -> None:
    lines = passwords.BLOCKLIST.read_text(encoding="utf-8").splitlines()
    header = [line for line in lines if line.startswith("#")]
    entries = [line for line in lines if not line.startswith("#")]

    assert lines[: len(header)] == header
    assert {"SecLists,", "MIT,", "913b327317496d062bcc7cace524aaad8a693be2"} <= set(" ".join(header).split())
    assert len(entries) >= 3000 and len(set(entries)) == len(entries)
    assert all(len(entry) >= 8 and entry == entry.lower() and entry == entry.strip() for entry in entries)


@pytest.mark.parametrize(
    "password",
    ["AskNuvexusDoc", "asknuvexusdoc!!", "Nuvexus-2026", "nuvexusdoc 123", "N.u.v.e.x.u.s.D.o.c"],
)
async def test_the_apps_name_is_refused(password: str) -> None:
    error = await refusal(password)

    assert (error.reason, error.detail) == (
        "context",
        "Don't use the app's name, your username or your email in the password.",
    )


async def test_the_username_and_the_emails_local_part_are_refused() -> None:
    context = ["user-ab2cd3ef", "John.Smith"]

    assert await reason("John.Smith1990", context=context) == "context"
    assert await reason("USER-ab2cd3ef!", context=context) == "context"
    await passwords.check("John.Smith1990 rocks", context=context)  # more than the word itself


async def test_context_words_under_4_letters_are_ignored() -> None:
    await passwords.check("bob!1234567", context=["bob"])
    assert await reason("bobby!1234567", context=["Bobby"]) == "context"


# ---------------- Have I Been Pwned ----------------


async def test_a_breached_password_is_refused_and_only_a_hash_prefix_leaves(fake_hibp: FakeHibp) -> None:
    fake_hibp.add(GOOD, count=42)

    error = await refusal(GOOD)

    assert (error.reason, error.detail) == (
        "breached",
        "This password has appeared in a data breach. Choose a different one.",
    )
    sha1 = hashlib.sha1(GOOD.encode()).hexdigest().upper()
    request = fake_hibp.requests[-1]
    assert str(request.url) == f"https://api.pwnedpasswords.com/range/{sha1[:5]}"
    assert request.headers["add-padding"] == "true" and "AskNuvexusDoc" in request.headers["user-agent"]
    assert GOOD not in str(request.url) and sha1[5:] not in str(request.url)


async def test_the_breach_check_hashes_the_nfc_form(fake_hibp: FakeHibp) -> None:
    fake_hibp.add("café-au-lait-42")

    assert await reason("cafe\u0301-au-lait-42") == "breached"


async def test_padding_entries_with_count_0_and_misses_pass(fake_hibp: FakeHibp) -> None:
    fake_hibp.add(GOOD, count=0)
    fake_hibp.add("some-other-password-1")

    await passwords.check(GOOD)
    assert await passwords.breached(GOOD) is False
    assert len(fake_hibp.prefixes()) == 2


@pytest.mark.parametrize("failure", ["timeout", "500"])
async def test_an_outage_lets_the_password_through_without_logging_it(
    fake_hibp: FakeHibp, failure: str, caplog: pytest.LogCaptureFixture
) -> None:
    fake_hibp.add(GOOD)
    fake_hibp.failure = failure  # type: ignore[assignment]

    with caplog.at_level(logging.WARNING, logger="app.services.auth.passwords"):
        await passwords.check(GOOD)
        assert await passwords.breached(GOOD) is None

    assert "the password was allowed" in caplog.text and GOOD not in caplog.text


async def test_no_request_with_the_breach_check_off(fake_hibp: FakeHibp, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "password_breach_check", False)
    fake_hibp.add(GOOD)

    await passwords.check(GOOD)

    assert await passwords.breached(GOOD) is None
    assert fake_hibp.requests == []


async def test_cheaper_rules_come_before_the_network(fake_hibp: FakeHibp) -> None:
    for password in ("short", "password", "AskNuvexusDoc", "x" * 257):
        fake_hibp.add(password)
        await refusal(password)

    assert fake_hibp.requests == []


# ---------------- Hashing ----------------


def test_passwords_are_hashed_and_verified_in_their_nfc_form() -> None:
    composed, decomposed = "café-au-lait", "cafe\u0301-au-lait"

    assert verify_password(decomposed, hash_password(composed))
    assert verify_password(composed, hash_password(decomposed))
    assert not verify_password("cafe-au-lait", hash_password(composed))


async def test_a_missing_hash_never_matches_and_needs_no_rehash() -> None:
    assert verify_password("anything", None) is False
    assert await verify_password_async("anything", None) is False
    assert needs_rehash(None) is False
    assert needs_rehash("not an argon2 hash") is True


# ---------------- ADMIN_PASSWORD ----------------


@pytest.mark.parametrize(
    ("password", "weak"),
    [
        ("plum-kettle-orbit-91", False),
        ("correct horse battery staple 42", False),
        ("plum-kettle-12", True),  # 14 characters
        ("qazwsxedcrfvtgb", True),  # common
        ("my-AskNuvexusDoc-2026!", True),  # the app's name inside
        ("nuvexus plum kettle orbit", True),
        ("plum-kettle-ADMIN-orbit", True),  # the admin's username (ADMIN_USERNAME=admin) inside
    ],
)
def test_a_weak_admin_password_is_short_common_or_contains_a_context_word(password: str, weak: bool) -> None:
    assert passwords.weak_admin_password(password) is weak


def test_the_admin_password_check_counts_after_nfc_and_makes_no_request(fake_hibp: FakeHibp) -> None:
    assert passwords.weak_admin_password("e\u0301" * 13 + "x")  # 27 code points as typed, 14 after NFC
    assert not passwords.weak_admin_password("e\u0301" * 14 + "x")
    assert fake_hibp.requests == []
