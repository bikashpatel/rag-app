import { describe, expect, it } from "vitest";
import { makeCheckoutOption, makePlanOffer, makeSeededOffers } from "../../test/billing";
import { makeLapsedPlanAccess, makePaidPlanAccess, makePlanAccess } from "../../test/fixtures";
import {
  canUpgrade,
  checkoutPath,
  daysLeftBadge,
  endsBadge,
  expiringDays,
  isRetired,
  isStacked,
  istDayMonth,
  istDaysUntil,
  istLongDay,
  istMoment,
  lastDayOf,
  methodLabel,
  planLine,
  renewalOf,
  renewPath,
  returnPath,
} from "./planText";

const NOW = Date.parse("2026-10-20T09:30:00Z");

describe("IST dates", () => {
  it("counts days to an IST date or instant, across the IST midnight", () => {
    expect(istDaysUntil("2026-10-20", NOW)).toBe(0);
    expect(istDaysUntil("2026-10-25", NOW)).toBe(5);
    expect(istDaysUntil("2026-10-19", NOW)).toBe(-1);
    // 18:30 UTC is already the next day in India.
    expect(istDaysUntil("2026-10-20T18:30:00Z", NOW)).toBe(1);
    expect(istDaysUntil("2026-10-20T18:29:00Z", NOW)).toBe(0);
    expect(istDaysUntil("2026-10-20", Date.parse("2026-10-19T18:30:00Z"))).toBe(0);
  });

  it("writes days and moments in India time", () => {
    expect(istDayMonth("2026-11-07T18:30:00Z")).toBe("8 Nov");
    expect(istDayMonth("2026-10-31")).toBe("31 Oct");
    expect(istLongDay("2026-11-07T18:30:00Z")).toBe("Sun 8 November");
    expect(istLongDay("2026-11-07")).toBe("Sat 7 November");
    expect(istLongDay("nonsense")).toBe("—");
    expect(istMoment("2026-10-20T09:28:00Z", NOW)).toBe("Today, 14:58");
    expect(istMoment("2026-10-18T20:00:00Z", NOW)).toBe("19 Oct, 01:30");
    expect(istMoment("nonsense", NOW)).toBe("—");
  });

  it("finds the last day of a period that ends at midnight IST", () => {
    expect(lastDayOf("2026-11-07T18:30:00Z")).toBe("2026-11-07");
    expect(lastDayOf("2026-12-31T18:30:00Z")).toBe("2026-12-31");
  });
});

describe("plan words", () => {
  it("puts the plan on one line", () => {
    expect(planLine(makePaidPlanAccess())).toBe("Standard · until 7 Nov");
    expect(planLine(makePaidPlanAccess({ paid_through: "2026-12-07T18:30:00Z", valid_through: "2026-12-07" }))).toBe(
      "Standard · paid through 7 Dec",
    );
    expect(planLine(makePlanAccess())).toBe("Complimentary Professional");
    expect(planLine(makeLapsedPlanAccess())).toBe("No active plan");
    expect(planLine(makePlanAccess({ state: "exempt", plan: null }))).toBe("Administrator");
  });

  it("knows a period paid ahead", () => {
    expect(isStacked(makePaidPlanAccess())).toBe(false);
    expect(isStacked(makePaidPlanAccess({ paid_through: "2026-12-07T18:30:00Z" }))).toBe(true);
    expect(isStacked(makePlanAccess())).toBe(false);
  });

  it("counts the days left only while the plan ends soon", () => {
    const ending = makePaidPlanAccess({ expiring_soon: true, valid_through: "2026-10-24" });
    expect(expiringDays(ending, NOW)).toBe(4);
    expect(expiringDays(makePaidPlanAccess({ expiring_soon: true, valid_through: "2026-10-19" }), NOW)).toBe(0);
    expect(expiringDays(makePaidPlanAccess(), NOW)).toBeNull();
    expect(expiringDays(makeLapsedPlanAccess("expired", { expiring_soon: true }), NOW)).toBeNull();
  });

  it.each([
    [5, "Ends in 5 days", "5 days left"],
    [1, "Ends tomorrow", "1 day left"],
    [0, "Ends today", "Last day"],
  ])("words %i days left", (days, badge, left) => {
    expect(endsBadge(days)).toBe(badge);
    expect(daysLeftBadge(days)).toBe(left);
  });

  it("names payment methods", () => {
    expect(methodLabel("upi")).toBe("UPI");
    expect(methodLabel("bank_transfer")).toBe("Bank transfer");
    expect(methodLabel("paylater_plan")).toBe("Paylater plan");
    expect(methodLabel(null)).toBeNull();
  });

  it("knows when a higher plan has more: a locked feature or more models", () => {
    const starter = makePaidPlanAccess({ features: { files: false, images: false, plugins: [] } });
    expect(canUpgrade(starter)).toBe(true);
    const professional = makePlanAccess({
      unlock: { files: "Standard", images: "Professional", plugins: { gmail: "Professional" }, models: null },
    });
    expect(canUpgrade(professional)).toBe(false);
    const gmailOnly = makePlanAccess({
      features: { files: true, images: true, plugins: [] },
      unlock: { files: null, images: null, plugins: { gmail: "Professional" }, models: null },
    });
    expect(canUpgrade(gmailOnly)).toBe(true);
  });

  it("finds the renewal only when the plan's period can be bought", () => {
    const period = makeCheckoutOption({ label: "Renew" });
    const now = makeCheckoutOption({ kind: "now", label: "Start a new period now · ₹450" });
    expect(renewalOf(makePlanOffer({ options: [now, period] }))).toBe(period);
    expect(renewalOf(makePlanOffer({ options: [now] }))).toBeNull();
    const paidAhead = makeCheckoutOption({ unavailable_reason: "You already have a period paid ahead." });
    expect(renewalOf(makePlanOffer({ options: [paidAhead] }))).toBeNull();
    expect(renewalOf(null)).toBeNull();
  });

  it("knows a plan that is no longer offered", () => {
    const [starter, standard] = makeSeededOffers();
    expect(isRetired([starter, standard], "standard")).toBe(false);
    expect(isRetired([starter, { ...standard, status: "retired" }], "standard")).toBe(true);
    expect(isRetired([starter], "standard")).toBe(true);
  });

  it("builds the checkout and return paths", () => {
    expect(checkoutPath("standard", "now")).toBe("/plans/checkout/standard?kind=now");
    expect(renewPath("starter")).toBe("/plans/checkout/starter?kind=period");
    expect(returnPath("a b")).toBe("/billing/return?order=a%20b");
  });
});
