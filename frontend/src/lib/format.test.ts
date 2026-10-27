import { afterEach, describe, expect, it, vi } from "vitest";
import {
  daysUntil,
  formatBytes,
  formatCost,
  formatDate,
  formatDateTime,
  formatDayMonth,
  formatLimit,
  formatMegabytes,
  formatMonthName,
  formatRelativeTime,
  formatResetMoment,
  formatUtcDay,
} from "./format";

describe("formatBytes", () => {
  it.each([
    [0, "0 B"],
    [-5, "0 B"],
    [Number.NaN, "0 B"],
    [512, "512 B"],
    [1536, "1.5 KB"],
    [482_133, "471 KB"],
    [3_250_000, "3.1 MB"],
    [25 * 1024 ** 2, "25 MB"],
    [1024 ** 5 * 3, "3072 TB"],
  ])("%d bytes -> %s", (bytes, expected) => {
    expect(formatBytes(bytes)).toBe(expected);
  });
});

describe("formatMegabytes", () => {
  it("writes a limit in megabytes as the plans do, with thousands separators", () => {
    expect(formatMegabytes(200)).toBe("200 MB");
    expect(formatMegabytes(1_000)).toBe("1,000 MB");
    expect(formatMegabytes(3_000)).toBe("3,000 MB");
  });
});

describe("formatCost", () => {
  it.each([
    [0, "$0.00"],
    [0.0042, "$0.0042"],
    [0.01, "$0.01"],
    [1.425, "$1.43"],
    [12, "$12.00"],
  ])("%d -> %s", (usd, expected) => {
    expect(formatCost(usd)).toBe(expected);
  });
});

describe("formatLimit", () => {
  it("reads 0 as unlimited and formats anything else", () => {
    expect(formatLimit(0)).toBe("Unlimited");
    expect(formatLimit(25, (n) => `${n} MB`)).toBe("25 MB");
  });
});

describe("dates", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("show a dash for missing or malformed times", () => {
    expect(formatDateTime(null)).toBe("—");
    expect(formatDateTime("not a date")).toBe("—");
    expect(formatDate(undefined)).toBe("—");
    expect(formatDate("2026-13-45")).toBe("—");
    expect(formatDayMonth("")).toBe("—");
    expect(formatResetMoment("soon")).toBe("—");
  });

  it("format local dates day before month on a 24-hour clock", () => {
    expect(formatDate("2026-10-18T14:05:00Z")).toBe("18 Oct 2026");
    expect(formatDayMonth("2026-11-01T00:00:00Z")).toBe("1 Nov");
    expect(formatResetMoment("2026-11-01T00:00:00Z")).toBe("Sun 1 November, 00:00");
  });

  it("formats UTC days and months, leaving malformed ones as they are", () => {
    expect(formatUtcDay("2026-10-14", "long")).toBe("Wed 14 October");
    expect(formatUtcDay("2026-10-14", "short")).toBe("14");
    expect(formatUtcDay("2026-02-30", "long")).toBe("2026-02-30");
    expect(formatMonthName("2026-10")).toBe("October 2026");
    expect(formatMonthName("2026-1")).toBe("2026-1");
  });

  it("counts whole days until a time, rounded up and never negative", () => {
    const now = Date.parse("2026-10-20T09:30:00Z");
    expect(daysUntil("2026-11-01T00:00:00Z", now)).toBe(12);
    expect(daysUntil("2026-10-20T09:30:01Z", now)).toBe(1);
    expect(daysUntil("2026-10-01T00:00:00Z", now)).toBe(0);
    expect(daysUntil("garbage", now)).toBe(0);
  });

  it("describes times relative to now", () => {
    vi.useFakeTimers({ now: new Date("2026-10-20T09:30:00Z") });
    const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
    expect(formatRelativeTime(null)).toBe("");
    expect(formatRelativeTime("nonsense")).toBe("");
    expect(formatRelativeTime("2026-10-20T09:29:30Z")).toBe(rtf.format(-30, "second"));
    expect(formatRelativeTime("2026-10-20T09:25:00Z")).toBe(rtf.format(-5, "minute"));
    expect(formatRelativeTime("2026-10-20T06:30:00Z")).toBe(rtf.format(-3, "hour"));
    expect(formatRelativeTime("2026-10-18T09:30:00Z")).toBe(rtf.format(-2, "day"));
    expect(formatRelativeTime("2026-08-01T09:30:00Z")).toBe(new Date("2026-08-01T09:30:00Z").toLocaleDateString());
  });
});
