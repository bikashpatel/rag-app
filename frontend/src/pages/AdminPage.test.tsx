import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import type { AdminStats } from "../api/types";
import { formatNumber } from "../lib/format";
import {
  makeActivityReport,
  makeAdminPlugin,
  makeAdminStats,
  makeSeededAdminPlans,
  makeSystemStatus,
  makeUsageReport,
} from "../test/adminTabs";
import { makeBillingAdminConfig, makeBillingSummary, makeUserBilling } from "../test/billing";
import { makeAdminConfig, makeAdminMe, makeAdminModel, makeAdminUser, makeMe } from "../test/fixtures";
import { errorResponse, stubFetch, type FetchCall, type Route } from "../test/http";
import { renderWithAuth } from "../test/render";
import AdminPage from "./AdminPage";

function adminRoutes(stats: AdminStats | Route = makeAdminStats(), extra: Record<string, Route> = {}): Record<string, Route> {
  return {
    "GET /auth/me": makeAdminMe(),
    "GET /admin/stats": stats,
    "GET /admin/users": [makeAdminUser({ id: 1, username: "admin", is_admin: true }), makeAdminUser()],
    "GET /admin/config": makeAdminConfig(),
    "GET /admin/models": [makeAdminModel()],
    "GET /admin/usage": makeUsageReport(),
    "GET /admin/documents": [],
    "GET /admin/plugins": [makeAdminPlugin()],
    "GET /admin/plugins/gmail/activity": makeActivityReport(),
    "GET /admin/system": makeSystemStatus(),
    "GET /admin/plans": makeSeededAdminPlans(),
    "GET /admin/billing/config": makeBillingAdminConfig(),
    "GET /admin/billing/summary": makeBillingSummary(),
    "GET /admin/billing/payments": { items: [], total: 0 },
    "GET /admin/billing/orders": { items: [], total: 0 },
    "GET /admin/billing/users/7": makeUserBilling(),
    ...extra,
  };
}

async function renderAdmin(routes: Record<string, Route> = adminRoutes(), hash = "") {
  const calls = stubFetch(routes);
  const rendered = await renderWithAuth(<AdminPage />, { route: `/admin${hash}` });
  return { calls, ...rendered };
}

/** The overview's tiles as label → value. */
async function overview(): Promise<Record<string, string>> {
  const list = await screen.findByLabelText("Overview");
  const terms = within(list).getAllByRole("term");
  return Object.fromEntries(
    terms.map((term) => {
      const values = within(term.parentElement as HTMLElement).getAllByRole("definition");
      return [term.textContent, values.map((value) => value.textContent).join(" · ")];
    }),
  );
}

const statsRequests = (calls: FetchCall[]) => calls.filter((call) => call.path === "/admin/stats").length;

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-20T09:30:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("AdminPage", () => {
  it("shows the overview and opens on the Users tab", async () => {
    await renderAdmin();

    expect(screen.getByRole("heading", { level: 1, name: "Administration" })).toBeInTheDocument();
    expect(document.title).toBe("Admin · AskNuvexusDoc");
    expect(await overview()).toEqual({
      Users: "3",
      Documents: "42",
      Failed: "0",
      "Needs re-index": "0",
      "Ingestion queue": "0 · Idle",
      "Embedding model": "Text Embedding 3 Large",
      "Vision model": "Claude Sonnet 5.5 (vision)",
      "Image model": "Off",
    });
    const tabs = within(screen.getByRole("tablist", { name: "Admin sections" })).getAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      "Users",
      "Plans",
      "Billing",
      "Models",
      "Usage",
      "Documents",
      "Plugins",
      "System",
    ]);
    expect(screen.getByRole("tab", { name: "Users" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel", { name: "Users" })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "New user" })).toBeInTheDocument();
  });

  it("opens the tab named in the hash", async () => {
    await renderAdmin(adminRoutes(), "#usage");
    expect(screen.getByRole("tab", { name: "Usage" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByRole("heading", { name: /^Usage in / })).toBeInTheDocument();
  });

  it("opens a Billing section, a record from the hash, and the plans", async () => {
    const user = userEvent.setup();
    const { location } = await renderAdmin(adminRoutes(), "#billing/orders");
    expect(screen.getByRole("tab", { name: "Billing" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByRole("heading", { name: "Orders" })).toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: "Business & payments" }));
    expect(location()).toBe("/admin#billing/business");
    expect(await screen.findByRole("heading", { name: "Going-live checklist" })).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Plans" }));
    expect(await screen.findByRole("row", { name: /^Standard/ })).toBeInTheDocument();
  });

  it("opens a user named in the hash and leaves the plain tab hash", async () => {
    const { location } = await renderAdmin(adminRoutes(), "#users?user=7");
    expect(await screen.findByRole("dialog", { name: "Edit maya.chen" })).toBeInTheDocument();
    await waitFor(() => expect(location()).toBe("/admin#users"));
  });

  it("opens the System tab", async () => {
    await renderAdmin(adminRoutes(), "#system");
    expect(screen.getByRole("tab", { name: "System" })).toHaveAttribute("aria-selected", "true");
    const panel = screen.getByRole("tabpanel", { name: "System" });
    expect(await within(panel).findByRole("heading", { level: 2, name: "System status" })).toBeInTheDocument();
  });

  it("switches tabs by click and keeps the hash in sync", async () => {
    const user = userEvent.setup();
    const { location } = await renderAdmin();

    await user.click(screen.getByRole("tab", { name: "Plugins" }));
    expect(location()).toBe("/admin#plugins");
    expect(screen.getByRole("tabpanel", { name: "Plugins" })).toBeInTheDocument();
    expect(await screen.findByRole("region", { name: /^Gmail/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Users" })).toHaveAttribute("tabindex", "-1");
    expect(screen.getByRole("tab", { name: "Plugins" })).toHaveAttribute("tabindex", "0");
  });

  it("moves between tabs with the arrow keys, Home and End", async () => {
    const user = userEvent.setup();
    const { location } = await renderAdmin();
    const tab = (name: string) => screen.getByRole("tab", { name });

    act(() => tab("Users").focus());
    await user.keyboard("{ArrowRight}");
    expect(tab("Plans")).toHaveFocus();
    expect(tab("Plans")).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{End}");
    expect(tab("System")).toHaveFocus();
    await user.keyboard("{ArrowRight}");
    expect(tab("Users")).toHaveFocus();
    await user.keyboard("{ArrowLeft}");
    expect(tab("System")).toHaveFocus();
    await user.keyboard("{Home}");
    expect(tab("Users")).toHaveFocus();
    expect(location()).toBe("/admin#users");
  });

  it("restores the page title when it closes", async () => {
    document.title = "AskNuvexusDoc";
    const { unmount } = await renderAdmin();
    expect(document.title).toBe("Admin · AskNuvexusDoc");
    unmount();
    expect(document.title).toBe("AskNuvexusDoc");
  });
});

describe("the overview", () => {
  it("warns when no embedding model is active and opens the models", async () => {
    const user = userEvent.setup();
    const { location } = await renderAdmin(adminRoutes(makeAdminStats({ active_embedding_model: null })));

    expect((await overview())["Embedding model"]).toBe("None");
    expect(screen.getByText("No active embedding model.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Open models" }));
    expect(location()).toBe("/admin#models");
  });

  it("re-indexes stale and failed documents, then refreshes", async () => {
    const user = userEvent.setup();
    const stats = makeAdminStats({ stale_documents: 3, failed_documents: 1 });
    const { calls } = await renderAdmin(adminRoutes(stats, { "POST /admin/reindex": { queued: 4 } }));

    await overview();
    expect(screen.getByText(/than the active one/)).toHaveTextContent(
      "3 documents were indexed with a different embedding model than the active one (Text Embedding 3 Large). Search skips them until they are re-indexed. 1 failed document is retried too.",
    );
    await user.click(screen.getByRole("button", { name: "Re-index now" }));

    expect(await screen.findByText("Queued 4 documents for re-indexing.")).toBeInTheDocument();
    expect(calls.find((call) => call.method === "POST")?.body).toEqual({ only_stale: true });
    expect(statsRequests(calls)).toBe(2);
  });

  it("offers a retry for failed documents and reports a refused request", async () => {
    const user = userEvent.setup();
    const routes = adminRoutes(makeAdminStats({ failed_documents: 1 }), {
      "POST /admin/reindex": () => errorResponse(409, "Another re-index is running."),
    });
    await renderAdmin(routes);

    await overview();
    expect(screen.getByText("1 document failed to index.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry failed" }));
    expect(await screen.findByText("Another re-index is running.")).toBeInTheDocument();
  });

  it("shows a load error with a retry", async () => {
    const user = userEvent.setup();
    let fail = true;
    await renderAdmin(adminRoutes(() => (fail ? errorResponse(503, "The database is starting.") : makeAdminStats())));

    expect(await screen.findByRole("alert")).toHaveTextContent("The database is starting.");
    fail = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect((await overview()).Users).toBe("3");
  });

  it("polls while documents are queued for indexing", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true, now: new Date("2026-10-20T09:30:00Z") });
    let queue = 2;
    const { calls } = await renderAdmin(adminRoutes(() => makeAdminStats({ queue_length: queue })));

    expect((await overview())["Ingestion queue"]).toBe(`${formatNumber(2)} · Indexing`);
    queue = 0;
    await act(() => vi.advanceTimersByTimeAsync(5_000));
    await waitFor(() => expect(statsRequests(calls)).toBe(2));
    expect((await overview())["Ingestion queue"]).toBe("0 · Idle");
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(statsRequests(calls)).toBe(2);
  });
});

describe("the admin top bar", () => {
  it("shows who is signed in and opens Settings over the page", async () => {
    const user = userEvent.setup();
    const { location } = await renderAdmin();

    expect(screen.getByTitle("Signed in as admin")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to chat" })).toHaveAttribute("href", "/");
    await user.click(screen.getByRole("button", { name: "Settings" }));
    expect(location()).toBe("/admin?settings=general");
  });

  it("signs out, or says why it couldn't", async () => {
    const user = userEvent.setup();
    let fail = true;
    const { calls } = await renderAdmin(
      adminRoutes(makeAdminStats(), {
        "POST /auth/logout": () => (fail ? errorResponse(503, "Unavailable") : new Response(null, { status: 204 })),
      }),
    );

    await user.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't sign out. Check your connection and try again.");
    expect(screen.getByRole("button", { name: "Sign out" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    fail = false;
    await user.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(screen.queryByTitle("Signed in as admin")).not.toBeInTheDocument());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(calls.filter((call) => call.path === "/auth/logout")).toHaveLength(2);
  });
});

describe("the /admin route", () => {
  it("loads the admin page for the admin", async () => {
    stubFetch(adminRoutes());
    window.history.replaceState(null, "", "/admin#models");
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Administration" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Models" })).toHaveAttribute("aria-selected", "true");
  });

  it("doesn't carry the page to the login form after an explicit sign-out", async () => {
    let signedIn = true;
    stubFetch(
      adminRoutes(makeAdminStats(), {
        "GET /auth/me": () => (signedIn ? makeAdminMe() : errorResponse(401, "Not authenticated")),
        "POST /auth/logout": () => {
          signedIn = false;
          return new Response(null, { status: 204 });
        },
      }),
    );
    window.history.replaceState(null, "", "/admin#users");
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: /^Welcome back/ })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/login");
    expect((window.history.state as { usr?: unknown } | null)?.usr ?? null).toBeNull();
  });

  it("sends anyone else to the chat", async () => {
    const calls = stubFetch({ "GET /auth/me": makeMe() });
    window.history.replaceState(null, "", "/admin");
    render(<App />);
    await waitFor(() => expect(window.location.pathname).toBe("/"));
    expect(screen.queryByRole("heading", { name: "Administration" })).not.toBeInTheDocument();
    expect(calls.some((call) => call.path.startsWith("/admin"))).toBe(false);
  });
});
