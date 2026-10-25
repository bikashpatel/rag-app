import type { AdminModel, AdminUser, AdminUserCreate, Me } from "../../frontend/src/api/types";
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
 * It only creates and deletes users named e2e-…, and reads users and models.
 */
export class AdminApi {
  private readonly cookie: string;

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
}

export function isTestUser(user: Pick<AdminUser, "username" | "is_admin">): boolean {
  return !user.is_admin && user.username.startsWith(TEST_USER_PREFIX);
}
