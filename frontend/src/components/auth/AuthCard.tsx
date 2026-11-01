import { useEffect, useRef, type ReactNode } from "react";
import "./auth-components.css";

type Mark = { icon: ReactNode; tone: "accent" | "success" | "warning" | "neutral" } | "busy";

/**
 * A task page (verify, reset, confirm, undo, Google sign-up, forgot): the brand on the desk, one sheet of paper with
 * the task, and the policy links. Without `mark` the card is a form (left-aligned title and lede; put the fields in a
 * `form.auth-form` and the main button in `.auth-submit`); with one it is a result (centred: the status mark, the
 * title, `lede` as its text, then the children, such as `.auth-actions`). `footer` holds the links under the card's
 * content. The title takes focus whenever it changes, and when the page opens unless a field took focus first.
 */
export default function AuthCard({
  title,
  lede,
  mark,
  children,
  footer,
}: {
  title: string;
  lede?: ReactNode;
  mark?: Mark;
  children?: ReactNode;
  footer?: ReactNode;
}) {
  const card = useRef<HTMLElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const focusedTitle = useRef<string | null>(null);

  useEffect(() => {
    if (focusedTitle.current === title) return;
    const opening = focusedTitle.current === null;
    focusedTitle.current = title;
    if (opening && card.current?.contains(document.activeElement)) return;
    heading.current?.focus();
  }, [title]);

  const tone = mark === "busy" ? "neutral" : mark?.tone;
  const markClass = tone === "accent" ? "auth-mark" : `auth-mark auth-mark-${tone}`;
  return (
    <div className="auth-page">
      <header className="auth-page-top">
        <a className="ui-brand" href="/login" aria-label="AskNuvexusDoc, sign in">
          <span className="ui-brand-mark" aria-hidden="true">
            N
          </span>
          <span className="ui-brand-name">AskNuvexusDoc</span>
        </a>
      </header>
      <main
        ref={card}
        className={mark ? "auth-card auth-result" : "auth-card"}
        aria-busy={mark === "busy" || undefined}
      >
        {mark ? (
          <div className={markClass} aria-hidden="true">
            {mark === "busy" ? <span className="auth-mark-spinner" /> : mark.icon}
          </div>
        ) : null}
        <h1 ref={heading} className="auth-title" tabIndex={-1}>
          {title}
        </h1>
        {lede ? <p className={mark ? "auth-text" : "auth-lede"}>{lede}</p> : null}
        {children}
        {footer ? <div className="auth-links">{footer}</div> : null}
      </main>
      <nav className="auth-page-foot" aria-label="Policies">
        <ul className="auth-page-links">
          <li>
            <a href="/terms">Terms</a>
          </li>
          <li>
            <a href="/privacy">Privacy</a>
          </li>
          <li>
            <a href="/contact">Contact</a>
          </li>
        </ul>
      </nav>
    </div>
  );
}
