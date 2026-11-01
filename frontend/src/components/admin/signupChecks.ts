import type { PageSlug, SignupAvailability, SignupStatus, SignupWarning } from "../../api/types";
import type { PageCheck } from "./policyPages";

const WARNING_ORDER: readonly SignupWarning[] = [
  "real_ip",
  "payments_not_live",
  "mail_webhook_missing",
  "admin_password_weak",
];

export const WARNING_TEXT: Record<SignupWarning, string> = {
  real_ip: "Visitor addresses aren't reaching the app: set NGINX_REAL_IP_FROM before opening sign-up.",
  payments_not_live: "Online payments aren't live: new accounts can't buy a plan yet.",
  mail_webhook_missing: "Bounce reports aren't set up: add Resend's webhook and MAIL_WEBHOOK_SECRET.",
  admin_password_weak: "The admin password is short or common: change ADMIN_PASSWORD in .env.",
};

type Unfinished = PageCheck & { state: Exclude<PageCheck["state"], "final"> };

const PAGE_STATE_TEXT: Partial<Record<PageSlug, Record<Unfinished["state"], string>>> = {
  privacy: {
    draft: "The privacy policy is still a draft.",
    missing: "The privacy policy isn't published.",
    error: "The privacy policy couldn't be checked.",
  },
  terms: {
    draft: "The terms of service are still a draft.",
    missing: "The terms of service aren't published.",
    error: "The terms of service couldn't be checked.",
  },
};

/** The policy pages' state in words, or null when they are published (or still being checked). */
export function policyWarning(pages: PageCheck[] | null): string | null {
  const unfinished = pages?.filter((page): page is Unfinished => page.state !== "final") ?? [];
  if (unfinished.length === 0) return null;
  if (unfinished.length > 1 && unfinished.every((page) => page.state === "draft")) {
    return "The privacy policy and terms are still drafts.";
  }
  return unfinished.map((page) => PAGE_STATE_TEXT[page.slug]?.[page.state] ?? `${page.title} isn't ready.`).join(" ");
}

/** The settings missing for email sign-up (the mailer and Turnstile) or for Google sign-in. */
export function missingEmailSettings(status: SignupStatus): string[] {
  return [
    ...(status.mail_configured ? [] : ["MAIL_SMTP_HOST", "MAIL_FROM"]),
    ...(status.turnstile_configured ? [] : ["TURNSTILE_SITE_KEY", "TURNSTILE_SECRET_KEY"]),
  ];
}

export const GOOGLE_SETTINGS = ["GOOGLE_SIGNIN_CLIENT_ID", "GOOGLE_SIGNIN_CLIENT_SECRET"] as const;

/** "A", "A and B", "A, B and C". */
export function listWords(words: readonly string[]): string {
  return words.length < 2 ? (words[0] ?? "") : `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
}

/** What opening sign-up would offer: the server's `effective` once open, else the switches with their prerequisites. */
export function offeredMethods(status: SignupStatus): Omit<SignupAvailability, "open"> {
  if (status.settings.open) return status.effective;
  return {
    email: status.settings.email && status.mail_configured && status.turnstile_configured,
    google: status.settings.google && status.google_configured,
  };
}

export function methodsLabel({ email, google }: Omit<SignupAvailability, "open">): string {
  if (email && google) return "Email and Google";
  if (email) return "Email only";
  return google ? "Google only" : "None";
}

/** What stands in the way of opening sign-up: a second click is needed while any does (the dialog's notice). */
export function openingWarnings(status: SignupStatus, pages: PageCheck[] | null): string[] {
  const warnings = WARNING_ORDER.filter(
    (warning) =>
      status.warnings.includes(warning) || (warning === "mail_webhook_missing" && !status.mail_webhook_configured),
  ).map((warning) => WARNING_TEXT[warning]);
  const policy = policyWarning(pages);
  return policy ? [...warnings, policy] : warnings;
}

export type CardWarning = { text: string; mail?: boolean };

/** The card's notices: the server's warnings, the policy drafts, and sign-up open without a mailer or Turnstile. */
export function cardWarnings(status: SignupStatus, pages: PageCheck[] | null): CardWarning[] {
  const warnings: CardWarning[] = WARNING_ORDER.filter((warning) => status.warnings.includes(warning)).map(
    (warning) => ({ text: WARNING_TEXT[warning] }),
  );
  const policy = policyWarning(pages);
  if (policy) warnings.push({ text: policy });
  if (status.settings.open && !(status.mail_configured && status.turnstile_configured)) {
    const missing = [status.mail_configured ? null : "the mailer", status.turnstile_configured ? null : "Turnstile"];
    const named = missing.filter((part): part is string => part !== null);
    const verb = named.length > 1 ? "aren't" : "isn't";
    warnings.push({
      text: `Sign-up is open, but ${listWords(named)} ${verb} configured: email sign-up and password resets are off.`,
      mail: !status.mail_configured,
    });
  }
  return warnings;
}

/** The next UTC midnight, when the day's mail count starts again, as IST wall time: "05:30". */
export function mailDayResetIst(now: number = Date.now()): string {
  const next = new Date(now);
  next.setUTCHours(24, 0, 0, 0);
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(next);
}
