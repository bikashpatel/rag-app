import logging
import os
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

logger = logging.getLogger(__name__)

RAZORPAY_API_BASE = "https://api.razorpay.com"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    app_name: str = "RAG Assistant"

    # --- Security ---
    secret_key: str = Field(min_length=32)
    access_token_expire_minutes: int = 720
    cookie_name: str = "rag_session"
    cookie_secure: bool = False
    login_max_attempts: int = 10  # failures per username
    # Failures per client IP. Larger, since every client behind a proxy or Docker's port publishing shares one IP.
    login_max_attempts_per_ip: int = 100
    login_window_seconds: int = 900

    # --- Admin (source of truth; synced on every start) ---
    admin_username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    admin_password: str = Field(min_length=8)

    # --- Infrastructure ---
    database_url: str | None = None
    postgres_user: str = "rag"
    postgres_password: str = "rag"
    postgres_db: str = "rag"
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    redis_url: str = "redis://redis:6379/0"
    qdrant_url: str = "http://qdrant:6333"
    qdrant_api_key: str | None = None
    qdrant_collection_prefix: str = "rag_chunks"
    upload_dir: str = "/data/uploads"

    # --- Limits (per plan, docs/briefs/plans-billing.md 3.1; this one caps every plan and override) ---
    max_upload_mb_hard: int = 100

    # --- RAG / ingestion ---
    rag_top_k: int = 6
    chunk_size: int = 1800  # characters
    chunk_overlap: int = 250  # characters
    history_messages: int = 10
    embed_batch_size: int = 64
    worker_concurrency: int = 2
    max_uncompressed_mb: int = 100
    max_xml_part_mb: int = 32  # per XML part of a .docx/.pptx: they're loaded into a DOM ~25x the size
    max_extracted_chars: int = 5_000_000
    max_pdf_pages: int = 5000
    parse_timeout_seconds: int = 300
    default_max_output_tokens: int = 16000
    llm_timeout_seconds: int = 300

    # --- Vision model for PDF pages (services/vision.py) ---
    vision_max_pages: int = Field(default=50, ge=1, le=200)  # units read per document
    vision_concurrency: int = Field(default=3, ge=1, le=16)  # calls in flight per document
    vision_max_in_flight: int = Field(default=4, ge=1, le=64)  # calls in flight in the whole worker
    vision_timeout_seconds: int = Field(default=1800, ge=60, le=86_400)  # no call starts after this, per document
    vision_min_text_chars: int = Field(default=40, ge=0, le=10_000)  # fewer non-whitespace characters: no text
    vision_min_image_ratio: float = Field(default=0.1, gt=0, le=1)  # share of the page images must cover
    vision_max_image_px: int = Field(default=1568, ge=256, le=2576)  # long edge of a rendered page
    vision_max_output_tokens: int = Field(default=8192, ge=256, le=128_000)  # per page, when the model has none

    # --- Files from chat (services/document_tool.py, services/filegen) ---
    chat_file_tools: bool = True  # offer create_document to chat models that support function calling
    generated_files_per_answer: int = Field(default=3, ge=1, le=10)  # an answer makes at most this + 2 model calls
    generated_file_max_chars: int = Field(default=100_000, ge=1_000, le=1_000_000)  # content (or xlsx cell text)
    # max_tokens of a call that offers the tool, unless the model has its own; lowered to LiteLLM's known cap
    generated_file_max_output_tokens: int = Field(default=48_000, ge=1_000, le=128_000)
    generated_file_timeout_seconds: int = Field(default=60, ge=5, le=600)  # per render, waiting for a slot included
    generated_file_concurrency: int = Field(default=2, ge=1, le=16)  # renders (child processes) at once

    # --- Images from chat (services/image_tool.py) ---
    chat_image_tools: bool = True  # offer create_image when an image model is active and the chat model has tools
    generated_images_per_answer: int = Field(default=3, ge=1, le=10)
    image_max_quality: Literal["low", "medium", "high"] = "medium"  # the highest quality create_image may use
    image_output_format: Literal["png", "jpeg", "webp"] = "png"  # asked of GPT image models; stored as returned
    # One image call, waiting for an image slot and quick provider retries included
    image_timeout_seconds: int = Field(default=180, ge=30, le=600)
    image_concurrency: int = Field(default=3, ge=1, le=16)  # image calls at once in one backend process

    # --- Plugins (app/plugins; docs/briefs/plugins-gmail.md) ---
    # Origins the app is reached at (comma-separated scheme://host[:port]); OAuth redirect URIs come only from these
    public_base_urls: str = "http://localhost:8080"
    # Comma-separated Fernet keys for users' OAuth tokens; the first encrypts. Empty or invalid: OAuth plugins are off
    token_encryption_key: str = ""
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_testing_mode: bool = True  # the Google app is in "Testing": Google ends connections after 7 days
    # Google's endpoints; other values only for a fake Google in tests and smoke runs
    google_oauth_authorize_url: str = "https://accounts.google.com/o/oauth2/v2/auth"  # browser-facing
    google_oauth_token_url: str = "https://oauth2.googleapis.com/token"
    google_oauth_revoke_url: str = "https://oauth2.googleapis.com/revoke"
    gmail_api_base_url: str = "https://gmail.googleapis.com"
    gmail_tool_calls_per_answer: int = Field(default=5, ge=1, le=20)
    gmail_max_chars_per_answer: int = Field(default=40_000, ge=5_000, le=400_000)  # email text returned to the model
    gmail_daily_send_limit: int = Field(default=20, ge=1, le=500)  # per user (the admin too) per UTC day
    gmail_send_confirm_minutes: int = Field(default=30, ge=5, le=1440)  # how long a proposed email can be sent
    # All attachments of one draft or email. At most 18: base64 makes them a third larger, and Gmail refuses messages
    # over 25 MB (the built message is checked too)
    gmail_max_attachment_mb: int = Field(default=15, ge=1, le=18)
    gmail_import_max_items: int = Field(default=20, ge=1, le=100)  # threads plus attachments per import request

    # --- Account (app/services/export.py; docs/briefs/ui-shell-settings.md) ---
    export_max_mb: int = Field(default=100, ge=1, le=1000)  # uncompressed content of one data export
    # From the request to the end of the build (nothing is sent before). At most 90: the Cloudflare quick tunnel drops a
    # response that sends nothing for about 100 s
    export_timeout_seconds: int = Field(default=80, ge=10, le=90)

    # --- Payments (Razorpay) and plans (app/services/billing; docs/briefs/plans-billing.md 6.10) ---
    # Both keys set: online payments are available; the key id's prefix sets the mode (razorpay_mode). Never logged.
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""  # webhooks are accepted only when set
    razorpay_webhook_secret_previous: str = ""  # also accepted, for the day after a rotation
    # Another origin only for a fake (plain http allowed, as the integration stack's fakes); live keys need the default
    razorpay_api_base: str = Field(default=RAZORPAY_API_BASE, pattern=r"^https?://[^\s/?#]+$")
    billing_checkout_minutes: int = Field(default=20, ge=16, le=120)  # a payment link's lifetime
    billing_late_capture_days: int = Field(default=5, ge=1, le=10)  # how long an unpaid checkout is still watched
    billing_reminder_days: int = Field(default=5, ge=1, le=14)  # the "ends soon" banner and first reminder email
    billing_reconcile_seconds: int = Field(default=30, ge=2, le=300)  # the worker's billing loop
    billing_usd_inr: float = Field(default=88, ge=1, le=1000)  # rupees per dollar, for cost estimates only
    billing_email_from: str = Field(default="", pattern=r"^$|^[^@\s]+@[^@\s]+\.[^@\s]+$")  # plan reminder emails

    @field_validator("admin_username")
    @classmethod
    def _lower_admin(cls, v: str) -> str:
        return v.strip().lower()

    @model_validator(mode="after")
    def _live_keys_use_razorpay(self) -> "Settings":
        if self.razorpay_key_id.startswith("rzp_live_") and self.razorpay_api_base != RAZORPAY_API_BASE:
            raise ValueError(f"With a live RAZORPAY_KEY_ID, RAZORPAY_API_BASE must be {RAZORPAY_API_BASE}")
        return self

    @property
    def razorpay_mode(self) -> Literal["test", "live"] | None:
        """The payments mode of the keys: None when either is missing or the key id has another prefix (off)."""
        if not (self.razorpay_key_id and self.razorpay_key_secret):
            return None
        if self.razorpay_key_id.startswith("rzp_test_"):
            return "test"
        return "live" if self.razorpay_key_id.startswith("rzp_live_") else None

    @property
    def sqlalchemy_url(self) -> str | URL:
        if self.database_url:
            return self.database_url
        # A URL object, not a string: no escaping round trip, so any character works in the credentials.
        return URL.create(
            "postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


# Ignored since plans set every limit (docs/briefs/plans-billing.md 6.10): Admin → Plans, and per-user overrides.
REMOVED_SETTINGS = (
    "DEFAULT_MONTHLY_TOKEN_LIMIT",
    "DEFAULT_RATE_LIMIT_PER_MIN",
    "DEFAULT_MAX_UPLOAD_MB",
    "DEFAULT_MAX_DOCUMENTS",
    "DEFAULT_MAX_STORAGE_MB",
    "DEFAULT_MONTHLY_IMAGE_LIMIT",
    "VISION_MONTHLY_TOKEN_LIMIT",
)


def warn_removed_settings() -> list[str]:
    """One warning per removed setting still in the environment (its value is ignored); returns their names."""
    present = {name.upper() for name in os.environ}
    found = [name for name in REMOVED_SETTINGS if name in present]
    for name in found:
        logger.warning("%s is ignored: it is now set per plan in Admin → Plans.", name)
    return found


@lru_cache
def get_settings() -> Settings:
    loaded = Settings()  # type: ignore[call-arg]
    warn_removed_settings()
    return loaded


settings = get_settings()
