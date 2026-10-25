import { afterEach, describe, expect, it, vi } from "vitest";
import {
  formatAddressForInput,
  formatCountdown,
  formatEmailDate,
  importStatusView,
  plural,
  safeHttpsUrl,
  splitAddressList,
  unicodeAddress,
} from "./email";

describe("safeHttpsUrl", () => {
  it("keeps https links only", () => {
    expect(safeHttpsUrl("https://mail.google.com/mail/u/0/#all/1")).toBe("https://mail.google.com/mail/u/0/#all/1");
    expect(safeHttpsUrl("HTTPS://mail.google.com/")).toBe("HTTPS://mail.google.com/");
    expect(safeHttpsUrl("http://mail.google.com/")).toBeNull();
    expect(safeHttpsUrl("javascript:alert(1)")).toBeNull();
    expect(safeHttpsUrl(" https://example.com")).toBeNull();
    expect(safeHttpsUrl(null)).toBeNull();
    expect(safeHttpsUrl(undefined)).toBeNull();
  });
});

describe("unicodeAddress", () => {
  it("shows an internationalized domain in its unicode form", () => {
    expect(unicodeAddress({ address: "info@xn--bcher-kva.de", name: null, unicode_domain: "bücher.de" })).toBe("info@bücher.de");
  });

  it("is null for an ASCII domain", () => {
    expect(unicodeAddress({ address: "priya@example.com", name: "Priya", unicode_domain: null })).toBeNull();
  });
});

describe("formatAddressForInput", () => {
  it("uses the bare address without a name", () => {
    expect(formatAddressForInput({ address: "bob@example.com", name: null, unicode_domain: null })).toBe("bob@example.com");
    expect(formatAddressForInput({ address: "bob@example.com", name: "  ", unicode_domain: null })).toBe("bob@example.com");
  });

  it("leaves plain names unquoted", () => {
    expect(formatAddressForInput({ address: "j@example.com", name: "J. Smith", unicode_domain: null })).toBe(
      "J. Smith <j@example.com>",
    );
  });

  it("quotes names with address-list specials and escapes quotes and backslashes", () => {
    expect(formatAddressForInput({ address: "jane@example.org", name: "Doe, Jane", unicode_domain: null })).toBe(
      '"Doe, Jane" <jane@example.org>',
    );
    expect(formatAddressForInput({ address: "ann@example.com", name: 'Ann "AJ" Lee\\', unicode_domain: null })).toBe(
      '"Ann \\"AJ\\" Lee\\\\" <ann@example.com>',
    );
  });
});

describe("splitAddressList", () => {
  it("splits on commas and semicolons, trimming and dropping empty entries", () => {
    expect(splitAddressList(" a@example.com, b@example.com;c@example.com ,, ")).toEqual([
      "a@example.com",
      "b@example.com",
      "c@example.com",
    ]);
  });

  it("keeps separators inside quotes and angle brackets", () => {
    expect(splitAddressList('"Doe, Jane" <jane@example.org>, Bob <bob@example.com>')).toEqual([
      '"Doe, Jane" <jane@example.org>',
      "Bob <bob@example.com>",
    ]);
    expect(splitAddressList("Odd <odd,name@example.com>")).toEqual(["Odd <odd,name@example.com>"]);
  });

  it("keeps escaped quotes inside a quoted name", () => {
    expect(splitAddressList('"Ann \\"AJ, the lead\\" Lee" <ann@example.com>, bob@example.com')).toEqual([
      '"Ann \\"AJ, the lead\\" Lee" <ann@example.com>',
      "bob@example.com",
    ]);
  });

  it("reads back what formatAddressForInput writes", () => {
    const recipients = [
      { address: "jane@example.org", name: "Doe, Jane", unicode_domain: null },
      { address: "ann@example.com", name: 'Ann "AJ"; Lee', unicode_domain: null },
      { address: "bob@example.com", name: null, unicode_domain: null },
    ];
    const formatted = recipients.map(formatAddressForInput);
    expect(splitAddressList(formatted.join(", "))).toEqual(formatted);
  });
});

describe("formatEmailDate", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("is empty for a missing or malformed date", () => {
    expect(formatEmailDate(null)).toBe("");
    expect(formatEmailDate("not a date")).toBe("");
  });

  it("omits the year for this year's dates only", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-20T09:30:00Z"));
    expect(formatEmailDate("2026-03-04T10:05:00Z")).not.toContain("2026");
    expect(formatEmailDate("2024-03-04T10:05:00Z")).toContain("2024");
  });
});

describe("formatCountdown", () => {
  it("rounds up to whole seconds and adds hours when needed", () => {
    expect(formatCountdown(0)).toBe("0:00");
    expect(formatCountdown(-5_000)).toBe("0:00");
    expect(formatCountdown(1)).toBe("0:01");
    expect(formatCountdown(61_000)).toBe("1:01");
    expect(formatCountdown(29 * 60_000 + 41_000)).toBe("29:41");
    expect(formatCountdown(3_723_000)).toBe("1:02:03");
  });
});

describe("importStatusView", () => {
  it("names every import status", () => {
    expect(importStatusView("imported")).toEqual({ label: "Imported", tone: "success" });
    expect(importStatusView("already_existed")).toEqual({ label: "Already in your documents", tone: "neutral" });
    expect(importStatusView("skipped")).toEqual({ label: "Skipped", tone: "warning" });
    expect(importStatusView("failed")).toEqual({ label: "Failed", tone: "danger" });
  });
});

describe("plural", () => {
  it("picks the singular for one only", () => {
    expect(plural(1, "email")).toBe("1 email");
    expect(plural(0, "email")).toBe("0 emails");
    expect(plural(3, "attachment")).toBe("3 attachments");
    expect(plural(2, "match", "matches")).toBe("2 matches");
  });
});
