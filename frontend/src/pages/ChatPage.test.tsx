import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ConversationDetail, ConversationSummary, Me } from "../api/types";
import { setMediaQueries } from "../test/browser";
import { deltaEvent, doneEvent, liveStream, metaEvent } from "../test/chatStream";
import { makeCatalog, makeOrder } from "../test/billing";
import {
  makeChatModel,
  makeDocumentConfig,
  makeLapsedPlanAccess,
  makeMe,
  makeMessage,
  makePaidPlanAccess,
  makeSource,
} from "../test/fixtures";
import { errorResponse, jsonResponse, stubFetch, type FetchCall, type Route as FetchRoute } from "../test/http";
import { renderWithAuth } from "../test/render";
import ChatPage from "./ChatPage";

// The sidebar groups by the local day (UTC in tests), so the clock is pinned: "Today" must not depend on the hour.
const NOW = "2026-10-20T12:00:00.000Z";

const review: ConversationSummary = {
  id: 42,
  title: "Q3 review",
  created_at: "2026-10-19T06:00:00.000Z",
  updated_at: "2026-10-20T10:00:00.000Z",
};
const hiring: ConversationSummary = {
  id: 43,
  title: "Hiring plan",
  created_at: "2026-10-08T00:00:00.000Z",
  updated_at: "2026-10-12T04:00:00.000Z",
};

const reviewDetail: ConversationDetail = {
  ...review,
  messages: [
    makeMessage({ id: 500, role: "user", content: "Summarize the Q3 report", sources: [], model_name: null }),
    makeMessage({ id: 501, content: "Revenue grew 18% [1]." }),
  ],
};

type Setup = { route?: string; conversations?: ConversationSummary[]; me?: Me; routes?: Record<string, FetchRoute> };

async function setup({ route = "/", conversations = [review, hiring], me = makeMe(), routes = {} }: Setup = {}) {
  const streams: ReturnType<typeof liveStream>[] = [];
  const calls: FetchCall[] = stubFetch({
    "GET /auth/me": me,
    "GET /models": [makeChatModel()],
    "GET /conversations": conversations,
    "GET /conversations/42": reviewDetail,
    "GET /documents": [],
    "GET /documents/config": makeDocumentConfig(),
    "GET /plugins": [],
    "POST /chat/stream": (call) => {
      const stream = liveStream();
      streams.push(stream);
      return stream.respond(call);
    },
    ...routes,
  });
  const user = userEvent.setup();
  const view = await renderWithAuth(
    <Routes>
      <Route path="/" element={<ChatPage />} />
      <Route path="/c/:conversationId" element={<ChatPage />} />
    </Routes>,
    { route },
  );
  await screen.findByRole("button", { name: /Claude Opus 5\.5/ });
  await waitFor(() => expect(within(sidebar()).queryByRole("status")).not.toBeInTheDocument());
  const stream = async () => {
    await waitFor(() => expect(streams.length).toBeGreaterThan(0));
    return streams[streams.length - 1];
  };
  return { ...view, user, calls, stream };
}

const composer = () => screen.getByRole("textbox", { name: "Message" });
const sidebar = () => screen.getByRole("navigation", { name: "Conversation history" });

describe("ChatPage", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(NOW));
  });
  afterEach(() => vi.useRealTimers());

  it("greets a new chat with example questions and the conversation history", async () => {
    await setup();
    expect(screen.getByRole("heading", { level: 1, name: "New chat" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: /How can I help/ })).toHaveTextContent("How can I help, maya.chen?");
    const examples = within(screen.getByRole("list", { name: "Example questions" })).getAllByRole("button");
    expect(examples.map((button) => button.textContent)).toEqual(
      [
        "Summarize the key points of my documents",
        "Which dates, deadlines or figures are mentioned?",
        "Explain the main topic in simple terms",
        "Create a PDF report summarizing my documents",
        "Draw a watercolor illustration of a lighthouse at dawn",
      ].map((prompt) => expect.stringContaining(prompt)),
    );
    expect(within(sidebar()).getByRole("heading", { name: "Today" })).toBeInTheDocument();
    expect(within(sidebar()).getByRole("heading", { name: "Older" })).toBeInTheDocument();
    expect(within(sidebar()).getByRole("link", { name: "Q3 review" })).toHaveAttribute("href", "/c/42");
    expect(document.title).toBe("RAG Assistant");
  });

  it("says when there are no conversations yet, and retries a failed list", async () => {
    let fail = true;
    const { user } = await setup({
      routes: { "GET /conversations": () => (fail ? errorResponse(503, "Database unavailable") : []) },
    });
    expect(await within(sidebar()).findByRole("alert")).toHaveTextContent("Database unavailable");
    fail = false;
    await user.click(within(sidebar()).getByRole("button", { name: "Retry" }));
    expect(await within(sidebar()).findByText("No conversations yet. Your chats will appear here.")).toBeInTheDocument();
  });

  it("streams the first answer of a new chat and lists the conversation", async () => {
    const { user, calls, stream, location } = await setup({ conversations: [hiring] });
    await user.type(composer(), "How did revenue change?{Enter}");

    expect(within(screen.getByRole("article", { name: "You" })).getByText("How did revenue change?")).toBeInTheDocument();
    expect(screen.getByText("Generating an answer…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop generating" })).toBeInTheDocument();
    const live = await stream();
    live.push(
      metaEvent(9, "How did revenue change?"),
      { event: "sources", data: { sources: [makeSource()] } },
      deltaEvent("Revenue grew "),
      deltaEvent("18% [1]."),
    );
    expect(await screen.findByRole("button", { name: "Source 1: quarterly-report.pdf, page 3" })).toBeInTheDocument();
    await waitFor(() => expect(location()).toBe("/c/9"));
    expect(screen.getByRole("heading", { level: 1, name: "How did revenue change?" })).toBeInTheDocument();

    live.push(doneEvent(41));
    live.end();
    await waitFor(() => expect(screen.getByRole("button", { name: "Send message" })).toBeInTheDocument());
    expect(await screen.findByText("Answer ready.")).toBeInTheDocument();
    const links = within(sidebar()).getAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual(["How did revenue change?", "Hiring plan"]);
    expect(links[0]).toHaveAttribute("aria-current", "page");
    expect(document.title).toBe("How did revenue change? · RAG Assistant");
    expect(calls.find((call) => call.path === "/chat/stream")?.body).toEqual({
      conversation_id: null,
      message: "How did revenue change?",
      model_id: 1,
      document_ids: null,
    });
    await waitFor(() => expect(calls.filter((call) => call.path === "/auth/me")).toHaveLength(2));
  });

  it("sends an example question at a click", async () => {
    const { user, calls } = await setup();
    await user.click(screen.getByRole("button", { name: /Explain the main topic in simple terms/ }));
    await waitFor(() => expect(calls.some((call) => call.path === "/chat/stream")).toBe(true));
    expect(calls.find((call) => call.path === "/chat/stream")?.body).toMatchObject({
      message: "Explain the main topic in simple terms",
    });
  });

  it("shows an answer that failed midway and retries its question", async () => {
    const { user, calls, stream } = await setup();
    await user.type(composer(), "How did revenue change?{Enter}");
    const first = await stream();
    first.push(metaEvent(9, "How did revenue change?"), deltaEvent("Revenue"), {
      event: "error",
      data: { message: "The model provider returned an error.", assistant_message_id: 41 },
    });
    first.end();
    const answer = await screen.findByRole("article", { name: "Assistant" });
    expect(await within(answer).findByText("The model provider returned an error.")).toBeInTheDocument();
    expect(within(answer).getByText("Revenue")).toBeInTheDocument();
    expect(await screen.findByText("The answer failed: The model provider returned an error.")).toBeInTheDocument();

    await user.click(within(answer).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(calls.filter((call) => call.path === "/chat/stream")).toHaveLength(2));
    expect(calls.filter((call) => call.path === "/chat/stream")[1].body).toMatchObject({
      conversation_id: 9,
      message: "How did revenue change?",
    });
  });

  it("keeps the partial answer after Stop", async () => {
    const { user, stream } = await setup();
    await user.type(composer(), "How did revenue change?{Enter}");
    const live = await stream();
    live.push(metaEvent(9, "How did revenue change?"), deltaEvent("Revenue grew"));
    const answer = await screen.findByRole("article", { name: "Assistant" });
    await within(answer).findByText("Revenue grew");
    await user.click(screen.getByRole("button", { name: "Stop generating" }));
    expect(await within(answer).findByText("Stopped")).toBeInTheDocument();
    expect(within(answer).getByText("Revenue grew")).toBeInTheDocument();
    expect(await screen.findByText("Answer stopped.")).toBeInTheDocument();
  });

  it("shows a rejected send as an alert and hands the draft back", async () => {
    const { user } = await setup({
      routes: { "POST /chat/stream": () => errorResponse(429, "Too many requests. Wait a minute and try again.") },
    });
    await user.type(composer(), "How did revenue change?{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many requests. Wait a minute and try again.");
    expect(composer()).toHaveValue("How did revenue change?");
    expect(screen.queryByRole("article", { name: "You" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Dismiss error" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("opens an existing conversation from its URL and replies into it", async () => {
    const { user, calls, stream } = await setup({ route: "/c/42" });
    const answer = await screen.findByRole("article", { name: "Assistant" });
    expect(within(answer).getByRole("button", { name: "Source 1: quarterly-report.pdf, page 3" })).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "You" })).toHaveTextContent("Summarize the Q3 report");
    expect(screen.getByRole("heading", { level: 1, name: "Q3 review" })).toBeInTheDocument();
    expect(within(sidebar()).getByRole("link", { name: "Q3 review" })).toHaveAttribute("aria-current", "page");
    expect(document.title).toBe("Q3 review · RAG Assistant");
    expect(composer()).toHaveAttribute("placeholder", "Reply…");

    await user.type(composer(), "And costs?{Enter}");
    const live = await stream();
    live.push(metaEvent(42, "Q3 review", 502), deltaEvent("Costs fell 4%."), doneEvent(503));
    live.end();
    expect(await screen.findByText("Costs fell 4%.")).toBeInTheDocument();
    expect(calls.find((call) => call.path === "/chat/stream")?.body).toMatchObject({ conversation_id: 42 });
  });

  it("opens a conversation from the sidebar", async () => {
    const { user, location } = await setup();
    await user.click(within(sidebar()).getByRole("link", { name: "Q3 review" }));
    expect(await screen.findByRole("article", { name: "Assistant" })).toHaveTextContent("Revenue grew 18%");
    expect(location()).toBe("/c/42");
  });

  it.each([
    ["a deleted conversation", "/c/404"],
    ["an invalid conversation id", "/c/abc"],
  ])("goes back to a new chat for %s", async (_, route) => {
    const { location } = await setup({
      route,
      routes: { "GET /conversations/404": () => errorResponse(404, "Conversation not found") },
    });
    expect(await screen.findByText("That conversation doesn't exist or has been deleted.")).toBeInTheDocument();
    await waitFor(() => expect(location()).toBe("/"));
    expect(screen.getByRole("heading", { level: 1, name: "New chat" })).toBeInTheDocument();
  });

  it("offers to try again when a conversation can't be loaded", async () => {
    let fail = true;
    const { user } = await setup({
      route: "/c/42",
      routes: { "GET /conversations/42": () => (fail ? errorResponse(503, "Database unavailable") : reviewDetail) },
    });
    const error = await screen.findByRole("alert");
    expect(error).toHaveTextContent("Couldn't load this conversation");
    expect(error).toHaveTextContent("Database unavailable");
    expect(composer()).toHaveAttribute("placeholder", "This conversation couldn't be loaded");
    fail = false;
    await user.click(within(error).getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("article", { name: "Assistant" })).toBeInTheDocument();
  });

  it("renames a conversation from its menu", async () => {
    const { user, calls } = await setup({
      route: "/c/42",
      routes: {
        "PATCH /conversations/42": ({ body }) => ({ ...review, ...(body as { title: string }), updated_at: NOW }),
      },
    });
    await user.click(within(sidebar()).getByRole("button", { name: "More actions for “Q3 review”" }));
    await user.click(screen.getByRole("menuitem", { name: "Rename" }));
    const input = within(sidebar()).getByRole("textbox", { name: "Conversation title" });
    expect(input).toHaveFocus();
    await user.clear(input);
    await user.type(input, "  Q3   board review {Enter}");
    expect(await within(sidebar()).findByRole("link", { name: "Q3 board review" })).toBeInTheDocument();
    expect(calls.find((call) => call.method === "PATCH")?.body).toEqual({ title: "Q3 board review" });
    expect(screen.getByRole("heading", { level: 1, name: "Q3 board review" })).toBeInTheDocument();
    expect(within(sidebar()).getByRole("button", { name: "More actions for “Q3 board review”" })).toHaveFocus();
  });

  it("shows why a rename failed and keeps editing", async () => {
    const { user } = await setup({
      routes: { "PATCH /conversations/42": () => errorResponse(422, [{ loc: ["body", "title"], msg: "Title too long" }]) },
    });
    await user.click(within(sidebar()).getByRole("button", { name: "More actions for “Q3 review”" }));
    await user.click(screen.getByRole("menuitem", { name: "Rename" }));
    const input = within(sidebar()).getByRole("textbox", { name: "Conversation title" });
    await user.type(input, " (final){Enter}");
    expect(await within(sidebar()).findByRole("alert")).toHaveTextContent("title: Title too long");
    expect(input).toHaveAttribute("aria-invalid", "true");
    await user.keyboard("{Escape}");
    expect(within(sidebar()).getByRole("link", { name: "Q3 review" })).toBeInTheDocument();
  });

  it("deletes the open conversation after confirming and returns to a new chat", async () => {
    const { user, calls, location } = await setup({
      route: "/c/42",
      routes: { "DELETE /conversations/42": () => new Response(null, { status: 204 }) },
    });
    await screen.findByRole("article", { name: "Assistant" });
    await user.click(within(sidebar()).getByRole("button", { name: "More actions for “Q3 review”" }));
    await user.click(screen.getByRole("menuitem", { name: "Delete" }));
    const confirm = within(sidebar()).getByRole("group", { name: "Delete this chat?" });
    expect(within(confirm).getByRole("button", { name: "Cancel" })).toHaveFocus();
    await user.click(within(confirm).getByRole("button", { name: "Delete chat" }));
    await waitFor(() => expect(location()).toBe("/"));
    expect(within(sidebar()).queryByRole("link", { name: "Q3 review" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "New chat" })).toBeInTheDocument();
    expect(calls.some((call) => call.method === "DELETE" && call.path === "/conversations/42")).toBe(true);
  });

  it("keeps a conversation whose delete failed", async () => {
    const { user } = await setup({
      routes: { "DELETE /conversations/43": () => errorResponse(500, "boom") },
    });
    await user.click(within(sidebar()).getByRole("button", { name: "More actions for “Hiring plan”" }));
    await user.click(screen.getByRole("menuitem", { name: "Delete" }));
    await user.click(within(sidebar()).getByRole("button", { name: "Delete chat" }));
    expect(await within(sidebar()).findByRole("alert")).toHaveTextContent("boom");
    await user.click(within(sidebar()).getByRole("button", { name: "Cancel" }));
    expect(within(sidebar()).getByRole("link", { name: "Hiring plan" })).toBeInTheDocument();
  });

  it("collapses and expands the documents column, remembering the choice", async () => {
    const { user } = await setup();
    const toggle = screen.getByRole("button", { name: "Documents" });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const panel = document.getElementById(toggle.getAttribute("aria-controls") ?? "");
    expect(panel).toBeVisible();
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(panel).not.toBeVisible();
    expect(localStorage.getItem("rag.documentsPanel")).toBe("hidden");
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(localStorage.getItem("rag.documentsPanel")).toBeNull();
  });

  it("opens the conversations drawer on narrower screens and closes it with Escape", async () => {
    setMediaQueries((query) => query === "(max-width: 1099.98px)");
    const { user } = await setup();
    const toggle = screen.getByRole("button", { name: "Conversations" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("dialog", { name: "Conversations" })).not.toBeInTheDocument();
    await user.click(toggle);
    const drawer = screen.getByRole("dialog", { name: "Conversations" });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(drawer).toContainElement(document.activeElement as HTMLElement);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Conversations" })).not.toBeInTheDocument();
    expect(toggle).toHaveFocus();

    await user.click(toggle);
    await user.click(within(screen.getByRole("dialog", { name: "Conversations" })).getByRole("button", { name: "Close conversations" }));
    expect(screen.queryByRole("dialog", { name: "Conversations" })).not.toBeInTheDocument();
  });

  it("opens the documents as a slide-over on phones", async () => {
    setMediaQueries((query) => query.startsWith("(max-width"));
    const { user } = await setup();
    await user.click(screen.getByRole("button", { name: "Documents" }));
    expect(screen.getByRole("dialog", { name: "Documents" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Documents" })).not.toBeInTheDocument();
  });

  it("says why chat is unavailable without models", async () => {
    stubFetch({
      "GET /auth/me": makeMe(),
      "GET /models": [],
      "GET /conversations": [],
      "GET /documents": [],
      "GET /documents/config": makeDocumentConfig(),
      "GET /plugins": [],
    });
    await renderWithAuth(<ChatPage />);
    await waitFor(() =>
      expect(composer()).toHaveAttribute("placeholder", "No chat models are available. Ask your administrator to enable one."),
    );
  });
});

describe("ChatPage with plans", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(NOW));
  });
  afterEach(() => vi.useRealTimers());

  const starter = () =>
    makePaidPlanAccess({
      plan: { id: 1, code: "starter", name: "Starter" },
      features: { files: false, images: false, plugins: [] },
    });

  it("leaves features outside the plan out of the examples and the composer hint", async () => {
    await setup({ me: makeMe({ plan: starter() }) });
    const examples = within(screen.getByRole("list", { name: "Example questions" })).getAllByRole("button");
    expect(examples).toHaveLength(3);
    expect(screen.queryByText(/Ask for a PDF/)).not.toBeInTheDocument();
  });

  it("shows a payment in progress before the plan's end under the top bar", async () => {
    const ending = makePaidPlanAccess({
      expiring_soon: true,
      period_end: "2026-10-24T18:30:00Z",
      paid_through: "2026-10-24T18:30:00Z",
      valid_through: "2026-10-24",
    });
    const first = await setup({ me: makeMe({ plan: ending }) });
    expect(screen.getByText(/plan ends in 4 days/)).toHaveTextContent("Your Standard plan ends in 4 days, on 24 Oct.");
    first.unmount();

    await setup({ me: makeMe({ plan: { ...ending, pending_order: makeOrder() } }) });
    expect(screen.getByText(/is in progress/)).toHaveTextContent("A payment for Standard is in progress.");
    expect(screen.queryByText(/plan ends in/)).not.toBeInTheDocument();
  });

  it("offers a new period or an upgrade when the chat allowance is used up", async () => {
    const { user } = await setup({
      me: makeMe({ plan: { ...starter(), can_start_new_period: true } }),
      routes: {
        "POST /chat/stream": () =>
          jsonResponse(
            { detail: "You've used this period's chat allowance (500,214 of 500,000).", code: "allowance_used" },
            { status: 429 },
          ),
      },
    });
    await user.type(composer(), "Summarize the contract{Enter}");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("You've used this period's chat allowance (500,214 of 500,000).");
    expect(within(alert).getByRole("link", { name: "Start a new period now" })).toHaveAttribute(
      "href",
      "/plans/checkout/starter?kind=now",
    );
    expect(within(alert).getByRole("link", { name: "Upgrade" })).toHaveAttribute("href", "/plans");
    expect(composer()).toHaveValue("Summarize the contract");
  });

  it("points to the plans when a feature isn't in the plan", async () => {
    const { user } = await setup({
      routes: {
        "POST /chat/stream": () =>
          jsonResponse(
            {
              detail: "Your Starter plan doesn't include Claude Opus 5.5.",
              code: "feature_not_in_plan",
              feature: "model",
            },
            { status: 402 },
          ),
      },
    });
    await user.type(composer(), "Hello{Enter}");
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByRole("link", { name: "See plans" })).toHaveAttribute("href", "/plans");
    expect(within(alert).queryByRole("link", { name: "Upgrade" })).not.toBeInTheDocument();
  });

  it("replaces the composer when the plan ends on the page, keeping the unsent message for the renewal", async () => {
    let me = makeMe({ plan: makePaidPlanAccess() });
    const { user, unmount } = await setup({
      me,
      routes: {
        "GET /auth/me": () => me,
        "GET /billing/plans": makeCatalog(),
        "POST /chat/stream": () =>
          jsonResponse({ detail: "Your Standard plan ended on 7 Oct 2026.", code: "plan_expired" }, { status: 402 }),
      },
    });
    me = makeMe({ plan: makeLapsedPlanAccess() });
    await user.type(composer(), "Draft the renewal letter{Enter}");
    const heading = await screen.findByRole("heading", { name: "Your Standard plan ended on 7 Oct" });
    await waitFor(() => expect(heading).toHaveFocus());
    expect(screen.queryByRole("textbox", { name: "Message" })).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(await screen.findByText("Your unsent message is saved and comes back when you renew.")).toBeInTheDocument();
    expect(JSON.parse(sessionStorage.getItem("rag.planDraft") ?? "null")).toEqual({
      conversation_id: null,
      text: "Draft the renewal letter",
    });
    unmount();

    me = makeMe({ plan: makePaidPlanAccess() });
    await setup({ me });
    await waitFor(() => expect(composer()).toHaveValue("Draft the renewal letter"));
    expect(sessionStorage.getItem("rag.planDraft")).toBeNull();
  });

  it("keeps a draft for another conversation until that one opens", async () => {
    sessionStorage.setItem("rag.planDraft", JSON.stringify({ conversation_id: 43, text: "Ask about the offer" }));
    await setup({ route: "/c/42" });
    expect(composer()).toHaveValue("");
    expect(sessionStorage.getItem("rag.planDraft")).not.toBeNull();
  });
});
