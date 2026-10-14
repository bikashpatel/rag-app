import { useId, useMemo, useState, type ReactNode } from "react";
import type { UsageReport } from "../../api/types";
import { formatCompact, formatCost, formatNumber } from "../../lib/format";
import Spinner from "../ui/Spinner";
import { useResource } from "./hooks";
import { ChevronLeftIcon, ChevronRightIcon } from "./icons";
import StatTile from "./StatTile";
import { EmptyState, ResourceGate } from "./StateViews";

const MONTH_PATTERN = /^\d{4}-(0[1-9]|1[0-2])$/;

/** Budgets use UTC calendar months, so the report does too. */
function currentMonth(): string {
  return new Date().toISOString().slice(0, 7);
}

function parseMonth(month: string): [number, number] {
  const [year, monthNumber] = month.split("-").map(Number);
  return [year, monthNumber - 1];
}

function shiftMonth(month: string, delta: number): string {
  const [year, monthIndex] = parseMonth(month);
  return new Date(Date.UTC(year, monthIndex + delta, 1)).toISOString().slice(0, 7);
}

function monthLabel(month: string): string {
  const [year, monthIndex] = parseMonth(month);
  return new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric", timeZone: "UTC" }).format(
    Date.UTC(year, monthIndex, 1),
  );
}

function dayLabel(date: string): string {
  const [year, month, day] = date.split("-").map(Number);
  return new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" }).format(
    Date.UTC(year, month - 1, day),
  );
}

export default function UsageTab() {
  const thisMonth = currentMonth();
  const [month, setMonth] = useState(thisMonth);
  const [draft, setDraft] = useState(thisMonth);
  const report = useResource<UsageReport>(`/admin/usage?month=${month}`, "Couldn't load usage.");
  const inputId = useId();
  const draftValid = MONTH_PATTERN.test(draft) && draft <= thisMonth;

  function selectMonth(value: string) {
    setDraft(value);
    if (MONTH_PATTERN.test(value) && value <= thisMonth) setMonth(value);
  }

  return (
    <div className="admin-stack">
      <div className="admin-section-header">
        <div>
          <h2 className="admin-card-title">Usage in {monthLabel(month)}</h2>
          <p className="admin-card-subtitle">
            Calendar months in UTC. Only chat tokens count toward user budgets; embedding and vision tokens are shown for
            cost.
          </p>
        </div>
        <span className="spacer" />
        <div className="admin-month-picker">
          {report.loading && report.data ? <Spinner size={14} label="Loading usage" /> : null}
          <button
            type="button"
            className="btn btn-ghost btn-icon"
            aria-label="Previous month"
            onClick={() => selectMonth(shiftMonth(month, -1))}
          >
            <ChevronLeftIcon />
          </button>
          <label className="sr-only" htmlFor={inputId}>
            Month
          </label>
          <input
            id={inputId}
            type="month"
            className="input admin-month-input"
            value={draft}
            max={thisMonth}
            placeholder="YYYY-MM"
            aria-invalid={draftValid ? undefined : true}
            onChange={(e) => selectMonth(e.target.value)}
          />
          <button
            type="button"
            className="btn btn-ghost btn-icon"
            aria-label="Next month"
            disabled={month >= thisMonth}
            onClick={() => selectMonth(shiftMonth(month, 1))}
          >
            <ChevronRightIcon />
          </button>
        </div>
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
            headers={["User", "Requests", "Chat tokens", "Embedding tokens", "Vision tokens", "Cost"]}
            rows={byUser.map((row) => ({
              key: String(row.user_id),
              cells: [
                <span className="admin-user-name admin-nowrap">{row.username}</span>,
                formatNumber(row.requests),
                formatNumber(row.chat_tokens),
                formatNumber(row.embedding_tokens),
                formatNumber(row.vision_tokens),
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
            headers={["Day", "Chat tokens", "Embedding tokens", "Vision tokens", "Cost"]}
            rows={daily.map((day) => ({
              key: day.date,
              cells: [
                <time className="admin-nowrap" dateTime={day.date}>{dayLabel(day.date)}</time>,
                <DailyBar value={day.chat_tokens} peak={peakChatTokens} />,
                formatNumber(day.embedding_tokens),
                formatNumber(day.vision_tokens),
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
