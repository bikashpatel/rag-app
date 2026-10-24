import { useEffect, useLayoutEffect, useRef, useState, type WheelEvent } from "react";
import { ArrowDownIcon } from "./icons";
import MessageItem from "./MessageItem";
import type { ThreadMessage } from "./useChatStream";
import { prefersReducedMotion } from "./useMediaQuery";

// Distance from the bottom (px) that still counts as "at the bottom".
const BOTTOM_SLACK = 32;

type MessageListProps = {
  messages: ThreadMessage[];
  streaming: boolean;
  onRetry: (prompt: string) => void;
};

function lastUserKey(messages: ThreadMessage[]): string | undefined {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "user") return messages[i].key;
  }
  return undefined;
}

function isAtBottom(scroller: HTMLElement): boolean {
  return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight <= BOTTOM_SLACK;
}

/**
 * Scrollable message thread. Follows the bottom while content grows until the user scrolls up or interacts with a
 * message (e.g. opens its sources); then a "Jump to latest" button appears. Sending a message follows again.
 * Remount it (key) for another conversation.
 */
export default function MessageList({ messages, streaming, onRetry }: MessageListProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const lastScrollTop = useRef(0);
  const [showJump, setShowJump] = useState(false);

  useEffect(() => {
    const scroller = scrollRef.current;
    const content = contentRef.current;
    if (!scroller || !content) return;
    const observer = new ResizeObserver(() => {
      if (stickToBottom.current) scroller.scrollTop = scroller.scrollHeight;
      else setShowJump(!isAtBottom(scroller));
    });
    observer.observe(content);
    return () => observer.disconnect();
  }, []);

  // Sending a message always brings the thread back to the bottom.
  const newestUserKey = lastUserKey(messages);
  useLayoutEffect(() => {
    const scroller = scrollRef.current;
    if (!scroller || newestUserKey === undefined) return;
    stickToBottom.current = true;
    scroller.scrollTop = scroller.scrollHeight;
    setShowJump(false);
  }, [newestUserKey]);

  function onScroll() {
    const scroller = scrollRef.current;
    if (!scroller) return;
    // The scroll event of an auto-scroll can arrive after the next chunk has already grown the thread, short of the
    // new bottom; only a move up (wheel, keys, scrollbar, touch) stops following.
    if (isAtBottom(scroller)) stickToBottom.current = true;
    else if (scroller.scrollTop < lastScrollTop.current) stickToBottom.current = false;
    lastScrollTop.current = scroller.scrollTop;
    setShowJump(!stickToBottom.current);
  }

  function onWheel(e: WheelEvent) {
    // Scrolling up during a stream must win against the next auto-scroll, even within BOTTOM_SLACK.
    if (e.deltaY < 0) stickToBottom.current = false;
  }

  function jumpToLatest() {
    const scroller = scrollRef.current;
    if (!scroller) return;
    stickToBottom.current = true;
    scroller.scrollTo({ top: scroller.scrollHeight, behavior: streaming || prefersReducedMotion() ? "auto" : "smooth" });
  }

  return (
    <div className="chat-thread">
      <div
        ref={scrollRef}
        className="chat-scroll"
        onScroll={onScroll}
        onWheel={onWheel}
        role="region"
        aria-label="Messages"
        tabIndex={0}
      >
        <div
          ref={contentRef}
          className="chat-messages"
          onPointerDown={() => {
            stickToBottom.current = false;
          }}
        >
          {messages.map((message, i) => {
            const previous = messages[i - 1];
            return (
              <MessageItem
                key={message.key}
                message={message}
                prompt={message.role === "assistant" && previous?.role === "user" ? previous.content : null}
                retryDisabled={streaming}
                onRetry={onRetry}
              />
            );
          })}
        </div>
      </div>
      {showJump ? (
        <button type="button" className="chat-jump" onClick={jumpToLatest} aria-label="Jump to latest" title="Jump to latest">
          <ArrowDownIcon size={18} />
        </button>
      ) : null}
    </div>
  );
}
