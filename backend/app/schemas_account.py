"""Request and response shapes of the caller's own account (SPEC.md section 6, "Account";
docs/briefs/ui-shell-settings.md): preferences, sign-in history, usage, and deleting every conversation. Contract only.
`Preferences` itself is in app.schemas, because `Me` carries it."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from app.schemas import MAX_INT, AccentColor, ChatFontSize, ThemePreference

DeviceType = Literal["desktop", "mobile", "tablet", "unknown"]
UsageKind = Literal["chat", "embedding", "vision", "image"]

# PATCH fields that may be omitted but never null (only default_model_id has a null meaning).
_NOT_NULL = ("theme", "accent", "chat_font_size", "enter_to_send")


class PreferencesUpdate(BaseModel):
    """PATCH /me/preferences: omitted fields stay unchanged; `default_model_id: null` = the server's default chat
    model. Unknown fields are refused (422), and so is null for the other fields. Written strictly: no coercion, so
    `true` is never model 1 and `"off"` never false."""

    model_config = ConfigDict(extra="forbid")

    theme: ThemePreference | None = None
    accent: AccentColor | None = None
    chat_font_size: ChatFontSize | None = None
    enter_to_send: StrictBool | None = None
    # An enabled chat model the caller may use (GET /models), else 422
    default_model_id: Annotated[StrictInt, Field(ge=1, le=MAX_INT)] | None = None

    @model_validator(mode="after")
    def _refuse_null(self) -> "PreferencesUpdate":
        nulls = [name for name in _NOT_NULL if name in self.model_fields_set and getattr(self, name) is None]
        if nulls:
            raise ValueError(f"{', '.join(nulls)} can't be null")
        return self


class LoginEvent(BaseModel):
    """One successful sign-in (login_events row). Only the caller's own; never shown to the admin."""

    id: int
    created_at: datetime
    # Approximate, as nginx saw the client: IPv4 with the last part hidden ("203.0.113.x"), IPv6 as its /48 network
    # ("2001:db8:85a3::/48"); None when unknown. Behind Docker's port publishing every client has the same address.
    ip: str | None
    # `ip` isn't a public internet address (private, loopback, link-local or shared address space; derived from the
    # stored value on read): the sign-in came through this machine, Docker, a local network or a proxy. False when
    # `ip` is None.
    ip_private: bool
    browser: str | None  # parsed from the User-Agent, which isn't stored: e.g. "Chrome 141", "Safari 26", "curl"
    os: str | None  # e.g. "macOS", "Windows", "iOS", "Android", "Linux", "ChromeOS"
    device: DeviceType
    current: bool  # the sign-in that started the session making this request


class AccountInfo(BaseModel):
    """GET /me/account."""

    id: int
    username: str
    is_admin: bool
    password_managed_by_env: bool  # the env admin: POST /auth/change-password answers 400, so don't offer it
    created_at: datetime
    last_login_at: datetime | None  # also set for sign-ins from before login_events existed
    conversation_count: int
    recent_logins: list[LoginEvent]  # newest first, at most 20 (older ones are deleted)


class UsageAmount(BaseModel):
    used: int
    limit: int  # 0 = unlimited (always 0 for the admin, who bypasses every limit)


class UsageCosts(BaseModel):
    """Estimated USD this month (the models' prices, or LiteLLM's cost map), per usage kind."""

    total: float
    chat: float
    embedding: float
    vision: float
    image: float


class DocumentUsage(BaseModel):
    """As in DocumentConfig: only documents the caller owns count; 0 = unlimited."""

    count: int
    max_documents: int
    storage_bytes: int
    max_storage_mb: int
    max_upload_mb: int


class MyUsageDay(BaseModel):
    date: str  # "YYYY-MM-DD" (UTC)
    chat_tokens: int
    requests: int  # chat answers
    images: int
    vision_tokens: int
    embedding_tokens: int
    cost_usd: float


class MyUsageModel(BaseModel):
    model_name: str  # the model's display name while it exists, else the litellm id stored with the usage
    kind: UsageKind
    tokens: int  # prompt + completion (image models: prompt text + image tokens)
    requests: int  # chat: answers; image: images; embedding and vision: ingestion runs
    cost_usd: float


class MyUsage(BaseModel):
    """GET /me/usage: the caller's current UTC calendar month."""

    month: str  # "YYYY-MM"
    period_start: datetime  # the 1st, 00:00 UTC
    resets_at: datetime  # the 1st of next month, 00:00 UTC: the monthly counts start again
    chat_tokens: UsageAmount  # limit: monthly_token_limit (the only budget; prompt + completion of chat calls)
    images: UsageAmount  # limit: the monthly image limit
    vision_tokens: UsageAmount  # limit: VISION_MONTHLY_TOKEN_LIMIT
    embedding_tokens: int  # recorded, never limited
    requests: int  # chat answers this month
    rate_limit_per_min: int  # chat requests per minute; 0 = unlimited
    cost_usd: UsageCosts
    documents: DocumentUsage
    daily: list[MyUsageDay]  # every UTC day from the 1st through today, zeros included
    by_model: list[MyUsageModel]  # cost descending


class ConversationsDeleted(BaseModel):
    """DELETE /conversations."""

    deleted: int
