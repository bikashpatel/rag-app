"""Response schemas shared by several routers (see SPEC.md section 6)."""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Discriminator, Field, Tag

# Largest Postgres INTEGER: no id or INTEGER column can hold more, and bigger values make the driver fail (a 500).
MAX_INT = 2_147_483_647

# The user's own UI settings (docs/briefs/ui-shell-settings.md, section 7). Adding a value is a contract change:
# SPEC.md, frontend/src/api/types.ts, frontend/public/theme-boot.js and src/theme/appearance.ts together.
ThemePreference = Literal["system", "light", "dark"]
AccentColor = Literal["clay", "ocean", "sage", "plum", "slate"]
ChatFontSize = Literal["small", "default", "large"]


class UserLimits(BaseModel):
    monthly_token_limit: int
    rate_limit_per_min: int
    max_upload_mb: int
    max_documents: int
    max_storage_mb: int
    monthly_image_limit: int = 0  # images per UTC month; 0 = unlimited (the admin's is 0)


class UsageSummary(BaseModel):
    month: str
    chat_tokens: int
    embedding_tokens: int
    vision_tokens: int
    images: int = 0  # images generated this month (image usage events)
    cost_usd: float
    monthly_token_limit: int


class Preferences(BaseModel):
    """The caller's settings (user_preferences row, read leniently: a missing or invalid value is its default)."""

    theme: ThemePreference = "system"  # "system" follows the browser's prefers-color-scheme
    accent: AccentColor = "clay"
    chat_font_size: ChatFontSize = "default"
    enter_to_send: bool = True  # False: Ctrl/Cmd+Enter sends and Enter adds a new line
    # The chat model new chats start with; None when unset or no longer one the user may use (the server's default)
    default_model_id: int | None = None


class Me(BaseModel):
    id: int
    username: str
    is_admin: bool
    limits: UserLimits
    usage: UsageSummary
    preferences: Preferences = Field(default_factory=Preferences)


ImageQuality = Literal["low", "medium", "high"]


class GeneratedImage(BaseModel):
    """Display details of an image the assistant generated in a chat (generated_images row). No prompt: that is
    private content and comes from GET /documents/{id}/image/details."""

    width: int
    height: int
    alt_text: str
    quality: ImageQuality  # as requested


class GeneratedImageDetails(BaseModel):
    """GET /documents/{id}/image/details: what the image viewer shows besides the image."""

    prompt: str
    revised_prompt: str | None
    model_name: str  # the image model's litellm_model when it was generated
    created_at: datetime


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
    image: GeneratedImage | None = None  # a generated image; always None in GET /admin/documents
    imported_from: str | None = None  # the plugin's name ("Gmail") when imported through a plugin (imported_documents)
    created_at: datetime
    updated_at: datetime


GeneratedFileFormat = Literal["pdf", "docx", "xlsx", "md", "png", "jpeg", "webp"]


class GeneratedFile(BaseModel):
    """A file the assistant created in an answer (message_files row); it is one of the user's private documents."""

    document_id: int | None  # None once the document was deleted
    filename: str
    format: GeneratedFileFormat
    size_bytes: int
    status: Literal["pending", "processing", "ready", "failed"] | None  # the document's status now; None when deleted
    image: GeneratedImage | None = None  # set for a generated image whose document still exists


class Source(BaseModel):
    """A retrieved document passage. It has no `kind` (stored messages predate it); see AnswerSource."""

    index: int
    document_id: int
    filename: str
    page: int | None
    chunk_index: int
    score: float
    snippet: str


class EmailSource(BaseModel):
    """An email a plugin tool returned in this answer, numbered after the document passages
    (docs/briefs/plugins-gmail.md, P14). Subject, sender and snippet are untrusted text from the mailbox."""

    kind: Literal["email"] = "email"
    index: int  # 1-based citation number, in the same sequence as the document passages
    plugin_id: str  # "gmail"
    message_id: str
    thread_id: str
    subject: str
    sender: str  # "Name <address>" or "address"
    date: datetime | None
    snippet: str  # at most 300 characters
    url: str  # the conversation in the provider's web app


def _source_kind(value: Any) -> str:
    kind = value.get("kind") if isinstance(value, dict) else getattr(value, "kind", None)
    return kind or "document"


# A source of an answer: a document passage (no `kind`) or an email (`kind: "email"`).
AnswerSource = Annotated[
    Annotated[Source, Tag("document")] | Annotated[EmailSource, Tag("email")], Discriminator(_source_kind)
]
