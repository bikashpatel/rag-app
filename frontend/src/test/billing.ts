// Typed plans and billing payloads (src/api/types.ts) with the seeded plans' values (docs/briefs/plans-billing.md 3.2).

import type {
  AdminOrder,
  AdminPayment,
  AdminPlan,
  AttentionItem,
  BillingAdminConfig,
  BillingStatus,
  BillingSummary,
  CheckoutOption,
  OrderView,
  PaymentsInfo,
  PaymentView,
  PeriodView,
  PlanCatalog,
  PlanModel,
  PlanOffer,
  PlanRef,
  PublicPlan,
  PublicSite,
  RefundView,
  UserBilling,
} from "../api/types";
import { makePaidPlanAccess } from "./fixtures";

export const STARTER: PlanRef = { id: 1, code: "starter", name: "Starter" };
export const STANDARD: PlanRef = { id: 2, code: "standard", name: "Standard" };
export const PROFESSIONAL: PlanRef = { id: 3, code: "professional", name: "Professional" };

const GMAIL = { id: "gmail", name: "Gmail", available: true };

export function makePlanModel(overrides: Partial<PlanModel> = {}): PlanModel {
  return { id: 5, display_name: "Claude Sonnet 5.5", allowance_rate: 1, answers: 260, ...overrides };
}

const model = (id: number, display_name: string, allowance_rate: number) => (answers: number) =>
  makePlanModel({ id, display_name, allowance_rate, answers });
const opus = model(1, "Claude Opus 5.5", 2);
const gpt = model(2, "GPT-5.5", 2.7);
const mini = model(3, "GPT-5.4 mini", 0.41);
const haiku = model(4, "Claude Haiku 5.5", 0.05);
const sonnet = model(5, "Claude Sonnet 5.5", 1);

/** A `period` option of Standard starting now (NOW is 20 Oct 2026, 09:30 UTC). */
export function makeCheckoutOption(overrides: Partial<CheckoutOption> = {}): CheckoutOption {
  return {
    kind: "period",
    label: "Choose Standard",
    starts_at: "2026-10-20T09:30:00Z",
    ends_at: "2026-11-19T18:30:00Z",
    valid_through: "2026-11-19",
    amount_paise: 200_000,
    plan_price_paise: 200_000,
    credit: null,
    changes: [],
    notes: [],
    unavailable_reason: null,
    ...overrides,
  };
}

/** Standard, the recommended plan. */
export function makePublicPlan(overrides: Partial<PublicPlan> = {}): PublicPlan {
  return {
    code: STANDARD.code,
    name: STANDARD.name,
    description: "Adds files from chat and Claude Sonnet for regular work.",
    price_paise: 200_000,
    recommended: true,
    features: { files: true, images: false, plugins: [] },
    plugins: [GMAIL],
    limits: {
      monthly_token_limit: 2_000_000,
      rate_limit_per_min: 20,
      max_upload_mb: 25,
      max_documents: 200,
      max_storage_mb: 1_000,
      monthly_image_limit: 30,
      vision_monthly_token_limit: 500_000,
    },
    chat_models: [haiku(5_200), mini(640), sonnet(260)],
    all_chat_models: false,
    default_chat_model: sonnet(260),
    answers_estimate: 260,
    ...overrides,
  };
}

export function makePlanOffer(overrides: Partial<PlanOffer> = {}): PlanOffer {
  return {
    ...makePublicPlan(),
    id: STANDARD.id,
    status: "active",
    current: false,
    options: [makeCheckoutOption()],
    ...overrides,
  };
}

/** The three seeded plans as a user without a plan sees them. */
export function makeSeededOffers(): PlanOffer[] {
  return [
    makePlanOffer({
      id: STARTER.id,
      code: STARTER.code,
      name: STARTER.name,
      description: "Ask questions across your documents, for everyday use.",
      price_paise: 50_000,
      recommended: false,
      features: { files: false, images: false, plugins: [] },
      limits: {
        monthly_token_limit: 500_000,
        rate_limit_per_min: 10,
        max_upload_mb: 10,
        max_documents: 50,
        max_storage_mb: 200,
        monthly_image_limit: 30,
        vision_monthly_token_limit: 150_000,
      },
      chat_models: [haiku(1_600), mini(200)],
      default_chat_model: haiku(1_600),
      answers_estimate: 1_600,
      options: [makeCheckoutOption({ label: "Choose Starter", amount_paise: 50_000, plan_price_paise: 50_000 })],
    }),
    makePlanOffer(),
    makePlanOffer({
      id: PROFESSIONAL.id,
      code: PROFESSIONAL.code,
      name: PROFESSIONAL.name,
      description: "Everything: files, images and Gmail, with every model.",
      price_paise: 500_000,
      recommended: false,
      features: { files: true, images: true, plugins: ["gmail"] },
      limits: {
        monthly_token_limit: 5_000_000,
        rate_limit_per_min: 30,
        max_upload_mb: 50,
        max_documents: 500,
        max_storage_mb: 3_000,
        monthly_image_limit: 60,
        vision_monthly_token_limit: 1_000_000,
      },
      chat_models: [opus(290), haiku(11_000), sonnet(580), mini(1_400), gpt(210)],
      all_chat_models: true,
      default_chat_model: opus(290),
      answers_estimate: 290,
      options: [makeCheckoutOption({ label: "Choose Professional", amount_paise: 500_000, plan_price_paise: 500_000 })],
    }),
  ];
}

export function makePaymentsInfo(overrides: Partial<PaymentsInfo> = {}): PaymentsInfo {
  return {
    available: true,
    mode: "live",
    contact_email: "billing@example.com",
    upi_limit_paise: 200_000,
    email_reminders: false,
    ...overrides,
  };
}

export function makeCatalog(overrides: Partial<PlanCatalog> = {}): PlanCatalog {
  return { plans: makeSeededOffers(), payments: makePaymentsInfo(), ...overrides };
}

/** A pending Standard checkout created two minutes before NOW. */
export function makeOrder(overrides: Partial<OrderView> = {}): OrderView {
  return {
    public_id: "q3Zx8LmP0aB2cD4eF6gH",
    kind: "period",
    plan: STANDARD,
    amount_paise: 200_000,
    credit_paise: 0,
    status: "pending",
    created_at: "2026-10-20T09:28:00Z",
    expires_at: "2026-10-20T09:48:00Z",
    paid_at: null,
    pay_url: "https://rzp.io/rzp/q3Zx8Lm",
    last_payment_error: null,
    period_start: null,
    period_end: null,
    valid_through: null,
    receipt: null,
    method: null,
    test_mode: false,
    ...overrides,
  };
}

/** The same order once paid by UPI. */
export function makePaidOrder(overrides: Partial<OrderView> = {}): OrderView {
  return makeOrder({
    status: "paid",
    paid_at: "2026-10-20T09:30:00Z",
    pay_url: null,
    period_start: "2026-10-20T09:30:00Z",
    period_end: "2026-11-19T18:30:00Z",
    valid_through: "2026-11-19",
    receipt: { id: 41, number: "RA/26-27/000041" },
    method: "upi",
    ...overrides,
  });
}

export function makePaymentView(overrides: Partial<PaymentView> = {}): PaymentView {
  return {
    id: 57,
    paid_at: "2026-10-08T05:12:00Z",
    plan: STANDARD,
    kind: "period",
    description: "Standard plan, 30 days: 8 Oct to 7 Nov 2026",
    amount_paise: 200_000,
    method: "upi",
    status: "captured",
    refunded_paise: 0,
    period_start: "2026-10-08T05:12:00Z",
    period_end: "2026-11-07T18:30:00Z",
    receipt: { id: 33, number: "RA/26-27/000033" },
    test_mode: false,
    ...overrides,
  };
}

export function makePeriodView(overrides: Partial<PeriodView> = {}): PeriodView {
  return {
    id: 12,
    plan: STANDARD,
    plan_name: STANDARD.name,
    starts_at: "2026-10-08T05:12:00Z",
    ends_at: "2026-11-07T18:30:00Z",
    valid_through: "2026-11-07",
    source: "payment",
    current: true,
    test_mode: false,
    reason: null,
    ...overrides,
  };
}

/** A user on a paid Standard period with nothing scheduled after it. */
export function makeBillingStatus(overrides: Partial<BillingStatus> = {}): BillingStatus {
  return {
    plan: makePaidPlanAccess(),
    periods: [makePeriodView()],
    complimentary: null,
    open_order: null,
    profile: { name: null, email: null },
    payments: makePaymentsInfo(),
    ...overrides,
  };
}

export function makePublicSite(overrides: Partial<PublicSite> = {}): PublicSite {
  return {
    business_name: "RAG Assistant",
    contact_email: "billing@example.com",
    contact_phone: null,
    address: null,
    ...overrides,
  };
}

// ---------------- Admin ----------------

export function makeAdminPlan(overrides: Partial<AdminPlan> = {}): AdminPlan {
  const plan = makePublicPlan();
  return {
    id: STANDARD.id,
    code: plan.code,
    name: plan.name,
    description: plan.description,
    price_paise: plan.price_paise,
    rank: 20,
    status: "active",
    recommended: true,
    features: plan.features,
    limits: plan.limits,
    chat_model_ids: [4, 3, 5],
    default_chat_model_id: 5,
    current_users: 4,
    scheduled_periods: 1,
    complimentary_users: 0,
    used: true,
    // ₹2,000 less 2.36%; the worst case is $7.57 of AI at ₹88 = $1 (3.3)
    cost: {
      kept_paise: 195_280,
      worst_case_paise: 66_600,
      worst_share: 0.34,
      typical_paise: 26_700,
      typical_share: 0.14,
      unlimited: [],
    },
    created_at: "2026-10-26T06:00:00Z",
    updated_at: "2026-10-26T06:00:00Z",
    ...overrides,
  };
}

export function makeBillingAdminConfig(overrides: Partial<BillingAdminConfig> = {}): BillingAdminConfig {
  return {
    settings: {
      business_name: "RAG Assistant",
      address: "",
      contact_email: "billing@example.com",
      contact_phone: null,
      receipt_prefix: "RA",
      upi_limit_paise: 200_000,
    },
    payments: {
      configured: true,
      mode: "live",
      key_id_hint: "rzp_live_…AbCd",
      webhook_secret_set: true,
      previous_webhook_secret_set: false,
      last_webhook_at: "2026-10-20T09:30:02Z",
      webhook_url: "https://rag.example.com/api/billing/razorpay/webhook",
      webhook_events: ["payment_link.paid", "payment.captured", "refund.processed", "payment.dispute.created"],
      test_links_created: 0,
      reconciler_last_run_at: "2026-10-20T09:29:45Z",
      last_error: null,
      paused_orders: 0,
      testers: 1,
      email_reminders: false,
    },
    estimate: { usd_inr: 88, standard_usd_per_mtok: 3.2, vision_usd_per_mtok: 2.34, image_usd: 0.042, fee_bp: 236 },
    ...overrides,
  };
}

export function makeAdminOrder(overrides: Partial<AdminOrder> = {}): AdminOrder {
  return {
    ...makeOrder(),
    id: 71,
    user: { id: 7, username: "maya.chen" },
    mode: "live",
    razorpay_link_id: "plink_Q3Zx8LmP0aB2cD",
    razorpay_order_id: null,
    review_reason: null,
    review_note: null,
    checks: 3,
    last_checked_at: "2026-10-20T09:29:30Z",
    next_check_at: "2026-10-20T09:30:00Z",
    last_error: null,
    paused: false,
    ...overrides,
  };
}

export function makeRefund(overrides: Partial<RefundView> = {}): RefundView {
  return {
    id: 5,
    amount_paise: 50_000,
    status: "processed",
    initiated_by: "admin",
    admin_username: "admin",
    reason: "Asked for a partial refund",
    end_access: false,
    created_at: "2026-10-12T10:00:00Z",
    processed_at: "2026-10-12T10:05:00Z",
    last_error: null,
    ...overrides,
  };
}

export function makeAdminPayment(overrides: Partial<AdminPayment> = {}): AdminPayment {
  return {
    ...makePaymentView(),
    user: { id: 7, username: "maya.chen" },
    order_public_id: "k8Yw2NnQ5rS7tU9vW1xY",
    razorpay_payment_id: "pay_Q3Zx8LmP0aB2cD",
    fee_paise: 4_000,
    fee_tax_paise: 720,
    refundable_paise: 200_000,
    credit_used_paise: 0,
    refunds: [],
    disputes: [],
    offline_reference: null,
    mode: "live",
    ...overrides,
  };
}

export function makeAttentionItem(overrides: Partial<AttentionItem> = {}): AttentionItem {
  return {
    kind: "review",
    message: "A payment needs review",
    detail: "Amount differs: paid ₹1,500, expected ₹2,000",
    order_public_id: "k8Yw2NnQ5rS7tU9vW1xY",
    payment_id: 57,
    count: null,
    ...overrides,
  };
}

export function makeBillingSummary(overrides: Partial<BillingSummary> = {}): BillingSummary {
  return {
    month: "2026-10",
    gross_paise: 950_000,
    fee_paise: 22_420,
    refunded_paise: 50_000,
    disputed_paise: 0,
    net_paise: 877_580,
    payments: 5,
    refunds: 1,
    by_plan: [
      { plan: "Starter", payments: 1, gross_paise: 50_000 },
      { plan: "Standard", payments: 2, gross_paise: 400_000 },
      { plan: "Professional", payments: 1, gross_paise: 500_000 },
    ],
    current_users: [
      { plan: "Starter", paid: 1, complimentary: 0 },
      { plan: "Standard", paid: 2, complimentary: 0 },
      { plan: "Professional", paid: 1, complimentary: 1 },
    ],
    users_without_plan: 2,
    months: [
      { month: "2026-09", gross_paise: 0, net_paise: 0 },
      { month: "2026-10", gross_paise: 950_000, net_paise: 877_580 },
    ],
    expiring: [
      { user: { id: 7, username: "maya.chen" }, plan: "Standard", date: "2026-10-24", last_payment_at: "2026-09-25T08:00:00Z" },
    ],
    lapsed: [
      { user: { id: 9, username: "sam.ortiz" }, plan: "Starter", date: "2026-10-11", last_payment_at: "2026-09-11T12:00:00Z" },
    ],
    attention: [],
    ...overrides,
  };
}

export function makeUserBilling(overrides: Partial<UserBilling> = {}): UserBilling {
  return {
    plan: makePaidPlanAccess(),
    periods: [makePeriodView()],
    complimentary: null,
    orders: [makeAdminOrder({ status: "paid", pay_url: null, paid_at: "2026-10-08T05:12:00Z" })],
    payments: [makeAdminPayment()],
    profile: { name: "Maya Chen", email: null },
    events: [
      {
        at: "2026-10-08T05:12:00Z",
        source: "callback",
        type: "payment.captured",
        actor: null,
        detail: { amount_paise: 200_000 },
      },
    ],
    ...overrides,
  };
}
