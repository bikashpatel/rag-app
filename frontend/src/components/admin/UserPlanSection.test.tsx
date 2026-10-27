import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { UserBilling } from "../../api/types";
import { makeSeededAdminPlans } from "../../test/adminTabs";
import { makePeriodView, makeUserBilling, PROFESSIONAL, STANDARD, STARTER } from "../../test/billing";
import { makeAdminUser, makeLapsedPlanAccess, makePlanAccess, NOW } from "../../test/fixtures";
import { errorResponse, stubFetch, type FetchCall, type Route } from "../../test/http";
import UserPlanSection from "./UserPlanSection";

const maya = makeAdminUser();
const plans = makeSeededAdminPlans();

const billing = makeUserBilling({
  periods: [
    makePeriodView({
      id: 13,
      plan: PROFESSIONAL,
      plan_name: "Professional",
      starts_at: "2026-11-07T18:30:00Z",
      ends_at: "2026-12-07T18:30:00Z",
      valid_through: "2026-12-07",
      source: "comp",
      current: false,
      reason: "Goodwill",
    }),
    makePeriodView(),
    makePeriodView({
      id: 9,
      plan: STARTER,
      plan_name: "Starter",
      starts_at: "2026-09-08T05:12:00Z",
      ends_at: "2026-10-08T05:12:00Z",
      valid_through: "2026-10-08",
      current: false,
      test_mode: true,
    }),
  ],
  events: Array.from({ length: 7 }, (_, index) => ({
    at: `2026-10-0${index + 1}T05:00:00Z`,
    source: "admin",
    type: `event.${index + 1}`,
    actor: index === 0 ? "admin" : null,
    detail: {},
  })),
});

function setup(routes: Record<string, Route> = {}, startGrant = false) {
  const calls = stubFetch({ "GET /admin/billing/users/7": billing, ...routes });
  const onChanged = vi.fn();
  const onNavigate = vi.fn();
  render(
    <UserPlanSection user={maya} plans={plans} startGrant={startGrant} onChanged={onChanged} onNavigate={onNavigate} />,
  );
  return { calls, onChanged, onNavigate };
}

const posts = (calls: FetchCall[]) =>
  calls.filter((call) => call.method === "POST").map(({ path, body }) => ({ path, body }));

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(NOW));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("UserPlanSection", () => {
  it("shows the state, the periods with Revoke on the unexpired ones, and the newest five events", async () => {
    setup();
    expect(await screen.findByText("Standard, valid 8 Oct to 7 Nov 2026")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringContaining("Professional8 Nov to 7 Dec 2026Complimentary"),
      expect.stringContaining("StandardCurrent8 Oct to 7 Nov 2026Paid"),
      expect.stringContaining("Starter8 Sep to 8 Oct 2026PaidTest"),
    ]);
    expect(
      within(rows[0]).getByRole("button", { name: "Revoke the Professional period 8 Nov to 7 Dec 2026" }),
    ).toBeInTheDocument();
    expect(within(rows[2]).queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByText(/event\.1 · admin/)).toBeInTheDocument();
    expect(screen.queryByText(/event\.6/)).not.toBeInTheDocument();
  });

  it("revokes a period with a reason and reports the result", async () => {
    const after: UserBilling = { ...billing, periods: billing.periods.slice(1) };
    const { calls, onChanged } = setup({ "POST /admin/billing/periods/13/revoke": after });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Revoke the Professional period/ }));
    const confirm = screen.getByRole("group", {
      name: /Revoke maya\.chen's Professional period, 8 Nov to 7 Dec 2026\? Later periods move up\.$/,
    });
    expect(within(confirm).getByRole("textbox", { name: "Reason" })).toHaveFocus();

    await user.click(within(confirm).getByRole("button", { name: "Revoke" }));
    expect(within(confirm).getByRole("alert")).toHaveTextContent("Give a reason: it's kept in the billing record.");
    expect(posts(calls)).toEqual([]);

    await user.type(within(confirm).getByRole("textbox", { name: "Reason" }), "Granted by mistake{Enter}");
    await waitFor(() =>
      expect(onChanged).toHaveBeenCalledWith("Revoked the Professional period, 8 Nov to 7 Dec 2026."),
    );
    expect(posts(calls)).toEqual([
      { path: "/admin/billing/periods/13/revoke", body: { reason: "Granted by mistake" } },
    ]);
    expect(screen.queryByText("Professional")).not.toBeInTheDocument();
  });

  it("says that revoking a paid period refunds nothing, and shows a server refusal", async () => {
    setup({ "POST /admin/billing/periods/12/revoke": () => errorResponse(409, "The period has ended.") });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Revoke the Standard period/ }));
    const confirm = screen.getByRole("group", { name: /No refund is made: refund the payment in Billing\./ });
    await user.type(within(confirm).getByRole("textbox", { name: "Reason" }), "Asked to stop");
    await user.click(within(confirm).getByRole("button", { name: "Revoke" }));
    expect(await within(confirm).findByRole("alert")).toHaveTextContent("The period has ended.");
    await user.click(within(confirm).getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("button", { name: /Revoke the Standard period/ })).toHaveFocus();
  });

  it("ends an open-ended complimentary plan", async () => {
    const comp = {
      ...billing,
      plan: makePlanAccess(),
      periods: [],
      complimentary: { plan: PROFESSIONAL, reason: "Staff", granted_by: "admin", granted_at: "2026-10-01T05:00:00Z" },
    };
    const { calls, onChanged } = setup({
      "GET /admin/billing/users/7": comp,
      "POST /admin/billing/users/7/end-complimentary": {
        ...comp,
        plan: makeLapsedPlanAccess("none"),
        complimentary: null,
      },
    });
    const user = userEvent.setup();
    expect(await screen.findByText("Complimentary Professional, no end date")).toBeInTheDocument();
    expect(screen.getByText(/granted by admin on 1 Oct/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Change plan…" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "End maya.chen's complimentary Professional plan" }));
    await user.type(screen.getByRole("textbox", { name: "Reason" }), "Left the team");
    await user.click(screen.getByRole("button", { name: "End plan" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith("Ended maya.chen's complimentary plan."));
    expect(posts(calls)).toEqual([
      { path: "/admin/billing/users/7/end-complimentary", body: { reason: "Left the team" } },
    ]);
    expect(screen.getByText("No plan")).toBeInTheDocument();
  });

  it("grants a dated plan after the current one by default, and an open-ended one", async () => {
    const { calls, onChanged } = setup({ "POST /admin/billing/users/7/grants": billing });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Grant a complimentary plan…" }));
    const form = screen.getByRole("group", { name: "Grant a complimentary plan" });
    expect(within(form).getByRole("combobox", { name: "Plan" })).toHaveFocus();
    expect(within(form).getByRole("combobox", { name: "Plan" })).toHaveDisplayValue("Professional");
    expect(within(form).getByRole("radio", { name: "After the current plan" })).toBeChecked();

    await user.clear(within(form).getByRole("textbox", { name: "Days" }));
    await user.type(within(form).getByRole("textbox", { name: "Days" }), "0");
    await user.click(within(form).getByRole("button", { name: "Grant" }));
    expect(within(form).getByText("Enter 1 to 90 days.")).toBeInTheDocument();
    expect(within(form).getByText("Give a reason: it's kept in the billing record.")).toBeInTheDocument();

    await user.clear(within(form).getByRole("textbox", { name: "Days" }));
    await user.type(within(form).getByRole("textbox", { name: "Days" }), "45");
    await user.selectOptions(within(form).getByRole("combobox", { name: "Plan" }), "Starter");
    await user.type(within(form).getByRole("textbox", { name: "Reason" }), "Trial{Enter}");
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith("Granted maya.chen Starter for 45 days."));

    await user.click(screen.getByRole("button", { name: "Grant a complimentary plan…" }));
    const again = screen.getByRole("group", { name: "Grant a complimentary plan" });
    await user.click(within(again).getByRole("switch", { name: "No end date" }));
    expect(within(again).queryByRole("radiogroup", { name: "Start" })).not.toBeInTheDocument();
    await user.type(within(again).getByRole("textbox", { name: "Reason" }), "Family");
    await user.click(within(again).getByRole("button", { name: "Grant" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith("Granted maya.chen Professional with no end date."));
    expect(posts(calls)).toEqual([
      {
        path: "/admin/billing/users/7/grants",
        body: { plan_id: 1, days: 45, reason: "Trial", start: "after_current" },
      },
      {
        path: "/admin/billing/users/7/grants",
        body: { plan_id: 3, days: null, reason: "Family", start: "after_current" },
      },
    ]);
  });

  it("opens the grant form at once when asked, warns about starting now, and shows a refusal", async () => {
    setup({ "POST /admin/billing/users/7/grants": () => errorResponse(409, "The user has a current period.") }, true);
    const user = userEvent.setup();
    const form = await screen.findByRole("group", { name: "Grant a complimentary plan" });
    await user.click(within(form).getByRole("radio", { name: "Now" }));
    expect(
      within(form).getByText("The user has a current period: revoke it first, or start after it."),
    ).toBeInTheDocument();
    await user.type(within(form).getByRole("textbox", { name: "Reason" }), "Trial");
    await user.click(within(form).getByRole("button", { name: "Grant" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("The user has a current period.");
    await user.click(within(form).getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("button", { name: "Grant a complimentary plan…" })).toBeInTheDocument();
  });

  it("changes the plan of the unexpired periods at no charge", async () => {
    const { calls, onChanged } = setup({ "POST /admin/billing/users/7/change-plan": billing });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Change plan…" }));
    const form = screen.getByRole("group", { name: "Change plan at no charge" });
    await user.click(within(form).getByRole("button", { name: "Change plan" }));
    expect(within(form).getByText("Give a reason: it's kept in the billing record.")).toBeInTheDocument();
    await user.selectOptions(within(form).getByRole("combobox", { name: "Plan" }), "Standard");
    await user.type(within(form).getByRole("textbox", { name: "Reason" }), "Goodwill upgrade{Enter}");
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith(`Moved maya.chen's periods to ${STANDARD.name}.`));
    expect(posts(calls)).toEqual([
      { path: "/admin/billing/users/7/change-plan", body: { plan_id: 2, reason: "Goodwill upgrade" } },
    ]);
  });

  it("shows a refused plan change and goes to the user's payments", async () => {
    const { onNavigate } = setup({
      "POST /admin/billing/users/7/change-plan": () => errorResponse(409, "No period left to change."),
    });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Change plan…" }));
    const form = screen.getByRole("group", { name: "Change plan at no charge" });
    await user.type(within(form).getByRole("textbox", { name: "Reason" }), "Fix");
    await user.click(within(form).getByRole("button", { name: "Change plan" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("No period left to change.");
    await user.click(within(form).getByRole("button", { name: "Cancel" }));
    await user.click(screen.getByRole("button", { name: "View payments" }));
    expect(onNavigate).toHaveBeenCalledWith("billing/payments?user=7");
  });

  it("describes a lapsed user and a failed load with a retry", async () => {
    let failing = true;
    const lapsed = { ...billing, plan: makeLapsedPlanAccess("expired"), periods: [] };
    stubFetch({
      "GET /admin/billing/users/7": () => {
        if (!failing) return lapsed;
        failing = false;
        return errorResponse(500, "Billing is unavailable.");
      },
    });
    const user = userEvent.setup();
    render(<UserPlanSection user={maya} plans={plans} onChanged={() => {}} onNavigate={() => {}} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Billing is unavailable.");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Standard ended on 7 Oct 2026")).toBeInTheDocument();
    expect(screen.getByText("Ended")).toBeInTheDocument();
  });
});
