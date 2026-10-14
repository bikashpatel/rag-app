"""Response schemas shared by several routers (see SPEC.md section 6)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class UserLimits(BaseModel):
    monthly_token_limit: int
    rate_limit_per_min: int
    max_upload_mb: int
    max_documents: int
    max_storage_mb: int


class UsageSummary(BaseModel):
    month: str
    chat_tokens: int
    embedding_tokens: int
    cost_usd: float
    monthly_token_limit: int


class Me(BaseModel):
    id: int
    username: str
    is_admin: bool
    limits: UserLimits
    usage: UsageSummary


class DocumentItem(BaseModel):
    id: int
    filename: str
    scope: Literal["private", "shared"]
    status: Literal["pending", "processing", "ready", "failed"]
    error: str | None
    size_bytes: int
    content_type: str
    num_chunks: int
    num_pages: int | None
    owner_id: int
    owner_username: str
    can_delete: bool
    is_stale: bool
    created_at: datetime
    updated_at: datetime


class Source(BaseModel):
    index: int
    document_id: int
    filename: str
    page: int | None
    chunk_index: int
    score: float
    snippet: str
