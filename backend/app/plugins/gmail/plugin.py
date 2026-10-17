"""The Gmail plugin (docs/briefs/plugins-gmail.md): what it declares to the framework (capabilities and their Google
scopes, OAuth endpoints, identity), its chat plan, and its hooks (cards, history notes, retention)."""

import json
import logging
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import APIRouter
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, settings
from app.models import Message, MessagePluginItem, User, utcnow
from app.plugins.base import (
    AccountIdentity,
    Capability,
    ChatPlan,
    ConnectionInfo,
    OAuthEndpoints,
    Plugin,
    PluginAccess,
    PluginError,
    TokenResponse,
    reconnect_message,
)
from app.plugins.gmail import importer, sends, tools
from app.plugins.gmail.mime import capped, clean_line, defuse
from app.plugins.gmail.schemas import GmailDraftCard, GmailImportCard, GmailSendCard
from app.schemas import EmailSource
from app.schemas_plugins import PluginCard
from app.services.filegen import xml_safe

logger = logging.getLogger(__name__)

READONLY = "https://www.googleapis.com/auth/gmail.readonly"
COMPOSE = "https://www.googleapis.com/auth/gmail.compose"
SEND = "https://www.googleapis.com/auth/gmail.send"
MODIFY = "https://www.googleapis.com/auth/gmail.modify"
FULL_ACCESS = "https://mail.google.com/"
_ISSUERS = frozenset({"https://accounts.google.com", "accounts.google.com"})
_CLOCK_SKEW = timedelta(minutes=5)
_ENDPOINT_SETTINGS = (
    "google_oauth_authorize_url",
    "google_oauth_token_url",
    "google_oauth_revoke_url",
    "gmail_api_base_url",
)
_GONE = "This email is no longer available."
_NOTE_EMAILS = 10
_NOTE_CHARS = 100
_CARDS: TypeAdapter[PluginCard] = TypeAdapter(PluginCard)


class GmailPlugin(Plugin):
    id = "gmail"
    name = "Gmail"
    description = (
        "Search and read your email in chat, import emails and attachments into your documents, and draft or send "
        "email when you ask."
    )
    capabilities = (
        Capability(
            id="read",
            name="Search and read",
            description="Find and read your email in chat, and import emails and attachments into your documents.",
            scopes=(READONLY,),
            granted_by=(READONLY, MODIFY, FULL_ACCESS),
        ),
        Capability(
            id="draft",
            name="Drafts",
            description="Write drafts in your Gmail when you ask; nothing is sent.",
            scopes=(COMPOSE,),
            granted_by=(COMPOSE, MODIFY, FULL_ACCESS),
            admin_note=(
                "Google's permission for drafts also allows sending email. The app still sends only when the user "
                "clicks Send, and only while Send is on."
            ),
        ),
        Capability(
            id="send",
            name="Send",
            description="Prepare emails that are sent only when you click Send.",
            scopes=(SEND,),
            granted_by=(SEND, COMPOSE, MODIFY, FULL_ACCESS),
        ),
    )
    supports_import = True

    def missing_settings(self) -> list[str]:
        return [
            name
            for name, value in (
                ("GOOGLE_OAUTH_CLIENT_ID", settings.google_oauth_client_id),
                ("GOOGLE_OAUTH_CLIENT_SECRET", settings.google_oauth_client_secret),
            )
            if not value.strip()
        ]

    def oauth(self) -> OAuthEndpoints:
        return OAuthEndpoints(
            authorize_url=settings.google_oauth_authorize_url,
            token_url=settings.google_oauth_token_url,
            revoke_url=settings.google_oauth_revoke_url or None,
            client_id=settings.google_oauth_client_id.strip(),
            client_secret=settings.google_oauth_client_secret.strip(),
            identity_scopes=("openid", "email"),
            authorize_params={"access_type": "offline", "prompt": "consent", "include_granted_scopes": "true"},
        )

    def test_endpoints(self) -> bool:
        return any(getattr(settings, name) != Settings.model_fields[name].default for name in _ENDPOINT_SETTINGS)

    def testing_mode(self) -> bool:
        return settings.google_oauth_testing_mode

    def scopes_for(self, capabilities: frozenset[str]) -> tuple[str, ...]:
        """read -> gmail.readonly; draft -> gmail.compose; send -> gmail.send, or nothing more with draft (Google's
        compose scope also sends)."""
        scopes = []
        if "read" in capabilities:
            scopes.append(READONLY)
        if "draft" in capabilities:
            scopes.append(COMPOSE)
        elif "send" in capabilities:
            scopes.append(SEND)
        return tuple(scopes)

    def identify(self, token: TokenResponse) -> AccountIdentity:
        """The Google account of a token, from its id_token. The token came straight from Google's token endpoint over
        TLS, so its signature isn't checked (OIDC Core 3.1.3.7); the claims are."""
        if not token.id_token:
            raise PluginError("Google didn't say which account this is.")
        try:
            claims = jwt.decode(token.id_token, options={"verify_signature": False})
        except jwt.PyJWTError as exc:
            raise PluginError("Google's account details couldn't be read.") from exc
        audience = claims.get("aud")
        audiences = audience if isinstance(audience, list) else [audience]
        if settings.google_oauth_client_id.strip() not in audiences:
            raise PluginError("Google's account details are for another app.")
        if claims.get("iss") not in _ISSUERS:
            raise PluginError("Google's account details came from an unexpected issuer.")
        expires = claims.get("exp")
        if not isinstance(expires, int | float) or isinstance(expires, bool):
            raise PluginError("Google's account details have no expiry.")
        if datetime.fromtimestamp(expires, tz=timezone.utc) < utcnow() - _CLOCK_SKEW:
            raise PluginError("Google's account details have expired.")
        subject, email = claims.get("sub"), claims.get("email")
        if not isinstance(subject, str) or not subject or not isinstance(email, str) or "@" not in email:
            raise PluginError("Google didn't share the account's email address.")
        if claims.get("email_verified") not in (True, "true"):
            raise PluginError("The Google account's email address isn't verified.")
        return AccountIdentity(subject=subject, email=email)

    async def plan_chat(self, db: AsyncSession, access: PluginAccess, *, tools_supported: bool) -> ChatPlan:
        connection = access.connection
        if connection is None:
            return ChatPlan(None, tools.NOTE_CONNECT)
        if connection.status != "connected":
            reason = reconnect_message(connection.status_reason or "revoked", self.name)
            return ChatPlan(None, tools.note_reconnect(reason))
        if not connection.chat_enabled:
            return ChatPlan(None, None)
        if not tools_supported:
            return ChatPlan(None, tools.NOTE_NO_TOOLS)
        if not access.usable:
            return ChatPlan(None, tools.note_reconnect("no access granted"))
        return ChatPlan(tools.toolset(self, access), None)

    def router(self) -> APIRouter:
        from app.plugins.gmail.router import router  # it imports this module

        return router

    async def on_disconnect(self, db: AsyncSession, connection: ConnectionInfo) -> None:
        await sends.cancel_for_account(db, connection)

    async def on_conversation_deleted(self, db: AsyncSession, user: User, conversation_id: int) -> None:
        await sends.scrub_conversation(db, user.id, conversation_id)

    async def on_startup(self) -> None:
        await sends.scrub_orphans()

    async def hydrate_cards(self, db: AsyncSession, user: User, items: Sequence[MessagePluginItem]) -> list[PluginCard]:
        """Send cards get the latest email of their chain of edits (live status), import cards their documents' current
        state; activity and draft cards are snapshots. A card whose saved data no longer validates is left out."""
        cards: list[PluginCard] = []
        for item in items:
            try:
                cards.append(_CARDS.validate_python({**item.data, "card_id": f"p{item.id}"}))
            except ValidationError:
                logger.warning("Left out Gmail card %s: its saved data isn't a valid card", item.id)
        send_ids = {card.pending.id for card in cards if isinstance(card, GmailSendCard) and card.pending is not None}
        latest = await sends.latest(db, user.id, send_ids)
        existing = await sends.existing_documents(db, user.id, latest.values())
        document_ids = [
            entry.document_id
            for card in cards
            if isinstance(card, GmailImportCard)
            for entry in card.items
            if entry.document_id is not None
        ]
        documents = await importer.document_items(db, user, document_ids)
        now = utcnow()
        hydrated: list[PluginCard] = []
        for card in cards:
            if isinstance(card, GmailSendCard) and card.pending is not None:
                row = latest.get(card.pending.id)
                if row is None:
                    card = card.model_copy(update={"status": "failed", "pending": None, "error": _GONE})
                else:
                    card = card.model_copy(update={"pending": sends.view(row, now, existing)})
            elif isinstance(card, GmailImportCard):
                entries = [
                    entry.model_copy(update={"document": documents.get(entry.document_id or 0)})
                    for entry in card.items
                ]
                card = card.model_copy(update={"items": entries})
            hydrated.append(card)
        return hydrated

    async def history_notes(
        self,
        db: AsyncSession,
        user: User,
        messages: Sequence[Message],
        items: Mapping[int, Sequence[MessagePluginItem]],
    ) -> dict[int, str]:
        """An App note per answer that used Gmail: the emails it cited (ids, and subjects and senders as untrusted
        text), the drafts it created and the emails it proposed, with what became of them."""
        cards: dict[int, list[PluginCard]] = {}
        for message_id, found in items.items():
            for item in found:
                try:
                    cards.setdefault(message_id, []).append(_CARDS.validate_python({**item.data, "card_id": "n"}))
                except ValidationError:
                    continue
        send_ids = {
            card.pending.id
            for found in cards.values()
            for card in found
            if isinstance(card, GmailSendCard) and card.pending is not None
        }
        latest = await sends.latest(db, user.id, send_ids)
        now = utcnow()
        notes: dict[int, str] = {}
        for message in messages:
            if message.role != "assistant":
                continue
            sentences = []
            emails = [
                source
                for source in message.sources or []
                if isinstance(source, dict) and source.get("kind") == "email" and source.get("plugin_id") == self.id
            ][:_NOTE_EMAILS]
            if emails:
                listed = "; ".join(_email_note(source) for source in emails)
                sentences.append(f"this answer used these emails, whose details are untrusted text: {listed}")
            for card in cards.get(message.id, []):
                if (sentence := _card_note(card, latest, now)) is not None:
                    sentences.append(sentence)
            if sentences:
                notes[message.id] = f"(App note: {'. '.join(sentences)}.)"
        return notes

    def describe_source(self, source: EmailSource) -> str:
        """`Email: <subject> (<sender>, <YYYY-MM-DD>)`, at most 200 characters."""
        date = f"{source.date:%Y-%m-%d}" if source.date is not None else ""
        details = ", ".join(part for part in (source.sender, date) if part)
        text = f"Email: {source.subject or '(no subject)'}" + (f" ({details})" if details else "")
        return capped(xml_safe(clean_line(text)), 200)[0]


def _quoted(text: str) -> str:
    """Untrusted text as a JSON string: a quote inside can't end the quotation and continue as the app's words."""
    return json.dumps(defuse(capped(clean_line(text), _NOTE_CHARS)[0]), ensure_ascii=False)


def _email_note(source: Mapping[str, Any]) -> str:
    try:
        email = EmailSource.model_validate(source)
    except ValidationError:
        return "an email"
    sender = defuse(capped(clean_line(email.sender), _NOTE_CHARS)[0]) or "an unknown sender"
    date = f", {email.date:%Y-%m-%d}" if email.date is not None else ""
    return (
        f"[{email.index}] {_quoted(email.subject)} from {sender}{date} "
        f"(message_id {email.message_id}, thread_id {email.thread_id})"
    )


_OUTCOMES = {
    "pending": "which the user hasn't sent yet",
    "sending": "which is being sent",
    "sent": "which the user sent",
    "cancelled": "which the user cancelled",
    "expired": "which expired unsent",
    "failed": "which wasn't sent (sending failed)",
    "replaced": "which the user edited",
}


def _card_note(card: PluginCard, latest: Mapping[int, Any], now: datetime) -> str | None:
    if isinstance(card, GmailDraftCard) and card.status == "created":
        recipients = ", ".join(item.address for item in card.to) or "no one"
        return f"It created a Gmail draft to {recipients}, {_quoted(card.subject or '')}"
    if isinstance(card, GmailSendCard) and card.pending is not None:
        row = latest.get(card.pending.id)
        if row is None:
            return None
        current, error = sends.effective_status(row, now)
        outcome = "which may have been sent" if error == sends.UNCERTAIN else _OUTCOMES.get(current, "")
        recipients = ", ".join(str(item.get("address")) for item in row.to_addrs or []) or "no one"
        return f"It proposed an email to {recipients}, {_quoted(row.subject)}, {outcome}"
    if isinstance(card, GmailImportCard):
        stored = sum(entry.status in ("imported", "already_existed") for entry in card.items)
        if stored:
            return f"It imported {stored} item{'s' if stored != 1 else ''} from Gmail into the user's documents"
    return None
