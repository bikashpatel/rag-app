import type { Element, ElementContent, Root, Text } from "hast";

// "[n]" not followed by "(" (that would be a markdown link the parser didn't turn into one).
const CITATION = /\[(\d{1,3})\](?!\()/g;
// Citations aren't linked inside code or inside links (no interactive element within another).
const SKIPPED_TAGS = new Set(["a", "code", "pre"]);
/** U+2060 WORD JOINER: no line break between what comes before and the first chip. */
const WORD_JOINER = "⁠";

export const CITATION_PROPERTY = "dataCitation";

function citationElement(index: number): Element {
  return {
    type: "element",
    tagName: "sup",
    properties: { [CITATION_PROPERTY]: index },
    children: [{ type: "text", value: String(index) }],
  };
}

type Run = { start: number; end: number; items: { index: number; start: number; end: number }[] };

/**
 * Replaces the citations in a text node. Each run of chips (separated by nothing but spaces or commas) goes into a
 * `span.chat-cite-glue` (white-space: nowrap) together with the word before it, so a chip never starts a line
 * (visual spec 6.17). Spaces before the run are dropped: the chip's own margin spaces it.
 */
function splitText(node: Text, indices: ReadonlySet<number>): ElementContent[] {
  const { value } = node;
  const runs: Run[] = [];
  for (const match of value.matchAll(CITATION)) {
    const index = Number(match[1]);
    if (!indices.has(index)) continue;
    const item = { index, start: match.index, end: match.index + match[0].length };
    const last = runs.at(-1);
    if (last && /^[\s,;]*$/.test(value.slice(last.end, item.start))) {
      last.items.push(item);
      last.end = item.end;
    } else {
      runs.push({ start: item.start, end: item.end, items: [item] });
    }
  }
  if (runs.length === 0) return [node];

  const parts: ElementContent[] = [];
  let cursor = 0;
  for (const run of runs) {
    const before = value.slice(cursor, run.start);
    const lastWord = /(\S*)\s*$/.exec(before) as RegExpExecArray;
    const head = before.slice(0, lastWord.index);
    if (head) parts.push({ type: "text", value: head });
    const glue: ElementContent[] = [{ type: "text", value: lastWord[1] || WORD_JOINER }];
    run.items.forEach((item, i) => {
      if (i > 0) {
        const separator = value.slice(run.items[i - 1].end, item.start).trim();
        if (separator) glue.push({ type: "text", value: separator });
      }
      glue.push(citationElement(item.index));
    });
    parts.push({ type: "element", tagName: "span", properties: { className: ["chat-cite-glue"] }, children: glue });
    cursor = run.end;
  }
  if (cursor < value.length) parts.push({ type: "text", value: value.slice(cursor) });
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
