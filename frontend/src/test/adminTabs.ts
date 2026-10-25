import type { AdminPlugin, AdminStats, PluginActivityReport, UsageReport } from "../api/types";

export function makeAdminStats(overrides: Partial<AdminStats> = {}): AdminStats {
  return {
    users: 3,
    documents: 42,
    stale_documents: 0,
    failed_documents: 0,
    active_embedding_model: { id: 3, display_name: "Text Embedding 3 Large" },
    active_vision_model: { id: 5, display_name: "Claude Sonnet 5.5 (vision)" },
    active_image_model: null,
    queue_length: 0,
    ...overrides,
  };
}

export function makeUsageReport(overrides: Partial<UsageReport> = {}): UsageReport {
  return {
    month: "2026-10",
    totals: { chat_tokens: 1_250_000, embedding_tokens: 340_000, vision_tokens: 12_000, images: 4, cost_usd: 18.5, requests: 220 },
    by_user: [
      { user_id: 7, username: "maya.chen", chat_tokens: 400_000, embedding_tokens: 40_000, vision_tokens: 0, images: 3, cost_usd: 4.2, requests: 80 },
      { user_id: 9, username: "sam.ortiz", chat_tokens: 850_000, embedding_tokens: 300_000, vision_tokens: 12_000, images: 1, cost_usd: 14.3, requests: 140 },
    ],
    by_model: [
      { model_name: "text-embedding-3-large", kind: "embedding", prompt_tokens: 340_000, completion_tokens: 0, cost_usd: 0.04, requests: 12 },
      { model_name: "Claude Opus 5.5", kind: "chat", prompt_tokens: 1_000_000, completion_tokens: 250_000, cost_usd: 17.5, requests: 220 },
    ],
    daily: [
      { date: "2026-10-02", chat_tokens: 500_000, embedding_tokens: 0, vision_tokens: 0, images: 1, cost_usd: 7 },
      { date: "2026-10-01", chat_tokens: 750_000, embedding_tokens: 340_000, vision_tokens: 12_000, images: 3, cost_usd: 11.5 },
    ],
    ...overrides,
  };
}

export function makeAdminPlugin(overrides: Partial<AdminPlugin> = {}): AdminPlugin {
  return {
    id: "gmail",
    name: "Gmail",
    description: "Search, read, draft and send email from chat.",
    auth_type: "oauth2",
    supports_import: true,
    configured: true,
    missing_settings: [],
    test_endpoints: false,
    enabled: true,
    capabilities: [
      {
        id: "read",
        name: "Read email",
        description: "Search and read email.",
        scopes: ["https://www.googleapis.com/auth/gmail.readonly"],
        enabled: true,
        note: null,
      },
      {
        id: "draft",
        name: "Create drafts",
        description: "Save drafts in the mailbox.",
        scopes: ["https://www.googleapis.com/auth/gmail.compose"],
        enabled: true,
        note: "Google's draft permission also allows sending.",
      },
      { id: "send", name: "Send email", description: "Send after the user confirms.", scopes: [], enabled: false, note: null },
    ],
    requested_scopes: ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.compose"],
    redirect_uris: ["http://localhost:8080/api/plugins/gmail/callback"],
    testing_mode: false,
    connections: { connected: 3, reconnect_needed: 1, old_key: 0 },
    activity: { month: "2026-10", actions: [] },
    ...overrides,
  };
}

export function makeActivityReport(overrides: Partial<PluginActivityReport> = {}): PluginActivityReport {
  return {
    month: "2026-10",
    totals: [
      { action: "search", ok: 14, failed: 1, refused: 0 },
      { action: "connect", ok: 3, failed: 0, refused: 0 },
      { action: "send", ok: 2, failed: 0, refused: 1 },
    ],
    by_user: [
      {
        user_id: 9,
        username: "sam.ortiz",
        actions: [
          { action: "search", ok: 4, failed: 1, refused: 0 },
          { action: "send", ok: 0, failed: 0, refused: 1 },
        ],
      },
      { user_id: 7, username: "maya.chen", actions: [{ action: "search", ok: 10, failed: 0, refused: 0 }, { action: "send", ok: 2, failed: 0, refused: 0 }] },
    ],
    ...overrides,
  };
}
