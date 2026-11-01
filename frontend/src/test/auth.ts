// Typed sign-up, sign-in and account-email payloads (src/api/types.ts; docs/briefs/signup-auth.md section 11).

import type {
  AuthConfig,
  EmailTokenInfo,
  GoogleLink,
  GooglePending,
  PasswordTokenInfo,
  PendingEmailInfo,
  SignupCheck,
  UndoInfo,
} from "../api/types";

/** Cloudflare's always-passing test sitekey (docs/briefs/signup-auth.md 7.5). */
export const TURNSTILE_TEST_SITE_KEY = "1x00000000000000000000AA";

/** Everything configured and sign-up open with both methods. */
export function makeAuthConfig(overrides: Partial<AuthConfig> = {}): AuthConfig {
  return {
    signup: { open: true, email: true, google: true },
    google_signin: true,
    password_reset: true,
    turnstile_site_key: TURNSTILE_TEST_SITE_KEY,
    password_min_length: 8,
    password_max_length: 256,
    terms_version: "2026-11-01",
    contact_email: "support@asknuvexusdoc.com",
    ...overrides,
  };
}

/** As the app ships: no mailer, Turnstile or Google sign-in, sign-up closed (sign-in by username only). */
export function makeClosedAuthConfig(overrides: Partial<AuthConfig> = {}): AuthConfig {
  return makeAuthConfig({
    signup: { open: false, email: false, google: false },
    google_signin: false,
    password_reset: false,
    turnstile_site_key: null,
    contact_email: null,
    ...overrides,
  });
}

/** A sign-up link opened in the browser that started it (no password needed). */
export function makeSignupCheck(overrides: Partial<SignupCheck> = {}): SignupCheck {
  return { email: "maya@example.com", expires_at: "2026-10-21T09:30:00Z", needs_password: false, next: null, ...overrides };
}

export function makePasswordTokenInfo(overrides: Partial<PasswordTokenInfo> = {}): PasswordTokenInfo {
  return {
    email: "maya@example.com",
    username: "user-k3m9q2xa",
    has_password: true,
    expires_at: "2026-10-20T10:00:00Z",
    google: null,
    ...overrides,
  };
}

/** A confirmation link opened in the session of the account that asked for it. */
export function makeEmailTokenInfo(overrides: Partial<EmailTokenInfo> = {}): EmailTokenInfo {
  return { email: "maya.chen@example.org", account: "m***@example.com", signed_in: true, ...overrides };
}

export function makeUndoInfo(overrides: Partial<UndoInfo> = {}): UndoInfo {
  return { old_email: "maya@example.com", new_email: "m***@example.org", ...overrides };
}

/** A new Gmail account waiting on /signup/google, from the sign-up page. */
export function makeGooglePending(overrides: Partial<GooglePending> = {}): GooglePending {
  return {
    email: "maya.chen@gmail.com",
    email_verified: true,
    from: "signup",
    expires_at: "2026-10-20T09:45:00Z",
    ...overrides,
  };
}

export function makePendingEmailInfo(overrides: Partial<PendingEmailInfo> = {}): PendingEmailInfo {
  return {
    address: "maya.chen@example.org",
    source: "user",
    sent_at: "2026-10-20T09:00:00Z",
    expires_at: "2026-10-21T09:00:00Z",
    ...overrides,
  };
}

export function makeGoogleLink(overrides: Partial<GoogleLink> = {}): GoogleLink {
  return {
    email: "maya.chen@gmail.com",
    linked_at: "2026-10-01T10:00:00Z",
    last_used_at: "2026-10-19T16:20:00Z",
    ...overrides,
  };
}
