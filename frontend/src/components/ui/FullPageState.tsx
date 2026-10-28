import type { ReactNode } from "react";
import Spinner from "./Spinner";

/** The boot spinner: centred on the page ground. */
export function FullPageSpinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="ui-fullpage">
      <Spinner size={24} label={label} />
    </div>
  );
}

type FullPageMessageProps = {
  title: string;
  children: ReactNode;
  /** The buttons under the text (a primary "Try again", for example). */
  action?: ReactNode;
};

/**
 * A whole-page message ("Can't reach the server", the error boundary): a card with the brand mark, a serif title, the
 * text and the actions. Uses no router or auth context, so it can sit outside both.
 */
export function FullPageMessage({ title, children, action }: FullPageMessageProps) {
  return (
    <div className="ui-fullpage">
      <div className="card ui-fullpage-card" role="alert">
        <span className="ui-brand-mark" aria-hidden="true">
          N
        </span>
        <h1 className="ui-fullpage-title">{title}</h1>
        <div className="ui-fullpage-text">{children}</div>
        {action ? <div className="ui-fullpage-actions">{action}</div> : null}
      </div>
    </div>
  );
}
