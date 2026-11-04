import { act, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { setMediaQueries } from "../../test/browser";
import { renderWithRouter } from "../../test/render";
import AuthSplit from "./AuthSplit";

const PHONE = "(max-width: 799.98px)";
const HERO_ALT = /a friendly robot gathers your PDF/;

function Card() {
  return (
    <>
      <h1>Create your account</h1>
      <button type="button">Create account</button>
    </>
  );
}

function precedes(first: Node, second: Node): boolean {
  return Boolean(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING);
}

describe("AuthSplit", () => {
  it("puts the card first, then the aside with the picture, copy and tour", () => {
    renderWithRouter(
      <AuthSplit>
        <Card />
      </AuthSplit>,
    );
    const main = screen.getByRole("main");
    const aside = screen.getByRole("complementary", { name: "About AskNuvexusDoc" });
    const button = within(main).getByRole("button", { name: "Create account" });
    expect(precedes(main, aside)).toBe(true);
    expect(button.closest(".login-card")).toBe(main.querySelector(".login-card"));
    expect(within(aside).getByRole("img", { name: HERO_ALT })).toBeInTheDocument();
    expect(within(aside).getByRole("region", { name: "Product tour" })).toBeInTheDocument();
    expect(within(aside).getByRole("heading", { level: 2 })).toHaveTextContent("Your documents. Your AI. Your answers.");
    // The "pure RAG" tagline sits under the brand, before the picture.
    const tagline = within(aside).getByText(/A modern, pure & powerful/);
    expect(tagline).toHaveTextContent("A modern, pure & powerful RAG application.");
    expect(within(aside).getByText("Pure RAG")).toBeInTheDocument();
    expect(within(aside).getByText(/Retrieval-Augmented Generation: answers drawn from your own documents/)).toBeInTheDocument();
    expect(precedes(tagline, within(aside).getByRole("img", { name: HERO_ALT }))).toBe(true);
    // The card's own content, then the credit line and the policy links.
    const credit = within(main).getByText(/Designed and developed by Elarxsys AI Hub Pvt\. Ltd\./);
    expect(credit).toHaveTextContent(/\(Intelligent : Innovation : Intellect\)© \d{4} asknuvexusdoc\.com\. All rights reserved\./);
    const policies = within(main).getByRole("navigation", { name: "Policies" });
    expect(precedes(button, credit) && precedes(credit, policies)).toBe(true);
    expect(within(policies).getAllByRole("link")).toHaveLength(5);
  });

  it("keeps the picture on desktops in the compact states", () => {
    renderWithRouter(
      <AuthSplit compactOnPhone>
        <Card />
      </AuthSplit>,
    );
    expect(screen.getByRole("img", { name: HERO_ALT })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Product tour" })).toBeInTheDocument();
  });

  it("on phones, shows the picture above the card and the copy below it, without the aside", () => {
    setMediaQueries((query) => query === PHONE);
    renderWithRouter(
      <AuthSplit>
        <Card />
      </AuthSplit>,
    );
    const main = screen.getByRole("main");
    expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Product tour" })).not.toBeInTheDocument();
    const hero = within(main).getByRole("img", { name: HERO_ALT });
    expect(hero.closest(".login-mobile-hero")).not.toBeNull();
    expect(precedes(screen.getByRole("button", { name: "Create account" }), hero)).toBe(true);
    expect(within(main).getByText("No endless searching.")).toBeInTheDocument();
    // The tagline leads the copy below the card, so the form keeps the first screen.
    const tagline = within(main).getByText(/A modern, pure & powerful/);
    expect(tagline.closest(".login-about")).not.toBeNull();
    expect(precedes(screen.getByRole("button", { name: "Create account" }), tagline)).toBe(true);
  });

  it("on phones, leaves the picture out in the compact states: the brand alone leads, the copy stays", () => {
    setMediaQueries((query) => query === PHONE);
    const { container } = renderWithRouter(
      <AuthSplit compactOnPhone>
        <Card />
      </AuthSplit>,
    );
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    const hero = container.querySelector(".login-mobile-hero");
    expect(hero?.querySelector(".ui-brand")).toHaveTextContent("AskNuvexusDoc");
    expect(hero?.querySelector(".ui-brand .ui-brand-mark-twinkle img")).toHaveAttribute("alt", "");
    expect(within(hero as HTMLElement).getByRole("heading", { level: 2 })).toHaveClass("sr-only");
    expect(screen.getByText("No endless searching.")).toBeInTheDocument();

    act(() => setMediaQueries(() => false)); // the window widens: the aside and its picture come back
    expect(screen.getByRole("complementary")).toContainElement(screen.getByRole("img", { name: HERO_ALT }));
  });
});
