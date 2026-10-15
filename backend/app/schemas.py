"""Response schemas shared by several routers (see SPEC.md section 6)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

# Largest Postgres INTEGER: no id or INTEGER column can hold more, and bigger values make the driver fail (a 500).
MAX_INT = 2_147_483_647


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
    vision_tokens: int
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
    vision_pages: int  # pages read with the vision model (vision_pages rows); 0 unless status is "ready"
    generated: bool  # the assistant created this file in a chat (generated_documents row)
    created_at: datetime
    updated_at: datetime


GeneratedFileFormat = Literal["pdf", "docx", "xlsx", "md"]


class GeneratedFile(BaseModel):
    """A file the assistant created in an answer (message_files row); it is one of the user's private documents."""

    document_id: int | None  # None once the document was deleted
    filename: str
    format: GeneratedFileFormat
    size_bytes: int
    status: Literal["pending", "processing", "ready", "failed"] | None  # the document's status now; None when deleted


class Source(BaseModel):
    index: int
    document_id: int
    filename: str
    page: int | None
    chunk_index: int
    score: float
    snippet: str
