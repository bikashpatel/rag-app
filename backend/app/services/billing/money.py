"""Money, billing dates and the plan arithmetic (docs/briefs/plans-billing.md 3.3, 3.4, 4.2 and 4.4).

Amounts are integer paise and the charge arithmetic is exact (integers and Fraction, never floats). Billing dates are
IST dates. The cost and answers figures are estimates, so they use floats."""

import math
from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction
from typing import Final

from app.config import settings
from app.schemas import UserLimits
from app.schemas_billing import CostInputs, PlanCostEstimate

IST: Final = timezone(timedelta(hours=5, minutes=30))
STANDARD_TOKEN_USD: Final = 3.20 / 1_000_000  # one standard token: Claude Sonnet 5.5's blended price
PERIOD_DAYS: Final = 30
MIN_CHARGE_PAISE: Final = 100  # ₹1
FEE_BP: Final = 236  # Razorpay's 2% fee plus 18% GST on it, in basis points of the charge
VISION_USD_PER_MTOK: Final = 2.34  # Claude Sonnet 5.5 reading pages, measured
IMAGE_USD: Final = 0.042  # GPT Image 2, landscape medium, measured
TYPICAL_USE: Final = 0.4  # the typical estimate: this share of every allowance
_INPUT_SHARE = 0.85  # a blended price: RAG answers are mostly prompt
_TOKENS_PER_ANSWER = 6000
_FILES_PROMPT_TOKENS = 1550  # create_document's tool and instructions
_IMAGES_PROMPT_TOKENS = 1040  # create_image's
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def allowance_tokens(cost_usd: float, total_tokens: int) -> int:
    """What a chat answer counts against the chat allowance: its cost in standard tokens, or its plain tokens when it
    has no cost. Rounded first to a millionth, so float noise (10.000000000000002) doesn't add a token."""
    if cost_usd > 0:
        return math.ceil(round(cost_usd / STANDARD_TOKEN_USD, 6))
    return total_tokens


def format_inr(paise: int) -> str:
    """ "₹2,000", "₹1,234.50", "₹1,00,000" (Indian digit grouping); a negative amount "−₹300"."""
    rupees, rest = divmod(abs(paise), 100)
    digits = str(rupees)
    head, groups = digits[:-3], [digits[-3:]]
    while head:
        groups.insert(0, head[-2:])
        head = head[:-2]
    sign = "−" if paise < 0 else ""
    return f"{sign}₹{','.join(groups)}" + (f".{rest:02d}" if rest else "")


def ist_date(instant: datetime) -> date:
    """The IST calendar date of an instant (a naive one is taken as UTC)."""
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(IST).date()


def format_ist_date(day: date) -> str:
    """ "25 Nov 2026"."""
    return f"{day.day} {_MONTHS[day.month - 1]} {day.year}"


def format_ist_range(start: date, end: date) -> str:
    """ "26 Oct to 25 Nov 2026"; "26 Dec 2026 to 25 Jan 2027" across a year."""
    if start.year == end.year:
        return f"{start.day} {_MONTHS[start.month - 1]} to {format_ist_date(end)}"
    return f"{format_ist_date(start)} to {format_ist_date(end)}"


def financial_year(instant: datetime) -> str:
    """The Indian financial year (1 April to 31 March, IST) of an instant: "26-27"."""
    day = ist_date(instant)
    start = day.year if day.month >= 4 else day.year - 1
    return f"{start % 100:02d}-{(start + 1) % 100:02d}"


def receipt_seq_key(mode: str, year: str) -> str:
    """The app_state key holding the last receipt number of a series (mode "live" or "test", a financial year)."""
    return f"receipt_seq:{mode}:{year}"


# ---------------- Allowances and the `now` charge (4.2, 4.4) ----------------


def scaled_allowance(limit: int, days: int) -> int:
    """An allowance for a period row of `days` (< 30: a complimentary row): ceil(limit × days ÷ 30); 0 stays
    unlimited."""
    if days >= PERIOD_DAYS:
        return limit
    return -(-limit * days // PERIOD_DAYS)


def scaled_limits(limits: UserLimits, days: int) -> UserLimits:
    """The chat allowance, images and vision tokens scaled to a row of `days`; the other limits as they are."""
    return limits.model_copy(
        update={
            name: scaled_allowance(getattr(limits, name), days)
            for name in ("monthly_token_limit", "monthly_image_limit", "vision_monthly_token_limit")
        }
    )


def used_share(usage: Iterable[tuple[int, int]]) -> Fraction:
    """The largest share used of the limited allowances, from (used, limit) pairs; limits of 0 are left out; 0..1."""
    return max((Fraction(min(used, limit), limit) for used, limit in usage if limit > 0), default=Fraction(0))


def credit_paise(basis_paise: int, *, starts_at: datetime, ends_at: datetime, now: datetime, used: Fraction) -> int:
    """The credit for the unused part of a period: its basis × min(share of time left, share of allowance unused),
    rounded down to whole rupees."""
    total = ends_at - starts_at
    left = min(max(ends_at - now, timedelta(0)), total)
    share = min(Fraction(left // timedelta(microseconds=1), total // timedelta(microseconds=1)), 1 - used)
    return math.floor(basis_paise * max(share, Fraction(0)) / 100) * 100


def now_charge(price_paise: int, credit: int) -> int:
    """A `now` checkout's charge: the plan's full price minus the credit, at least ₹1."""
    return max(MIN_CHARGE_PAISE, price_paise - credit)


# ---------------- Rates, answers and cost estimates (3.3) ----------------


def blended_price(input_usd_per_mtok: float | None, output_usd_per_mtok: float | None) -> float | None:
    """USD per million tokens of a typical RAG answer (85% input, 15% output); None without a price."""
    if input_usd_per_mtok is None or output_usd_per_mtok is None:
        return None
    price = _INPUT_SHARE * input_usd_per_mtok + (1 - _INPUT_SHARE) * output_usd_per_mtok
    return price if price > 0 else None


def allowance_rate(blended: float | None) -> float | None:
    """How many standard tokens one of the model's tokens counts, to two significant figures; None without a price
    (it then counts plain tokens)."""
    return None if blended is None else float(f"{blended / (STANDARD_TOKEN_USD * 1_000_000):.2g}")


def answers(allowance: int, blended: float | None, *, files: bool, images: bool) -> int | None:
    """About how many answers an allowance gives with a model, rounded down to two significant figures; None when
    the allowance is unlimited or the model has no price."""
    if allowance <= 0 or blended is None:
        return None
    tokens = _TOKENS_PER_ANSWER + (_FILES_PROMPT_TOKENS if files else 0) + (_IMAGES_PROMPT_TOKENS if images else 0)
    count = allowance * STANDARD_TOKEN_USD * 1_000_000 / blended / tokens
    if count < 1:
        return 0
    unit = 10 ** max(0, math.floor(math.log10(count)) - 1)
    return math.floor(count / unit + 1e-9) * unit


def cost_inputs() -> CostInputs:
    """What the cost estimates are computed from (Admin → Plans computes the same in the browser)."""
    return CostInputs(
        usd_inr=settings.billing_usd_inr,
        standard_usd_per_mtok=STANDARD_TOKEN_USD * 1_000_000,
        vision_usd_per_mtok=VISION_USD_PER_MTOK,
        image_usd=IMAGE_USD,
        fee_bp=FEE_BP,
    )


def cost_estimate(price_paise: int, limits: UserLimits, *, images: bool, inputs: CostInputs) -> PlanCostEstimate:
    """What one period costs the owner in AI calls when every allowance is used up (worst case) and when 40% is
    (typical), against what they keep of the price after Razorpay's fee. Unlimited allowances add nothing and are
    listed; images count only when the plan includes them."""
    kept = price_paise * (10_000 - inputs.fee_bp) // 10_000
    worst_usd = (
        limits.monthly_token_limit * inputs.standard_usd_per_mtok / 1_000_000
        + limits.vision_monthly_token_limit * inputs.vision_usd_per_mtok / 1_000_000
        + (limits.monthly_image_limit * inputs.image_usd if images else 0)
    )
    worst = round(worst_usd * inputs.usd_inr * 100)
    typical = round(worst_usd * TYPICAL_USE * inputs.usd_inr * 100)
    allowances = ["monthly_token_limit", *(["monthly_image_limit"] if images else []), "vision_monthly_token_limit"]
    return PlanCostEstimate(
        kept_paise=kept,
        worst_case_paise=worst,
        worst_share=worst / kept if kept else 0.0,
        typical_paise=typical,
        typical_share=typical / kept if kept else 0.0,
        unlimited=[name for name in allowances if getattr(limits, name) == 0],
    )
