import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";
import type { AdminModel, AdminUser, AdminUserCreate, AdminUserUpdate } from "../../api/types";
import { makeAdminUserPlan, makeSeededAdminPlans } from "../../test/adminTabs";
import { makeUserBilling } from "../../test/billing";
import { makeAdminConfig, makeAdminModel, makeAdminUser, makeLimits } from "../../test/fixtures";
import { deferred, errorResponse, jsonResponse, stubFetch, type FetchCall, type Route } from "../../test/http";
import UserFormModal from "./UserFormModal";

const chatModels: AdminModel[] = [
  makeAdminModel(),
  makeAdminModel({
    id: 2,
    display_name: "GPT-5.5",
    litellm_model: "openai/gpt-5.5",
    enabled: false,
    is_default: false,
  }),
];
const plans = makeSeededAdminPlans();

type Props = ComponentProps<typeof UserFormModal>;

function defaultProps(): Props {
  return {
    user: null,
    config: makeAdminConfig(),
    chatModels,
    modelsError: null,
    plans,
    paymentsOpen: true,
    passwordMin: 8,
    onClose: vi.fn(),
    onSaved: vi.fn(),
    onPlanChanged: vi.fn(),
    onNavigate: vi.fn(),
  };
}

function renderForm(overrides: Partial<Props> = {}) {
  const props: Props = { ...defaultProps(), ...overrides };
  render(<UserFormModal {...props} />);
  return props;
}

/** The read-only sign-in facts as term → text. */
function facts(): Record<string, string> {
  const list = screen.getByText("Signed up").closest("dl") as HTMLElement;
  return Object.fromEntries(
    within(list)
      .getAllByRole("term")
      .map((term) => [term.textContent, term.nextElementSibling?.textContent?.trim() ?? ""]),
  );
}

// Typing speed doesn't matter to the form: no delay between keystrokes keeps the long fields fast on a busy machine.
const setupUser = () => userEvent.setup({ delay: null });
const field = (name: string) => screen.getByRole("textbox", { name });
const passwordField = (label = "Password") => screen.getByLabelText(label);
const CHAT = "Chat allowance (standard tokens)";

const refused = (reason: string, detail: string) =>
  jsonResponse({ detail, code: "password_rejected", reason }, { status: 422 });

function sent(calls: FetchCall[], method: string): unknown[] {
  return calls.filter((call) => call.method === method).map((call) => call.body);
}

async function fillAccount(user: ReturnType<typeof setupUser>, username = "maya.chen") {
  await user.type(field("Username"), username);
  await user.type(passwordField(), "correct horse battery");
}

describe("UserFormModal: creating", () => {
  it("starts with blank overrides that show the plan's values", () => {
    renderForm();
    expect(screen.getByRole("dialog", { name: "New user" })).toBeInTheDocument();
    expect(field("Username")).toHaveFocus();
    expect(field(CHAT)).toHaveValue("");
    expect(field(CHAT)).toHaveAttribute("placeholder", "Plan: 2,000,000");
    expect(field("Images per period")).toHaveAttribute("placeholder", "Plan: 50");
    expect(field("Requests per minute")).toHaveAttribute("placeholder", "Plan: 20");
    expect(field("Max file size (MB)")).toHaveAccessibleDescription("1 to 100 MB per file.");
    expect(screen.queryByText("Custom")).not.toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Active" })).toBeChecked();
    expect(screen.getByRole("switch", { name: "Start with a complimentary plan" })).not.toBeChecked();
    expect(screen.getByRole("switch", { name: "Can make test payments" })).not.toBeChecked();
    expect(screen.getByRole("switch", { name: "Restrict to selected models" })).not.toBeChecked();
    expect(screen.queryByRole("group", { name: "Allowed chat models" })).not.toBeInTheDocument();
  });

  it("requires a username and a password, and focuses the first invalid field", async () => {
    const calls = stubFetch({});
    const user = setupUser();
    const props = renderForm();

    await user.click(screen.getByRole("button", { name: "Create user" }));

    expect(field("Username")).toHaveAttribute("aria-invalid", "true");
    expect(field("Username")).toHaveAccessibleDescription(expect.stringContaining("This field is required."));
    expect(passwordField()).toHaveAccessibleDescription(expect.stringContaining("This field is required."));
    expect(field("Username")).toHaveFocus();
    expect(calls).toHaveLength(0);
    expect(props.onSaved).not.toHaveBeenCalled();
  });

  it("checks the username's characters and the password's length", async () => {
    stubFetch({});
    const user = setupUser();
    renderForm();

    await user.type(field("Username"), "maya chen!");
    await user.type(passwordField(), "short");
    await user.click(screen.getByRole("button", { name: "Create user" }));

    expect(field("Username")).toHaveAccessibleDescription(
      expect.stringContaining("Use only letters, digits, dots, dashes and underscores."),
    );
    expect(passwordField()).toHaveAccessibleDescription(expect.stringContaining("Use at least 8 characters."));

    await user.clear(field("Username"));
    await user.type(field("Username"), "ab");
    await user.click(screen.getByRole("button", { name: "Create user" }));
    expect(field("Username")).toHaveAccessibleDescription(expect.stringContaining("Use at least 3 characters."));
  });

  it("accepts a password of exactly 8 characters", async () => {
    const calls = stubFetch({ "POST /admin/users": makeAdminUser() });
    const user = setupUser();
    renderForm();

    await user.type(field("Username"), "maya.chen");
    await user.type(passwordField(), "1234567");
    await user.click(screen.getByRole("button", { name: "Create user" }));
    expect(passwordField()).toHaveAccessibleDescription(expect.stringContaining("Use at least 8 characters."));
    expect(calls).toHaveLength(0);

    await user.type(passwordField(), "8");
    await user.click(screen.getByRole("button", { name: "Create user" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(sent(calls, "POST")).toEqual([expect.objectContaining({ password: "12345678" })]);
  });

  it("checks the password against the configured minimum", async () => {
    const calls = stubFetch({});
    const user = setupUser();
    renderForm({ passwordMin: 12 });

    expect(passwordField()).toHaveAccessibleDescription("At least 12 characters, not a common or leaked password.");
    await user.type(field("Username"), "maya.chen");
    await user.type(passwordField(), "eleven-char");
    await user.click(screen.getByRole("button", { name: "Create user" }));
    expect(passwordField()).toHaveAccessibleDescription(expect.stringContaining("Use at least 12 characters."));
    expect(calls).toHaveLength(0);
  });

  it("shows the server's password refusal under the field", async () => {
    stubFetch({
      "POST /admin/users": () =>
        refused("common", "This password is too common. Choose one that's harder to guess."),
    });
    const user = setupUser();
    const props = renderForm();

    await fillAccount(user);
    await user.click(screen.getByRole("button", { name: "Create user" }));

    await waitFor(() =>
      expect(passwordField()).toHaveAccessibleDescription(
        expect.stringContaining("This password is too common. Choose one that's harder to guess."),
      ),
    );
    expect(passwordField()).toHaveAttribute("aria-invalid", "true");
    expect(passwordField()).toHaveFocus();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(props.onSaved).not.toHaveBeenCalled();
  });

  it("shows a retired username under the field", async () => {
    stubFetch({
      "POST /admin/users": () =>
        jsonResponse(
          { detail: "That username belonged to a deleted account. Choose another.", code: "username_retired" },
          { status: 409 },
        ),
    });
    const user = setupUser();
    renderForm();

    await fillAccount(user);
    await user.click(screen.getByRole("button", { name: "Create user" }));

    await waitFor(() =>
      expect(field("Username")).toHaveAccessibleDescription(
        expect.stringContaining("That username belonged to a deleted account. Choose another."),
      ),
    );
    expect(field("Username")).toHaveFocus();
  });

  it("has no sign-in facts for a new user", () => {
    renderForm();
    expect(screen.queryByText("Signed up")).not.toBeInTheDocument();
  });

  it("validates the overrides and clears a field's error once it is edited", async () => {
    const calls = stubFetch({});
    const user = setupUser();
    renderForm();

    await fillAccount(user);
    await user.type(field(CHAT), "lots");
    await user.type(field("Max file size (MB)"), "101");
    await user.type(field("Max documents"), "-1");
    await user.type(field("Requests per minute"), "2.5");
    await user.click(screen.getByRole("button", { name: "Create user" }));

    expect(field(CHAT)).toHaveAccessibleDescription(expect.stringContaining("Enter a whole number."));
    expect(field("Max file size (MB)")).toHaveAccessibleDescription(expect.stringContaining("Must be at most 100."));
    expect(field("Max documents")).toHaveAccessibleDescription(expect.stringContaining("Must be at least 0."));
    expect(field("Requests per minute")).toHaveAccessibleDescription(expect.stringContaining("Enter a whole number."));
    expect(field(CHAT)).toHaveFocus();
    expect(calls).toHaveLength(0);

    await user.clear(field("Max file size (MB)"));
    await user.type(field("Max file size (MB)"), "0");
    expect(field("Max file size (MB)")).not.toHaveAttribute("aria-invalid");
    await user.click(screen.getByRole("button", { name: "Create user" }));
    expect(field("Max file size (MB)")).toHaveAccessibleDescription(expect.stringContaining("Must be at least 1."));
  });

  it("marks typed values Custom, flags those above the plan, and resets to the plan", async () => {
    const user = setupUser();
    renderForm();

    await user.type(field("Requests per minute"), "60");
    const rate = field("Requests per minute").closest(".field") as HTMLElement;
    expect(within(rate).getByText("Custom")).toBeInTheDocument();
    expect(within(rate).getByText("Above plan")).toBeInTheDocument();

    await user.clear(field("Requests per minute"));
    await user.type(field("Requests per minute"), "5");
    expect(within(rate).queryByText("Above plan")).not.toBeInTheDocument();

    await user.click(within(rate).getByRole("button", { name: "Reset Requests per minute to the plan's value" }));
    expect(field("Requests per minute")).toHaveValue("");
    expect(within(rate).queryByText("Custom")).not.toBeInTheDocument();
  });

  it("asks before an unlimited chat allowance and sends the confirmation", async () => {
    const created = makeAdminUser({ id: 12 });
    const calls = stubFetch({ "POST /admin/users": created });
    const user = setupUser();
    const props = renderForm();

    await user.type(field("Username"), "  Maya.Chen ");
    await user.type(passwordField(), "correct horse battery");
    await user.type(field(CHAT), "0");
    expect(screen.getByText("Unlimited chat allowance: this user has no cost ceiling.")).toBeInTheDocument();
    await user.click(screen.getByRole("switch", { name: "Active" }));
    await user.click(screen.getByRole("button", { name: "Create user" }));
    expect(field(CHAT)).toHaveAccessibleDescription(
      expect.stringContaining("Confirm an unlimited chat allowance below."),
    );
    expect(calls).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "Allow unlimited" }));
    expect(screen.getByText("Unlimited chat allowance allowed: this user has no cost ceiling.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create user" }));

    const body: AdminUserCreate = {
      username: "maya.chen",
      password: "correct horse battery",
      is_active: false,
      restrict_models: false,
      allowed_model_ids: [],
      billing_tester: false,
      overrides: { monthly_token_limit: 0 },
      confirm_unlimited: true,
    };
    await waitFor(() => expect(props.onSaved).toHaveBeenCalledWith(created, true));
    expect(sent(calls, "POST")).toEqual([body]);
  });

  it("starts with a 30-day Professional grant while users can't pay, and sends it", async () => {
    const calls = stubFetch({ "POST /admin/users": makeAdminUser() });
    const user = setupUser();
    renderForm({ paymentsOpen: false });

    expect(
      screen.getByText("This user can't use the app until they have a plan, and online payment isn't set up yet."),
    ).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Start with a complimentary plan" })).toBeChecked();
    expect(screen.getByRole("combobox", { name: "Plan" })).toHaveDisplayValue("Professional");
    expect(field("Days")).toHaveValue("30");
    expect(field(CHAT)).toHaveAttribute("placeholder", "Plan: 5,000,000");

    await fillAccount(user);
    await user.click(screen.getByRole("switch", { name: "Can make test payments" }));
    await user.click(screen.getByRole("button", { name: "Create user" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(sent(calls, "POST")).toEqual([
      expect.objectContaining({
        billing_tester: true,
        grant: { plan_id: 3, days: 30, reason: "New account" },
      }),
    ]);
  });

  it("grants with no end date, and checks the days and the reason", async () => {
    const calls = stubFetch({ "POST /admin/users": makeAdminUser() });
    const user = setupUser();
    renderForm({ paymentsOpen: false });

    await fillAccount(user);
    await user.clear(field("Days"));
    await user.type(field("Days"), "91");
    await user.clear(field("Reason"));
    await user.click(screen.getByRole("button", { name: "Create user" }));
    expect(field("Days")).toHaveAccessibleDescription(expect.stringContaining("Must be at most 90."));
    expect(field("Reason")).toHaveAccessibleDescription(expect.stringContaining("This field is required."));

    await user.selectOptions(screen.getByRole("combobox", { name: "Plan" }), "Starter");
    expect(field(CHAT)).toHaveAttribute("placeholder", "Plan: 500,000");
    await user.click(screen.getByRole("switch", { name: "No end date" }));
    expect(field("Days")).toBeDisabled();
    await user.type(field("Reason"), "Family");
    await user.click(screen.getByRole("button", { name: "Create user" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(sent(calls, "POST")).toEqual([
      expect.objectContaining({ grant: { plan_id: 1, days: null, reason: "Family" } }),
    ]);
  });

  it("creates without a plan when the grant is switched off", async () => {
    const calls = stubFetch({ "POST /admin/users": makeAdminUser() });
    const user = setupUser();
    renderForm({ paymentsOpen: false });
    await fillAccount(user);
    await user.click(screen.getByRole("switch", { name: "Start with a complimentary plan" }));
    expect(screen.getByText("Without a plan the user sees the plan page and pays to start.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create user" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(sent(calls, "POST")[0]).not.toHaveProperty("grant");
    expect(sent(calls, "POST")[0]).not.toHaveProperty("display_name");
  });

  it("creates with an optional name, normalized, after checking it as the server does", async () => {
    const calls = stubFetch({ "POST /admin/users": makeAdminUser() });
    const user = setupUser();
    renderForm();
    await fillAccount(user, "bikash");
    const name = field("Name");
    expect(name).toHaveAccessibleDescription(/^Optional\./);
    await user.type(name, "Bikash <Patel>");
    await user.click(screen.getByRole("button", { name: "Create user" }));
    expect(name).toHaveAccessibleDescription(/^Leave out < and >\./);
    expect(name).toHaveFocus();
    expect(calls).toHaveLength(0);

    await user.clear(name);
    await user.type(name, "  Bikash   Patel ");
    await user.click(screen.getByRole("button", { name: "Create user" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(sent(calls, "POST")[0]).toMatchObject({ username: "bikash", display_name: "Bikash Patel" });
  });

  it("restricts the user to the checked chat models", async () => {
    const calls = stubFetch({ "POST /admin/users": makeAdminUser({ restrict_models: true, allowed_model_ids: [1] }) });
    const user = setupUser();
    const props = renderForm();

    await fillAccount(user);
    await user.click(screen.getByRole("switch", { name: "Restrict to selected models" }));

    const checklist = screen.getByRole("group", { name: "Allowed chat models" });
    const claude = within(checklist).getByRole("checkbox", { name: /Claude Opus 5\.5/ });
    const gpt = within(checklist).getByRole("checkbox", { name: /GPT-5\.5/ });
    expect(gpt).toHaveAccessibleName(expect.stringContaining("disabled"));
    expect(within(checklist).getByText("With no models selected, this user can't chat.")).toBeInTheDocument();

    await user.click(claude);
    expect(claude).toBeChecked();
    expect(within(checklist).queryByText("With no models selected, this user can't chat.")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Create user" }));
    await waitFor(() => expect(props.onSaved).toHaveBeenCalled());
    expect(sent(calls, "POST")).toEqual([expect.objectContaining({ restrict_models: true, allowed_model_ids: [1] })]);
  });

  it("explains when the model list is loading, failed or empty", async () => {
    const user = setupUser();
    const first = render(
      <UserFormModal
        user={null}
        config={makeAdminConfig()}
        chatModels={null}
        modelsError={null}
        plans={null}
        paymentsOpen
        passwordMin={8}
        onClose={() => {}}
        onSaved={() => {}}
        onPlanChanged={() => {}}
        onNavigate={() => {}}
      />,
    );
    expect(screen.getByRole("switch", { name: "Start with a complimentary plan" })).toBeDisabled();
    await user.click(screen.getByRole("switch", { name: "Restrict to selected models" }));
    expect(screen.getByText("Loading models…")).toBeInTheDocument();
    first.unmount();

    renderForm({ chatModels: null, modelsError: "Couldn't load the model list." });
    await user.click(screen.getByRole("switch", { name: "Restrict to selected models" }));
    expect(screen.getByText("Couldn't load the model list.")).toBeInTheDocument();
  });

  it("points to the Models tab when there are no chat models", async () => {
    const user = setupUser();
    renderForm({ chatModels: [] });
    await user.click(screen.getByRole("switch", { name: "Restrict to selected models" }));
    expect(screen.getByText("No chat models are configured yet. Add one in the Models tab.")).toBeInTheDocument();
  });

  it("shows a taken username under the field", async () => {
    stubFetch({ "POST /admin/users": () => errorResponse(409, "A user named maya.chen already exists.") });
    const user = setupUser();
    const props = renderForm();

    await fillAccount(user);
    await user.click(screen.getByRole("button", { name: "Create user" }));

    await waitFor(() =>
      expect(field("Username")).toHaveAccessibleDescription(
        expect.stringContaining("A user named maya.chen already exists."),
      ),
    );
    expect(field("Username")).toHaveFocus();
    expect(screen.getByRole("button", { name: "Create user" })).toBeEnabled();
    expect(props.onSaved).not.toHaveBeenCalled();
  });

  it("shows other server errors as an alert and allows another try", async () => {
    stubFetch({ "POST /admin/users": () => errorResponse(500, "Database is unavailable.") });
    const user = setupUser();
    renderForm();

    await fillAccount(user);
    await user.click(screen.getByRole("button", { name: "Create user" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Database is unavailable.");
    expect(screen.getByRole("button", { name: "Create user" })).toBeEnabled();
  });

  it("disables both buttons while saving", async () => {
    const pending = deferred<AdminUser>();
    stubFetch({ "POST /admin/users": () => pending.promise });
    const user = setupUser();
    const props = renderForm();

    await fillAccount(user);
    await user.click(screen.getByRole("button", { name: "Create user" }));

    expect(screen.getByRole("button", { name: "Create user" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Create user" })).toHaveAttribute("aria-busy", "true");
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();

    pending.resolve(makeAdminUser());
    await waitFor(() => expect(props.onSaved).toHaveBeenCalledTimes(1));
  });

  it("closes from Cancel and Escape", async () => {
    const user = setupUser();
    const props = renderForm();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await user.keyboard("{Escape}");
    expect(props.onClose).toHaveBeenCalledTimes(2);
  });
});

describe("UserFormModal: editing", () => {
  const existing = makeAdminUser({
    restrict_models: true,
    allowed_model_ids: [2],
    limits: makeLimits({ monthly_token_limit: 500_000 }),
    overrides: { ...makeAdminUser().overrides, monthly_token_limit: 500_000 },
    plan: makeAdminUserPlan(),
  });

  function stubEdit(routes: Record<string, Route> = {}) {
    return stubFetch({ "GET /admin/billing/users/7": makeUserBilling(), ...routes });
  }

  it("shows the user's overrides and the plan's values without a username field", async () => {
    stubEdit();
    renderForm({ user: existing });
    expect(screen.getByRole("dialog", { name: "Edit maya.chen" })).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Username" })).not.toBeInTheDocument();
    expect(passwordField("Set new password")).toHaveValue("");
    expect(field(CHAT)).toHaveValue("500000");
    expect(within(field(CHAT).closest(".field") as HTMLElement).getByText("Custom")).toBeInTheDocument();
    expect(field(CHAT)).toHaveAttribute("placeholder", "Plan: 2,000,000");
    expect(field("Requests per minute")).toHaveValue("");
    expect(field("Requests per minute")).toHaveAttribute("placeholder", "Plan: 20");
    expect(screen.getByRole("switch", { name: "Restrict to selected models" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /GPT-5\.5/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Claude Opus 5\.5/ })).not.toBeChecked();
    expect(await screen.findByText(/Standard, valid 8 Oct to 7 Nov 2026/)).toBeInTheDocument();
  });

  it("sends only the overrides that changed, null for one reset to the plan", async () => {
    const saved = makeAdminUser();
    const calls = stubEdit({ "PATCH /admin/users/7": saved });
    const user = setupUser();
    const props = renderForm({ user: existing });

    await user.type(field("Requests per minute"), "60");
    await user.click(screen.getByRole("button", { name: `Reset ${CHAT} to the plan's value` }));
    await user.click(screen.getByRole("switch", { name: "Can make test payments" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    const body: AdminUserUpdate = {
      is_active: true,
      restrict_models: true,
      allowed_model_ids: [2],
      billing_tester: true,
      overrides: { monthly_token_limit: null, rate_limit_per_min: 60 },
    };
    await waitFor(() => expect(props.onSaved).toHaveBeenCalledWith(saved, false));
    expect(sent(calls, "PATCH")).toEqual([body]);
  });

  it("leaves the name out of the update when it didn't change", async () => {
    const named = makeAdminUser({ display_name: "Maya Chen" });
    const calls = stubEdit({ "PATCH /admin/users/7": named });
    const user = setupUser();
    renderForm({ user: named });
    expect(field("Name")).toHaveValue("Maya Chen");
    await user.type(field("Name"), "  ");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(sent(calls, "PATCH")).toHaveLength(1));
    expect(sent(calls, "PATCH")[0]).not.toHaveProperty("display_name");
  });

  it.each([
    ["Maya C.", "Maya C."],
    ["", null],
  ])("sends the name %j as %j", async (typed, display_name) => {
    const calls = stubEdit({ "PATCH /admin/users/7": makeAdminUser() });
    const user = setupUser();
    renderForm({ user: makeAdminUser({ display_name: "Maya Chen" }) });
    await user.clear(field("Name"));
    if (typed) await user.type(field("Name"), typed);
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(sent(calls, "PATCH")).toHaveLength(1));
    expect(sent(calls, "PATCH")[0]).toMatchObject({ display_name });
  });

  it("keeps the server's Above plan flag for an unchanged override and needs no confirmation for an existing 0", async () => {
    stubEdit();
    renderForm({
      user: makeAdminUser({
        overrides: { ...makeAdminUser().overrides, monthly_token_limit: 0 },
        above_plan: ["monthly_token_limit"],
        plan: makeAdminUserPlan(),
      }),
    });
    const chat = field(CHAT).closest(".field") as HTMLElement;
    expect(within(chat).getByText("Above plan")).toBeInTheDocument();
    expect(screen.queryByText("Unlimited chat allowance: this user has no cost ceiling.")).not.toBeInTheDocument();
  });

  it("warns when the allowed models and the plan don't overlap", () => {
    stubEdit();
    renderForm({ user: makeAdminUser({ restrict_models: true, allowed_model_ids: [2], no_models: true }) });
    expect(screen.getByText("No model left: the user's list and the plan's models don't overlap.")).toBeInTheDocument();
  });

  it("sends a new password only when it is long enough", async () => {
    const calls = stubEdit({ "PATCH /admin/users/7": makeAdminUser() });
    const user = setupUser();
    const props = renderForm({ user: existing });

    await user.type(passwordField("Set new password"), "short");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(passwordField("Set new password")).toHaveAccessibleDescription(
      expect.stringContaining("Use at least 8 characters."),
    );
    expect(sent(calls, "PATCH")).toHaveLength(0);

    await user.type(passwordField("Set new password"), "-but-longer");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(props.onSaved).toHaveBeenCalled());
    expect(sent(calls, "PATCH")).toEqual([expect.objectContaining({ password: "short-but-longer" })]);
  });

  it("shows a refused new password under the field", async () => {
    stubEdit({
      "PATCH /admin/users/7": () =>
        refused("breached", "This password has appeared in a data breach. Choose a different one."),
    });
    const user = setupUser();
    renderForm({ user: existing });

    await user.type(passwordField("Set new password"), "password123456");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() =>
      expect(passwordField("Set new password")).toHaveAccessibleDescription(
        expect.stringContaining("This password has appeared in a data breach. Choose a different one."),
      ),
    );
    expect(passwordField("Set new password")).toHaveFocus();
  });

  it("shows how an email sign-up signs in, read only", () => {
    stubEdit();
    renderForm({
      user: makeAdminUser({
        signup_source: "email",
        created_at: "2026-10-30T08:32:00Z",
        terms_accepted_at: "2026-10-30T08:32:00Z",
        terms_version: "2026-11-01",
        email: "maya@example.com",
        email_verified_at: "2026-10-30T08:32:00Z",
        google_linked: true,
        google_email: "maya.chen@gmail.com",
        plan: makeAdminUserPlan(),
      }),
    });
    expect(facts()).toEqual({
      "Signed up": "With email on 30 Oct 2026Accepted the terms of 2026-11-01 on 30 Oct 2026, 08:32",
      Email: "maya@example.comConfirmed",
      Google: "Connected as maya.chen@gmail.com",
      Password: "Set",
    });
    expect(screen.queryByRole("textbox", { name: /email/i })).not.toBeInTheDocument();
  });

  it("shows a pending address, a change in progress and a Google-only account", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-31T10:00:00Z"));
    stubEdit();
    const pending = { address: "maya.chen@studio-nine.in", sent_at: "2026-10-31T09:57:00Z" };
    const first = render(
      <UserFormModal
        {...defaultProps()}
        user={makeAdminUser({
          signup_source: "google",
          has_password: false,
          pending_email: pending,
          google_linked: true,
          google_email: "maya.chen@gmail.com",
        })}
      />,
    );
    expect(facts()).toEqual({
      "Signed up": "With Google on 2 Sept 2026",
      Email: "Pending: maya.chen@studio-nine.in, sent 3 minutes ago",
      Google: "Connected as maya.chen@gmail.com",
      Password: "Not set",
    });
    expect(passwordField("Set new password")).toHaveAccessibleDescription(
      "The user has no password and signs in with Google. Leave blank to keep it that way.",
    );
    first.unmount();

    render(
      <UserFormModal
        {...defaultProps()}
        user={makeAdminUser({ email: "maya@example.com", pending_email: { ...pending, sent_at: null } })}
      />,
    );
    expect(facts()).toEqual({
      "Signed up": "Created by the admin on 2 Sept 2026",
      Email: "maya@example.comConfirmedChanging to maya.chen@studio-nine.in, not sent yet",
      Google: "Not connected",
      Password: "Set",
    });
    vi.useRealTimers();
  });

  it("shows a 409 while editing as an alert, not as a username error", async () => {
    stubEdit({ "PATCH /admin/users/7": () => errorResponse(409, "The user changed meanwhile.") });
    const user = setupUser();
    renderForm({ user: existing });

    await screen.findByText(/Standard, valid/);
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The user changed meanwhile.");
  });
});
