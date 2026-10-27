"""The built-in plugins and what each user may do with them (brief 4.3): the admin's switches (plugin_settings, read on
every request), configuration, access (which the user's plan bounds, plans-billing 5.6), the chat plan of a turn,
history notes and taint, and the FastAPI dependencies of plugin routes."""

import importlib
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal, get_db
from app.deps import current_entitlements, get_entitled_user
from app.errors import AppError
from app.models import (
    ImportedDocument,
    LLMModel,
    Message,
    MessagePluginItem,
    PluginConnection,
    PluginSetting,
    User,
    utcnow,
)
from app.plugins import connections, crypto, oauth
from app.plugins.base import ChatToolSet, NotConnected, Plugin, PluginAccess, reconnect_message
from app.schemas_plugins import PluginCard
from app.services import llm, rag
from app.services.billing import entitlements
from app.services.billing.entitlements import Entitlements
from app.services.document_tool import unlock_phrase

logger = logging.getLogger(__name__)

# module:class of each built-in plugin, imported on first use (an import error fails startup loudly).
_BUILTINS: tuple[tuple[str, str], ...] = (("app.plugins.gmail.plugin", "GmailPlugin"),)

# Google reports the identity scopes as URLs; connect requests may use the short names.
_SCOPE_ALIASES = {
    "email": "https://www.googleapis.com/auth/userinfo.email",
    "profile": "https://www.googleapis.com/auth/userinfo.profile",
}

_builtins: list[Plugin] | None = None
_override: list[Plugin] | None = None
# plugin id -> its system prompt note for a connected user whose plan doesn't include it ({unlock}: which plan does)
_plan_notes: dict[str, str] = {}
_PLAN_NOTE = (
    "The user's plan doesn't include the {name} plugin. If the user asks for something that needs {name}, tell them "
    "their plan doesn't include it and that {unlock} does; you can't use {name} in this conversation."
)


def builtin_plugins() -> list[Plugin]:
    """One instance of each built-in plugin class, imported on first use (an import error propagates: the app doesn't
    start without its plugins)."""
    global _builtins
    if _builtins is None:
        _builtins = [
            getattr(importlib.import_module(module_name), class_name)() for module_name, class_name in _BUILTINS
        ]
    return list(_builtins)


def all_plugins() -> list[Plugin]:
    """The plugins in registry order: the built-ins, or the test override (use_plugins)."""
    return list(_override) if _override is not None else builtin_plugins()


def get_plugin(plugin_id: str) -> Plugin | None:
    return next((plugin for plugin in all_plugins() if plugin.id == plugin_id), None)


def plugin_name(plugin_id: str) -> str:
    """The display name of a plugin id (an unregistered one: the id capitalized)."""
    plugin = get_plugin(plugin_id)
    return plugin.name if plugin is not None else plugin_id.capitalize()


@contextmanager
def use_plugins(plugins: Sequence[Plugin]) -> Iterator[None]:
    """Tests: the registry holds exactly `plugins` inside the block."""
    global _override
    previous = _override
    _override = list(plugins)
    try:
        yield
    finally:
        _override = previous


# ---------------- Scopes and configuration ----------------


def normalize_scope(scope: str) -> str:
    return _SCOPE_ALIASES.get(scope, scope)


def normalize_scopes(scopes: Iterable[str]) -> set[str]:
    return {normalize_scope(scope) for scope in scopes if scope}


def granted_capabilities(plugin: Plugin, scopes: Iterable[str]) -> frozenset[str]:
    """The capabilities some granted scope provides (Capability.granted_by)."""
    granted = normalize_scopes(scopes)
    return frozenset(
        capability.id for capability in plugin.capabilities if granted & normalize_scopes(capability.granted_by)
    )


def requested_scopes(plugin: Plugin, capabilities: frozenset[str]) -> tuple[str, ...]:
    """What a connect asks for: the identity scopes, then the plugin's scopes for these capabilities."""
    scopes = [*plugin.oauth().identity_scopes, *plugin.scopes_for(capabilities)]
    return tuple(dict.fromkeys(scopes))


def configured(plugin: Plugin) -> list[str]:
    """The settings still missing in .env ([] = configured)."""
    missing = list(plugin.missing_settings())
    if crypto.token_cipher() is None:
        missing.append("TOKEN_ENCRYPTION_KEY")
    if not oauth.public_base_urls():
        missing.append("PUBLIC_BASE_URLS")
    return missing


# ---------------- The admin's switches ----------------


@dataclass(frozen=True)
class PluginState:
    enabled: bool
    capabilities: dict[str, bool]  # every capability of the plugin, in its order

    @property
    def enabled_capabilities(self) -> frozenset[str]:
        return frozenset(capability for capability, on in self.capabilities.items() if on)


def _state_of(plugin: Plugin, row: PluginSetting | None) -> PluginState:
    stored = row.capabilities if row is not None and isinstance(row.capabilities, dict) else {}
    switches = {
        capability.id: bool(stored.get(capability.id, capability.default_enabled)) for capability in plugin.capabilities
    }
    return PluginState(enabled=bool(row is not None and row.enabled), capabilities=switches)


async def state(db: AsyncSession, plugin: Plugin) -> PluginState:
    return _state_of(plugin, await db.get(PluginSetting, plugin.id, populate_existing=True))


async def set_state(
    db: AsyncSession, plugin: Plugin, *, enabled: bool | None, capabilities: Mapping[str, bool] | None
) -> PluginState:
    """Apply the changes (ids must be the plugin's) and commit."""
    row = await db.get(PluginSetting, plugin.id, with_for_update=True, populate_existing=True)
    if row is None:
        row = PluginSetting(plugin_id=plugin.id, enabled=False, capabilities={})
        db.add(row)
    if enabled is not None:
        row.enabled = enabled
    if capabilities:
        row.capabilities = {**(row.capabilities or {}), **{key: bool(value) for key, value in capabilities.items()}}
    row.updated_at = utcnow()
    await db.commit()
    return _state_of(plugin, row)


# ---------------- Access ----------------


@dataclass(frozen=True)
class PlanPluginAccess(PluginAccess):
    """PluginAccess with the entitlements it was decided by: `entitled` when the user's plan includes the plugin
    (always for the admin); `usable` is empty otherwise, and the connection is left untouched."""

    ent: Entitlements

    @property
    def entitled(self) -> bool:
        return entitlements.allows_plugin(self.ent, self.plugin.id)


async def access_for(
    db: AsyncSession, user: User, plugin: Plugin, ent: Entitlements | None = None
) -> PlanPluginAccess:
    """The user's access now (`ent`: their entitlements, loaded when not given). A connection in Google's testing mode
    that is 7 days old turns reconnect needed (expired_testing) here, without a provider call, so the user sees it
    before a chat fails."""
    ent = ent if ent is not None else await entitlements.load(db, user)
    current = await state(db, plugin)
    enabled = current.enabled and not configured(plugin)
    row = await db.scalar(
        select(PluginConnection)
        .where(PluginConnection.user_id == user.id, PluginConnection.plugin_id == plugin.id)
        .execution_options(populate_existing=True)
    )
    if row is not None and row.status == "connected" and connections.testing_expired(plugin, row.connected_at):
        await connections.mark_reconnect_needed(row.id, "expired_testing")
        await db.refresh(row)
    info = connections.connection_info(row) if row is not None else None
    usable: frozenset[str] = frozenset()
    if enabled and info is not None and info.status == "connected" and entitlements.allows_plugin(ent, plugin.id):
        usable = current.enabled_capabilities & granted_capabilities(plugin, info.scopes)
    return PlanPluginAccess(
        plugin=plugin,
        user=user,
        enabled=enabled,
        enabled_capabilities=current.enabled_capabilities,
        connection=info,
        usable=usable,
        ent=ent,
    )


async def live_access(user_id: int, plugin: Plugin, ent: Entitlements | None = None) -> PlanPluginAccess:
    """access_for in a session of its own: what a plugin action re-checks just before it runs (P26). A chat action
    passes its turn's entitlements, which hold for the whole answer."""
    async with SessionLocal() as db:
        user = await db.get(User, user_id)
        if user is None:
            raise NotConnected("The account no longer exists.")
        return await access_for(db, user, plugin, ent)


async def not_in_plan(db: AsyncSession, ent: Entitlements, plugin: Plugin) -> AppError:
    """402 feature_not_in_plan (feature "plugin:<id>"), naming the cheapest active plan that includes the plugin."""
    return await entitlements.feature_error(db, ent, f"plugin:{plugin.id}", plugin.name)


def in_plan(plugin_id: str) -> Callable[..., Awaitable[User]]:
    """FastAPI dependency of a plugin route that works while the plugin is turned off (a pending send's state): the
    user needs access, and their plan must include the plugin (402)."""

    async def dependency(
        request: Request, user: User = Depends(get_entitled_user), db: AsyncSession = Depends(get_db)
    ) -> User:
        plugin = get_plugin(plugin_id)
        if plugin is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown plugin")
        ent = current_entitlements(request)
        if not entitlements.allows_plugin(ent, plugin.id):
            raise await not_in_plan(db, ent, plugin)
        return user

    return dependency


def require(
    plugin_id: str, capability: str | None = None
) -> Callable[..., Awaitable[tuple[User, PlanPluginAccess]]]:
    """FastAPI dependency of a plugin route: the session user, who needs access (402), and their access to the
    plugin. The plugin or `capability` turned off: 403; outside the user's plan: 402 feature_not_in_plan; no
    connection, a reconnect needed or `capability` not granted: 409 (never 401, which signs the client out)."""

    async def dependency(
        request: Request, user: User = Depends(get_entitled_user), db: AsyncSession = Depends(get_db)
    ) -> tuple[User, PlanPluginAccess]:
        plugin = get_plugin(plugin_id)
        if plugin is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown plugin")
        access = await access_for(db, user, plugin, current_entitlements(request))
        await db.commit()  # end the read transaction; the route may call the provider next
        if not access.enabled:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"{plugin.name} is turned off.")
        wanted = next((item for item in plugin.capabilities if item.id == capability), None)
        if capability is not None and capability not in access.enabled_capabilities:
            label = wanted.name if wanted is not None else capability
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=f"{plugin.name}: {label} is turned off by the admin."
            )
        if not access.entitled:
            raise await not_in_plan(db, access.ent, plugin)
        connection = access.connection
        if connection is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"{plugin.name} isn't connected.")
        if connection.status != "connected":
            reason = connection.status_reason or "revoked"
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=reconnect_message(reason, plugin.name)
            )
        if capability is not None and capability not in access.usable:
            label = wanted.name if wanted is not None else capability
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"{plugin.name}: {label} access wasn't granted. Grant it in Settings → Plugins & data.",
            )
        return user, access

    return dependency


# ---------------- Chat ----------------


@dataclass(frozen=True)
class PluginChatPlan:
    toolsets: list[ChatToolSet] = field(default_factory=list)  # offered, in registry order
    notes: list[str] = field(default_factory=list)  # from plugins that offer no tools this turn

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [tool for toolset in self.toolsets for tool in toolset.tools]

    @property
    def max_calls(self) -> int:
        return sum(toolset.max_calls for toolset in self.toolsets)

    def instructions(self, other: str | None) -> str | None:
        """The toolsets' instructions and the notes, by blank lines; with the App-note sentence when a toolset is
        offered and `other` (the file and image instructions) doesn't say it already."""
        parts = [toolset.instructions for toolset in self.toolsets] + self.notes
        if self.toolsets and rag.APP_NOTE_SENTENCE not in (other or ""):
            parts.append(rag.APP_NOTE_SENTENCE)
        return "\n\n".join(parts) or None

    def no_tools_instructions(self) -> str | None:
        """What replaces the instructions when the provider rejected the tools (the loop's recovery 1)."""
        return "\n\n".join([toolset.no_tools_note for toolset in self.toolsets] + self.notes) or None


def register_plan_note(plugin_id: str, note: str) -> None:
    """A plugin's own wording of the note for a connected user whose plan doesn't include it ({unlock}: the plan that
    does, e.g. "the Professional plan")."""
    _plan_notes[plugin_id] = note


async def _plan_note(db: AsyncSession, plugin: Plugin) -> str:
    unlock = unlock_phrase((await entitlements.unlock(db)).plugins.get(plugin.id))
    return _plan_notes.get(plugin.id, _PLAN_NOTE).format(name=plugin.name, unlock=unlock)


async def plan_chat(db: AsyncSession, user: User, chat_model: LLMModel, ent: Entitlements) -> PluginChatPlan:
    """The tool sets and notes this turn gets. A plugin outside the user's plan offers no tools; a connected user gets
    a note saying which plan includes it. A plugin that fails is logged and contributes nothing."""
    plugins = all_plugins()
    if not plugins:
        return PluginChatPlan()
    toolsets: list[ChatToolSet] = []
    notes: list[str] = []
    tools_supported: bool | None = None
    for plugin in plugins:
        try:
            access = await access_for(db, user, plugin, ent)
            if not access.enabled:
                continue
            if not access.entitled:
                if access.connection is not None:
                    notes.append(await _plan_note(db, plugin))
                continue
            if tools_supported is None:
                tools_supported = await llm.supports_tools(chat_model)
            plan = await plugin.plan_chat(db, access, tools_supported=tools_supported)
        except Exception:
            logger.exception("The %s plugin failed to plan a chat turn for user %s", plugin.id, user.id)
            continue
        if plan.toolset is not None:
            toolsets.append(plan.toolset)
        elif plan.note:
            notes.append(plan.note)
    return PluginChatPlan(toolsets, notes)


async def hydrate_cards(db: AsyncSession, user: User, items: Sequence[MessagePluginItem]) -> dict[int, PluginCard]:
    """item id -> its card with live state (one hydration per plugin). Items of an unregistered plugin or of a plugin
    whose hydration raises (logged), and cards a plugin left out, have no entry."""
    by_plugin: defaultdict[str, list[MessagePluginItem]] = defaultdict(list)
    for item in items:
        by_plugin[item.plugin_id].append(item)
    hydrated: dict[int, PluginCard] = {}
    for plugin_id, own in by_plugin.items():
        plugin = get_plugin(plugin_id)
        if plugin is None:
            continue
        try:
            cards = await plugin.hydrate_cards(db, user, own)
        except Exception:
            logger.exception("The %s plugin failed to load the cards of a conversation", plugin_id)
            continue
        for card in cards:  # matched by card_id "p<item id>": a plugin may leave out a card it can't load
            if card.card_id.startswith("p") and card.card_id[1:].isdigit():
                hydrated[int(card.card_id[1:])] = card
    return hydrated


async def history_items(db: AsyncSession, history: Sequence[Message]) -> dict[int, list[MessagePluginItem]]:
    """message id -> the cards of that answer, in order (one query)."""
    ids = [message.id for message in history if message.role == "assistant"]
    if not ids:
        return {}
    items: defaultdict[int, list[MessagePluginItem]] = defaultdict(list)
    rows = await db.scalars(
        select(MessagePluginItem).where(MessagePluginItem.message_id.in_(ids)).order_by(MessagePluginItem.id)
    )
    for item in rows:
        items[item.message_id].append(item)
    return dict(items)


def _email_plugins(message: Message) -> set[str]:
    return {
        source.get("plugin_id", "")
        for source in message.sources or []
        if isinstance(source, dict) and source.get("kind") == "email"
    }


async def history_notes(
    db: AsyncSession,
    user: User,
    history: Sequence[Message],
    items: Mapping[int, Sequence[MessagePluginItem]] | None = None,
) -> dict[int, str]:
    """message id -> the plugins' App notes about that answer (cards and cited emails), for the model's history."""
    if not all_plugins():
        return {}
    if items is None:
        items = await history_items(db, history)
    assistant = [message for message in history if message.role == "assistant"]
    involved = {item.plugin_id for found in items.values() for item in found}
    involved |= {plugin_id for message in assistant for plugin_id in _email_plugins(message)}
    notes: defaultdict[int, list[str]] = defaultdict(list)
    for plugin in all_plugins():
        if plugin.id not in involved:
            continue
        own = {
            message_id: mine
            for message_id, found in items.items()
            if (mine := [item for item in found if item.plugin_id == plugin.id])
        }
        try:
            found_notes = await plugin.history_notes(db, user, assistant, own)
        except Exception:
            logger.exception("The %s plugin failed to write history notes", plugin.id)
            continue
        for message_id, note in found_notes.items():
            if note:
                notes[message_id].append(note)
    return {message_id: "\n\n".join(found) for message_id, found in notes.items()}


async def tainted_messages(
    db: AsyncSession, history: Sequence[Message], items: Mapping[int, Sequence[MessagePluginItem]] | None = None
) -> set[int]:
    """The assistant messages of `history` that saw untrusted text: they have plugin cards, cite an email, or cite a
    passage of a document imported through a plugin (an email or an email's attachment)."""
    assistant = [message for message in history if message.role == "assistant"]
    if not assistant:
        return set()
    if items is None:
        items = await history_items(db, history)
    tainted = {message.id for message in assistant if items.get(message.id) or _email_plugins(message)}
    cited: defaultdict[int, set[int]] = defaultdict(set)  # document id -> messages citing it
    for message in assistant:
        if message.id in tainted:
            continue
        for source in message.sources or []:
            if isinstance(source, dict) and not source.get("kind") and isinstance(source.get("document_id"), int):
                cited[source["document_id"]].add(message.id)
    if cited:
        imported = await db.scalars(select(ImportedDocument.document_id).where(ImportedDocument.document_id.in_(cited)))
        for document_id in imported:
            tainted |= cited[document_id]
    return tainted


async def conversation_tainted(db: AsyncSession, conversation_id: int) -> bool:
    """Whether any answer of the whole conversation saw untrusted text, also one that has left the history window: the
    answers since may repeat an email's request in the assistant's own words, so the conversation's assistant prose is
    never shown to the request check again (P11)."""
    with_cards = await db.scalar(
        select(MessagePluginItem.id)
        .join(Message, Message.id == MessagePluginItem.message_id)
        .where(Message.conversation_id == conversation_id)
        .limit(1)
    )
    if with_cards is not None:
        return True
    rows = await db.execute(
        select(Message.sources).where(Message.conversation_id == conversation_id, Message.role == "assistant")
    )
    cited: set[int] = set()
    for (sources,) in rows.tuples():
        for source in sources or []:
            if not isinstance(source, dict):
                continue
            if source.get("kind") == "email":
                return True
            if not source.get("kind") and isinstance(source.get("document_id"), int):
                cited.add(source["document_id"])
    if not cited:
        return False
    imported = await db.scalar(
        select(ImportedDocument.document_id).where(ImportedDocument.document_id.in_(cited)).limit(1)
    )
    return imported is not None
