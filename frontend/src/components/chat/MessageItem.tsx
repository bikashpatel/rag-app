import { memo, useCallback, useEffect, useId, useState } from "react";
import type { AnswerSource } from "../../api/types";
import { formatNumber } from "../../lib/format";
import { fileTypeTag, type TagTone } from "../documents/fileTypes";
import {
  AlertCircleIcon,
  AlertIcon,
  CheckIcon,
  ChevronDownIcon,
  CopyIcon,
  FileIcon,
  MailIcon,
  RefreshIcon,
  SparkleIcon,
} from "../ui/icons";
import GeneratedFileCard from "./GeneratedFileCard";
import GeneratedImageCard from "./GeneratedImageCard";
import Markdown from "./Markdown";
import PluginCard, { isKnownPluginCard } from "./plugins/PluginCard";
import SourcesList, { type SourceHighlight } from "./SourcesList";
import { isImageCard, pluginCardBusy, type FileCard, type ThreadMessage } from "./useChatStream";

const HIGHLIGHT_MS = 2400;
const COPY_FEEDBACK_MS = 1600;

type MessageItemProps = {
  message: ThreadMessage;
  /** The user message this answer replies to; Retry sends it again. */
  prompt: string | null;
  retryDisabled: boolean;
  onRetry: (prompt: string) => void;
};

function MessageItem(props: MessageItemProps) {
  if (props.message.role === "user") {
    return (
      <article className="chat-message-user" aria-label="You">
        <div className="chat-bubble">{props.message.content}</div>
      </article>
    );
  }
  return <AssistantMessage {...props} />;
}

export default memo(MessageItem);

/** File-type tones as the fixed tag colours (visual spec 3.6): they never follow the accent. */
const DOT_TONES: Record<TagTone, string> = {
  danger: "red",
  info: "blue",
  success: "green",
  warning: "ochre",
  accent: "plum",
  neutral: "ink",
};

/** Up to three overlapping dots for the answer's distinct sources (decorative; the toggle's text says how many). */
function SourceDots({ sources }: { sources: AnswerSource[] }) {
  const seen = new Set<string>();
  const dots: { key: string; tone: string; email: boolean }[] = [];
  for (const source of sources) {
    const key = source.kind === "email" ? "email" : `d${source.document_id}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const tone = source.kind === "email" ? "red" : DOT_TONES[fileTypeTag(source.filename).tone];
    dots.push({ key, tone, email: source.kind === "email" });
    if (dots.length === 3) break;
  }
  return (
    <span className="chat-src-stack" aria-hidden="true">
      {dots.map((dot) => (
        <span key={dot.key} className={`chat-src-dot chat-src-dot-${dot.tone}`}>
          {dot.email ? <MailIcon size={13} /> : <FileIcon size={13} />}
        </span>
      ))}
    </span>
  );
}

function cardsLabel(cards: FileCard[]): string {
  const images = cards.filter(isImageCard).length;
  return images === 0 ? "Files" : images === cards.length ? "Images" : "Files and images";
}

function AssistantMessage({ message, prompt, retryDisabled, onRetry }: MessageItemProps) {
  const sourcesId = useId();
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [highlight, setHighlight] = useState<SourceHighlight | null>(null);

  useEffect(() => {
    if (!highlight) return;
    const timer = window.setTimeout(() => setHighlight(null), HIGHLIGHT_MS);
    return () => window.clearTimeout(timer);
  }, [highlight]);

  const onCite = useCallback((index: number) => {
    setSourcesOpen(true);
    setHighlight({ index });
  }, []);

  const { content, sources, streaming, cards } = message;
  const pluginCards = message.pluginCards.filter(isKnownPluginCard);
  const tokens = message.prompt_tokens + message.completion_tokens;
  const retry =
    prompt !== null ? (
      // aria-disabled, not disabled: starting the retry would otherwise disable the focused button, dropping focus.
      <button
        type="button"
        className="btn btn-sm chat-retry"
        onClick={() => {
          if (!retryDisabled) onRetry(prompt);
        }}
        aria-disabled={retryDisabled || undefined}
      >
        <RefreshIcon size={15} />
        Retry
      </button>
    ) : null;

  return (
    <article className="chat-message-assistant" aria-label="Assistant" aria-busy={streaming}>
      <div className="chat-avatar" aria-hidden="true">
        <SparkleIcon size={16} />
      </div>
      <div className="chat-message-body">
        {content ? (
          <div className={streaming ? "chat-md chat-md-streaming" : "chat-md"}>
            <Markdown content={content} sources={sources} onCite={onCite} />
          </div>
        ) : null}
        {cards.length > 0 ? (
          <ul className="chat-files" aria-label={cardsLabel(cards)}>
            {cards.map((card) => (
              <li key={card.key}>
                {card.kind === "generating" || isImageCard(card) ? (
                  <GeneratedImageCard card={card} />
                ) : (
                  <GeneratedFileCard card={card} />
                )}
              </li>
            ))}
          </ul>
        ) : null}
        {pluginCards.length > 0 ? (
          <ul className="chat-plugin-cards" aria-label="Plugin activity">
            {pluginCards.map((card) => (
              <li key={card.card_id}>
                <PluginCard card={card} />
              </li>
            ))}
          </ul>
        ) : null}
        {/* Below the cards, so they also cover the follow-up call after a file is created or fails. A card being written
            or generated shows its own spinner, and once text streams the cursor at its end takes over. */}
        {streaming &&
        !content &&
        !cards.some((card) => card.kind === "writing" || card.kind === "generating") &&
        !pluginCards.some((card) => pluginCardBusy(card) && !card.stopped) ? (
          <div className="chat-typing" aria-hidden="true">
            <span />
            <span />
            <span />
          </div>
        ) : null}

        {message.warnings.map((warning, i) => (
          <div key={i} className="chat-note chat-note-warning">
            <AlertIcon size={15} />
            <span>{warning}</span>
          </div>
        ))}
        {message.error ? (
          <div className="chat-note chat-note-error">
            <AlertCircleIcon size={15} />
            <span>{message.error}</span>
            {retry}
          </div>
        ) : null}
        {message.stopped ? (
          <div className="chat-note chat-note-muted">
            <span>Stopped</span>
            {retry}
          </div>
        ) : null}

        {sources.length > 0 || !streaming ? (
          <div className="chat-message-footer">
            {sources.length > 0 ? (
              <button
                type="button"
                className="chat-sources-toggle"
                aria-expanded={sourcesOpen}
                aria-controls={sourcesId}
                onClick={() => setSourcesOpen((open) => !open)}
              >
                <SourceDots sources={sources} />
                {sources.length === 1 ? "1 source" : `${sources.length} sources`}
                <ChevronDownIcon size={15} className="chat-sources-chev" />
              </button>
            ) : null}
            {!streaming ? (
              <>
                <span className="chat-message-meta">
                  {message.model_name}
                  {message.model_name && tokens > 0 ? " · " : null}
                  {tokens > 0 ? (
                    <span
                      title={`${formatNumber(message.prompt_tokens)} prompt + ${formatNumber(message.completion_tokens)} completion tokens`}
                    >
                      {formatNumber(tokens)} tokens
                    </span>
                  ) : null}
                </span>
                {content ? <CopyButton text={content} /> : null}
              </>
            ) : null}
          </div>
        ) : null}
        {sourcesOpen && sources.length > 0 ? <SourcesList id={sourcesId} sources={sources} highlight={highlight} /> : null}
      </div>
    </article>
  );
}

function CopyButton({ text }: { text: string }) {
  const [result, setResult] = useState<"copied" | "failed" | null>(null);

  useEffect(() => {
    if (!result) return;
    const timer = window.setTimeout(() => setResult(null), COPY_FEEDBACK_MS);
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

  const label = result === "copied" ? "Copied" : result === "failed" ? "Couldn't copy" : "Copy answer";
  return (
    <>
      <button type="button" className="btn btn-ghost btn-icon btn-sm chat-icon-btn" onClick={copy} aria-label={label} title={label}>
        {result === "copied" ? <CheckIcon size={17} /> : <CopyIcon size={17} />}
      </button>
      <span className="sr-only" aria-live="polite">
        {result === "copied" ? "Answer copied to the clipboard" : result === "failed" ? "Couldn't copy the answer" : ""}
      </span>
    </>
  );
}
