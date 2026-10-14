import { useState } from "react";
import type { AdminStats } from "../../api/types";
import { formatNumber } from "../../lib/format";
import Spinner from "../ui/Spinner";
import { errorMessage, queuedMessage, reindexDocuments } from "./adminApi";
import FlashMessage from "./FlashMessage";
import { useFlash, usePolling, type Resource } from "./hooks";
import { AlertIcon } from "./icons";
import StatTile from "./StatTile";
import { ErrorState, RefreshError } from "./StateViews";

const QUEUE_POLL_MS = 5000;
const TILE_COUNT = 7;

type StatsBarProps = {
  stats: Resource<AdminStats>;
  onOpenModels: () => void;
};

export default function StatsBar({ stats, onOpenModels }: StatsBarProps) {
  const flash = useFlash();
  const [reindexing, setReindexing] = useState(false);
  const data = stats.data;

  usePolling((data?.queue_length ?? 0) > 0, QUEUE_POLL_MS, () => {
    if (!stats.loading) void stats.reload();
  });

  /** only_stale=true re-queues both stale and failed documents (POST /admin/reindex). */
  async function reindexStaleAndFailed() {
    setReindexing(true);
    try {
      flash.success(queuedMessage(await reindexDocuments(true)));
      await stats.reload();
    } catch (err) {
      flash.error(errorMessage(err, "Couldn't queue the documents."));
    } finally {
      setReindexing(false);
    }
  }

  if (!data) {
    if (stats.error) return <ErrorState message={stats.error} onRetry={() => void stats.reload()} />;
    return (
      <div className="admin-tiles" aria-busy="true">
        <span className="sr-only">Loading overview</span>
        {Array.from({ length: TILE_COUNT }, (_, i) => (
          <div key={i} className="admin-tile" aria-hidden="true">
            <span className="admin-skeleton" />
            <span className="admin-skeleton admin-skeleton-value" />
          </div>
        ))}
      </div>
    );
  }

  const activeModel = data.active_embedding_model;
  const visionModel = data.active_vision_model;
  const stale = data.stale_documents;
  const failed = data.failed_documents;
  return (
    <div className="admin-overview">
      {stats.error ? <RefreshError message={stats.error} onRetry={() => void stats.reload()} /> : null}
      <dl className="admin-tiles" aria-label="Overview">
        <StatTile label="Users" value={formatNumber(data.users)} />
        <StatTile label="Documents" value={formatNumber(data.documents)} />
        <StatTile
          label="Failed"
          value={formatNumber(failed)}
          tone={failed > 0 ? "danger" : "neutral"}
        />
        <StatTile label="Needs re-index" value={formatNumber(stale)} tone={stale > 0 ? "warning" : "neutral"} />
        <StatTile
          label="Ingestion queue"
          value={formatNumber(data.queue_length)}
          detail={
            data.queue_length > 0 ? (
              <span className="admin-tile-busy">
                <Spinner size={11} label="Processing" />
                Processing
              </span>
            ) : (
              "Idle"
            )
          }
        />
        <StatTile
          label="Embedding model"
          value={<span className="admin-tile-text">{activeModel?.display_name ?? "None"}</span>}
          title={activeModel?.display_name}
          tone={activeModel ? "neutral" : "danger"}
        />
        <StatTile
          label="Vision model"
          value={
            <span className={visionModel ? "admin-tile-text" : "admin-tile-text muted"}>
              {visionModel?.display_name ?? "Off"}
            </span>
          }
          title={visionModel?.display_name}
        />
      </dl>

      {activeModel === null ? (
        <div className="admin-notice admin-notice-danger">
          <AlertIcon size={18} />
          <span className="admin-notice-text">
            <strong>No active embedding model.</strong> New uploads can't be indexed and document search is off until you
            enable an embedding model and make it active.
          </span>
          <button type="button" className="btn btn-sm" onClick={onOpenModels}>
            Open models
          </button>
        </div>
      ) : stale > 0 || failed > 0 ? (
        <div className={stale > 0 ? "admin-notice admin-notice-warning" : "admin-notice admin-notice-danger"}>
          <AlertIcon size={18} />
          {stale > 0 ? (
            <span className="admin-notice-text">
              <strong>
                {formatNumber(stale)} document{stale === 1 ? " was" : "s were"} indexed with a different embedding model
              </strong>{" "}
              than the active one ({activeModel.display_name}). Search skips {stale === 1 ? "it" : "them"} until{" "}
              {stale === 1 ? "it is" : "they are"} re-indexed.
              {failed > 0 ? (
                <>
                  {" "}
                  {formatNumber(failed)} failed document{failed === 1 ? " is" : "s are"} retried too.
                </>
              ) : null}
            </span>
          ) : (
            <span className="admin-notice-text">
              <strong>
                {formatNumber(failed)} document{failed === 1 ? "" : "s"} failed to process.
              </strong>{" "}
              Retry once the cause is fixed (for example the embedding model or its API key). Files that can't be read
              will fail again; delete them from the Documents tab.
            </span>
          )}
          <button
            type="button"
            className="btn btn-primary btn-sm"
            disabled={reindexing}
            onClick={() => void reindexStaleAndFailed()}
          >
            {reindexing ? <Spinner size={12} label="Queuing" /> : null}
            {stale > 0 ? "Re-index now" : "Retry failed"}
          </button>
        </div>
      ) : null}
      <FlashMessage flash={flash} />
    </div>
  );
}
