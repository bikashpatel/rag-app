import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Me, MyUsage } from "../../api/types";
import { makeAdminMe, makeMe, makeMyUsage } from "../../test/fixtures";
import { deferred, errorResponse, stubFetch, type Route } from "../../test/http";
import UsageTab from "./UsageTab";

async function renderUsage(usage: Route = makeMyUsage(), user: Me = makeMe()) {
  const onClose = vi.fn();
  const calls = stubFetch({ "GET /me/usage": usage });
  render(<UsageTab user={user} onClose={onClose} />);
  await screen.findByRole("heading", { name: "By model" });
  return { calls, onClose };
}

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
  it("shows the month, when the limits reset and who sets them", async () => {
    await renderUsage();
    expect(screen.getByText("October 2026 (UTC)")).toBeInTheDocument();
    const lede = screen.getByText(/Limits start again on/);
    expect(lede).toHaveTextContent(/1 November, 00:00 \(in 12 days\)\. Your administrator sets them\./);
  });

  it("shows each limit as a figure and a meter with the exact numbers", async () => {
    await renderUsage();
    expect(tile("Chat tokens")).toHaveTextContent("120K / 2M");
    expect(tile("Chat tokens")).toHaveTextContent("1,880,000 left");
    expect(screen.getByRole("meter", { name: "Chat tokens" })).toHaveAttribute(
      "aria-valuetext",
      "120,000 of 2,000,000 chat tokens",
    );
    expect(screen.getByRole("meter", { name: "Images" })).toHaveAttribute("aria-valuenow", "3");
    expect(screen.getByRole("meter", { name: "Images" })).toHaveAttribute("aria-valuemax", "50");
    expect(tile("Vision tokens")).toHaveTextContent("Reading scanned PDF pages");
  });

  it("warns at 90% and says when a limit is reached, in words", async () => {
    await renderUsage(
      makeMyUsage({ chat_tokens: { used: 1_850_000, limit: 2_000_000 }, images: { used: 52, limit: 50 } }),
    );
    expect(tile("Chat tokens")).toHaveTextContent("Almost used up · 150,000 left");
    expect(screen.getByRole("meter", { name: "Chat tokens" })).toHaveAttribute(
      "aria-valuetext",
      "1,850,000 of 2,000,000 chat tokens. Almost used up",
    );
    expect(tile("Images")).toHaveTextContent("Limit reached");
    const images = screen.getByRole("meter", { name: "Images" });
    expect(images).toHaveAttribute("aria-valuenow", "50");
    expect(images).toHaveAttribute("aria-valuetext", "52 of 50 images. Limit reached");
  });

  it("shows unlimited amounts without meters", async () => {
    await renderUsage(
      makeMyUsage({
        chat_tokens: { used: 412_380, limit: 0 },
        images: { used: 3, limit: 0 },
        rate_limit_per_min: 0,
        documents: { count: 4, max_documents: 0, storage_bytes: 3_250_000, max_storage_mb: 0, max_upload_mb: 25 },
      }),
    );
    expect(tile("Chat tokens")).toHaveTextContent("412K · Unlimited");
    expect(tile("Chat tokens")).toHaveTextContent("412,380 chat tokens, unlimited.");
    expect(tile("Chat tokens")).toHaveTextContent("No monthly budget");
    expect(tile("Images")).toHaveTextContent("No monthly limit");
    expect(screen.queryByRole("meter", { name: "Chat tokens" })).not.toBeInTheDocument();
    expect(fact("Requests per minute")).toHaveTextContent("Unlimited");
    expect(fact("Documents")).toHaveTextContent("4 · Unlimited");
    expect(fact("Storage")).toHaveTextContent("3.1 MB · Unlimited");
  });

  it("tells the admin there are no limits", async () => {
    const zero = { used: 0, limit: 0 };
    await renderUsage(
      makeMyUsage({ chat_tokens: { used: 90_000, limit: 0 }, images: zero, vision_tokens: zero }),
      makeAdminMe(),
    );
    expect(tile("Chat tokens")).toHaveTextContent("90K · Unlimited (admin)");
    expect(screen.getByText(/As the administrator you have no limits\./)).toBeInTheDocument();
    expect(screen.queryAllByRole("meter")).toHaveLength(0);
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

  it("lists the models used this month", async () => {
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

  it("says when nothing was used yet this month", async () => {
    const usage = makeMyUsage({ rate_limit_per_min: 0 });
    await renderUsage({
      ...usage,
      daily: usage.daily.map((day) => ({ ...day, chat_tokens: 0, requests: 0, images: 0, embedding_tokens: 0, cost_usd: 0 })),
    } satisfies MyUsage);
    expect(screen.getByText("No usage yet this month.")).toBeInTheDocument();
    expect(screen.getByText("No request limit")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show as table" })).not.toBeInTheDocument();
  });

  it("reports a failed load and loads again on Try again", async () => {
    const user = userEvent.setup();
    let fail = true;
    stubFetch({ "GET /me/usage": () => (fail ? errorResponse(500, null) : makeMyUsage()) });
    render(<UsageTab user={makeMe()} onClose={() => {}} />);
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
    expect(tile("Chat tokens")).toHaveTextContent("120K / 2M");
    await waitFor(() => expect(refresh).not.toHaveAttribute("aria-disabled"));
  });

  it("closes from the panel's close button", async () => {
    const user = userEvent.setup();
    const { onClose } = await renderUsage();
    await user.click(screen.getByRole("button", { name: "Close settings" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
