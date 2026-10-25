import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { makeMyUsage } from "../../test/fixtures";
import DailyUsageChart from "./DailyUsageChart";

const usage = makeMyUsage();

function renderChart() {
  render(<DailyUsageChart month={usage.month} daily={usage.daily} rateLimitPerMin={20} resetsAt={usage.resets_at} />);
  return screen.getByRole("group", { name: /per day, October 2026/ });
}

beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(DOMRect.fromRect({ width: 600, height: 176 }));
});

describe("DailyUsageChart", () => {
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
