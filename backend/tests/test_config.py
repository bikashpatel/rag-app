"""Settings that are derived rather than read as-is (app.config)."""

import pytest
from pydantic import ValidationError
from sqlalchemy import make_url

from app.config import RAZORPAY_API_BASE, Settings


@pytest.mark.parametrize(
    "password", ["correct horse battery staple", "a+b", "x y+z", "p@ss:w/d%#?", "x%2By", "naïve pass"]
)
def test_postgres_url_keeps_every_character_of_the_credentials(password: str) -> None:
    configured = Settings(  # type: ignore[call-arg]
        database_url=None, postgres_user="rag user", postgres_password=password, postgres_db="rag db"
    )

    url = make_url(configured.sqlalchemy_url)

    assert (url.username, url.password, url.database) == ("rag user", password, "rag db")
    assert (url.drivername, url.host, url.port) == ("postgresql+asyncpg", "postgres", 5432)


def test_database_url_wins_over_the_postgres_settings() -> None:
    configured = Settings(database_url="sqlite+aiosqlite:///x.db", postgres_password="a b")  # type: ignore[call-arg]

    assert configured.sqlalchemy_url == "sqlite+aiosqlite:///x.db"


# ---------------- Payments (docs/briefs/plans-billing.md 6.9 and 6.10) ----------------


@pytest.mark.parametrize(
    ("key_id", "secret", "mode"),
    [
        ("rzp_test_AbCd", "secret", "test"),
        ("rzp_live_AbCd", "secret", "live"),
        ("rzp_other_AbCd", "secret", None),
        ("rzp_test_AbCd", "", None),
        ("", "secret", None),
    ],
)
def test_the_key_ids_prefix_sets_the_payments_mode(key_id: str, secret: str, mode: str | None) -> None:
    configured = Settings(razorpay_key_id=key_id, razorpay_key_secret=secret)  # type: ignore[call-arg]

    assert configured.razorpay_mode == mode


def test_live_keys_refuse_to_start_with_another_api_base() -> None:
    with pytest.raises(ValidationError, match="RAZORPAY_API_BASE must be https://api.razorpay.com"):
        Settings(razorpay_key_id="rzp_live_AbCd", razorpay_api_base="https://fake-razorpay:8443")  # type: ignore[call-arg]

    live = Settings(razorpay_key_id="rzp_live_AbCd", razorpay_api_base=RAZORPAY_API_BASE)  # type: ignore[call-arg]
    fake = Settings(razorpay_key_id="rzp_test_AbCd", razorpay_api_base="https://fake-razorpay:8443")  # type: ignore[call-arg]
    assert live.razorpay_api_base == RAZORPAY_API_BASE and fake.razorpay_api_base == "https://fake-razorpay:8443"


@pytest.mark.parametrize(
    "values",
    [
        {"razorpay_api_base": "https://api.razorpay.com/v1"},
        {"razorpay_api_base": "ftp://api.razorpay.com"},
        {"billing_checkout_minutes": 15},
        {"billing_checkout_minutes": 121},
        {"billing_late_capture_days": 11},
        {"billing_reminder_days": 0},
        {"billing_reconcile_seconds": 1},
        {"billing_usd_inr": 0},
        {"billing_email_from": "not an address"},
    ],
)
def test_payment_settings_out_of_range_stop_the_start(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(**values)  # type: ignore[arg-type]
