import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Me, MyUsage } from "../../api/types";
import { makeAdminMe, makeLapsedPlanAccess, makeMe, makeMyUsage, makePaidPlanAccess } from "../../test/fixtures";
import { deferred, errorResponse, stubFetch, type Route } from "../../test/http";
import { renderWithRouter } from "../../test/render";
import UsageTab from "./UsageTab";

async function renderUsage(usage: Route = makeMyUsage(), user: Me = makeMe()) {
  const onClose = vi.fn();
  const calls = stubFetch({ "GET /me/usage": usage });
  const view = renderWithRouter(<UsageTab user={user} onClose={onClose} />);
  await screen.findByRole("heading", { name: "By model" });
  return { calls, onClose, view };
}

/** A paid Standard period, 8 Oct to 7 Nov 2026, and its usage window. */
const paidUsage = () =>
  makeMyUsage({ period_start: "2026-10-08T05:12:00Z", resets_at: "2026-11-07T18:30:00Z" });

function fact(term: string): HTMLElement {
  return screen.getByText(term, { selector: "dt" }).nextElementSibling as HTMLElement;
}

function tile(label: string): HTMLElement {
  return screen.getByText(label, { selector: ".settings-tile-label" }).closest(".settings-tile") as HTMLElement;
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-20T12:00:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("UsageTab", () => {
  it("shows the month of an open-ended plan and when the allowances start again", async () => {
    await renderUsage();
    expect(screen.getByText("October 2026 (UTC)")).toBeInTheDocument();
    expect(screen.getByText(/Your allowances start again on/)).toHaveTextContent(
      "Your allowances start again on Sun 1 November (in 12 days).",
    );
    expect(screen.getByText("Resets 1 Nov, 00:00 UTC")).toBeInTheDocument();
  });

  it("shows a paid period, and that the allowances end with the plan when nothing follows", async () => {
    await renderUsage(paidUsage(), makeMe({ plan: makePaidPlanAccess() }));
    expect(screen.getByText("This period: 8 Oct to 7 Nov 2026")).toBeInTheDocument();
    expect(screen.getByText(/Your allowances end on/)).toHaveTextContent(
      "Your allowances end on Sat 7 November (in 18 days) with your plan.",
    );
    expect(screen.getByText("Ends 7 Nov")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Usage by model, 8 Oct to 7 Nov 2026" })).toBeInTheDocument();
  });

  it("says the allowances start again with the next period when one follows", async () => {
    const plan = makePaidPlanAccess({ next_plan: { id: 1, code: "starter", name: "Starter" } });
    await renderUsage(paidUsage(), makeMe({ plan }));
    expect(screen.getByText(/Your allowances start again on/)).toHaveTextContent(
      "Your allowances start again on Sun 8 November (in 19 days) with your next period.",
    );
    expect(screen.getByText("Starts again 8 Nov")).toBeInTheDocument();
  });

  it("shows the counts without limits for a user without a plan, with the way back", async () => {
    const { view } = await renderUsage(makeMyUsage(), makeMe({ plan: makeLapsedPlanAccess() }));
    expect(screen.getByText(/No active plan\. /)).toHaveTextContent("No active plan. Choose a plan to start again.");
    expect(screen.queryAllByRole("meter")).toHaveLength(0);
    expect(tile("Chat allowance")).toHaveTextContent("120K");
    expect(tile("Chat allowance")).toHaveTextContent("No active plan");
    await userEvent.setup().click(screen.getByRole("link", { name: "Choose a plan" }));
    expect(view.location()).toBe("/plans");
  });

  it("shows each limit as a figure and a meter with the exact numbers", async () => {
    await renderUsage();
    expect(tile("Chat allowance")).toHaveTextContent("120K / 2M");
    expect(tile("Chat allowance")).toHaveTextContent("1,880,000 left");
    expect(screen.getByRole("meter", { name: "Chat allowance" })).toHaveAttribute(
      "aria-valuetext",
      "120,000 of 2,000,000 standard tokens",
    );
    expect(screen.getByRole("meter", { name: "Images" })).toHaveAttribute("aria-valuenow", "3");
    expect(screen.getByRole("meter", { name: "Images" })).toHaveAttribute("aria-valuemax", "50");
    expect(tile("Vision tokens")).toHaveTextContent("Reading scanned PDF pages");
  });

  it("warns at 90% and says when a limit is reached, in words", async () => {
    await renderUsage(
      makeMyUsage({ chat_allowance: { used: 1_850_000, limit: 2_000_000 }, images: { used: 52, limit: 50 } }),
    );
    expect(tile("Chat allowance")).toHaveTextContent("Almost used up · 150,000 left");
    expect(screen.getByRole("meter", { name: "Chat allowance" })).toHaveAttribute(
      "aria-valuetext",
      "1,850,000 of 2,000,000 standard tokens. Almost used up",
    );
    expect(tile("Images")).toHaveTextContent("Limit reached");
    const images = screen.getByRole("meter", { name: "Images" });
    expect(images).toHaveAttribute("aria-valuenow", "50");
    expect(images).toHaveAttribute("aria-valuetext", "52 of 50 images. Limit reached");
  });

  it("shows unlimited amounts without meters", async () => {
    await renderUsage(
      makeMyUsage({
        chat_allowance: { used: 412_380, limit: 0 },
        images: { used: 3, limit: 0 },
        rate_limit_per_min: 0,
        documents: { count: 4, max_documents: 0, storage_bytes: 3_250_000, max_storage_mb: 0, max_upload_mb: 25 },
      }),
    );
    expect(tile("Chat allowance")).toHaveTextContent("412K · Unlimited");
    expect(tile("Chat allowance")).toHaveTextContent("412,380 standard tokens, unlimited.");
    expect(tile("Chat allowance")).toHaveTextContent("No limit");
    expect(tile("Chat allowance")).toHaveTextContent("Counted at each model's rate.");
    expect(tile("Images")).toHaveTextContent("No limit");
    expect(screen.queryByRole("meter", { name: "Chat allowance" })).not.toBeInTheDocument();
    expect(fact("Requests per minute")).toHaveTextContent("Unlimited");
    expect(fact("Documents")).toHaveTextContent("4 · Unlimited");
    expect(fact("Storage")).toHaveTextContent("3.1 MB · Unlimited");
  });

  it("tells the admin there are no limits", async () => {
    const zero = { used: 0, limit: 0 };
    await renderUsage(
      makeMyUsage({ chat_allowance: { used: 90_000, limit: 0 }, images: zero, vision_tokens: zero }),
      makeAdminMe(),
    );
    expect(tile("Chat allowance")).toHaveTextContent("90K · Unlimited (admin)");
    expect(tile("Chat allowance")).toHaveTextContent("No monthly budget");
    expect(tile("Images")).toHaveTextContent("No monthly limit");
    expect(screen.getByText(/Limits start again on/)).toHaveTextContent(
      /1 November, 00:00 \(in 12 days\)\. As the administrator you have no limits\./,
    );
    expect(screen.queryAllByRole("meter")).toHaveLength(0);
  });

  it("writes the storage limit as the plan does", async () => {
    const usage = makeMyUsage();
    await renderUsage({ ...usage, documents: { ...usage.documents, storage_bytes: 0, max_storage_mb: 3_000 } });
    expect(fact("Storage")).toHaveTextContent("0 B of 3,000 MB");
  });

  it("lists the other figures and the estimated cost", async () => {
    await renderUsage();
    expect(fact("Chat answers")).toHaveTextContent("37");
    expect(fact("Requests per minute")).toHaveTextContent("20 per minute");
    expect(fact("Embedding tokens")).toHaveTextContent("40K not limited");
    expect(fact("Documents")).toHaveTextContent("4 of 100");
    expect(fact("Storage")).toHaveTextContent("3.1 MB of 500 MB");
    expect(fact("Largest upload")).toHaveTextContent("25 MB");
    expect(screen.getByText("$1.42")).toBeInTheDocument();
    const parts = within(screen.getByRole("list", { name: "Cost by kind" })).getAllByRole("listitem");
    expect(parts.map((part) => part.textContent)).toEqual(["Chat $1.20", "Images $0.20", "Vision $0.00", "Embeddings $0.02"]);
  });

  it("lists the models used this period", async () => {
    await renderUsage();
    const table = screen.getByRole("table", { name: "Usage by model, October 2026" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getAllByRole("cell").map((cell) => cell.textContent)).toEqual([
      "Claude Opus 5.5Chat",
      "Chat",
      "120,000",
      "37",
      "$1.20",
    ]);
    expect(within(rows[1]).getAllByRole("cell").map((cell) => cell.textContent)).toEqual([
      "GPT ImageImage",
      "Image",
      "—",
      "3",
      "$0.20",
    ]);
  });

  it("says when no model was used yet", async () => {
    await renderUsage(makeMyUsage({ by_model: [] }));
    expect(screen.getByText("Nothing used yet.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("shows the daily usage as a table on request", async () => {
    const user = userEvent.setup();
    await renderUsage();
    const toggle = screen.getByRole("button", { name: "Show as table" });
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(toggle).toHaveTextContent("Hide table");
    const table = screen.getByRole("table", { name: "Usage per day, October 2026 (UTC days)" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    expect(within(rows[0]).getByRole("rowheader")).toHaveTextContent(/1 October/);
    expect(within(rows[2]).getAllByRole("cell").map((cell) => cell.textContent)).toEqual([
      "120,000",
      "12",
      "2",
      "0",
      "10,000",
      "$0.60",
    ]);
  });

  it("describes the request limit and the reset under the chart", async () => {
    await renderUsage();
    expect(screen.getByText("Up to 20 requests a minute")).toBeInTheDocument();
    expect(screen.getByText("Resets 1 Nov, 00:00 UTC")).toBeInTheDocument();
  });

  it("says when nothing was used yet this period", async () => {
    const usage = makeMyUsage({ rate_limit_per_min: 0 });
    await renderUsage({
      ...usage,
      daily: usage.daily.map((day) => ({ ...day, chat_allowance: 0, chat_tokens: 0, requests: 0, images: 0, embedding_tokens: 0, cost_usd: 0 })),
    } satisfies MyUsage);
    expect(screen.getByText("No usage yet this period.")).toBeInTheDocument();
    expect(screen.getByText("No request limit")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show as table" })).not.toBeInTheDocument();
  });

  it("reports a failed load and loads again on Try again", async () => {
    const user = userEvent.setup();
    let fail = true;
    stubFetch({ "GET /me/usage": () => (fail ? errorResponse(500, null) : makeMyUsage()) });
    renderWithRouter(<UsageTab user={makeMe()} onClose={() => {}} />);
    expect(screen.getByText("Loading your usage…")).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Couldn't load your usage. The server ran into a problem. Please try again.",
    );
    fail = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: "By model" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("refreshes in place and keeps the figures when a refresh fails", async () => {
    const user = userEvent.setup();
    const second = deferred<Response>();
    let calls = 0;
    await renderUsage(() => (++calls === 1 ? makeMyUsage() : second.promise));
    const refresh = screen.getByRole("button", { name: "Refresh usage" });
    await user.click(refresh);
    expect(refresh).toHaveAttribute("aria-disabled", "true");
    await user.click(refresh);
    expect(calls).toBe(2);
    second.resolve(errorResponse(503, "Usage is being recalculated."));
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load your usage. Usage is being recalculated.");
    expect(tile("Chat allowance")).toHaveTextContent("120K / 2M");
    await waitFor(() => expect(refresh).not.toHaveAttribute("aria-disabled"));
  });

  it("closes from the panel's close button", async () => {
    const user = userEvent.setup();
    const { onClose } = await renderUsage();
    await user.click(screen.getByRole("button", { name: "Close settings" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
