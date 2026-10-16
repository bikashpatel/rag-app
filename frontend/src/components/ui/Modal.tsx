import { useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

type ModalProps = {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** Buttons rendered right-aligned in the footer. */
  footer?: ReactNode;
  width?: number;
};

const FOCUSABLE =
  'a[href], button:not(:disabled), input:not(:disabled):not([type="hidden"]), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';

/** Modals currently open: the app root stays inert until the last one closes. */
let openModals = 0;

function setBackgroundInert(inert: boolean): void {
  const root = document.getElementById("root");
  if (root) root.inert = inert;
}

/**
 * Dialog rendered into <body>, outside the app root, which is made inert while it is open (as aria-modal promises):
 * the page behind can't be focused, clicked or reached by assistive tech. Tab and Shift+Tab cycle inside the dialog,
 * Escape closes it, and focus returns to the element that opened it.
 */
export default function Modal({ open, title, onClose, children, footer, width = 520 }: ModalProps) {
  const titleId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  // Callers usually pass an inline onClose; keep it in a ref so the effect below
  // runs only when the modal opens/closes (not on every render, which would steal focus).
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!open) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        // Handled here, so a drawer the dialog was opened from (useDrawer) doesn't close as well.
        e.preventDefault();
        onCloseRef.current();
        return;
      }
      if (e.key !== "Tab") return;
      const dialog = dialogRef.current;
      if (!dialog) return;
      const items = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (el) => el.getClientRects().length > 0,
      );
      if (items.length === 0) {
        e.preventDefault();
        dialog.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      const inside = active instanceof Node && dialog.contains(active);
      // Wrap at either end, and pull focus back in if it ever ended up outside (e.g. on <body> after the
      // focused control was replaced).
      if (e.shiftKey ? !inside || active === dialog || active === first : !inside || active === last) {
        e.preventDefault();
        (e.shiftKey ? last : first).focus();
      }
    };
    // Capture phase: runs before the bubbling listeners of drawers on the document.
    document.addEventListener("keydown", onKey, true);
    openModals += 1;
    setBackgroundInert(true);
    // Focus the first form control (or the dialog itself) when opening.
    const first = dialogRef.current?.querySelector<HTMLElement>("input, select, textarea, button:not([data-modal-close])");
    (first ?? dialogRef.current)?.focus();
    return () => {
      document.removeEventListener("keydown", onKey, true);
      openModals -= 1;
      // The background must be focusable again before focus can go back to the opener.
      if (openModals === 0) setBackgroundInert(false);
      if (previouslyFocused && previouslyFocused !== document.body && previouslyFocused.isConnected) {
        previouslyFocused.focus();
      }
    };
  }, [open]);

  if (!open) return null;

  return createPortal(
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        style={{ maxWidth: width }}
      >
        <div className="modal-header">
          <h2 id={titleId}>{title}</h2>
          <button type="button" className="btn btn-ghost btn-icon" onClick={onClose} aria-label="Close" data-modal-close>
            ✕
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer ? <div className="modal-footer">{footer}</div> : null}
      </div>
    </div>,
    document.body,
  );
}
