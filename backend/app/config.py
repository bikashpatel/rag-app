from functools import lru_cache
from urllib.parse import quote_plus

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    app_name: str = "RAG App"

    # --- Security ---
    secret_key: str = Field(min_length=32)
    access_token_expire_minutes: int = 720
    cookie_name: str = "rag_session"
    cookie_secure: bool = False
    login_max_attempts: int = 10
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
    max_uncompressed_mb: int = 300
    max_extracted_chars: int = 5_000_000
    default_max_output_tokens: int = 16000
    llm_timeout_seconds: int = 300

    @field_validator("admin_username")
    @classmethod
    def _lower_admin(cls, v: str) -> str:
        return v.strip().lower()

    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+asyncpg://{quote_plus(self.postgres_user)}:{quote_plus(self.postgres_password)}"
            f"@{self.postgres_host}:{self.postgres_port}/{quote_plus(self.postgres_db)}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
