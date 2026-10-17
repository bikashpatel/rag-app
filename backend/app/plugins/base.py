"""The plugin interfaces (docs/briefs/plugins-gmail.md, section 4.2): what a plugin declares and implements, the access
a user has to it, the chat tool set it offers, and the errors it raises."""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Protocol

from fastapi import APIRouter
from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Message, MessagePluginItem, User
from app.schemas import EmailSource
from app.schemas_plugins import ConnectionReason, PluginCard
from app.services import document_tool, llm

if TYPE_CHECKING:
    from app.plugins.answer import AnswerContext

Origin = Literal["chat", "ui", "system"]
SideEffect = Literal["file", "image", "draft", "send", "import"]
ConnectionState = Literal["connected", "reconnect_needed"]

ToolResult = document_tool.ToolResult  # the last item of a run: the content of the tool message

_CARDS: TypeAdapter[PluginCard] = TypeAdapter(PluginCard)


@dataclass(frozen=True)
class Capability:
    id: str  # [a-z_]{1,24}; Gmail: "read", "draft", "send"
    name: str
    description: str  # one sentence, shown to the admin and the user
    scopes: tuple[str, ...]  # what it requests on its own (AdminPluginCapability.scopes)
    granted_by: tuple[str, ...]  # any of these granted scopes provides it (includes `scopes`)
    default_enabled: bool = True
    admin_note: str | None = None  # AdminPluginCapability.note


@dataclass(frozen=True)
class OAuthEndpoints:
    authorize_url: str  # browser-facing
    token_url: str
    revoke_url: str | None
    client_id: str
    client_secret: str
    identity_scopes: tuple[str, ...]  # always requested, e.g. ("openid", "email")
    authorize_params: Mapping[str, str]  # extra query parameters (access_type, prompt, include_granted_scopes)


@dataclass(frozen=True)
class TokenResponse:
    """The token endpoint's JSON, parsed."""

    access_token: str
    expires_in: int
    refresh_token: str | None
    scopes: tuple[str, ...]  # `scope`, split on spaces
    id_token: str | None


@dataclass(frozen=True)
class AccountIdentity:
    subject: str  # the provider's stable account id (OIDC `sub`)
    email: str


@dataclass(frozen=True)
class ConnectionInfo:
    """A plugin_connections row without its tokens."""

    id: int
    user_id: int
    plugin_id: str
    account_email: str
    account_subject: str
    scopes: frozenset[str]
    status: ConnectionState
    status_reason: ConnectionReason | None
    chat_enabled: bool
    connected_at: datetime


@dataclass(frozen=True)
class PluginAccess:
    """What one user may do with one plugin right now."""

    plugin: "Plugin"
    user: User
    enabled: bool  # the admin's switch, and the plugin is configured
    enabled_capabilities: frozenset[str]  # by the admin
    connection: ConnectionInfo | None
    usable: frozenset[str]  # enabled and granted; empty unless enabled and the connection is "connected"


# ---------------- Errors ----------------


class PluginError(Exception):
    """User-facing: str() is safe to show the user and the model. `http_status` is what a route answers when it escapes
    (main.py's handler); subclasses set their own (a plugin's "not found" error, say, 404). Never 401: the client
    takes that as a signed-out session."""

    http_status: ClassVar[int] = 400


class NotConnected(PluginError):
    http_status = 409


_RECONNECT_MESSAGES: dict[str, str] = {
    "expired_testing": (
        "Google ended this connection: while this app is in Google's testing mode, connections last 7 days. "
        "Reconnect to keep using {name}."
    ),
    "revoked": "Google access was removed or has expired. Reconnect to keep using {name}.",
    "key_changed": "The server's encryption key changed, so the saved Google access can't be used. Reconnect.",
    "scopes_missing": "Google no longer allows this app to use your {account} as before. Reconnect and allow access.",
}


def reconnect_message(reason: str, plugin_name: str | None = None) -> str:
    """What to tell the user about a connection that needs a reconnect (PluginConnectionStatus.status_message)."""
    template = _RECONNECT_MESSAGES.get(reason, "This connection needs to be renewed. Reconnect to keep using {name}.")
    return template.format(name=plugin_name or "it", account=plugin_name or "account")


class ReconnectNeeded(PluginError):
    http_status = 409

    def __init__(self, reason: ConnectionReason, message: str | None = None) -> None:
        super().__init__(message or reconnect_message(reason))
        self.reason = reason


class AccountChanged(PluginError):
    """The connection now belongs to another provider account than the one this request started with (409)."""

    http_status = 409


class PluginUnavailable(PluginError):
    """Temporary: network, timeouts, 5xx, rate limits after retries, a misconfigured client."""

    http_status = 503


class PluginDisabled(PluginError):
    http_status = 403


# ---------------- Chat ----------------


@dataclass(frozen=True)
class PluginEvent:
    """A named SSE event a plugin runner yields: `plugin` (a card) or `sources` (the whole list)."""

    name: Literal["plugin", "sources"]
    data: dict[str, Any]


LoopEvent = dict[str, Any] | PluginEvent  # a dict: a `file` event (create_document, create_image)


class ToolRunner(Protocol):
    """The loop's per-tool interface (document_tool.FileSession and image_tool.ImageSession have it). The loop calls
    `begin_call` on every runner before each model call, `started` on the runner of the tool whose call began, and
    `progress` and `open_calls` on every runner: each runner reports only the calls announced to it."""

    def can_create(self) -> bool: ...  # may run another call in this answer

    def begin_call(self) -> None: ...

    def started(self, index: int) -> LoopEvent | None: ...

    def progress(self, index: int, step: llm.ChatStep) -> LoopEvent | None: ...

    def open_calls(self, message: str) -> Sequence[LoopEvent]: ...

    def cut_off(self, call: llm.ToolCall) -> AsyncGenerator[LoopEvent | ToolResult]: ...

    def run(self, call: llm.ToolCall) -> AsyncGenerator[LoopEvent | ToolResult]: ...


class ToolSession(Protocol):
    """One per answer and offered tool set."""

    def runner(self, tool_name: str) -> ToolRunner: ...


@dataclass(frozen=True)
class ChatToolSet:
    plugin_id: str
    tools: list[dict[str, Any]]  # OpenAI-format specs, fixed order
    instructions: str  # its system prompt section
    no_tools_note: str  # replaces `instructions` when the provider rejects tools (the loop's recovery 1)
    max_calls: int  # adds to the loop's model-call cap
    open_call_errors: Mapping[str, str]  # "failed" / "declined" / "incomplete" -> wording for an open call
    start: Callable[["AnswerContext"], ToolSession]


@dataclass(frozen=True)
class ChatPlan:
    toolset: ChatToolSet | None
    note: str | None  # system prompt note when no tools are offered (None: nothing)


def tool_names(toolset: ChatToolSet) -> list[str]:
    return [spec["function"]["name"] for spec in toolset.tools]


# ---------------- Plugins ----------------


class Plugin(ABC):
    id: ClassVar[str]  # [a-z][a-z0-9_]{1,31}; also the URL segment
    name: ClassVar[str]
    description: ClassVar[str]
    auth_type: ClassVar[Literal["oauth2"]] = "oauth2"
    capabilities: ClassVar[tuple[Capability, ...]]
    supports_import: ClassVar[bool] = False

    @abstractmethod
    def missing_settings(self) -> list[str]:
        """Its own env vars that are empty ([] = configured)."""

    @abstractmethod
    def oauth(self) -> OAuthEndpoints: ...

    @abstractmethod
    def test_endpoints(self) -> bool:
        """Any endpoint isn't the provider's default (a test setup)."""

    def testing_mode(self) -> bool:
        """The provider app only admits test users, and ends connections after 7 days (Google's "Testing" status)."""
        return False

    @abstractmethod
    def scopes_for(self, capabilities: frozenset[str]) -> tuple[str, ...]:
        """Provider scopes to request for these capabilities, in a fixed order, without the identity scopes."""

    @abstractmethod
    def identify(self, token: TokenResponse) -> AccountIdentity:
        """The account the token belongs to; raises PluginError (no id_token, wrong audience, ...)."""

    @abstractmethod
    async def plan_chat(self, db: AsyncSession, access: PluginAccess, *, tools_supported: bool) -> ChatPlan: ...

    def router(self) -> APIRouter | None:
        """Its own API, mounted at /api/plugins/<id> (the segments connect, oauth and connection are reserved)."""
        return None

    async def on_disconnect(self, db: AsyncSession, connection: ConnectionInfo) -> None:
        """In the transaction that deletes or replaces the connection, before it does."""
        return None

    async def on_conversation_deleted(self, db: AsyncSession, user: User, conversation_id: int) -> None:
        """In the transaction that deletes the conversation, before its commit."""
        return None

    async def on_startup(self) -> None:
        """At startup, with its own sessions; errors are logged, never fatal."""
        return None

    async def hydrate_cards(
        self, db: AsyncSession, user: User, items: Sequence[MessagePluginItem]
    ) -> list[PluginCard]:
        """The saved cards with their live state, in the same order; card_id "p<item id>". By default, as saved."""
        return [_CARDS.validate_python({**item.data, "card_id": f"p{item.id}"}) for item in items]

    async def history_notes(
        self,
        db: AsyncSession,
        user: User,
        messages: Sequence[Message],
        items: Mapping[int, Sequence[MessagePluginItem]],
    ) -> dict[int, str]:
        """message id -> an "(App note: …)" for the model's history about what that answer did with this plugin."""
        return {}

    def describe_source(self, source: EmailSource) -> str:
        """An email's line in the Sources section of a generated file."""
        return describe_email(source)


def describe_email(source: EmailSource) -> str:
    """The Sources line of an email: "Email: <subject> (<sender>, <YYYY-MM-DD>)"."""
    date = f", {source.date.date().isoformat()}" if source.date is not None else ""
    return f"Email: {source.subject} ({source.sender}{date})"
