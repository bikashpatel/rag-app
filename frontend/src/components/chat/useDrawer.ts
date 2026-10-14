import { useEffect, useRef, type RefObject } from "react";

const FOCUSABLE = 'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';

/**
 * While `open`: Escape (unless something handled it first) calls `onClose`, focus moves into the
 * container, and it goes back to the previously focused element when the drawer closes.
 */
export function useDrawer(open: boolean, containerRef: RefObject<HTMLElement | null>, onClose: () => void): void {
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!open) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented) onCloseRef.current();
    };
    document.addEventListener("keydown", onKey);
    const container = containerRef.current;
    (container?.querySelector<HTMLElement>(FOCUSABLE) ?? container)?.focus();
    return () => {
      document.removeEventListener("keydown", onKey);
      if (previouslyFocused?.isConnected) previouslyFocused.focus();
    };
  }, [open, containerRef]);
}
