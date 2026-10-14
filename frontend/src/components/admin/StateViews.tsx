import type { ReactNode } from "react";
import Spinner from "../ui/Spinner";
import type { Resource } from "./hooks";
import { AlertIcon, RefreshIcon } from "./icons";

export function LoadingState({ label }: { label: string }) {
  return (
    <div className="admin-state">
      <Spinner size={22} label={label} />
      <span aria-hidden="true">{label}…</span>
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="admin-state admin-state-error" role="alert">
      <AlertIcon size={22} />
      <p>{message}</p>
      <button type="button" className="btn btn-sm" onClick={onRetry}>
        <RefreshIcon size={14} />
        Try again
      </button>
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="admin-state">
      <p className="admin-state-title">{title}</p>
      {children}
    </div>
  );
}

/** Inline notice for a failed refresh while the last good data stays on screen. */
export function RefreshError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="admin-notice admin-notice-danger" role="alert">
      <AlertIcon size={16} />
      <span className="admin-notice-text">{message}</span>
      <button type="button" className="btn btn-sm" onClick={onRetry}>
        Retry
      </button>
    </div>
  );
}

type ResourceGateProps<T> = {
  resource: Resource<T>;
  loadingLabel: string;
  children: (data: T) => ReactNode;
};

/** Loading and error states for a resource; renders `children` once data has arrived. */
export function ResourceGate<T>({ resource, loadingLabel, children }: ResourceGateProps<T>) {
  const retry = () => void resource.reload();
  if (resource.data === null) {
    return resource.error ? <ErrorState message={resource.error} onRetry={retry} /> : <LoadingState label={loadingLabel} />;
  }
  return (
    <>
      {resource.error ? <RefreshError message={resource.error} onRetry={retry} /> : null}
      {children(resource.data)}
    </>
  );
}
