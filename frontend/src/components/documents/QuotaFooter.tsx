import type { DocumentConfig } from "../../api/types";
import { formatBytes, formatLimit, formatNumber } from "../../lib/format";
import { BYTES_PER_MB } from "./fileTypes";

export default function QuotaFooter({ config }: { config: DocumentConfig }) {
  return (
    <footer className="docs-footer" aria-label="Your document quota">
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
};

function QuotaMeter({ label, used, limit, format }: QuotaMeterProps) {
  const ratio = limit > 0 ? Math.min(1, used / limit) : 0;
  const tone = ratio >= 0.95 ? " docs-meter-fill-danger" : ratio >= 0.8 ? " docs-meter-fill-warning" : "";
  return (
    <div className="docs-quota">
      <div className="docs-quota-label">
        <span>{label}</span>
        <span className="docs-quota-value">
          {format(used)} / {formatLimit(limit, format)}
        </span>
      </div>
      {limit > 0 ? (
        <div className="docs-meter" aria-hidden="true">
          <div className={`docs-meter-fill${tone}`} style={{ width: `${ratio * 100}%` }} />
        </div>
      ) : null}
    </div>
  );
}
