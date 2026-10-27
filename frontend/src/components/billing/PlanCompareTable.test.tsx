import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { PublicPlan } from "../../api/types";
import { makePlanModel, makeSeededOffers } from "../../test/billing";
import PlanCompareTable from "./PlanCompareTable";

const seeded: PublicPlan[] = makeSeededOffers();

/** The cells of the row headed `name`, as text (sr-only words included). */
function cells(name: string | RegExp): string[] {
  const [header] = screen.getAllByRole("rowheader", { name }).filter((cell) => cell.getAttribute("scope") === "row");
  const row = header.closest("tr") as HTMLElement;
  return within(row)
    .getAllByRole("cell")
    .map((cell) => cell.textContent ?? "");
}

describe("PlanCompareTable", () => {
  it("is a focusable region named by its heading, with a column per plan and its price", () => {
    render(<PlanCompareTable plans={seeded} />);
    const region = screen.getByRole("region", { name: "Compare plans" });
    expect(region).toHaveAttribute("tabindex", "0");
    const table = within(region).getByRole("table", { name: "What each plan includes" });
    const headers = within(table).getAllByRole("columnheader").map((cell) => cell.textContent);
    expect(headers).toEqual(["Feature", "Starter₹500 / month", "Standard₹2,000 / month", "Professional₹5,000 / month"]);
    expect(screen.getByText("Every plan includes the shared knowledge base and keeps your chats and documents.")).toBeInTheDocument();
  });

  it("groups the rows under row group headers", () => {
    render(<PlanCompareTable plans={seeded} />);
    const groups = screen.getAllByRole("rowheader").filter((cell) => cell.getAttribute("scope") === "rowgroup");
    expect(groups.map((cell) => cell.textContent)).toEqual(["Features", "Models", "Limits per 30 days", "Documents"]);
    expect(groups[0]).toHaveAttribute("colspan", "4");
  });

  it("marks features included or not in words", () => {
    render(<PlanCompareTable plans={seeded} />);
    expect(cells("Chat with your documents")).toEqual(["Included", "Included", "Included"]);
    expect(cells(/^Files from chat/)).toEqual(["—Not included", "Included", "Included"]);
    expect(cells("Images from chat")).toEqual(["—Not included", "—Not included", "Included"]);
    expect(cells(/^Gmail plugin/)).toEqual(["—Not included", "—Not included", "Included"]);
    expect(screen.getByRole("rowheader", { name: /^Gmail plugin/ })).toHaveTextContent("Search, read, draft and send");
  });

  it("lists every chat model by name with about how many answers each plan gives", () => {
    render(<PlanCompareTable plans={seeded} />);
    const models = screen
      .getAllByRole("rowheader")
      .map((cell) => cell.textContent)
      .slice(6, 11);
    expect(models).toEqual(["Claude Haiku 5.5", "Claude Opus 5.5", "Claude Sonnet 5.5", "GPT-5.4 mini", "GPT-5.5"]);
    expect(cells("Claude Haiku 5.5")).toEqual(["About 1,600 answers", "About 5,200 answers", "About 11,000 answers"]);
    expect(cells("Claude Opus 5.5")).toEqual(["—Not included", "—Not included", "About 290 answers"]);
    expect(cells("New chats start with")).toEqual(["Claude Haiku 5.5", "Claude Sonnet 5.5", "Claude Opus 5.5"]);
  });

  it("shows the limits per 30 days and the documents", () => {
    render(<PlanCompareTable plans={seeded} />);
    expect(screen.getByRole("rowheader", { name: /^Chat allowance/ })).toHaveTextContent(
      "Counted at each model's rate: Claude Haiku 5.5 ×0.05, GPT-5.5 ×2.7",
    );
    expect(cells(/^Chat allowance/)).toEqual(["500,000", "2,000,000", "5,000,000"]);
    expect(cells("Images")).toEqual(["—Not included", "—Not included", "60"]);
    expect(cells("Scanned PDF pages read")).toEqual(["About 50", "About 170", "About 330"]);
    expect(cells("Requests a minute")).toEqual(["10", "20", "30"]);
    expect(cells("Documents")).toEqual(["50", "200", "500"]);
    expect(cells("Storage")).toEqual(["200 MB", "1,000 MB", "3,000 MB"]);
    expect(cells("Largest file")).toEqual(["10 MB", "25 MB", "50 MB"]);
  });

  it("tints the emphasised plan's column", () => {
    render(<PlanCompareTable plans={seeded} emphasisedCode="standard" />);
    expect(screen.getByRole("columnheader", { name: /^Standard/ })).toHaveClass("is-featured");
    expect(screen.getByRole("columnheader", { name: /^Starter/ })).not.toHaveClass("is-featured");
    const row = screen.getByRole("rowheader", { name: "Storage" }).closest("tr") as HTMLElement;
    expect(within(row).getAllByRole("cell").map((cell) => cell.classList.contains("is-featured"))).toEqual([false, true, false]);
  });

  it("writes Unlimited for limits of 0 and handles unpriced models, missing defaults and plugins not set up", () => {
    const [, standard] = seeded;
    const plan: PublicPlan = {
      ...standard,
      features: { files: true, images: true, plugins: ["gmail"] },
      plugins: [{ id: "gmail", name: "Gmail", available: false }],
      limits: {
        monthly_token_limit: 0,
        rate_limit_per_min: 0,
        max_upload_mb: 100,
        max_documents: 0,
        max_storage_mb: 0,
        monthly_image_limit: 0,
        vision_monthly_token_limit: 0,
      },
      chat_models: [makePlanModel({ id: 9, display_name: "Local Llama", allowance_rate: null, answers: null })],
      default_chat_model: null,
    };
    const priced = { ...plan, code: "priced", limits: { ...plan.limits, monthly_token_limit: 1_000 } };
    render(<PlanCompareTable plans={[plan, priced]} />);
    expect(cells(/^Gmail plugin/)).toEqual(["Not available yet", "Not available yet"]);
    expect(cells("Local Llama")).toEqual(["Unlimited", "Included"]);
    expect(cells("New chats start with")).toEqual(["—None", "—None"]);
    expect(screen.getByRole("rowheader", { name: /^Chat allowance/ })).toHaveTextContent("Standard tokens");
    expect(cells(/^Chat allowance/)).toEqual(["Unlimited", "1,000"]);
    expect(cells("Images")).toEqual(["Unlimited", "Unlimited"]);
    expect(cells("Scanned PDF pages read")).toEqual(["Unlimited", "Unlimited"]);
    expect(cells("Requests a minute")).toEqual(["Unlimited", "Unlimited"]);
    expect(cells("Documents")).toEqual(["Unlimited", "Unlimited"]);
    expect(cells("Largest file")).toEqual(["100 MB", "100 MB"]);
  });

  it("rounds scanned pages to at least one", () => {
    const [starter] = seeded;
    render(<PlanCompareTable plans={[{ ...starter, limits: { ...starter.limits, vision_monthly_token_limit: 1_000 } }]} />);
    expect(cells("Scanned PDF pages read")).toEqual(["About 1"]);
  });
});
