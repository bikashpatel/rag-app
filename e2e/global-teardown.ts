import { AdminApi } from "./support/admin-api";
import { ADMIN_SESSION_ENV } from "./support/env";

/** Deletes this run's test users (and their data) still there, also after failed or interrupted tests, and checks it. */
export default async function globalTeardown(): Promise<void> {
  if (!process.env[ADMIN_SESSION_ENV]) return;
  const admin = AdminApi.fromEnvironment();
  const removed = await admin.sweepRunUsers();
  const left = (await admin.runUsers()).length;
  console.log(`Test users of this run: ${removed} leftover removed by the teardown, ${left} left.`);
  if (left > 0) throw new Error(`${left} test users of this run are still there after the sweep.`);
}
