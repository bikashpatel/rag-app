import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { makeMyUsage } from "../../test/fixtures";
import DailyUsageChart from "./DailyUsageChart";

const usage = makeMyUsage();

function renderChart() {
  render(
    <DailyUsageChart
      start={usage.period_start}
      end={usage.resets_at}
      daily={usage.daily}
      rateLimitPerMin={20}
      windowLabel="October 2026"
      windowWord="this period"
      resetText="Resets 1 Nov, 00:00 UTC"
    />,
  );
  return screen.getByRole("group", { name: /per day, October 2026/ });
}

beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(DOMRect.fromRect({ width: 600, height: 176 }));
});

describe("DailyUsageChart", () => {
  it("covers a period across two months, from its first day", () => {
    const daily = Array.from({ length: 10 }, (_, index) => ({
      ...usage.daily[index % usage.daily.length],
      date: `2026-11-${String(index + 6).padStart(2, "0")}`,
    }));
    const { container } = render(
      <DailyUsageChart
        start="2026-11-06T05:12:00Z"
        end="2026-12-06T18:30:00Z"
        daily={daily}
        rateLimitPerMin={0}
        windowLabel="6 Nov to 6 Dec 2026"
        windowWord="this period"
        resetText="Starts again 7 Dec"
      />,
    );
    expect(screen.getByRole("group", { name: /^Chat tokens per day, 6 Nov to 6 Dec 2026: / })).toBeInTheDocument();
    const labels = [...container.querySelectorAll("text.settings-chart-axis")].map((label) => label.textContent);
    expect(labels).toContain("6 Nov");
    expect(labels).toContain("Today");
    expect(labels).toContain("27");
    expect(screen.getByText("Starts again 7 Dec")).toBeInTheDocument();
    expect(screen.getByText("No request limit")).toBeInTheDocument();
  });

  it("says when nothing was used yet this period", () => {
    const empty = usage.daily.map((day) => ({ ...day, chat_tokens: 0, chat_allowance: 0, images: 0, cost_usd: 0, requests: 0 }));
    render(
      <DailyUsageChart
        start={usage.period_start}
        end={usage.resets_at}
        daily={empty.map((day) => ({ ...day, vision_tokens: 0, embedding_tokens: 0 }))}
        rateLimitPerMin={20}
        windowLabel="October 2026"
        windowWord="this period"
        resetText="Resets 1 Nov, 00:00 UTC"
      />,
    );
    expect(screen.getByText("No usage yet this period.")).toBeInTheDocument();
  });

  it("is a named figure whose plot summarises the month", () => {
    const plot = renderChart();
    expect(screen.getByRole("figure", { name: "Per day" })).toBeInTheDocument();
    expect(plot).toHaveAccessibleName("Chat tokens per day, October 2026: 240K in total, most on 3 October with 120K.");
    expect(plot).toHaveAccessibleDescription("Left and right arrow keys move between days.");
    expect(plot.querySelector("svg")).not.toBeNull();
  });

  it("switches the metric", async () => {
    const user = userEvent.setup();
    const plot = renderChart();
    await user.click(screen.getByRole("radio", { name: "Images" }));
    expect(plot).toHaveAccessibleName("Images per day, October 2026: 3 in total, most on 3 October with 2.");
    await user.click(screen.getByRole("radio", { name: "Cost" }));
    expect(plot).toHaveAccessibleName("Cost per day, October 2026: $1.50 in total, most on 3 October with $0.60.");
  });

  it("reads out the day chosen with the arrow keys, never past today", async () => {
    const user = userEvent.setup();
    const plot = renderChart();
    await user.tab();
    expect(screen.getByRole("radio", { name: "Chat tokens" })).toHaveFocus();
    await user.tab();
    expect(plot).toHaveFocus();
    await user.keyboard("{ArrowLeft}");
    expect(screen.getByText(/2 October · 80,000 chat tokens · 1 image · \$0\.50/, { selector: "[aria-live]" })).toBeInTheDocument();
    await user.keyboard("{Home}");
    expect(screen.getByText(/1 October · 40,000 chat tokens · 0 images · \$0\.40/, { selector: "[aria-live]" })).toBeInTheDocument();
    await user.keyboard("{End}{ArrowRight}");
    expect(screen.getByText(/3 October · 120,000 chat tokens · 2 images · \$0\.60/, { selector: "[aria-live]" })).toBeInTheDocument();
  });
});
