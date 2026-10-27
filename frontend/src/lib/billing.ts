// Money, IST dates and plan figures for the plans and billing screens (docs/briefs/plans-billing.md 8;
// plans-billing-visual.md 2.3). Billing dates are India dates whatever the browser's zone, matching the server's.

import { formatNumber } from "./format";

const rupees = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" });
const wholeRupees = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 0,
  maximumFractionDigits: 0,
});

/** Integer paise in en-IN grouping, without ".00" for whole rupees: "₹2,000", "₹1,234.50", "₹1,00,000". */
export function formatInr(paise: number): string {
  return (paise % 100 === 0 ? wholeRupees : rupees).format(paise / 100);
}

// Fixed English abbreviations: Intl's en-GB writes "Sept", the server writes "Sep".
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
/** India has no daylight saving time: IST is always UTC+05:30. */
const IST_OFFSET_MS = 330 * 60_000;

type Day = { year: number; month: number; day: number };

/** A "YYYY-MM-DD" is that calendar day; anything else is an instant, read as its IST date. */
function istDay(value: string | Date): Day | null {
  const plain = typeof value === "string" ? /^(\d{4})-(\d{2})-(\d{2})$/.exec(value) : null;
  const instant = plain
    ? Date.UTC(Number(plain[1]), Number(plain[2]) - 1, Number(plain[3]))
    : (typeof value === "string" ? Date.parse(value) : value.getTime()) + IST_OFFSET_MS;
  if (Number.isNaN(instant)) return null;
  const d = new Date(instant);
  if (plain && (d.getUTCMonth() !== Number(plain[2]) - 1 || d.getUTCDate() !== Number(plain[3]))) return null;
  return { year: d.getUTCFullYear(), month: d.getUTCMonth(), day: d.getUTCDate() };
}

/** "25 Nov 2026" (IST); a "YYYY-MM-DD" is taken as that day. "—" when malformed. */
export function formatIstDate(value: string | Date): string {
  const d = istDay(value);
  return d ? `${d.day} ${MONTHS[d.month]} ${d.year}` : "—";
}

/**
 * "26 Oct to 25 Nov 2026" ("26 Dec 2026 to 25 Jan 2027" across years). An `end` instant is exclusive, as a period's
 * `ends_at` (midnight IST): the range ends the day before. A "YYYY-MM-DD" `end` (a valid-through date) is included.
 */
export function formatIstRange(start: string, end: string): string {
  const from = istDay(start);
  const endsExclusive = !/^\d{4}-\d{2}-\d{2}$/.test(end);
  const to = istDay(endsExclusive ? new Date(Date.parse(end) - 1) : end);
  if (!from || !to) return "—";
  const first = `${from.day} ${MONTHS[from.month]}${from.year === to.year ? "" : ` ${from.year}`}`;
  return `${first} to ${to.day} ${MONTHS[to.month]} ${to.year}`;
}

/** "About 1,600 answers a month with Claude Haiku 5.5"; "Unlimited" when there's no figure (an unlimited allowance). */
export function answersLabel(answers: number | null, model: string | null): string {
  if (answers === null) return "Unlimited";
  const figure = `About ${formatNumber(answers)} answers a month`;
  return model ? `${figure} with ${model}` : figure;
}

/** A model's rate against the chat allowance: "×2", "×0.05"; null for 1 or unknown (plain tokens). */
export function formatRate(rate: number | null): string | null {
  return rate === null || rate === 1 ? null : `×${rate}`;
}

/** Where Pay may send the browser: https on rzp.io or a razorpay.com subdomain; in test mode also this app (mocks). */
export function isRazorpayUrl(url: string, testMode: boolean): boolean {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  if (testMode && parsed.origin === window.location.origin) return true;
  const plain = parsed.protocol === "https:" && parsed.port === "" && !parsed.username && !parsed.password;
  return plain && (parsed.hostname === "rzp.io" || parsed.hostname.endsWith(".razorpay.com"));
}

/** The first line of a policy page that is still a placeholder (frontend/public/legal/*.md). */
export const DRAFT_MARKER = "<!-- draft -->";
