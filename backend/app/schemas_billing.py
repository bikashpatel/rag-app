"""Shapes of plans, checkout and billing: the user's, the public and the admin's (SPEC.md section 6, "Billing";
docs/briefs/plans-billing.md section 7). Contract only. Amounts are integer paise; instants are UTC; `date` fields are
IST dates."""

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    model_validator,
)

from app.config import settings
from app.schemas import MAX_INT, UserLimits

# Largest Postgres BIGINT: bigger values would make the driver fail with a 500.
MAX_BIGINT = 9_223_372_036_854_775_807
MIN_PRICE_PAISE = 100  # ₹1
MAX_PRICE_PAISE = 10_000_000  # ₹1,00,000

PlanState = Literal["exempt", "none", "active", "expired"]
PlanStatus = Literal["active", "retired"]
CheckoutKind = Literal["period", "now"]
PaymentsMode = Literal["test", "live"]
OrderMode = Literal["test", "live", "offline"]
OrderStatus = Literal["pending", "paid", "expired", "cancelled", "review"]
PaymentStatus = Literal["captured", "refunded", "partially_refunded", "disputed"]
PeriodSource = Literal["payment", "comp"]
RefundStatus = Literal["creating", "pending", "processed", "failed"]
DisputeStatus = Literal["open", "under_review", "action_required", "won", "lost", "closed"]
AttentionKind = Literal[
    "review",
    "duplicate",
    "refund_failed",
    "refund_pending",
    "partial_refund",
    "dispute",
    "dispute_lost",
    "credit_reversed",
    "webhook_errors",
    "payments_not_configured",
    "test_mode",
    "test_keys_public",
    "live_without_webhook",
    "keys_rejected",
    "paused_orders",
]


def _strip(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


def _blank_to_none(value: object) -> object:
    return (value.strip() or None) if isinstance(value, str) else value


def _printable(value: str) -> str:
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("must not contain control characters")
    return value


def _text(value: str) -> str:
    """Several lines allowed (newlines and tabs); other control characters are refused."""
    if any((ord(char) < 32 and char not in "\n\t") or ord(char) == 127 for char in value):
        raise ValueError("must not contain control characters")
    return value


def _whole_rupees(value: int) -> int:
    if value % 100:
        raise ValueError("must be a whole number of rupees (a multiple of 100 paise)")
    return value


def _accepted(value: bool) -> bool:
    if not value:
        raise ValueError("Accept the terms to continue.")
    return value


def _unique[T](values: list[T]) -> list[T]:
    return list(dict.fromkeys(values))


def _not_test_prefix(value: str) -> str:
    if value == "TEST":
        raise ValueError("TEST is reserved for test-mode receipts")
    return value


Id = Annotated[StrictInt, Field(ge=1, le=MAX_INT)]
Paise = Annotated[StrictInt, Field(ge=MIN_PRICE_PAISE, le=MAX_INT)]
Count = Annotated[StrictInt, Field(ge=0, le=MAX_INT)]
TokenCount = Annotated[StrictInt, Field(ge=0, le=MAX_BIGINT)]
PlanCode = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{1,31}$")]
PluginId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
PlanName = Annotated[
    str, StringConstraints(min_length=1, max_length=60), BeforeValidator(_strip), AfterValidator(_printable)
]
PlanDescription = Annotated[str, StringConstraints(max_length=300), BeforeValidator(_strip), AfterValidator(_printable)]
Price = Annotated[StrictInt, Field(ge=MIN_PRICE_PAISE, le=MAX_PRICE_PAISE), AfterValidator(_whole_rupees)]
Rank = Annotated[StrictInt, Field(ge=0, le=MAX_INT)]
Reason = Annotated[
    str, StringConstraints(min_length=1, max_length=200), BeforeValidator(_strip), AfterValidator(_printable)
]
Note = Annotated[str, StringConstraints(min_length=1, max_length=500), BeforeValidator(_strip), AfterValidator(_text)]
Email = Annotated[str, StringConstraints(max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]
ModelIds = Annotated[list[Id], Field(max_length=1000), AfterValidator(_unique)]


# ---------------- Shared: plans and what a user may do (Me.plan) ----------------


class PlanFeatures(BaseModel):
    files: bool
    images: bool
    plugins: list[str]  # plugin ids, e.g. ["gmail"]


class PlanFeaturesInput(PlanFeatures):
    """Features as the admin sends them; unknown plugin ids are refused by the router (422)."""

    model_config = ConfigDict(extra="forbid")

    files: StrictBool
    images: StrictBool
    plugins: Annotated[list[PluginId], Field(max_length=20), AfterValidator(_unique)]


class LimitsInput(UserLimits):
    """The seven limits as the admin sends them: per period, 0 = unlimited (max_upload_mb is at least 1)."""

    model_config = ConfigDict(extra="forbid")

    monthly_token_limit: TokenCount
    rate_limit_per_min: Count
    max_upload_mb: Annotated[StrictInt, Field(ge=1, le=settings.max_upload_mb_hard)]
    max_documents: Count
    max_storage_mb: Count
    monthly_image_limit: Count
    vision_monthly_token_limit: TokenCount


class PlanSnapshot(BaseModel):
    """What a plan period (or an order) holds of its plan (plan_periods.snapshot, billing_orders.plan_snapshot)."""

    plan_id: int
    code: str
    name: str
    features: PlanFeatures
    limits: UserLimits  # scaled for comp rows shorter than 30 days
    restrict_models: bool
    chat_model_ids: list[int]  # the plan's chat models when restrict_models; [] otherwise
    default_chat_model_id: int | None


class PlanRef(BaseModel):
    id: int
    code: str
    name: str


class PendingOrder(BaseModel):
    public_id: str
    kind: CheckoutKind
    plan: PlanRef
    amount_paise: int
    created_at: datetime
    expires_at: datetime
    pay_url: str | None  # None once the link expired


class PlanUnlock(BaseModel):
    """The name of the cheapest active plan that includes each feature (None when none does)."""

    files: str | None
    images: str | None
    plugins: dict[str, str | None]
    models: str | None  # a higher plan with a chat model the user can't use now


class PlanAccess(BaseModel):
    """Me.plan: everything the UI gates on (plans-billing 5.9)."""

    state: PlanState
    access: bool  # exempt or active
    plan: PlanRef | None  # current (period or open-ended), or the last one when expired
    complimentary: bool
    open_ended: bool
    test_mode: bool
    period_start: datetime | None  # the current period; None for an open-ended plan
    period_end: datetime | None
    paid_through: datetime | None  # by a period: the end of the chain from the current one; expired: when it ended
    valid_through: date | None  # IST date of the last day with access; None: open-ended
    next_plan: PlanRef | None
    expiring_soon: bool
    ended_at: datetime | None  # expired: when access ended
    features: PlanFeatures  # exempt: everything; without access: nothing
    pending_order: PendingOrder | None
    can_start_new_period: bool
    unlock: PlanUnlock


# ---------------- Billing: the user's ----------------


class PlanModel(BaseModel):
    id: int
    display_name: str
    allowance_rate: float | None  # ×rate against the chat allowance; None: no price (plain tokens)
    answers: int | None  # about this many answers with the plan's allowance; None: unlimited or unpriced


class PlanPlugin(BaseModel):
    id: str
    name: str
    available: bool  # set up and turned on by the admin


class CreditLine(BaseModel):
    description: str  # "Credit for unused Starter, 26 Oct to 25 Nov"
    amount_paise: int


class CheckoutOption(BaseModel):
    kind: CheckoutKind
    label: str
    starts_at: datetime
    ends_at: datetime
    valid_through: date
    amount_paise: int  # what is charged
    plan_price_paise: int
    credit: CreditLine | None  # `now` only
    changes: list[str]  # what the switch removes (4.5); may be empty
    notes: list[str]
    unavailable_reason: str | None


class PublicPlan(BaseModel):
    code: str
    name: str
    description: str
    price_paise: int
    recommended: bool
    features: PlanFeatures
    plugins: list[PlanPlugin]  # every registered plugin; the plan includes those in features.plugins
    limits: UserLimits
    chat_models: list[PlanModel]  # the plan's: every enabled chat model when unrestricted
    all_chat_models: bool
    default_chat_model: PlanModel | None  # the plan's default, else the server's default when the plan has it
    answers_estimate: int | None  # with the default model


class PlanOffer(PublicPlan):
    id: int
    status: PlanStatus
    current: bool  # the plan that applies now
    options: list[CheckoutOption]  # empty for the admin


class PaymentsInfo(BaseModel):
    available: bool  # for this caller: keys set, and a tester under test keys
    mode: PaymentsMode | None  # None when not available to this caller
    contact_email: str | None
    upi_limit_paise: int  # 0: no limit
    email_reminders: bool  # SMTP and BILLING_EMAIL_FROM are set


class PlanCatalog(BaseModel):
    plans: list[PlanOffer]
    payments: PaymentsInfo


class ReceiptRef(BaseModel):
    id: int
    number: str


class OrderView(BaseModel):
    public_id: str
    kind: CheckoutKind
    plan: PlanRef
    amount_paise: int
    credit_paise: int
    status: OrderStatus
    created_at: datetime
    expires_at: datetime
    paid_at: datetime | None
    pay_url: str | None  # pending and not expired
    last_payment_error: str | None
    period_start: datetime | None  # what it bought
    period_end: datetime | None
    valid_through: date | None
    receipt: ReceiptRef | None
    method: str | None  # upi | card | netbanking | wallet | emi | bank_transfer
    test_mode: bool


class CheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: Id
    kind: CheckoutKind
    accept_terms: Annotated[StrictBool, AfterValidator(_accepted)]  # the consent line under Pay, stored with the order


class CheckoutStarted(BaseModel):
    order: OrderView
    pay_url: str


class PaymentView(BaseModel):
    id: int
    paid_at: datetime
    plan: PlanRef | None
    kind: CheckoutKind
    description: str
    amount_paise: int
    method: str | None
    status: PaymentStatus
    refunded_paise: int
    period_start: datetime | None  # None: a receipt-only offline payment
    period_end: datetime | None
    receipt: ReceiptRef | None
    test_mode: bool


class PeriodView(BaseModel):
    id: int
    plan: PlanRef | None
    plan_name: str
    starts_at: datetime
    ends_at: datetime
    valid_through: date
    source: PeriodSource
    current: bool
    test_mode: bool
    reason: str | None  # comps


class BillingProfile(BaseModel):
    """The user's own, optional (GET and PUT /billing/profile): blank strings become None."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[
        Annotated[str, StringConstraints(max_length=100), AfterValidator(_printable)] | None,
        BeforeValidator(_blank_to_none),
    ]
    email: Annotated[Email | None, BeforeValidator(_blank_to_none)]


class ComplimentaryView(BaseModel):
    plan: PlanRef
    reason: str
    granted_by: str
    granted_at: datetime


class BillingStatus(BaseModel):
    plan: PlanAccess
    periods: list[PeriodView]  # unexpired, in order
    complimentary: ComplimentaryView | None  # the open-ended plan
    open_order: OrderView | None
    profile: BillingProfile
    payments: PaymentsInfo


class WebhookAck(BaseModel):
    ok: bool


# ---------------- Public ----------------


class PublicSite(BaseModel):
    business_name: str
    contact_email: str | None
    contact_phone: str | None
    address: str | None


# ---------------- Admin: plans ----------------


class PlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: PlanName
    description: PlanDescription
    price_paise: Price
    rank: Rank
    status: PlanStatus
    recommended: StrictBool
    features: PlanFeaturesInput
    limits: LimitsInput
    chat_model_ids: ModelIds | None  # None: every enabled chat model
    default_chat_model_id: Id | None

    @model_validator(mode="after")
    def _consistent(self) -> "PlanInput":
        check_plan(self.status, self.recommended, self.chat_model_ids, self.default_chat_model_id)
        return self


def check_plan(
    status: PlanStatus, recommended: bool, chat_model_ids: list[int] | None, default_chat_model_id: int | None
) -> None:
    """The rules between a plan's fields (also for a PATCH merged with the stored plan). Raises ValueError."""
    if recommended and status == "retired":
        raise ValueError("A retired plan can't be the recommended one.")
    if chat_model_ids is not None and default_chat_model_id is not None and default_chat_model_id not in chat_model_ids:
        raise ValueError("The default chat model must be one of the plan's models.")


class PlanCreate(PlanInput):
    code: PlanCode  # set once, never changed


# PATCH fields that may be omitted but never null (chat_model_ids and default_chat_model_id have a null meaning).
_PLAN_NOT_NULL = ("name", "description", "price_paise", "rank", "status", "recommended", "features", "limits")


class PlanUpdate(BaseModel):
    """Omitted fields stay unchanged; the router checks the merged plan with `check_plan`."""

    model_config = ConfigDict(extra="forbid")

    name: PlanName | None = None
    description: PlanDescription | None = None
    price_paise: Price | None = None
    rank: Rank | None = None
    status: PlanStatus | None = None
    recommended: StrictBool | None = None
    features: PlanFeaturesInput | None = None
    limits: LimitsInput | None = None
    chat_model_ids: ModelIds | None = None
    default_chat_model_id: Id | None = None
    apply_to_current: StrictBool = False  # also rewrite the current and scheduled periods' snapshots (3.5)
    confirm_reduction: StrictBool = False  # required when that lowers what paid periods have

    @model_validator(mode="after")
    def _refuse_null(self) -> "PlanUpdate":
        nulls = [name for name in _PLAN_NOT_NULL if name in self.model_fields_set and getattr(self, name) is None]
        if nulls:
            raise ValueError(f"{', '.join(nulls)} can't be null")
        return self


class PlanCostEstimate(BaseModel):
    kept_paise: int  # the price after Razorpay's fee
    worst_case_paise: int  # every allowance used up
    worst_share: float  # of kept_paise
    typical_paise: int  # 40% of every allowance
    typical_share: float
    unlimited: list[str]  # limit names set to 0: no ceiling


class AdminPlan(BaseModel):
    id: int
    code: str
    name: str
    description: str
    price_paise: int
    rank: int
    status: PlanStatus
    recommended: bool
    features: PlanFeatures
    limits: UserLimits
    chat_model_ids: list[int] | None  # None: every enabled chat model
    default_chat_model_id: int | None
    current_users: int
    scheduled_periods: int
    complimentary_users: int  # open-ended
    used: bool  # a period, order or open-ended plan has used it: it can't be deleted
    cost: PlanCostEstimate
    created_at: datetime
    updated_at: datetime


class PlanSaved(BaseModel):
    plan: AdminPlan
    applied_periods: int


# ---------------- Admin: billing settings and setup ----------------


class CostInputs(BaseModel):
    """What the plan editor computes PlanCostEstimate from, as the server does (plans-billing 3.3)."""

    usd_inr: float
    standard_usd_per_mtok: float  # 3.20
    vision_usd_per_mtok: float
    image_usd: float
    fee_bp: int  # Razorpay's fee with GST on it, in basis points of the charge (236)


class BillingSettings(BaseModel):
    """app_state billing_settings; PUT /admin/billing/settings replaces it whole."""

    model_config = ConfigDict(extra="forbid")

    business_name: Annotated[
        str, StringConstraints(min_length=1, max_length=100), BeforeValidator(_strip), AfterValidator(_printable)
    ]
    address: Annotated[str, StringConstraints(max_length=500), BeforeValidator(_strip), AfterValidator(_text)]
    contact_email: Annotated[Email | None, BeforeValidator(_blank_to_none)]
    contact_phone: Annotated[
        Annotated[str, StringConstraints(max_length=20), AfterValidator(_printable)] | None,
        BeforeValidator(_blank_to_none),
    ]
    receipt_prefix: Annotated[str, StringConstraints(pattern=r"^[A-Z0-9]{1,4}$"), AfterValidator(_not_test_prefix)]
    upi_limit_paise: Annotated[StrictInt, Field(ge=0, le=MAX_PRICE_PAISE)]  # 0: no limit


class PaymentsSetup(BaseModel):
    configured: bool
    mode: PaymentsMode | None
    key_id_hint: str | None  # "rzp_test_…AbCd"; never the secret
    webhook_secret_set: bool
    previous_webhook_secret_set: bool
    last_webhook_at: datetime | None
    webhook_url: str  # to register in Razorpay's Dashboard
    webhook_events: list[str]
    test_links_created: int
    reconciler_last_run_at: datetime | None
    last_error: str | None  # e.g. the keys were rejected
    paused_orders: int  # of the other mode
    testers: int
    email_reminders: bool


class BillingAdminConfig(BaseModel):
    settings: BillingSettings
    payments: PaymentsSetup
    estimate: CostInputs


# ---------------- Admin: orders, payments, refunds and disputes ----------------


class UserRef(BaseModel):
    id: int | None  # None: deleted
    username: str


class AdminOrder(OrderView):
    id: int
    user: UserRef
    mode: OrderMode
    razorpay_link_id: str | None
    razorpay_order_id: str | None
    review_reason: str | None
    review_note: str | None
    checks: int
    last_checked_at: datetime | None
    next_check_at: datetime | None
    last_error: str | None
    paused: bool  # of the other mode than the server's keys


class OrderEvent(BaseModel):
    at: datetime
    source: str
    type: str
    detail: dict[str, Any]
    error: str | None


class AdminOrderDetail(AdminOrder):
    events: list[OrderEvent]


class AdminOrderPage(BaseModel):
    items: list[AdminOrder]
    total: int


class RefundView(BaseModel):
    id: int
    amount_paise: int
    status: RefundStatus
    initiated_by: Literal["admin", "razorpay"]
    admin_username: str | None
    reason: str | None
    end_access: bool
    created_at: datetime
    processed_at: datetime | None
    last_error: str | None


class DisputeView(BaseModel):
    id: int
    razorpay_dispute_id: str
    amount_paise: int
    status: DisputeStatus
    reason_code: str | None
    respond_by: datetime | None
    created_at: datetime
    updated_at: datetime


class AdminPayment(PaymentView):
    user: UserRef
    order_public_id: str | None
    razorpay_payment_id: str | None
    fee_paise: int | None
    fee_tax_paise: int | None
    refundable_paise: int
    credit_used_paise: int  # credit later `now` orders took from its period
    refunds: list[RefundView]
    disputes: list[DisputeView]
    offline_reference: str | None
    mode: OrderMode


class AdminPaymentPage(BaseModel):
    items: list[AdminPayment]
    total: int


class RefundRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount_paise: Paise | None  # None: all that's refundable
    reason: Reason
    end_access: StrictBool  # a partial refund ends access only with this


class OfflinePaymentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: Id
    plan_id: Id
    amount_paise: Paise
    received_on: date
    reference: Annotated[
        str, StringConstraints(min_length=1, max_length=64), BeforeValidator(_strip), AfterValidator(_printable)
    ]
    note: Annotated[Note | None, BeforeValidator(_blank_to_none)]  # required below the plan's price (the router)
    apply: StrictBool  # False: a receipt only


class ReconcileResult(BaseModel):
    checked: int


# ---------------- Admin: a user's plan ----------------


class UserGrant(BaseModel):
    """A complimentary plan; AdminUserCreate.grant starts it now."""

    model_config = ConfigDict(extra="forbid")

    plan_id: Id
    days: Annotated[StrictInt, Field(ge=1, le=90)] | None  # None: open-ended
    reason: Reason


class GrantRequest(UserGrant):
    start: Literal["now", "after_current"]  # ignored when open-ended


class ChangePlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: Id
    reason: Reason


class RevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Reason


class EndComplimentaryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Reason


class ResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: Note


class BillingEventView(BaseModel):
    at: datetime
    source: str
    type: str
    actor: str | None
    detail: dict[str, Any]


class UserBilling(BaseModel):
    plan: PlanAccess
    periods: list[PeriodView]  # every one, newest first, at most 100
    complimentary: ComplimentaryView | None
    orders: list[AdminOrder]
    payments: list[AdminPayment]
    profile: BillingProfile
    events: list[BillingEventView]  # newest 50


# ---------------- Admin: the summary ----------------


class AttentionItem(BaseModel):
    kind: AttentionKind
    message: str
    detail: str | None
    order_public_id: str | None
    payment_id: int | None
    count: int | None


class UserPlanRow(BaseModel):
    user: UserRef
    plan: str | None
    date: date  # IST: valid through, or the day it ended
    last_payment_at: datetime | None


class PlanRevenue(BaseModel):
    plan: str
    payments: int
    gross_paise: int


class PlanUsers(BaseModel):
    plan: str
    paid: int
    complimentary: int


class MonthTotal(BaseModel):
    month: str  # "YYYY-MM", IST
    gross_paise: int
    net_paise: int


class BillingSummary(BaseModel):
    month: str  # "YYYY-MM", IST
    gross_paise: int
    fee_paise: int
    refunded_paise: int
    disputed_paise: int
    net_paise: int  # gross − fees − refunds − disputes lost
    payments: int
    refunds: int
    by_plan: list[PlanRevenue]
    current_users: list[PlanUsers]
    users_without_plan: int
    months: list[MonthTotal]  # the 12 months up to this one
    expiring: list[UserPlanRow]  # valid through within 7 days, nothing after
    lapsed: list[UserPlanRow]  # ended within the last 30 days
    attention: list[AttentionItem]
