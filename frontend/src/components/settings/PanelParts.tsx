import type { ReactNode } from "react";
import { AlertIcon, CloseIcon } from "../ui/icons";

type PanelHeadProps = {
  title: string;
  /** Beside the title, baseline-aligned ("October 2026 (UTC)"). */
  note?: ReactNode;
  /** The tab's own controls, before the close button (a "Saved" mark, Refresh). */
  actions?: ReactNode;
  onClose: () => void;
};

/** The sticky header of a Settings panel: the tab's h2, then its actions and the close button (hidden on phones). */
export function PanelHead({ title, note, actions, onClose }: PanelHeadProps) {
  return (
    <header className="settings-head">
      <h2>{title}</h2>
      {note ? <span className="settings-head-note">{note}</span> : null}
      <div className="settings-head-actions">
        {actions}
        <button type="button" className="btn btn-ghost btn-icon settings-close" aria-label="Close settings" onClick={onClose}>
          <CloseIcon />
        </button>
      </div>
    </header>
  );
}

/** A load that failed: the message and Try again. */
export function PanelError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="settings-state">
      <p className="ui-notice ui-notice-danger" role="alert">
        <AlertIcon size={15} />
        <span className="ui-notice-text">{message}</span>
      </p>
      <button type="button" className="btn btn-sm" onClick={onRetry}>
        Try again
      </button>
    </div>
  );
}

/** Usage while it loads: the lede, three tiles and the chart as a flat card. */
export function UsageSkeleton() {
  return (
    <div className="settings-skeleton">
      <span className="sr-only">Loading your usage…</span>
      <span className="ui-skeleton" style={{ width: "58%" }} aria-hidden="true" />
      <div className="settings-meters settings-skeleton-tiles" aria-hidden="true">
        {[0, 1, 2].map((index) => (
          <div key={index} className="settings-tile">
            <span className="ui-skeleton" style={{ width: "48%" }} />
            <span className="ui-skeleton settings-skeleton-figure" />
            <span className="ui-skeleton ui-skeleton-sub" style={{ width: "40%" }} />
          </div>
        ))}
      </div>
      <div className="settings-skeleton-card settings-skeleton-block" aria-hidden="true" />
      <div className="settings-skeleton-card settings-skeleton-chart" aria-hidden="true" />
    </div>
  );
}

/** Account while it loads: the profile card and the sign-in list. */
export function AccountSkeleton() {
  return (
    <div className="settings-skeleton">
      <span className="sr-only">Loading your account…</span>
      <div className="settings-skeleton-card settings-skeleton-profile" aria-hidden="true">
        <span className="ui-skeleton settings-skeleton-avatar" />
        <span className="settings-skeleton-lines">
          <span className="ui-skeleton" style={{ width: "42%", height: 18 }} />
          <span className="ui-skeleton ui-skeleton-sub" style={{ width: "64%" }} />
        </span>
      </div>
      {[0, 1, 2].map((index) => (
        <div key={index} className="settings-skeleton-card settings-skeleton-profile" aria-hidden="true">
          <span className="ui-skeleton settings-skeleton-icon" />
          <span className="settings-skeleton-lines">
            <span className="ui-skeleton" style={{ width: "36%" }} />
            <span className="ui-skeleton ui-skeleton-sub" style={{ width: "22%" }} />
          </span>
        </div>
      ))}
    </div>
  );
}

/** "1 chat" / "48 chats". */
export function plural(count: number, one: string, many: string): string {
  return `${new Intl.NumberFormat().format(count)} ${count === 1 ? one : many}`;
}
