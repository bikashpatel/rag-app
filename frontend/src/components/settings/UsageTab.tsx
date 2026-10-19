import { useId, type ReactNode } from "react";
import { getMyUsage } from "../../api/client";
import type { Me, MyUsage, UsageAmount } from "../../api/types";
import { daysUntil, formatBytes, formatCompact, formatCost, formatMonthName, formatNumber, formatResetMoment } from "../../lib/format";
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
};

/** One limit as a tile: the serif figure, a meter, and what is left (with words and an icon at 90% and 100%). */
function UsageTile({ label, amount, noun, admin, note, unlimitedNote }: TileProps) {
  const labelId = useId();
  const { used, limit } = amount;
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

function UsageReport({ usage, admin }: { usage: MyUsage; admin: boolean }) {
  const days = daysUntil(usage.resets_at);
  const docs = usage.documents;
  const cost = usage.cost_usd;
  return (
    <>
      <p className="settings-lede">
        Limits start again on <b>{formatResetMoment(usage.resets_at)}</b> (
        {days === 0 ? "today" : `in ${plural(days, "day", "days")}`}).{" "}
        {admin ? "As the administrator you have no limits." : "Your administrator sets them."}
      </p>

      <div className="settings-meters">
        <UsageTile
          label="Chat tokens"
          amount={usage.chat_tokens}
          noun="chat tokens"
          admin={admin}
          unlimitedNote="No monthly budget"
        />
        <UsageTile label="Images" amount={usage.images} noun="images" admin={admin} unlimitedNote="No monthly limit" />
        <UsageTile
          label="Vision tokens"
          amount={usage.vision_tokens}
          noun="vision tokens"
          admin={admin}
          note="Reading scanned PDF pages"
          unlimitedNote="Reading scanned PDF pages"
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
            <small>of {formatBytes(docs.max_storage_mb * 1024 * 1024)}</small>
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
        month={usage.month}
        daily={usage.daily}
        rateLimitPerMin={usage.rate_limit_per_min}
        resetsAt={usage.resets_at}
      />

      <h3 className="settings-sub">By model</h3>
      {usage.by_model.length === 0 ? (
        <p className="settings-empty-line">Nothing used yet.</p>
      ) : (
        <div className="table-wrap settings-models-table">
          <table className="table">
            <caption className="sr-only">Usage by model, {formatMonthName(usage.month)}</caption>
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

/** Settings → Usage: this UTC month against the limits, the cost estimate, a daily chart and the models used. */
export default function UsageTab({ user, onClose }: { user: Me; onClose: () => void }) {
  const usage = useLoad((signal) => getMyUsage(signal), "Couldn't load your usage.");
  const refreshing = usage.loading && usage.data !== null;
  let body: ReactNode;
  if (usage.data) body = <UsageReport usage={usage.data} admin={user.is_admin} />;
  else if (usage.error) body = <PanelError message={usage.error} onRetry={usage.reload} />;
  else body = <UsageSkeleton />;
  return (
    <>
      <PanelHead
        title="Usage"
        note={usage.data ? `${formatMonthName(usage.data.month)} (UTC)` : null}
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
