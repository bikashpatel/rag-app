import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { makeAdminUserPlan } from "../../test/adminTabs";
import { NOW } from "../../test/fixtures";
import {
  CopyButton,
  currentIstMonth,
  endedOn,
  istDateTime,
  istToday,
  methodLabel,
  OrderStatusBadge,
  Pagination,
  PaymentStatusBadge,
  PlanLine,
  roundedInr,
  shortIstDate,
} from "./billingUi";

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(NOW));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("billing formats", () => {
  it("reads months, days and times in India", () => {
    expect(currentIstMonth(Date.parse("2026-10-31T18:29:00Z"))).toBe("2026-10");
    expect(currentIstMonth(Date.parse("2026-10-31T18:30:00Z"))).toBe("2026-11");
    expect(istToday(Date.parse("2026-10-20T19:00:00Z"))).toBe("2026-10-21");
    expect(shortIstDate("2026-11-25")).toBe("25 Nov");
    expect(shortIstDate("2025-11-25")).toBe("25 Nov 2025");
    expect(istDateTime("2026-10-20T09:32:00Z")).toBe("20 Oct 2026, 15:02 IST");
    expect(istDateTime("soon")).toBe("—");
    expect(roundedInr(66_616)).toBe("₹666");
    expect(methodLabel("bank_transfer")).toBe("Bank transfer");
    expect(methodLabel("paylater")).toBe("paylater");
    expect(methodLabel(null)).toBe("—");
  });

  it("dates an ended plan by its last valid day", () => {
    expect(endedOn({ valid_through: "2026-10-11", ended_at: "2026-10-11T18:30:00Z" })).toBe("2026-10-11");
    expect(shortIstDate(endedOn({ valid_through: null, ended_at: "2026-10-11T18:30:00Z" }) ?? "")).toBe("11 Oct");
    expect(endedOn({ valid_through: null, ended_at: null })).toBeNull();
    expect(endedOn({ valid_through: null, ended_at: "never" })).toBeNull();
  });
});

describe("badges", () => {
  it("names each payment and order state in words", () => {
    render(
      <>
        <PaymentStatusBadge payment={{ status: "captured", refunded_paise: 0 }} />
        <PaymentStatusBadge payment={{ status: "refunded", refunded_paise: 200_000 }} />
        <PaymentStatusBadge payment={{ status: "partially_refunded", refunded_paise: 50_000 }} />
        <PaymentStatusBadge payment={{ status: "disputed", refunded_paise: 0 }} />
        {(["pending", "paid", "expired", "cancelled", "review"] as const).map((status) => (
          <OrderStatusBadge key={status} status={status} />
        ))}
      </>,
    );
    expect(document.body).toHaveTextContent("PaidRefundedRefunded ₹500DisputedPendingPaidExpiredCancelledReview");
  });

  it("describes a user's plan in each state", () => {
    const { rerender } = render(<PlanLine plan={makeAdminUserPlan()} isAdmin />);
    expect(screen.getByText("Exempt")).toBeInTheDocument();
    rerender(<PlanLine plan={makeAdminUserPlan({ state: "none", plan: null })} isAdmin={false} />);
    expect(screen.getByText("No plan")).toBeInTheDocument();
    rerender(
      <PlanLine plan={makeAdminUserPlan({ state: "expired", plan: null, valid_through: null })} isAdmin={false} />,
    );
    expect(screen.getByText("Ended")).toBeInTheDocument();
    rerender(<PlanLine plan={makeAdminUserPlan({ open_ended: true, valid_through: null })} isAdmin={false} />);
    expect(screen.getByText("Standard")).toBeInTheDocument();
    expect(screen.getByText("No end date")).toBeInTheDocument();
    expect(screen.queryByText("Complimentary")).not.toBeInTheDocument();
  });
});

describe("Pagination", () => {
  it("hides on a single page and moves between pages", async () => {
    const onPage = vi.fn();
    const user = userEvent.setup();
    const { rerender } = render(<Pagination page={1} pageSize={50} total={20} label="Pages" onPage={onPage} />);
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    rerender(<Pagination page={2} pageSize={50} total={60} label="Pages" onPage={onPage} />);
    expect(screen.getByRole("navigation", { name: "Pages" })).toHaveTextContent("51–60 of 60");
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Previous page" }));
    expect(onPage).toHaveBeenCalledWith(1);
    rerender(<Pagination page={2} pageSize={50} total={0} label="Pages" onPage={onPage} />);
    expect(screen.getByRole("navigation", { name: "Pages" })).toHaveTextContent("0–0 of 0");
  });
});

describe("CopyButton", () => {
  it("says when the clipboard refuses", async () => {
    const user = userEvent.setup();
    vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(new Error("denied"));
    render(<CopyButton value="pay_1" label="Copy the id" />);
    await user.click(screen.getByRole("button", { name: "Copy the id" }));
    expect(await screen.findByText("Couldn't copy", { selector: "button" })).toBeInTheDocument();
  });
});
