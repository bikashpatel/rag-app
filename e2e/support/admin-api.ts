import type {
  AdminModel,
  AdminPlan,
  AdminUser,
  AdminUserCreate,
  GrantRequest,
  Me,
  RevokeRequest,
  UserBilling,
} from "../../frontend/src/api/types";
import { ADMIN_SESSION_ENV, BASE_URL, RUN_ID_ENV, SESSION_COOKIE } from "./env";

/** Every user the suite creates starts with this; nothing else is ever changed or deleted. */
export const TEST_USER_PREFIX = "e2e-";

/** A test user older than this belongs to a run that was interrupted (no run takes this long), not to one in progress. */
const LEFTOVER_AGE_MS = 60 * 60 * 1000;

/** This run's users are named e2e-<run id>-…, so runs against the same stack can overlap. */
export function runUserPrefix(): string {
  const runId = process.env[RUN_ID_ENV];
  if (!runId) throw new Error("No run id: run the suite with `npx playwright test` (global-setup.ts picks one).");
  return `${TEST_USER_PREFIX}${runId}-`;
}

export class AdminApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

/**
 * The admin API, signed in with the admin's session cookie. It uses Node's fetch, not a Playwright request context,
 * so no trace, step or report ever records the admin's cookie (Playwright traces every request context a test can see).
 * It only creates, grants or revokes plans of, and deletes users named e2e-…, and reads users, models and plans.
 */
export class AdminApi {
  private readonly cookie: string;
  private plans: Promise<AdminPlan[]> | null = null;

  constructor(session: string) {
    this.cookie = `${SESSION_COOKIE}=${session}`;
  }

  /** The admin session global-setup.ts signed in (passed to the workers in the environment). */
  static fromEnvironment(): AdminApi {
    const session = process.env[ADMIN_SESSION_ENV];
    if (!session) throw new Error("No admin session: run the suite with `npx playwright test` (global-setup.ts signs in).");
    return new AdminApi(session);
  }

  private async call<T>(method: string, path: string, body?: unknown): Promise<T> {
    const res = await fetch(`${BASE_URL}/api${path}`, {
      method,
      headers: { Cookie: this.cookie, ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!res.ok) {
      const detail = await res.text().catch(() => "");
      throw new AdminApiError(res.status, `${method} /api${path} failed: HTTP ${res.status} ${detail.slice(0, 300)}`);
    }
    return (res.status === 204 ? undefined : await res.json()) as T;
  }

  me(): Promise<Me> {
    return this.call<Me>("GET", "/auth/me");
  }

  listUsers(): Promise<AdminUser[]> {
    return this.call<AdminUser[]>("GET", "/admin/users");
  }

  async getUser(id: number): Promise<AdminUser | undefined> {
    return (await this.listUsers()).find((user) => user.id === id);
  }

  createUser(input: AdminUserCreate): Promise<AdminUser> {
    if (!input.username.startsWith(TEST_USER_PREFIX)) throw new Error(`Test users must be named ${TEST_USER_PREFIX}…`);
    return this.call<AdminUser>("POST", "/admin/users", input);
  }

  /** Deletes a test user with all their data; already gone is fine. */
  async deleteUser(id: number): Promise<void> {
    const user = await this.getUser(id);
    if (!user) return;
    if (!isTestUser(user)) throw new Error(`Refusing to delete user ${id}: not a test user`);
    try {
      await this.call<void>("DELETE", `/admin/users/${id}`);
    } catch (err) {
      if (!(err instanceof AdminApiError && err.status === 404)) throw err;
    }
  }

  /** This run's test users still there. */
  async runUsers(): Promise<AdminUser[]> {
    const prefix = runUserPrefix();
    return (await this.listUsers()).filter((user) => isTestUser(user) && user.username.startsWith(prefix));
  }

  /** Deletes this run's test users still there (failed or interrupted tests); returns how many. */
  async sweepRunUsers(): Promise<number> {
    const leftovers = await this.runUsers();
    for (const user of leftovers) await this.deleteUser(user.id);
    return leftovers.length;
  }

  /** Deletes the test users of earlier, interrupted runs (older than an hour), never those of a run in progress. */
  async sweepLeftoverUsers(now = Date.now()): Promise<number> {
    const leftovers = (await this.listUsers()).filter(
      (user) => isTestUser(user) && now - Date.parse(user.created_at) > LEFTOVER_AGE_MS,
    );
    for (const user of leftovers) await this.deleteUser(user.id);
    return leftovers.length;
  }

  listModels(): Promise<AdminModel[]> {
    return this.call<AdminModel[]>("GET", "/admin/models");
  }

  /** Gives a test user a complimentary plan: dated (`days`) or open-ended (null), starting now. */
  async grantPlan(userId: number, code: string, days: number | null = null): Promise<UserBilling> {
    await this.requireTestUser(userId);
    const body: GrantRequest = { plan_id: await this.planId(code), days, reason: "End-to-end test", start: "now" };
    return this.call<UserBilling>("POST", `/admin/billing/users/${userId}/grants`, body);
  }

  /** Revokes a test user's current period: access ends now, and their plan reads as ended ("expired"). */
  async revokeCurrentPeriod(userId: number): Promise<UserBilling> {
    await this.requireTestUser(userId);
    const billing = await this.call<UserBilling>("GET", `/admin/billing/users/${userId}`);
    const period = billing.periods.find((item) => item.current);
    if (!period) throw new Error(`User ${userId} has no current period to revoke`);
    const body: RevokeRequest = { reason: "End-to-end test" };
    return this.call<UserBilling>("POST", `/admin/billing/periods/${period.id}/revoke`, body);
  }

  private async requireTestUser(userId: number): Promise<void> {
    const user = await this.getUser(userId);
    if (!user || !isTestUser(user)) throw new Error(`Refusing to change the plan of user ${userId}: not a test user`);
  }

  /** The id of the plan with this code (the seeded starter, standard or professional). */
  async planId(code: string): Promise<number> {
    this.plans ??= this.call<AdminPlan[]>("GET", "/admin/plans");
    const plan = (await this.plans).find((item) => item.code === code);
    if (!plan) throw new Error(`No plan "${code}": the seeded plans are starter, standard and professional`);
    return plan.id;
  }
}

export function isTestUser(user: Pick<AdminUser, "username" | "is_admin">): boolean {
  return !user.is_admin && user.username.startsWith(TEST_USER_PREFIX);
}
