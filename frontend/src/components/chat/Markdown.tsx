import { memo, useId, useMemo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import type { AnswerSource } from "../../api/types";
import { CITATION_PROPERTY, rehypeCitations } from "./citations";

const remarkPlugins = [remarkGfm];

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
