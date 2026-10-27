import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { AdminUser } from "../../api/types";
import { makeAdminUserPlan, makeSeededAdminPlans } from "../../test/adminTabs";
import { makeBillingAdminConfig, makeUserBilling, STARTER } from "../../test/billing";
import { makeAdminConfig, makeAdminModel, makeAdminUser, makeLimits } from "../../test/fixtures";
import { errorResponse, stubFetch, type Route } from "../../test/http";
import UsersTab from "./UsersTab";

const usage = (chat: number, images: number, cost_usd: number) => ({
  ...makeAdminUser().usage,
  chat_tokens: chat,
  chat_allowance_used: chat,
  images,
  cost_usd,
});
const admin = makeAdminUser({
  id: 1,
  username: "admin",
  is_admin: true,
  last_login_at: "2026-10-20T08:00:00Z",
  usage: usage(5_000, 0, 0.05),
  limits: makeLimits({
    monthly_token_limit: 0,
    rate_limit_per_min: 0,
    max_upload_mb: 100,
    max_documents: 0,
    max_storage_mb: 0,
    monthly_image_limit: 0,
  }),
  plan: makeAdminUserPlan({ state: "exempt", plan: null, valid_through: null, paid_through: null }),
});
const maya = makeAdminUser({ plan: makeAdminUserPlan() });
const sam = makeAdminUser({
  id: 9,
  username: "sam.ortiz",
  is_active: false,
  restrict_models: true,
  allowed_model_ids: [],
  last_login_at: null,
  limits: makeLimits({ rate_limit_per_min: 0 }),
  usage: usage(1_900_000, 50, 12.5),
  plan: makeAdminUserPlan({ state: "none", plan: null, valid_through: null, paid_through: null }),
});
const lee = makeAdminUser({
  id: 10,
  username: "lee.park",
  restrict_models: true,
  allowed_model_ids: [1, 2],
  limits: makeLimits({ max_storage_mb: 3_000 }),
  plan: makeAdminUserPlan({
    state: "expired",
    plan: STARTER,
    valid_through: "2026-10-11",
    ended_at: "2026-10-11T18:30:00Z",
  }),
});
const ana = makeAdminUser({
  id: 11,
  username: "ana.lima",
  billing_tester: true,
  no_models: true,
  restrict_models: true,
  allowed_model_ids: [1],
  plan: makeAdminUserPlan({ valid_through: "2026-10-22", expiring_soon: true, test_mode: true, complimentary: true }),
});

function stubAdmin(users: AdminUser[] | Route, extra: Record<string, Route> = {}) {
  return stubFetch({
    "GET /admin/users": users,
    "GET /admin/config": makeAdminConfig(),
    "GET /admin/plans": makeSeededAdminPlans(),
    "GET /admin/billing/config": makeBillingAdminConfig(),
    "GET /admin/models": [
      makeAdminModel(),
      makeAdminModel({ id: 2, display_name: "GPT-5.5", litellm_model: "openai/gpt-5.5", is_default: false }),
      makeAdminModel({
        id: 3,
        display_name: "Embeddings",
        litellm_model: "openai/text-embedding-3-large",
        kind: "embedding",
      }),
    ],
    ...extra,
  });
}

const row = (username: string) => screen.getByRole("row", { name: new RegExp(username.replace(".", "\\.")) });

const noParams = new URLSearchParams();

async function renderTab(params = noParams, onNavigate = vi.fn()) {
  const onChanged = vi.fn();
  render(<UsersTab onChanged={onChanged} params={params} onNavigate={onNavigate} />);
  await screen.findByRole("table");
  await waitFor(() => expect(screen.getByRole("button", { name: "New user" })).toBeEnabled());
  return onChanged;
}

describe("UsersTab", () => {
  it("lists every user with their plan, this period's use, limits and model access", async () => {
    stubAdmin([admin, maya, sam, lee, ana]);
    await renderTab();

    expect(screen.getByRole("heading", { name: "Users" })).toBeInTheDocument();
    expect(screen.getAllByRole("row")).toHaveLength(6);
    expect(screen.getByRole("columnheader", { name: "Chat this period" })).toBeInTheDocument();

    const adminRow = within(row("admin"));
    expect(adminRow.getByText("admin", { selector: ".badge" })).toBeInTheDocument();
    expect(adminRow.getByText("Exempt")).toBeInTheDocument();
    expect(adminRow.getByText("Unlimited")).toBeInTheDocument();
    expect(adminRow.getByText("All models")).toBeInTheDocument();
    expect(adminRow.getByRole("button", { name: "Edit" })).toBeDisabled();
    expect(adminRow.getByRole("button", { name: "Delete" })).toBeDisabled();
    expect(adminRow.getByText("Managed in .env")).toBeInTheDocument();

    const mayaRow = within(row("maya.chen"));
    expect(mayaRow.getByText("Standard")).toBeInTheDocument();
    expect(mayaRow.getByText("until 7 Nov")).toBeInTheDocument();
    expect(mayaRow.getByText("20 / min")).toBeInTheDocument();
    expect(mayaRow.getByText("25 MB per file")).toBeInTheDocument();
    expect(mayaRow.getByText("$1.42")).toBeInTheDocument();
    expect(mayaRow.getByText("Plan's models")).toBeInTheDocument();
    expect(mayaRow.getByRole("button", { name: "Edit maya.chen" })).toBeEnabled();
    expect(mayaRow.getByRole("button", { name: "Delete maya.chen" })).toBeEnabled();

    const samRow = within(row("sam.ortiz"));
    expect(samRow.getByText("inactive")).toBeInTheDocument();
    expect(samRow.getByText("No plan")).toBeInTheDocument();
    expect(samRow.getByText("No models")).toBeInTheDocument();
    expect(samRow.getByText("Never")).toBeInTheDocument();
    expect(samRow.getByText("Unlimited")).toBeInTheDocument();
    expect(samRow.getByText(/standard tokens, 95% used\. Almost used up/)).toBeInTheDocument();
    expect(samRow.getByText(/images, 100% used\. Limit reached/)).toBeInTheDocument();

    const leeRow = within(row("lee.park"));
    expect(leeRow.getByText("2 models")).toHaveAttribute("title", "Claude Opus 5.5, GPT-5.5");
    expect(leeRow.getByText("Ended 11 Oct")).toBeInTheDocument();
    expect(leeRow.getByText("Starter")).toBeInTheDocument();
    // The storage limit as the plan writes it, the amount used in bytes.
    expect(leeRow.getByText("4/100 docs · 3.1 MB/3,000 MB")).toBeInTheDocument();
    expect(leeRow.getByText("4 of 100 documents, 3.1 MB of 3,000 MB storage")).toBeInTheDocument();

    const anaRow = within(row("ana.lima"));
    expect(anaRow.getByText("tester")).toBeInTheDocument();
    expect(anaRow.getByText("Complimentary Standard")).toBeInTheDocument();
    expect(anaRow.getByText("Ends soon")).toBeInTheDocument();
    expect(anaRow.getByText("Test")).toBeInTheDocument();
    expect(anaRow.getByText("No model left")).toBeInTheDocument();
    expect(screen.queryByText(/No other users yet/)).not.toBeInTheDocument();
  });

  it("filters by plan state with counts and sorts by plan", async () => {
    stubAdmin([admin, maya, sam, lee, ana]);
    const user = userEvent.setup();
    await renderTab();
    const filters = screen.getByRole("radiogroup", { name: "Show" });
    expect(
      within(filters)
        .getAllByRole("radio")
        .map((radio) => radio.closest("label")?.textContent),
    ).toEqual(["All 5", "Active 2", "Expiring 1", "Lapsed 1", "Complimentary 1", "No plan 1"]);
    const names = () => screen.getAllByTitle(/^[a-z.]+$/).map((name) => name.textContent);

    await user.click(within(filters).getByRole("radio", { name: "Lapsed 1" }));
    expect(names()).toEqual(["lee.park"]);
    await user.click(within(filters).getByRole("radio", { name: "Expiring 1" }));
    expect(names()).toEqual(["ana.lima"]);
    await user.click(within(filters).getByRole("radio", { name: "All 5" }));

    const header = screen.getByRole("columnheader", { name: /Plan/ });
    expect(header).not.toHaveAttribute("aria-sort");
    await user.click(within(header).getByRole("button"));
    expect(header).toHaveAttribute("aria-sort", "ascending");
    expect(names()).toEqual(["admin", "ana.lima", "maya.chen", "lee.park", "sam.ortiz"]);
    await user.click(within(header).getByRole("button"));
    expect(header).toHaveAttribute("aria-sort", "descending");
    expect(names()).toEqual(["sam.ortiz", "lee.park", "maya.chen", "ana.lima", "admin"]);
  });

  it("says so when no user matches a filter", async () => {
    stubAdmin([admin, maya]);
    const user = userEvent.setup();
    await renderTab();
    await user.click(screen.getByRole("radio", { name: "No plan 0" }));
    expect(screen.getByText("No user matches this filter.")).toBeInTheDocument();
  });

  it("opens the user named in the hash, with the grant form when asked, and clears the hash", async () => {
    stubAdmin([admin, maya], { "GET /admin/billing/users/7": makeUserBilling() });
    const onNavigate = vi.fn();
    await renderTab(new URLSearchParams("user=7&grant=1"), onNavigate);
    const dialog = await screen.findByRole("dialog", { name: "Edit maya.chen" });
    expect(await within(dialog).findByRole("group", { name: "Grant a complimentary plan" })).toBeInTheDocument();
    expect(onNavigate).toHaveBeenCalledWith("users");
  });

  it("ignores a hash naming the admin or an unknown user", async () => {
    stubAdmin([admin, maya]);
    const onNavigate = vi.fn();
    await renderTab(new URLSearchParams("user=1"), onNavigate);
    await waitFor(() => expect(onNavigate).toHaveBeenCalledWith("users"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("warns before deleting a user with paid time left", async () => {
    stubAdmin([admin, maya]);
    const user = userEvent.setup();
    await renderTab();
    await user.click(screen.getByRole("button", { name: "Delete maya.chen" }));
    expect(screen.getByRole("group", { name: /Delete maya\.chen/ })).toHaveTextContent(
      "maya.chen has a paid plan until 7 Nov 2026. No refund is made automatically.",
    );
  });

  it("invites creating the first account when only the admin exists", async () => {
    stubAdmin([admin]);
    await renderTab();
    expect(screen.getByText("No other users yet. Use “New user” to create the first account.")).toBeInTheDocument();
  });

  it("shows a failed load with a retry", async () => {
    const replies = [errorResponse(500, "The database is unavailable."), [admin, maya]];
    stubAdmin(() => replies.shift());
    const user = userEvent.setup();
    render(<UsersTab onChanged={() => {}} params={noParams} onNavigate={() => {}} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("The database is unavailable.");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("row", { name: /maya\.chen/ })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("deletes a user after confirmation", async () => {
    const calls = stubAdmin([admin, maya], { "DELETE /admin/users/7": () => new Response(null, { status: 204 }) });
    const user = userEvent.setup();
    const onChanged = await renderTab();

    await user.click(screen.getByRole("button", { name: "Delete maya.chen" }));
    const confirm = screen.getByRole("group", { name: /^Delete maya\.chen and all of their documents\?/ });
    expect(within(confirm).getByRole("button", { name: "Cancel" })).toHaveFocus();
    await user.click(within(confirm).getByRole("button", { name: "Delete user" }));

    await waitFor(() => expect(screen.queryByRole("row", { name: /maya\.chen/ })).not.toBeInTheDocument());
    expect(screen.getByRole("status")).toHaveTextContent("Deleted maya.chen and their documents.");
    expect(calls.filter((call) => call.method === "DELETE").map((call) => call.path)).toEqual(["/admin/users/7"]);
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it("keeps the user and shows the error when deleting fails", async () => {
    stubAdmin([admin, maya], { "DELETE /admin/users/7": () => errorResponse(500, "Couldn't remove the documents.") });
    const user = userEvent.setup();
    const onChanged = await renderTab();

    await user.click(screen.getByRole("button", { name: "Delete maya.chen" }));
    await user.click(screen.getByRole("button", { name: "Delete user" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't remove the documents.");
    expect(row("maya.chen")).toBeInTheDocument();
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("cancels a delete with Escape and returns focus to the trigger", async () => {
    stubAdmin([admin, maya]);
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByRole("button", { name: "Delete maya.chen" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("button", { name: "Delete user" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete maya.chen" })).toHaveFocus();
  });

  it("adds a created user to the list", async () => {
    const created = makeAdminUser({ id: 15, username: "ana.lima" });
    stubAdmin([admin, maya], { "POST /admin/users": created });
    const user = userEvent.setup();
    const onChanged = await renderTab();

    await user.click(screen.getByRole("button", { name: "New user" }));
    const dialog = screen.getByRole("dialog", { name: "New user" });
    await user.type(within(dialog).getByRole("textbox", { name: "Username" }), "ana.lima");
    await user.type(within(dialog).getByLabelText("Password"), "correct horse battery");
    await user.click(within(dialog).getByRole("button", { name: "Create user" }));

    expect(await screen.findByRole("row", { name: /ana\.lima/ })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Created ana.lima.");
    expect(screen.getAllByTitle(/^(admin|ana\.lima|maya\.chen)$/).map((name) => name.textContent)).toEqual([
      "admin",
      "ana.lima",
      "maya.chen",
    ]);
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it("saves an edited user in place", async () => {
    stubAdmin([admin, maya], { "PATCH /admin/users/7": makeAdminUser({ is_active: false }) });
    const user = userEvent.setup();
    const onChanged = await renderTab();

    await user.click(screen.getByRole("button", { name: "Edit maya.chen" }));
    const dialog = screen.getByRole("dialog", { name: "Edit maya.chen" });
    await user.click(within(dialog).getByRole("switch", { name: "Active" }));
    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(within(row("maya.chen")).getByText("inactive")).toBeInTheDocument());
    expect(screen.getByRole("status")).toHaveTextContent("Saved changes to maya.chen.");
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("can't create or edit users until the default limits have loaded", async () => {
    stubFetch({
      "GET /admin/users": [admin, maya],
      "GET /admin/config": () => errorResponse(500, "Config unavailable."),
      "GET /admin/models": [],
    });
    render(<UsersTab onChanged={() => {}} params={noParams} onNavigate={() => {}} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Config unavailable.");
    expect(await screen.findByRole("button", { name: "Edit maya.chen" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "New user" })).toBeDisabled();
  });
});
