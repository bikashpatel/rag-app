// Typed API payloads (src/api/types.ts) with realistic defaults; payload drift fails `tsc`.

import type {
  AccountInfo,
  AdminConfig,
  AdminModel,
  AdminUser,
  ChatMessage,
  ChatModel,
  DocumentConfig,
  DocumentItem,
  EmailRecipient,
  EmailSource,
  GeneratedFile,
  LoginEvent,
  Me,
  MyUsage,
  PendingSend,
  PluginCapabilityInfo,
  PluginConnectionStatus,
  PluginInfo,
  Preferences,
  Source,
  UserLimits,
} from "../api/types";

export const NOW = "2026-10-20T09:30:00Z";

export function makeLimits(overrides: Partial<UserLimits> = {}): UserLimits {
  return {
    monthly_token_limit: 2_000_000,
    rate_limit_per_min: 20,
    max_upload_mb: 25,
    max_documents: 100,
    max_storage_mb: 500,
    monthly_image_limit: 50,
    ...overrides,
  };
}

export function makePreferences(overrides: Partial<Preferences> = {}): Preferences {
  return { theme: "system", accent: "clay", chat_font_size: "default", enter_to_send: true, default_model_id: null, ...overrides };
}

export function makeMe(overrides: Partial<Me> = {}): Me {
  return {
    id: 7,
    username: "maya.chen",
    is_admin: false,
    limits: makeLimits(),
    usage: {
      month: "2026-10",
      chat_tokens: 120_000,
      embedding_tokens: 40_000,
      vision_tokens: 0,
      images: 3,
      cost_usd: 1.42,
      monthly_token_limit: 2_000_000,
    },
    preferences: makePreferences(),
    ...overrides,
  };
}

export function makeAdminMe(overrides: Partial<Me> = {}): Me {
  return makeMe({
    id: 1,
    username: "admin",
    is_admin: true,
    limits: makeLimits({
      monthly_token_limit: 0,
      rate_limit_per_min: 0,
      max_documents: 0,
      max_storage_mb: 0,
      monthly_image_limit: 0,
    }),
    ...overrides,
  });
}

export function makeChatModel(overrides: Partial<ChatModel> = {}): ChatModel {
  return {
    id: 1,
    display_name: "Claude Opus 5.5",
    provider: "anthropic",
    is_default: true,
    can_create_files: true,
    can_create_images: true,
    supports_tools: true,
    ...overrides,
  };
}

export function makeDocument(overrides: Partial<DocumentItem> = {}): DocumentItem {
  return {
    id: 101,
    filename: "quarterly-report.pdf",
    scope: "private",
    status: "ready",
    error: null,
    size_bytes: 482_133,
    content_type: "application/pdf",
    num_chunks: 42,
    num_pages: 12,
    owner_id: 7,
    owner_username: "maya.chen",
    can_delete: true,
    is_stale: false,
    vision_pages: 0,
    generated: false,
    image: null,
    imported_from: null,
    created_at: "2026-10-18T14:05:00Z",
    updated_at: "2026-10-18T14:06:10Z",
    ...overrides,
  };
}

export function makeDocumentConfig(overrides: Partial<DocumentConfig> = {}): DocumentConfig {
  return {
    supported_extensions: [".pdf", ".docx", ".pptx", ".xlsx", ".csv", ".html", ".htm", ".md", ".txt", ".json"],
    max_upload_mb: 25,
    max_documents: 100,
    max_storage_mb: 500,
    document_count: 4,
    storage_bytes: 3_250_000,
    can_upload_shared: false,
    ...overrides,
  };
}

export function makeSource(overrides: Partial<Source> = {}): Source {
  return {
    index: 1,
    document_id: 101,
    filename: "quarterly-report.pdf",
    page: 3,
    chunk_index: 5,
    score: 0.82,
    snippet: "Revenue grew 18% year over year, driven by the enterprise segment.",
    ...overrides,
  };
}

export function makeEmailSource(overrides: Partial<EmailSource> = {}): EmailSource {
  return {
    kind: "email",
    index: 2,
    plugin_id: "gmail",
    message_id: "18f2a9c0d1e2f3a4",
    thread_id: "18f2a9c0d1e2f3a0",
    subject: "Q3 numbers",
    sender: "Priya Raman <priya@example.com>",
    date: "2026-10-17T08:12:00Z",
    snippet: "Attached are the final Q3 numbers for review.",
    url: "https://mail.google.com/mail/u/0/#all/18f2a9c0d1e2f3a0",
    ...overrides,
  };
}

export function makeGeneratedFile(overrides: Partial<GeneratedFile> = {}): GeneratedFile {
  return {
    document_id: 205,
    filename: "summary.docx",
    format: "docx",
    size_bytes: 18_432,
    status: "ready",
    image: null,
    ...overrides,
  };
}

export function makeMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: 501,
    role: "assistant",
    content: "Revenue grew 18% [1].",
    sources: [makeSource()],
    model_name: "Claude Opus 5.5",
    prompt_tokens: 1_200,
    completion_tokens: 180,
    error: null,
    files: [],
    plugin_cards: [],
    created_at: NOW,
    ...overrides,
  };
}

export function makeCapability(overrides: Partial<PluginCapabilityInfo> = {}): PluginCapabilityInfo {
  return { id: "read", name: "Read email", description: "Search and read your email.", enabled: true, granted: true, ...overrides };
}

export function makeConnection(overrides: Partial<PluginConnectionStatus> = {}): PluginConnectionStatus {
  return {
    account_email: "maya@example.com",
    status: "connected",
    status_reason: null,
    status_message: null,
    granted_scopes: ["https://www.googleapis.com/auth/gmail.readonly"],
    chat_enabled: true,
    connected_at: "2026-10-01T10:00:00Z",
    last_used_at: "2026-10-19T16:20:00Z",
    refresh_expires_at: null,
    ...overrides,
  };
}

export function makeGmailPlugin(overrides: Partial<PluginInfo> = {}): PluginInfo {
  return {
    id: "gmail",
    name: "Gmail",
    description: "Search, read, draft and send email from chat.",
    auth_type: "oauth2",
    configured: true,
    enabled: true,
    testing_mode: false,
    supports_import: true,
    import_max_items: 20,
    capabilities: [
      makeCapability(),
      makeCapability({ id: "draft", name: "Create drafts", description: "Write drafts in your mailbox." }),
      makeCapability({ id: "send", name: "Send email", description: "Send email after you confirm it." }),
    ],
    connection: makeConnection(),
    ...overrides,
  };
}

export function makeRecipient(overrides: Partial<EmailRecipient> = {}): EmailRecipient {
  return { address: "priya@example.com", name: "Priya Raman", unicode_domain: null, unmentioned: false, ...overrides };
}

export function makePendingSend(overrides: Partial<PendingSend> = {}): PendingSend {
  return {
    id: 31,
    status: "pending",
    account_email: "maya@example.com",
    to: [makeRecipient()],
    cc: [],
    bcc: [],
    subject: "Q3 summary",
    body: "Hi Priya,\n\nHere is the Q3 summary you asked for.\n\nMaya",
    attachments: [],
    reply_to: null,
    content_hash: "sha256:9f2c1e",
    created_at: "2026-10-20T09:25:00Z",
    expires_at: "2026-10-20T10:25:00Z",
    sent_at: null,
    error: null,
    gmail_url: null,
    replaced_by: null,
    ...overrides,
  };
}

export function makeAdminUser(overrides: Partial<AdminUser> = {}): AdminUser {
  return {
    id: 7,
    username: "maya.chen",
    is_admin: false,
    is_active: true,
    monthly_token_limit: 2_000_000,
    rate_limit_per_min: 20,
    max_upload_mb: 25,
    max_documents: 100,
    max_storage_mb: 500,
    monthly_image_limit: 50,
    restrict_models: false,
    allowed_model_ids: [],
    created_at: "2026-09-02T11:00:00Z",
    last_login_at: "2026-10-19T08:45:00Z",
    usage: { chat_tokens: 120_000, embedding_tokens: 40_000, vision_tokens: 0, images: 3, cost_usd: 1.42 },
    document_count: 4,
    storage_bytes: 3_250_000,
    ...overrides,
  };
}

export function makeAdminModel(overrides: Partial<AdminModel> = {}): AdminModel {
  return {
    id: 1,
    display_name: "Claude Opus 5.5",
    litellm_model: "anthropic/claude-opus-5-5",
    kind: "chat",
    enabled: true,
    is_default: true,
    api_base: null,
    api_key_env: null,
    max_output_tokens: null,
    temperature: null,
    input_cost_per_mtok: 5,
    output_cost_per_mtok: 25,
    embedding_dim: null,
    created_at: "2026-09-01T10:00:00Z",
    updated_at: "2026-09-01T10:00:00Z",
    ...overrides,
  };
}

export function makeAdminConfig(overrides: Partial<AdminConfig> = {}): AdminConfig {
  return {
    defaults: makeLimits(),
    max_upload_mb_hard: 100,
    admin_username: "admin",
    ...overrides,
  };
}

export function makeLoginEvent(overrides: Partial<LoginEvent> = {}): LoginEvent {
  return {
    id: 90,
    created_at: "2026-10-20T08:00:00Z",
    ip: "203.0.113.x",
    ip_private: false,
    browser: "Firefox 144",
    os: "macOS",
    device: "desktop",
    current: true,
    ...overrides,
  };
}

export function makeAccount(overrides: Partial<AccountInfo> = {}): AccountInfo {
  return {
    id: 7,
    username: "maya.chen",
    is_admin: false,
    password_managed_by_env: false,
    created_at: "2026-09-02T11:00:00Z",
    last_login_at: "2026-10-20T08:00:00Z",
    conversation_count: 12,
    recent_logins: [
      makeLoginEvent(),
      makeLoginEvent({ id: 89, created_at: "2026-10-18T19:30:00Z", browser: "Safari 26", os: "iOS", device: "mobile", current: false }),
    ],
    ...overrides,
  };
}

export function makeMyUsage(overrides: Partial<MyUsage> = {}): MyUsage {
  const days = ["2026-10-01", "2026-10-02", "2026-10-03"];
  return {
    month: "2026-10",
    period_start: "2026-10-01T00:00:00Z",
    resets_at: "2026-11-01T00:00:00Z",
    chat_tokens: { used: 120_000, limit: 2_000_000 },
    images: { used: 3, limit: 50 },
    vision_tokens: { used: 0, limit: 500_000 },
    embedding_tokens: 40_000,
    requests: 37,
    rate_limit_per_min: 20,
    cost_usd: { total: 1.42, chat: 1.2, embedding: 0.02, vision: 0, image: 0.2 },
    documents: { count: 4, max_documents: 100, storage_bytes: 3_250_000, max_storage_mb: 500, max_upload_mb: 25 },
    daily: days.map((date, i) => ({
      date,
      chat_tokens: 40_000 * (i + 1),
      requests: 10 + i,
      images: i,
      vision_tokens: 0,
      embedding_tokens: 10_000,
      cost_usd: 0.4 + i * 0.1,
    })),
    by_model: [
      { model_name: "Claude Opus 5.5", kind: "chat", tokens: 120_000, requests: 37, cost_usd: 1.2 },
      { model_name: "GPT Image", kind: "image", tokens: 0, requests: 3, cost_usd: 0.2 },
    ],
    ...overrides,
  };
}
