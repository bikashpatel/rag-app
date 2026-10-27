import { randomBytes } from "node:crypto";
import { test as base, expect, type BrowserContext, type BrowserContextOptions, type Request, type Route } from "@playwright/test";
import type { AdminUserCreate } from "../../frontend/src/api/types";
import { AdminApi, TEST_USER_PREFIX, isTestUser, runUserPrefix } from "./admin-api";
import { BASE_URL } from "./env";
import { CHAT_STREAM, SseStub } from "./sse";

export { expect };

export type TestUser = { id: number; username: string; password: string };
export type UserOptions = Omit<AdminUserCreate, "username" | "password" | "grant"> & {
  /** The plan's code for an open-ended complimentary plan (the existing specs need access); null: no plan. */
  plan?: string | null;
};

/** Every test user has a plan unless the test asks for none, so the chat opens instead of the plans. */
const DEFAULT_PLAN = "professional";

/** A new name in this run's namespace: e2e-<run id>-<random>. */
export function randomUsername(): string {
  return `${runUserPrefix()}${randomBytes(5).toString("hex")}`;
}

export function randomPassword(): string {
  return `Pw-${randomBytes(12).toString("base64url")}`;
}

/**
 * Blocks, and records, every request a test must never make: any host other than the app (Google above all), a chat
 * request no canned answer handled (it would call a model), uploads (they call the embedding model), plugin connects
 * (OAuth) and the admin's model, request-check and re-index calls. A test's own page.route() handlers run first.
 */
async function installGuards(context: BrowserContext, allowModelCalls: boolean, violations: string[]): Promise<void> {
  const app = new URL(BASE_URL);
  // Uncaught page errors and CSP violations fail the test too.
  context.on("weberror", (error) => violations.push(`page error: ${error.error().message}`));
  context.on("console", (message) => {
    if (/Content Security Policy|Refused to (load|connect|execute|apply)/i.test(message.text())) {
      violations.push(`CSP violation: ${message.text()}`);
    }
  });
  const block = (what: string) => async (route: Route) => {
    violations.push(`${what}: ${route.request().method()} ${route.request().url()}`);
    await route.abort("blockedbyclient");
  };
  await context.route((url) => url.host !== app.host, block("request outside the app"));
  if (!allowModelCalls) await context.route(CHAT_STREAM, block("chat request without a canned answer"));
  await context.route("**/api/plugins/*/connect", block("plugin connect (OAuth)"));
  await context.route("**/api/documents", (route) =>
    route.request().method() === "POST" ? block("upload (embedding model)")(route) : route.fallback(),
  );
  await context.route(/\/api\/documents\/\d+\/reprocess$/, block("re-index (embedding model)"));
  await context.route(/\/api\/admin\/(models\/\d+\/test|plugins\/request-check\/test|reindex)$/, block("admin model call"));
}

/** The only writes the admin's browser may send: creating an e2e-… user, and changing or deleting one. */
async function isTestUserWrite(request: Request, admin: AdminApi): Promise<boolean> {
  const { pathname } = new URL(request.url());
  if (request.method() === "POST" && pathname === "/api/admin/users") {
    const username = (request.postDataJSON() as { username?: unknown } | null)?.username;
    return typeof username === "string" && username.startsWith(TEST_USER_PREFIX);
  }
  const id = /^\/api\/admin\/users\/(\d+)$/.exec(pathname)?.[1];
  if (id === undefined || !["PATCH", "DELETE"].includes(request.method())) return false;
  const user = await admin.getUser(Number(id));
  return user !== undefined && isTestUser(user);
}

/**
 * For a context signed in as the admin: blocks, and records, every write to the app other than to e2e-… users. Models,
 * shared documents, plugin settings, other users and the admin's own preferences, password and sessions (Sign out
 * would end the session every worker shares) stay as they are, whatever a test clicks. Install it after installGuards,
 * so it runs first.
 */
export async function guardAdminContext(context: BrowserContext, admin: AdminApi, violations: string[]): Promise<void> {
  const app = new URL(BASE_URL);
  await context.route(
    (url) => url.host === app.host && url.pathname.startsWith("/api/"),
    async (route) => {
      const request = route.request();
      if (["GET", "HEAD"].includes(request.method()) || (await isTestUserWrite(request, admin))) return route.fallback();
      violations.push(`admin write outside the test users: ${request.method()} ${request.url()}`);
      await route.abort("blockedbyclient");
    },
  );
}

type TestFixtures = {
  /** Lets chat requests through to the backend (the @live test only). */
  allowModelCalls: boolean;
  /** Overrides, models and plan for the test's `user` (an open-ended Professional plan otherwise). */
  userOptions: UserOptions;
  /** Requests the guards blocked, page errors and CSP violations in this test; it fails when there are any. */
  violations: string[];
  /** Creates an e2e-… user (deleted with all their data after the test). */
  makeUser: (options?: UserOptions) => Promise<TestUser>;
  /** A fresh e2e-… user for this test. */
  user: TestUser;
  /** Deletes the e2e-… user with this name after the test (users created through the admin UI). */
  deleteAfter: (username: string) => void;
  /** Another browser context (another browser, as far as the app can tell), guarded like the default one. */
  newContext: (options?: BrowserContextOptions) => Promise<BrowserContext>;
  /** A local SSE server for answers that stream piece by piece. */
  sseStub: SseStub;
};

type WorkerFixtures = { admin: AdminApi };

export const test = base.extend<TestFixtures, WorkerFixtures>({
  admin: [async ({}, use) => use(AdminApi.fromEnvironment()), { scope: "worker" }],

  allowModelCalls: [false, { option: true }],
  userOptions: [{}, { option: true }],

  violations: async ({}, use) => {
    const violations: string[] = [];
    await use(violations);
    expect(violations, "blocked requests, page errors and CSP violations").toEqual([]);
  },

  context: async ({ context, allowModelCalls, violations }, use) => {
    await installGuards(context, allowModelCalls, violations);
    await use(context);
  },

  newContext: async ({ browser, allowModelCalls, violations }, use) => {
    const contexts: BrowserContext[] = [];
    await use(async (options) => {
      const context = await browser.newContext(options);
      contexts.push(context);
      await installGuards(context, allowModelCalls, violations);
      return context;
    });
    for (const context of contexts) await context.close();
  },

  makeUser: async ({ admin }, use) => {
    const created: number[] = [];
    await use(async ({ plan = DEFAULT_PLAN, ...options } = {}) => {
      const password = randomPassword();
      const grant = plan === null ? undefined : { plan_id: await admin.planId(plan), days: null, reason: "E2E test" };
      const user = await admin.createUser({ ...options, grant, username: randomUsername(), password });
      created.push(user.id);
      return { id: user.id, username: user.username, password };
    });
    for (const id of created) await admin.deleteUser(id);
  },

  user: async ({ makeUser, userOptions }, use) => use(await makeUser(userOptions)),

  deleteAfter: async ({ admin }, use) => {
    const names: string[] = [];
    await use((username) => names.push(username));
    if (names.length === 0) return;
    for (const user of await admin.listUsers()) {
      if (names.includes(user.username)) await admin.deleteUser(user.id);
    }
  },

  sseStub: async ({}, use) => {
    const stub = await SseStub.start();
    await use(stub);
    await stub.close();
  },
});

/** Signs `user` in for the whole context through the API (the cookie lands in the context). */
export async function signIn(context: BrowserContext, user: Pick<TestUser, "username" | "password">): Promise<void> {
  const res = await context.request.post("/api/auth/login", { data: { username: user.username, password: user.password } });
  expect(res.status(), `sign-in of ${user.username}`).toBe(200);
}
