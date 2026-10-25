import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

/** The app under test: the Docker stack's nginx (Compose `APP_PORT`, 8080 by default). */
export const BASE_URL = (process.env.E2E_BASE_URL ?? "http://localhost:8080").replace(/\/+$/, "");

/** Set by global-setup.ts for the workers: the admin's session cookie value. Internal, never set it by hand. */
export const ADMIN_SESSION_ENV = "E2E_ADMIN_SESSION";

/** Set by global-setup.ts for the workers: this run's id, part of every test user's name. Internal as well. */
export const RUN_ID_ENV = "E2E_RUN_ID";

/** The session cookie the backend sets (`COOKIE_NAME`, backend/app/config.py). */
export const SESSION_COOKIE = "rag_session";

export const LIVE = process.env.E2E_LIVE === "1";

const REPO_ENV_FILE = path.resolve(__dirname, "..", "..", ".env");

/**
 * Compose expands `$` in unquoted and double-quoted values: `$$` is a literal `$`. Any other `$` (`${NAME}`, `$NAME`)
 * would need the variable, which this reader doesn't resolve, so it refuses rather than sign in with the wrong password
 * (failed sign-ins count toward the admin's lockout). The error names the key, never the value.
 */
function unescapeDollars(key: string, value: string): string {
  return value.replace(/\$(\$)?/g, (_, escaped?: string) => {
    if (escaped) return "$";
    throw new Error(`${key} in the repo's .env uses $ expansion, which the e2e suite doesn't resolve: set E2E_${key} instead.`);
  });
}

/**
 * One value of a dotenv line, read the way Compose reads it: single quotes keep the value literal; double quotes are
 * unescaped; an unquoted value's ` # comment` is dropped; `$$` becomes `$` outside single quotes.
 */
function parseValue(key: string, raw: string): string {
  const value = raw.trim();
  const quote = value[0];
  if (quote === "'") {
    const end = value.indexOf(quote, 1);
    return end === -1 ? value.slice(1) : value.slice(1, end);
  }
  if (quote === '"') {
    const inner = /^"((?:[^"\\]|\\.)*)"?/.exec(value)?.[1] ?? "";
    const unescaped = inner.replace(/\\(["\\nrt])/g, (_, c: string) => ({ n: "\n", r: "\r", t: "\t" })[c] ?? c);
    return unescapeDollars(key, unescaped);
  }
  return unescapeDollars(key, value.replace(/\s+#.*$/, "").trim());
}

/** Reads only `keys` from the repo's .env (nothing else is kept in memory). */
function readRepoEnv(keys: readonly string[]): Map<string, string> {
  const found = new Map<string, string>();
  if (!existsSync(REPO_ENV_FILE)) return found;
  for (const line of readFileSync(REPO_ENV_FILE, "utf8").split(/\r?\n/)) {
    const match = /^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$/.exec(line);
    if (match && keys.includes(match[1])) found.set(match[1], parseValue(match[1], match[2]));
  }
  return found;
}

/**
 * The env admin's credentials: E2E_ADMIN_USERNAME / E2E_ADMIN_PASSWORD, else ADMIN_USERNAME / ADMIN_PASSWORD from the
 * repo's .env (local runs). Never log or print them.
 */
export function adminCredentials(): { username: string; password: string } {
  let username = process.env.E2E_ADMIN_USERNAME;
  let password = process.env.E2E_ADMIN_PASSWORD;
  const missing = [username ? "" : "ADMIN_USERNAME", password ? "" : "ADMIN_PASSWORD"].filter(Boolean);
  if (missing.length > 0) {
    const fromFile = readRepoEnv(missing);
    username ||= fromFile.get("ADMIN_USERNAME");
    password ||= fromFile.get("ADMIN_PASSWORD");
  }
  if (!username || !password) {
    throw new Error("Set E2E_ADMIN_USERNAME and E2E_ADMIN_PASSWORD (or ADMIN_USERNAME and ADMIN_PASSWORD in the repo's .env).");
  }
  return { username, password };
}
