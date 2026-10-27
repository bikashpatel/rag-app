import type { Page } from "@playwright/test";
import type {
  ChatModel,
  CheckoutOption,
  DocumentConfig,
  DocumentItem,
  OrderView,
  PlanCatalog,
  PlanModel,
  PlanOffer,
} from "../../frontend/src/api/types";
import { BASE_URL } from "./env";

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
    allowance_rate: null,
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

const DAY_MS = 86_400_000;

/** A period of `days` from now, as the server quotes it (the end at the next midnight IST is close enough here). */
function quote(label: string, amount: number, days = 1): CheckoutOption {
  const now = Date.now();
  const end = new Date(now + days * DAY_MS);
  return {
    kind: "period",
    label,
    starts_at: new Date(now).toISOString(),
    ends_at: end.toISOString(),
    valid_through: end.toISOString().slice(0, 10),
    amount_paise: amount,
    plan_price_paise: amount,
    credit: null,
    changes: [],
    notes: [],
    unavailable_reason: null,
  };
}

function fakePlanModel(display_name: string, allowance_rate: number, answers: number): PlanModel {
  return { id: fakeId(), display_name, allowance_rate, answers };
}

function fakeOffer(code: string, name: string, price: number, overrides: Partial<PlanOffer>): PlanOffer {
  const haiku = fakePlanModel("Claude Haiku 5.5", 0.05, 1_600);
  return {
    id: fakeId(),
    code,
    name,
    description: `${name} for the end-to-end tests.`,
    price_paise: price,
    recommended: false,
    features: { files: false, images: false, plugins: [] },
    plugins: [{ id: "gmail", name: "Gmail", available: true }],
    limits: {
      monthly_token_limit: 500_000,
      rate_limit_per_min: 10,
      max_upload_mb: 10,
      max_documents: 50,
      max_storage_mb: 200,
      monthly_image_limit: 30,
      vision_monthly_token_limit: 150_000,
    },
    chat_models: [haiku],
    all_chat_models: false,
    default_chat_model: haiku,
    answers_estimate: 1_600,
    status: "active",
    current: false,
    options: [quote(`Choose ${name}`, price)],
    ...overrides,
  };
}

/** GET /billing/plans for a tester under test keys (so a pay_url on the app's own origin is accepted). */
export function fakeCatalog(): PlanCatalog {
  return {
    plans: [
      fakeOffer("starter", "Starter", 50_000, {}),
      fakeOffer("standard", "Standard", 200_000, {
        recommended: true,
        features: { files: true, images: false, plugins: [] },
      }),
    ],
    payments: { available: true, mode: "test", contact_email: null, upi_limit_paise: 200_000, email_reminders: false },
  };
}

/** A checkout of Standard; `paid` adds what the payment bought. */
export function fakeOrder(publicId: string, status: OrderView["status"]): OrderView {
  const now = new Date().toISOString();
  const paid = status === "paid";
  const end = new Date(Date.now() + DAY_MS).toISOString();
  return {
    public_id: publicId,
    kind: "period",
    plan: { id: 2, code: "standard", name: "Standard" },
    amount_paise: 200_000,
    credit_paise: 0,
    status,
    created_at: now,
    expires_at: new Date(Date.now() + 20 * 60_000).toISOString(),
    paid_at: paid ? now : null,
    pay_url: paid ? null : `${BASE_URL}/__fake-razorpay/${publicId}`,
    last_payment_error: null,
    period_start: paid ? now : null,
    period_end: paid ? end : null,
    valid_through: paid ? end.slice(0, 10) : null,
    receipt: paid ? { id: fakeId(), number: "TEST/26-27/000001" } : null,
    method: paid ? "card" : null,
    test_mode: true,
  };
}
