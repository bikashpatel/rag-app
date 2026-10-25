import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { AnswerSource } from "../../api/types";
import { makeEmailSource, makeSource } from "../../test/fixtures";
import Markdown from "./Markdown";

const sources: AnswerSource[] = [
  makeSource({ index: 1, filename: "quarterly-report.pdf", page: 3 }),
  makeSource({ index: 2, document_id: 102, filename: "notes.md", page: null }),
  makeEmailSource({ index: 3, subject: "Q3 numbers" }),
];

function renderMarkdown(content: string, onCite = vi.fn()) {
  const result = render(<Markdown content={content} sources={sources} onCite={onCite} />);
  return { ...result, onCite };
}

describe("Markdown citations", () => {
  it("turns the answer's source numbers into named chips", () => {
    renderMarkdown("Revenue grew 18% [1], margins held [2] and the email agrees [3].");
    expect(screen.getByRole("button", { name: "Source 1: quarterly-report.pdf, page 3" })).toHaveTextContent("1");
    expect(screen.getByRole("button", { name: "Source 2: notes.md" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Email 3: Q3 numbers" })).toBeInTheDocument();
  });

  it("leaves numbers that aren't sources as text", () => {
    renderMarkdown("See [1] and [9].");
    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(screen.getByText(/and \[9\]\./)).toBeInTheDocument();
  });

  it("reports the clicked source number", async () => {
    const user = userEvent.setup();
    const { onCite } = renderMarkdown("Both agree [1][3].");
    await user.click(screen.getByRole("button", { name: "Email 3: Q3 numbers" }));
    expect(onCite).toHaveBeenCalledWith(3);
  });

  it("doesn't link citations inside code or links", () => {
    renderMarkdown("Cited [1]. Inline `items[1]` and a [link [2]](https://example.com/docs).\n\n```\nlist[1]\n```");
    expect(screen.getAllByRole("button", { name: /^Source/ })).toHaveLength(1);
    expect(screen.getByText("items[1]")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "link [2]" })).toBeInTheDocument();
    expect(screen.getByText("list[1]")).toBeInTheDocument();
  });
});

describe("Markdown safety", () => {
  it("shows raw HTML as text instead of rendering it", () => {
    const { container } = renderMarkdown('Hello <b>bold</b> <img src="x" onerror="alert(1)"> <script>alert(1)</script>');
    expect(container.querySelector("b, img, script")).toBeNull();
    expect(container).toHaveTextContent("<b>bold</b>");
  });

  it("drops unsafe link targets but keeps the text", () => {
    renderMarkdown("[click me](javascript:alert(1)) and [data](data:text/html,<script>alert(1)</script>)");
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getByText("click me")).toBeInTheDocument();
  });

  it("opens external links in a new tab without an opener", () => {
    renderMarkdown("Read [the docs](https://example.com/docs).");
    const link = screen.getByRole("link", { name: "the docs" });
    expect(link).toHaveAttribute("href", "https://example.com/docs");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("keeps in-page links in the same tab", () => {
    renderMarkdown("Jump to [the summary](#summary).");
    const link = screen.getByRole("link", { name: "the summary" });
    expect(link).toHaveAttribute("href", "#summary");
    expect(link).not.toHaveAttribute("target");
  });

  it("keeps footnote links inside their own answer", () => {
    const content = "Revenue grew[^1].\n\n[^1]: Unaudited figures.";
    render(
      <>
        <Markdown content={content} sources={[]} onCite={vi.fn()} />
        <Markdown content={content} sources={[]} onCite={vi.fn()} />
      </>,
    );
    const references = screen.getAllByRole("link", { name: "1" });
    expect(references).toHaveLength(2);
    const [first, second] = references.map((link) => link.getAttribute("href") ?? "");
    expect(first).not.toBe(second);
    for (const href of [first, second]) {
      expect(href.startsWith("#")).toBe(true);
      expect(document.getElementById(decodeURIComponent(href.slice(1)))).toHaveTextContent("Unaudited figures.");
    }
  });

  it("shows images as links instead of loading them", () => {
    const { container } = renderMarkdown("![Revenue chart](https://example.com/chart.png)");
    expect(container.querySelector("img")).toBeNull();
    const link = screen.getByRole("link", { name: "Revenue chart" });
    expect(link).toHaveAttribute("href", "https://example.com/chart.png");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });
});

describe("Markdown formatting", () => {
  it("renders GFM tables", () => {
    renderMarkdown("| Quarter | Revenue |\n|---|---|\n| Q3 | $4.2M |");
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "$4.2M" })).toBeInTheDocument();
  });

  it("copies a code block's source", async () => {
    const user = userEvent.setup();
    renderMarkdown("```ts\nconst total = 42;\n```");
    const copy = screen.getByRole("button", { name: "Copy ts code" });
    await user.click(copy);
    expect(await navigator.clipboard.readText()).toBe("const total = 42;\n");
    expect(copy).toHaveTextContent("Copied");
    expect(screen.getByText("Code copied to the clipboard")).toBeInTheDocument();
  });

  it("names the copy button generically without a language and reports a failed copy", async () => {
    const user = userEvent.setup();
    renderMarkdown("```\nplain\n```");
    vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(new Error("denied"));
    const copy = screen.getByRole("button", { name: "Copy code" });
    await user.click(copy);
    expect(copy).toHaveTextContent("Couldn't copy");
    expect(screen.getByText("Couldn't copy the code")).toBeInTheDocument();
  });
});
