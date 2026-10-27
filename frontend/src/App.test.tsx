import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useNavigate } from "react-router-dom";
import { describe, expect, it } from "vitest";
import App, { RequireAuth } from "./App";
import type { Me } from "./api/types";
import { useAuth } from "./auth/AuthContext";
import { makeAdminMe, makeLapsedPlanAccess, makeMe, makePaidPlanAccess } from "./test/fixtures";
import { makeCatalog, makeOrder } from "./test/billing";
import { errorResponse, stubFetch } from "./test/http";
import { renderWithAuth } from "./test/render";

/** The chat routes' stand-in: the navigations the chat page makes. */
function Chat() {
  const navigate = useNavigate();
  const { user, refresh } = useAuth();
  return (
    <>
      <h1>Chat</h1>
      <p>{user?.plan.access ? "With access" : "Without access"}</p>
      <button type="button" onClick={() => void refresh()}>
        Refresh
      </button>
      <button type="button" onClick={() => navigate("?settings=account")}>
        Settings
      </button>
      <button type="button" onClick={() => navigate("/c/9", { replace: true })}>
        Replace
      </button>
      <button type="button" onClick={() => navigate("/c/2")}>
        Other chat
      </button>
    </>
  );
}

const tree = (
  <Routes>
    <Route
      path="/"
      element={
        <RequireAuth needsPlan>
          <Chat />
        </RequireAuth>
      }
    />
    <Route
      path="/c/:conversationId"
      element={
        <RequireAuth needsPlan>
          <Chat />
        </RequireAuth>
      }
    />
    <Route
      path="/plans"
      element={
        <RequireAuth>
          <h1>Plans</h1>
        </RequireAuth>
      }
    />
    <Route path="/login" element={<h1>Login</h1>} />
  </Routes>
);

function renderAt(route: string, me: () => Me | Response) {
  stubFetch({ "GET /auth/me": me });
  return renderWithAuth(tree, { route });
}

describe("RequireAuth needsPlan", () => {
  it("sends a user without access from the chat to /plans, keeping an open Settings tab", async () => {
    const lapsed = makeMe({ plan: makeLapsedPlanAccess() });
    const view = await renderAt("/", () => lapsed);
    expect(screen.getByRole("heading", { name: "Plans" })).toBeInTheDocument();
    expect(view.location()).toBe("/plans");
    view.unmount();

    const other = await renderAt("/c/12?settings=billing&x=1#m3", () => makeMe({ plan: makeLapsedPlanAccess("none") }));
    expect(other.location()).toBe("/plans?settings=billing");
  });

  it("lets a user with a plan and the admin in", async () => {
    const paid = await renderAt("/c/12", () => makeMe({ plan: makePaidPlanAccess() }));
    expect(screen.getByRole("heading", { name: "Chat" })).toBeInTheDocument();
    expect(paid.location()).toBe("/c/12");
    paid.unmount();

    // The admin always passes, whatever Me.plan says.
    const admin = await renderAt("/", () => makeAdminMe({ plan: makeLapsedPlanAccess("none") }));
    expect(admin.location()).toBe("/");
  });

  it("sends a signed-out visitor to the login page", async () => {
    const view = await renderAt("/c/12", () => errorResponse(401, "Not authenticated"));
    expect(view.location()).toBe("/login");
  });

  it("keeps the page shown when the plan ends there, until the next navigation", async () => {
    let me = makeMe({ plan: makePaidPlanAccess() });
    const view = await renderAt("/", () => me);
    const user = userEvent.setup();
    me = makeMe({ plan: makeLapsedPlanAccess() });
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByText("Without access")).toBeInTheDocument();
    expect(view.location()).toBe("/");

    await user.click(screen.getByRole("button", { name: "Settings" }));
    expect(view.location()).toBe("/?settings=account");
    await user.click(screen.getByRole("button", { name: "Replace" }));
    expect(view.location()).toBe("/c/9");
    expect(screen.getByRole("heading", { name: "Chat" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Other chat" }));
    expect(view.location()).toBe("/plans");
  });
});

describe("App routes", () => {
  async function renderApp(path: string, me: Me) {
    stubFetch({
      "GET /auth/me": me,
      "GET /billing/plans": makeCatalog(),
      "GET /billing/profile": { name: null, email: null },
      "GET /billing/orders/q3Zx8LmP0aB2cD4eF6gH": makeOrder(),
    });
    window.history.replaceState(null, "", path);
    render(<App />);
    await act(async () => {});
  }

  it("opens the plan page for a user without a plan who opens the app", async () => {
    await renderApp("/", makeMe({ plan: makeLapsedPlanAccess("none") }));
    expect(await screen.findByRole("heading", { name: "Choose a plan" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/plans");
  });

  it("serves the checkout and the return page to any signed-in user", async () => {
    await renderApp("/plans/checkout/standard?kind=period", makeMe({ plan: makeLapsedPlanAccess() }));
    expect(await screen.findByRole("heading", { name: "Review and pay" })).toBeInTheDocument();
  });

  it("serves the return page from Razorpay", async () => {
    await renderApp("/billing/return?order=q3Zx8LmP0aB2cD4eF6gH", makeMe());
    expect(await screen.findByRole("heading", { name: "Confirming your payment…" })).toBeInTheDocument();
    expect(window.location.search).toBe("?order=q3Zx8LmP0aB2cD4eF6gH");
  });

  it("sends /settings/billing to the Settings tab over the app", async () => {
    await renderApp("/settings/billing", makeMe({ plan: makeLapsedPlanAccess() }));
    expect(await screen.findByRole("heading", { name: "Your Standard plan has ended" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Plan & billing" })).toBeInTheDocument();
    expect(window.location.pathname + window.location.search).toBe("/plans?settings=billing");
  });
});
