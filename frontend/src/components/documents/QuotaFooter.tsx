import type { DocumentConfig } from "../../api/types";
import { formatBytes, formatLimit, formatMegabytes, formatNumber } from "../../lib/format";
import { AlertCircleIcon, AlertIcon } from "../ui/icons";
import { meterState } from "../ui/Meter";
import { BYTES_PER_MB } from "./fileTypes";

export default function QuotaFooter({ config }: { config: DocumentConfig }) {
  return (
    <footer className="docs-footer" role="group" aria-label="Your document quota">
      <QuotaMeter
        label="Documents"
        used={config.document_count}
        limit={config.max_documents}
        format={formatNumber}
      />
      <QuotaMeter
        label="Storage"
        used={config.storage_bytes}
        limit={config.max_storage_mb * BYTES_PER_MB}
        format={formatBytes}
        formatLimitValue={() => formatMegabytes(config.max_storage_mb)}
      />
    </footer>
  );
}

type QuotaMeterProps = {
  label: string;
  used: number;
  /** 0 = unlimited: shown as "Unlimited" without a bar. */
  limit: number;
  format: (value: number) => string;
  /** The limit's own words when they differ from the amount's (storage: "3,000 MB", as the plan says it). */
  formatLimitValue?: (limit: number) => string;
};

/**
 * The label and "used / limit" on one row, a 4 px bar under them (decorative: the row says it in words). From 90% a
 * caption under the bar adds a warning icon and "Almost used up", at 100% an alert icon and "Limit reached" (never
 * colour alone); under the bar, so the two columns' rows and bars stay aligned in the narrow footer.
 */
function QuotaMeter({ label, used, limit, format, formatLimitValue = format }: QuotaMeterProps) {
  const ratio = limit > 0 ? Math.min(1, used / limit) : 0;
  const state = meterState(used, limit);
  return (
    <div className="docs-quota">
      <div className="ui-meter-row">
        <span>{label}</span>
        <span className="ui-meter-value">
          <span>
            {format(used)} <small>/ {formatLimit(limit, formatLimitValue)}</small>
          </span>
        </span>
      </div>
      {limit > 0 ? (
        <div className={state ? `ui-meter docs-meter ui-meter-${state.tone}` : "ui-meter docs-meter"} aria-hidden="true">
          <span style={{ width: `${ratio * 100}%` }} />
        </div>
      ) : null}
      {state ? (
        <span className={`ui-meter-state ui-meter-state-${state.tone} docs-quota-state`}>
          {state.tone === "danger" ? <AlertCircleIcon size={13} /> : <AlertIcon size={13} />}
          {state.words}
        </span>
      ) : null}
    </div>
  );
}
