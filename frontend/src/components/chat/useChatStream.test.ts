import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  ChatStreamEvent,
  ConversationDetail,
  DocumentItem,
  GmailActivityCard,
  GmailImportCard,
  GmailImportItem,
} from "../../api/types";
import { deltaEvent, doneEvent, KEEPALIVE, liveStream, metaEvent } from "../../test/chatStream";
import {
  makeDocument,
  makeEmailSource,
  makeGeneratedFile,
  makeMessage,
  makeSource,
} from "../../test/fixtures";
import { errorResponse, jsonResponse, stubFetch, type FetchCall, type Route } from "../../test/http";
import { subscribeToAnnouncements, subscribeToReloadRequests } from "../documents/documentEvents";
import { isImageCard, pluginCardBusy, useChatStream, type FileCard, type SendRequest } from "./useChatStream";

const request: SendRequest = { message: "How did revenue change?", modelId: 1, documentIds: null };

function setup(conversationId: number | null = null, routes: Record<string, Route> = {}) {
  const stream = liveStream();
  const calls = stubFetch({ "POST /chat/stream": (call) => stream.respond(call), ...routes });
  const callbacks = { onConversationCreated: vi.fn(), onConversationMissing: vi.fn(), onTurnFinished: vi.fn() };
  const hook = renderHook(({ id }) => useChatStream(id, callbacks), { initialProps: { id: conversationId } });
  return { ...hook, stream, calls, callbacks };
}

/** Starts a send; returns its promise (resolves once the turn ended). */
function start(result: { current: ReturnType<typeof useChatStream> }, body: SendRequest = request): Promise<boolean> {
  let sent!: Promise<boolean>;
  act(() => {
    sent = result.current.send(body);
  });
  return sent;
}

function answer(result: { current: ReturnType<typeof useChatStream> }) {
  const last = result.current.messages.at(-1);
  if (last?.role !== "assistant") throw new Error("No answer in the thread");
  return last;
}

const createdDocument = makeDocument({
  id: 205,
  filename: "Q3 summary.docx",
  content_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  status: "pending",
  generated: true,
});

const fileEvents: ChatStreamEvent[] = [
  { event: "file", data: { call_id: "f1", kind: "file", status: "writing", title: null, format: null, chars: 0 } },
  { event: "file", data: { call_id: "f1", kind: "file", status: "writing", title: "Q3 summary", format: "docx", chars: 1_840 } },
];

afterEach(() => {
  vi.useRealTimers();
});

describe("useChatStream: a new chat", () => {
  it("streams an answer from meta to done", async () => {
    const { result, stream, calls, callbacks } = setup();
    expect(result.current.status).toBe("ready");
    const sent = start(result);

    expect(result.current.streaming).toBe(true);
    expect(result.current.messages.map((message) => [message.role, message.content])).toEqual([
      ["user", "How did revenue change?"],
      ["assistant", ""],
    ]);
    expect(answer(result).streaming).toBe(true);

    const source = makeSource();
    act(() =>
      stream.push(
        metaEvent(9, "How did revenue change?", 40),
        { event: "sources", data: { sources: [source] } },
        { event: "warning", data: { message: "Document search unavailable: the vector store timed out." } },
        KEEPALIVE,
        deltaEvent("Revenue grew "),
        deltaEvent("18% [1]."),
      ),
    );
    await waitFor(() => expect(answer(result).content).toBe("Revenue grew 18% [1]."));
    expect(callbacks.onConversationCreated).toHaveBeenCalledWith(9, "How did revenue change?");
    expect(result.current.title).toBe("How did revenue change?");
    expect(result.current.messages[0].id).toBe(40);
    expect(answer(result)).toMatchObject({
      model_name: "Claude Opus 5.5",
      sources: [source],
      warnings: ["Document search unavailable: the vector store timed out."],
    });

    act(() => {
      stream.push(doneEvent(41, { prompt_tokens: 1_500, completion_tokens: 220 }));
      stream.end();
    });
    expect(await act(() => sent)).toBe(true);
    expect(answer(result)).toMatchObject({ id: 41, prompt_tokens: 1_500, completion_tokens: 220, streaming: false, error: null });
    expect(result.current.streaming).toBe(false);
    expect(callbacks.onTurnFinished).toHaveBeenCalledWith({ conversationId: 9, title: "How did revenue change?" }, true);
    expect(calls[0].body).toEqual({ conversation_id: null, message: "How did revenue change?", model_id: 1, document_ids: null });
  });

  it("keeps the partial answer when the server ends with an error", async () => {
    const { result, stream, callbacks } = setup();
    const sent = start(result);
    act(() => {
      stream.push(metaEvent(9, "Revenue"), deltaEvent("Revenue grew"));
      stream.push({ event: "error", data: { message: "The model provider returned an error.", assistant_message_id: 41 } });
      stream.end();
    });
    expect(await act(() => sent)).toBe(true);
    expect(answer(result)).toMatchObject({
      id: 41,
      content: "Revenue grew",
      error: "The model provider returned an error.",
      streaming: false,
    });
    expect(callbacks.onTurnFinished).toHaveBeenCalledWith({ conversationId: 9, title: "Revenue" }, true);
  });

  it("undoes the turn and reports a request rejected before streaming", async () => {
    const { result, callbacks } = setup(null, {
      "POST /chat/stream": () => errorResponse(429, "Monthly token budget reached. It resets on 1 November."),
    });
    const sent = start(result);
    expect(await act(() => sent)).toBe(false);
    expect(result.current.messages).toEqual([]);
    expect(result.current.streaming).toBe(false);
    expect(result.current.sendError).toEqual({
      message: "Monthly token budget reached. It resets on 1 November.",
      code: null,
    });
    expect(callbacks.onTurnFinished).not.toHaveBeenCalled();
    act(() => result.current.dismissSendError());
    expect(result.current.sendError).toBeNull();
  });

  it("keeps the server's code of a refusal, and shows none for a plan that ended", async () => {
    const allowance = setup(null, {
      "POST /chat/stream": () =>
        jsonResponse(
          { detail: "You've used this period's chat allowance.", code: "allowance_used", feature: "chat_tokens" },
          { status: 429 },
        ),
    });
    expect(await act(() => start(allowance.result))).toBe(false);
    expect(allowance.result.current.sendError).toEqual({
      message: "You've used this period's chat allowance.",
      code: "allowance_used",
    });
    allowance.unmount();

    const ended = setup(null, {
      "POST /chat/stream": () =>
        jsonResponse({ detail: "Your Standard plan ended on 7 Oct 2026.", code: "plan_expired" }, { status: 402 }),
      "GET /auth/me": () => errorResponse(401, "Not authenticated"),
    });
    expect(await act(() => start(ended.result))).toBe(false);
    expect(ended.result.current.sendError).toBeNull();
  });

  it("keeps the partial text after Stop", async () => {
    const { result, stream, callbacks } = setup();
    const sent = start(result);
    act(() => stream.push(metaEvent(9, "Revenue"), deltaEvent("Revenue grew 18%")));
    await waitFor(() => expect(answer(result).content).toBe("Revenue grew 18%"));
    act(() => result.current.stop());
    expect(await act(() => sent)).toBe(true);
    expect(answer(result)).toMatchObject({ content: "Revenue grew 18%", stopped: true, streaming: false, error: null });
    expect(callbacks.onTurnFinished).toHaveBeenCalledWith({ conversationId: 9, title: "Revenue" }, false);
  });

  it("hands the message back when stopped before the conversation was known", async () => {
    const { result, callbacks } = setup();
    const sent = start(result);
    act(() => result.current.stop());
    expect(await act(() => sent)).toBe(false);
    expect(result.current.messages).toEqual([]);
    expect(result.current.sendError).toEqual({ message: "Stopped before the answer started.", code: null });
    expect(callbacks.onTurnFinished).toHaveBeenCalledWith(null, false);
  });

  it("says the connection was lost when the stream ends without done", async () => {
    const { result, stream } = setup();
    const sent = start(result);
    act(() => {
      stream.push(metaEvent(9, "Revenue"), deltaEvent("Revenue"));
      stream.end();
    });
    expect(await act(() => sent)).toBe(true);
    expect(answer(result)).toMatchObject({
      content: "Revenue",
      error: "The connection was lost before the answer finished.",
      streaming: false,
    });
  });

  it("hands the message back when the connection is lost before meta", async () => {
    const { result, stream } = setup();
    const sent = start(result);
    act(() => stream.end());
    expect(await act(() => sent)).toBe(false);
    expect(result.current.messages).toEqual([]);
    expect(result.current.sendError).toEqual({
      message: "The connection was lost before the answer started.",
      code: null,
    });
  });

  it("replaces the sources each time they are sent", async () => {
    const { result, stream } = setup();
    void start(result);
    const passage = makeSource();
    const email = makeEmailSource();
    act(() => stream.push(metaEvent(9, "Revenue"), { event: "sources", data: { sources: [passage] } }));
    await waitFor(() => expect(answer(result).sources).toEqual([passage]));
    act(() => stream.push({ event: "sources", data: { sources: [passage, email] } }));
    await waitFor(() => expect(answer(result).sources).toEqual([passage, email]));
    act(() => stream.push({ event: "sources", data: { sources: [email] } }));
    await waitFor(() => expect(answer(result).sources).toEqual([email]));
  });

  it("refuses a second send while one is streaming", async () => {
    const { result } = setup();
    void start(result);
    let second!: Promise<boolean>;
    act(() => {
      second = result.current.send({ ...request, message: "And costs?" });
    });
    await expect(second).resolves.toBe(false);
    expect(result.current.messages).toHaveLength(2);
  });
});

describe("useChatStream: files and plugin cards", () => {
  it("follows a file from writing to created and announces the document", async () => {
    const announced: DocumentItem[] = [];
    const unsubscribe = subscribeToAnnouncements((doc) => announced.push(doc));
    const { result, stream } = setup();
    const sent = start(result);
    act(() => stream.push(metaEvent(9, "Summary"), fileEvents[0]));
    await waitFor(() => expect(answer(result).cards).toHaveLength(1));
    expect(answer(result).cards[0]).toMatchObject({ key: "cf1", kind: "writing", title: null, chars: 0 });

    act(() =>
      stream.push(fileEvents[1], {
        event: "file",
        data: { call_id: "f1", kind: "file", status: "writing", title: null, format: null, chars: 2_400 },
      }),
    );
    await waitFor(() => expect(answer(result).cards[0]).toMatchObject({ chars: 2_400 }));
    expect(answer(result).cards[0]).toMatchObject({ kind: "writing", title: "Q3 summary", format: "docx" });

    const file = makeGeneratedFile({ document_id: 205, filename: "Q3 summary.docx", status: "pending" });
    act(() => {
      stream.push(
        { event: "file", data: { call_id: "f1", kind: "file", status: "created", file, document: createdDocument, already_existed: false } },
        { event: "file", data: { call_id: "f2", kind: "file", status: "failed", title: "Budget", format: "xlsx", error: "Sheets need string cells." } },
        doneEvent(41),
      );
      stream.end();
    });
    await act(() => sent);
    unsubscribe();
    expect(announced).toEqual([createdDocument]);
    expect(answer(result).cards).toEqual<FileCard[]>([
      { key: "cf1", kind: "created", file, alreadyExisted: false },
      { key: "cf2", kind: "failed", fileKind: "file", title: "Budget", format: "xlsx", error: "Sheets need string cells." },
    ]);
  });

  it("marks unfinished files as stopped and asks the panel to look again, later too for an image", async () => {
    const reloads = vi.fn();
    const unsubscribe = subscribeToReloadRequests(reloads);
    const { result, stream } = setup();
    const sent = start(result);
    act(() =>
      stream.push(metaEvent(9, "Pictures"), fileEvents[1], {
        event: "file",
        data: { call_id: "i1", kind: "image", status: "generating", title: "Lighthouse", width: 1024, height: 1024, quality: "medium" },
      }),
    );
    await waitFor(() => expect(answer(result).cards).toHaveLength(2));
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    act(() => result.current.stop());
    await act(() => sent);
    expect(answer(result).cards).toEqual<FileCard[]>([
      { key: "cf1", kind: "stopped", fileKind: "file", title: "Q3 summary", format: "docx", wasGenerating: false },
      { key: "ci1", kind: "stopped", fileKind: "image", title: "Lighthouse", format: null, wasGenerating: true },
    ]);
    expect(reloads).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(30_000);
    expect(reloads).toHaveBeenCalledTimes(2);
    vi.advanceTimersByTime(160_000);
    expect(reloads).toHaveBeenCalledTimes(4);
    unsubscribe();
  });

  it("replaces plugin cards by card_id and announces imported documents once", async () => {
    const announced: DocumentItem[] = [];
    const unsubscribe = subscribeToAnnouncements((doc) => announced.push(doc));
    const { result, stream } = setup();
    const sent = start(result);
    const search: GmailActivityCard = {
      card_id: "g1",
      plugin_id: "gmail",
      type: "gmail_activity",
      status: "running",
      action: "search",
      query: "from:priya invoice",
      count: 0,
      error: null,
    };
    const imported = makeDocument({ id: 310, filename: "Invoice thread.txt", imported_from: "Gmail", status: "pending" });
    const item: GmailImportItem = {
      kind: "thread",
      thread_id: "18f2a9c0d1e2f3a0",
      message_id: null,
      part_id: null,
      label: "Invoice",
      status: "imported",
      reason: null,
      document_id: 310,
      document: imported,
    };
    const importing: GmailImportCard = { card_id: "g2", plugin_id: "gmail", type: "gmail_import", status: "importing", items: [item], error: null };
    act(() => stream.push(metaEvent(9, "Invoices"), { event: "plugin", data: search }, { event: "plugin", data: importing }));
    await waitFor(() => expect(answer(result).pluginCards).toHaveLength(2));

    act(() =>
      stream.push(
        { event: "plugin", data: { ...search, status: "done", count: 3 } },
        { event: "plugin", data: { ...importing, items: [item] } },
      ),
    );
    await waitFor(() => expect(answer(result).pluginCards[0]).toMatchObject({ status: "done", count: 3 }));
    act(() => result.current.stop());
    await act(() => sent);
    unsubscribe();
    expect(answer(result).pluginCards).toEqual([
      { ...search, status: "done", count: 3, stopped: false },
      { ...importing, stopped: true },
    ]);
    expect(announced).toEqual([imported]);
  });

  it("tells images from files and running plugin actions from finished ones", () => {
    const file = makeGeneratedFile();
    expect(isImageCard({ key: "d1", kind: "created", file, alreadyExisted: false })).toBe(false);
    expect(isImageCard({ key: "s0", kind: "created", file: { ...file, format: "png", document_id: null }, alreadyExisted: false })).toBe(true);
    expect(isImageCard({ key: "ci1", kind: "generating", title: "Map", width: 1, height: 1, quality: "low", startedAt: 0 })).toBe(true);
    expect(isImageCard({ key: "ci2", kind: "failed", fileKind: "image", title: null, format: null, error: "No" })).toBe(true);
    expect(pluginCardBusy({ card_id: "g1", plugin_id: "gmail", type: "gmail_import", status: "importing", items: [], error: null })).toBe(true);
    expect(pluginCardBusy({ card_id: "g1", plugin_id: "gmail", type: "gmail_import", status: "done", items: [], error: null })).toBe(false);
  });
});

describe("useChatStream: an existing conversation", () => {
  const detail: ConversationDetail = {
    id: 42,
    title: "Q3 review",
    created_at: "2026-10-18T09:00:00Z",
    updated_at: "2026-10-18T09:05:00Z",
    messages: [
      makeMessage({ id: 500, role: "user", content: "Summarize Q3", sources: [], model_name: null }),
      makeMessage({ id: 501, files: [makeGeneratedFile(), makeGeneratedFile({ document_id: null, filename: "old.md", format: "md", status: null })] }),
      makeMessage({ id: 502, role: "user", content: "And Q4?", sources: [] }),
      makeMessage({
        id: 503,
        content: "Q4 is",
        error: "Stopped before completion",
        plugin_cards: [
          { card_id: "p1", plugin_id: "gmail", type: "gmail_activity", status: "running", action: "search", query: "q4", count: 0, error: null },
        ],
      }),
    ],
  };

  it("loads the messages, showing a stopped answer as stopped", async () => {
    const { result } = setup(42, { "GET /conversations/42": detail });
    expect(result.current.status).toBe("loading");
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.title).toBe("Q3 review");
    expect(result.current.messages.map((message) => message.key)).toEqual(["m500", "m501", "m502", "m503"]);
    expect(result.current.messages[1].cards.map((card) => card.key)).toEqual(["d205", "s1"]);
    expect(result.current.messages[3]).toMatchObject({ stopped: true, error: null });
    expect(result.current.messages[3].pluginCards[0].stopped).toBe(true);
  });

  it("sends follow-ups into the conversation", async () => {
    const { result, stream, calls, callbacks } = setup(42, { "GET /conversations/42": detail });
    await waitFor(() => expect(result.current.status).toBe("ready"));
    const sent = start(result, { message: "And costs?", modelId: null, documentIds: [101] });
    act(() => {
      stream.push(metaEvent(42, "Q3 review", 504), deltaEvent("Costs fell."), doneEvent(505));
      stream.end();
    });
    expect(await act(() => sent)).toBe(true);
    expect(callbacks.onConversationCreated).not.toHaveBeenCalled();
    expect(result.current.messages.slice(-2).map((message) => [message.id, message.content])).toEqual([
      [504, "And costs?"],
      [505, "Costs fell."],
    ]);
    expect(calls.find((call: FetchCall) => call.path === "/chat/stream")?.body).toMatchObject({
      conversation_id: 42,
      model_id: null,
      document_ids: [101],
    });
  });

  it("reports a missing conversation", async () => {
    const { callbacks } = setup(404, { "GET /conversations/404": () => errorResponse(404, "Conversation not found") });
    await waitFor(() => expect(callbacks.onConversationMissing).toHaveBeenCalledTimes(1));
  });

  it("shows a load error and loads again on reload", async () => {
    let fail = true;
    const { result } = setup(42, { "GET /conversations/42": () => (fail ? errorResponse(503, "Database unavailable") : detail) });
    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.loadError).toBe("Database unavailable");
    await expect(result.current.send(request)).resolves.toBe(false);

    fail = false;
    act(() => result.current.reload());
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.messages).toHaveLength(4);
  });

  it("drops a running stream when another conversation opens", async () => {
    const { result, stream, rerender, callbacks } = setup(null, { "GET /conversations/42": detail });
    const sent = start(result);
    const firstThread = result.current.threadKey;
    act(() => stream.push(metaEvent(9, "Revenue"), deltaEvent("Revenue")));
    await waitFor(() => expect(answer(result).content).toBe("Revenue"));

    rerender({ id: 42 });
    await waitFor(() => expect(result.current.status).toBe("ready"));
    await act(() => sent);
    expect(result.current.threadKey).not.toBe(firstThread);
    expect(result.current.streaming).toBe(false);
    expect(result.current.title).toBe("Q3 review");
    expect(result.current.messages.map((message) => message.key)).toEqual(["m500", "m501", "m502", "m503"]);
    expect(callbacks.onTurnFinished).toHaveBeenCalledWith({ conversationId: 9, title: "Revenue" }, false);
  });
});
