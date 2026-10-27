import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { ConversationSummary, DocumentItem } from "../../api/types";
import { makeDocument } from "../../test/fixtures";
import { errorResponse, stubFetch, type Route } from "../../test/http";
import { renderWithRouter } from "../../test/render";
import YourDataSection from "./YourDataSection";

const chat = (id: number, title: string): ConversationSummary => ({
  id,
  title,
  created_at: "2026-10-01T09:00:00Z",
  updated_at: "2026-10-04T09:00:00Z",
});

function setup(routes: Record<string, Route> = {}) {
  const calls = stubFetch({
    "GET /documents": [
      makeDocument({ id: 1, filename: "Q3 report.pdf", size_bytes: 2_200_000, created_at: "2026-11-04T09:00:00Z" }),
      makeDocument({ id: 2, filename: "summary.docx", generated: true }),
      makeDocument({
        id: 3,
        filename: "lighthouse.png",
        generated: true,
        image: { width: 1024, height: 1024, alt_text: "A lighthouse", quality: "medium" },
      }),
      makeDocument({ id: 4, filename: "Invoice.pdf", imported_from: "Gmail" }),
    ] satisfies DocumentItem[],
    "GET /conversations": [chat(7, "Notice period"), chat(8, "Q3 revenue")],
    "DELETE /documents/1": () => new Response(null, { status: 204 }),
    "DELETE /conversations/8": () => new Response(null, { status: 204 }),
    ...routes,
  });
  const user = userEvent.setup();
  renderWithRouter(<YourDataSection />);
  return { calls, user };
}

const row = (name: string) => screen.getByText(name).closest("li") as HTMLElement;

describe("YourDataSection", () => {
  it("lists documents with their size, date and source, and a download link each", async () => {
    setup();
    expect(await screen.findByText("Q3 report.pdf")).toBeInTheDocument();
    expect(row("Q3 report.pdf")).toHaveTextContent("2.1 MB · 4 Nov 2026 · Uploaded");
    expect(row("summary.docx")).toHaveTextContent("Generated file");
    expect(row("lighthouse.png")).toHaveTextContent("Image");
    expect(row("Invoice.pdf")).toHaveTextContent("From Gmail");
    expect(within(row("Q3 report.pdf")).getByRole("link", { name: "Download Q3 report.pdf" })).toHaveAttribute(
      "href",
      "/api/documents/1/download",
    );
    expect(screen.getByText("Everything comes back when you choose a plan.", { exact: false })).toBeInTheDocument();
  });

  it("deletes a document after an inline confirmation", async () => {
    const { calls, user } = setup();
    await user.click(await screen.findByRole("button", { name: "Delete Q3 report.pdf…" }));
    const confirm = screen.getByRole("group", { name: "Delete Q3 report.pdf" });
    expect(confirm).toHaveTextContent("Delete “Q3 report.pdf”? It can't be undone.");
    expect(within(confirm).getByRole("button", { name: "Cancel" })).toHaveFocus();
    await user.click(within(confirm).getByRole("button", { name: "Delete" }));
    expect(screen.queryByText("Q3 report.pdf")).not.toBeInTheDocument();
    expect(calls.some((call) => call.method === "DELETE" && call.path === "/documents/1")).toBe(true);
    expect(screen.getByRole("heading", { name: "Your documents" })).toHaveFocus();
  });

  it("cancels a delete", async () => {
    const { calls, user } = setup();
    await user.click(await screen.findByRole("button", { name: "Delete Notice period…" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByText("Notice period")).toBeInTheDocument();
    expect(calls.some((call) => call.method === "DELETE")).toBe(false);
  });

  it("deletes a chat, and keeps it with the reason when the delete fails", async () => {
    const { user } = setup({ "DELETE /conversations/7": () => errorResponse(503, "The server is busy.") });
    await user.click(await screen.findByRole("button", { name: "Delete Q3 revenue…" }));
    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(screen.queryByText("Q3 revenue")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Delete Notice period…" }));
    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The server is busy.");
    expect(screen.getByText("Notice period")).toBeInTheDocument();
  });

  it("takes a document that is already gone as deleted", async () => {
    const { user } = setup({ "DELETE /documents/1": () => errorResponse(404, "Not found") });
    await user.click(await screen.findByRole("button", { name: "Delete Q3 report.pdf…" }));
    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(screen.queryByText("Q3 report.pdf")).not.toBeInTheDocument();
  });

  it("shows 20 rows, then all on request", async () => {
    const chats = Array.from({ length: 23 }, (_, index) => chat(index + 1, `Chat ${index + 1}`));
    const { user } = setup({ "GET /conversations": chats });
    expect(await screen.findByText("Chat 20")).toBeInTheDocument();
    expect(screen.queryByText("Chat 21")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Show all 23" }));
    expect(screen.getByText("Chat 23")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show fewer" })).toBeInTheDocument();
  });

  it("says when a list is empty, and retries one that failed", async () => {
    let fail = true;
    const { user } = setup({
      "GET /documents": [],
      "GET /conversations": () => (fail ? errorResponse(500, null) : []),
    });
    expect(await screen.findByText("No documents.")).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load your chats.");
    fail = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("No chats.")).toBeInTheDocument();
  });
});
