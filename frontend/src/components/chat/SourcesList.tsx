import { useEffect, useRef } from "react";
import type { AnswerSource, EmailSource, Source } from "../../api/types";
import { DocumentsIcon, ExternalLinkIcon, MailIcon } from "./icons";
import { formatEmailDate, safeHttpsUrl } from "./plugins/email";
import { prefersReducedMotion } from "./useMediaQuery";

/** A new object for every citation click, so clicking the same citation again scrolls to it again. */
export type SourceHighlight = { index: number };

type SourcesListProps = {
  id: string;
  sources: AnswerSource[];
  highlight: SourceHighlight | null;
};

/** SPEC: Source.snippet is at most 500 characters of the chunk, so a snippet at the cap was cut by the backend. */
const SNIPPET_MAX_CHARS = 500;

/** Mark a cut snippet with "…" (dropping the partial last word) so it doesn't read like broken data. */
function displaySnippet(snippet: string): string {
  // Count code points, as the backend (Python) does, not UTF-16 units.
  if (Array.from(snippet).length < SNIPPET_MAX_CHARS || snippet.endsWith("…")) return snippet;
  // Keep the hard cut when there's no earlier word boundary (one long token).
  const trimmed = snippet.replace(/\s+\S*$/, "").replace(/[\s,;:]+$/, "");
  return `${trimmed || snippet}…`;
}

function relevance(score: number): number {
  return Math.round(Math.min(Math.max(score, 0), 1) * 100);
}

export default function SourcesList({ id, sources, highlight }: SourcesListProps) {
  const items = useRef(new Map<number, HTMLLIElement>());

  useEffect(() => {
    if (!highlight) return;
    items.current
      .get(highlight.index)
      ?.scrollIntoView({ block: "nearest", behavior: prefersReducedMotion() ? "auto" : "smooth" });
  }, [highlight]);

  return (
    <ol id={id} className="chat-sources">
      {sources.map((source) => (
        <li
          key={source.index}
          ref={(el) => {
            if (!el) return;
            items.current.set(source.index, el);
            return () => {
              items.current.delete(source.index);
            };
          }}
          className={highlight?.index === source.index ? "chat-source chat-source-highlight" : "chat-source"}
        >
          {source.kind === "email" ? <EmailSourceItem source={source} /> : <DocumentSourceItem source={source} />}
        </li>
      ))}
    </ol>
  );
}

function DocumentSourceItem({ source }: { source: Source }) {
  return (
    <>
      <div className="chat-source-head">
        <span className="chat-source-index">
          <span className="sr-only">Source </span>
          {source.index}
        </span>
        <DocumentsIcon size={14} />
        <span className="chat-source-name" title={source.filename}>
          {source.filename}
        </span>
        {source.page !== null ? <span className="chat-source-page">p. {source.page}</span> : null}
        <span className="chat-source-score" title="Relevance">
          <span className="sr-only">Relevance </span>
          {relevance(source.score)}%
        </span>
      </div>
      <p className="chat-source-snippet">{displaySnippet(source.snippet)}</p>
    </>
  );
}

/** An email a plugin tool returned: subject, sender and date, snippet, and a link to the conversation. Untrusted text. */
function EmailSourceItem({ source }: { source: EmailSource }) {
  const date = formatEmailDate(source.date);
  const link = safeHttpsUrl(source.url);
  const subject = source.subject || "(no subject)";
  return (
    <>
      <div className="chat-source-head">
        <span className="chat-source-index">
          <span className="sr-only">Email </span>
          {source.index}
        </span>
        <MailIcon size={14} />
        <span className="chat-source-name" title={subject}>
          {subject}
        </span>
      </div>
      <div className="chat-source-email-meta">
        <span className="chat-source-sender">{source.sender}</span>
        {date ? <span className="chat-source-date"> · {date}</span> : null}
      </div>
      {source.snippet ? <p className="chat-source-snippet">{source.snippet}</p> : null}
      {link ? (
        <a className="chat-source-link" href={link} target="_blank" rel="noopener noreferrer">
          {source.plugin_id === "gmail" ? "Open in Gmail" : "Open"}
          <ExternalLinkIcon size={12} />
        </a>
      ) : null}
    </>
  );
}
