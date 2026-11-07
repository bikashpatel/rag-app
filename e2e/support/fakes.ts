import type { Page } from "@playwright/test";
import type {
  Accepted,
  AdminOrder,
  AuthConfig,
  ChatModel,
  CheckoutOption,
  DocumentConfig,
  DocumentItem,
  EmailTokenInfo,
  GooglePending,
  Me,
  OrderView,
  PasswordTokenInfo,
  PlanCatalog,
  PlanModel,
  PlanOffer,
  SignupCheck,
  UndoInfo,
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
    payments: {
      available: true,
      mode: "test",
      contact_email: null,
      upi_limit_paise: 200_000,
      email_reminders: false,
      upi_direct: false,
    },
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
    channel: "razorpay",
    upi: null,
  };
}

/** A direct-UPI order of Standard (docs/briefs/upi-direct.md 5): pending (waiting for the UTR), submitted (waiting for
 * the admin), paid or rejected. */
export function fakeUpiOrder(publicId: string, status: "pending" | "submitted" | "paid" | "rejected"): OrderView {
  const now = new Date().toISOString();
  const paid = status === "paid";
  const submitted = status !== "pending";
  const end = new Date(Date.now() + 30 * DAY_MS).toISOString();
  return {
    ...fakeOrder(publicId, status),
    expires_at: new Date(Date.now() + DAY_MS).toISOString(),
    pay_url: null,
    period_start: paid ? now : null,
    period_end: paid ? end : null,
    valid_through: paid ? end.slice(0, 10) : null,
    receipt: paid ? { id: fakeId(), number: "RA/26-27/000001" } : null,
    method: paid ? "upi" : null,
    test_mode: false,
    channel: "upi_direct",
    upi: {
      reference: "AN-7K4M-Q9TX",
      upi_id: "asha.rao@okhdfcbank",
      payee_name: "Asha Rao",
      uri: submitted ? null : "upi://pay?pa=asha.rao@okhdfcbank&pn=Asha%20Rao&am=2000.00&cu=INR&tn=AN-7K4M-Q9TX",
      utr: submitted ? "630512345678" : null,
      submitted_at: submitted ? now : null,
      decided_at: paid || status === "rejected" ? now : null,
      rejection:
        status === "rejected"
          ? {
              reason: "not_received",
              message: "We couldn't find a payment of ₹2,000 with UTR 6305 1234 5678.",
              note: null,
            }
          : null,
    },
  };
}

// ---------------- Sign-up, sign-in and the account's email (docs/briefs/signup-auth.md 15.3) ----------------
// Every endpoint that would send mail or call Cloudflare or Google is route-mocked with these.

const HOUR_MS = 3_600_000;

function fromNow(ms: number): string {
  return new Date(Date.now() + ms).toISOString();
}

/** Sign-up open with both methods, Google sign-in and reset available, Cloudflare's always-passing test sitekey. */
/** The same order as the admin lists it (Billing → UPI), for a made-up e2e-… user that no test signs in as. */
export function fakeAdminUpiOrder(publicId: string, status: "submitted" | "paid" | "rejected"): AdminOrder {
  return {
    ...fakeUpiOrder(publicId, status),
    id: fakeId(),
    user: { id: fakeId(), username: "e2e-upi-payer", email: null },
    mode: "offline",
    razorpay_link_id: null,
    razorpay_order_id: null,
    review_reason: null,
    review_note: null,
    checks: 0,
    last_checked_at: null,
    next_check_at: null,
    last_error: null,
    paused: false,
    upi_decided_by: status === "submitted" ? null : "admin",
  };
}

/** A QR-like SVG for the intercepted QR endpoints (the real one is drawn by the server). */
export const FAKE_QR_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 29 29"><rect width="29" height="29" fill="#fff"/>' +
  '<path d="M4 4h7v7H4zM18 4h7v7h-7zM4 18h7v7H4zM13 13h3v3h-3z"/></svg>';

export function fakeAuthConfig(overrides: Partial<AuthConfig> = {}): AuthConfig {
  return {
    signup: { open: true, email: true, google: true },
    google_signin: true,
    password_reset: true,
    turnstile_site_key: "1x00000000000000000000AA",
    password_min_length: 8,
    password_max_length: 256,
    terms_version: "2026-11-01",
    contact_email: null,
    ...overrides,
  };
}

export const ACCEPTED: Accepted = { status: "accepted" };

export function fakeSignupCheck(overrides: Partial<SignupCheck> & Pick<SignupCheck, "email">): SignupCheck {
  return { expires_at: fromNow(24 * HOUR_MS), needs_password: false, next: null, ...overrides };
}

export function fakePasswordTokenInfo(
  overrides: Partial<PasswordTokenInfo> & Pick<PasswordTokenInfo, "email" | "username">,
): PasswordTokenInfo {
  return { has_password: true, expires_at: fromNow(HOUR_MS / 2), google: null, ...overrides };
}

export function fakeEmailTokenInfo(overrides: Partial<EmailTokenInfo> & Pick<EmailTokenInfo, "email">): EmailTokenInfo {
  return { account: "e***@example.com", signed_in: true, ...overrides };
}

export function fakeUndoInfo(overrides: Partial<UndoInfo> & Pick<UndoInfo, "old_email">): UndoInfo {
  return { new_email: "e***@example.org", ...overrides };
}

export function fakeGooglePending(overrides: Partial<GooglePending> & Pick<GooglePending, "email">): GooglePending {
  return { email_verified: true, from: "signup", expires_at: fromNow(HOUR_MS / 4), ...overrides };
}

/** What a just-created account's sign-up answers: no plan yet, so the app sends it to /plans. */
export function fakeNewMe(overrides: Partial<Me> & Pick<Me, "username" | "email">): Me {
  const now = new Date();
  const monthStart = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
  const monthEnd = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 1));
  return {
    id: fakeId(),
    is_admin: false,
    limits: {
      monthly_token_limit: 500_000,
      rate_limit_per_min: 10,
      max_upload_mb: 10,
      max_documents: 50,
      max_storage_mb: 200,
      monthly_image_limit: 30,
      vision_monthly_token_limit: 150_000,
    },
    usage: {
      month: monthStart.toISOString().slice(0, 7),
      chat_tokens: 0,
      embedding_tokens: 0,
      vision_tokens: 0,
      images: 0,
      cost_usd: null,
      monthly_token_limit: 500_000,
      chat_allowance_used: 0,
      window_start: monthStart.toISOString(),
      window_end: monthEnd.toISOString(),
    },
    preferences: {
      theme: "system",
      accent: "clay",
      chat_font_size: "default",
      enter_to_send: true,
      default_model_id: null,
      email_plan_reminders: true,
      email_receipts: true,
    },
    plan: {
      state: "none",
      access: false,
      plan: null,
      complimentary: false,
      open_ended: false,
      test_mode: false,
      period_start: null,
      period_end: null,
      paid_through: null,
      valid_through: null,
      next_plan: null,
      expiring_soon: false,
      ended_at: null,
      features: { files: false, images: false, plugins: [] },
      pending_order: null,
      can_start_new_period: false,
      unlock: { files: "Standard", images: "Professional", plugins: { gmail: "Professional" }, models: null },
      rejected_order: null,
    },
    display_name: null,
    ...overrides,
  };
}

/**
 * Cloudflare's api.js, stubbed (docs/briefs/signup-auth.md 7.5): every widget hands out the dummy token at once, and
 * again after a reset. Served for the exact URL the app loads, ahead of the outside-host guard.
 */
const TURNSTILE_STUB = `(() => {
  const widgets = new Map();
  let next = 0;
  const pass = (options) => setTimeout(() => options.callback("XXXX.DUMMY.TOKEN.XXXX"), 50);
  window.turnstile = {
    render(container, options) {
      const id = "e2e-widget-" + ++next;
      const box = document.createElement("div");
      box.textContent = "Robot check (stub)";
      box.style.height = "65px";
      container.append(box);
      widgets.set(id, { box, options });
      pass(options);
      return id;
    },
    reset(id) { const widget = widgets.get(id); if (widget) pass(widget.options); },
    remove(id) { widgets.get(id)?.box.remove(); widgets.delete(id); },
  };
})();`;

export const TURNSTILE_URL = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

export async function stubTurnstile(page: Page): Promise<void> {
  await page.route(
    (url) => url.href === TURNSTILE_URL,
    (route) => route.fulfill({ contentType: "text/javascript", body: TURNSTILE_STUB }),
  );
}

/** Answers `METHOD /api<path>` with `status` and `body` (a function of the request's JSON body, or a value). */
export async function mockApi(
  page: Page,
  method: string,
  path: string,
  reply: { status?: number; body?: unknown } | ((body: unknown) => { status?: number; body?: unknown }),
): Promise<unknown[]> {
  const bodies: unknown[] = [];
  await page.route(`**/api${path}`, async (route) => {
    const request = route.request();
    if (request.method() !== method) return route.fallback();
    const sent = request.postData() ? request.postDataJSON() : undefined;
    bodies.push(sent);
    const { status = 200, body } = typeof reply === "function" ? reply(sent) : reply;
    if (body === undefined) await route.fulfill({ status });
    else await route.fulfill({ status, json: body });
  });
  return bodies;
}

