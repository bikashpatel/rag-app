import type { Page } from "@playwright/test";
import type { ChatModel, DocumentConfig, DocumentItem } from "../../frontend/src/api/types";

// Intercepted responses for state the suite can't create for real without a model call (indexed documents need the
// embedding model). Shapes follow frontend/src/api/types.ts, so a contract change shows up in `npm run typecheck`.

/** Ids far above anything a real database hands out, so a canned conversation never collides with a real one. */
let nextFakeId = 1_900_000_000 + Math.floor(Math.random() * 50_000_000);
export function fakeId(): number {
  return nextFakeId++;
}

export function fakeDocument(overrides: Partial<DocumentItem> & Pick<DocumentItem, "filename">): DocumentItem {
  const now = new Date().toISOString();
  return {
    id: fakeId(),
    scope: "private",
    status: "ready",
    error: null,
    size_bytes: 48_213,
    content_type: "application/pdf",
    num_chunks: 12,
    num_pages: 4,
    owner_id: 0,
    owner_username: "",
    can_delete: true,
    is_stale: false,
    vision_pages: 0,
    generated: false,
    image: null,
    imported_from: null,
    created_at: now,
    updated_at: now,
    ...overrides,
  };
}

export function fakeModel(overrides: Partial<ChatModel> & Pick<ChatModel, "display_name">): ChatModel {
  return {
    id: fakeId(),
    provider: "anthropic",
    is_default: false,
    can_create_files: false,
    can_create_images: false,
    supports_tools: false,
    ...overrides,
  };
}

const SUPPORTED = [".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm", ".md", ".markdown", ".txt", ".csv", ".json"];

export function fakeConfig(overrides: Partial<DocumentConfig> = {}): DocumentConfig {
  return {
    supported_extensions: SUPPORTED,
    max_upload_mb: 25,
    max_documents: 100,
    max_storage_mb: 500,
    document_count: 0,
    storage_bytes: 0,
    can_upload_shared: false,
    ...overrides,
  };
}

/** Serves `GET path` from `body` (other methods go on to the next handler: the guards, or the backend). */
export async function serveJson(page: Page, path: string, body: unknown | (() => unknown)): Promise<void> {
  await page.route(`**/api${path}`, async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill({ json: typeof body === "function" ? (body as () => unknown)() : body });
  });
}
