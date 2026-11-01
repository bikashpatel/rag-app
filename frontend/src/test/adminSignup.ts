import type { SignupStatus } from "../api/types";

/** Admin → Users → Sign-up with everything configured and sign-up open: no warnings. */
export function makeSignupStatus(overrides: Partial<SignupStatus> = {}): SignupStatus {
  return {
    settings: { open: true, email: true, google: true },
    effective: { open: true, email: true, google: true },
    mail_configured: true,
    mail_webhook_configured: true,
    turnstile_configured: true,
    google_configured: true,
    payments_live: true,
    mail_sent_today: 12,
    mail_queued: 0,
    mail_daily_limit: 80,
    public_mail_left: 20,
    public_paused: false,
    bounces_today: 0,
    complaints_7d: 0,
    mail_last_error: null,
    signups_today: 3,
    signups_7d: 14,
    paid_7d: 2,
    warnings: [],
    ...overrides,
  };
}

/** As the app ships: sign-up closed, no mailer, Turnstile or Google sign-in, test payment keys. */
export function makeShippedSignupStatus(overrides: Partial<SignupStatus> = {}): SignupStatus {
  return makeSignupStatus({
    settings: { open: false, email: true, google: true },
    effective: { open: false, email: false, google: false },
    mail_configured: false,
    mail_webhook_configured: false,
    turnstile_configured: false,
    google_configured: false,
    payments_live: false,
    mail_sent_today: 0,
    public_mail_left: 0,
    signups_today: 0,
    signups_7d: 0,
    paid_7d: 0,
    warnings: ["payments_not_live"],
    ...overrides,
  });
}
