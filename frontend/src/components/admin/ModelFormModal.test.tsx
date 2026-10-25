import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { AdminModel, AdminModelCreate, ModelKind } from "../../api/types";
import { makeAdminModel } from "../../test/fixtures";
import { deferred, errorResponse, stubFetch, type FetchCall, type Route } from "../../test/http";
import ModelFormModal from "./ModelFormModal";

const embedding = makeAdminModel({
  id: 3,
  display_name: "Text Embedding 3 Large",
  litellm_model: "openai/text-embedding-3-large",
  kind: "embedding",
  embedding_dim: 3_072,
  input_cost_per_mtok: 0.13,
  output_cost_per_mtok: null,
});

function renderForm(model: AdminModel | null = null, kind: ModelKind = "chat", routes: Record<string, Route> = {}) {
  const calls = stubFetch({
    "POST /admin/models": ({ body }) => makeAdminModel({ ...(body as AdminModelCreate), id: 9 }),
    [`PATCH /admin/models/${model?.id ?? 0}`]: ({ body }) => ({ ...model, ...(body as object) }),
    ...routes,
  });
  const onClose = vi.fn();
  const onSaved = vi.fn();
  render(<ModelFormModal model={model} kind={kind} onClose={onClose} onSaved={onSaved} />);
  const dialog = within(screen.getByRole("dialog"));
  return { calls, onClose, onSaved, dialog };
}

const field = (name: string) => screen.getByRole("textbox", { name });
const sent = (calls: FetchCall[]) => calls.filter((call) => call.method !== "GET").map(({ method, path, body }) => ({ method, path, body }));

describe("ModelFormModal: adding a model", () => {
  it("adds a chat model with blank optional fields sent as null", async () => {
    const user = userEvent.setup();
    const { calls, onSaved } = renderForm();

    expect(screen.getByRole("dialog", { name: "Add model" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Chat" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Chat" })).toHaveFocus();
    await user.type(field("Display name"), "  GPT-5.4 mini ");
    await user.type(field("LiteLLM model"), "openai/gpt-5.4-mini");
    await user.click(screen.getByRole("button", { name: "Add model" }));

    expect(sent(calls)).toEqual([
      {
        method: "POST",
        path: "/admin/models",
        body: {
          display_name: "GPT-5.4 mini",
          litellm_model: "openai/gpt-5.4-mini",
          api_base: null,
          api_key_env: null,
          enabled: true,
          input_cost_per_mtok: null,
          max_output_tokens: null,
          temperature: null,
          output_cost_per_mtok: null,
          kind: "chat",
          is_default: false,
        },
      },
    ]);
    expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ id: 9, display_name: "GPT-5.4 mini" }), true);
  });

  it("requires a name and a model, focusing the first missing one", async () => {
    const user = userEvent.setup();
    const { calls } = renderForm();

    await user.click(screen.getByRole("button", { name: "Add model" }));
    expect(field("Display name")).toHaveAttribute("aria-invalid", "true");
    expect(field("Display name")).toHaveAccessibleDescription("This field is required. Shown to users in the model picker.");
    expect(field("LiteLLM model")).toHaveAttribute("aria-invalid", "true");
    expect(field("Display name")).toHaveFocus();
    expect(sent(calls)).toEqual([]);

    await user.type(field("Display name"), "Local Llama");
    expect(field("Display name")).not.toHaveAttribute("aria-invalid");
  });

  it.each<[string, string, string]>([
    ["LiteLLM model", "openai/gpt 5", "Must not contain spaces."],
    ["API base (optional)", "localhost:11434", "Enter a full http:// or https:// URL."],
    ["API key env var (optional)", "groq_key", "Use capital letters, digits and underscores, ending in _API_KEY."],
    ["API key env var (optional)", "QDRANT_API_KEY", "QDRANT_API_KEY is reserved for the app's own infrastructure."],
    ["Max output tokens", "1.5", "Enter a whole number."],
    ["Max output tokens", "0", "Must be at least 1."],
    ["Temperature", "2.5", "Must be at most 2."],
    ["Temperature", "warm", "Enter a number."],
    ["Input cost per 1M tokens (USD)", "-1", "Must be at least 0."],
  ])("rejects %s %j", async (label, value, message) => {
    const user = userEvent.setup();
    const { calls } = renderForm();
    await user.type(field("Display name"), "Model");
    if (label !== "LiteLLM model") await user.type(field("LiteLLM model"), "openai/gpt-5.5");
    await user.type(field(label), value);
    await user.click(screen.getByRole("button", { name: "Add model" }));

    expect(field(label)).toHaveAttribute("aria-invalid", "true");
    expect(field(label)).toHaveAccessibleDescription(expect.stringContaining(message));
    expect(field(label)).toHaveFocus();
    expect(sent(calls)).toEqual([]);
  });

  it("sends the numbers, the key variable and the default switch", async () => {
    const user = userEvent.setup();
    const { calls } = renderForm();
    await user.type(field("Display name"), "Groq Llama");
    await user.type(field("LiteLLM model"), "groq/llama-3.3-70b");
    await user.type(field("API base (optional)"), "https://api.groq.com/openai/v1");
    await user.type(field("API key env var (optional)"), "GROQ_API_KEY");
    await user.type(field("Max output tokens"), "4096");
    await user.type(field("Temperature"), "0.7");
    await user.type(field("Input cost per 1M tokens (USD)"), "0.59");
    await user.type(field("Output cost per 1M tokens (USD)"), ".79");
    await user.click(screen.getByRole("switch", { name: "Default chat model" }));
    await user.click(screen.getByRole("button", { name: "Add model" }));

    expect(sent(calls)[0].body).toEqual({
      display_name: "Groq Llama",
      litellm_model: "groq/llama-3.3-70b",
      api_base: "https://api.groq.com/openai/v1",
      api_key_env: "GROQ_API_KEY",
      enabled: true,
      input_cost_per_mtok: 0.59,
      max_output_tokens: 4096,
      temperature: 0.7,
      output_cost_per_mtok: 0.79,
      kind: "chat",
      is_default: true,
    });
  });

  it("only makes an enabled model the default", async () => {
    const user = userEvent.setup();
    const { calls } = renderForm();
    const makeDefault = screen.getByRole("switch", { name: "Default chat model" });
    await user.click(makeDefault);
    await user.click(screen.getByRole("switch", { name: "Enabled" }));

    expect(makeDefault).toBeDisabled();
    expect(makeDefault).not.toBeChecked();
    expect(makeDefault).toHaveAccessibleDescription("Enable the model to make it the default.");
    await user.type(field("Display name"), "Hidden");
    await user.type(field("LiteLLM model"), "openai/gpt-5.5");
    await user.click(screen.getByRole("button", { name: "Add model" }));
    expect(sent(calls)[0].body).toMatchObject({ enabled: false, is_default: false });
  });

  it("fills the model from an example, with Ollama's API base", async () => {
    const user = userEvent.setup();
    renderForm();
    await user.click(screen.getByRole("button", { name: "ollama/llama3.1" }));
    expect(field("LiteLLM model")).toHaveValue("ollama/llama3.1");
    expect(field("API base (optional)")).toHaveValue("http://host.docker.internal:11434");

    await user.clear(field("API base (optional)"));
    await user.type(field("API base (optional)"), "http://gpu-box:11434");
    await user.click(screen.getByRole("button", { name: "ollama/llama3.1" }));
    expect(field("API base (optional)")).toHaveValue("http://gpu-box:11434");
  });

  it("shows only the fields an embedding model uses", async () => {
    const user = userEvent.setup();
    const { calls } = renderForm(null, "embedding");

    expect(screen.getByRole("radio", { name: "Embedding" })).toBeChecked();
    expect(screen.queryByRole("textbox", { name: "Temperature" })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Max output tokens" })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: /^Output cost/ })).not.toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Active embedding model" })).toHaveAccessibleDescription(
      "Turn on to index documents and search queries with this model.",
    );
    await user.type(field("Display name"), "Small embeddings");
    await user.click(screen.getByRole("button", { name: "openai/text-embedding-3-small" }));
    await user.type(field("Cost per 1M tokens (USD)"), "0.02");
    await user.click(screen.getByRole("switch", { name: "Active embedding model" }));
    expect(screen.getByRole("switch", { name: "Active embedding model" })).toHaveAccessibleDescription(
      "Existing documents must be re-indexed after switching; search skips them until then.",
    );
    await user.click(screen.getByRole("button", { name: "Add model" }));

    expect(sent(calls)[0].body).toEqual({
      display_name: "Small embeddings",
      litellm_model: "openai/text-embedding-3-small",
      api_base: null,
      api_key_env: null,
      enabled: true,
      input_cost_per_mtok: 0.02,
      kind: "embedding",
      is_default: true,
    });
  });

  it("labels the prices of an image model and keeps its output cost", async () => {
    const user = userEvent.setup();
    renderForm();
    await user.click(screen.getByRole("radio", { name: "Image" }));

    expect(field("Prompt cost per 1M tokens (USD)")).toBeInTheDocument();
    expect(field("Image output cost per 1M tokens (USD)")).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Temperature" })).not.toBeInTheDocument();
    expect(field("LiteLLM model")).toHaveAttribute("placeholder", "openai/gpt-image-2");
    expect(screen.getByRole("switch", { name: "Active image model" })).toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: "Vision" }));
    expect(field("Temperature")).toBeInTheDocument();
    expect(field("Max output tokens")).toHaveAccessibleDescription("Per page, thinking included. Blank = server default (8192).");
  });

  it("shows a server error and lets the admin try again", async () => {
    const user = userEvent.setup();
    const reply = deferred<Response>();
    const { onSaved } = renderForm(null, "chat", { "POST /admin/models": () => reply.promise });
    await user.type(field("Display name"), "GPT-5.5");
    await user.type(field("LiteLLM model"), "openai/gpt-5.5");
    await user.click(screen.getByRole("button", { name: "Add model" }));

    expect(screen.getByRole("button", { name: "Add model" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Add model" })).toHaveAttribute("aria-busy", "true");
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    reply.resolve(errorResponse(409, "A chat model with this LiteLLM name already exists."));
    expect(await screen.findByRole("alert")).toHaveTextContent("A chat model with this LiteLLM name already exists.");
    expect(screen.getByRole("button", { name: "Add model" })).toBeEnabled();
    expect(onSaved).not.toHaveBeenCalled();
  });

  it("closes from Cancel", async () => {
    const user = userEvent.setup();
    const { onClose } = renderForm();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe("ModelFormModal: editing a model", () => {
  it("prefills the model and patches it without the kind", async () => {
    const user = userEvent.setup();
    const gpt = makeAdminModel({
      id: 2,
      display_name: "GPT-5.5",
      litellm_model: "openai/gpt-5.5",
      is_default: false,
      max_output_tokens: 8_000,
      temperature: 1,
      api_key_env: "OPENAI_API_KEY",
    });
    const { calls, onSaved } = renderForm(gpt);

    expect(screen.getByRole("dialog", { name: "Edit GPT-5.5" })).toBeInTheDocument();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(field("Max output tokens")).toHaveValue("8000");
    expect(field("Temperature")).toHaveValue("1");
    await user.clear(field("Temperature"));
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(sent(calls)).toEqual([
      {
        method: "PATCH",
        path: "/admin/models/2",
        body: {
          display_name: "GPT-5.5",
          litellm_model: "openai/gpt-5.5",
          api_base: null,
          api_key_env: "OPENAI_API_KEY",
          enabled: true,
          input_cost_per_mtok: 5,
          max_output_tokens: 8_000,
          temperature: null,
          output_cost_per_mtok: 25,
        },
      },
    ]);
    expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ id: 2, temperature: null }), false);
  });

  it("sends is_default only when the model becomes the default", async () => {
    const user = userEvent.setup();
    const gpt = makeAdminModel({ id: 2, display_name: "GPT-5.5", litellm_model: "openai/gpt-5.5", is_default: false });
    const { calls } = renderForm(gpt);
    await user.click(screen.getByRole("switch", { name: "Default chat model" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(sent(calls)[0].body).toMatchObject({ is_default: true });
  });

  it("locks the default and the active embedding model's switches", async () => {
    renderForm(embedding);
    const active = screen.getByRole("switch", { name: "Active embedding model" });
    expect(active).toBeChecked();
    expect(active).toBeDisabled();
    expect(active).toHaveAccessibleDescription("This is the active model. Make another embedding model active to change it.");
    expect(screen.getByRole("switch", { name: "Enabled" })).toBeDisabled();
    expect(screen.getByRole("switch", { name: "Enabled" })).toHaveAccessibleDescription(
      "Make another embedding model active before disabling this one.",
    );
  });

  it("warns that changing an embedding model discards its index", async () => {
    const user = userEvent.setup();
    const { calls } = renderForm(embedding);
    expect(screen.queryByText(/discards this model's search index/)).not.toBeInTheDocument();

    await user.clear(field("LiteLLM model"));
    await user.type(field("LiteLLM model"), "openai/text-embedding-3-small");
    expect(screen.getByText(/discards this model's search index/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(sent(calls)[0].body).toEqual({
      display_name: "Text Embedding 3 Large",
      litellm_model: "openai/text-embedding-3-small",
      api_base: null,
      api_key_env: null,
      enabled: true,
      input_cost_per_mtok: 0.13,
    });
  });
});
