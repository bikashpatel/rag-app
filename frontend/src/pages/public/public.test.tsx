import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import App from "../../App";
import { markdownResponse } from "../../test/adminTabs";
import { makePublicPlan, makePublicSite, makeSeededOffers } from "../../test/billing";
import { makeMe } from "../../test/fixtures";
import { errorResponse, stubFetch, type FetchCall, type Route } from "../../test/http";
import { parsePolicy } from "./PolicyPage";

const signedOut = () => errorResponse(401, "Not authenticated");
const REFUNDS = `<!-- draft -->
# Cancellation and refunds

_Last updated 26 Oct 2026_

**Draft.** This page is a placeholder.

## Refunds

[Say when you refund]

# A second title

- one
- [Terms](/terms)
`;
const publicPlans = makeSeededOffers().map(({ id, status, current, options, ...plan }) => plan);

function openPage(path: string, routes: Record<string, Route> = {}): FetchCall[] {
  const calls = stubFetch({
    "GET /auth/me": signedOut,
    "GET /public/site": makePublicSite({ contact_phone: "+91 98765 43210", address: "12 MG Road\nBengaluru" }),
    "GET /public/plans": publicPlans,
    "GET /legal/refunds.md": () => markdownResponse(REFUNDS),
    "GET /legal/pricing.md": () => markdownResponse("# Pricing\n\nPlans for every team."),
    "GET /legal/contact.md": () => markdownResponse("# Contact us\n\nWrite to us."),
    ...routes,
  });
  window.history.replaceState(null, "", path);
  render(<App />);
  return calls;
}

describe("parsePolicy", () => {
  it("reads the draft marker, the title and the last-updated line", () => {
    expect(parsePolicy(REFUNDS)).toEqual({
      draft: true,
      title: "Cancellation and refunds",
      updated: "Last updated 26 Oct 2026",
      body: expect.stringMatching(/^\*\*Draft\.\*\* This page/),
    });
    expect(parsePolicy("﻿Just text.\r\nMore.")).toEqual({
      draft: false,
      title: null,
      updated: null,
      body: "Just text.\nMore.",
    });
  });
});

describe("public pages", () => {
  it("shows a policy page without a session: the frame, the Draft band, the title and the prose", async () => {
    openPage("/refunds");
    expect(await screen.findByRole("heading", { level: 1, name: "Cancellation and refunds" })).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent(
      "DraftThis page is a placeholder. The owner hasn't published its final text yet.",
    );
    expect(screen.getByText("Last updated 26 Oct 2026")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "A second title" })).toBeInTheDocument();
    expect(within(screen.getByRole("main")).getByRole("link", { name: "Terms" })).toHaveAttribute("href", "/terms");
    expect(document.title).toBe("Cancellation and refunds · RAG Assistant");

    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
    const nav = screen.getByRole("navigation", { name: "Site" });
    expect(
      within(nav)
        .getAllByRole("link")
        .map((link) => link.textContent),
    ).toEqual(["Pricing", "About", "Contact"]);
    const footer = screen.getByRole("contentinfo");
    expect(within(footer).getByText("billing@example.com · +91 98765 43210")).toBeInTheDocument();
    expect(within(footer).getByText(/12 MG Road/)).toBeInTheDocument();
    expect(within(footer).getAllByRole("link")).toHaveLength(7);
    expect(within(footer).getByText("Payments are processed by Razorpay.")).toBeInTheDocument();
  });

  it("lists the contact details on the contact page and offers the app when signed in", async () => {
    openPage("/contact", { "GET /auth/me": makeMe() });
    expect(await screen.findByRole("heading", { level: 1, name: "Contact us" })).toBeInTheDocument();
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
    const facts = screen.getByLabelText("Contact details");
    expect(within(facts).getByRole("link", { name: "billing@example.com" })).toHaveAttribute(
      "href",
      "mailto:billing@example.com",
    );
    expect(within(facts).getByRole("link", { name: "+91 98765 43210" })).toHaveAttribute("href", "tel:+919876543210");
    expect(await screen.findByRole("link", { name: "Open the app" })).toHaveAttribute("href", "/");
  });

  it("names a page that hasn't been written and keeps the app's name when the site details fail", async () => {
    openPage("/shipping", {
      "GET /legal/shipping.md": () => new Response("Not found", { status: 404 }),
      "GET /public/site": () => errorResponse(500, "Down"),
    });
    expect(await screen.findByText("This page hasn't been written yet.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Shipping and delivery" })).toBeInTheDocument();
    expect(within(screen.getByRole("contentinfo")).getByText("RAG Assistant")).toBeInTheDocument();
  });

  it("shows a failed page with a retry, keeping the frame while moving between pages", async () => {
    let failing = true;
    const calls = openPage("/terms", {
      "GET /legal/terms.md": () => {
        if (!failing) return markdownResponse("# Terms of service\n\nThe terms.");
        failing = false;
        return new Response("Oops", { status: 500 });
      },
      "GET /legal/about.md": () => markdownResponse("# About us\n\nWho we are."),
    });
    const user = userEvent.setup();
    expect(await screen.findByRole("alert")).toHaveTextContent("The server ran into a problem. Please try again.");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("The terms.")).toBeInTheDocument();
    await user.click(within(screen.getByRole("navigation", { name: "Site" })).getByRole("link", { name: "About" }));
    expect(await screen.findByText("Who we are.")).toBeInTheDocument();
    expect(calls.filter((call) => call.path === "/public/site")).toHaveLength(1);
  });

  it("shows pricing: the intro, the plan cards with the recommended one, the compare table and sign-in", async () => {
    openPage("/pricing");
    expect(await screen.findByRole("heading", { level: 1, name: "Pricing" })).toBeInTheDocument();
    expect(screen.getByText("Plans for every team.")).toBeInTheDocument();
    const cards = await screen.findByRole("list", { name: "Plans" });
    expect(within(cards).getAllByRole("listitem", { name: /Starter|Standard|Professional/ })).toHaveLength(3);
    expect(within(cards).getByText("Recommended")).toBeInTheDocument();
    expect(within(cards).queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Compare plans" })).toBeInTheDocument();
    expect(screen.getByText(/plans don't renew automatically/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign in to choose a plan" })).toHaveAttribute("href", "/login");
    expect(document.title).toBe("Pricing · RAG Assistant");
  });

  it("sends a signed-in visitor from pricing to the plans", async () => {
    openPage("/pricing", { "GET /auth/me": makeMe(), "GET /public/plans": [makePublicPlan()] });
    expect(await screen.findByRole("link", { name: "Choose a plan" })).toHaveAttribute("href", "/plans");
  });

  it("says when no plan is offered, and retries a failed load", async () => {
    let failing = true;
    openPage("/pricing", {
      "GET /public/plans": () => {
        if (!failing) return [];
        failing = false;
        return errorResponse(503, "Plans are unavailable.");
      },
    });
    const user = userEvent.setup();
    expect(await screen.findByRole("alert")).toHaveTextContent("Plans are unavailable.");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText(/No plans are available yet\./)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "billing@example.com" })).toHaveAttribute(
      "href",
      "mailto:billing@example.com",
    );
  });

  it("goes to the login form with the plans as the place to return to", async () => {
    openPage("/pricing");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("link", { name: "Sign in to choose a plan" }));
    await waitFor(() => expect(window.location.pathname).toBe("/login"));
    expect((window.history.state as { usr?: unknown }).usr).toEqual({
      from: { pathname: "/plans", search: "", hash: "" },
    });
  });
});
