import { useEffect, useId, useRef, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import { CloseIcon } from "./icons";

type ModalProps = {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** Buttons rendered right-aligned in the footer. */
  footer?: ReactNode;
  /** Maximum width in px (without it: 520, or what the caller's `className` sets; large dialogs go up to 1100). */
  width?: number;
  /** "sheet": the dialog fills the screen under 800 px. */
  variant?: "dialog" | "sheet";
  /** Added to the dialog element, for sizing by the caller's own class prefix. */
  className?: string;
  /** aria-describedby of the dialog. */
  describedBy?: string;
  /** Focused on open instead of the first form control. */
  initialFocus?: RefObject<HTMLElement | null>;
  /** No built-in header (title and close button): the caller renders its own title and close control. */
  hideHeader?: boolean;
  /** aria-labelledby (an id inside children). Without it a dialog with a hidden header gets aria-label={title}. */
  labelledBy?: string;
  /** Children go straight into the dialog: no padded, scrolling body (the caller lays out and scrolls its panes). */
  bare?: boolean;
};

const FOCUSABLE =
  'a[href], button:not(:disabled), input:not(:disabled):not([type="hidden"]), select:not(:disabled), textarea:not(:disabled), [tabindex]';
/** What gets focus on open when there is no `initialFocus`: the first form control (the close button is last resort). */
const OPEN_FOCUS = 'input:not(:disabled):not([type="hidden"]), select:not(:disabled), textarea:not(:disabled), button:not(:disabled):not([data-modal-close])';

/** Tab stops: roving items (tabs, menu items, the unselected radios of a group) have tabindex -1 and are skipped. */
function tabbable(container: HTMLElement, selector: string): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(selector)).filter(
    (el) => el.getAttribute("tabindex") !== "-1" && !el.closest("[inert]") && el.getClientRects().length > 0,
  );
}

/** Modals currently open: the app root stays inert until the last one closes. */
let openModals = 0;

function setBackgroundInert(inert: boolean): void {
  const root = document.getElementById("root");
  if (root) root.inert = inert;
}

/**
 * Dialog rendered into <body>, outside the app root, which is made inert while it is open (as aria-modal promises):
 * the page behind can't be focused, clicked or reached by assistive tech. Tab and Shift+Tab cycle inside the dialog,
 * Escape closes it, and focus returns to the element that opened it. Focus on open runs after the children's own
 * effects, so `initialFocus` wins; a child may still move focus later (after a load).
 */
export default function Modal({
  open,
  title,
  onClose,
  children,
  footer,
  width,
  variant = "dialog",
  className,
  describedBy,
  initialFocus,
  hideHeader = false,
  labelledBy,
  bare = false,
}: ModalProps) {
  const titleId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  // Callers usually pass an inline onClose; keep it in a ref so the effect below
  // runs only when the modal opens/closes (not on every render, which would steal focus).
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const initialFocusRef = useRef(initialFocus);
  initialFocusRef.current = initialFocus;

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
      const items = tabbable(dialog, FOCUSABLE);
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
    const dialog = dialogRef.current;
    const requested = initialFocusRef.current?.current;
    if (requested?.isConnected) requested.focus();
    else if (dialog) (tabbable(dialog, OPEN_FOCUS)[0] ?? dialog).focus();
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

  const sheet = variant === "sheet";
  const label = labelledBy ? { "aria-labelledby": labelledBy } : hideHeader ? { "aria-label": title } : { "aria-labelledby": titleId };

  return createPortal(
    <div
      className={sheet ? "modal-backdrop modal-backdrop-sheet" : "modal-backdrop"}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        className={["modal", sheet ? "modal-sheet" : null, className].filter(Boolean).join(" ")}
        role="dialog"
        aria-modal="true"
        {...label}
        aria-describedby={describedBy}
        tabIndex={-1}
        style={width ? { maxWidth: width } : undefined}
      >
        {hideHeader ? null : (
          <div className="modal-header">
            <h2 id={titleId}>{title}</h2>
            <button type="button" className="btn btn-ghost btn-icon" onClick={onClose} aria-label="Close" data-modal-close>
              <CloseIcon size={18} />
            </button>
          </div>
        )}
        {bare ? children : <div className="modal-body">{children}</div>}
        {footer ? <div className="modal-footer">{footer}</div> : null}
      </div>
    </div>,
    document.body,
  );
}
