from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC timestamps on every backend (SQLite on its own returns naive datetimes)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return value.astimezone(timezone.utc) if value is not None and value.tzinfo is not None else value

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


user_allowed_models = Table(
    "user_allowed_models",
    Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("model_id", ForeignKey("llm_models.id", ondelete="CASCADE"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0)

    # Usage controls (0 = unlimited, except max_upload_mb which is always >= 1)
    monthly_token_limit: Mapped[int] = mapped_column(BigInteger, default=0)
    rate_limit_per_min: Mapped[int] = mapped_column(Integer, default=0)
    max_upload_mb: Mapped[int] = mapped_column(Integer, default=25)
    max_documents: Mapped[int] = mapped_column(Integer, default=0)
    max_storage_mb: Mapped[int] = mapped_column(Integer, default=0)
    restrict_models: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    allowed_models: Mapped[list["LLMModel"]] = relationship(secondary=user_allowed_models, lazy="selectin")


class LLMModel(Base):
    __tablename__ = "llm_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(100))
    litellm_model: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16), index=True)  # "chat" | "embedding" | "vision" | "image"
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    api_base: Mapped[str | None] = mapped_column(String(500), nullable=True)
    api_key_env: Mapped[str | None] = mapped_column(String(100), nullable=True)
    max_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    input_cost_per_mtok: Mapped[float | None] = mapped_column(Float, nullable=True)
    output_cost_per_mtok: Mapped[float | None] = mapped_column(Float, nullable=True)
    embedding_dim: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    scope: Mapped[str] = mapped_column(String(16), index=True)  # "private" | "shared"
    filename: Mapped[str] = mapped_column(String(255))
    stored_name: Mapped[str] = mapped_column(String(80), unique=True)
    content_type: Mapped[str] = mapped_column(String(150), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    num_chunks: Mapped[int] = mapped_column(Integer, default=0)
    num_pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("llm_models.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

    owner: Mapped[User] = relationship(lazy="selectin")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="New chat")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Message.id",
        lazy="noload",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text, default="")
    sources: Mapped[list | None] = mapped_column(JSON, nullable=True)
    model_id: Mapped[int | None] = mapped_column(ForeignKey("llm_models.id", ondelete="SET NULL"), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages", lazy="noload")


class UsageEvent(Base):
    __tablename__ = "usage_events"
    __table_args__ = (Index("ix_usage_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    model_id: Mapped[int | None] = mapped_column(ForeignKey("llm_models.id", ondelete="SET NULL"), nullable=True)
    model_name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16))  # "chat" | "embedding" | "vision" | "image"
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


class VisionPage(Base):
    """The vision model's cleaned output for one page of a document, reused while the fingerprint matches.

    No ORM relationship from Document: the database's ON DELETE CASCADE removes the rows.
    """

    __tablename__ = "vision_pages"

    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True)
    page: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), primary_key=True)  # "transcribe" | "describe"
    fingerprint: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class AppState(Base):
    """Server flags, e.g. "vision_seeded"."""

    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class MessageFile(Base):
    """A file the assistant created in an answer. The filename, format and size are kept when the document is
    deleted (document_id becomes NULL), so the card can say so."""

    __tablename__ = "message_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    filename: Mapped[str] = mapped_column(String(255))
    format: Mapped[str] = mapped_column(String(8))  # "pdf" | "docx" | "xlsx" | "md" | "png" | "jpeg" | "webp"
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class GeneratedDocument(Base):
    """Marks a document the assistant created in a chat. Lives as long as the document, also after the
    conversation that created it is deleted."""

    __tablename__ = "generated_documents"

    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class GeneratedImage(Base):
    """An image the assistant generated in a chat; its document holds the file. Lives as long as the document.
    `conversation_id` decides which images create_document may embed (SET NULL when the conversation is deleted)."""

    __tablename__ = "generated_images"

    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True)
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    model_name: Mapped[str] = mapped_column(String(200))  # the image model's litellm_model
    prompt: Mapped[str] = mapped_column(Text)
    revised_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    alt_text: Mapped[str] = mapped_column(String(300))
    format: Mapped[str] = mapped_column(String(8))  # "png" | "jpeg" | "webp"
    quality: Mapped[str] = mapped_column(String(8))  # "low" | "medium" | "high", as requested
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class UserImageLimit(Base):
    """A user's monthly image allowance (0 = unlimited). No row: DEFAULT_MONTHLY_IMAGE_LIMIT. A table of its own
    instead of a users column, so no migration is needed (docs/briefs/image-generation.md, section 3.3)."""

    __tablename__ = "user_image_limits"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    monthly_image_limit: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


# ---------------- Plugins (docs/briefs/plugins-gmail.md, section 3) ----------------


class PluginSetting(Base):
    """The admin's switches for one plugin. No row: disabled, with the plugin's default capabilities."""

    __tablename__ = "plugin_settings"

    plugin_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict)  # capability id -> enabled; missing ids: default
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class PluginConnection(Base):
    """A user's own account at a plugin's provider. Tokens are encrypted (app.plugins.crypto) and bound to this user and
    plugin; `key_id` names the key that encrypted them."""

    __tablename__ = "plugin_connections"
    __table_args__ = (UniqueConstraint("user_id", "plugin_id", name="uq_plugin_connections_user_plugin"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    plugin_id: Mapped[str] = mapped_column(String(32), index=True)
    account_subject: Mapped[str] = mapped_column(String(255))  # the provider's stable account id (OIDC sub)
    account_email: Mapped[str] = mapped_column(String(320))
    scopes: Mapped[list] = mapped_column(JSON, default=list)  # granted scopes, sorted
    status: Mapped[str] = mapped_column(String(24), default="connected")  # "connected" | "reconnect_needed"
    # "revoked" | "expired_testing" | "key_changed" | "scopes_missing"
    status_reason: Mapped[str | None] = mapped_column(String(24), nullable=True)
    refresh_token_enc: Mapped[str] = mapped_column(Text)
    access_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    key_id: Mapped[str] = mapped_column(String(16))
    chat_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    connected_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)  # the current refresh token's consent
    refreshed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class MessagePluginItem(Base):
    """A card a plugin added to an answer (the card's JSON as last sent); the plugin hydrates live state on load."""

    __tablename__ = "message_plugin_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    plugin_id: Mapped[str] = mapped_column(String(32))
    card_type: Mapped[str] = mapped_column(String(32))  # e.g. "gmail_send"
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class ImportedDocument(Base):
    """Marks a document imported through a plugin (provenance: "Imported from Gmail"). Lives as long as the document."""

    __tablename__ = "imported_documents"

    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True)
    plugin_id: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(16))  # "email" | "attachment"
    external_ref: Mapped[str] = mapped_column(String(255))  # Gmail: the thread id, or "<message id>/<part id>"
    account_email: Mapped[str] = mapped_column(String(320))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class PluginAuditEvent(Base):
    """One plugin action of a user: metadata only (no subjects, addresses, queries or text)."""

    __tablename__ = "plugin_audit_events"
    __table_args__ = (Index("ix_plugin_audit_plugin_created", "plugin_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    plugin_id: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(24))  # schemas_plugins.PluginAction
    outcome: Mapped[str] = mapped_column(String(16))  # "ok" | "error" | "refused"
    origin: Mapped[str] = mapped_column(String(8))  # "chat" | "ui" | "system"
    items: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


class GmailPendingSend(Base):
    """An email the assistant proposed (or the user edited): exactly what Send would send, once, before `expires_at`.
    Deleting its conversation cancels it if pending and scrubs the content (subject, body, recipients, attachments,
    reply details); id, status and times stay for the daily cap."""

    __tablename__ = "gmail_pending_sends"
    __table_args__ = (Index("ix_gmail_pending_sends_user_attempted", "user_id", "attempted_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    account_email: Mapped[str] = mapped_column(String(320))
    account_subject: Mapped[str] = mapped_column(String(255))  # the connection's account (OIDC sub) when proposed
    # [{name, address, unmentioned}]: normalized (brief 9.2); the hash covers name and address
    to_addrs: Mapped[list] = mapped_column(JSON)
    cc_addrs: Mapped[list] = mapped_column(JSON, default=list)
    bcc_addrs: Mapped[list] = mapped_column(JSON, default=list)
    subject: Mapped[str] = mapped_column(Text)  # Text: a reply's subject comes from the original, of any length
    body: Mapped[str] = mapped_column(Text)
    # [{document_id, filename, size_bytes, sha256, content_type, unrequested}]
    attachments: Mapped[list] = mapped_column(JSON, default=list)
    thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    in_reply_to: Mapped[str | None] = mapped_column(Text, nullable=True)
    references_header: Mapped[str | None] = mapped_column(Text, nullable=True)
    # {message_id, subject, sender: {name, address}, date}: the email replied to, as shown on the card
    reply_to: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    # "pending" | "sending" | "sent" | "failed" | "cancelled" | "expired" | "replaced"
    status: Mapped[str] = mapped_column(String(16), default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    origin: Mapped[str] = mapped_column(String(8))  # "chat" | "edit"
    replaced_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("gmail_pending_sends.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    attempted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)  # claimed for sending
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    gmail_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    gmail_thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
