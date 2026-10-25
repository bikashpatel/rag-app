import { randomBytes } from "node:crypto";
import { request } from "@playwright/test";
import { AdminApi } from "./support/admin-api";
import { ADMIN_SESSION_ENV, BASE_URL, RUN_ID_ENV, SESSION_COOKIE, adminCredentials } from "./support/env";

const HEALTH_TIMEOUT_MS = Number(process.env.E2E_WAIT_SECONDS ?? "120") * 1000;

async function waitForStack(): Promise<void> {
  const deadline = Date.now() + HEALTH_TIMEOUT_MS;
  let last = "no answer";
  while (Date.now() < deadline) {
    try {
      const [health, page] = await Promise.all([fetch(`${BASE_URL}/api/health`), fetch(`${BASE_URL}/login`)]);
      if (health.ok && page.ok) return;
      last = `health ${health.status}, page ${page.status}`;
    } catch (err) {
      last = (err as Error).message;
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error(`The app at ${BASE_URL} isn't up (${last}). Start it with \`docker compose up -d\`.`);
}

/**
 * Signs the admin in once per run through the API (never the UI, so nothing records the password being typed) and
 * hands the session and a run id to the workers in the environment; then removes test users an interrupted run left
 * behind (only old ones: another run may be using its users right now).
 */
export default async function globalSetup(): Promise<void> {
  await waitForStack();
  process.env[RUN_ID_ENV] = randomBytes(3).toString("hex");
  const { username, password } = adminCredentials();
  const context = await request.newContext({ baseURL: BASE_URL });
  try {
    const res = await context.post("/api/auth/login", { data: { username, password } });
    if (!res.ok()) throw new Error(`The admin sign-in failed (HTTP ${res.status()}). Check the admin credentials.`);
    const session = (await context.storageState()).cookies.find((cookie) => cookie.name === SESSION_COOKIE)?.value;
    if (!session) throw new Error("The admin sign-in set no session cookie.");
    process.env[ADMIN_SESSION_ENV] = session;
  } finally {
    await context.dispose();
  }
  const admin = AdminApi.fromEnvironment();
  if (!(await admin.me()).is_admin) throw new Error("The configured admin account isn't an admin.");
  await admin.sweepLeftoverUsers();
}
