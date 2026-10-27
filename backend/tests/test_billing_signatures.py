"""Razorpay's two signatures (app/services/billing/razorpay.py; docs/briefs/plans-billing.md 6.1): the Payment Link
callback's HMAC with the key secret over "link|reference|status|payment", and the webhook's HMAC of the raw body with
the webhook secret or the previous one. The vectors were computed with `openssl dgst -sha256 -hmac`."""

import pytest

from app.config import settings
from app.services.billing import razorpay

LINK, REFERENCE, STATUS, PAYMENT = "plink_Ab12Cd34Ef56Gh", "rag-AbCdEfGhIjKlMnOpQrSt", "paid", "pay_Zy98Xw76Vu54Ts"
CALLBACK = "cbedd3e420e97816104965e737ffb3be0d6e70cc08a676dc3eee8229f866fb80"
BODY = b'{"entity":"event","event":"payment.captured","created_at":1793000000}'
WEBHOOK_CURRENT = "784c50914cb8444110bfdd8d79bcecc3d7c5d9cb8d7a524b6c12cf590463e11d"
WEBHOOK_PREVIOUS = "649b964672b9fe80e98a5f27f6da6a8fe9ce0ce4021a55a1de64abb5e6e7edce"


@pytest.fixture(autouse=True)
def secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "razorpay_key_secret", "key-secret-123")
    monkeypatch.setattr(settings, "razorpay_webhook_secret", "whsec-current")
    monkeypatch.setattr(settings, "razorpay_webhook_secret_previous", "")


def test_the_callback_signature_matches_the_fixed_vector() -> None:
    assert razorpay.hmac_sha256_hex("key-secret-123", f"{LINK}|{REFERENCE}|{STATUS}|{PAYMENT}".encode()) == CALLBACK
    assert razorpay.callback_signature_ok(LINK, REFERENCE, STATUS, PAYMENT, CALLBACK)


def test_upper_case_hex_is_the_same_signature() -> None:
    assert razorpay.callback_signature_ok(LINK, REFERENCE, STATUS, PAYMENT, CALLBACK.upper())
    assert razorpay.webhook_signature_ok(BODY, WEBHOOK_CURRENT.upper())


@pytest.mark.parametrize(
    "fields",
    [
        (REFERENCE, LINK, STATUS, PAYMENT),  # fields in another order
        (LINK, REFERENCE, "created", PAYMENT),  # another status
        (LINK, REFERENCE, STATUS, "pay_Other0000000000"),  # another payment
        (LINK, "", STATUS, PAYMENT),  # no reference
    ],
)
def test_any_other_field_or_order_fails(fields: tuple[str, str, str, str]) -> None:
    assert not razorpay.callback_signature_ok(*fields, CALLBACK)


def test_a_wrong_or_missing_key_secret_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "razorpay_key_secret", "another-secret")
    assert not razorpay.callback_signature_ok(LINK, REFERENCE, STATUS, PAYMENT, CALLBACK)
    monkeypatch.setattr(settings, "razorpay_key_secret", "")
    assert not razorpay.callback_signature_ok(LINK, REFERENCE, STATUS, PAYMENT, CALLBACK)


@pytest.mark.parametrize("signature", ["", "not-hex", "é" * 64, CALLBACK[:-1]])
def test_malformed_signatures_fail_without_an_error(signature: str) -> None:
    assert not razorpay.callback_signature_ok(LINK, REFERENCE, STATUS, PAYMENT, signature)
    assert not razorpay.webhook_signature_ok(BODY, signature)


def test_the_webhook_signature_covers_the_raw_body() -> None:
    assert razorpay.webhook_signature_ok(BODY, WEBHOOK_CURRENT)
    assert not razorpay.webhook_signature_ok(BODY + b" ", WEBHOOK_CURRENT)
    assert not razorpay.webhook_signature_ok(BODY, WEBHOOK_PREVIOUS)


def test_the_previous_webhook_secret_is_accepted_only_while_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "razorpay_webhook_secret_previous", "whsec-previous")
    assert razorpay.webhook_signature_ok(BODY, WEBHOOK_PREVIOUS)
    assert razorpay.webhook_signature_ok(BODY, WEBHOOK_CURRENT)
    monkeypatch.setattr(settings, "razorpay_webhook_secret_previous", "")
    assert not razorpay.webhook_signature_ok(BODY, WEBHOOK_PREVIOUS)


def test_without_any_webhook_secret_nothing_verifies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "razorpay_webhook_secret", "")
    assert not razorpay.webhook_signature_ok(BODY, razorpay.hmac_sha256_hex("", BODY))


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://rzp.io/i/AbCd1234", True),
        ("https://pages.razorpay.com/pl_AbCd", True),
        ("http://rzp.io/i/AbCd1234", False),
        ("https://rzp.io.evil.example/i/x", False),
        ("https://evilrazorpay.com/x", False),
        ("https://user:pass@rzp.io/i/x", False),
        ("https://rzp.io:8443/i/x", False),
        ("javascript:alert(1)", False),
        (None, False),
    ],
)
def test_pay_urls_must_be_razorpay_addresses(url: object, ok: bool) -> None:
    assert razorpay.pay_url_ok(url) is ok


def test_any_https_pay_url_only_for_a_fake_under_test_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "razorpay_key_id", "rzp_test_Fake")
    monkeypatch.setattr(settings, "razorpay_api_base", "https://fake-razorpay:8443")
    assert razorpay.pay_url_ok("https://127.0.0.1:18093/i/plink_x")
    assert not razorpay.pay_url_ok("http://127.0.0.1:18093/i/plink_x")
    monkeypatch.setattr(settings, "razorpay_api_base", "https://api.razorpay.com")
    assert not razorpay.pay_url_ok("https://127.0.0.1:18093/i/plink_x")


@pytest.mark.parametrize(("value", "ok"), [("pay_Q2xS9e1LkvM8tz", True), ("../x", False), ("a/b", False), ("", False)])
def test_only_plain_ids_go_into_paths(value: str, ok: bool) -> None:
    assert (razorpay.entity_id(value) is not None) is ok


async def test_a_bad_id_never_reaches_razorpay() -> None:
    with pytest.raises(razorpay.RazorpayError):
        await razorpay.fetch_payment("../../v1/payments")
