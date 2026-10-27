import { useId, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { getMyUsage } from "../../api/client";
import type { Me, MyUsage, UsageAmount } from "../../api/types";
import { formatIstRange } from "../../lib/billing";
import {
  daysUntil,
  formatBytes,
  formatCompact,
  formatCost,
  formatMegabytes,
  formatMonthName,
  formatNumber,
  formatResetMoment,
} from "../../lib/format";
import { isStacked, istDayMonth, istDaysUntil, istLongDay, lastDayOf } from "../billing/planText";
import { AlertCircleIcon, AlertIcon, RefreshIcon } from "../ui/icons";
import { meterState } from "../ui/Meter";
import DailyUsageChart from "./DailyUsageChart";
import { PanelError, PanelHead, plural, UsageSkeleton } from "./PanelParts";
import { useLoad } from "./useLoad";

const KIND_LABEL: Record<MyUsage["by_model"][number]["kind"], string> = {
  chat: "Chat",
  embedding: "Embedding",
  vision: "Vision",
  image: "Image",
};

/** The tile's big figure: three significant digits ("360K", "22.8K", "1M"); the exact numbers are in the meter. */
function formatFigure(n: number): string {
  return new Intl.NumberFormat(undefined, { notation: "compact", maximumSignificantDigits: 3 }).format(n);
}

type TileProps = {
  label: string;
  amount: UsageAmount;
  /** For the accessible value: "412,380 of 1,000,000 chat tokens". */
  noun: string;
  admin: boolean;
  /** The foot line when nothing more pressing is to be said ("Reading scanned PDF pages"). */
  note?: string;
  /** The foot line without a limit. */
  unlimitedNote: string;
  /** No access: the count only, without a limit or meter. */
  lapsed?: boolean;
  /** A last line under the foot ("Counted at each model's rate."). */
  hint?: string;
};

/** One limit as a tile: the serif figure, a meter, and what is left (with words and an icon at 90% and 100%). */
function UsageTile({ label, amount, noun, admin, note, unlimitedNote, lapsed = false, hint }: TileProps) {
  const labelId = useId();
  const { used, limit } = amount;
  if (lapsed) {
    return (
      <div className="settings-tile">
        <div className="settings-tile-label">{label}</div>
        <div className="settings-tile-value">{formatFigure(used)}</div>
        <span className="sr-only">
          {formatNumber(used)} {noun}.
        </span>
        <span className="settings-tile-foot">No active plan</span>
        {hint ? <span className="settings-tile-foot">{hint}</span> : null}
      </div>
    );
  }
  const state = meterState(used, limit);
  const ratio = limit > 0 ? Math.min(Math.max(used / limit, 0), 1) : 0;
  let foot: ReactNode;
  if (state?.tone === "danger") {
    foot = (
      <span className="settings-tile-foot settings-tile-danger">
        <AlertCircleIcon size={13} />
        {state.words}
      </span>
    );
  } else if (state) {
    foot = (
      <span className="settings-tile-foot settings-tile-warning">
        <AlertIcon size={13} />
        {state.words} · {formatNumber(limit - used)} left
      </span>
    );
  } else if (limit > 0) {
    foot = <span className="settings-tile-foot">{note ?? `${formatNumber(limit - used)} left`}</span>;
  } else {
    foot = <span className="settings-tile-foot">{note ?? unlimitedNote}</span>;
  }
  return (
    <div className="settings-tile">
      <div className="settings-tile-label" id={labelId}>
        {label}
      </div>
      <div className="settings-tile-value">
        {formatFigure(used)}{" "}
        <span>{limit > 0 ? `/ ${formatFigure(limit)}` : admin ? "· Unlimited (admin)" : "· Unlimited"}</span>
      </div>
      {limit > 0 ? (
        <div
          className={state ? `ui-meter ui-meter-${state.tone}` : "ui-meter"}
          role="meter"
          aria-labelledby={labelId}
          aria-valuemin={0}
          aria-valuemax={limit}
          aria-valuenow={Math.min(used, limit)}
          aria-valuetext={`${formatNumber(used)} of ${formatNumber(limit)} ${noun}${state ? `. ${state.words}` : ""}`}
        >
          <span style={{ width: `${ratio * 100}%` }} />
        </div>
      ) : (
        <span className="sr-only">
          {formatNumber(used)} {noun}, unlimited.
        </span>
      )}
      {foot}
      {hint ? <span className="settings-tile-foot">{hint}</span> : null}
    </div>
  );
}

function Fact({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div>
      <dt>{term}</dt>
      <dd>{children}</dd>
    </div>
  );
}

/** "(in 12 days)", "(tomorrow)", "(today)". */
function inDays(days: number): string {
  if (days <= 0) return "today";
  return days === 1 ? "tomorrow" : `in ${plural(days, "day", "days")}`;
}

/** The allowance window's kind: a paid or dated period, the calendar month (an open-ended plan, the admin), none. */
type Window = "period" | "month" | "lapsed" | "admin";

function windowOf(user: Me): Window {
  if (user.is_admin) return "admin";
  if (!user.plan.access) return "lapsed";
  return user.plan.open_ended || user.plan.period_start === null ? "month" : "period";
}

/** When the allowances start again, or end with the plan (visual 8.4); the admin's monthly reset as before. */
function Lede({ user, usage, kind }: { user: Me; usage: MyUsage; kind: Window }) {
  if (kind === "admin") {
    const days = daysUntil(usage.resets_at);
    return (
      <p className="settings-lede">
        Limits start again on <b>{formatResetMoment(usage.resets_at)}</b> ({inDays(days)}). As the administrator you have
        no limits.
      </p>
    );
  }
  if (kind === "lapsed") {
    return (
      <p className="settings-lede">
        No active plan. <Link to="/plans">Choose a plan</Link> to start again.
      </p>
    );
  }
  const followed = kind === "month" || isStacked(user.plan) || user.plan.next_plan !== null;
  if (followed) {
    return (
      <p className="settings-lede">
        Your allowances start again on <b>{istLongDay(usage.resets_at)}</b> ({inDays(istDaysUntil(usage.resets_at))})
        {kind === "period" ? " with your next period" : ""}.
      </p>
    );
  }
  const lastDay = lastDayOf(usage.resets_at);
  return (
    <p className="settings-lede">
      Your allowances end on <b>{istLongDay(lastDay)}</b> ({inDays(istDaysUntil(lastDay))}) with your plan.
    </p>
  );
}

/** "Resets 1 Nov, 00:00 UTC" (the month), "Starts again 8 Nov" or "Ends 7 Nov" (a period). */
function resetText(usage: MyUsage, kind: Window, followed: boolean): string {
  if (kind === "period") {
    return followed ? `Starts again ${istDayMonth(usage.resets_at)}` : `Ends ${istDayMonth(lastDayOf(usage.resets_at))}`;
  }
  const d = new Date(usage.resets_at);
  if (Number.isNaN(d.getTime())) return "Resets on the 1st, 00:00 UTC";
  const date = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "UTC" }).format(d);
  return `Resets ${date}, 00:00 UTC`;
}

/** The header note: "This period: 8 Oct to 7 Nov 2026", or "October 2026 (UTC)". */
function windowNote(usage: MyUsage, kind: Window): string {
  return kind === "period"
    ? `This period: ${formatIstRange(usage.period_start, usage.resets_at)}`
    : `${formatMonthName(usage.month)} (UTC)`;
}

function UsageReport({ user, usage }: { user: Me; usage: MyUsage }) {
  const admin = user.is_admin;
  const kind = windowOf(user);
  const lapsed = kind === "lapsed";
  const followed = isStacked(user.plan) || user.plan.next_plan !== null;
  const unlimited = admin ? "No monthly budget" : "No limit";
  const docs = usage.documents;
  const cost = usage.cost_usd;
  return (
    <>
      <Lede user={user} usage={usage} kind={kind} />

      <div className="settings-meters">
        <UsageTile
          label="Chat allowance"
          amount={usage.chat_allowance}
          noun="standard tokens"
          admin={admin}
          unlimitedNote={unlimited}
          lapsed={lapsed}
          hint={admin ? undefined : "Counted at each model's rate."}
        />
        <UsageTile
          label="Images"
          amount={usage.images}
          noun="images"
          admin={admin}
          unlimitedNote={admin ? "No monthly limit" : unlimited}
          lapsed={lapsed}
        />
        <UsageTile
          label="Vision tokens"
          amount={usage.vision_tokens}
          noun="vision tokens"
          admin={admin}
          note="Reading scanned PDF pages"
          unlimitedNote="Reading scanned PDF pages"
          lapsed={lapsed}
        />
      </div>

      <dl className="settings-usage-facts">
        <Fact term="Chat answers">{formatNumber(usage.requests)}</Fact>
        <Fact term="Requests per minute">
          {usage.rate_limit_per_min > 0 ? (
            <>
              {formatNumber(usage.rate_limit_per_min)} <small>per minute</small>
            </>
          ) : (
            "Unlimited"
          )}
        </Fact>
        <Fact term="Embedding tokens">
          {formatCompact(usage.embedding_tokens)} <small>not limited</small>
        </Fact>
        <Fact term="Documents">
          {formatNumber(docs.count)} {docs.max_documents > 0 ? <small>of {formatNumber(docs.max_documents)}</small> : <small>· Unlimited</small>}
        </Fact>
        <Fact term="Storage">
          {formatBytes(docs.storage_bytes)}{" "}
          {docs.max_storage_mb > 0 ? (
            <small>of {formatMegabytes(docs.max_storage_mb)}</small>
          ) : (
            <small>· Unlimited</small>
          )}
        </Fact>
        <Fact term="Largest upload">{formatNumber(docs.max_upload_mb)} MB</Fact>
      </dl>

      <div className="settings-cost">
        <div>
          <div className="settings-tile-label">Estimated cost</div>
          <div className="settings-cost-total">{formatCost(cost.total)}</div>
        </div>
        <ul className="settings-cost-parts" aria-label="Cost by kind">
          <li className="badge">Chat {formatCost(cost.chat)}</li>
          <li className="badge">Images {formatCost(cost.image)}</li>
          <li className="badge">Vision {formatCost(cost.vision)}</li>
          <li className="badge">Embeddings {formatCost(cost.embedding)}</li>
        </ul>
        <p className="settings-cost-note">An estimate from the models' prices; the provider's bill may differ.</p>
      </div>

      <DailyUsageChart
        start={usage.period_start}
        end={usage.resets_at}
        daily={usage.daily}
        rateLimitPerMin={usage.rate_limit_per_min}
        windowLabel={kind === "period" ? formatIstRange(usage.period_start, usage.resets_at) : formatMonthName(usage.month)}
        windowWord={admin ? "this month" : "this period"}
        resetText={resetText(usage, kind, followed)}
      />

      <h3 className="settings-sub">By model</h3>
      {usage.by_model.length === 0 ? (
        <p className="settings-empty-line">Nothing used yet.</p>
      ) : (
        <div className="table-wrap settings-models-table">
          <table className="table">
            <caption className="sr-only">
              Usage by model, {kind === "period" ? formatIstRange(usage.period_start, usage.resets_at) : formatMonthName(usage.month)}
            </caption>
            <thead>
              <tr>
                <th scope="col">Model</th>
                <th scope="col" className="settings-models-kind">
                  Kind
                </th>
                <th scope="col" className="num">
                  Tokens
                </th>
                <th scope="col" className="num">
                  Requests
                </th>
                <th scope="col" className="num">
                  Cost
                </th>
              </tr>
            </thead>
            <tbody>
              {usage.by_model.map((row) => (
                <tr key={`${row.model_name}-${row.kind}`}>
                  <td className="settings-models-name">
                    {row.model_name}
                    <small>{KIND_LABEL[row.kind]}</small>
                  </td>
                  <td className="settings-models-kind">{KIND_LABEL[row.kind]}</td>
                  <td className="num">{row.kind === "image" && row.tokens === 0 ? "—" : formatNumber(row.tokens)}</td>
                  <td className="num">{formatNumber(row.requests)}</td>
                  <td className="num">{formatCost(row.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

/**
 * Settings → Usage: the allowance window (this period, or the UTC month for an open-ended plan and the admin) against
 * the limits, the cost estimate, a daily chart and the models used.
 */
export default function UsageTab({ user, onClose }: { user: Me; onClose: () => void }) {
  const usage = useLoad((signal) => getMyUsage(signal), "Couldn't load your usage.");
  const refreshing = usage.loading && usage.data !== null;
  let body: ReactNode;
  if (usage.data) body = <UsageReport user={user} usage={usage.data} />;
  else if (usage.error) body = <PanelError message={usage.error} onRetry={usage.reload} />;
  else body = <UsageSkeleton />;
  return (
    <>
      <PanelHead
        title="Usage"
        note={usage.data ? windowNote(usage.data, windowOf(user)) : null}
        onClose={onClose}
        actions={
          <button
            type="button"
            className={refreshing ? "btn btn-ghost btn-icon settings-spin" : "btn btn-ghost btn-icon"}
            aria-label="Refresh usage"
            title="Refresh"
            aria-disabled={usage.loading || undefined}
            onClick={() => {
              if (!usage.loading) usage.reload();
            }}
          >
            <RefreshIcon />
          </button>
        }
      />
      <div className="settings-body" aria-busy={usage.loading}>
        {usage.data && usage.error ? (
          <p className="error-text settings-error" role="alert">
            <AlertIcon size={13} />
            {usage.error}
          </p>
        ) : null}
        {body}
      </div>
    </>
  );
}
