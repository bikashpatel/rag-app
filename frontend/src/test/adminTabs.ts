import type { AdminPlugin, AdminStats, PluginActivityReport, SystemStatus, UsageReport } from "../api/types";

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

export function makeSystemStatus(overrides: Partial<SystemStatus> = {}): SystemStatus {
  return {
    status: "ok",
    checked_at: "2026-10-20T09:29:50Z",
    app_version: "v1.4.0-3-gabc1234",
    checks: [
      { id: "postgres", label: "Database (Postgres)", status: "ok", detail: "Reachable; the database holds 48 MB" },
      { id: "redis", label: "Redis", status: "ok", detail: "Reachable" },
      { id: "qdrant", label: "Vector store (Qdrant)", status: "ok", detail: "Reachable; 1 collection" },
      { id: "worker", label: "Ingestion worker", status: "ok", detail: "Heartbeat 4 s ago; 0 jobs running" },
      { id: "queue", label: "Ingestion queue", status: "ok", detail: "Idle" },
      { id: "backup", label: "Backups", status: "ok", detail: "Last backup 7.0 h ago (512 MB)" },
      { id: "disk_uploads", label: "Disk space for uploads", status: "ok", detail: "40 GB free of 100 GB (40 %)" },
      { id: "disk_backups", label: "Disk space for backups", status: "ok", detail: "40 GB free of 100 GB (40 %)" },
      { id: "proxy", label: "Web server (nginx)", status: "ok", detail: "The app answers through nginx" },
      { id: "monitor", label: "Monitor and alerts", status: "ok", detail: "Ran 20 s ago; alerts go to webhook, ntfy" },
    ],
    worker: { heartbeat_age_seconds: 4.2, running_jobs: 0 },
    queue: { queued: 0, pending: 0, processing: 0, oldest_pending_age_seconds: null },
    failed_documents_24h: 0,
    database_size_bytes: 48 * 1024 ** 2,
    disks: [
      { id: "uploads", label: "Uploads", free_bytes: 40 * 1024 ** 3, total_bytes: 100 * 1024 ** 3, checked_at: "2026-10-20T09:29:50Z" },
      { id: "backups", label: "Backups", free_bytes: 40 * 1024 ** 3, total_bytes: 100 * 1024 ** 3, checked_at: "2026-10-20T09:25:00Z" },
    ],
    backup: {
      status_seen: true,
      max_age_hours: 26,
      last_success_at: "2026-10-20T02:30:41Z",
      last_success_name: "20261020T023000Z-scheduled",
      last_success_size_bytes: 512 * 1024 ** 2,
      last_success_duration_seconds: 41,
      age_hours: 7,
      last_failure_at: null,
      last_failure_error: null,
      failing: false,
      running_since: null,
      schedule: "02:30",
      next_run_at: "2026-10-21T02:30:00Z",
      scheduler_seen_at: "2026-10-20T09:25:00Z",
      backups_kept: 9,
      total_size_bytes: 4 * 1024 ** 3,
    },
    gmail_reconnect_needed: 0,
    monitor: {
      running: true,
      last_run_at: "2026-10-20T09:29:30Z",
      interval_seconds: 60,
      channels: ["webhook", "ntfy"],
      test_pending: false,
    },
    alerts: [
      {
        at: "2026-10-20T08:12:00Z",
        kind: "recovery",
        title: "[RAG Assistant] Resolved: Vector store (Qdrant)",
        lines: ["RESOLVED Vector store (Qdrant): OK again after 4 min (Reachable; 1 collection)", "At 2026-10-20 08:12:00 UTC.", "Details: http://localhost:8080/admin#system"],
        delivered: ["webhook", "ntfy"],
        failed: [],
      },
      {
        at: "2026-10-20T08:08:00Z",
        kind: "problem",
        title: "[RAG Assistant] Problem: Vector store (Qdrant)",
        lines: ["PROBLEM Vector store (Qdrant) (critical): Unreachable: connection refused", "At 2026-10-20 08:08:00 UTC."],
        delivered: ["webhook"],
        failed: ["ntfy"],
      },
    ],
    ...overrides,
  };
}
