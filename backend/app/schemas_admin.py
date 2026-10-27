"""Request and response schemas of the admin API (SPEC.md section 6, "Admin")."""

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, StringConstraints

from app.config import settings
from app.schemas import MAX_INT, UserLimits
from app.schemas_billing import PlanRef, PlanState, UserGrant

# Largest Postgres BIGINT: bigger values would make the driver fail with a 500.
MAX_BIGINT = 9_223_372_036_854_775_807

# Infrastructure secrets that match the *_API_KEY pattern but must never be sent to a model provider.
_RESERVED_KEY_ENVS = frozenset({"QDRANT_API_KEY"})

ModelKind = Literal["chat", "embedding", "vision", "image"]


def _strip(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


def _strip_lower(value: object) -> object:
    return value.strip().lower() if isinstance(value, str) else value


def _blank_to_none(value: object) -> object:
    if isinstance(value, str):
        return value.strip() or None
    return value


def _blank_password_to_none(value: object) -> object:
    # Only a blank password means "keep the current one"; a real one is kept exactly (create and login don't strip).
    return None if isinstance(value, str) and not value.strip() else value


def _printable(value: str) -> str:
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("must not contain control characters")
    return value


def _not_reserved(value: str) -> str:
    if value in _RESERVED_KEY_ENVS:
        raise ValueError(f"{value} is reserved for the app's own infrastructure")
    return value


# A BeforeValidator listed after StringConstraints wraps them, so it runs first and the
# constraints still report their specific errors (e.g. "at least 3 characters").
Username = Annotated[
    str, StringConstraints(min_length=3, max_length=64, pattern=r"^[a-z0-9_.-]+$"), BeforeValidator(_strip_lower)
]
Password = Annotated[str, Field(min_length=8, max_length=256)]
Count = Annotated[int, Field(ge=0, le=MAX_INT)]
TokenLimit = Annotated[int, Field(ge=0, le=MAX_BIGINT)]
UploadMb = Annotated[int, Field(ge=1, le=settings.max_upload_mb_hard)]
ModelIds = Annotated[list[Annotated[int, Field(ge=1, le=MAX_INT)]], Field(max_length=1000)]

DisplayName = Annotated[
    str, StringConstraints(min_length=1, max_length=100), BeforeValidator(_strip), AfterValidator(_printable)
]
LiteLLMModel = Annotated[
    str, StringConstraints(min_length=1, max_length=200, pattern=r"^[^\s\x00-\x1f\x7f]+$"), BeforeValidator(_strip)
]
ApiBase = Annotated[
    str, StringConstraints(max_length=500, pattern=r"^https?://[^\s\x00-\x1f\x7f/?#]+[^\s\x00-\x1f\x7f]*$")
]
ApiKeyEnv = Annotated[
    str, StringConstraints(max_length=100, pattern=r"^[A-Z][A-Z0-9_]*_API_KEY$"), AfterValidator(_not_reserved)
]
MaxOutputTokens = Annotated[int, Field(ge=1, le=1_000_000)]
Temperature = Annotated[float, Field(ge=0, le=2, allow_inf_nan=False)]
CostPerMtok = Annotated[float, Field(ge=0, allow_inf_nan=False)]


# ---------------- Users ----------------

LimitName = Literal[
    "monthly_token_limit",
    "rate_limit_per_min",
    "max_upload_mb",
    "max_documents",
    "max_storage_mb",
    "monthly_image_limit",
    "vision_monthly_token_limit",
]
UNLIMITED_ALLOWANCE = "Confirm an unlimited chat allowance."


class LimitOverrides(BaseModel):
    """The user's own values of the plan's limits (users' limit columns): null = the plan's value; 0 = unlimited."""

    monthly_token_limit: int | None
    rate_limit_per_min: int | None
    max_upload_mb: int | None
    max_documents: int | None
    max_storage_mb: int | None
    monthly_image_limit: int | None
    vision_monthly_token_limit: int | None


class LimitOverridesInput(BaseModel):
    """Overrides as the admin sends them: a key present sets it (null clears it, back to the plan's value); absent keys
    stay as they are."""

    model_config = ConfigDict(extra="forbid")

    monthly_token_limit: TokenLimit | None = None
    rate_limit_per_min: Count | None = None
    max_upload_mb: UploadMb | None = None
    max_documents: Count | None = None
    max_storage_mb: Count | None = None
    monthly_image_limit: Count | None = None
    vision_monthly_token_limit: TokenLimit | None = None

    def changes(self) -> dict[str, int | None]:
        return self.model_dump(include=self.model_fields_set)


class AdminUserPlan(BaseModel):
    state: PlanState
    plan: PlanRef | None  # current (period or open-ended), or the last one when expired
    valid_through: date | None  # IST date; None: open-ended (or none)
    paid_through: datetime | None
    complimentary: bool
    open_ended: bool
    test_mode: bool
    expiring_soon: bool
    ended_at: datetime | None  # expired: when access ended


class AdminUserUsage(BaseModel):
    """In the user's allowance window (the current period, or the UTC calendar month)."""

    chat_tokens: int
    chat_allowance_used: int  # standard tokens, against limits.monthly_token_limit
    embedding_tokens: int
    vision_tokens: int
    images: int
    cost_usd: float
    window_start: datetime
    window_end: datetime


class AdminUser(BaseModel):
    id: int
    username: str
    is_admin: bool
    is_active: bool
    restrict_models: bool
    allowed_model_ids: list[int]
    created_at: datetime
    last_login_at: datetime | None
    usage: AdminUserUsage
    document_count: int
    storage_bytes: int
    billing_tester: bool  # may check out while test keys run
    limits: UserLimits  # effective: the plan's merged with the overrides
    overrides: LimitOverrides
    above_plan: list[LimitName]  # overrides more generous than the plan's value
    no_models: bool  # the allowed list and the plan's models don't overlap
    plan: AdminUserPlan


class AdminUserCreate(BaseModel):
    """A new user has no plan unless `grant` gives one (from now). An override of 0 for monthly_token_limit (an
    unlimited chat allowance) needs confirm_unlimited (422 otherwise)."""

    model_config = ConfigDict(extra="forbid")

    username: Username
    password: Password
    is_active: bool = True
    restrict_models: bool = False
    allowed_model_ids: ModelIds = Field(default_factory=list)
    billing_tester: bool = False
    overrides: LimitOverridesInput | None = None
    grant: UserGrant | None = None
    confirm_unlimited: StrictBool = False


class AdminUserUpdate(BaseModel):
    """Omitted or null fields stay unchanged (overrides: see LimitOverridesInput); an empty password means "keep the
    current one". An override of 0 for monthly_token_limit needs confirm_unlimited (422 otherwise)."""

    model_config = ConfigDict(extra="forbid")

    password: Annotated[Password | None, BeforeValidator(_blank_password_to_none)] = None
    is_active: bool | None = None
    restrict_models: bool | None = None
    allowed_model_ids: ModelIds | None = None
    billing_tester: bool | None = None
    overrides: LimitOverridesInput | None = None
    confirm_unlimited: StrictBool = False


# ---------------- Models ----------------


class AdminModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    display_name: str
    litellm_model: str
    kind: ModelKind
    enabled: bool
    is_default: bool
    api_base: str | None
    api_key_env: str | None
    max_output_tokens: int | None
    temperature: float | None
    input_cost_per_mtok: float | None
    output_cost_per_mtok: float | None
    embedding_dim: int | None
    created_at: datetime
    updated_at: datetime


class _ModelOptions(BaseModel):
    """Optional model settings; null (or an empty string) means "not set"."""

    api_base: Annotated[ApiBase | None, BeforeValidator(_blank_to_none)] = None
    api_key_env: Annotated[ApiKeyEnv | None, BeforeValidator(_blank_to_none)] = None
    max_output_tokens: MaxOutputTokens | None = None
    temperature: Temperature | None = None
    input_cost_per_mtok: CostPerMtok | None = None
    output_cost_per_mtok: CostPerMtok | None = None


class AdminModelCreate(_ModelOptions):
    display_name: DisplayName
    litellm_model: LiteLLMModel
    kind: ModelKind
    enabled: bool = True
    is_default: bool = False


class AdminModelUpdate(_ModelOptions):
    display_name: DisplayName | None = None
    litellm_model: LiteLLMModel | None = None
    enabled: bool | None = None
    is_default: bool | None = None

    def changes(self) -> dict[str, Any]:
        """Fields the client sent. null clears an optional setting and leaves a required field unchanged."""
        optional = _ModelOptions.model_fields.keys()
        return {
            name: value
            for name, value in self.model_dump(exclude_unset=True).items()
            if value is not None or name in optional
        }


class ModelTestResult(BaseModel):
    ok: bool
    message: str
    latency_ms: int
    embedding_dim: int | None
    image_data_url: str | None = None  # image models: a data: URL of a small thumbnail of the test image


# ---------------- Dashboard, usage, re-indexing ----------------


class AdminConfig(BaseModel):
    defaults: UserLimits  # a user without a plan or overrides: the cheapest active plan's limits (all 0 without one)
    max_upload_mb_hard: int
    admin_username: str


class ModelRef(BaseModel):
    id: int
    display_name: str


class AdminStats(BaseModel):
    users: int
    documents: int
    stale_documents: int
    failed_documents: int
    active_embedding_model: ModelRef | None
    active_vision_model: ModelRef | None
    active_image_model: ModelRef | None = None
    queue_length: int


class UsageTotals(BaseModel):
    chat_tokens: int
    embedding_tokens: int
    vision_tokens: int
    images: int = 0
    cost_usd: float
    requests: int


class UserUsage(BaseModel):
    user_id: int
    username: str
    chat_tokens: int
    embedding_tokens: int
    vision_tokens: int
    images: int = 0
    cost_usd: float
    requests: int


class ModelUsage(BaseModel):
    model_name: str
    kind: ModelKind
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    requests: int


class DailyUsage(BaseModel):
    date: str
    chat_tokens: int
    embedding_tokens: int
    vision_tokens: int
    images: int = 0
    cost_usd: float


class UsageReport(BaseModel):
    month: str
    totals: UsageTotals
    by_user: list[UserUsage]
    by_model: list[ModelUsage]
    daily: list[DailyUsage]


class ReindexRequest(BaseModel):
    only_stale: bool = True


class ReindexResult(BaseModel):
    queued: int
