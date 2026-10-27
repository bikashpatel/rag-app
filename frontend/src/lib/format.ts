// Shared display formatters.

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const value = bytes / 1024 ** i;
  return `${value >= 10 || i === 0 ? Math.round(value) : value.toFixed(1)} ${units[i]}`;
}

export function formatNumber(n: number): string {
  return new Intl.NumberFormat().format(n);
}

/** A limit set in megabytes, written as the plans write it: "3,000 MB". Amounts used go through formatBytes. */
export function formatMegabytes(mb: number): string {
  return `${formatNumber(mb)} MB`;
}

/** 1234 -> "1.2K", 3_400_000 -> "3.4M" */
export function formatCompact(n: number): string {
  return new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(n);
}

export function formatCost(usd: number): string {
  if (!usd) return "$0.00";
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(2)}`;
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

export function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  const seconds = Math.round((d.getTime() - Date.now()) / 1000);
  if (Number.isNaN(seconds)) return "";
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  const abs = Math.abs(seconds);
  if (abs < 60) return rtf.format(seconds, "second");
  if (abs < 3600) return rtf.format(Math.round(seconds / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(seconds / 3600), "hour");
  if (abs < 86400 * 30) return rtf.format(Math.round(seconds / 86400), "day");
  return d.toLocaleDateString();
}

/** "0" limit means unlimited. */
export function formatLimit(n: number, formatter: (n: number) => string = formatNumber): string {
  return n === 0 ? "Unlimited" : formatter(n);
}

// Settings and usage dates read "18 Oct 2026", "Sat 1 November, 01:00": day before month, 24-hour clock.
const DATE_LOCALE = "en-GB";
const DAY_MS = 86_400_000;

function parseDate(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "YYYY-MM-DD" → that day at 00:00 UTC; null when malformed. */
function parseUtcDay(date: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
  if (!match) return null;
  const [year, month, day] = [Number(match[1]), Number(match[2]) - 1, Number(match[3])];
  const d = new Date(Date.UTC(year, month, day));
  return d.getUTCFullYear() === year && d.getUTCMonth() === month && d.getUTCDate() === day ? d : null;
}

/** Local date: "18 Oct 2026"; "—" when absent. */
export function formatDate(iso: string | null | undefined): string {
  const d = parseDate(iso);
  return d ? new Intl.DateTimeFormat(DATE_LOCALE, { day: "numeric", month: "short", year: "numeric" }).format(d) : "—";
}

/** Local day and month: "1 Nov". */
export function formatDayMonth(iso: string): string {
  const d = parseDate(iso);
  return d ? new Intl.DateTimeFormat(DATE_LOCALE, { day: "numeric", month: "short" }).format(d) : "—";
}

/** Local weekday, date and time: "Sat 1 November, 01:00" (when the monthly limits start again). */
export function formatResetMoment(iso: string): string {
  const d = parseDate(iso);
  if (!d) return "—";
  const day = new Intl.DateTimeFormat(DATE_LOCALE, { weekday: "short", day: "numeric", month: "long" }).format(d);
  const time = new Intl.DateTimeFormat(DATE_LOCALE, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(d);
  return `${day}, ${time}`;
}

/** A "YYYY-MM-DD" UTC day: "Tue 14 October" (long) or "14" (short). */
export function formatUtcDay(date: string, style: "long" | "short"): string {
  const d = parseUtcDay(date);
  if (!d) return date;
  if (style === "short") return String(d.getUTCDate());
  return new Intl.DateTimeFormat(DATE_LOCALE, { weekday: "short", day: "numeric", month: "long", timeZone: "UTC" }).format(d);
}

/** "YYYY-MM" → "October 2026". */
export function formatMonthName(month: string): string {
  const d = parseUtcDay(`${month}-01`);
  return d ? new Intl.DateTimeFormat(DATE_LOCALE, { month: "long", year: "numeric", timeZone: "UTC" }).format(d) : month;
}

/** Whole days from `now` until `iso`, rounded up, never negative (0 when malformed). */
export function daysUntil(iso: string, now: number = Date.now()): number {
  const d = parseDate(iso);
  return d ? Math.max(0, Math.ceil((d.getTime() - now) / DAY_MS)) : 0;
}
