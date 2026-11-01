import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { makePendingOrder, makeRejectedOrder, makeUpiOrder } from "../../test/billing";
import { errorResponse, stubFetch } from "../../test/http";
import { renderWithRouter } from "../../test/render";
import PendingCheckoutBanner, {
  PendingCheckoutNotice,
  RejectedOrderBanner,
  RejectedOrderNotice,
} from "./PendingCheckoutBanner";

const upiPending = makePendingOrder({ public_id: "u7Kp2QwX9mN4bV6cZ8dA", channel: "upi_direct", pay_url: null });
const upiSubmitted = makePendingOrder({ ...upiPending, status: "submitted" });

describe("PendingCheckoutBanner", () => {
  it("follows a Razorpay checkout", () => {
    renderWithRouter(<PendingCheckoutBanner order={makePendingOrder()} />);
    expect(screen.getByRole("status")).toHaveTextContent("A payment for Standard is in progress.");
    expect(screen.getByRole("link", { name: "Check status" })).toHaveAttribute(
      "href",
      "/billing/return?order=q3Zx8LmP0aB2cD4eF6gH",
    );
  });

  it("sends a direct-UPI order back to its UTR, then to its details while the owner checks it", () => {
    const first = renderWithRouter(<PendingCheckoutBanner order={upiPending} />);
    expect(screen.getByRole("status")).toHaveTextContent("Finish your UPI payment for Standard: send its UTR.");
    expect(screen.getByRole("link", { name: "Continue" })).toHaveAttribute(
      "href",
      "/billing/return?order=u7Kp2QwX9mN4bV6cZ8dA",
    );
    first.unmount();

    renderWithRouter(<PendingCheckoutBanner order={makeUpiOrder({ status: "submitted" })} />);
    expect(screen.getByRole("status")).toHaveTextContent("We're checking your UPI payment of ₹2,000 for Standard.");
    expect(screen.getByRole("link", { name: "Details" })).toBeInTheDocument();
  });
});

describe("PendingCheckoutNotice", () => {
  it("cancels a direct-UPI order waiting for its UTR", async () => {
    const calls = stubFetch({ "POST /billing/orders/u7Kp2QwX9mN4bV6cZ8dA/cancel": makeUpiOrder({ status: "cancelled" }) });
    const onCancelled = vi.fn();
    renderWithRouter(<PendingCheckoutNotice order={upiPending} onCancelled={onCancelled} />);
    expect(screen.queryByRole("link", { name: /Resume payment/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancelled).toHaveBeenCalledOnce();
    expect(calls.map((call) => call.path)).toEqual(["/billing/orders/u7Kp2QwX9mN4bV6cZ8dA/cancel"]);
  });

  it("can't cancel a UTR the owner is checking", () => {
    renderWithRouter(<PendingCheckoutNotice order={upiSubmitted} onCancelled={vi.fn()} />);
    expect(screen.getByRole("status")).toHaveTextContent("We're checking your UPI payment of ₹2,000 for Standard.");
    expect(screen.getByRole("link", { name: "Details" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
  });

  it("shows a refused cancel", async () => {
    stubFetch({ "POST /billing/orders/u7Kp2QwX9mN4bV6cZ8dA/cancel": errorResponse(409, "Already sent for checking.") });
    renderWithRouter(<PendingCheckoutNotice order={upiPending} onCancelled={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(await screen.findByText("Already sent for checking.")).toBeInTheDocument();
  });
});

describe("A rejected UPI payment", () => {
  it("is always shown as a notice, with See why", () => {
    renderWithRouter(<RejectedOrderNotice order={makeRejectedOrder()} />);
    expect(screen.getByRole("status")).toHaveTextContent("We couldn't confirm your UPI payment of ₹2,000 for Standard.");
    expect(screen.getByRole("link", { name: "See why" })).toHaveAttribute(
      "href",
      "/billing/return?order=u7Kp2QwX9mN4bV6cZ8dA",
    );
    expect(screen.queryByRole("button", { name: "Dismiss" })).not.toBeInTheDocument();
  });

  it("can be dismissed in the chat for the session, per order", async () => {
    const order = makeRejectedOrder();
    const first = renderWithRouter(<RejectedOrderBanner order={order} />);
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(sessionStorage.getItem("rag.upiRejectedSeen")).toBe(order.public_id);
    first.unmount();

    const again = renderWithRouter(<RejectedOrderBanner order={order} />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    again.unmount();

    renderWithRouter(<RejectedOrderBanner order={makeRejectedOrder({ public_id: "newer" })} />);
    expect(screen.getByRole("status")).toHaveTextContent("We couldn't confirm your UPI payment");
  });

  it("still dismisses when session storage is unavailable", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    renderWithRouter(<RejectedOrderBanner order={makeRejectedOrder()} />);
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
