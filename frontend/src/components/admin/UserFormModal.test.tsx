import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";
import type { AdminModel, AdminUser, AdminUserCreate, AdminUserUpdate } from "../../api/types";
import { makeAdminConfig, makeAdminModel, makeAdminUser } from "../../test/fixtures";
import { deferred, errorResponse, stubFetch, type FetchCall } from "../../test/http";
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

type Props = ComponentProps<typeof UserFormModal>;

function renderForm(overrides: Partial<Props> = {}) {
  const props: Props = {
    user: null,
    config: makeAdminConfig(),
    chatModels,
    modelsError: null,
    onClose: vi.fn(),
    onSaved: vi.fn(),
    ...overrides,
  };
  render(<UserFormModal {...props} />);
  return props;
}

// Typing speed doesn't matter to the form: no delay between keystrokes keeps the long fields fast on a busy machine.
const setupUser = () => userEvent.setup({ delay: null });
const field = (name: string) => screen.getByRole("textbox", { name });
const passwordField = (label = "Password") => screen.getByLabelText(label);

function sent(calls: FetchCall[], method: string): unknown[] {
  return calls.filter((call) => call.method === method).map((call) => call.body);
}

describe("UserFormModal: creating", () => {
  it("starts from the default limits", () => {
    renderForm();
    expect(screen.getByRole("dialog", { name: "New user" })).toBeInTheDocument();
    expect(field("Username")).toHaveFocus();
    expect(field("Monthly token limit")).toHaveValue("2000000");
    expect(field("Monthly image limit")).toHaveValue("50");
    expect(field("Requests per minute")).toHaveValue("20");
    expect(field("Max file size (MB)")).toHaveValue("25");
    expect(field("Max file size (MB)")).toHaveAccessibleDescription("1 to 100 MB per file.");
    expect(field("Max documents")).toHaveValue("100");
    expect(field("Max storage (MB)")).toHaveValue("500");
    expect(screen.getByRole("switch", { name: "Active" })).toBeChecked();
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

  it("validates the limits and clears a field's error once it is edited", async () => {
    const calls = stubFetch({});
    const user = setupUser();
    renderForm();

    await user.type(field("Username"), "maya.chen");
    await user.type(passwordField(), "correct horse battery");
    await user.clear(field("Monthly token limit"));
    await user.type(field("Monthly token limit"), "lots");
    await user.clear(field("Max file size (MB)"));
    await user.type(field("Max file size (MB)"), "101");
    await user.clear(field("Max documents"));
    await user.type(field("Max documents"), "-1");
    await user.clear(field("Requests per minute"));
    await user.type(field("Requests per minute"), "2.5");
    await user.click(screen.getByRole("button", { name: "Create user" }));

    expect(field("Monthly token limit")).toHaveAccessibleDescription(expect.stringContaining("Enter a whole number."));
    expect(field("Max file size (MB)")).toHaveAccessibleDescription(expect.stringContaining("Must be at most 100."));
    expect(field("Max documents")).toHaveAccessibleDescription(expect.stringContaining("Must be at least 0."));
    expect(field("Requests per minute")).toHaveAccessibleDescription(expect.stringContaining("Enter a whole number."));
    expect(field("Monthly token limit")).toHaveFocus();
    expect(calls).toHaveLength(0);

    await user.clear(field("Max file size (MB)"));
    await user.type(field("Max file size (MB)"), "0");
    expect(field("Max file size (MB)")).not.toHaveAttribute("aria-invalid");
    await user.click(screen.getByRole("button", { name: "Create user" }));
    expect(field("Max file size (MB)")).toHaveAccessibleDescription(expect.stringContaining("Must be at least 1."));
  });

  it("creates the user with a lowercase username and the typed limits", async () => {
    const created = makeAdminUser({ id: 12, username: "maya.chen", monthly_token_limit: 0 });
    const calls = stubFetch({ "POST /admin/users": created });
    const user = setupUser();
    const props = renderForm();

    await user.type(field("Username"), "  Maya.Chen ");
    await user.type(passwordField(), "correct horse battery");
    await user.clear(field("Monthly token limit"));
    await user.type(field("Monthly token limit"), "0");
    await user.click(screen.getByRole("switch", { name: "Active" }));
    await user.click(screen.getByRole("button", { name: "Create user" }));

    const body: AdminUserCreate = {
      username: "maya.chen",
      password: "correct horse battery",
      is_active: false,
      monthly_token_limit: 0,
      monthly_image_limit: 50,
      rate_limit_per_min: 20,
      max_upload_mb: 25,
      max_documents: 100,
      max_storage_mb: 500,
      restrict_models: false,
      allowed_model_ids: [],
    };
    await waitFor(() => expect(props.onSaved).toHaveBeenCalledWith(created, true));
    expect(sent(calls, "POST")).toEqual([body]);
  });

  it("restricts the user to the checked chat models", async () => {
    const calls = stubFetch({ "POST /admin/users": makeAdminUser({ restrict_models: true, allowed_model_ids: [1] }) });
    const user = setupUser();
    const props = renderForm();

    await user.type(field("Username"), "maya.chen");
    await user.type(passwordField(), "correct horse battery");
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
    const { unmount } = render(
      <UserFormModal
        user={null}
        config={makeAdminConfig()}
        chatModels={null}
        modelsError={null}
        onClose={() => {}}
        onSaved={() => {}}
      />,
    );
    await user.click(screen.getByRole("switch", { name: "Restrict to selected models" }));
    expect(screen.getByText("Loading models…")).toBeInTheDocument();
    unmount();

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

    await user.type(field("Username"), "maya.chen");
    await user.type(passwordField(), "correct horse battery");
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

    await user.type(field("Username"), "maya.chen");
    await user.type(passwordField(), "correct horse battery");
    await user.click(screen.getByRole("button", { name: "Create user" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Database is unavailable.");
    expect(screen.getByRole("button", { name: "Create user" })).toBeEnabled();
  });

  it("disables both buttons while saving", async () => {
    const pending = deferred<AdminUser>();
    stubFetch({ "POST /admin/users": () => pending.promise });
    const user = setupUser();
    const props = renderForm();

    await user.type(field("Username"), "maya.chen");
    await user.type(passwordField(), "correct horse battery");
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
  const existing = makeAdminUser({ monthly_token_limit: 500_000, restrict_models: true, allowed_model_ids: [2] });

  it("shows the user's values without a username field", () => {
    renderForm({ user: existing });
    expect(screen.getByRole("dialog", { name: "Edit maya.chen" })).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Username" })).not.toBeInTheDocument();
    expect(passwordField("Set new password")).toHaveValue("");
    expect(field("Monthly token limit")).toHaveValue("500000");
    expect(screen.getByRole("switch", { name: "Restrict to selected models" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /GPT-5\.5/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Claude Opus 5\.5/ })).not.toBeChecked();
  });

  it("keeps the password when the field is left blank", async () => {
    const saved = makeAdminUser({ rate_limit_per_min: 60 });
    const calls = stubFetch({ "PATCH /admin/users/7": saved });
    const user = setupUser();
    const props = renderForm({ user: existing });

    await user.clear(field("Requests per minute"));
    await user.type(field("Requests per minute"), "60");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    const body: AdminUserUpdate = {
      is_active: true,
      monthly_token_limit: 500_000,
      monthly_image_limit: 50,
      rate_limit_per_min: 60,
      max_upload_mb: 25,
      max_documents: 100,
      max_storage_mb: 500,
      restrict_models: true,
      allowed_model_ids: [2],
    };
    await waitFor(() => expect(props.onSaved).toHaveBeenCalledWith(saved, false));
    expect(sent(calls, "PATCH")).toEqual([body]);
  });

  it("sends a new password only when it is long enough", async () => {
    const calls = stubFetch({ "PATCH /admin/users/7": makeAdminUser() });
    const user = setupUser();
    const props = renderForm({ user: existing });

    await user.type(passwordField("Set new password"), "short");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(passwordField("Set new password")).toHaveAccessibleDescription(
      expect.stringContaining("Use at least 8 characters."),
    );
    expect(calls).toHaveLength(0);

    await user.type(passwordField("Set new password"), "-but-longer");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(props.onSaved).toHaveBeenCalled());
    expect(sent(calls, "PATCH")).toEqual([expect.objectContaining({ password: "short-but-longer" })]);
  });

  it("shows a 409 while editing as an alert, not as a username error", async () => {
    stubFetch({ "PATCH /admin/users/7": () => errorResponse(409, "The user changed meanwhile.") });
    const user = setupUser();
    renderForm({ user: existing });

    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The user changed meanwhile.");
  });
});
