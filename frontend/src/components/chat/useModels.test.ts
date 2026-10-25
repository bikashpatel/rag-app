import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ChatModel } from "../../api/types";
import { makeChatModel } from "../../test/fixtures";
import { errorResponse, stubFetch } from "../../test/http";
import { useModels } from "./useModels";

const opus = makeChatModel();
const gpt = makeChatModel({ id: 2, display_name: "GPT-5.5", provider: "openai", is_default: false });
const mini = makeChatModel({ id: 3, display_name: "GPT-5.4 mini", provider: "openai", is_default: false });

async function loaded(models: ChatModel[], preferred: number | null = null) {
  const calls = stubFetch({ "GET /models": () => models });
  const hook = renderHook(({ preferredDefault }) => useModels(preferredDefault), {
    initialProps: { preferredDefault: preferred },
  });
  await waitFor(() => expect(hook.result.current.models).not.toBeNull());
  return { ...hook, calls };
}

describe("useModels", () => {
  it("starts with the account default when it is listed", async () => {
    const { result } = await loaded([opus, gpt], 2);
    expect(result.current.selectedId).toBe(2);
    expect(result.current.defaultId).toBe(2);
  });

  it("falls back to the server default, then the first model", async () => {
    expect((await loaded([gpt, opus], 99)).result.current.selectedId).toBe(1);
    expect((await loaded([gpt, mini])).result.current.selectedId).toBe(2);
    const empty = await loaded([]);
    expect(empty.result.current.selectedId).toBeNull();
    expect(empty.result.current.defaultId).toBeNull();
  });

  it("selects a model for this chat and resets to the default", async () => {
    const { result, rerender } = await loaded([opus, gpt, mini]);
    act(() => result.current.select(3));
    expect(result.current.selectedId).toBe(3);
    expect(result.current.defaultId).toBe(1);

    rerender({ preferredDefault: 2 });
    expect(result.current.defaultId).toBe(2);
    expect(result.current.selectedId).toBe(3);
    act(() => result.current.resetToDefault());
    expect(result.current.selectedId).toBe(2);
  });

  it("keeps a choice that is still offered after a reload, and replaces one that was withdrawn", async () => {
    let models = [opus, gpt, mini];
    const calls = stubFetch({ "GET /models": () => models });
    const { result } = renderHook(() => useModels(null));
    await waitFor(() => expect(result.current.models).toHaveLength(3));
    act(() => result.current.select(3));

    models = [opus, mini];
    act(() => result.current.reload());
    await waitFor(() => expect(result.current.models).toHaveLength(2));
    expect(result.current.selectedId).toBe(3);

    models = [gpt, opus];
    act(() => result.current.reload());
    await waitFor(() => expect(result.current.models?.[0]).toEqual(gpt));
    expect(result.current.selectedId).toBe(1);
    expect(calls).toHaveLength(3);
  });

  it("reports a failed load and keeps the list on a failed refetch", async () => {
    let fail = true;
    stubFetch({ "GET /models": () => (fail ? errorResponse(503, "Models are unavailable.") : [opus]) });
    const { result } = renderHook(() => useModels(null));
    await waitFor(() => expect(result.current.error).toBe("Models are unavailable."));
    expect(result.current.models).toBeNull();

    fail = false;
    act(() => result.current.reload());
    await waitFor(() => expect(result.current.models).toEqual([opus]));
    expect(result.current.error).toBeNull();

    stubFetch({ "GET /models": () => new Response("<html>", { status: 200 }) });
    act(() => result.current.reload());
    await waitFor(() => expect(result.current.error).toBe("Couldn't load the models."));
    expect(result.current.models).toEqual([opus]);
  });

  it("forgets the per-device choice of earlier versions", async () => {
    localStorage.setItem("rag.modelId", "2");
    await loaded([opus]);
    expect(localStorage.getItem("rag.modelId")).toBeNull();
  });
});
