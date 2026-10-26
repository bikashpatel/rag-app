// API shapes — must match SPEC.md section 6 and the backend schemas exactly.

export type UserLimits = {
  monthly_token_limit: number; // 0 = unlimited
  rate_limit_per_min: number; // 0 = unlimited
  max_upload_mb: number;
  max_documents: number; // 0 = unlimited
  max_storage_mb: number; // 0 = unlimited
  monthly_image_limit: number; // images per UTC calendar month; 0 = unlimited (the admin's is 0)
};

export type UsageSummary = {
  month: string; // "YYYY-MM"
  chat_tokens: number;
  embedding_tokens: number;
  vision_tokens: number;
  images: number; // images generated this month
  cost_usd: number; // every kind
  monthly_token_limit: number; // 0 = unlimited; counts chat_tokens only
};

// The user's own settings (docs/briefs/ui-shell-settings.md, section 7). Adding a value is a contract change: SPEC.md,
// the backend schemas, public/theme-boot.js and src/theme/appearance.ts together.
export type ThemePreference = "system" | "light" | "dark"; // "system" follows prefers-color-scheme, live
export type AccentColor = "clay" | "ocean" | "sage" | "plum" | "slate"; // "clay" is the default
export type ChatFontSize = "small" | "default" | "large";

export type Preferences = {
  theme: ThemePreference;
  accent: AccentColor;
  chat_font_size: ChatFontSize;
  enter_to_send: boolean; // false: Ctrl/Cmd+Enter sends and Enter adds a new line
  default_model_id: number | null; // the chat model new chats start with; null = the server's default (also when no longer usable)
};

/** PATCH /me/preferences: omitted = unchanged; default_model_id null = the server's default. Null elsewhere is a 422. */
export type PreferencesUpdate = Partial<Preferences>;

export type Me = {
  id: number;
  username: string;
  is_admin: boolean;
  limits: UserLimits;
  usage: UsageSummary;
  preferences: Preferences;
};

export type ChatModel = {
  id: number;
  display_name: string;
  provider: string;
  is_default: boolean;
  /** The assistant can create files with this model (CHAT_FILE_TOOLS on and the model supports function calling). */
  can_create_files: boolean;
  /** The assistant can generate images with this model (CHAT_IMAGE_TOOLS on, a usable image model active, function calling). */
  can_create_images: boolean;
  /** The model gets tools (function calling, not rejected since the backend started): plugin tools such as Gmail work. */
  supports_tools: boolean;
};

// ---------------- Account (GET /me/account, GET /me/usage) ----------------

export type DeviceType = "desktop" | "mobile" | "tablet" | "unknown";

/** One successful sign-in of the caller (the last 20 are kept). */
export type LoginEvent = {
  id: number;
  created_at: string;
  ip: string | null; // approximate, as nginx saw it: "203.0.113.x" or an IPv6 "/48" network; null when unknown
  // Not a public internet address (private, loopback, link-local, shared): through this machine, Docker, a local
  // network or a proxy. Show "Via a local network or proxy" rather than the bare address. False when ip is null.
  ip_private: boolean;
  browser: string | null; // parsed from the User-Agent (not stored): "Chrome 141", "Safari 26", "curl", …
  os: string | null; // "macOS", "Windows", "iOS", "Android", "Linux", "ChromeOS", …
  device: DeviceType;
  current: boolean; // the sign-in that started this session ("This device")
};

export type AccountInfo = {
  id: number;
  username: string;
  is_admin: boolean;
  password_managed_by_env: boolean; // the env admin: don't offer Change password (the server answers 400)
  created_at: string;
  last_login_at: string | null; // also set for sign-ins from before the history existed
  conversation_count: number;
  recent_logins: LoginEvent[]; // newest first, at most 20
};

export type UsageAmount = { used: number; limit: number }; // limit 0 = unlimited (always 0 for the admin)

export type MyUsage = {
  month: string; // "YYYY-MM" (UTC calendar month)
  period_start: string; // the 1st, 00:00 UTC
  resets_at: string; // the 1st of next month, 00:00 UTC
  chat_tokens: UsageAmount; // the monthly token budget (the only budget)
  images: UsageAmount; // the monthly image limit
  vision_tokens: UsageAmount; // VISION_MONTHLY_TOKEN_LIMIT
  embedding_tokens: number; // recorded, never limited
  requests: number; // chat answers this month
  rate_limit_per_min: number; // 0 = unlimited
  cost_usd: { total: number; chat: number; embedding: number; vision: number; image: number }; // estimated
  documents: { count: number; max_documents: number; storage_bytes: number; max_storage_mb: number; max_upload_mb: number };
  daily: {
    date: string; // "YYYY-MM-DD" (UTC); every day from the 1st through today, zeros included
    chat_tokens: number;
    requests: number;
    images: number;
    vision_tokens: number;
    embedding_tokens: number;
    cost_usd: number;
  }[];
  by_model: {
    model_name: string; // display name while the model exists, else its litellm id
    kind: "chat" | "embedding" | "vision" | "image";
    tokens: number;
    requests: number; // chat: answers; image: images; embedding and vision: ingestion runs
    cost_usd: number;
  }[]; // cost descending
};

/** DELETE /conversations: every conversation of the caller is gone (documents are kept). */
export type ConversationsDeleted = { deleted: number };

export type DocumentStatus = "pending" | "processing" | "ready" | "failed";
export type DocumentScope = "private" | "shared";

export type ImageQuality = "low" | "medium" | "high";

/** Display details of an image the assistant generated in a chat (no prompt: see GeneratedImageDetails). */
export type GeneratedImage = {
  width: number;
  height: number;
  alt_text: string;
  quality: ImageQuality; // as requested
};

/** GET /documents/{id}/image/details: what the image viewer shows besides the image (owner only). */
export type GeneratedImageDetails = {
  prompt: string;
  revised_prompt: string | null;
  model_name: string; // the image model's litellm_model when it was generated
  created_at: string;
};

export type DocumentItem = {
  id: number;
  filename: string;
  scope: DocumentScope;
  status: DocumentStatus;
  error: string | null;
  size_bytes: number;
  content_type: string;
  num_chunks: number;
  num_pages: number | null;
  owner_id: number;
  owner_username: string;
  can_delete: boolean;
  is_stale: boolean;
  vision_pages: number; // pages read with the vision model; 0 unless status is "ready"
  generated: boolean; // the assistant created this file in a chat
  image: GeneratedImage | null; // a generated image (GET /documents/{id}/image serves it); always null in GET /admin/documents
  imported_from: string | null; // the plugin's name ("Gmail") when imported through a plugin: "Imported from Gmail"
  created_at: string;
  updated_at: string;
};

export type DocumentConfig = {
  supported_extensions: string[];
  max_upload_mb: number;
  max_documents: number; // 0 = unlimited
  max_storage_mb: number; // 0 = unlimited
  document_count: number;
  storage_bytes: number;
  can_upload_shared: boolean;
};

/** null = all accessible documents; number[] = only these ids ([] = don't use documents). */
export type DocumentSelection = number[] | null;

/** A retrieved document passage. It carries no `kind` (narrow with `source.kind === "email"`). */
export type Source = {
  kind?: undefined;
  index: number; // 1-based citation number, matches [n] in the answer
  document_id: number;
  filename: string;
  page: number | null;
  chunk_index: number;
  score: number;
  snippet: string;
};

/**
 * An email a plugin tool returned in this answer, numbered after the document passages (same [n] sequence).
 * Subject, sender and snippet are untrusted text from the mailbox: render as text only.
 */
export type EmailSource = {
  kind: "email";
  index: number;
  plugin_id: string; // "gmail"
  message_id: string;
  thread_id: string;
  subject: string;
  sender: string; // "Name <address>" or "address"
  date: string | null;
  snippet: string; // at most 300 characters
  url: string; // the conversation in Gmail (open in a new tab)
};

/** A source of an answer: document passages first, then the emails it cites. */
export type AnswerSource = Source | EmailSource;

export type ConversationSummary = {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
};

export type GeneratedFileFormat = "pdf" | "docx" | "xlsx" | "md" | "png" | "jpeg" | "webp";
/** What a `file` event is about: a create_document file or a create_image image. */
export type GeneratedFileKind = "file" | "image";

/** A file the assistant created in an answer; it is one of the user's private documents. */
export type GeneratedFile = {
  document_id: number | null; // null once the document was deleted
  filename: string;
  format: GeneratedFileFormat;
  size_bytes: number;
  status: DocumentStatus | null; // the document's status now; null when deleted
  image: GeneratedImage | null; // set for a generated image whose document still exists
};

export type ChatMessage = {
  id: number;
  role: "user" | "assistant";
  content: string;
  sources: AnswerSource[]; // document passages, then the emails the answer cites
  model_name: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  error: string | null;
  files: GeneratedFile[]; // in creation order; [] for user messages
  plugin_cards: PluginCard[]; // cards plugins added to this answer, in order (card_id "p<n>"); [] for user messages
  created_at: string;
};

export type ConversationDetail = ConversationSummary & { messages: ChatMessage[] };

export type ChatStreamRequest = {
  conversation_id: number | null;
  message: string; // 1..20000 chars
  model_id: number | null; // null = default chat model
  document_ids: DocumentSelection; // at most 10000 ids
};

export type TokenUsage = {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_usd: number;
};

/**
 * Data of the `file` event: progress of one create_document call (`kind` "file", `call_id` "f1", "f2", …) or one
 * create_image call (`kind` "image", `call_id` "i1", "i2", …) of the answer.
 */
export type FileEventData =
  | {
      // The call started (chars 0, title and format null); then at most every 2 s while it is written (title and
      // format as soon as they appear); once more with the validated title (and a file's format) before the work.
      call_id: string;
      kind: GeneratedFileKind;
      status: "writing";
      title: string | null;
      format: GeneratedFileFormat | null; // always null for images
      chars: number; // characters of the file (or the image prompt) written so far (approximate)
    }
  | {
      // Images only: the arguments are valid and the image model was called (20 to 120 s); the final size is known.
      call_id: string;
      kind: "image";
      status: "generating";
      title: string;
      width: number;
      height: number;
      quality: ImageQuality;
    }
  | {
      call_id: string;
      kind: GeneratedFileKind;
      status: "created";
      file: GeneratedFile; // file.image is set for images
      document: DocumentItem;
      already_existed: boolean; // an identical private document existed and is linked instead (always false for images)
    }
  | {
      call_id: string;
      kind: GeneratedFileKind;
      status: "failed";
      title: string | null;
      format: GeneratedFileFormat | null;
      error: string;
    };

// Server-sent events of POST /api/chat/stream
export type ChatStreamEvent =
  | {
      event: "meta";
      data: {
        conversation_id: number;
        title: string;
        user_message_id: number;
        model: { id: number; display_name: string };
      };
    }
  // Always sent once after meta; sent again (whole list) when a plugin tool adds emails and, before done, when emails
  // the answer doesn't cite are dropped. Replace the list each time.
  | { event: "sources"; data: { sources: AnswerSource[] } }
  | { event: "warning"; data: { message: string } }
  | { event: "delta"; data: { content: string } }
  | { event: "file"; data: FileEventData }
  | { event: "plugin"; data: PluginCard } // the card's whole state; replace the card with the same card_id
  | { event: "done"; data: { assistant_message_id: number; usage: TokenUsage } } // usage sums every model call
  | { event: "error"; data: { message: string; assistant_message_id: number | null } };

// ---------------- Admin ----------------

export type AdminUser = {
  id: number;
  username: string;
  is_admin: boolean;
  is_active: boolean;
  monthly_token_limit: number;
  rate_limit_per_min: number;
  max_upload_mb: number;
  max_documents: number;
  max_storage_mb: number;
  monthly_image_limit: number; // effective: the user's own value, else DEFAULT_MONTHLY_IMAGE_LIMIT; 0 = unlimited
  restrict_models: boolean;
  allowed_model_ids: number[];
  created_at: string;
  last_login_at: string | null;
  usage: { chat_tokens: number; embedding_tokens: number; vision_tokens: number; images: number; cost_usd: number };
  document_count: number;
  storage_bytes: number;
};

export type AdminUserCreate = {
  username: string;
  password: string;
  is_active?: boolean;
  monthly_token_limit?: number;
  rate_limit_per_min?: number;
  max_upload_mb?: number;
  max_documents?: number;
  max_storage_mb?: number;
  monthly_image_limit?: number; // 0 = unlimited; default AdminConfig.defaults.monthly_image_limit
  restrict_models?: boolean;
  allowed_model_ids?: number[];
};

export type AdminUserUpdate = Partial<Omit<AdminUserCreate, "username">>;

/**
 * "vision": reads PDF pages for ingestion; "image": generates images in chat (create_image). The active one of each is
 * the enabled default; neither is ever offered as a chat model.
 */
export type ModelKind = "chat" | "embedding" | "vision" | "image";

export type AdminModel = {
  id: number;
  display_name: string;
  litellm_model: string;
  kind: ModelKind;
  enabled: boolean;
  is_default: boolean;
  api_base: string | null;
  api_key_env: string | null;
  max_output_tokens: number | null;
  temperature: number | null;
  input_cost_per_mtok: number | null;
  output_cost_per_mtok: number | null;
  embedding_dim: number | null;
  created_at: string;
  updated_at: string;
};

export type AdminModelCreate = {
  display_name: string;
  litellm_model: string;
  kind: ModelKind;
  enabled?: boolean;
  is_default?: boolean;
  api_base?: string | null;
  api_key_env?: string | null;
  max_output_tokens?: number | null;
  temperature?: number | null;
  input_cost_per_mtok?: number | null;
  output_cost_per_mtok?: number | null;
};

export type AdminModelUpdate = Partial<Omit<AdminModelCreate, "kind">>;

export type ModelTestResult = {
  ok: boolean;
  message: string;
  latency_ms: number;
  embedding_dim: number | null;
  image_data_url: string | null; // image models: a data: URL of a small thumbnail of the test image
};

export type AdminConfig = {
  defaults: UserLimits;
  max_upload_mb_hard: number;
  admin_username: string;
};

export type AdminStats = {
  users: number;
  documents: number;
  stale_documents: number;
  failed_documents: number;
  active_embedding_model: { id: number; display_name: string } | null;
  active_vision_model: { id: number; display_name: string } | null;
  active_image_model: { id: number; display_name: string } | null;
  queue_length: number;
};

export type UsageReport = {
  month: string;
  totals: {
    chat_tokens: number;
    embedding_tokens: number;
    vision_tokens: number;
    images: number;
    cost_usd: number;
    requests: number; // chat requests only
  };
  by_user: {
    user_id: number;
    username: string;
    chat_tokens: number;
    embedding_tokens: number;
    vision_tokens: number;
    images: number;
    cost_usd: number;
    requests: number;
  }[];
  by_model: {
    model_name: string;
    kind: ModelKind;
    prompt_tokens: number; // image models: prompt text tokens
    completion_tokens: number; // image models: image tokens
    cost_usd: number;
    requests: number; // image models: images
  }[];
  daily: {
    date: string;
    chat_tokens: number;
    embedding_tokens: number;
    vision_tokens: number;
    images: number;
    cost_usd: number;
  }[];
};

// ---------------- Plugins (docs/briefs/plugins-gmail.md) ----------------

export type PluginCapabilityInfo = {
  id: string; // Gmail: "read" | "draft" | "send"
  name: string;
  description: string;
  enabled: boolean; // by the admin
  granted: boolean; // by the caller's connection (false without one); enabled && !granted -> offer "Grant access"
};

export type PluginConnectionReason = "revoked" | "expired_testing" | "key_changed" | "scopes_missing";

/** The caller's own connection; nobody else's is ever returned. */
export type PluginConnectionStatus = {
  account_email: string;
  status: "connected" | "reconnect_needed";
  status_reason: PluginConnectionReason | null;
  status_message: string | null; // what to show when a reconnect is needed
  granted_scopes: string[];
  chat_enabled: boolean; // "Use in chat"
  connected_at: string;
  last_used_at: string | null;
  refresh_expires_at: string | null; // Google testing mode: when Google ends the connection (estimate)
};

/**
 * GET /plugins: every registered plugin (the catalog), whatever its state. Status, in this order of precedence:
 * !configured → "Not set up"; !enabled → "Turned off by the admin"; connection reconnect_needed → "Reconnect needed";
 * connection → "Connected"; else "Not connected" (offer Connect). In the first two states a caller who still has a
 * connection can Disconnect it (DELETE …/connection works then too). Tools reach chat only when enabled and connected
 * with "Use in chat" on.
 */
export type PluginInfo = {
  id: string; // also the URL segment: /api/plugins/<id>/...
  name: string;
  description: string;
  auth_type: "oauth2";
  configured: boolean; // the settings it needs are in the server's .env (the admin API lists the missing ones)
  enabled: boolean; // turned on by the admin and configured: it can be connected and used
  // The provider app is in testing mode: only accounts the admin added as test users can connect (others see Google's
  // "Access blocked" page and never come back): say so before redirecting. Connections end after 7 days.
  testing_mode: boolean;
  supports_import: boolean;
  import_max_items: number | null; // GMAIL_IMPORT_MAX_ITEMS for plugins that import
  capabilities: PluginCapabilityInfo[];
  connection: PluginConnectionStatus | null;
};

export type PluginConnectRequest = { capabilities?: string[] }; // default: every capability the admin enabled
export type PluginConnectResponse = { authorize_url: string }; // navigate the window there
export type PluginConnectionUpdate = { chat_enabled: boolean };

/** After Google, the browser lands on /?plugin=<id>&oauth=<result>. */
export type PluginOAuthResult = "connected" | "denied" | "expired" | "invalid" | "no_access" | "disabled" | "error";

// ---------------- Gmail: browsing and importing ----------------

/** POST /plugins/gmail/threads/search: the query travels in the body, never in the URL (access logs). */
export type GmailThreadSearch = {
  q?: string | null; // Gmail search syntax; empty or absent: "in:inbox"
  page_token?: string | null;
  max_results?: number; // 1-25, default 20
};

export type GmailThreadSummary = {
  thread_id: string;
  subject: string;
  sender: string; // of the newest message
  date: string | null; // of the newest message
  snippet: string;
  message_count: number;
  unread: boolean;
  gmail_url: string;
};

export type GmailThreadPage = {
  threads: GmailThreadSummary[];
  next_page_token: string | null;
  result_size_estimate: number;
};

export type GmailAttachmentInfo = {
  part_id: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  importable: boolean;
  reason: string | null; // why it can't be imported
};

export type GmailMessageView = {
  message_id: string;
  thread_id: string;
  sender: string;
  to: string[];
  cc: string[];
  date: string | null;
  subject: string;
  snippet: string;
  body_text: string; // plain text only (render as text, never as HTML); at most 20,000 characters
  body_truncated: boolean;
  attachments: GmailAttachmentInfo[];
  unread: boolean;
};

export type GmailThreadDetail = {
  thread_id: string;
  subject: string;
  gmail_url: string;
  messages: GmailMessageView[]; // oldest first; at most the newest 50
  messages_truncated: boolean;
};

export type GmailImportRequest = {
  // Threads with include_thread plus attachments: 1..PluginInfo.import_max_items items
  threads: {
    thread_id: string;
    include_thread: boolean; // the conversation itself as a text document
    attachments: { message_id: string; part_id: string }[];
  }[];
};

export type GmailImportItem = {
  kind: "thread" | "attachment";
  thread_id: string;
  message_id: string | null; // attachments only
  part_id: string | null; // attachments only
  label: string; // the thread's subject or the attachment's file name
  status: "imported" | "already_existed" | "skipped" | "failed";
  reason: string | null;
  document_id: number | null;
  document: DocumentItem | null; // current state; null when not imported or deleted since (announce it when set)
};

export type GmailImportResult = { items: GmailImportItem[] }; // in request order

// ---------------- Gmail: drafts and pending sends ----------------

/** One address. Show `address` separately from `name` (untrusted); when `unicode_domain` is set, show both forms with a
 * warning (an internationalized domain can imitate another). */
export type EmailAddress = {
  address: string; // ASCII, what is sent; an internationalized domain in punycode ("xn--…")
  name: string | null; // null when absent or dropped because it contained "@"
  unicode_domain: string | null;
};

/** unmentioned: the user never typed this exact address in this conversation and it isn't a participant of the email
 * replied to (Bcc: only typed addresses count). Flag it "not mentioned by you". */
export type EmailRecipient = EmailAddress & { unmentioned: boolean };

/** The email a draft or proposed email replies to: show "Replying to <subject> from <sender> · <date>". */
export type EmailReplyInfo = { message_id: string; subject: string; sender: EmailAddress; date: string | null };

export type EmailAttachment = {
  document_id: number | null;
  filename: string;
  size_bytes: number;
  unrequested: boolean; // not named in the user's messages, not created in this conversation, not picked in Edit: flag it
};

export type PendingSendStatus = "pending" | "sending" | "sent" | "failed" | "cancelled" | "expired" | "replaced";

/** An email the assistant proposed: exactly what Send would send (POST /plugins/gmail/sends/{id}/send), once. */
export type PendingSend = {
  id: number;
  status: PendingSendStatus; // "sending" for over 5 minutes is reported as "failed" ("may have been sent")
  account_email: string; // From
  to: EmailRecipient[];
  cc: EmailRecipient[];
  bcc: EmailRecipient[]; // hidden from the other recipients: always highlight
  subject: string;
  body: string; // the whole plain-text body
  attachments: EmailAttachment[];
  reply_to: EmailReplyInfo | null; // null for a new email
  content_hash: string; // send it back with Send
  created_at: string;
  expires_at: string;
  sent_at: string | null;
  error: string | null;
  gmail_url: string | null; // once sent
  replaced_by: number | null;
};

export type PendingSendConfirm = { content_hash: string };

/** Edit = a new proposal (POST /plugins/gmail/sends/{id}/revise); the subject of a reply can't change.
 * Addresses as "Name <address>" or "address" (format EmailRecipient back to this for the form). */
export type PendingSendRevision = {
  to: string[];
  cc: string[];
  bcc: string[];
  subject: string;
  body: string;
  attachment_document_ids: number[]; // the user's own documents, at most 5
};

// ---------------- Plugin cards on answers (SSE `plugin`, ChatMessage.plugin_cards) ----------------

export type GmailActivityCard = {
  card_id: string; // "g1", "g2", … while streaming; "p<n>" when loaded with the conversation
  plugin_id: "gmail";
  type: "gmail_activity";
  status: "running" | "done" | "failed";
  action: "search" | "read_message" | "read_thread";
  query: string | null; // search only
  count: number; // messages found or read
  error: string | null;
};

export type GmailDraftCard = {
  card_id: string;
  plugin_id: "gmail";
  type: "gmail_draft";
  status: "writing" | "creating" | "created" | "failed";
  account_email: string | null;
  to: EmailRecipient[];
  cc: EmailRecipient[];
  bcc: EmailRecipient[];
  subject: string | null;
  preview: string; // the first 500 characters of the body
  attachments: EmailAttachment[];
  reply_to: EmailReplyInfo | null;
  gmail_url: string | null; // "Open in Gmail", once created
  error: string | null;
};

export type GmailSendCard = {
  card_id: string;
  plugin_id: "gmail";
  type: "gmail_send";
  status: "writing" | "proposed" | "failed";
  pending: PendingSend | null; // set once proposed; live state when loaded with the conversation
  error: string | null;
};

export type GmailImportCard = {
  card_id: string;
  plugin_id: "gmail";
  type: "gmail_import";
  status: "importing" | "done" | "failed";
  items: GmailImportItem[];
  error: string | null;
};

/** Unknown `type`s must be ignored (a later plugin may add more). */
export type PluginCard = GmailActivityCard | GmailDraftCard | GmailSendCard | GmailImportCard;

// ---------------- Admin: plugins ----------------

export type PluginAction =
  | "connect"
  | "disconnect"
  | "reconnect_needed"
  | "search"
  | "read"
  | "import"
  | "draft"
  | "send_proposed"
  | "send"
  | "send_cancelled";

export type PluginActionCount = { action: PluginAction; ok: number; failed: number; refused: number };

export type AdminPlugin = {
  id: string;
  name: string;
  description: string;
  auth_type: "oauth2";
  supports_import: boolean;
  configured: boolean;
  missing_settings: string[]; // env vars to set in .env, e.g. ["GOOGLE_OAUTH_CLIENT_ID", "TOKEN_ENCRYPTION_KEY"]
  test_endpoints: boolean; // the OAuth or API endpoints aren't Google's (a test setup): show a warning
  enabled: boolean;
  capabilities: {
    id: string;
    name: string;
    description: string;
    scopes: string[]; // what it requests on its own
    enabled: boolean;
    note: string | null; // show under the switch (Gmail drafts: Google's draft permission also allows sending)
  }[];
  requested_scopes: string[]; // exactly what a connect asks for with the current switches
  redirect_uris: string[]; // register each in Google Cloud (one per PUBLIC_BASE_URLS entry)
  testing_mode: boolean; // GOOGLE_OAUTH_TESTING_MODE: only test users can connect; connections end after 7 days
  // old_key: connections still encrypted with a key other than the first TOKEN_ENCRYPTION_KEY: keep old keys until 0
  connections: { connected: number; reconnect_needed: number; old_key: number };
  activity: { month: string; actions: PluginActionCount[] }; // this month
};

export type AdminPluginUpdate = { enabled?: boolean; capabilities?: Record<string, boolean> };

export type PluginActivityReport = {
  month: string;
  totals: PluginActionCount[];
  by_user: { user_id: number; username: string; actions: PluginActionCount[] }[];
};

/** POST /admin/plugins/request-check/test: the request check (which gates drafts, sends and imports) with one chat
 * model and a fixed message; ok when the model answered with valid JSON asking for a draft only. No usage recorded. */
export type RequestCheckTest = { model_id: number };
export type RequestCheckTestResult = {
  ok: boolean;
  message: string;
  latency_ms: number;
  answer: Record<"file" | "image" | "draft" | "send" | "import", boolean> | null; // null without valid JSON
};

// ---------------- Health and Admin: system (SPEC.md section 10) ----------------

/** GET /health/ready (public): 200 when Postgres, Redis and Qdrant answer and the worker's heartbeat is fresh, else 503. */
export type Readiness = { status: "ready" | "unready" };

export type CheckStatus = "ok" | "warning" | "critical" | "unknown";
export type CheckId =
  | "postgres"
  | "redis"
  | "qdrant"
  | "worker"
  | "queue"
  | "backup"
  | "disk_uploads"
  | "disk_backups"
  | "proxy" // checked by the monitor service
  | "monitor";
export type AlertChannel = "webhook" | "ntfy" | "email";
export type AlertKind = "problem" | "reminder" | "recovery" | "test";

export type SystemCheck = { id: CheckId; label: string; status: CheckStatus; detail: string };

export type SystemAlert = {
  at: string;
  kind: AlertKind;
  title: string;
  lines: string[];
  delivered: AlertChannel[];
  failed: AlertChannel[]; // still being retried (for up to an hour), or given up on
};

/** GET /admin/system */
export type SystemStatus = {
  status: CheckStatus; // the worst check ("unknown" counts as a warning)
  checked_at: string;
  app_version: string | null;
  checks: SystemCheck[];
  worker: { heartbeat_age_seconds: number | null; running_jobs: number | null };
  queue: {
    queued: number | null; // ids waiting in Redis
    pending: number | null;
    processing: number | null;
    oldest_pending_age_seconds: number | null;
  };
  failed_documents_24h: number | null;
  database_size_bytes: number | null; // null on SQLite
  disks: { id: "uploads" | "backups"; label: string; free_bytes: number; total_bytes: number; checked_at: string }[];
  backup: {
    status_seen: boolean; // the backup service's status file exists
    max_age_hours: number; // 0: backup checks are off
    last_success_at: string | null;
    last_success_name: string | null;
    last_success_size_bytes: number | null;
    last_success_duration_seconds: number | null;
    age_hours: number | null;
    last_failure_at: string | null;
    last_failure_error: string | null;
    failing: boolean; // the latest attempt failed
    running_since: string | null;
    schedule: string | null; // "02:30" or "02:30,14:30" (UTC)
    next_run_at: string | null;
    scheduler_seen_at: string | null;
    backups_kept: number | null;
    total_size_bytes: number | null;
  };
  gmail_reconnect_needed: number | null;
  monitor: {
    running: boolean;
    last_run_at: string | null;
    interval_seconds: number | null;
    channels: AlertChannel[];
    test_pending: boolean;
  };
  alerts: SystemAlert[]; // newest first, at most 50
};

/** POST /admin/system/test-alert: 202; 409 when the monitor isn't running, has no channel, or a test is waiting. */
export type AlertTestQueued = { queued_at: string };
