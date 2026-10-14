import type { ReactNode } from "react";

export type StatTone = "neutral" | "warning" | "danger";

type StatTileProps = {
  label: string;
  value: ReactNode;
  /** Secondary line under the value. */
  detail?: ReactNode;
  /** Full-precision value shown on hover when `value` is abbreviated. */
  title?: string;
  tone?: StatTone;
};

/** One term/definition pair; render inside `<dl className="admin-tiles">`. */
export default function StatTile({ label, value, detail, title, tone = "neutral" }: StatTileProps) {
  return (
    <div className={`admin-tile admin-tile-${tone}`}>
      <dt className="admin-tile-label">{label}</dt>
      <dd className="admin-tile-value" title={title}>
        {value}
      </dd>
      {detail ? <dd className="admin-tile-detail">{detail}</dd> : null}
    </div>
  );
}
