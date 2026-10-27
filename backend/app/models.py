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
    false,
    text,
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

    # Limit overrides (plans-billing 5.2): NULL = the plan's value, 0 = unlimited (max_upload_mb is at least 1). The
    # other two, monthly_image_limit and vision_monthly_token_limit, came later and sit at the end of the table.
    monthly_token_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    rate_limit_per_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_upload_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_documents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_storage_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    restrict_models: Mapped[bool] = mapped_column(Boolean, default=False)  # the allowed list narrows the plan's models

    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    monthly_image_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vision_monthly_token_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    billing_tester: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())  # pays with test keys
    # An open-ended complimentary plan (no end date; the plan's live values), unless a period applies
    comp_plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("plans.id", ondelete="SET NULL", name="users_comp_plan_id_fkey"), nullable=True
    )
    comp_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    comp_granted_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    comp_granted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

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
    # Chat events: what the answer counts against the chat allowance (services/billing/money.allowance_tokens), fixed
    # when written; 0 for the other kinds
    allowance_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))


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


# ---------------- Account (docs/briefs/ui-shell-settings.md, section 3) ----------------


class UserPreference(Base):
    """A user's own UI settings (app.schemas.Preferences fields; unknown keys a newer version wrote are kept). No row:
    every default. Read leniently, written strictly (app.services.account)."""

    __tablename__ = "user_preferences"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class LoginEvent(Base):
    """One successful sign-in. The newest account.LOGIN_HISTORY_LIMIT per user are kept. The IP is approximate and the
    User-Agent isn't stored, only what was parsed from it."""

    __tablename__ = "login_events"
    __table_args__ = (Index("ix_login_events_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)  # "203.0.113.x" or an IPv6 /48 network
    browser: Mapped[str | None] = mapped_column(String(64), nullable=True)
    os: Mapped[str | None] = mapped_column(String(32), nullable=True)
    device: Mapped[str] = mapped_column(String(16), default="unknown")  # "desktop" | "mobile" | "tablet" | "unknown"


# ---------------- Plans and billing (docs/briefs/plans-billing.md, section 6.2) ----------------
# Rows that must outlive their user (orders, payments, receipts, events) keep the username and get user_id NULL.

plan_chat_models = Table(
    "plan_chat_models",
    Base.metadata,
    Column("plan_id", ForeignKey("plans.id", ondelete="CASCADE"), primary_key=True),
    Column("model_id", ForeignKey("llm_models.id", ondelete="CASCADE"), primary_key=True),
)


class Plan(Base):
    """What a user can buy: features, the seven limits (per period, 0 = unlimited) and chat models. Periods keep a
    snapshot of it (schemas_billing.PlanSnapshot); open-ended complimentary plans follow it live."""

    __tablename__ = "plans"
    __table_args__ = (UniqueConstraint("code", name="uq_plans_code"), UniqueConstraint("rank", name="uq_plans_rank"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32))  # never changed
    name: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(String(300), default="")
    price_paise: Mapped[int] = mapped_column(BigInteger)  # a whole number of rupees: the charge for one period
    rank: Mapped[int] = mapped_column(Integer)  # higher = a higher plan
    status: Mapped[str] = mapped_column(String(16), default="active")  # "active" | "retired"
    recommended: Mapped[bool] = mapped_column(Boolean, default=False)
    features: Mapped[dict] = mapped_column(JSON)  # {files: bool, images: bool, plugins: [plugin ids]}
    monthly_token_limit: Mapped[int] = mapped_column(BigInteger)  # the chat allowance, in standard tokens
    rate_limit_per_min: Mapped[int] = mapped_column(Integer)
    max_upload_mb: Mapped[int] = mapped_column(Integer)
    max_documents: Mapped[int] = mapped_column(Integer)
    max_storage_mb: Mapped[int] = mapped_column(Integer)
    monthly_image_limit: Mapped[int] = mapped_column(Integer)
    vision_monthly_token_limit: Mapped[int] = mapped_column(BigInteger)
    restrict_models: Mapped[bool] = mapped_column(Boolean, default=False)  # False: every enabled chat model
    default_chat_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("llm_models.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

    chat_models: Mapped[list[LLMModel]] = relationship(secondary=plan_chat_models, lazy="selectin")


class PlanPeriod(Base):
    """A stretch of time with a plan, paid or complimentary. Access comes from a counting, unrevoked period containing
    now, with its snapshot (services/billing/entitlements.py). A user's periods never overlap (services/billing/
    periods.py, and on Postgres the exclusion constraint ex_plan_periods_no_overlap of migration 0003)."""

    __tablename__ = "plan_periods"
    __table_args__ = (Index("ix_plan_periods_user_ends", "user_id", "ends_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("plans.id", ondelete="SET NULL"), nullable=True)
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime())
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime())  # the first midnight IST at or after starts_at + days
    days: Mapped[int] = mapped_column(Integer)  # 30 paid, 1 in test mode, 1 to 30 for a complimentary row
    source: Mapped[str] = mapped_column(String(16))  # "payment" | "comp"
    mode: Mapped[str] = mapped_column(String(8))  # "test" | "live" | "offline" | "none" (comps)
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("billing_orders.id", ondelete="SET NULL"), nullable=True, index=True
    )
    snapshot: Mapped[dict] = mapped_column(JSON)  # schemas_billing.PlanSnapshot
    charge_basis_paise: Mapped[int] = mapped_column(BigInteger, default=0)  # what it was worth, for a credit (4.4)
    # [{at, kind: "ended_by_order" | "plan_applied" | "change" | "restored", by, order_id, snapshot_before?,
    #   basis_before?, ends_at_before?}]
    history: Mapped[list] = mapped_column(JSON, default=list)
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    granted_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class BillingOrder(Base):
    """One checkout attempt (a Razorpay Payment Link), or one bank transfer the admin recorded."""

    __tablename__ = "billing_orders"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_billing_orders_public_id"),
        UniqueConstraint("razorpay_link_id", name="uq_billing_orders_razorpay_link_id"),
        Index("ix_billing_orders_user_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(24))  # in URLs; Razorpay's reference_id is "rag-<public_id>"
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    username: Mapped[str] = mapped_column(String(64))
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("plans.id", ondelete="SET NULL"), nullable=True)
    plan_snapshot: Mapped[dict] = mapped_column(JSON)  # what was quoted
    kind: Mapped[str] = mapped_column(String(16))  # "period" | "now"
    # A `now` order: the period it replaces, as quoted (applied only while that period is unchanged)
    replaces_period_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "plan_periods.id", ondelete="SET NULL", use_alter=True, name="billing_orders_replaces_period_id_fkey"
        ),
        nullable=True,
    )
    replaces_plan_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    replaces_ends_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    credit_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    mode: Mapped[str] = mapped_column(String(8))  # "test" | "live" | "offline"
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | paid | expired | cancelled | review
    review_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    review_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    terms_accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)  # the consent line
    razorpay_link_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    razorpay_order_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    pay_url: Mapped[str | None] = mapped_column(String(200), nullable=True)
    origin: Mapped[str] = mapped_column(String(200))  # the app origin the callback returns to
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())  # the link's expire_by
    watch_until: Mapped[datetime] = mapped_column(UTCDateTime())  # expires_at + BILLING_LATE_CAPTURE_DAYS
    next_check_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True, index=True)
    check_step: Mapped[int] = mapped_column(Integer, default=0)
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    checks: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(200), nullable=True)  # our own short reason, never a secret
    last_payment_error: Mapped[str | None] = mapped_column(String(200), nullable=True)  # the last failed attempt's
    paid_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    payment_id: Mapped[int | None] = mapped_column(  # the applied payment
        ForeignKey("billing_payments.id", ondelete="SET NULL", use_alter=True, name="billing_orders_payment_id_fkey"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class BillingPayment(Base):
    """A payment fetched from Razorpay (or an offline one): ids, status, amount, method and fees, never the payer's
    details. `applied_at` is set once, when it bought its period."""

    __tablename__ = "billing_payments"
    __table_args__ = (UniqueConstraint("razorpay_payment_id", name="uq_billing_payments_razorpay_payment_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    razorpay_payment_id: Mapped[str | None] = mapped_column(String(40), nullable=True)  # NULL for offline payments
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("billing_orders.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    username: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24))  # captured | failed | refunded | partially_refunded | disputed
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    method: Mapped[str | None] = mapped_column(String(24), nullable=True)  # upi | card | netbanking | bank_transfer...
    fee_paise: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    fee_tax_paise: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    captured_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    refunded_paise: Mapped[int] = mapped_column(BigInteger, default=0)  # the sum of its processed refunds
    mode: Mapped[str] = mapped_column(String(8))  # "test" | "live" | "offline"
    offline_reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recorded_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class BillingRefund(Base):
    __tablename__ = "billing_refunds"
    __table_args__ = (
        UniqueConstraint("razorpay_refund_id", name="uq_billing_refunds_razorpay_refund_id"),
        UniqueConstraint("idempotency_key", name="uq_billing_refunds_idempotency_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("billing_payments.id", ondelete="CASCADE"), index=True)
    razorpay_refund_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(40))  # "rfd_<token>", sent as X-Refund-Idempotency
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16))  # "creating" | "pending" | "processed" | "failed"
    initiated_by: Mapped[str] = mapped_column(String(16))  # "admin" | "razorpay" (made in its Dashboard)
    admin_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    end_access: Mapped[bool] = mapped_column(Boolean, default=False)
    access_ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)  # set once
    last_error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class BillingDispute(Base):
    """A chargeback, tracked from Razorpay's webhooks; answered in Razorpay's Dashboard."""

    __tablename__ = "billing_disputes"
    __table_args__ = (UniqueConstraint("razorpay_dispute_id", name="uq_billing_disputes_razorpay_dispute_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    razorpay_dispute_id: Mapped[str] = mapped_column(String(40))
    payment_id: Mapped[int] = mapped_column(ForeignKey("billing_payments.id", ondelete="CASCADE"), index=True)
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(24))  # open | under_review | action_required | won | lost | closed
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    respond_by: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    access_ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)  # set once
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class BillingReceipt(Base):
    """One per applied payment, numbered per mode and Indian financial year; a snapshot, never changed."""

    __tablename__ = "billing_receipts"
    __table_args__ = (
        UniqueConstraint("number", name="uq_billing_receipts_number"),
        UniqueConstraint("mode", "financial_year", "seq", name="uq_billing_receipts_series"),
        UniqueConstraint("payment_id", name="uq_billing_receipts_payment_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    number: Mapped[str] = mapped_column(String(32))  # "RA/26-27/000001"
    mode: Mapped[str] = mapped_column(String(8))  # "live" (Razorpay live and offline payments) | "test"
    financial_year: Mapped[str] = mapped_column(String(7))  # "26-27"
    seq: Mapped[int] = mapped_column(Integer)
    payment_id: Mapped[int] = mapped_column(ForeignKey("billing_payments.id", ondelete="CASCADE"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    issued_at: Mapped[datetime] = mapped_column(UTCDateTime())
    seller: Mapped[dict] = mapped_column(JSON)
    buyer: Mapped[dict] = mapped_column(JSON)
    lines: Mapped[list] = mapped_column(JSON)  # [{description, amount_paise}]; a credit line is negative
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    method: Mapped[str | None] = mapped_column(String(24), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class BillingEvent(Base):
    """The audit trail of every change that grants or removes value (services/billing/audit.py), the webhook inbox and
    its dedupe. `detail` holds ids, statuses, amounts, the method and values before and after: never secrets, request
    bodies or the payer's details."""

    __tablename__ = "billing_events"
    __table_args__ = (
        UniqueConstraint("razorpay_event_id", name="uq_billing_events_razorpay_event_id"),
        UniqueConstraint("body_sha256", name="uq_billing_events_body_sha256"),
        Index("ix_billing_events_type_created", "type", "created_at"),
        Index("ix_billing_events_processed_source", "processed", "source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    source: Mapped[str] = mapped_column(String(16))  # webhook | callback | reconcile | user | admin | system
    type: Mapped[str] = mapped_column(String(48))  # e.g. "payment.captured", "period.granted", "plan.updated"
    razorpay_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    body_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("billing_orders.id", ondelete="SET NULL"), nullable=True, index=True
    )
    payment_id: Mapped[int | None] = mapped_column(
        ForeignKey("billing_payments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    actor: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    processed: Mapped[bool] = mapped_column(Boolean, default=True)  # webhook events: False until the worker took it
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(200), nullable=True)


class UserBillingProfile(Base):
    """Optional, the user's own: the name on receipts and the address for plan reminders."""

    __tablename__ = "user_billing_profiles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
