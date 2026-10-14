import type { Element, ElementContent, Root, Text } from "hast";

// "[n]" not followed by "(" (that would be a markdown link the parser didn't turn into one).
const CITATION = /\[(\d{1,3})\](?!\()/g;
// Citations aren't linked inside code or inside links (no interactive element within another).
const SKIPPED_TAGS = new Set(["a", "code", "pre"]);

export const CITATION_PROPERTY = "dataCitation";

function citationElement(index: number): Element {
  return {
    type: "element",
    tagName: "sup",
    properties: { [CITATION_PROPERTY]: index },
    children: [{ type: "text", value: String(index) }],
  };
}

function splitText(node: Text, indices: ReadonlySet<number>): ElementContent[] {
  const parts: ElementContent[] = [];
  let last = 0;
  for (const match of node.value.matchAll(CITATION)) {
    const index = Number(match[1]);
    if (!indices.has(index)) continue;
    if (match.index > last) parts.push({ type: "text", value: node.value.slice(last, match.index) });
    parts.push(citationElement(index));
    last = match.index + match[0].length;
  }
  if (last === 0) return [node];
  if (last < node.value.length) parts.push({ type: "text", value: node.value.slice(last) });
  return parts;
}

function linkChildren(children: ElementContent[], indices: ReadonlySet<number>): ElementContent[] {
  return children.flatMap((child) => {
    if (child.type === "text") return splitText(child, indices);
    if (child.type === "element" && !SKIPPED_TAGS.has(child.tagName)) {
      child.children = linkChildren(child.children, indices);
    }
    return [child];
  });
}

/**
 * Rehype plugin: replaces "[n]" in text with `<sup data-citation=n>` for every n in `indices`
 * (the answer's source numbers), skipping code blocks, inline code and links.
 */
export function rehypeCitations(indices: ReadonlySet<number>) {
  return () => (tree: Root) => {
    if (indices.size === 0) return;
    for (const child of tree.children) {
      if (child.type === "element" && !SKIPPED_TAGS.has(child.tagName)) {
        child.children = linkChildren(child.children, indices);
      }
    }
  };
}
