"""Money, billing dates and the plan arithmetic (app/services/billing/money.py and the period dates of periods.py;
docs/briefs/plans-billing.md 3.2 to 4.4): the end of a period, the credit of a `now` checkout, scaled allowances, the
chat allowance in standard tokens, and the answers and cost figures of the seeded plans."""

from datetime import date, datetime, timedelta, timezone
from fractions import Fraction

import pytest

from app.config import settings
from app.schemas import UserLimits
from app.services.billing import money
from app.services.billing.money import (
    allowance_rate,
    allowance_tokens,
    answers,
    blended_price,
    cost_estimate,
    credit_paise,
    financial_year,
    format_inr,
    format_ist_date,
    format_ist_range,
    ist_date,
    now_charge,
    scaled_allowance,
    scaled_limits,
    used_share,
)
from app.services.billing.periods import period_end, valid_through


def utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=timezone.utc)  # type: ignore[misc]


# ---------------- Periods end at midnight IST ----------------


@pytest.mark.parametrize(
    ("start", "days", "ends", "through"),
    [
        # 08:42 IST: the period runs to the end of the IST day 30 days later (a few free hours)
        (utc(2026, 10, 26, 3, 12), 30, utc(2026, 11, 25, 18, 30), date(2026, 11, 25)),
        # 00:30 IST the next day, after 18:30 UTC: one IST day later
        (utc(2026, 10, 26, 19, 0), 30, utc(2026, 11, 26, 18, 30), date(2026, 11, 26)),
        # a second before midnight IST
        (utc(2026, 10, 26, 18, 29, 59), 30, utc(2026, 11, 25, 18, 30), date(2026, 11, 25)),
        # exactly midnight IST: exactly 30 days
        (utc(2026, 10, 25, 18, 30), 30, utc(2026, 11, 24, 18, 30), date(2026, 11, 24)),
        # a 1-day test-mode period
        (utc(2026, 10, 26, 10, 0), 1, utc(2026, 10, 27, 18, 30), date(2026, 10, 27)),
        # across the year
        (utc(2026, 12, 15, 6, 0), 30, utc(2027, 1, 14, 18, 30), date(2027, 1, 14)),
    ],
)
def test_a_period_ends_at_the_first_midnight_ist_at_or_after_its_days(
    start: datetime, days: int, ends: datetime, through: date
) -> None:
    assert period_end(start, days) == ends
    assert period_end(start, days).tzinfo == timezone.utc
    assert valid_through(ends) == through


def test_a_paid_period_lasts_30_to_31_days() -> None:
    start = utc(2026, 10, 1)
    for minute in range(0, 24 * 60, 7):
        begins = start + timedelta(minutes=minute)
        assert timedelta(days=30) <= period_end(begins, 30) - begins < timedelta(days=31)


# ---------------- Money and dates as users read them ----------------


@pytest.mark.parametrize(
    ("paise", "text"),
    [
        (200_000, "₹2,000"),
        (123_450, "₹1,234.50"),
        (10_000_000, "₹1,00,000"),
        (1_234_567_800, "₹1,23,45,678"),
        (100, "₹1"),
        (5, "₹0.05"),
        (0, "₹0"),
        (-30_000, "−₹300"),
    ],
)
def test_amounts_use_indian_grouping_and_hide_zero_paise(paise: int, text: str) -> None:
    assert format_inr(paise) == text


def test_billing_dates_are_ist_dates() -> None:
    assert ist_date(utc(2026, 3, 31, 18, 30)) == date(2026, 4, 1)
    assert ist_date(utc(2026, 3, 31, 18, 29, 59)) == date(2026, 3, 31)
    assert ist_date(datetime(2026, 3, 31, 18, 30)) == date(2026, 4, 1)  # naive: UTC
    assert format_ist_date(date(2026, 11, 25)) == "25 Nov 2026"
    assert format_ist_date(date(2026, 11, 5)) == "5 Nov 2026"
    assert format_ist_range(date(2026, 10, 26), date(2026, 11, 25)) == "26 Oct to 25 Nov 2026"
    assert format_ist_range(date(2026, 12, 26), date(2027, 1, 25)) == "26 Dec 2026 to 25 Jan 2027"


def test_the_financial_year_turns_on_1_april_ist() -> None:
    assert financial_year(utc(2027, 3, 31, 18, 29, 59)) == "26-27"
    assert financial_year(utc(2027, 3, 31, 18, 30)) == "27-28"  # already 1 April in India
    assert financial_year(utc(2026, 10, 26)) == "26-27"
    assert financial_year(utc(2099, 4, 1)) == "99-00"
    assert money.receipt_seq_key("live", "26-27") == "receipt_seq:live:26-27"


# ---------------- The chat allowance in standard tokens ----------------


@pytest.mark.parametrize(
    ("cost_usd", "total_tokens", "counted"),
    [
        (3.2 / 1_000_000, 50, 1),
        (0.000032, 50, 10),  # 10.000000000000002 in floats: still 10
        (0.000033, 50, 11),  # rounded up
        (5100 * 0.10e-6 + 900 * 0.50e-6, 6000, 300),  # a Claude Haiku 5.5 answer counts 1/20 of its tokens
        (5100 * 4e-6 + 900 * 20e-6, 6000, 12_000),  # a Claude Opus 5.5 answer twice
        (0.0, 6000, 6000),  # no price: its plain tokens
    ],
)
def test_an_answer_counts_its_cost_in_standard_tokens(cost_usd: float, total_tokens: int, counted: int) -> None:
    assert allowance_tokens(cost_usd, total_tokens) == counted


# ---------------- Scaled allowances (4.2) ----------------


def test_short_rows_get_their_share_of_each_allowance() -> None:
    assert scaled_allowance(500_000, 30) == 500_000
    assert scaled_allowance(500_000, 1) == 16_667  # ceil(500,000 ÷ 30)
    assert scaled_allowance(60, 15) == 30
    assert scaled_allowance(60, 1) == 2
    assert scaled_allowance(0, 1) == 0  # unlimited stays unlimited
    limits = UserLimits(
        monthly_token_limit=5_000_000,
        rate_limit_per_min=30,
        max_upload_mb=50,
        max_documents=500,
        max_storage_mb=3000,
        monthly_image_limit=60,
        vision_monthly_token_limit=1_000_000,
    )
    assert scaled_limits(limits, 10).model_dump() == {
        **limits.model_dump(),
        "monthly_token_limit": 1_666_667,
        "monthly_image_limit": 20,
        "vision_monthly_token_limit": 333_334,
    }


# ---------------- The `now` charge and its credit (4.4) ----------------

START, END = utc(2026, 10, 1, 18, 30), utc(2026, 10, 31, 18, 30)  # a 30-day period


def at_share_left(share: Fraction) -> datetime:
    return END - (END - START) * float(share)


def test_the_credit_is_the_smaller_of_the_time_left_and_the_allowance_unused() -> None:
    now = at_share_left(Fraction(3, 5))
    # The brief's example: ₹500 Starter, 60% of the time left, 30% of the chat allowance used
    credit = credit_paise(50_000, starts_at=START, ends_at=END, now=now, used=used_share([(150_000, 500_000)]))
    assert credit == 30_000
    assert now_charge(200_000, credit) == 170_000  # Standard now: ₹1,700 for a fresh period
    # 90% of the allowance used: the unused 10% decides
    credit = credit_paise(50_000, starts_at=START, ends_at=END, now=now, used=used_share([(450_000, 500_000)]))
    assert credit == 5_000
    assert now_charge(200_000, credit) == 195_000


def test_unlimited_allowances_are_left_out_and_overuse_counts_as_all_used() -> None:
    assert used_share([(10**9, 0), (300, 1000)]) == Fraction(3, 10)
    assert used_share([(10**9, 0)]) == 0
    assert used_share([]) == 0
    assert used_share([(1500, 1000), (1, 10)]) == 1
    now = at_share_left(Fraction(1, 2))
    assert credit_paise(50_000, starts_at=START, ends_at=END, now=now, used=Fraction(1)) == 0


def test_the_credit_is_whole_rupees_and_a_comp_gives_none() -> None:
    now = at_share_left(Fraction(1, 3))
    assert credit_paise(50_000, starts_at=START, ends_at=END, now=now, used=Fraction(0)) == 16_600  # ₹166.66 → ₹166
    assert credit_paise(0, starts_at=START, ends_at=END, now=now, used=Fraction(0)) == 0  # comps: basis 0
    assert now_charge(200_000, 0) == 200_000


def test_the_time_share_stays_between_none_and_all() -> None:
    assert credit_paise(50_000, starts_at=START, ends_at=END, now=END + timedelta(days=1), used=Fraction(0)) == 0
    assert credit_paise(50_000, starts_at=START, ends_at=END, now=START - timedelta(days=1), used=Fraction(0)) == 50_000


def test_a_now_checkout_costs_at_least_one_rupee() -> None:
    assert now_charge(50_000, 50_000) == 100
    assert now_charge(50_000, 60_000) == 100
    assert now_charge(50_000, 49_950) == 100
    assert now_charge(50_000, 49_800) == 200


def test_an_upgrade_on_the_last_day_buys_no_allowance_cheaply() -> None:
    """SEC-1: with one day left of a 30 to 31 day period and nothing used, the credit is at most 1/30 of the basis."""
    starts_at = utc(2026, 10, 26, 3, 12)
    ends_at = period_end(starts_at, 30)
    last_day = ends_at - timedelta(days=1)
    credit = credit_paise(50_000, starts_at=starts_at, ends_at=ends_at, now=last_day, used=Fraction(0))
    assert 0 < credit <= 50_000 // 30
    assert now_charge(500_000, credit) >= 500_000 - 50_000 // 30


# ---------------- Rates, answers and cost estimates (3.2, 3.3) ----------------

PRICES = {  # USD per 1M tokens in and out (seeded prices; LiteLLM's map for GPT)
    "haiku": (0.10, 0.50),
    "gpt-mini": (0.75, 4.50),
    "sonnet": (2.0, 10.0),
    "opus": (4.0, 20.0),
    "gpt": (5.0, 30.0),
}


def blended(model: str) -> float | None:
    return blended_price(*PRICES[model])


def test_a_models_rate_is_its_blended_price_against_sonnets() -> None:
    assert {model: allowance_rate(blended(model)) for model in PRICES} == {
        "haiku": 0.05,
        "gpt-mini": 0.41,
        "sonnet": 1.0,
        "opus": 2.0,
        "gpt": 2.7,
    }
    assert blended_price(None, 1.0) is None and blended_price(0.0, 0.0) is None
    assert allowance_rate(None) is None


@pytest.mark.parametrize(
    ("allowance", "files", "images", "expected"),
    [
        (500_000, False, False, {"haiku": 1600, "gpt-mini": 200}),  # Starter
        (2_000_000, True, False, {"sonnet": 260, "haiku": 5200, "gpt-mini": 640}),  # Standard
        (5_000_000, True, True, {"opus": 290, "sonnet": 580, "gpt": 210, "haiku": 11_000}),  # Professional
    ],
)
def test_the_answers_figures_of_the_seeded_plans(
    allowance: int, files: bool, images: bool, expected: dict[str, int]
) -> None:
    assert {model: answers(allowance, blended(model), files=files, images=images) for model in expected} == expected


def test_answers_are_rounded_down_and_unknown_without_a_limit_or_a_price() -> None:
    assert answers(0, blended("haiku"), files=False, images=False) is None
    assert answers(500_000, None, files=False, images=False) is None
    assert answers(1000, blended("gpt"), files=False, images=False) == 0
    assert answers(30_000, blended("sonnet"), files=False, images=False) == 5
    assert answers(410_000, blended("sonnet"), files=False, images=False) == 68


def plan_limits(chat: int, images: int, vision: int) -> UserLimits:
    return UserLimits(
        monthly_token_limit=chat,
        rate_limit_per_min=10,
        max_upload_mb=10,
        max_documents=50,
        max_storage_mb=200,
        monthly_image_limit=images,
        vision_monthly_token_limit=vision,
    )


@pytest.mark.parametrize(
    ("price", "limits", "images", "kept", "worst", "typical", "worst_share", "typical_share"),
    [
        (50_000, plan_limits(500_000, 30, 150_000), False, 48_820, 17_169, 6_868, 0.35, 0.14),
        (200_000, plan_limits(2_000_000, 30, 500_000), False, 195_280, 66_616, 26_646, 0.34, 0.14),
        (500_000, plan_limits(5_000_000, 60, 1_000_000), True, 488_200, 183_568, 73_427, 0.38, 0.15),
    ],
    ids=["starter", "standard", "professional"],
)
def test_the_cost_estimates_of_the_seeded_plans(
    price: int,
    limits: UserLimits,
    images: bool,
    kept: int,
    worst: int,
    typical: int,
    worst_share: float,
    typical_share: float,
) -> None:
    estimate = cost_estimate(price, limits, images=images, inputs=money.cost_inputs())
    assert (estimate.kept_paise, estimate.worst_case_paise, estimate.typical_paise) == (kept, worst, typical)
    assert (round(estimate.worst_share, 2), round(estimate.typical_share, 2)) == (worst_share, typical_share)
    assert estimate.unlimited == []


def test_unlimited_allowances_have_no_ceiling_in_the_estimate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "billing_usd_inr", 100)
    inputs = money.cost_inputs()
    assert inputs.model_dump() == {
        "usd_inr": 100,
        "standard_usd_per_mtok": 3.2,
        "vision_usd_per_mtok": 2.34,
        "image_usd": 0.042,
        "fee_bp": 236,
    }
    estimate = cost_estimate(50_000, plan_limits(0, 0, 100_000), images=True, inputs=inputs)
    assert estimate.unlimited == ["monthly_token_limit", "monthly_image_limit"]
    assert estimate.worst_case_paise == round(100_000 * 2.34e-6 * 100 * 100)
    assert cost_estimate(50_000, plan_limits(0, 0, 0), images=False, inputs=inputs).unlimited == [
        "monthly_token_limit",
        "vision_monthly_token_limit",
    ]
