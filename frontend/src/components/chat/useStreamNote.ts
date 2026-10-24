import { useEffect, useRef, useState } from "react";
import type { ThreadMessage } from "./useChatStream";

function result(answer: ThreadMessage): string {
  if (answer.error) return `The answer failed: ${answer.error}`;
  return answer.stopped ? "Answer stopped." : "Answer ready.";
}

/**
 * The chat page's polite status line about the answer being written. Its region stays mounted across the empty
 * state and the thread, so the first answer of a new chat is announced too; it speaks only when a stream starts or
 * ends, never because a conversation was opened. A send rejected before streaming says nothing (its error is an alert).
 */
export function useStreamNote(threadKey: number, streaming: boolean, messages: ThreadMessage[]): string {
  const [note, setNote] = useState("");
  const state = useRef({ threadKey, streaming, answerKey: null as string | null });
  const messagesRef = useRef(messages);
  messagesRef.current = messages;

  useEffect(() => {
    const current = state.current;
    if (threadKey !== current.threadKey) {
      state.current = { threadKey, streaming, answerKey: null };
      setNote("");
      return;
    }
    if (streaming === current.streaming) return;
    current.streaming = streaming;
    if (streaming) {
      const last = messagesRef.current.at(-1);
      current.answerKey = last?.role === "assistant" ? last.key : null;
      setNote("Generating an answer…");
    } else {
      const answer = messagesRef.current.find((message) => message.key === current.answerKey);
      setNote(answer ? result(answer) : "");
    }
  }, [threadKey, streaming]);

  return note;
}
