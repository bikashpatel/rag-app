import { describe, expect, it } from "vitest";
import {
  answersLabel,
  DRAFT_MARKER,
  formatInr,
  formatIstDate,
  formatIstRange,
  formatRate,
  isRazorpayUrl,
} from "./billing";

describe("formatInr", () => {
  it("writes rupees in Indian grouping, without paise when they are zero", () => {
    expect(formatInr(200_000)).toBe("₹2,000");
    expect(formatInr(123_450)).toBe("₹1,234.50");
    expect(formatInr(10_000_000)).toBe("₹1,00,000");
    expect(formatInr(100)).toBe("₹1");
    expect(formatInr(5)).toBe("₹0.05");
    expect(formatInr(0)).toBe("₹0");
  });
});

describe("formatIstDate", () => {
  it("reads an instant as its date in India, whatever the browser's zone", () => {
    expect(formatIstDate("2026-11-25T18:29:59Z")).toBe("25 Nov 2026");
    expect(formatIstDate("2026-11-25T18:30:00Z")).toBe("26 Nov 2026");
    expect(formatIstDate(new Date("2026-03-31T18:30:00Z"))).toBe("1 Apr 2026");
  });

  it("takes a YYYY-MM-DD as that day, and writes September as the server does", () => {
    expect(formatIstDate("2026-11-25")).toBe("25 Nov 2026");
    expect(formatIstDate("2026-09-07")).toBe("7 Sep 2026");
  });

  it("shows a dash for what isn't a date", () => {
    expect(formatIstDate("")).toBe("—");
    expect(formatIstDate("soon")).toBe("—");
    expect(formatIstDate("2026-02-30")).toBe("—");
    expect(formatIstDate(new Date(Number.NaN))).toBe("—");
  });
});

describe("formatIstRange", () => {
  it("ends the day before a period's end instant (midnight in India)", () => {
    expect(formatIstRange("2026-10-26T05:12:00Z", "2026-11-25T18:30:00Z")).toBe("26 Oct to 25 Nov 2026");
  });

  it("includes a valid-through day", () => {
    expect(formatIstRange("2026-10-26T05:12:00Z", "2026-11-25")).toBe("26 Oct to 25 Nov 2026");
  });

  it("names both years across a new year", () => {
    expect(formatIstRange("2026-12-26T05:00:00Z", "2027-01-25")).toBe("26 Dec 2026 to 25 Jan 2027");
  });

  it("shows a dash when either end is malformed", () => {
    expect(formatIstRange("nope", "2026-11-25")).toBe("—");
    expect(formatIstRange("2026-10-26T05:12:00Z", "later")).toBe("—");
  });
});

describe("answersLabel", () => {
  it("says about how many answers a month, with the model when known", () => {
    expect(answersLabel(1_600, "Claude Haiku 5.5")).toBe("About 1,600 answers a month with Claude Haiku 5.5");
    expect(answersLabel(260, null)).toBe("About 260 answers a month");
  });

  it("calls an allowance without a figure unlimited", () => {
    expect(answersLabel(null, "Claude Opus 5.5")).toBe("Unlimited");
  });
});

describe("formatRate", () => {
  it("shows a rate other than 1, and nothing for 1 or an unpriced model", () => {
    expect(formatRate(2)).toBe("×2");
    expect(formatRate(0.05)).toBe("×0.05");
    expect(formatRate(2.7)).toBe("×2.7");
    expect(formatRate(1)).toBeNull();
    expect(formatRate(null)).toBeNull();
  });
});

describe("isRazorpayUrl", () => {
  it("accepts https links on rzp.io and razorpay.com subdomains", () => {
    expect(isRazorpayUrl("https://rzp.io/rzp/q3Zx8Lm", false)).toBe(true);
    expect(isRazorpayUrl("https://pages.razorpay.com/pl_Q3Zx8Lm/view", false)).toBe(true);
  });

  it("refuses anything else", () => {
    for (const url of [
      "http://rzp.io/rzp/q3Zx8Lm",
      "https://rzp.io:8443/rzp/q3Zx8Lm",
      "https://rzp.io.example.com/rzp",
      "https://evilrazorpay.com/pay",
      "https://razorpay.com.example.com/pay",
      "https://rzp.io@example.com/pay",
      "https://user:secret@pages.razorpay.com/pay",
      "javascript:alert(1)",
      "/__fake-razorpay/q3Zx8Lm",
      "not a url",
    ]) {
      expect(isRazorpayUrl(url, false), url).toBe(false);
    }
  });

  it("accepts this app's own origin only in test mode (the end-to-end mocks)", () => {
    const own = `${window.location.origin}/__fake-razorpay/q3Zx8Lm`;
    expect(isRazorpayUrl(own, true)).toBe(true);
    expect(isRazorpayUrl(own, false)).toBe(false);
    expect(isRazorpayUrl("https://example.com/pay", true)).toBe(false);
  });
});

it("marks a draft policy page with an HTML comment", () => {
  expect(DRAFT_MARKER).toBe("<!-- draft -->");
});
