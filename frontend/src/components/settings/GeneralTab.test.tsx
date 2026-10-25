import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Preferences, PreferencesUpdate } from "../../api/types";
import { setMediaQueries } from "../../test/browser";
import { makeChatModel, makeMe, makePreferences } from "../../test/fixtures";
import { deferred, errorResponse, stubFetch, type FetchCall, type Route } from "../../test/http";
import { renderWithAuth } from "../../test/render";
import GeneralTab from "./GeneralTab";

const MODELS = [
  makeChatModel(),
  makeChatModel({ id: 2, display_name: "GPT-5.5", provider: "openai", is_default: false }),
];

/** PATCH /me/preferences: stores the change and answers with every stored preference, as the server does. */
function savePreferences(initial: Preferences): (call: FetchCall) => Preferences {
  let stored = initial;
  return (call: FetchCall) => {
    stored = { ...stored, ...(call.body as PreferencesUpdate) };
    return stored;
  };
}

async function renderGeneral(preferences = makePreferences(), overrides: Record<string, Route> = {}) {
  const onClose = vi.fn();
  const calls = stubFetch({
    "GET /auth/me": makeMe({ preferences }),
    "GET /models": MODELS,
    "PATCH /me/preferences": savePreferences(preferences),
    ...overrides,
  });
  await renderWithAuth(<GeneralTab onClose={onClose} />);
  await screen.findByRole("option", { name: "GPT-5.5" });
  return { calls, onClose };
}

function patches(calls: FetchCall[]): unknown[] {
  return calls.filter((call) => call.method === "PATCH" && call.path === "/me/preferences").map((call) => call.body);
}

describe("GeneralTab", () => {
  it("shows the saved preferences", async () => {
    await renderGeneral(makePreferences({ theme: "dark", accent: "sage", chat_font_size: "large", enter_to_send: false, default_model_id: 2 }));
    expect(screen.getByRole("radio", { name: "Dark" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Sage" })).toBeChecked();
    expect(within(screen.getByRole("radiogroup", { name: "Chat font size" })).getByRole("radio", { name: "Large" })).toBeChecked();
    expect(screen.getByRole("switch", { name: "Enter to send" })).not.toBeChecked();
    expect(screen.getByRole("combobox", { name: "Default chat model" })).toHaveValue("2");
  });

  it("saves a theme at once and applies it to the page", async () => {
    const user = userEvent.setup();
    const { calls } = await renderGeneral();
    await user.click(screen.getByRole("radio", { name: "Dark" }));
    expect(screen.getByRole("radio", { name: "Dark" })).toBeChecked();
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(document.documentElement).toHaveAttribute("data-theme-preference", "dark");
    await waitFor(() => expect(patches(calls)).toEqual([{ theme: "dark" }]));
    expect(await screen.findByText("Saved")).toBeInTheDocument();
  });

  it("says which theme System resolves to", async () => {
    setMediaQueries((query) => query === "(prefers-color-scheme: dark)");
    await renderGeneral(makePreferences({ theme: "system" }));
    expect(screen.getByRole("radiogroup", { name: "Theme" })).toHaveAccessibleDescription("Currently dark, from your system.");
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
  });

  it("explains System when another theme is chosen", async () => {
    await renderGeneral(makePreferences({ theme: "light" }));
    expect(screen.getByRole("radiogroup", { name: "Theme" })).toHaveAccessibleDescription(
      "System follows your device's setting and switches with it.",
    );
  });

  it("saves the accent colour and the chat font size", async () => {
    const user = userEvent.setup();
    const { calls } = await renderGeneral();
    await user.click(screen.getByRole("radio", { name: "Ocean" }));
    await user.click(screen.getByRole("radio", { name: "Small" }));
    await waitFor(() => expect(patches(calls)).toEqual([{ accent: "ocean" }, { chat_font_size: "small" }]));
    expect(document.documentElement).toHaveAttribute("data-accent", "ocean");
    expect(document.documentElement).toHaveAttribute("data-chat-font", "small");
  });

  it("turns Enter to send off", async () => {
    const user = userEvent.setup();
    const { calls } = await renderGeneral();
    const toggle = screen.getByRole("switch", { name: "Enter to send" });
    expect(toggle).toHaveAccessibleDescription(/When off, Enter adds a new line/);
    await user.click(toggle);
    expect(toggle).not.toBeChecked();
    expect(toggle).toHaveAccessibleDescription(/When on, Enter sends and Shift Enter adds a new line\./);
    await waitFor(() => expect(patches(calls)).toEqual([{ enter_to_send: false }]));
  });

  it("offers the user's models with the server default first", async () => {
    const user = userEvent.setup();
    const { calls } = await renderGeneral(makePreferences({ default_model_id: 2 }));
    const select = screen.getByRole("combobox", { name: "Default chat model" });
    expect(within(select).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "Server default (Claude Opus 5.5)",
      "Claude Opus 5.5",
      "GPT-5.5",
    ]);
    await user.selectOptions(select, "Server default (Claude Opus 5.5)");
    await user.selectOptions(select, "Claude Opus 5.5");
    await waitFor(() => expect(patches(calls)).toEqual([{ default_model_id: null }, { default_model_id: 1 }]));
  });

  it("shows the server default for a default model that is no longer offered", async () => {
    await renderGeneral(makePreferences({ default_model_id: 99 }));
    expect(screen.getByRole("combobox", { name: "Default chat model" })).toHaveValue("");
  });

  it("reports a failed model list and loads it again on Try again", async () => {
    const user = userEvent.setup();
    let fail = true;
    stubFetch({
      "GET /auth/me": makeMe(),
      "GET /models": () => (fail ? errorResponse(503, "Model registry unavailable") : MODELS),
    });
    await renderWithAuth(<GeneralTab onClose={() => {}} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load your models. Model registry unavailable");
    expect(screen.getByRole("combobox", { name: "Default chat model" })).toBeDisabled();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("option", { name: "GPT-5.5" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Default chat model" })).toBeEnabled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("reverts a change the server refused and says why", async () => {
    const user = userEvent.setup();
    const response = deferred<Response>();
    await renderGeneral(makePreferences({ theme: "light" }), { "PATCH /me/preferences": () => response.promise });
    await user.click(screen.getByRole("radio", { name: "Dark" }));
    expect(screen.getByRole("radio", { name: "Dark" })).toBeChecked();
    response.resolve(errorResponse(422, "theme: Input should be 'system', 'light' or 'dark'"));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Couldn't save: theme: Input should be 'system', 'light' or 'dark'",
    );
    expect(screen.getByRole("radio", { name: "Light" })).toBeChecked();
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
  });

  it("clears an earlier save error on the next change", async () => {
    const user = userEvent.setup();
    let fail = true;
    const save = savePreferences(makePreferences());
    await renderGeneral(makePreferences(), {
      "PATCH /me/preferences": (call) => (fail ? errorResponse(500, null) : save(call)),
    });
    await user.click(screen.getByRole("radio", { name: "Plum" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't save: The server ran into a problem. Please try again.");
    fail = false;
    await user.click(screen.getByRole("radio", { name: "Slate" }));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(screen.getByRole("radio", { name: "Slate" })).toBeChecked();
  });

  it("closes from the panel's close button", async () => {
    const user = userEvent.setup();
    const { onClose } = await renderGeneral();
    await user.click(screen.getByRole("button", { name: "Close settings" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
