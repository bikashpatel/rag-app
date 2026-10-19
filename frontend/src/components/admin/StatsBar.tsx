import { useState } from "react";
import type { AdminStats } from "../../api/types";
import { formatNumber } from "../../lib/format";
import Spinner from "../ui/Spinner";
import { errorMessage, queuedMessage, reindexDocuments } from "./adminApi";
import FlashMessage from "./FlashMessage";
import { useFlash, usePolling, type Resource } from "./hooks";
import { AlertCircleIcon, AlertIcon } from "../ui/icons";
import StatTile from "./StatTile";
import { ErrorState, RefreshError } from "./StateViews";

const QUEUE_POLL_MS = 5000;
const TILE_COUNT = 8;

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
      <div className="admin-tiles admin-tiles-overview" aria-busy="true">
        <span className="sr-only">Loading overview</span>
        {Array.from({ length: TILE_COUNT }, (_, i) => (
          <div key={i} className="admin-tile" aria-hidden="true">
            <span className="ui-skeleton admin-skeleton-label" />
            <span className="ui-skeleton admin-skeleton-value" />
          </div>
        ))}
      </div>
    );
  }

  const activeModel = data.active_embedding_model;
  const visionModel = data.active_vision_model;
  const imageModel = data.active_image_model;
  const stale = data.stale_documents;
  const failed = data.failed_documents;
  return (
    <div className="admin-overview">
      {stats.error ? <RefreshError message={stats.error} onRetry={() => void stats.reload()} /> : null}
      <dl className="admin-tiles admin-tiles-overview" aria-label="Overview">
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
                <span className="spinner" aria-hidden="true" />
                Indexing
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
        <StatTile
          label="Image model"
          value={
            <span className={imageModel ? "admin-tile-text" : "admin-tile-text muted"}>
              {imageModel?.display_name ?? "Off"}
            </span>
          }
          title={imageModel?.display_name}
        />
      </dl>

      {activeModel === null ? (
        <div className="ui-notice ui-notice-danger">
          <AlertCircleIcon size={16} />
          <span className="ui-notice-text">
            <b>No active embedding model.</b> New uploads can't be indexed and document search is off until you
            enable an embedding model and make it active.
          </span>
          <button type="button" className="btn btn-sm" onClick={onOpenModels}>
            Open models
          </button>
        </div>
      ) : stale > 0 || failed > 0 ? (
        <div className={stale > 0 ? "ui-notice ui-notice-warning" : "ui-notice ui-notice-danger"}>
          {stale > 0 ? <AlertIcon size={16} /> : <AlertCircleIcon size={16} />}
          {stale > 0 ? (
            <span className="ui-notice-text">
              <b>
                {formatNumber(stale)} document{stale === 1 ? " was" : "s were"} indexed with a different embedding model
              </b>{" "}
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
            <span className="ui-notice-text">
              <b>
                {formatNumber(failed)} document{failed === 1 ? "" : "s"} failed to index.
              </b>{" "}
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
