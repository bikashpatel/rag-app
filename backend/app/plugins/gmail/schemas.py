"""Request and response shapes of the Gmail plugin (SPEC.md section 6, "Plugins"; docs/briefs/plugins-gmail.md).

Contract only: no imports from the app beyond app.schemas, so app.schemas_plugins can import this module cheaply.
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.schemas import MAX_INT, DocumentItem

GmailId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
PartId = Annotated[str, StringConstraints(pattern=r"^[0-9.]{0,32}$")]  # "": a message that is one attachment
DocumentId = Annotated[int, Field(ge=1, le=MAX_INT)]


# ---------------- Browsing and importing ----------------


class GmailThreadSearch(BaseModel):
    """The Gmail browser's search. A request body, never a URL query: search terms (often addresses and subjects) must
    not reach the nginx or uvicorn access logs (brief P20)."""

    q: str | None = Field(None, max_length=500)  # Gmail search syntax; empty or absent: "in:inbox"
    page_token: str | None = Field(None, pattern=r"^[A-Za-z0-9_-]{1,200}$")
    max_results: int = Field(20, ge=1, le=25)


class GmailThreadSummary(BaseModel):
    thread_id: str
    subject: str
    sender: str  # of the newest message: "Name <address>" or "address"
    date: datetime | None  # of the newest message
    snippet: str
    message_count: int
    unread: bool
    gmail_url: str


class GmailThreadPage(BaseModel):
    threads: list[GmailThreadSummary]
    next_page_token: str | None
    result_size_estimate: int


class GmailAttachmentInfo(BaseModel):
    part_id: str  # stable within the message (Gmail's attachmentId isn't)
    filename: str
    mime_type: str
    size_bytes: int
    importable: bool  # a supported extension and not empty
    reason: str | None  # why not importable


class GmailMessageView(BaseModel):
    message_id: str
    thread_id: str
    sender: str
    to: list[str]
    cc: list[str]
    date: datetime | None
    subject: str
    snippet: str
    body_text: str  # plain text (HTML converted, hidden content dropped), at most 20,000 characters
    body_truncated: bool
    attachments: list[GmailAttachmentInfo]
    unread: bool


class GmailThreadDetail(BaseModel):
    thread_id: str
    subject: str
    gmail_url: str
    messages: list[GmailMessageView]  # oldest first; at most the newest 50
    messages_truncated: bool


class GmailAttachmentRef(BaseModel):
    message_id: GmailId
    part_id: PartId


class GmailImportThread(BaseModel):
    thread_id: GmailId
    include_thread: bool  # import the conversation itself as a text document
    attachments: list[GmailAttachmentRef] = Field(default_factory=list, max_length=100)


class GmailImportRequest(BaseModel):
    """Threads plus attachments count toward GMAIL_IMPORT_MAX_ITEMS (checked by the endpoint)."""

    threads: list[GmailImportThread] = Field(min_length=1, max_length=100)


ImportStatus = Literal["imported", "already_existed", "skipped", "failed"]


class GmailImportItem(BaseModel):
    kind: Literal["thread", "attachment"]
    thread_id: str
    message_id: str | None  # attachments only
    part_id: str | None  # attachments only
    label: str  # the thread's subject or the attachment's file name
    status: ImportStatus
    reason: str | None  # skipped or failed: why
    document_id: int | None  # the document created or linked
    document: DocumentItem | None  # its current state; None when not imported, deleted since or not the caller's


class GmailImportResult(BaseModel):
    items: list[GmailImportItem]  # in request order


# ---------------- Pending sends ----------------

PendingSendStatus = Literal["pending", "sending", "sent", "failed", "cancelled", "expired", "replaced"]


class EmailAddress(BaseModel):
    """One address of an email, display name kept apart so the card can't be misread (brief 9.2)."""

    address: str  # ASCII, what is sent; an internationalized domain in punycode ("xn--…")
    name: str | None  # display name (untrusted); None when absent or dropped because it contained "@"
    unicode_domain: str | None  # the domain's Unicode form when it is internationalized: show both, with a warning


class EmailRecipient(EmailAddress):
    # The user never typed this exact address in this conversation and it isn't a participant of the email replied
    # to (Bcc: only typed addresses count). Flag it as "not mentioned by you".
    unmentioned: bool


class EmailReplyInfo(BaseModel):
    """The email a draft or proposed email replies to (it joins that email's conversation)."""

    message_id: str
    subject: str  # untrusted
    sender: EmailAddress
    date: datetime | None


class EmailAttachment(BaseModel):
    document_id: int | None  # one of the user's documents; None once deleted
    filename: str
    size_bytes: int
    # Not named in the user's messages of this conversation, not created in it and not picked by the user in Edit
    unrequested: bool = False


class PendingSend(BaseModel):
    """An email the assistant proposed; only POST /plugins/gmail/sends/{id}/send sends it, once."""

    id: int
    status: PendingSendStatus  # "sending" for more than 5 minutes reads as "failed" ("may have been sent")
    account_email: str  # the From address
    to: list[EmailRecipient]
    cc: list[EmailRecipient]
    bcc: list[EmailRecipient]  # hidden from the other recipients: always shown highlighted
    subject: str
    body: str  # the whole plain-text body, exactly as it would be sent
    attachments: list[EmailAttachment]
    reply_to: EmailReplyInfo | None  # the email this replies to; None for a new email
    content_hash: str  # sent back with Send
    created_at: datetime
    expires_at: datetime
    sent_at: datetime | None
    error: str | None
    gmail_url: str | None  # the sent conversation, once sent
    replaced_by: int | None  # the newer proposal that replaced this one


class PendingSendConfirm(BaseModel):
    content_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class PendingSendRevision(BaseModel):
    """Edit: creates a new proposal (the old one becomes "replaced"). A reply keeps its subject; this one is ignored."""

    to: list[Annotated[str, StringConstraints(max_length=400)]] = Field(min_length=1, max_length=20)
    cc: list[Annotated[str, StringConstraints(max_length=400)]] = Field(default_factory=list, max_length=20)
    bcc: list[Annotated[str, StringConstraints(max_length=400)]] = Field(default_factory=list, max_length=20)
    subject: Annotated[str, StringConstraints(max_length=250)] = ""
    body: Annotated[str, StringConstraints(min_length=1, max_length=50_000)]
    attachment_document_ids: list[DocumentId] = Field(default_factory=list, max_length=5)


# ---------------- Chat cards (SSE `plugin` event, ChatMessage.plugin_cards) ----------------


class GmailActivityCard(BaseModel):
    card_id: str  # "g1", "g2", … while streaming; "p<item id>" when loaded with the conversation
    plugin_id: Literal["gmail"] = "gmail"
    type: Literal["gmail_activity"] = "gmail_activity"
    status: Literal["running", "done", "failed"]
    action: Literal["search", "read_message", "read_thread"]
    query: str | None  # the search query (search only)
    count: int  # messages found or read
    error: str | None = None


class GmailDraftCard(BaseModel):
    card_id: str
    plugin_id: Literal["gmail"] = "gmail"
    type: Literal["gmail_draft"] = "gmail_draft"
    status: Literal["writing", "creating", "created", "failed"]
    account_email: str | None  # None while writing
    to: list[EmailRecipient]
    cc: list[EmailRecipient]
    bcc: list[EmailRecipient]
    subject: str | None
    preview: str  # the first 500 characters of the body
    attachments: list[EmailAttachment]
    reply_to: EmailReplyInfo | None
    gmail_url: str | None  # the draft in Gmail, once created
    error: str | None = None


class GmailSendCard(BaseModel):
    card_id: str
    plugin_id: Literal["gmail"] = "gmail"
    type: Literal["gmail_send"] = "gmail_send"
    status: Literal["writing", "proposed", "failed"]
    pending: PendingSend | None  # set once proposed; its live state when loaded with the conversation
    error: str | None = None


class GmailImportCard(BaseModel):
    card_id: str
    plugin_id: Literal["gmail"] = "gmail"
    type: Literal["gmail_import"] = "gmail_import"
    status: Literal["importing", "done", "failed"]
    items: list[GmailImportItem]
    error: str | None = None
