import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ChatModel, PlanAccess } from "../../api/types";
import { makeChatModel, makePaidPlanAccess, makePlanAccess } from "../../test/fixtures";
import { renderWithRouter } from "../../test/render";
import ModelMenu from "./ModelMenu";
import type { ModelsState } from "./useModels";

const opus = makeChatModel();
const gpt = makeChatModel({
  id: 2,
  display_name: "GPT-5.5",
  provider: "openai",
  is_default: false,
  can_create_images: false,
  allowance_rate: 1,
});
const mini = makeChatModel({
  id: 3,
  display_name: "GPT-5.4 mini",
  provider: "openai",
  is_default: false,
  can_create_files: false,
  can_create_images: false,
  allowance_rate: null,
});

function modelsState(overrides: Partial<ModelsState> = {}): ModelsState {
  return {
    models: [opus, gpt, mini],
    error: null,
    selectedId: 2,
    defaultId: 1,
    select: vi.fn(),
    resetToDefault: vi.fn(),
    reload: vi.fn(),
    ...overrides,
  };
}

function setup(state: ModelsState = modelsState(), plan: PlanAccess | null = makePlanAccess()) {
  const user = userEvent.setup();
  const onOpenSettings = vi.fn();
  const view = renderWithRouter(<ModelMenu models={state} plan={plan} onOpenSettings={onOpenSettings} />);
  return { user, onOpenSettings, view };
}

const openMenu = async (user: ReturnType<typeof userEvent.setup>, name: string) =>
  user.click(screen.getByRole("button", { name }));

describe("ModelMenu", () => {
  it("names the selected model on the trigger and opens on it", async () => {
    const { user } = setup();
    await openMenu(user, "Model: GPT-5.5");
    expect(screen.getByRole("menu", { name: "Model" })).toBeInTheDocument();
    const selected = screen.getByRole("menuitemradio", { name: "GPT-5.5" });
    expect(selected).toHaveAttribute("aria-checked", "true");
    expect(selected).toHaveFocus();
    const opusItem = screen.getByRole("menuitemradio", { name: /Claude Opus 5\.5/ });
    expect(opusItem).toHaveAttribute("aria-checked", "false");
    expect(opusItem).toHaveTextContent("Default");
    expect(opusItem).toHaveAccessibleName("Claude Opus 5.5 (default)");
  });

  it("groups models by provider and lists what each can create", async () => {
    const { user } = setup();
    await openMenu(user, "Model: GPT-5.5");
    const anthropic = screen.getByRole("group", { name: "Anthropic" });
    const openai = screen.getByRole("group", { name: "OpenAI" });
    expect(anthropic).toContainElement(screen.getByRole("menuitemradio", { name: /Claude Opus 5\.5/ }));
    expect(openai).toContainElement(screen.getByRole("menuitemradio", { name: "GPT-5.4 mini" }));
    const opusItem = screen.getByRole("menuitemradio", { name: /Claude Opus 5\.5/ });
    expect(opusItem).toHaveTextContent("Can create Files and Images");
    expect(opusItem).toHaveAccessibleDescription(/Counts ×2 against your chat allowance$/);
    expect(screen.getByRole("menuitemradio", { name: "GPT-5.5" })).toHaveTextContent("Can create Files");
    expect(screen.getByRole("menuitemradio", { name: "GPT-5.4 mini" })).not.toHaveAccessibleDescription();
  });

  it("shows a model's rate against the allowance after its name, unless it is 1 or unknown", async () => {
    const { user } = setup();
    await openMenu(user, "Model: GPT-5.5");
    const opusItem = screen.getByRole("menuitemradio", { name: /Claude Opus 5\.5/ });
    expect(opusItem).toHaveTextContent("×2");
    expect(screen.getByTitle("Counts ×2 against your chat allowance")).toBeInTheDocument();
    expect(screen.getByRole("menuitemradio", { name: "GPT-5.5" })).not.toHaveTextContent("×");
  });

  it("shows the Files and Images tags only when the plan includes them", async () => {
    const starter = makePaidPlanAccess({ features: { files: false, images: false, plugins: [] } });
    const { user } = setup(modelsState(), starter);
    await openMenu(user, "Model: GPT-5.5");
    expect(screen.getByRole("menuitemradio", { name: /Claude Opus 5\.5/ })).toHaveAccessibleDescription(
      "Counts ×2 against your chat allowance",
    );
    expect(screen.queryByText("Files")).not.toBeInTheDocument();
    expect(screen.queryByText("Images")).not.toBeInTheDocument();
  });

  it("offers more models with a higher plan, leading to the plans", async () => {
    const { user, view } = setup(modelsState(), makePaidPlanAccess());
    await openMenu(user, "Model: GPT-5.5");
    await user.click(screen.getByRole("menuitem", { name: "More models with Professional" }));
    expect(view.location()).toBe("/plans");
  });

  it("puts a single provider's models in one unlabelled list", async () => {
    const { user } = setup(modelsState({ models: [opus, makeChatModel({ id: 4, display_name: "Claude Sonnet 5", is_default: false })], selectedId: 1 }));
    await openMenu(user, "Model: Claude Opus 5.5");
    expect(screen.getByRole("group", { name: "Models" })).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Anthropic" })).not.toBeInTheDocument();
  });

  it("selects a model and closes", async () => {
    const state = modelsState();
    const { user } = setup(state);
    await openMenu(user, "Model: GPT-5.5");
    await user.click(screen.getByRole("menuitemradio", { name: "GPT-5.4 mini" }));
    expect(state.select).toHaveBeenCalledExactlyOnceWith(3);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("links to Settings to change the default", async () => {
    const { user, onOpenSettings } = setup();
    await openMenu(user, "Model: GPT-5.5");
    await user.click(screen.getByRole("menuitem", { name: "Set the default in Settings…" }));
    expect(onOpenSettings).toHaveBeenCalledTimes(1);
  });

  it("says the models are loading", async () => {
    const { user } = setup(modelsState({ models: null, selectedId: null, defaultId: null }));
    await openMenu(user, "Model: Loading models…");
    expect(screen.getByRole("menuitem", { name: "Loading models…" })).toHaveAttribute("aria-disabled", "true");
  });

  it("falls back to the default model when the list couldn't load", async () => {
    const { user } = setup(modelsState({ models: null, error: "Couldn't load the models.", selectedId: null, defaultId: null }));
    await openMenu(user, "Model: Default model");
    const item = screen.getByRole("menuitem", { name: "Default model" });
    expect(item).toHaveAttribute("aria-disabled", "true");
    expect(item).toHaveAccessibleDescription("Couldn't load the models.");
  });

  it("explains an empty list", async () => {
    const empty: ChatModel[] = [];
    const { user } = setup(modelsState({ models: empty, selectedId: null, defaultId: null }));
    await openMenu(user, "Model: No models available");
    expect(screen.getByRole("menuitem", { name: "No models available" })).toHaveAccessibleDescription(
      "Ask your administrator to enable a chat model.",
    );
  });
});
