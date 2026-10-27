// API shapes — must match SPEC.md section 6 and the backend schemas exactly.

/** Per period for users with a plan (an open-ended plan and the admin: per UTC calendar month); 0 = unlimited. */
export type UserLimits = {
  monthly_token_limit: number; // the chat allowance, in standard tokens (each model counts at its rate)
  rate_limit_per_min: number;
  max_upload_mb: number; // at least 1
  max_documents: number;
  max_storage_mb: number;
  monthly_image_limit: number; // images (the admin's is 0)
  vision_monthly_token_limit: number; // plain vision tokens (scanned PDF pages)
};

export type UsageSummary = {
  month: string; // "YYYY-MM" of the window's start
  chat_tokens: number; // plain tokens of chat answers in the window
  embedding_tokens: number;
  vision_tokens: number;
  images: number; // images generated in the window
  cost_usd: number; // every kind
  monthly_token_limit: number; // 0 = unlimited; counts chat_allowance_used
  chat_allowance_used: number; // standard tokens counted in the window
  window_start: string; // the allowance window: the current period, or the UTC calendar month
  window_end: string;
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
  plan: PlanAccess;
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
  /** ×rate against the chat allowance ("×2", "×0.05"); null: no price, it counts plain tokens. */
  allowance_rate: number | null;
};

// ---------------- Plans and billing (docs/briefs/plans-billing.md) ----------------
// Amounts are integer paise; instants are ISO 8601 UTC; "IST date" fields are "YYYY-MM-DD" days in India time.

export type PlanFeatures = { files: boolean; images: boolean; plugins: string[] /* plugin ids, e.g. "gmail" */ };
export type PlanRef = { id: number; code: string; name: string };
/** exempt: the admin; none: never had a plan; active: a current period or an open-ended plan; expired: had one. */
export type PlanState = "exempt" | "none" | "active" | "expired";
/** period: one more period of the plan (now, or when the paid time ends); now: a new period from now, for a credit. */
export type CheckoutKind = "period" | "now";

export type PendingOrder = {
  public_id: string;
  kind: CheckoutKind;
  plan: PlanRef;
  amount_paise: number;
  created_at: string;
  expires_at: string;
  pay_url: string | null; // null once the link expired
};

/** Per locked feature, the name of the cheapest active plan that includes it (null when none does). */
export type PlanUnlock = {
  files: string | null;
  images: string | null;
  plugins: Record<string, string | null>;
  models: string | null; // a higher plan with a chat model the user can't use now
};

/** Me.plan: everything the UI gates on. The server decides; the UI only mirrors it. */
export type PlanAccess = {
  state: PlanState;
  access: boolean; // exempt or active
  plan: PlanRef | null; // current (period or open-ended), or the last one when expired
  complimentary: boolean;
  open_ended: boolean;
  test_mode: boolean;
  period_start: string | null; // the current period; null for an open-ended plan
  period_end: string | null;
  paid_through: string | null; // by a period: the end of the chain from the current one; expired: when it ended
  valid_through: string | null; // IST date of the last day with access; null: open-ended
  next_plan: PlanRef | null;
  expiring_soon: boolean;
  ended_at: string | null; // expired: when access ended
  features: PlanFeatures; // exempt: everything; without access: nothing
  pending_order: PendingOrder | null;
  can_start_new_period: boolean; // a current period with 90% or more of one of its allowances used
  unlock: PlanUnlock;
};

export type PlanModel = {
  id: number;
  display_name: string;
  allowance_rate: number | null; // ×rate against the chat allowance; null: no price (plain tokens)
  answers: number | null; // about this many answers with the plan's allowance; null: unlimited or unpriced
};

export type PlanPlugin = { id: string; name: string; available: boolean /* set up and turned on by the admin */ };
export type CreditLine = {
  description: string; // "Credit for unused Starter, 26 Oct to 25 Nov"
  amount_paise: number;
};

export type CheckoutOption = {
  kind: CheckoutKind;
  label: string; // "Choose Standard", "Renew", "Upgrade now · ₹1,700", "Switch on 26 Nov", …
  starts_at: string;
  ends_at: string;
  valid_through: string; // IST date
  amount_paise: number; // what is charged
  plan_price_paise: number;
  credit: CreditLine | null; // now only
  changes: string[]; // what the switch removes; may be empty
  notes: string[]; // e.g. "Your complimentary plan continues after it."
  unavailable_reason: string | null; // set: show the option disabled with this text
};

export type PlanOffer = {
  id: number;
  code: string;
  name: string;
  description: string;
  price_paise: number;
  recommended: boolean;
  features: PlanFeatures;
  plugins: PlanPlugin[]; // every registered plugin, in registry order; the plan includes those in features.plugins
  limits: UserLimits; // the plan's own
  chat_models: PlanModel[]; // the plan's: every enabled chat model when unrestricted
  all_chat_models: boolean;
  default_chat_model: PlanModel | null; // the plan's default, else the server's default when the plan has it
  answers_estimate: number | null; // with default_chat_model
  status: "active" | "retired";
  current: boolean; // the plan that applies now
  options: CheckoutOption[]; // empty for the admin
};

export type PaymentsInfo = {
  available: boolean; // for this caller: keys set, and a tester under test keys
  mode: "test" | "live" | null; // null when not available to this caller
  contact_email: string | null;
  upi_limit_paise: number; // 0: no limit
  email_reminders: boolean; // reminder emails can be sent: ask for an email only then
};

export type PlanCatalog = { plans: PlanOffer[]; payments: PaymentsInfo };

export type OrderStatus = "pending" | "paid" | "expired" | "cancelled" | "review";
export type ReceiptRef = { id: number; number: string };

export type OrderView = {
  public_id: string;
  kind: CheckoutKind;
  plan: PlanRef;
  amount_paise: number;
  credit_paise: number;
  status: OrderStatus;
  created_at: string;
  expires_at: string;
  paid_at: string | null;
  pay_url: string | null; // pending and not expired
  last_payment_error: string | null;
  period_start: string | null; // what it bought
  period_end: string | null;
  valid_through: string | null;
  receipt: ReceiptRef | null;
  method: string | null; // upi | card | netbanking | wallet | emi | bank_transfer
  test_mode: boolean;
};

/** POST /billing/checkout: accept_terms records the consent line under Pay with the order. */
export type CheckoutRequest = { plan_id: number; kind: CheckoutKind; accept_terms: true };
export type CheckoutStarted = { order: OrderView; pay_url: string };

export type PaymentStatus = "captured" | "refunded" | "partially_refunded" | "disputed";

export type PaymentView = {
  id: number;
  paid_at: string;
  plan: PlanRef | null;
  kind: CheckoutKind;
  description: string;
  amount_paise: number;
  method: string | null;
  status: PaymentStatus;
  refunded_paise: number;
  period_start: string | null; // null: a receipt-only offline payment
  period_end: string | null;
  receipt: ReceiptRef | null;
  test_mode: boolean;
};

export type PeriodSource = "payment" | "comp";

export type PeriodView = {
  id: number;
  plan: PlanRef | null;
  plan_name: string;
  starts_at: string;
  ends_at: string;
  valid_through: string; // IST date
  source: PeriodSource;
  current: boolean;
  test_mode: boolean;
  reason: string | null; // comps
};

/** The user's own, optional (GET and PUT /billing/profile); blank strings are saved as null. */
export type BillingProfile = { name: string | null /* ≤ 100 */; email: string | null /* ≤ 254 */ };
export type ComplimentaryView = { plan: PlanRef; reason: string; granted_by: string; granted_at: string };

export type BillingStatus = {
  plan: PlanAccess;
  periods: PeriodView[]; // unexpired, in order
  complimentary: ComplimentaryView | null; // the open-ended plan
  open_order: OrderView | null;
  profile: BillingProfile;
  payments: PaymentsInfo;
};

// ---------------- Public (no session) ----------------

export type PublicPlan = Omit<PlanOffer, "id" | "status" | "current" | "options">;
/** The Markdown files frontend/public/legal/<slug>.md. */
export type PageSlug = "pricing" | "terms" | "privacy" | "refunds" | "contact" | "about" | "shipping";
export type PublicSite = {
  business_name: string;
  contact_email: string | null;
  contact_phone: string | null;
  address: string | null;
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
  month: string; // "YYYY-MM" of period_start
  // The allowance window: the current period, or the UTC calendar month (an open-ended plan, the admin, no access).
  period_start: string;
  resets_at: string;
  chat_allowance: UsageAmount; // standard tokens against monthly_token_limit, the chat allowance
  chat_tokens: number; // plain tokens of chat answers; no limit
  images: UsageAmount; // monthly_image_limit
  vision_tokens: UsageAmount; // vision_monthly_token_limit
  embedding_tokens: number; // recorded, never limited
  requests: number; // chat answers in the window
  rate_limit_per_min: number; // 0 = unlimited
  cost_usd: { total: number; chat: number; embedding: number; vision: number; image: number }; // estimated
  documents: { count: number; max_documents: number; storage_bytes: number; max_storage_mb: number; max_upload_mb: number };
  daily: {
    date: string; // "YYYY-MM-DD" (UTC); every day of the window through today, zeros included
    chat_allowance: number;
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
    allowance_tokens: number; // 0 for other kinds than chat
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

/** Per-user overrides of the plan's limits: null = the plan's value; 0 = unlimited. */
export type LimitOverrides = { [K in keyof UserLimits]: number | null };

export type AdminUserPlan = {
  state: PlanState;
  plan: PlanRef | null; // current (period or open-ended), or the last one when expired
  valid_through: string | null; // IST date; null: open-ended (or none)
  paid_through: string | null;
  complimentary: boolean;
  open_ended: boolean;
  test_mode: boolean;
  expiring_soon: boolean;
  ended_at: string | null; // expired: when access ended
};

export type AdminUser = {
  id: number;
  username: string;
  is_admin: boolean;
  is_active: boolean;
  restrict_models: boolean;
  allowed_model_ids: number[];
  created_at: string;
  last_login_at: string | null;
  /** In the user's allowance window (the current period, or the UTC calendar month). */
  usage: {
    chat_tokens: number;
    chat_allowance_used: number; // standard tokens, against limits.monthly_token_limit
    embedding_tokens: number;
    vision_tokens: number;
    images: number;
    cost_usd: number;
    window_start: string;
    window_end: string;
  };
  document_count: number;
  storage_bytes: number;
  billing_tester: boolean; // may check out while test keys run
  limits: UserLimits; // effective: the plan's merged with the overrides
  overrides: LimitOverrides;
  above_plan: (keyof UserLimits)[]; // overrides more generous than the plan's value
  no_models: boolean; // the allowed list and the plan's models don't overlap
  plan: AdminUserPlan;
};

export type AdminUserCreate = {
  username: string; // 3-64, ^[a-zA-Z0-9_.-]+$, lowercased
  password: string; // >= 8
  is_active?: boolean;
  restrict_models?: boolean;
  allowed_model_ids?: number[];
  billing_tester?: boolean;
  overrides?: Partial<LimitOverrides>;
  grant?: Omit<GrantRequest, "start">; // a complimentary plan from now
  confirm_unlimited?: boolean; // required when an override sets monthly_token_limit to 0
};

export type AdminUserUpdate = {
  password?: string; // = reset
  is_active?: boolean;
  restrict_models?: boolean;
  allowed_model_ids?: number[];
  billing_tester?: boolean;
  overrides?: Partial<LimitOverrides>; // a key present sets it, null clears it; absent keys stay
  confirm_unlimited?: boolean; // required when an override sets monthly_token_limit to 0
};

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

// ---------------- Admin: plans and billing ----------------

export type PlanInput = {
  name: string; // 1..60
  description: string; // ≤ 300, one line
  price_paise: number; // whole rupees (a multiple of 100), ₹1 to ₹1,00,000
  rank: number; // unique; higher = a higher plan, which must cost more among active plans
  status: "active" | "retired";
  recommended: boolean; // at most one plan, which must be active
  features: PlanFeatures;
  limits: UserLimits; // all seven
  chat_model_ids: number[] | null; // null: every enabled chat model
  default_chat_model_id: number | null; // in chat_model_ids when that is a list
};

export type PlanCostEstimate = {
  kept_paise: number; // the price after Razorpay's fee
  worst_case_paise: number; // every allowance used up
  worst_share: number; // of kept_paise, 0..n
  typical_paise: number; // 40% of every allowance
  typical_share: number;
  unlimited: string[]; // limit names set to 0: no ceiling
};

export type AdminPlan = PlanInput & {
  id: number;
  code: string;
  current_users: number;
  scheduled_periods: number;
  complimentary_users: number; // open-ended
  used: boolean; // can't be deleted
  cost: PlanCostEstimate;
  created_at: string;
  updated_at: string;
};

export type PlanCreate = PlanInput & { code: string /* ^[a-z][a-z0-9_-]{1,31}$, never changed */ };
export type PlanUpdate = Partial<PlanInput> & {
  apply_to_current?: boolean; // also the current and scheduled periods (default false)
  confirm_reduction?: boolean; // required when that lowers what paid periods have (409 confirmation_required)
};
export type PlanSaved = { plan: AdminPlan; applied_periods: number };

/** What the plan editor computes PlanCostEstimate from, as the server does. */
export type CostInputs = {
  usd_inr: number;
  standard_usd_per_mtok: number; // 3.20
  vision_usd_per_mtok: number;
  image_usd: number;
  fee_bp: number; // Razorpay's fee with GST on it, in basis points of the charge (236)
};

export type BillingSettings = {
  business_name: string; // 1..100
  address: string; // ≤ 500, several lines
  contact_email: string | null;
  contact_phone: string | null; // ≤ 20
  receipt_prefix: string; // 1 to 4 of A-Z0-9, not "TEST"
  upi_limit_paise: number; // 0: no limit
};

export type PaymentsSetup = {
  configured: boolean;
  mode: "test" | "live" | null;
  key_id_hint: string | null; // "rzp_test_…AbCd"
  webhook_secret_set: boolean;
  previous_webhook_secret_set: boolean;
  last_webhook_at: string | null;
  webhook_url: string; // to register in Razorpay's Dashboard
  webhook_events: string[];
  test_links_created: number;
  reconciler_last_run_at: string | null;
  last_error: string | null; // e.g. the keys were rejected
  paused_orders: number; // of the other mode
  testers: number;
  email_reminders: boolean;
};

export type BillingAdminConfig = { settings: BillingSettings; payments: PaymentsSetup; estimate: CostInputs };

export type UserRef = { id: number | null /* null: deleted */; username: string };

export type AdminOrder = OrderView & {
  id: number;
  user: UserRef;
  mode: "test" | "live" | "offline";
  razorpay_link_id: string | null;
  razorpay_order_id: string | null;
  review_reason: string | null;
  review_note: string | null;
  checks: number;
  last_checked_at: string | null;
  next_check_at: string | null;
  last_error: string | null;
  paused: boolean; // of the other mode than the server's keys
};

export type AdminOrderDetail = AdminOrder & {
  events: { at: string; source: string; type: string; detail: Record<string, unknown>; error: string | null }[];
};

export type RefundView = {
  id: number;
  amount_paise: number;
  status: "creating" | "pending" | "processed" | "failed";
  initiated_by: "admin" | "razorpay";
  admin_username: string | null;
  reason: string | null;
  end_access: boolean;
  created_at: string;
  processed_at: string | null;
  last_error: string | null;
};

export type DisputeStatus = "open" | "under_review" | "action_required" | "won" | "lost" | "closed";

export type DisputeView = {
  id: number;
  razorpay_dispute_id: string;
  amount_paise: number;
  status: DisputeStatus;
  reason_code: string | null;
  respond_by: string | null;
  created_at: string;
  updated_at: string;
};

export type AdminPayment = PaymentView & {
  user: UserRef;
  order_public_id: string | null;
  razorpay_payment_id: string | null;
  fee_paise: number | null;
  fee_tax_paise: number | null;
  refundable_paise: number;
  credit_used_paise: number; // credit later upgrades took from its period
  refunds: RefundView[];
  disputes: DisputeView[];
  offline_reference: string | null;
  mode: "test" | "live" | "offline";
};

/** GET /admin/billing/orders and /admin/billing/payments: newest first, 50 per page. */
export type Page<T> = { items: T[]; total: number };

export type RefundRequest = {
  amount_paise: number | null; // null: all that's refundable
  reason: string; // 1..200
  end_access: boolean; // a partial refund ends access only with this
};

export type OfflinePaymentRequest = {
  user_id: number;
  plan_id: number;
  amount_paise: number; // ≥ 100
  received_on: string; // YYYY-MM-DD
  reference: string; // 1..64, the bank reference
  note: string | null; // ≤ 500; required below the plan's price
  apply: boolean; // false: a receipt only
};

export type GrantRequest = {
  plan_id: number;
  days: number | null; // 1..90; null: open-ended
  reason: string; // 1..200
  start: "now" | "after_current"; // ignored when open-ended
};
export type ChangePlanRequest = { plan_id: number; reason: string };
export type RevokeRequest = { reason: string };
export type EndComplimentaryRequest = { reason: string };
export type ResolveRequest = { note: string /* 1..500 */ };
export type ReconcileResult = { checked: number };

export type AttentionKind =
  | "review"
  | "duplicate"
  | "refund_failed"
  | "refund_pending"
  | "partial_refund"
  | "dispute"
  | "dispute_lost" // a dispute lost in the last 30 days
  | "credit_reversed" // a refunded or charged-back payment's credit still runs in an upgrade
  | "webhook_errors"
  | "payments_not_configured"
  | "test_mode"
  | "test_keys_public"
  | "live_without_webhook"
  | "keys_rejected"
  | "paused_orders";

export type AttentionItem = {
  kind: AttentionKind;
  message: string;
  detail: string | null;
  order_public_id: string | null;
  payment_id: number | null;
  count: number | null;
};

export type UserPlanRow = {
  user: UserRef;
  plan: string | null;
  date: string; // IST date: valid through, or the day it ended
  last_payment_at: string | null;
};

export type BillingSummary = {
  month: string; // "YYYY-MM", IST
  gross_paise: number;
  fee_paise: number;
  refunded_paise: number;
  disputed_paise: number;
  net_paise: number; // gross − fees − refunds − disputes lost
  payments: number;
  refunds: number;
  by_plan: { plan: string; payments: number; gross_paise: number }[];
  current_users: { plan: string; paid: number; complimentary: number }[];
  users_without_plan: number;
  months: { month: string; gross_paise: number; net_paise: number }[]; // the 12 months up to this one
  expiring: UserPlanRow[]; // valid through within 7 days, nothing after
  lapsed: UserPlanRow[]; // ended within the last 30 days
  attention: AttentionItem[];
};

export type UserBilling = {
  plan: PlanAccess;
  periods: PeriodView[]; // every one, newest first, at most 100
  complimentary: ComplimentaryView | null;
  orders: AdminOrder[];
  payments: AdminPayment[];
  profile: BillingProfile;
  // newest 50
  events: { at: string; source: string; type: string; actor: string | null; detail: Record<string, unknown> }[];
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
 * !configured → "Not set up"; !enabled → "Turned off by the admin"; !in_plan → "Not in your plan" (offer Upgrade);
 * connection reconnect_needed → "Reconnect needed"; connection → "Connected"; else "Not connected" (offer Connect). In
 * the first three states a caller who still has a connection can Disconnect it (DELETE …/connection works then too).
 * Tools reach chat only when enabled, in the plan and connected with "Use in chat" on.
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
  in_plan: boolean; // the caller's plan includes it (always true for the admin); false: "Not in your plan"
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
  | "monitor"
  | "billing"; // "Payments"
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
