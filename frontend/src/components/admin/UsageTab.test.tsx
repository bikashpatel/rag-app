import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { UsageReport } from "../../api/types";
import { formatCompact, formatNumber } from "../../lib/format";
import { makeUsageReport } from "../../test/adminTabs";
import { errorResponse, stubFetch, type Route } from "../../test/http";
import { monthLabel } from "./MonthPicker";
import UsageTab from "./UsageTab";

function stubUsage(route: Route = makeUsageReport()) {
  return stubFetch({ "GET /admin/usage": route });
}

function table(title: string) {
  return within(screen.getByRole("region", { name: title }));
}

function rows(title: string): string[][] {
  return table(title)
    .getAllByRole("row")
    .slice(1)
    .map((row) => within(row).getAllByRole("cell").map((cell) => cell.textContent ?? ""));
}

function utcDay(date: string): string {
  return new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" }).format(
    new Date(`${date}T00:00:00Z`),
  );
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-20T09:30:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("UsageTab", () => {
  it("shows this month's totals and breakdowns", async () => {
    const calls = stubUsage();
    render(<UsageTab />);

    expect(screen.getByRole("heading", { name: `Usage in ${monthLabel("2026-10")}` })).toBeInTheDocument();
    const totals = within(await screen.findByLabelText(`Totals for ${monthLabel("2026-10")}`));
    const tiles = Object.fromEntries(
      totals.getAllByRole("term").map((term) => [term.textContent, term.nextElementSibling?.textContent]),
    );
    expect(tiles).toEqual({
      "Chat tokens": formatCompact(1_250_000),
      "Embedding tokens": formatCompact(340_000),
      "Vision tokens": formatCompact(12_000),
      Images: "4",
      Cost: "$18.50",
      "Chat requests": "220",
    });
    expect(totals.getByText(formatCompact(1_250_000))).toHaveAttribute("title", formatNumber(1_250_000));
    expect(calls.map((call) => call.path)).toEqual(["/admin/usage?month=2026-10"]);
  });

  it("sorts users and models by cost and days by date", async () => {
    stubUsage();
    render(<UsageTab />);
    await screen.findByRole("region", { name: "By user" });

    expect(rows("By user")).toEqual([
      ["sam.ortiz", "140", formatNumber(850_000), formatNumber(300_000), formatNumber(12_000), "1", "$14.30"],
      ["maya.chen", "80", formatNumber(400_000), formatNumber(40_000), "0", "3", "$4.20"],
    ]);
    expect(rows("By model")).toEqual([
      ["Claude Opus 5.5", "chat", "220", formatNumber(1_000_000), formatNumber(250_000), "$17.50"],
      ["text-embedding-3-large", "embedding", "12", formatNumber(340_000), "0", "$0.04"],
    ]);
    expect(rows("Daily")).toEqual([
      [utcDay("2026-10-01"), formatNumber(750_000), formatNumber(340_000), formatNumber(12_000), "3", "$11.50"],
      [utcDay("2026-10-02"), formatNumber(500_000), "0", "0", "1", "$7.00"],
    ]);
    expect(table("Daily").getByTitle(`${formatNumber(500_000)} chat tokens`)).toBeInTheDocument();
  });

  it("says when a breakdown or the whole month is empty", async () => {
    stubUsage(makeUsageReport({ by_user: [] }));
    const { unmount } = render(<UsageTab />);
    expect(await screen.findByRole("region", { name: "By user" })).toHaveTextContent("Nothing recorded.");
    unmount();

    const empty: UsageReport = makeUsageReport({
      totals: { chat_tokens: 0, embedding_tokens: 0, vision_tokens: 0, images: 0, cost_usd: 0, requests: 0 },
      by_user: [],
      by_model: [],
      daily: [],
    });
    stubUsage(empty);
    render(<UsageTab />);
    expect(await screen.findByText(`No usage recorded in ${monthLabel("2026-10")}.`)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("moves between months, never past the current one", async () => {
    const user = userEvent.setup();
    const calls = stubUsage(({ path }) => makeUsageReport({ month: new URLSearchParams(path.split("?")[1]).get("month") ?? "" }));
    render(<UsageTab />);
    await screen.findByRole("region", { name: "By user" });
    const next = screen.getByRole("button", { name: "Next month" });
    expect(next).toBeDisabled();
    expect(screen.getByLabelText("Month")).toHaveAttribute("max", "2026-10");

    await user.click(screen.getByRole("button", { name: "Previous month" }));
    expect(await screen.findByRole("heading", { name: `Usage in ${monthLabel("2026-09")}` })).toBeInTheDocument();
    expect(await screen.findByLabelText(`Totals for ${monthLabel("2026-09")}`)).toBeInTheDocument();
    expect(next).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Previous month" }));
    await user.click(next);

    expect(calls.map((call) => call.path)).toEqual([
      "/admin/usage?month=2026-10",
      "/admin/usage?month=2026-09",
      "/admin/usage?month=2026-08",
      "/admin/usage?month=2026-09",
    ]);
  });

  it("crosses a year boundary", async () => {
    vi.setSystemTime(new Date("2027-01-03T08:00:00Z"));
    const user = userEvent.setup();
    const calls = stubUsage(makeUsageReport({ month: "2027-01" }));
    render(<UsageTab />);
    await user.click(screen.getByRole("button", { name: "Previous month" }));
    await waitFor(() => expect(calls.at(-1)?.path).toBe("/admin/usage?month=2026-12"));
  });

  it("loads a typed month, but not one after the current month", async () => {
    const calls = stubUsage();
    render(<UsageTab />);
    await screen.findByRole("region", { name: "By user" });
    const input = screen.getByLabelText("Month");

    fireEvent.change(input, { target: { value: "2026-12" } });
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(calls).toHaveLength(1);

    fireEvent.change(input, { target: { value: "2026-07" } });
    expect(input).not.toHaveAttribute("aria-invalid");
    await waitFor(() => expect(calls.at(-1)?.path).toBe("/admin/usage?month=2026-07"));
    expect(screen.getByRole("heading", { name: `Usage in ${monthLabel("2026-07")}` })).toBeInTheDocument();
  });

  it("shows a load error with a retry", async () => {
    const user = userEvent.setup();
    let fail = true;
    stubUsage(() => (fail ? errorResponse(500, "Usage is unavailable.") : makeUsageReport()));
    render(<UsageTab />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Usage is unavailable.");
    fail = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("region", { name: "By model" })).toBeInTheDocument();
  });
});
