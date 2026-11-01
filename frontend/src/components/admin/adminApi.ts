import { api, ApiError } from "../../api/client";
import type {
  Accepted,
  AdminOrder,
  AdminOrderDetail,
  AdminPayment,
  BillingAdminConfig,
  BillingSettings,
  ChangePlanRequest,
  EndComplimentaryRequest,
  GrantRequest,
  OfflinePaymentRequest,
  OrderChannel,
  OrderStatus,
  Page,
  PaymentStatus,
  PlanCreate,
  PlanSaved,
  PlanUpdate,
  MailTest,
  ReconcileResult,
  RefundRequest,
  ResolveRequest,
  RevokeRequest,
  SignupSettings,
  SignupStatus,
  UpiConfirmRequest,
  UpiRejectRequest,
  UserBilling,
} from "../../api/types";

export function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

export function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

/** DELETE that treats 404 as success: the resource being gone is the outcome the admin asked for. */
export async function deleteResource(path: string): Promise<void> {
  try {
    await api.del(path);
  } catch (err) {
    if (!(err instanceof ApiError && err.status === 404)) throw err;
  }
}

/** POST /admin/reindex; returns how many documents were queued. */
export async function reindexDocuments(onlyStale: boolean): Promise<number> {
  const { queued } = await api.post<{ queued: number }>("/admin/reindex", { only_stale: onlyStale });
  return queued;
}

export function queuedMessage(queued: number): string {
  if (queued === 0) return "Nothing needed re-indexing.";
  return `Queued ${queued} document${queued === 1 ? "" : "s"} for re-indexing.`;
}

// ---------------- Sign-up (SPEC section 6, "Sign-up") ----------------

export const SIGNUP_SETTINGS_PATH = "/admin/signup-settings";
export const saveSignupSettings = (body: SignupSettings) => api.put<SignupStatus>(SIGNUP_SETTINGS_PATH, body);
/** Queues a `test` mail to that address (503 without a mailer, 5 an hour). */
export const sendTestEmail = (body: MailTest) => api.post<Accepted>("/admin/mail/test", body);

// ---------------- Plans and billing (SPEC section 6, "Plans and billing") ----------------

/** A query string of the set values: "?month=2026-10&page=2", or "". */
function query(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const createPlan = (body: PlanCreate) => api.post<PlanSaved>("/admin/plans", body);
export const updatePlan = (id: number, body: PlanUpdate) => api.patch<PlanSaved>(`/admin/plans/${id}`, body);

export const BILLING_CONFIG_PATH = "/admin/billing/config";
export const saveBillingSettings = (body: BillingSettings) =>
  api.put<BillingAdminConfig>("/admin/billing/settings", body);
export const billingSummaryPath = (month: string) => `/admin/billing/summary${query({ month })}`;
/** Same-origin link to the month's payments as CSV; the server sends it as an attachment. */
export const billingExportUrl = (month: string) => `/api/admin/billing/export.csv${query({ month })}`;
/** Same-origin link to any user's receipt PDF. */
export const adminReceiptUrl = (id: number) => `/api/admin/billing/receipts/${id}`;
export const reconcileNow = () => api.post<ReconcileResult>("/admin/billing/reconcile");

/** Billing lists come 50 to a page, newest first. */
export const PAGE_SIZE = 50;

/** month null: every month. */
export type PaymentFilter = { month: string | null; userId: number | null; status: PaymentStatus | null; page: number };
export const paymentsPath = ({ month, userId, status, page }: PaymentFilter) =>
  `/admin/billing/payments${query({ month, user_id: userId, status, page })}`;
export const getPayment = (id: number) => api.get<AdminPayment>(`/admin/billing/payments/${id}`);
export const refundPayment = (id: number, body: RefundRequest) =>
  api.post<AdminPayment>(`/admin/billing/payments/${id}/refund`, body);
export const recordOfflinePayment = (body: OfflinePaymentRequest) =>
  api.post<AdminPayment>("/admin/billing/offline-payments", body);

/** `status=submitted`: the direct-UPI UTRs to check, the oldest first. */
export const ordersPath = (status: OrderStatus | null, page: number, channel: OrderChannel | null = null) =>
  `/admin/billing/orders${query({ status, channel, page })}`;
const orderPath = (publicId: string) => `/admin/billing/orders/${encodeURIComponent(publicId)}`;
export const getOrder = (publicId: string, signal?: AbortSignal) =>
  api.get<AdminOrderDetail>(orderPath(publicId), { signal });
export const checkOrder = (publicId: string) => api.post<AdminOrder>(`${orderPath(publicId)}/check`);
export const resolveOrder = (publicId: string, body: ResolveRequest) =>
  api.post<AdminOrder>(`${orderPath(publicId)}/resolve`, body);
export const listOrders = (status: OrderStatus | null, page: number) =>
  api.get<Page<AdminOrder>>(ordersPath(status, page));
/** Records the direct-UPI payment and applies it once; `utr` must be the order's (409 utr_mismatch). */
export const confirmUpi = (publicId: string, body: UpiConfirmRequest) =>
  api.post<AdminOrder>(`${orderPath(publicId)}/upi-confirm`, body);
export const rejectUpi = (publicId: string, body: UpiRejectRequest) =>
  api.post<AdminOrder>(`${orderPath(publicId)}/upi-reject`, body);
/** The ₹1 test QR of the saved UPI ID and payee name; `v` changes with them, so a new save shows a new image. */
export const upiPreviewUrl = (upiId: string, payeeName: string) =>
  `/api/admin/billing/upi-preview.svg${query({ v: `${upiId} ${payeeName}` })}`;

export const userBillingPath = (userId: number) => `/admin/billing/users/${userId}`;
export const grantPlan = (userId: number, body: GrantRequest) =>
  api.post<UserBilling>(`${userBillingPath(userId)}/grants`, body);
export const endComplimentary = (userId: number, body: EndComplimentaryRequest) =>
  api.post<UserBilling>(`${userBillingPath(userId)}/end-complimentary`, body);
export const changePlan = (userId: number, body: ChangePlanRequest) =>
  api.post<UserBilling>(`${userBillingPath(userId)}/change-plan`, body);
export const revokePeriod = (periodId: number, body: RevokeRequest) =>
  api.post<UserBilling>(`/admin/billing/periods/${periodId}/revoke`, body);
