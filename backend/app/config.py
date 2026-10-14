from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

# Largest Postgres BIGINT (app.schemas_admin.MAX_BIGINT, which imports this module).
_MAX_BIGINT = 9_223_372_036_854_775_807


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    app_name: str = "RAG App"

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

    # --- Defaults for new users (0 = unlimited where noted in SPEC) ---
    default_monthly_token_limit: int = 1_000_000
    default_rate_limit_per_min: int = 20
    default_max_upload_mb: int = 25
    default_max_documents: int = 100
    default_max_storage_mb: int = 500
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
    vision_monthly_token_limit: int = Field(default=2_000_000, ge=0, le=_MAX_BIGINT)  # per user; 0 = unlimited
    vision_min_text_chars: int = Field(default=40, ge=0, le=10_000)  # fewer non-whitespace characters: no text
    vision_min_image_ratio: float = Field(default=0.1, gt=0, le=1)  # share of the page images must cover
    vision_max_image_px: int = Field(default=1568, ge=256, le=2576)  # long edge of a rendered page
    vision_max_output_tokens: int = Field(default=8192, ge=256, le=128_000)  # per page, when the model has none

    @field_validator("admin_username")
    @classmethod
    def _lower_admin(cls, v: str) -> str:
        return v.strip().lower()

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


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
