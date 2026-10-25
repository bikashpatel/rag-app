import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { AdminModel, ModelTestResult } from "../../api/types";
import { makeAdminModel } from "../../test/fixtures";
import { deferred, errorResponse, stubFetch, type FetchCall, type Route } from "../../test/http";
import ModelsTab from "./ModelsTab";

const claude = makeAdminModel();
const gpt = makeAdminModel({
  id: 2,
  display_name: "GPT-5.5",
  litellm_model: "openai/gpt-5.5",
  is_default: false,
  enabled: false,
  max_output_tokens: 8_000,
  input_cost_per_mtok: null,
  output_cost_per_mtok: null,
  api_key_env: "OPENAI_API_KEY",
});
const embedding = makeAdminModel({
  id: 3,
  display_name: "Text Embedding 3 Large",
  litellm_model: "openai/text-embedding-3-large",
  kind: "embedding",
  embedding_dim: 3_072,
  input_cost_per_mtok: 0.13,
  output_cost_per_mtok: null,
});
const spareEmbedding = makeAdminModel({
  id: 4,
  display_name: "Text Embedding 3 Small",
  litellm_model: "openai/text-embedding-3-small",
  kind: "embedding",
  is_default: false,
  embedding_dim: null,
});

function testResult(overrides: Partial<ModelTestResult> = {}): ModelTestResult {
  return { ok: true, message: "The model answered.", latency_ms: 812.4, embedding_dim: null, image_data_url: null, ...overrides };
}

function stubModels(extra: Record<string, Route> = {}, models: AdminModel[] = [claude, gpt, embedding, spareEmbedding]) {
  return stubFetch({ "GET /admin/models": models, ...extra });
}

const section = (name: string) => within(screen.getByRole("region", { name }));
const row = (name: string) => within(screen.getAllByRole("row", { name: new RegExp(`^${name}`) })[0]);

function patches(calls: FetchCall[]): { path: string; body: unknown }[] {
  return calls.filter((call) => call.method === "PATCH").map(({ path, body }) => ({ path, body }));
}

async function renderTab() {
  const onChanged = vi.fn();
  render(<ModelsTab onChanged={onChanged} />);
  await screen.findByRole("region", { name: "Chat models" });
  return onChanged;
}

describe("ModelsTab", () => {
  it("groups the models by kind with their settings", async () => {
    stubModels();
    await renderTab();

    const chat = section("Chat models");
    expect(chat.getAllByRole("row")).toHaveLength(3);
    expect(row("Claude Opus 5.5").getByText("anthropic/claude-opus-5-5")).toBeInTheDocument();
    expect(row("Claude Opus 5.5").getByText("Default")).toBeInTheDocument();
    expect(row("Claude Opus 5.5").getByRole("switch", { name: "Enable Claude Opus 5.5" })).toBeChecked();
    expect(row("Claude Opus 5.5").getByText("default")).toBeInTheDocument();

    const price = (usd: number) => `$${usd.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`;
    expect(screen.getByRole("row", { name: /^Claude Opus 5\.5/ })).toHaveTextContent(`${price(5)} · ${price(25)}`);

    expect(row("GPT-5.5").getByText("OPENAI_API_KEY")).toBeInTheDocument();
    expect(row("GPT-5.5").getByRole("switch", { name: "Enable GPT-5.5" })).not.toBeChecked();
    expect(row("GPT-5.5").getByRole("button", { name: "Make GPT-5.5 the default chat model" })).toBeDisabled();
    expect(row("GPT-5.5").getAllByText("auto")).toHaveLength(2);

    const embeddings = section("Embedding models");
    expect(embeddings.getByRole("columnheader", { name: "Dimensions" })).toBeInTheDocument();
    expect(row("Text Embedding 3 Large").getByText("Active")).toBeInTheDocument();
    expect(row("Text Embedding 3 Small").getByText("—")).toBeInTheDocument();

    expect(section("Vision models").getByText("No vision models yet.")).toBeInTheDocument();
    expect(section("Image models").getByText("No image models yet.")).toBeInTheDocument();
    expect(
      section("Image models").getByText("Add one and make it active so users can ask for images in chat."),
    ).toBeInTheDocument();
  });

  it("shows a failed load with a retry", async () => {
    const replies = [errorResponse(503, "The model registry is unavailable."), [claude]];
    stubFetch({ "GET /admin/models": () => replies.shift() });
    const user = userEvent.setup();
    render(<ModelsTab onChanged={() => {}} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("The model registry is unavailable.");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("row", { name: /^Claude Opus 5\.5/ })).toBeInTheDocument();
  });

  it("enables a model", async () => {
    const calls = stubModels({ "PATCH /admin/models/2": { ...gpt, enabled: true } });
    const user = userEvent.setup();
    const onChanged = await renderTab();

    await user.click(screen.getByRole("switch", { name: "Enable GPT-5.5" }));

    expect(await section("Chat models").findByText("GPT-5.5 enabled.")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Enable GPT-5.5" })).toBeChecked();
    expect(screen.getByRole("button", { name: "Make GPT-5.5 the default chat model" })).toBeEnabled();
    expect(patches(calls)).toEqual([{ path: "/admin/models/2", body: { enabled: true } }]);
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("turns the switch back and reports the error when the change fails", async () => {
    const pending = deferred<Response>();
    stubModels({ "PATCH /admin/models/1": () => pending.promise });
    const user = userEvent.setup();
    await renderTab();

    const toggle = screen.getByRole("switch", { name: "Enable Claude Opus 5.5" });
    await user.click(toggle);
    expect(toggle).not.toBeChecked();
    expect(toggle).toBeDisabled();

    pending.resolve(errorResponse(400, "The default chat model can't be disabled."));
    expect(await section("Chat models").findByText("The default chat model can't be disabled.")).toBeInTheDocument();
    expect(toggle).toBeChecked();
    expect(toggle).toBeEnabled();
  });

  it("locks the active embedding model's switch", async () => {
    stubModels();
    await renderTab();

    const locked = screen.getByRole("switch", { name: "Enable Text Embedding 3 Large" });
    expect(locked).toBeDisabled();
    expect(locked).toHaveAccessibleDescription(
      "The active embedding model can't be disabled. Make another embedding model active first.",
    );
    expect(screen.getByRole("switch", { name: "Enable Text Embedding 3 Small" })).toBeEnabled();
  });

  it("runs a test and shows the result", async () => {
    const pending = deferred<ModelTestResult>();
    const calls = stubModels({ "POST /admin/models/1/test": () => pending.promise });
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByRole("button", { name: "Test Claude Opus 5.5" }));
    expect(screen.getByText("Sending a short test request to Claude Opus 5.5…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Test Claude Opus 5.5" })).toBeDisabled();

    pending.resolve(testResult());
    const result = await screen.findByText("Claude Opus 5.5 works");
    expect(result.closest("[role='status']")).toHaveTextContent("Claude Opus 5.5 works · 812 msThe model answered.");
    expect(calls.filter((call) => call.method === "POST").map((call) => call.path)).toEqual(["/admin/models/1/test"]);

    await user.click(screen.getByRole("button", { name: "Dismiss test result" }));
    expect(screen.queryByText("Claude Opus 5.5 works")).not.toBeInTheDocument();
  });

  it("reports a failed test as an alert", async () => {
    stubModels({
      "POST /admin/models/2/test": testResult({ ok: false, message: "AuthenticationError: invalid x-api-key", latency_ms: 0 }),
    });
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByRole("button", { name: "Test GPT-5.5" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("GPT-5.5 failed");
    expect(alert).toHaveTextContent("AuthenticationError: invalid x-api-key");
  });

  it("records the dimensions an embedding test found", async () => {
    stubModels({ "POST /admin/models/4/test": testResult({ latency_ms: 95, embedding_dim: 1_536 }) });
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByRole("button", { name: "Test Text Embedding 3 Small" }));
    expect(await screen.findByText(/dimensions/)).toBeInTheDocument();
    expect(row("Text Embedding 3 Small").getByRole("cell", { name: (1_536).toLocaleString() })).toBeInTheDocument();
  });

  it("makes another chat model the default without asking", async () => {
    const enabledGpt = { ...gpt, enabled: true };
    const calls = stubModels(
      { "PATCH /admin/models/2": { ...enabledGpt, is_default: true } },
      [claude, enabledGpt, embedding],
    );
    const user = userEvent.setup();
    const onChanged = await renderTab();

    await user.click(screen.getByRole("button", { name: "Make GPT-5.5 the default chat model" }));

    expect(await section("Chat models").findByText("GPT-5.5 is now the default chat model.")).toBeInTheDocument();
    expect(row("GPT-5.5").getByText("Default")).toBeInTheDocument();
    expect(row("Claude Opus 5.5").getByRole("button", { name: "Make Claude Opus 5.5 the default chat model" })).toBeEnabled();
    expect(patches(calls)).toEqual([{ path: "/admin/models/2", body: { is_default: true } }]);
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("asks before switching the active embedding model", async () => {
    const calls = stubModels({ "PATCH /admin/models/4": { ...spareEmbedding, is_default: true } });
    const user = userEvent.setup();
    const onChanged = await renderTab();

    await user.click(screen.getByRole("button", { name: "Make Text Embedding 3 Small the active embedding model" }));
    const confirm = screen.getByRole("group", {
      name: "Index and search with this model from now on? Existing documents must then be re-indexed.",
    });
    expect(patches(calls)).toEqual([]);
    await user.click(within(confirm).getByRole("button", { name: "Make active" }));

    expect(
      await section("Embedding models").findByText(
        "Text Embedding 3 Small is now the active embedding model. Re-index documents so search can find them.",
      ),
    ).toBeInTheDocument();
    expect(row("Text Embedding 3 Small").getByText("Active")).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it("deletes a model after confirmation", async () => {
    const calls = stubModels({ "DELETE /admin/models/4": () => new Response(null, { status: 204 }) });
    const user = userEvent.setup();
    const onChanged = await renderTab();

    await user.click(screen.getByRole("button", { name: "Delete Text Embedding 3 Small" }));
    const confirm = screen.getByRole("group", { name: "Delete Text Embedding 3 Small? Its stored vectors are removed too." });
    await user.click(within(confirm).getByRole("button", { name: "Delete model" }));

    await waitFor(() => expect(screen.queryByRole("row", { name: /^Text Embedding 3 Small/ })).not.toBeInTheDocument());
    expect(section("Embedding models").getByText("Deleted Text Embedding 3 Small.")).toBeInTheDocument();
    expect(calls.filter((call) => call.method === "DELETE").map((call) => call.path)).toEqual(["/admin/models/4"]);
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it("opens the model form from a section", async () => {
    stubModels();
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByRole("button", { name: "Add vision model" }));
    expect(screen.getByRole("dialog", { name: "Add model" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Edit GPT-5.5" }));
    expect(screen.getByRole("dialog", { name: "Edit GPT-5.5" })).toBeInTheDocument();
  });
});
