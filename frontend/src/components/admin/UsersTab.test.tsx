import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { AdminUser } from "../../api/types";
import { makeAdminConfig, makeAdminModel, makeAdminUser } from "../../test/fixtures";
import { errorResponse, stubFetch, type Route } from "../../test/http";
import UsersTab from "./UsersTab";

const admin = makeAdminUser({
  id: 1,
  username: "admin",
  is_admin: true,
  last_login_at: "2026-10-20T08:00:00Z",
  usage: { chat_tokens: 5_000, embedding_tokens: 0, vision_tokens: 0, images: 0, cost_usd: 0.05 },
});
const maya = makeAdminUser();
const sam = makeAdminUser({
  id: 9,
  username: "sam.ortiz",
  is_active: false,
  restrict_models: true,
  allowed_model_ids: [],
  last_login_at: null,
  rate_limit_per_min: 0,
  usage: { chat_tokens: 1_900_000, embedding_tokens: 0, vision_tokens: 0, images: 50, cost_usd: 12.5 },
});
const lee = makeAdminUser({ id: 10, username: "lee.park", restrict_models: true, allowed_model_ids: [1, 2] });

function stubAdmin(users: AdminUser[] | Route, extra: Record<string, Route> = {}) {
  return stubFetch({
    "GET /admin/users": users,
    "GET /admin/config": makeAdminConfig(),
    "GET /admin/models": [
      makeAdminModel(),
      makeAdminModel({ id: 2, display_name: "GPT-5.5", litellm_model: "openai/gpt-5.5", is_default: false }),
      makeAdminModel({ id: 3, display_name: "Embeddings", litellm_model: "openai/text-embedding-3-large", kind: "embedding" }),
    ],
    ...extra,
  });
}

const row = (username: string) => screen.getByRole("row", { name: new RegExp(username.replace(".", "\\.")) });

async function renderTab() {
  const onChanged = vi.fn();
  render(<UsersTab onChanged={onChanged} />);
  await screen.findByRole("table");
  await waitFor(() => expect(screen.getByRole("button", { name: "New user" })).toBeEnabled());
  return onChanged;
}

describe("UsersTab", () => {
  it("lists every user with their limits and model access", async () => {
    stubAdmin([admin, maya, sam, lee]);
    await renderTab();

    expect(screen.getByRole("heading", { name: "Users" })).toBeInTheDocument();
    expect(screen.getAllByRole("row")).toHaveLength(5);

    const adminRow = within(row("admin"));
    expect(adminRow.getByText("admin", { selector: ".badge" })).toBeInTheDocument();
    expect(adminRow.getByText("Unlimited")).toBeInTheDocument();
    expect(adminRow.getByText("All models")).toBeInTheDocument();
    expect(adminRow.getByRole("button", { name: "Edit" })).toBeDisabled();
    expect(adminRow.getByRole("button", { name: "Delete" })).toBeDisabled();
    expect(adminRow.getByText("Managed in .env")).toBeInTheDocument();

    const mayaRow = within(row("maya.chen"));
    expect(mayaRow.getByText("20 / min")).toBeInTheDocument();
    expect(mayaRow.getByText("25 MB per file")).toBeInTheDocument();
    expect(mayaRow.getByText("$1.42")).toBeInTheDocument();
    expect(mayaRow.getByText("All models")).toBeInTheDocument();
    expect(mayaRow.getByRole("button", { name: "Edit maya.chen" })).toBeEnabled();
    expect(mayaRow.getByRole("button", { name: "Delete maya.chen" })).toBeEnabled();

    const samRow = within(row("sam.ortiz"));
    expect(samRow.getByText("inactive")).toBeInTheDocument();
    expect(samRow.getByText("No models")).toBeInTheDocument();
    expect(samRow.getByText("Never")).toBeInTheDocument();
    expect(samRow.getByText("Unlimited")).toBeInTheDocument();
    expect(samRow.getByText(/tokens, 95% used\. Almost used up/)).toBeInTheDocument();
    expect(samRow.getByText(/images, 100% used\. Limit reached/)).toBeInTheDocument();

    expect(within(row("lee.park")).getByText("2 models")).toHaveAttribute("title", "Claude Opus 5.5, GPT-5.5");
    expect(screen.queryByText(/No other users yet/)).not.toBeInTheDocument();
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
    render(<UsersTab onChanged={() => {}} />);

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
    const confirm = screen.getByRole("group", { name: "Delete maya.chen and all of their documents?" });
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
    render(<UsersTab onChanged={() => {}} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Config unavailable.");
    expect(await screen.findByRole("button", { name: "Edit maya.chen" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "New user" })).toBeDisabled();
  });
});
