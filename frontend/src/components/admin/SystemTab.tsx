import { useId, useState, type ReactNode } from "react";
import { api } from "../../api/client";
import type {
  AlertChannel,
  AlertKind,
  AlertTestQueued,
  CheckId,
  CheckStatus,
  SystemAlert,
  SystemCheck,
  SystemStatus,
} from "../../api/types";
import { formatBytes, formatDateTime, formatNumber, formatRelativeTime } from "../../lib/format";
import { ButtonSpinner } from "../ui/Spinner";
import { AlertCircleIcon, AlertIcon, CheckCircleIcon, InfoIcon, RefreshIcon } from "../ui/icons";
import { errorMessage } from "./adminApi";
import FlashMessage from "./FlashMessage";
import { useFlash, usePolling, useResource } from "./hooks";
import StatTile, { type StatTone } from "./StatTile";
import { ResourceGate } from "./StateViews";

const POLL_MS = 15_000;

const STATUS_LABELS: Record<CheckStatus, string> = {
  ok: "OK",
  warning: "Warning",
  critical: "Critical",
  unknown: "Unknown",
};
const STATUS_BADGES: Record<CheckStatus, string> = {
  ok: "badge-success",
  warning: "badge-warning",
  critical: "badge-danger",
  unknown: "badge-neutral",
};
const STATUS_TONES: Record<CheckStatus, StatTone> = {
  ok: "neutral",
  warning: "warning",
  critical: "danger",
  unknown: "neutral",
};
const STATUS_ICONS = { ok: CheckCircleIcon, warning: AlertIcon, critical: AlertCircleIcon, unknown: InfoIcon };
const CHANNEL_LABELS: Record<AlertChannel, string> = { webhook: "Webhook", ntfy: "ntfy", email: "Email" };
const KIND_LABELS: Record<AlertKind, string> = {
  problem: "Problem",
  reminder: "Still failing",
  recovery: "Resolved",
  test: "Test",
};
const KIND_BADGES: Record<AlertKind, string> = {
  problem: "badge-danger",
  reminder: "badge-warning",
  recovery: "badge-success",
  test: "badge-info",
};

function StatusIcon({ status }: { status: CheckStatus }) {
  const Icon = STATUS_ICONS[status];
  return (
    <span className={`admin-system-icon admin-system-icon-${status}`} aria-hidden="true">
      <Icon size={16} />
    </span>
  );
}

function StatusBadge({ status }: { status: CheckStatus }) {
  return <span className={`badge ${STATUS_BADGES[status]}`}>{STATUS_LABELS[status]}</span>;
}

function seconds(value: number): string {
  return value < 90 ? `${Math.round(value)} s` : `${Math.round(value / 60)} min`;
}

function when(iso: string | null): ReactNode {
  if (!iso) return "—";
  return (
    <time dateTime={iso} title={formatDateTime(iso)}>
      {formatDateTime(iso)} ({formatRelativeTime(iso)})
    </time>
  );
}

function percent(part: number, total: number): number {
  return total ? Math.round((part / total) * 100) : 0;
}

function list(items: string[]): string {
  return items.length <= 1 ? items.join("") : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

export default function SystemTab() {
  const status = useResource<SystemStatus>("/admin/system", "Couldn't load the system status.");
  const { loading, reload } = status;
  usePolling(true, POLL_MS, () => {
    if (!loading) void reload();
  });

  return (
    <div className="admin-stack">
      <div className="admin-section-header">
        <div>
          <h2 className="admin-card-title">System status</h2>
          <p className="admin-card-subtitle">
            The services, backups, disks and alerts, checked again every 15 seconds while this tab is open. The monitor
            service checks them in the background too, and sends alerts when something breaks or recovers.
          </p>
        </div>
        <span className="spacer" />
        <button
          type="button"
          className="btn btn-sm"
          disabled={loading}
          aria-busy={loading || undefined}
          onClick={() => void reload()}
        >
          {loading && status.data ? <ButtonSpinner size={12} /> : <RefreshIcon size={15} />}
          Refresh
        </button>
      </div>
      <ResourceGate resource={status} loadingLabel="Loading the system status">
        {(data) => <SystemView status={data} onTestQueued={() => void reload()} />}
      </ResourceGate>
    </div>
  );
}

function SystemView({ status, onTestQueued }: { status: SystemStatus; onTestQueued: () => void }) {
  const byId = new Map<CheckId, SystemCheck>(status.checks.map((item) => [item.id, item]));
  const tone = (id: CheckId) => STATUS_TONES[byId.get(id)?.status ?? "unknown"];
  const { worker, queue } = status;
  const attention = status.checks.filter((item) => item.status !== "ok");
  const reconnect = status.gmail_reconnect_needed ?? 0;

  return (
    <>
      {attention.length > 0 ? (
        <div className={`ui-notice ${status.status === "critical" ? "ui-notice-danger" : "ui-notice-warning"}`}>
          {status.status === "critical" ? <AlertCircleIcon size={16} /> : <AlertIcon size={16} />}
          <span className="ui-notice-text">
            <b>
              {attention.length === 1 ? "1 check needs" : `${formatNumber(attention.length)} checks need`} attention:
            </b>{" "}
            {list(attention.map((item) => item.label))}.
          </span>
        </div>
      ) : null}

      <ChecksCard status={status} />

      <dl className="admin-tiles" aria-label="System details">
        <StatTile
          label="Worker heartbeat"
          value={worker.heartbeat_age_seconds === null ? "None" : `${seconds(worker.heartbeat_age_seconds)} ago`}
          detail={worker.running_jobs === null ? undefined : `${formatNumber(worker.running_jobs)} running`}
          tone={tone("worker")}
        />
        <StatTile
          label="Waiting to index"
          value={queue.pending === null ? "—" : formatNumber(queue.pending)}
          detail={
            queue.oldest_pending_age_seconds !== null
              ? `Oldest ${seconds(queue.oldest_pending_age_seconds)}`
              : queue.processing
                ? `${formatNumber(queue.processing)} processing`
                : "Idle"
          }
          tone={tone("queue")}
        />
        <StatTile
          label="Failed in 24 h"
          value={status.failed_documents_24h === null ? "—" : formatNumber(status.failed_documents_24h)}
          tone={status.failed_documents_24h ? "warning" : "neutral"}
        />
        <StatTile
          label="Database"
          value={status.database_size_bytes === null ? "—" : formatBytes(status.database_size_bytes)}
        />
        {status.disks.map((disk) => (
          <StatTile
            key={disk.id}
            label={`Free for ${disk.label.toLowerCase()}`}
            value={formatBytes(disk.free_bytes)}
            detail={`of ${formatBytes(disk.total_bytes)} (${percent(disk.free_bytes, disk.total_bytes)} %)`}
            tone={tone(disk.id === "uploads" ? "disk_uploads" : "disk_backups")}
          />
        ))}
        <StatTile
          label="App version"
          value={
            <span className={status.app_version ? "admin-tile-text" : "admin-tile-text muted"}>
              {status.app_version ?? "Not set"}
            </span>
          }
          title={status.app_version ?? undefined}
        />
      </dl>

      {reconnect > 0 ? (
        <div className="ui-notice ui-notice-info">
          <InfoIcon size={16} />
          <span className="ui-notice-text">
            {reconnect === 1 ? "1 Gmail connection needs" : `${formatNumber(reconnect)} Gmail connections need`}{" "}
            reconnecting. Their users see this in Settings → Plugins & data.
          </span>
        </div>
      ) : null}

      <BackupsCard status={status} check={byId.get("backup")} />
      <AlertsCard status={status} onTestQueued={onTestQueued} />
    </>
  );
}

function ChecksCard({ status }: { status: SystemStatus }) {
  const titleId = useId();
  return (
    <section className="card admin-card" aria-labelledby={titleId}>
      <div className="admin-card-header">
        <div className="admin-card-heading">
          <h3 id={titleId} className="admin-card-title">
            Checks
          </h3>
          <p className="admin-card-subtitle">
            Checked {when(status.checked_at)}.
          </p>
        </div>
        <StatusBadge status={status.status} />
      </div>
      <ul className="admin-system-checks">
        {status.checks.map((item) => (
          <li key={item.id} className="admin-system-check">
            <StatusIcon status={item.status} />
            <span className="admin-system-check-label">{item.label}</span>
            <StatusBadge status={item.status} />
            <span className="admin-system-check-detail">{item.detail}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function BackupsCard({ status, check }: { status: SystemStatus; check: SystemCheck | undefined }) {
  const titleId = useId();
  const { backup } = status;
  return (
    <section className="card admin-card" aria-labelledby={titleId}>
      <div className="admin-card-header">
        <div className="admin-card-heading">
          <h3 id={titleId} className="admin-card-title">
            Backups
          </h3>
          <p className="admin-card-subtitle">
            The database, the uploaded files and the vector index, verified after every backup.
          </p>
        </div>
        {check ? <StatusBadge status={check.status} /> : null}
      </div>
      {backup.status_seen ? (
        <dl className="admin-system-facts">
          <dt>Last backup</dt>
          <dd>
            {backup.last_success_at ? (
              <>
                {when(backup.last_success_at)}
                {backup.last_success_size_bytes !== null ? `, ${formatBytes(backup.last_success_size_bytes)}` : ""}
                {backup.last_success_name ? (
                  <>
                    {" "}
                    <code className="admin-code">{backup.last_success_name}</code>
                  </>
                ) : null}
              </>
            ) : (
              "None yet"
            )}
          </dd>
          {backup.last_failure_at ? (
            <>
              <dt>Last failure</dt>
              <dd className={backup.failing ? "admin-system-failing" : undefined}>
                {when(backup.last_failure_at)}: {backup.last_failure_error}
              </dd>
            </>
          ) : null}
          {backup.running_since ? (
            <>
              <dt>Running</dt>
              <dd>Since {when(backup.running_since)}</dd>
            </>
          ) : null}
          <dt>Schedule</dt>
          <dd>
            {backup.schedule ? (
              <>
                Daily at {backup.schedule.split(",").join(", ")} UTC; next {when(backup.next_run_at)}
              </>
            ) : (
              "The backup service isn't running its schedule"
            )}
          </dd>
          {backup.backups_kept !== null ? (
            <>
              <dt>Kept</dt>
              <dd>
                {formatNumber(backup.backups_kept)} backup{backup.backups_kept === 1 ? "" : "s"}
                {backup.total_size_bytes !== null ? `, ${formatBytes(backup.total_size_bytes)} in all` : ""}
              </dd>
            </>
          ) : null}
        </dl>
      ) : (
        <p className="admin-system-empty">{check?.detail ?? "No backup has run yet."}</p>
      )}
      <p className="admin-card-footnote">
        Backups don't include <code className="admin-code">.env</code>. Keep a copy of it (above all{" "}
        <code className="admin-code">SECRET_KEY</code> and{" "}
        <code className="admin-code">TOKEN_ENCRYPTION_KEY</code>) in a password manager: without the same token key,
        every user reconnects Gmail after a restore. Back up now with{" "}
        <code className="admin-code">scripts/backup.sh</code>, restore with{" "}
        <code className="admin-code">scripts/restore.sh</code>, and copy the backup folder off this machine.
      </p>
    </section>
  );
}

function deliveryText(alert: SystemAlert, channels: AlertChannel[]): string {
  if (channels.length === 0 && alert.delivered.length === 0 && alert.failed.length === 0) {
    return "Logged only (no alert channel)";
  }
  const parts: string[] = [];
  if (alert.delivered.length) parts.push(`Sent to ${list(alert.delivered.map((name) => CHANNEL_LABELS[name]))}`);
  if (alert.failed.length) parts.push(`not delivered to ${list(alert.failed.map((name) => CHANNEL_LABELS[name]))}`);
  const text = parts.join("; ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function AlertsCard({ status, onTestQueued }: { status: SystemStatus; onTestQueued: () => void }) {
  const titleId = useId();
  const flash = useFlash();
  const [sending, setSending] = useState(false);
  const { monitor, alerts } = status;
  const blocked = !monitor.running
    ? "The monitor service isn't running, so no alert can be sent."
    : monitor.channels.length === 0
      ? "Set an alert channel in .env first (ALERT_WEBHOOK_URL, ALERT_NTFY_URL or ALERT_SMTP_…)."
      : monitor.test_pending
        ? "A test alert is on its way."
        : null;

  async function sendTest() {
    setSending(true);
    try {
      await api.post<AlertTestQueued>("/admin/system/test-alert");
      flash.success("Test alert queued: the monitor sends it to every channel on its next check.");
      onTestQueued();
    } catch (err) {
      flash.error(errorMessage(err, "Couldn't queue a test alert."));
    } finally {
      setSending(false);
    }
  }

  return (
    <section className="card admin-card" aria-labelledby={titleId}>
      <div className="admin-card-header">
        <div className="admin-card-heading">
          <h3 id={titleId} className="admin-card-title">
            Alerts
          </h3>
          <p className="admin-card-subtitle">
            Sent when something breaks, as a reminder while it stays broken, and when it recovers. They never include
            document names, email contents or usernames.
          </p>
        </div>
        <button
          type="button"
          className="btn btn-sm"
          disabled={blocked !== null || sending}
          aria-busy={sending || undefined}
          title={blocked ?? undefined}
          onClick={() => void sendTest()}
        >
          {sending ? <ButtonSpinner size={12} /> : null}
          Send a test alert
        </button>
      </div>
      <FlashMessage flash={flash} />
      <div className="admin-system-channels">
        {monitor.channels.length ? (
          <>
            <span className="muted">Alerts go to</span>
            {monitor.channels.map((name) => (
              <span key={name} className="badge badge-info">
                {CHANNEL_LABELS[name]}
              </span>
            ))}
          </>
        ) : (
          <span className="muted">
            {monitor.running
              ? "No alert channel is configured: alerts are only logged and shown below."
              : "The monitor service isn't running: no checks run in the background and no alerts are sent."}
          </span>
        )}
        {monitor.last_run_at ? (
          <span className="muted admin-system-last-run">Last check {formatRelativeTime(monitor.last_run_at)}</span>
        ) : null}
      </div>
      {alerts.length === 0 ? (
        <p className="admin-system-empty">No alerts yet.</p>
      ) : (
        <ol className="admin-system-alerts" aria-label="Recent alerts">
          {alerts.map((alert, index) => (
            <li key={`${alert.at}-${index}`} className="admin-system-alert">
              <div className="admin-system-alert-head">
                <span className={`badge ${KIND_BADGES[alert.kind]}`}>{KIND_LABELS[alert.kind]}</span>
                <span className="admin-system-alert-title">{alert.title}</span>
                <time className="muted admin-nowrap" dateTime={alert.at}>
                  {formatDateTime(alert.at)}
                </time>
              </div>
              <ul className="admin-system-alert-lines">
                {alert.lines
                  .filter((line) => !line.startsWith("At ") && !line.startsWith("Details: "))
                  .map((line, lineIndex) => (
                    <li key={lineIndex}>{line}</li>
                  ))}
              </ul>
              <p className={`admin-system-alert-delivery${alert.failed.length ? " admin-text-warning" : ""}`}>
                {deliveryText(alert, monitor.channels)}
              </p>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
