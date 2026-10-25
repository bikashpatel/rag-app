import type { Element, ElementContent, Root } from "hast";
import { describe, expect, it } from "vitest";
import { CITATION_PROPERTY, rehypeCitations } from "./citations";

const WORD_JOINER = "⁠";

function text(value: string): ElementContent {
  return { type: "text", value };
}

function element(tagName: string, children: ElementContent[]): Element {
  return { type: "element", tagName, properties: {}, children };
}

function run(indices: number[], ...children: Element[]): Root {
  const tree: Root = { type: "root", children };
  rehypeCitations(new Set(indices))()(tree);
  return tree;
}

/** The paragraph as a compact string: chips as {n}, glue spans as ‹…›. */
function render(nodes: ElementContent[]): string {
  return nodes
    .map((node) => {
      if (node.type === "text") return node.value;
      if (node.type !== "element") return "";
      if (node.tagName === "sup" && node.properties[CITATION_PROPERTY] !== undefined) {
        return `{${String(node.properties[CITATION_PROPERTY])}}`;
      }
      const inner = render(node.children);
      return node.tagName === "span" ? `‹${inner}›` : `<${node.tagName}>${inner}</${node.tagName}>`;
    })
    .join("");
}

function paragraph(tree: Root): string {
  return render((tree.children[0] as Element).children);
}

describe("rehypeCitations", () => {
  it("turns the answer's source numbers into chips glued to the word before them", () => {
    const tree = run([1, 2], element("p", [text("Revenue grew 18% [1] in Q3 [2].")]));
    expect(paragraph(tree)).toBe("Revenue grew ‹18%{1}› in ‹Q3{2}›.");
  });

  it("keeps a run of chips together with its separators", () => {
    const tree = run([1, 2, 3], element("p", [text("Both agree [1], [2]; [3] and more.")]));
    expect(paragraph(tree)).toBe("Both ‹agree{1},{2};{3}› and more.");
  });

  it("leaves numbers that aren't sources as plain text", () => {
    const tree = run([1], element("p", [text("See [1] and [4], not [x].")]));
    expect(paragraph(tree)).toBe("‹See{1}› and [4], not [x].");
  });

  it("uses a word joiner when a chip starts the text", () => {
    const tree = run([3], element("p", [text("[3] says so.")]));
    expect(paragraph(tree)).toBe(`‹${WORD_JOINER}{3}› says so.`);
  });

  it("skips code, preformatted blocks and links, but looks inside other elements", () => {
    const tree = run(
      [1],
      element("p", [
        element("code", [text("arr[1]")]),
        text(" and "),
        element("a", [text("link [1]")]),
        text(" "),
        element("strong", [text("bold [1]")]),
      ]),
      element("pre", [text("x[1]")]),
    );
    expect(paragraph(tree)).toBe("<code>arr[1]</code> and <a>link [1]</a> <strong>‹bold{1}›</strong>");
    expect(render([tree.children[1] as Element])).toBe("<pre>x[1]</pre>");
  });

  it("doesn't treat a markdown link the parser left as text as a citation", () => {
    const tree = run([1], element("p", [text("[1](https://example.com)")]));
    expect(paragraph(tree)).toBe("[1](https://example.com)");
  });

  it("changes nothing without sources", () => {
    const tree = run([], element("p", [text("Revenue [1].")]));
    expect(paragraph(tree)).toBe("Revenue [1].");
  });
});
