import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { AnswerSource } from "../../api/types";
import { makeEmailSource, makeSource } from "../../test/fixtures";
import { formatEmailDate } from "./plugins/email";
import SourcesList from "./SourcesList";

function renderSources(sources: AnswerSource[], highlight: { index: number } | null = null) {
  return render(<SourcesList id="sources-1" sources={sources} highlight={highlight} />);
}

describe("SourcesList", () => {
  it("lists document passages with their file, page, match and snippet", () => {
    renderSources([makeSource(), makeSource({ index: 2, filename: "notes.md", page: null, score: 0.456, snippet: "Hiring plan." })]);
    const [first, second] = screen.getAllByRole("listitem");
    expect(first).toHaveTextContent("Source 1");
    expect(within(first).getByText("quarterly-report.pdf")).toBeInTheDocument();
    expect(within(first).getByText("page 3")).toBeInTheDocument();
    expect(within(first).getByText("82% match")).toBeInTheDocument();
    expect(within(first).getByText(/Revenue grew 18%/)).toBeInTheDocument();
    expect(within(second).queryByText(/^page/)).not.toBeInTheDocument();
    expect(within(second).getByText("46% match")).toBeInTheDocument();
  });

  it("clamps the match percentage", () => {
    renderSources([makeSource({ score: 1.4 }), makeSource({ index: 2, score: -0.2 })]);
    expect(screen.getByText("100% match")).toBeInTheDocument();
    expect(screen.getByText("0% match")).toBeInTheDocument();
  });

  it("marks a snippet cut at the backend's limit with an ellipsis at a word boundary", () => {
    const cut = `${"word ".repeat(99)}trunc`;
    expect(Array.from(cut)).toHaveLength(500);
    renderSources([makeSource({ snippet: cut }), makeSource({ index: 2, snippet: "Short and complete." })]);
    expect(screen.getByText(`${"word ".repeat(98)}word…`)).toBeInTheDocument();
    expect(screen.getByText("Short and complete.")).toBeInTheDocument();
  });

  it("shows an email's subject, sender, date and snippet as text with a safe Gmail link", () => {
    const email = makeEmailSource({ subject: "<b>Q3</b> numbers" });
    renderSources([email]);
    const item = screen.getByRole("listitem");
    expect(item).toHaveTextContent("Email 2");
    expect(within(item).getByText("<b>Q3</b> numbers")).toBeInTheDocument();
    expect(within(item).getByText("Priya Raman <priya@example.com>")).toBeInTheDocument();
    expect(item).toHaveTextContent(`· ${formatEmailDate(email.date)}`);
    expect(within(item).getByText(email.snippet)).toBeInTheDocument();
    const link = within(item).getByRole("link", { name: "Open in Gmail" });
    expect(link).toHaveAttribute("href", email.url);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("leaves out links that aren't https and names a missing subject", () => {
    renderSources([makeEmailSource({ subject: "", url: "javascript:alert(1)", date: null })]);
    expect(screen.getByText("(no subject)")).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("scrolls the highlighted source into view", () => {
    const scroll = vi.spyOn(Element.prototype, "scrollIntoView");
    renderSources([makeSource(), makeSource({ index: 2 })], { index: 2 });
    expect(scroll).toHaveBeenCalledTimes(1);
    expect(scroll.mock.contexts[0]).toBe(screen.getAllByRole("listitem")[1]);
  });
});
