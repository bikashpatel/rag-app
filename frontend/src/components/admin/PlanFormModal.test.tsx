import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";
import type { PlanCreate, PlanUpdate } from "../../api/types";
import { makeAdminPlugin, makeSeededAdminPlans, makeSeededChatModels } from "../../test/adminTabs";
import { makeBillingAdminConfig } from "../../test/billing";
import { jsonResponse, stubFetch, type FetchCall, type Route } from "../../test/http";
import PlanFormModal from "./PlanFormModal";

const plans = makeSeededAdminPlans();
const [starter, standard, professional] = plans;
const models = makeSeededChatModels();

type Props = ComponentProps<typeof PlanFormModal>;

function renderEditor(overrides: Partial<Props> = {}, routes: Record<string, Route> = {}) {
  const calls = stubFetch(routes);
  const props: Props = {
    plan: null,
    plans,
    chatModels: models,
    plugins: [makeAdminPlugin()],
    inputs: makeBillingAdminConfig().estimate,
    hardCap: 100,
    onClose: vi.fn(),
    onSaved: vi.fn(),
    ...overrides,
  };
  render(<PlanFormModal {...props} />);
  return { props, calls };
}

const setupUser = () => userEvent.setup({ delay: null });
const field = (name: string) => screen.getByRole("textbox", { name });
const estimate = () => screen.getByRole("complementary", { name: "AI cost per period" });
const sent = (calls: FetchCall[]) =>
  calls.filter((call) => call.method !== "GET").map(({ method, path, body }) => ({ method, path, body }));

async function fillNew(user: ReturnType<typeof setupUser>) {
  await user.type(field("Code"), "team");
  await user.type(field("Name"), "Team");
  await user.type(field("Price per 30 days"), "9000");
}

describe("PlanFormModal: a new plan", () => {
  it("starts with defaults, the next rank and a live estimate", () => {
    renderEditor();
    expect(screen.getByRole("dialog", { name: "New plan" })).toBeInTheDocument();
    expect(field("Code")).toHaveFocus();
    expect(field("Rank")).toHaveValue("40");
    expect(field("Chat allowance")).toHaveValue("2000000");
    expect(field("Chat allowance")).toHaveAccessibleDescription("About 160 answers with Claude Opus 5.5.");
    expect(field("Scanned PDF pages")).toHaveAccessibleDescription("About 170 pages.");
    expect(field("Images")).toHaveAccessibleDescription("Used only with Images from chat.");
    expect(field("Largest file")).toHaveAccessibleDescription("Up to 100 MB (the server's cap).");
    expect(screen.getByRole("switch", { name: "Recommended" })).toHaveAccessibleDescription(
      "One plan at a time: saving this takes the flag from Standard.",
    );
    expect(within(estimate()).getByText("₹666")).toBeInTheDocument();
    expect(within(estimate()).getByText("₹266 · —")).toBeInTheDocument();
    expect(screen.queryByRole("radiogroup", { name: "Apply the changes to" })).not.toBeInTheDocument();
  });

  it("checks the code, name, price, rank and the price order of active plans", async () => {
    const { calls } = renderEditor();
    const user = setupUser();

    await user.type(field("Code"), "Team!");
    await user.type(field("Price per 30 days"), "12.5");
    await user.clear(field("Rank"));
    await user.type(field("Rank"), "20");
    await user.click(screen.getByRole("button", { name: "Create plan" }));
    expect(field("Code")).toHaveAccessibleDescription(expect.stringContaining("Use 2 to 32 lowercase letters"));
    expect(field("Name")).toHaveAccessibleDescription(expect.stringContaining("This field is required."));
    expect(field("Price per 30 days")).toHaveAccessibleDescription(expect.stringContaining("Enter a whole number."));
    expect(field("Rank")).toHaveAccessibleDescription(
      expect.stringContaining("Standard has rank 20. Ranks must differ."),
    );
    expect(field("Code")).toHaveFocus();

    await user.clear(field("Code"));
    await user.type(field("Code"), "standard");
    await user.clear(field("Price per 30 days"));
    await user.type(field("Price per 30 days"), "1,500");
    await user.clear(field("Rank"));
    await user.type(field("Rank"), "25");
    await user.click(screen.getByRole("button", { name: "Create plan" }));
    expect(field("Code")).toHaveAccessibleDescription(expect.stringContaining("Another plan has this code."));
    expect(field("Price per 30 days")).toHaveAccessibleDescription(
      expect.stringContaining("A higher plan must cost more: Standard (rank 20) costs ₹2,000."),
    );
    expect(sent(calls)).toEqual([]);
  });

  it("creates the plan with every field", async () => {
    const saved = { plan: { ...standard, id: 9, code: "team" }, applied_periods: 0 };
    const { props, calls } = renderEditor({}, { "POST /admin/plans": saved });
    const user = setupUser();

    await fillNew(user);
    await user.type(field("Description"), "For teams.");
    await user.click(screen.getByRole("switch", { name: "Files from chat" }));
    await user.click(screen.getByRole("switch", { name: "Gmail plugin" }));
    await user.click(screen.getByRole("switch", { name: "Recommended" }));
    await user.clear(field("Requests a minute"));
    await user.type(field("Requests a minute"), "40");
    await user.click(screen.getByRole("button", { name: "Create plan" }));

    const body: PlanCreate = {
      code: "team",
      name: "Team",
      description: "For teams.",
      price_paise: 900_000,
      rank: 40,
      status: "active",
      recommended: true,
      features: { files: true, images: false, plugins: ["gmail"] },
      limits: {
        monthly_token_limit: 2_000_000,
        monthly_image_limit: 30,
        vision_monthly_token_limit: 500_000,
        rate_limit_per_min: 40,
        max_documents: 200,
        max_storage_mb: 1_000,
        max_upload_mb: 25,
      },
      chat_model_ids: null,
      default_chat_model_id: null,
    };
    await waitFor(() => expect(props.onSaved).toHaveBeenCalledWith(saved, true));
    expect(sent(calls)).toEqual([{ method: "POST", path: "/admin/plans", body }]);
  });

  it("limits the plan to ticked models with a default, and needs at least one", async () => {
    const { props, calls } = renderEditor({}, { "POST /admin/plans": { plan: starter, applied_periods: 0 } });
    const user = setupUser();
    await fillNew(user);
    await user.click(screen.getByRole("radio", { name: "Only these" }));
    await user.click(screen.getByRole("button", { name: "Create plan" }));
    expect(screen.getByText("Choose at least one chat model.")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Claude Opus 5.5" })).toHaveFocus();

    await user.click(screen.getByRole("checkbox", { name: "Claude Haiku 5.5" }));
    await user.click(screen.getByRole("checkbox", { name: "Claude Sonnet 5.5" }));
    expect(field("Chat allowance")).toHaveAccessibleDescription("About 6,600 answers with Claude Haiku 5.5.");
    await user.click(screen.getByRole("button", { name: "Make Claude Sonnet 5.5 the default" }));
    expect(screen.getByText("Default")).toBeInTheDocument();
    expect(field("Chat allowance")).toHaveAccessibleDescription("About 330 answers with Claude Sonnet 5.5.");

    await user.click(screen.getByRole("checkbox", { name: "Claude Sonnet 5.5" }));
    expect(screen.queryByText("Default")).not.toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: "Claude Sonnet 5.5" }));
    await user.click(screen.getByRole("button", { name: "Make Claude Sonnet 5.5 the default" }));
    await user.click(screen.getByRole("button", { name: "Create plan" }));
    await waitFor(() => expect(props.onSaved).toHaveBeenCalled());
    expect(sent(calls)[0].body).toEqual(expect.objectContaining({ chat_model_ids: [4, 5], default_chat_model_id: 5 }));
  });

  it("explains a plugin that isn't set up and a retired plan's recommended switch", async () => {
    renderEditor({ plugins: [makeAdminPlugin({ configured: false })] });
    const user = setupUser();
    expect(screen.getByRole("switch", { name: "Gmail plugin" })).toHaveAccessibleDescription(
      expect.stringContaining("Not set up yet: users see it as not available."),
    );
    await user.click(screen.getByRole("switch", { name: "Recommended" }));
    await user.click(screen.getByRole("radio", { name: "Retired" }));
    expect(screen.getByRole("switch", { name: "Recommended" })).toBeDisabled();
    expect(screen.getByRole("switch", { name: "Recommended" })).not.toBeChecked();
    expect(screen.getByText("Retired: nobody can buy or renew it; current periods run on.")).toBeInTheDocument();
  });

  it("says when a plugin is turned off and when no chat model exists", async () => {
    renderEditor({ plugins: [makeAdminPlugin({ enabled: false })], chatModels: [] });
    const user = setupUser();
    expect(screen.getByRole("switch", { name: "Gmail plugin" })).toHaveAccessibleDescription(
      expect.stringContaining("Turned off in the Plugins tab."),
    );
    expect(field("Chat allowance")).toHaveAccessibleDescription("Standard tokens: each model counts at its rate.");
    await user.click(screen.getByRole("radio", { name: "Only these" }));
    expect(screen.getByText("No chat models yet. Add one in the Models tab.")).toBeInTheDocument();
  });
});

describe("PlanFormModal: editing", () => {
  it("shows the plan, what changed, and the estimate as it is typed", async () => {
    renderEditor({ plan: professional });
    const user = setupUser();
    expect(screen.getByRole("dialog", { name: "Edit plan: Professional" })).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Code" })).not.toBeInTheDocument();
    expect(field("Price per 30 days")).toHaveValue("5000");
    expect(field("Chat allowance")).toHaveAccessibleDescription("About 290 answers with Claude Opus 5.5.");
    expect(within(estimate()).getByText("₹1,836")).toBeInTheDocument();
    expect(within(estimate()).getByText("₹4,882")).toBeInTheDocument();
    expect(within(estimate()).queryByText(/High: over 60%/)).not.toBeInTheDocument();

    await user.clear(field("Chat allowance"));
    await user.type(field("Chat allowance"), "9,000,000");
    expect(field("Chat allowance")).toHaveAccessibleDescription(
      "Was 5,000,000. About 520 answers with Claude Opus 5.5.",
    );
    expect(within(estimate()).getByText("High: over 60% of what you keep")).toBeInTheDocument();

    await user.clear(field("Chat allowance"));
    await user.type(field("Chat allowance"), "0");
    expect(field("Chat allowance")).toHaveAccessibleDescription("Was 5,000,000. Unlimited: no cost ceiling.");
    expect(within(estimate()).getByText("No ceiling: an allowance is unlimited")).toBeInTheDocument();
    expect(within(estimate()).getAllByText("No ceiling").length).toBeGreaterThan(0);

    await user.selectOptions(screen.getByRole("combobox", { name: "New chats start with" }), "GPT-5.5");
    expect(field("Chat allowance")).toHaveAccessibleDescription("Was 5,000,000. Unlimited: no cost ceiling.");
  });

  it("applies to current periods only when chosen, and asks again before lowering paid periods", async () => {
    const saved = { plan: professional, applied_periods: 3 };
    let confirmed = false;
    const { props, calls } = renderEditor(
      { plan: professional },
      {
        "PATCH /admin/plans/3": (call) => {
          if ((call.body as PlanUpdate).confirm_reduction) {
            confirmed = true;
            return saved;
          }
          return jsonResponse(
            { detail: "This lowers what 3 users paid for until their periods end.", code: "confirmation_required" },
            { status: 409 },
          );
        },
      },
    );
    const user = setupUser();
    const choice = screen.getByRole("radiogroup", { name: "Apply the changes to" });
    expect(within(choice).getByRole("radio", { name: "Apply to new periods only" })).toBeChecked();
    await user.click(
      within(choice).getByRole("radio", { name: "Also apply to the 3 current and scheduled Professional periods" }),
    );
    await user.clear(field("Images"));
    await user.type(field("Images"), "20");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This lowers what 3 users paid for until their periods end.",
    );
    expect(props.onSaved).not.toHaveBeenCalled();
    // The question takes the focus: the Save button's request is over and Enter answers it.
    await waitFor(() => expect(screen.getByRole("button", { name: "Apply anyway" })).toHaveFocus());
    await user.click(screen.getByRole("button", { name: "Apply anyway" }));
    await waitFor(() => expect(props.onSaved).toHaveBeenCalledWith(saved, false));
    expect(confirmed).toBe(true);
    const patches = sent(calls).map((call) => call.body as PlanUpdate);
    expect(patches.map((body) => [body.apply_to_current, body.confirm_reduction])).toEqual([
      [true, undefined],
      [true, true],
    ]);
    expect(patches[0].limits?.monthly_image_limit).toBe(20);
  });

  it("shows other server errors in the form", async () => {
    renderEditor(
      { plan: starter },
      { "PATCH /admin/plans/1": () => jsonResponse({ detail: "Unknown plugin: drive" }, { status: 422 }) },
    );
    const user = setupUser();
    expect(screen.queryByRole("radiogroup", { name: "Apply the changes to" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Unknown plugin: drive");
    expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Save changes" })).not.toHaveAttribute("aria-disabled");
    expect(screen.getByRole("button", { name: "Save changes" })).toHaveFocus();
  });
});
