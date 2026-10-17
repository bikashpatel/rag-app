import { useId, useMemo, useState, type ReactNode } from "react";
import type { UsageReport } from "../../api/types";
import { formatCompact, formatCost, formatNumber } from "../../lib/format";
import { useResource } from "./hooks";
import MonthPicker, { currentMonth, monthLabel } from "./MonthPicker";
import StatTile from "./StatTile";
import { EmptyState, ResourceGate } from "./StateViews";

function dayLabel(date: string): string {
  const [year, month, day] = date.split("-").map(Number);
  return new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" }).format(
    Date.UTC(year, month - 1, day),
  );
}

export default function UsageTab() {
  const [month, setMonth] = useState(currentMonth);
  const report = useResource<UsageReport>(`/admin/usage?month=${month}`, "Couldn't load usage.");

  return (
    <div className="admin-stack">
      <div className="admin-section-header">
        <div>
          <h2 className="admin-card-title">Usage in {monthLabel(month)}</h2>
          <p className="admin-card-subtitle">
            Calendar months in UTC. Only chat tokens count toward user budgets; embedding and vision tokens are shown for
            cost. Images count toward each user's monthly image limit.
          </p>
        </div>
        <span className="spacer" />
        <MonthPicker
          month={month}
          onChange={setMonth}
          loading={report.loading && report.data !== null}
          loadingLabel="Loading usage"
        />
      </div>
      <ResourceGate resource={report} loadingLabel="Loading usage">
        {(data) => <UsageReportView report={data} pending={data.month !== month} />}
      </ResourceGate>
    </div>
  );
}

function UsageReportView({ report, pending }: { report: UsageReport; pending: boolean }) {
  const { totals } = report;
  const byUser = useMemo(
    () => [...report.by_user].sort((a, b) => b.cost_usd - a.cost_usd || b.chat_tokens - a.chat_tokens),
    [report.by_user],
  );
  const byModel = useMemo(
    () => [...report.by_model].sort((a, b) => b.cost_usd - a.cost_usd || b.requests - a.requests),
    [report.by_model],
  );
  const daily = useMemo(() => [...report.daily].sort((a, b) => a.date.localeCompare(b.date)), [report.daily]);
  const peakChatTokens = Math.max(1, ...daily.map((day) => day.chat_tokens));
  const empty = byUser.length === 0 && byModel.length === 0 && daily.length === 0;

  return (
    <div className={pending ? "admin-stack admin-pending" : "admin-stack"} aria-busy={pending || undefined}>
      <dl className="admin-tiles" aria-label={`Totals for ${monthLabel(report.month)}`}>
        <StatTile label="Chat tokens" value={formatCompact(totals.chat_tokens)} title={formatNumber(totals.chat_tokens)} />
        <StatTile
          label="Embedding tokens"
          value={formatCompact(totals.embedding_tokens)}
          title={formatNumber(totals.embedding_tokens)}
        />
        <StatTile
          label="Vision tokens"
          value={formatCompact(totals.vision_tokens)}
          title={formatNumber(totals.vision_tokens)}
        />
        <StatTile label="Images" value={formatNumber(totals.images)} />
        <StatTile label="Cost" value={formatCost(totals.cost_usd)} />
        <StatTile label="Chat requests" value={formatNumber(totals.requests)} />
      </dl>

      {empty ? (
        <section className="card admin-card">
          <EmptyState title={`No usage recorded in ${monthLabel(report.month)}.`}>
            <p className="muted">Chats and document indexing show up here as they happen.</p>
          </EmptyState>
        </section>
      ) : (
        <>
          <UsageTable
            title="By user"
            headers={["User", "Requests", "Chat tokens", "Embedding tokens", "Vision tokens", "Images", "Cost"]}
            rows={byUser.map((row) => ({
              key: String(row.user_id),
              cells: [
                <span className="admin-user-name admin-nowrap">{row.username}</span>,
                formatNumber(row.requests),
                formatNumber(row.chat_tokens),
                formatNumber(row.embedding_tokens),
                formatNumber(row.vision_tokens),
                formatNumber(row.images),
                formatCost(row.cost_usd),
              ],
            }))}
          />
          <UsageTable
            title="By model"
            headers={["Model", "Kind", "Requests", "Prompt tokens", "Completion tokens", "Cost"]}
            textColumns={2}
            rows={byModel.map((row) => ({
              key: `${row.kind}:${row.model_name}`,
              cells: [
                <code className="admin-code">{row.model_name}</code>,
                <span className={`badge ${row.kind === "chat" ? "badge-info" : "badge-neutral"}`}>{row.kind}</span>,
                formatNumber(row.requests),
                formatNumber(row.prompt_tokens),
                formatNumber(row.completion_tokens),
                formatCost(row.cost_usd),
              ],
            }))}
          />
          <UsageTable
            title="Daily"
            headers={["Day", "Chat tokens", "Embedding tokens", "Vision tokens", "Images", "Cost"]}
            rows={daily.map((day) => ({
              key: day.date,
              cells: [
                <time className="admin-nowrap" dateTime={day.date}>{dayLabel(day.date)}</time>,
                <DailyBar value={day.chat_tokens} peak={peakChatTokens} />,
                formatNumber(day.embedding_tokens),
                formatNumber(day.vision_tokens),
                formatNumber(day.images),
                formatCost(day.cost_usd),
              ],
            }))}
          />
        </>
      )}
    </div>
  );
}

type UsageTableProps = {
  title: string;
  headers: string[];
  rows: { key: string; cells: ReactNode[] }[];
  /** Leading columns that hold text; the rest are right-aligned numbers. */
  textColumns?: number;
};

function UsageTable({ title, headers, rows, textColumns = 1 }: UsageTableProps) {
  const titleId = useId();
  const align = (index: number) => (index < textColumns ? undefined : "num");
  return (
    <section className="card admin-card" aria-labelledby={titleId}>
      <div className="admin-card-header">
        <h3 id={titleId} className="admin-card-title">
          {title}
        </h3>
      </div>
      {rows.length === 0 ? (
        <p className="admin-card-footnote">Nothing recorded.</p>
      ) : (
        <div className="table-wrap">
          <table className="table admin-table">
            <thead>
              <tr>
                {headers.map((header, index) => (
                  <th key={header} scope="col" className={align(index)}>
                    {header}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.key}>
                  {row.cells.map((cell, index) => (
                    <td key={headers[index]} className={align(index)}>
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function DailyBar({ value, peak }: { value: number; peak: number }) {
  return (
    <span className="admin-bar-row" title={`${formatNumber(value)} chat tokens`}>
      <span className="admin-bar" aria-hidden="true">
        <span style={{ width: `${(value / peak) * 100}%` }} />
      </span>
      <span className="admin-bar-value">{formatNumber(value)}</span>
    </span>
  );
}
