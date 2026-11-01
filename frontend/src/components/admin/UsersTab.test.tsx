import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { AdminUser } from "../../api/types";
import { makeSignupStatus } from "../../test/adminSignup";
import { makeAdminUserPlan, makeSeededAdminPlans, markdownResponse } from "../../test/adminTabs";
import { makeAuthConfig } from "../../test/auth";
import { makeAdminOrder, makeBillingAdminConfig, makeUserBilling, STARTER } from "../../test/billing";
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
  signup_source: "env",
  created_at: "2026-09-01T06:00:00Z",
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
const maya = makeAdminUser({
  plan: makeAdminUserPlan(),
  signup_source: "email",
  created_at: "2026-10-18T09:00:00Z",
  email: "maya@example.com",
});
const sam = makeAdminUser({
  id: 9,
  username: "sam.ortiz",
  is_active: false,
  restrict_models: true,
  allowed_model_ids: [],
  last_login_at: null,
  created_at: "2026-10-19T10:00:00Z",
  signup_source: "google",
  email: "sam.ortiz@gmail.com",
  limits: makeLimits({ rate_limit_per_min: 0 }),
  usage: usage(1_900_000, 50, 12.5),
  plan: makeAdminUserPlan({ state: "none", plan: null, valid_through: null, paid_through: null }),
});
const lee = makeAdminUser({
  id: 10,
  username: "lee.park",
  created_at: "2026-09-20T10:00:00Z",
  pending_email: { address: "lee@studio-nine.in", sent_at: "2026-10-19T08:00:00Z" },
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
  created_at: "2026-10-05T10:00:00Z",
  signup_source: "email",
  email: "ana@example.org",
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
    "GET /admin/signup-settings": makeSignupStatus(),
    "GET /auth/config": makeAuthConfig(),
    "GET /legal/privacy.md": () => markdownResponse("# Privacy policy\n\nWhat we keep."),
    "GET /legal/terms.md": () => markdownResponse("# Terms of service\n\nThe rules."),
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
const usersCard = () => within(screen.getByRole("region", { name: "Users" }));
/** The usernames in the table, top to bottom. */
const names = () => screen.getAllByTitle(/^[a-z.]+$/).map((name) => name.textContent);

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
    expect(adminRow.getAllByText("Managed in .env")).toHaveLength(2);
    expect(adminRow.getByText("Env")).toBeInTheDocument();

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

  it("says so when no user matches the filters, and clears them", async () => {
    stubAdmin([admin, maya]);
    const user = userEvent.setup();
    await renderTab();
    await user.click(screen.getByRole("radio", { name: "No plan 0" }));
    expect(screen.getByRole("cell", { name: /^No users match\./ })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(names()).toEqual(["maya.chen", "admin"]);
    expect(screen.getByRole("radio", { name: "All 2" })).toBeChecked();
    expect(screen.getByRole("searchbox", { name: /Search users/ })).toHaveFocus();
  });

  it("shows each user's email under the name and how they signed up", async () => {
    const kim = makeAdminUser({ id: 12, username: "kim.cho", created_at: "2026-10-01T10:00:00Z" });
    stubAdmin([admin, maya, sam, lee, kim]);
    await renderTab();

    expect(within(row("maya.chen")).getByText("maya@example.com")).toBeInTheDocument();
    expect(within(row("maya.chen")).getByText("Confirmed:")).toHaveClass("sr-only");
    expect(within(row("maya.chen")).getByTitle("maya@example.com, confirmed")).toBeInTheDocument();
    expect(within(row("lee.park")).getByText("Pending: lee@studio-nine.in")).toBeInTheDocument();
    expect(within(row("kim.cho")).getByText("No email")).toBeInTheDocument();

    const signedUp = (username: string) => row(username).querySelector(".admin-source")?.textContent?.trim();
    expect(signedUp("maya.chen")).toBe("Email18 Oct 2026");
    expect(signedUp("sam.ortiz")).toBe("Google19 Oct 2026");
    expect(signedUp("lee.park")).toBe("Admin20 Sept 2026");
    expect(signedUp("admin")).toBe("Env1 Sept 2026");
    expect(row("sam.ortiz").querySelector(".admin-source .auth-google-g")).not.toBeNull();
  });

  it("lists the newest sign-ups first and sorts by sign-up date either way", async () => {
    stubAdmin([admin, maya, sam, lee, ana]);
    const user = userEvent.setup();
    await renderTab();

    const header = screen.getByRole("columnheader", { name: /Signed up/ });
    expect(header).toHaveAttribute("aria-sort", "descending");
    expect(names()).toEqual(["sam.ortiz", "maya.chen", "ana.lima", "lee.park", "admin"]);
    await user.click(within(header).getByRole("button"));
    expect(header).toHaveAttribute("aria-sort", "ascending");
    expect(names()).toEqual(["admin", "lee.park", "ana.lima", "maya.chen", "sam.ortiz"]);

    await user.click(within(screen.getByRole("columnheader", { name: /Plan/ })).getByRole("button"));
    expect(header).not.toHaveAttribute("aria-sort");
    await user.click(within(header).getByRole("button"));
    expect(header).toHaveAttribute("aria-sort", "descending");
    expect(names()[0]).toBe("sam.ortiz");
  });

  it("searches the loaded list by username or email, pending addresses included", async () => {
    stubAdmin([admin, maya, sam, lee, ana]);
    const user = userEvent.setup();
    await renderTab();
    const search = screen.getByRole("searchbox", { name: /Search users/ });
    expect(search).toHaveAttribute("placeholder", "Search name or email");

    await user.type(search, "GMAIL");
    expect(names()).toEqual(["sam.ortiz"]);
    expect(screen.getByRole("radio", { name: "All 1" })).toBeChecked();
    await user.clear(search);
    await user.type(search, "studio-nine");
    expect(names()).toEqual(["lee.park"]);
    await user.clear(search);
    await user.type(search, "ana.");
    expect(names()).toEqual(["ana.lima"]);
  });

  it("filters by how users signed up, the last 7 days' sign-ups included", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-20T09:30:00Z"));
    stubAdmin([admin, maya, sam, lee, ana]);
    const user = userEvent.setup();
    await renderTab();
    const select = screen.getByRole("combobox", { name: "Signed up" });
    expect([...select.querySelectorAll("option")].map((option) => option.textContent)).toEqual([
      "All sign-ups",
      "Last 7 days",
      "Email",
      "Google",
      "Admin",
    ]);

    await user.selectOptions(select, "Last 7 days");
    expect(names()).toEqual(["sam.ortiz", "maya.chen"]);
    await user.selectOptions(select, "Email");
    expect(names()).toEqual(["maya.chen", "ana.lima"]);
    await user.selectOptions(select, "Google");
    expect(names()).toEqual(["sam.ortiz"]);
    await user.selectOptions(select, "Admin");
    expect(names()).toEqual(["lee.park"]);
    vi.useRealTimers();
  });

  it("opens the last 7 days' sign-ups from the Sign-up card", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-20T09:30:00Z"));
    stubAdmin([admin, maya, sam, lee, ana]);
    const user = userEvent.setup();
    await renderTab();
    await user.click(screen.getByRole("radio", { name: "Lapsed 1" }));
    await user.type(screen.getByRole("searchbox", { name: /Search users/ }), "lee");

    await user.click(await screen.findByRole("button", { name: "14 in 7 days · 2 paid" }));
    expect(screen.getByRole("combobox", { name: "Signed up" })).toHaveValue("recent");
    expect(screen.getByRole("combobox", { name: "Signed up" })).toHaveFocus();
    expect(screen.getByRole("searchbox", { name: /Search users/ })).toHaveValue("");
    expect(names()).toEqual(["sam.ortiz", "maya.chen"]);
    vi.useRealTimers();
  });

  it("gives the user form the configured password minimum, or 8 without it", async () => {
    stubAdmin([admin, maya], { "GET /auth/config": makeAuthConfig({ password_min_length: 12 }) });
    const user = userEvent.setup();
    await renderTab();
    await user.click(screen.getByRole("button", { name: "New user" }));
    await waitFor(() =>
      expect(screen.getByLabelText("Password")).toHaveAccessibleDescription(
        "At least 12 characters, not a common or leaked password.",
      ),
    );
    cleanup();

    stubAdmin([admin, maya], { "GET /auth/config": () => errorResponse(503, "Unavailable") });
    await renderTab();
    await user.click(screen.getByRole("button", { name: "New user" }));
    expect(screen.getByLabelText("Password")).toHaveAccessibleDescription(
      "At least 8 characters, not a common or leaked password.",
    );
  });

  it("opens the user named in the hash, with the grant form when asked, and clears the hash", async () => {
    stubAdmin([admin, maya], { "GET /admin/billing/users/7": makeUserBilling() });
    const onNavigate = vi.fn();
    await renderTab(new URLSearchParams("user=7&grant=1"), onNavigate);
    const dialog = await screen.findByRole("dialog", { name: "Edit maya.chen" });
    expect(await within(dialog).findByRole("group", { name: "Grant a complimentary plan" })).toBeInTheDocument();
    expect(onNavigate).toHaveBeenCalledWith("users");
  });

  it("warns before deleting a user whose UPI payment waits for a check", async () => {
    const waiting = makeAdminOrder({
      status: "submitted",
      channel: "upi_direct",
      amount_paise: 200_000,
      user: { id: maya.id, username: "maya.chen" },
    });
    stubAdmin([admin, maya], { "GET /admin/billing/orders?status=submitted&page=1": { items: [waiting], total: 1 } });
    const user = userEvent.setup();
    await renderTab();
    await user.click(screen.getByRole("button", { name: "Delete maya.chen" }));
    await waitFor(() =>
      expect(screen.getByRole("group", { name: /Delete maya\.chen/ })).toHaveTextContent(
        "maya.chen has a UPI payment of ₹2,000 waiting for your check. Confirm or reject it first.",
      ),
    );
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
    expect(usersCard().getByRole("status")).toHaveTextContent("Deleted maya.chen and their documents.");
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
    const created = makeAdminUser({ id: 15, username: "ana.lima", created_at: "2026-10-20T09:00:00Z" });
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
    expect(usersCard().getByRole("status")).toHaveTextContent("Created ana.lima.");
    expect(names()).toEqual(["ana.lima", "maya.chen", "admin"]);
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
    expect(usersCard().getByRole("status")).toHaveTextContent("Saved changes to maya.chen.");
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("can't create or edit users until the default limits have loaded", async () => {
    stubFetch({
      "GET /admin/users": [admin, maya],
      "GET /admin/config": () => errorResponse(500, "Config unavailable."),
      "GET /admin/models": [],
      "GET /admin/signup-settings": makeSignupStatus(),
    });
    render(<UsersTab onChanged={() => {}} params={noParams} onNavigate={() => {}} />);

    expect(await usersCard().findByRole("alert")).toHaveTextContent("Config unavailable.");
    expect(await screen.findByRole("button", { name: "Edit maya.chen" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "New user" })).toBeDisabled();
  });
});
