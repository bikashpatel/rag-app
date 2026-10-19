"""Request and response shapes of the plugin framework (SPEC.md section 6, "Plugins" and "Admin";
docs/briefs/plugins-gmail.md). Contract only."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.plugins.gmail.schemas import GmailActivityCard, GmailDraftCard, GmailImportCard, GmailSendCard
from app.schemas import MAX_INT

ModelId = Annotated[int, Field(ge=1, le=MAX_INT)]
CapabilityId = Annotated[str, StringConstraints(pattern=r"^[a-z_]{1,24}$")]
ConnectionState = Literal["connected", "reconnect_needed"]
ConnectionReason = Literal["revoked", "expired_testing", "key_changed", "scopes_missing"]
OAuthResult = Literal["connected", "denied", "expired", "invalid", "no_access", "disabled", "error"]
PluginAction = Literal[
    "connect",
    "disconnect",
    "reconnect_needed",
    "search",
    "read",
    "import",
    "draft",
    "send_proposed",
    "send",
    "send_cancelled",
]

# A card a plugin added to an answer (SSE `plugin` event data, ChatMessage.plugin_cards).
PluginCard = Annotated[
    GmailActivityCard | GmailDraftCard | GmailSendCard | GmailImportCard, Field(discriminator="type")
]


# ---------------- User ----------------


class PluginCapabilityInfo(BaseModel):
    id: str
    name: str
    description: str
    enabled: bool  # by the admin
    granted: bool  # by the caller's connection (False without one)


class PluginConnectionStatus(BaseModel):
    """The caller's own connection; never anyone else's."""

    account_email: str
    status: ConnectionState
    status_reason: ConnectionReason | None
    status_message: str | None  # what to tell the user when a reconnect is needed
    granted_scopes: list[str]
    chat_enabled: bool  # "Use in chat": the plugin's chat tools are offered
    connected_at: datetime
    last_used_at: datetime | None
    refresh_expires_at: datetime | None  # Google testing mode: when Google ends the connection (estimate)


class PluginInfo(BaseModel):
    """GET /plugins lists every registered plugin (the catalog), whatever its state; see SPEC.md for the status a client
    derives from `configured`, `enabled` and `connection`."""

    id: str
    name: str
    description: str
    auth_type: Literal["oauth2"]
    # The settings it needs are in .env (registry.configured() is empty); only the admin API says which are missing
    configured: bool = True
    enabled: bool  # turned on by the admin and configured: it can be connected and used
    # The provider app is in testing mode (Google: GOOGLE_OAUTH_TESTING_MODE): only accounts the admin added as test
    # users can connect (others see Google's "Access blocked" page and never come back); connections end after 7 days
    testing_mode: bool
    supports_import: bool
    import_max_items: int | None  # GMAIL_IMPORT_MAX_ITEMS for plugins that import, else None
    capabilities: list[PluginCapabilityInfo]
    connection: PluginConnectionStatus | None


class PluginConnectRequest(BaseModel):
    capabilities: list[CapabilityId] | None = Field(default=None, max_length=10)  # None: every enabled capability


class PluginConnectResponse(BaseModel):
    authorize_url: str


class PluginConnectionUpdate(BaseModel):
    chat_enabled: bool


# ---------------- Admin ----------------


class AdminPluginCapability(BaseModel):
    id: str
    name: str
    description: str
    scopes: list[str]  # what this capability requests on its own
    enabled: bool
    # Shown under the switch, e.g. for Gmail drafts: Google's draft permission also allows sending; the app still sends
    # only when the user clicks Send
    note: str | None


class PluginActionCount(BaseModel):
    action: PluginAction
    ok: int
    failed: int
    refused: int  # the request check refused it (side effects only)


class PluginActivitySummary(BaseModel):
    month: str  # "YYYY-MM"
    actions: list[PluginActionCount]


class PluginConnectionCounts(BaseModel):
    connected: int
    reconnect_needed: int
    # Connections whose tokens are encrypted with a key other than the first TOKEN_ENCRYPTION_KEY (not re-encrypted at
    # startup because no configured key can read them). Remove an old key only while this is 0.
    old_key: int


class AdminPlugin(BaseModel):
    id: str
    name: str
    description: str
    auth_type: Literal["oauth2"]
    supports_import: bool
    configured: bool
    missing_settings: list[str]  # env vars to set in .env, e.g. ["GOOGLE_OAUTH_CLIENT_ID", "TOKEN_ENCRYPTION_KEY"]
    test_endpoints: bool  # the OAuth or API endpoints aren't the provider's (a test setup)
    enabled: bool
    capabilities: list[AdminPluginCapability]
    requested_scopes: list[str]  # exactly what a connect asks for with the current switches (register them with Google)
    redirect_uris: list[str]  # to register with the provider: one per PUBLIC_BASE_URLS entry
    testing_mode: bool  # GOOGLE_OAUTH_TESTING_MODE: connections end after 7 days
    connections: PluginConnectionCounts
    activity: PluginActivitySummary  # this month


class AdminPluginUpdate(BaseModel):
    """Omitted fields stay unchanged; `capabilities` may list some ids only."""

    enabled: bool | None = None
    capabilities: dict[CapabilityId, bool] | None = Field(default=None, max_length=10)


class PluginUserActivity(BaseModel):
    user_id: int
    username: str
    actions: list[PluginActionCount]


class PluginActivityReport(BaseModel):
    month: str
    totals: list[PluginActionCount]
    by_user: list[PluginUserActivity]


class DisconnectAllResult(BaseModel):
    disconnected: int


class RequestCheckTest(BaseModel):
    """POST /admin/plugins/request-check/test: run the request check (brief 4.5) once with a fixed message."""

    model_id: ModelId  # a chat model


class RequestCheckTestResult(BaseModel):
    ok: bool  # the model answered with valid JSON and the expected actions (draft only)
    message: str
    latency_ms: int
    answer: dict[str, bool] | None  # the five actions as the model answered them; None without valid JSON
