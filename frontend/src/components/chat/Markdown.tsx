import { memo, useEffect, useId, useMemo, useState, type ReactNode } from "react";
import type { ElementContent } from "hast";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import type { AnswerSource } from "../../api/types";
import { CheckIcon, CopyIcon } from "../ui/icons";
import { CITATION_PROPERTY, rehypeCitations } from "./citations";

const remarkPlugins = [remarkGfm];
const COPIED_MS = 1500;

type MarkdownProps = {
  content: string;
  sources: AnswerSource[];
  /** Called with the source number when a citation chip is clicked. */
  onCite: (index: number) => void;
};

/** The citation chip's accessible name: "Source 2: report.pdf, page 4" or "Email 7: Invoice 42". */
function citationLabel(index: number, source: AnswerSource | undefined): string {
  if (!source) return `Source ${index}`;
  if (source.kind === "email") return `Email ${index}: ${source.subject || "(no subject)"}`;
  return `Source ${index}: ${source.page === null ? source.filename : `${source.filename}, page ${source.page}`}`;
}

/** The text of a hast subtree (a code block's source, for Copy). */
function hastText(node: ElementContent): string {
  if (node.type === "text") return node.value;
  if (node.type === "element") return node.children.map(hastText).join("");
  return "";
}

/** "language-ts" → "ts"; only short, plain names are shown. */
function languageOf(classes: unknown): string | null {
  const list = Array.isArray(classes) ? classes.map(String) : typeof classes === "string" ? classes.split(" ") : [];
  const name = list.find((item) => item.startsWith("language-"))?.slice("language-".length) ?? "";
  return /^[\w+#.-]{1,24}$/.test(name) ? name : null;
}

function CopyCodeButton({ text, language }: { text: string; language: string | null }) {
  const [result, setResult] = useState<"copied" | "failed" | null>(null);

  useEffect(() => {
    if (!result) return;
    const timer = window.setTimeout(() => setResult(null), COPIED_MS);
    return () => window.clearTimeout(timer);
  }, [result]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setResult("copied");
    } catch {
      setResult("failed");
    }
  }

  return (
    <>
      <button
        type="button"
        className="btn btn-ghost btn-xs chat-code-copy"
        onClick={copy}
        aria-label={language ? `Copy ${language} code` : "Copy code"}
      >
        {result === "copied" ? <CheckIcon size={14} /> : <CopyIcon size={14} />}
        {result === "copied" ? "Copied" : result === "failed" ? "Couldn't copy" : "Copy"}
      </button>
      <span className="sr-only" aria-live="polite">
        {result === "copied" ? "Code copied to the clipboard" : result === "failed" ? "Couldn't copy the code" : ""}
      </span>
    </>
  );
}

/** A fenced code block: a header with the language (when given) and Copy, then the monochrome code. */
function CodeBlock({ language, text, children }: { language: string | null; text: string; children: ReactNode }) {
  return (
    <div className="chat-code">
      <div className="chat-code-head">
        <span className="chat-code-lang">{language ?? ""}</span>
        <CopyCodeButton text={text} language={language} />
      </div>
      {children}
    </div>
  );
}

/** Assistant answer: GFM markdown without raw HTML; links open in a new tab and "[n]" becomes a citation chip. */
function Markdown({ content, sources, onCite }: MarkdownProps) {
  // Footnote ids are prefixed per answer, so footnote links in one answer never jump into another.
  const idPrefix = useId();
  const remarkRehypeOptions = useMemo(() => ({ clobberPrefix: `${idPrefix}-` }), [idPrefix]);
  const rehypePlugins = useMemo(() => [rehypeCitations(new Set(sources.map((source) => source.index)))], [sources]);

  const components = useMemo<Components>(() => {
    const byIndex = new Map(sources.map((source) => [source.index, source]));
    return {
      // An empty href is what the URL sanitizer leaves of unsafe links (javascript: and the like).
      a: ({ node: _node, href, ...props }) =>
        !href ? (
          <span>{props.children}</span>
        ) : href.startsWith("#") ? (
          <a href={href} {...props} />
        ) : (
          <a href={href} {...props} target="_blank" rel="noopener noreferrer" />
        ),
      // Images in answers are shown as links: nothing is loaded from URLs a model (or a document) chose.
      img: ({ src, alt }) =>
        typeof src === "string" && src ? (
          <a href={src} target="_blank" rel="noopener noreferrer">
            {alt || "Image"}
          </a>
        ) : null,
      table: ({ node: _node, ...props }) => (
        <div className="chat-md-table">
          <table {...props} />
        </div>
      ),
      pre: ({ node, ...props }) => {
        const code = node?.children.find(
          (child): child is Extract<ElementContent, { type: "element" }> => child.type === "element" && child.tagName === "code",
        );
        const language = languageOf(code?.properties.className);
        return (
          <CodeBlock language={language} text={code ? hastText(code) : ""}>
            <pre {...props} />
          </CodeBlock>
        );
      },
      sup: ({ node, ...props }) => {
        const index = node?.properties[CITATION_PROPERTY];
        if (typeof index !== "number") return <sup {...props} />;
        const label = citationLabel(index, byIndex.get(index));
        return (
          <button type="button" className="chat-cite" onClick={() => onCite(index)} aria-label={label} title={label}>
            {index}
          </button>
        );
      },
    };
  }, [sources, onCite]);

  return (
    <ReactMarkdown
      remarkPlugins={remarkPlugins}
      remarkRehypeOptions={remarkRehypeOptions}
      rehypePlugins={rehypePlugins}
      components={components}
    >
      {content}
    </ReactMarkdown>
  );
}

export default memo(Markdown);
