import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { AdminPlan } from "../../api/types";
import { makeAdminPlugin, makeSeededAdminPlans, makeSeededChatModels } from "../../test/adminTabs";
import { makeAdminPlan, makeBillingAdminConfig } from "../../test/billing";
import { makeAdminConfig } from "../../test/fixtures";
import { errorResponse, stubFetch, type Route } from "../../test/http";
import PlansTab from "./PlansTab";

const [starter, standard, professional] = makeSeededAdminPlans();
const trial = makeAdminPlan({
  id: 4,
  code: "team-trial",
  name: "Team trial",
  status: "retired",
  recommended: false,
  rank: 5,
  price_paise: 100_000,
  used: false,
  current_users: 0,
  scheduled_periods: 0,
  features: { files: true, images: true, plugins: [] },
  limits: { ...standard.limits, monthly_token_limit: 0 },
  chat_model_ids: null,
  default_chat_model_id: null,
  cost: {
    kept_paise: 97_640,
    worst_case_paise: 4_000,
    worst_share: 0.04,
    typical_paise: 1_600,
    typical_share: 0.016,
    unlimited: ["monthly_token_limit"],
  },
});
const pricey = { ...professional, cost: { ...professional.cost, worst_share: 0.62, worst_case_paise: 302_684 } };

function stubPlans(plans: AdminPlan[] | Route, extra: Record<string, Route> = {}) {
  return stubFetch({
    "GET /admin/plans": plans,
    "GET /admin/billing/config": makeBillingAdminConfig(),
    "GET /admin/models": makeSeededChatModels(),
    "GET /admin/plugins": [makeAdminPlugin()],
    "GET /admin/config": makeAdminConfig(),
    ...extra,
  });
}

const row = (name: string) => screen.getByRole("row", { name: new RegExp(`^${name}`) });

async function renderTab() {
  render(<PlansTab />);
  await screen.findByRole("table");
  await waitFor(() => expect(screen.getByRole("button", { name: "New plan" })).toBeEnabled());
}

describe("PlansTab", () => {
  it("lists the plans by rank with status, price, users, features, key limits and the cost estimate", async () => {
    stubPlans([professional, standard, starter, trial]);
    await renderTab();

    expect(
      screen
        .getAllByRole("row")
        .slice(1)
        .map((r) => within(r).getAllByRole("cell")[0].textContent),
    ).toEqual(["Team trialteam-trial", "Starterstarter", "Standardstandard", "Professionalprofessional"]);

    const starterRow = within(row("Starter"));
    expect(starterRow.getByText("Active")).toBeInTheDocument();
    expect(starterRow.getByText("₹500")).toBeInTheDocument();
    expect(starterRow.getByText("per 30 days")).toBeInTheDocument();
    expect(starterRow.getByText("3 · 0 next · 0 free")).toBeInTheDocument();
    expect(starterRow.getByText("Chat only")).toBeInTheDocument();
    expect(
      starterRow.getByText("≈1,600 answers with Claude Haiku 5.5 · 50 docs · 200 MB · 2 models"),
    ).toBeInTheDocument();
    expect(starterRow.getByText("₹172 · 35%")).toBeInTheDocument();
    expect(starterRow.getByRole("meter", { name: "Worst case" })).toHaveAttribute(
      "aria-valuetext",
      "35% of what you keep",
    );
    expect(starterRow.getByRole("button", { name: "Delete Starter" })).toBeDisabled();
    expect(starterRow.getByText("Starter has been used: retire it instead of deleting it.")).toBeInTheDocument();

    const standardRow = within(row("Standard"));
    expect(standardRow.getByText("Recommended")).toBeInTheDocument();
    expect(
      standardRow.getByText("≈260 answers with Claude Sonnet 5.5 · 200 docs · 1,000 MB · 3 models"),
    ).toBeInTheDocument();
    expect(standardRow.getByText("Files")).toBeInTheDocument();

    const proRow = within(row("Professional"));
    expect(
      proRow.getByText("≈290 answers with Claude Opus 5.5 · 500 docs · 3,000 MB · all models"),
    ).toBeInTheDocument();
    expect(proRow.getAllByText(/^(Files|Images|Gmail)$/).map((chip) => chip.textContent)).toEqual([
      "Files",
      "Images",
      "Gmail",
    ]);
    expect(proRow.getByText("2 · 1 next · 1 free")).toBeInTheDocument();

    const trialRow = within(row("Team trial"));
    expect(trialRow.getByText("Retired")).toBeInTheDocument();
    expect(trialRow.getByText("No ceiling")).toBeInTheDocument();
    expect(trialRow.queryByRole("meter")).not.toBeInTheDocument();
    expect(trialRow.getByText(/^Unlimited chat · /)).toBeInTheDocument();
    expect(trialRow.getByRole("button", { name: "Delete Team trial" })).toBeEnabled();

    expect(screen.getByText(/₹88 to the dollar, against what you keep after Razorpay's fee/)).toBeInTheDocument();
  });

  it("warns when the worst case is 60% or more of what the owner keeps", async () => {
    stubPlans([pricey]);
    await renderTab();
    const proRow = within(row("Professional"));
    expect(proRow.getByText("High")).toBeInTheDocument();
    expect(proRow.getByRole("meter", { name: "Worst case" })).toHaveClass("ui-meter-warning");
  });

  it("deletes an unused plan after confirmation and shows a refusal", async () => {
    const calls = stubPlans([starter, trial], {
      "DELETE /admin/plans/4": () => new Response(null, { status: 204 }),
    });
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByRole("button", { name: "Delete Team trial" }));
    await user.click(screen.getByRole("button", { name: "Delete plan" }));
    await waitFor(() => expect(screen.queryByRole("row", { name: /^Team trial/ })).not.toBeInTheDocument());
    expect(screen.getByRole("status")).toHaveTextContent("Deleted Team trial.");
    expect(calls.filter((call) => call.method === "DELETE").map((call) => call.path)).toEqual(["/admin/plans/4"]);
  });

  it("keeps the plan when the server refuses the delete", async () => {
    stubPlans([trial], {
      "DELETE /admin/plans/4": () => errorResponse(409, "This plan has been used. Retire it instead."),
    });
    const user = userEvent.setup();
    await renderTab();
    await user.click(screen.getByRole("button", { name: "Delete Team trial" }));
    await user.click(screen.getByRole("button", { name: "Delete plan" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This plan has been used. Retire it instead.");
  });

  it("says users can't pay without plans", async () => {
    stubPlans([]);
    render(<PlansTab />);
    expect(await screen.findByText("No plans")).toBeInTheDocument();
    expect(screen.getByText("Users can't pay until you create one.")).toBeInTheDocument();
  });

  it("shows a failed load with a retry, and what the editor still needs", async () => {
    let failing = true;
    stubPlans(
      () => {
        if (!failing) return [starter];
        failing = false;
        return errorResponse(500, "The database is unavailable.");
      },
      { "GET /admin/plugins": () => errorResponse(500, "Couldn't read the plugins.") },
    );
    const user = userEvent.setup();
    render(<PlansTab />);
    expect(await screen.findByText("The database is unavailable.")).toBeInTheDocument();
    expect(await screen.findByText("Couldn't read the plugins.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New plan" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("row", { name: /^Starter/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit Starter" })).toBeDisabled();
  });

  it("adds a created plan and moves the recommended flag to a saved one", async () => {
    const created = makeAdminPlan({
      id: 9,
      code: "team",
      name: "Team",
      rank: 40,
      price_paise: 900_000,
      recommended: true,
    });
    stubPlans([starter, standard], {
      "POST /admin/plans": { plan: created, applied_periods: 0 },
      "PATCH /admin/plans/1": { plan: { ...starter, name: "Starter plus" }, applied_periods: 3 },
    });
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByRole("button", { name: "New plan" }));
    const dialog = screen.getByRole("dialog", { name: "New plan" });
    await user.type(within(dialog).getByRole("textbox", { name: "Code" }), "team");
    await user.type(within(dialog).getByRole("textbox", { name: "Name" }), "Team");
    await user.type(within(dialog).getByRole("textbox", { name: "Price per 30 days" }), "9000");
    await user.click(within(dialog).getByRole("button", { name: "Create plan" }));

    expect(await screen.findByRole("row", { name: /^Team/ })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Created Team.");
    expect(within(row("Standard")).queryByText("Recommended")).not.toBeInTheDocument();
    expect(within(row("Team")).getByText("Recommended")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Edit Starter" }));
    await user.click(
      within(screen.getByRole("dialog", { name: "Edit plan: Starter" })).getByRole("button", { name: "Save changes" }),
    );
    expect(await screen.findByRole("row", { name: /^Starter plus/ })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Saved Starter plus. Also applied to 3 current or scheduled periods.",
    );
    await user.click(screen.getByRole("button", { name: "Edit Starter plus" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
