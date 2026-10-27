import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { PublicPlan } from "../../api/types";
import { makePlanModel, makePublicPlan, makeSeededOffers } from "../../test/billing";
import PlanCard, { PlanGridSkeleton } from "./PlanCard";

function renderCard(plan: PublicPlan, props: Omit<Parameters<typeof PlanCard>[0], "plan"> = {}) {
  render(
    <ul aria-label="Plans">
      <PlanCard plan={plan} {...props} />
    </ul>,
  );
  return screen.getByRole("listitem", { name: plan.name });
}

function features(card: HTMLElement): string[] {
  const included = within(card).getByRole("heading", { name: "Included" });
  return within(included.parentElement as HTMLElement)
    .getAllByRole("listitem")
    .map((item) => item.textContent ?? "");
}

/** The wide layout's value of a limit (the phone's tile hides some words with CSS). */
function limit(card: HTMLElement, term: string): string {
  const dt = within(card).getByText(term, { selector: "dt, dt > .plans-limit-wide" }).closest("dt");
  return dt?.nextElementSibling?.textContent ?? "";
}

const [starter, standard, professional] = makeSeededOffers();

describe("PlanCard", () => {
  it("is a list item named by its heading, with the description and the price read in full", () => {
    const card = renderCard(standard);
    expect(within(card).getByRole("heading", { level: 2, name: "Standard" })).toBeInTheDocument();
    expect(card).toHaveTextContent("Adds files from chat and Claude Sonnet for regular work.");
    expect(within(card).getByText("2,000 rupees a month, for 30 days")).toHaveClass("sr-only");
    expect(within(card).getByText("₹2,000").parentElement).toHaveAttribute("aria-hidden", "true");
    expect(card).toHaveTextContent("/ month");
    expect(card).toHaveTextContent("30 days, paid in advance");
  });

  it("takes a lower heading level, with its section labels one below", () => {
    const card = renderCard(standard, { headingLevel: 3 });
    expect(within(card).getByRole("heading", { level: 3, name: "Standard" })).toBeInTheDocument();
    expect(within(card).getByRole("heading", { level: 4, name: "Included" })).toBeInTheDocument();
    expect(within(card).getByRole("heading", { level: 4, name: "Limits" })).toBeInTheDocument();
  });

  it.each([
    ["recommended", "Recommended", "plans-flag"],
    ["current", "Current plan", "plans-flag-current"],
    ["last", "Your last plan", "plans-flag-current"],
  ] as const)("flags the %s card in words", (emphasis, label, flagClass) => {
    const card = renderCard(standard, { emphasis });
    expect(card).toHaveClass("plans-card-featured");
    expect(within(card).getByText(label)).toHaveClass(flagClass);
  });

  it("has no flag without emphasis", () => {
    const card = renderCard(standard, { emphasis: null });
    expect(card).not.toHaveClass("plans-card-featured");
    expect(within(card).queryByText(/Recommended|Current plan|Your last plan/)).not.toBeInTheDocument();
  });

  it("lists the features in a fixed order, saying in words what isn't included", () => {
    expect(features(renderCard(starter))).toEqual([
      "Chat with your documentsand the shared knowledge baseChat",
      "Files from chatFilesNot included, not included",
      "Images from chatImagesNot included, not included",
      "Gmail pluginGmailNot included, not included",
    ]);
  });

  it("shows every included feature with its detail", () => {
    expect(features(renderCard(professional))).toEqual([
      "Chat with your documentsand the shared knowledge baseChat",
      "Files from chatPDF, Word, Excel and MarkdownFiles",
      "Images from chatImages",
      "Gmail pluginSearch, read, draft and sendGmail",
    ]);
  });

  it("says a plugin in the plan isn't available yet when the admin hasn't set it up", () => {
    const plan = { ...professional, plugins: [{ id: "gmail", name: "Gmail", available: false }] };
    const card = renderCard(plan);
    expect(features(card)[3]).toBe("Gmail (not available yet)Gmail, not available yet");
    expect(within(card).getByText("Gmail (not available yet)").closest("li")).toHaveClass("plans-feature-off");
  });

  it("shows the limits, with the chat allowance as answers with the default model", () => {
    const card = renderCard(standard);
    expect(limit(card, "Chat")).toBe("About 260 answersa month with Claude Sonnet 5.5");
    expect(within(card).getByText("Answers")).toHaveClass("plans-limit-narrow");
    expect(within(card).getByText("About")).toHaveClass("plans-limit-wide");
    expect(limit(card, "Documents")).toBe("200up to 25 MB each");
    expect(limit(card, "Storage")).toBe("1,000 MB");
    expect(limit(card, "Models")).toBe("3 models");
    expect(within(card).queryByText("Images", { selector: "dt" })).not.toBeInTheDocument();
  });

  it("shows images only with them, and the models by name or as all of them", () => {
    const pro = renderCard(professional);
    expect(limit(pro, "Images")).toBe("60a month");
    expect(limit(pro, "Models")).toBe("All models");
    expect(limit(renderCard(starter), "Models")).toBe("Claude Haiku 5.5, GPT-5.4 mini");
  });

  it("names the models a plan adds to the next lower plan's whole list", () => {
    expect(limit(renderCard(standard, { lower: starter }), "Models")).toBe("Starter's, plus Claude Sonnet 5.5");
    expect(limit(renderCard(professional, { lower: standard }), "Models")).toBe("All models");
    const narrower = { ...standard, name: "Narrower", chat_models: standard.chat_models.slice(1) };
    expect(limit(renderCard(narrower, { lower: starter }), "Models")).toBe("GPT-5.4 mini, Claude Sonnet 5.5");
    const many = [6, 7, 8].map((id) => makePlanModel({ id, display_name: `Model ${id}` }));
    const wider = { ...standard, name: "Wider", chat_models: [...starter.chat_models, ...many] };
    expect(limit(renderCard(wider, { lower: starter }), "Models")).toBe("Starter's, plus 3 more models");
  });

  it("writes Unlimited for limits of 0, and plain tokens when no answers figure exists", () => {
    const unlimited = makePublicPlan({
      name: "Unlimited",
      features: { files: true, images: true, plugins: [] },
      limits: { ...standard.limits, monthly_token_limit: 0, max_documents: 0, max_storage_mb: 0, monthly_image_limit: 0 },
      answers_estimate: null,
      chat_models: [],
    });
    const card = renderCard(unlimited);
    expect(limit(card, "Chat")).toBe("Unlimited");
    expect(limit(card, "Documents")).toBe("Unlimitedup to 25 MB each");
    expect(limit(card, "Storage")).toBe("Unlimited");
    expect(limit(card, "Images")).toBe("Unlimited");
    expect(limit(card, "Models")).toBe("None");

    const unpriced = makePublicPlan({
      name: "Local",
      answers_estimate: null,
      default_chat_model: makePlanModel({ display_name: "Local Llama", allowance_rate: null, answers: null }),
    });
    const unpricedCard = renderCard(unpriced);
    expect(limit(unpricedCard, "Chat")).toBe("2,000,000 standard tokensa month");
    expect(within(unpricedCard).getByText("Chat tokens")).toHaveClass("plans-limit-narrow");
    const noDefault = makePublicPlan({ name: "Plain", default_chat_model: null });
    expect(limit(renderCard(noDefault), "Chat")).toBe("About 260 answersa month");
  });

  it("renders the actions and badges it is given; without actions the card has no actions row", () => {
    const withActions = renderCard(standard, {
      actions: <button type="button">Choose Standard</button>,
      badges: <span className="badge">Test mode</span>,
    });
    expect(within(withActions).getByRole("button", { name: "Choose Standard" }).parentElement).toHaveClass("plans-actions");
    expect(within(withActions).getByText("Test mode").parentElement).toHaveClass("plans-card-badges");
    expect(withActions).not.toHaveClass("plans-card-static");

    const bare = renderCard({ ...starter, description: "" });
    expect(bare).toHaveClass("plans-card-static");
    expect(bare.querySelector(".plans-actions, .plans-card-badges, .plans-card-desc")).toBeNull();
  });
});

describe("PlanGridSkeleton", () => {
  it("is a busy list of hidden placeholder cards, announced as loading", () => {
    render(<PlanGridSkeleton />);
    const list = screen.getByRole("list", { name: "Plans" });
    expect(list).toHaveAttribute("aria-busy", "true");
    expect(list.querySelectorAll("li.plans-card-skeleton[aria-hidden='true']")).toHaveLength(3);
    expect(screen.getByRole("status")).toHaveTextContent("Loading plans…");
  });

  it("takes the number of cards", () => {
    render(<PlanGridSkeleton count={2} />);
    expect(document.querySelectorAll(".plans-card-skeleton")).toHaveLength(2);
  });
});
