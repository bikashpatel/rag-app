import { useId } from "react";
import { formatNumber } from "../../lib/format";
import { AlertCircleIcon, AlertIcon } from "./icons";

type MeterProps = {
  label: string;
  used: number;
  /** 0 = unlimited: no bar, the value reads "<used> · Unlimited". */
  limit: number;
  /** How the visible numbers are written ("412K"); the accessible value always has the exact numbers. */
  format: (n: number) => string;
  /** What is counted, for the accessible value: "412,380 of 1,000,000 tokens". */
  noun: string;
  /** The word after the value when there is no limit (Settings → Usage says "Unlimited (admin)"). */
  unlimitedLabel?: string;
  className?: string;
};

type MeterState = { tone: "warning" | "danger"; words: string };

/** 90% and up: "Almost used up"; 100% and up: "Limit reached" (with an icon, never colour alone). */
export function meterState(used: number, limit: number): MeterState | null {
  if (limit <= 0) return null;
  if (used >= limit) return { tone: "danger", words: "Limit reached" };
  if (used >= limit * 0.9) return { tone: "warning", words: "Almost used up" };
  return null;
}

/** Usage against a limit: the label and the numbers on one row, a bar under them (role="meter"). */
export default function Meter({ label, used, limit, format, noun, unlimitedLabel = "Unlimited", className }: MeterProps) {
  const labelId = useId();
  const state = meterState(used, limit);
  const ratio = limit > 0 ? Math.min(Math.max(used / limit, 0), 1) : 0;
  const valueText = `${formatNumber(used)} of ${formatNumber(limit)} ${noun}${state ? `. ${state.words}` : ""}`;
  return (
    <div className={className ? `ui-meter-block ${className}` : "ui-meter-block"}>
      <div className="ui-meter-row">
        <span id={labelId} className="ui-meter-label">
          {label}
        </span>
        <span className="ui-meter-value">
          {state ? (
            <span className={`ui-meter-state ui-meter-state-${state.tone}`}>
              {state.tone === "danger" ? <AlertCircleIcon size={13} /> : <AlertIcon size={13} />}
              {state.words}
            </span>
          ) : null}
          {limit > 0 ? (
            <span>
              {format(used)} <small>/ {format(limit)}</small>
            </span>
          ) : (
            <span>
              {format(used)} <small>· {unlimitedLabel}</small>
            </span>
          )}
        </span>
      </div>
      {limit > 0 ? (
        <div
          className={state ? `ui-meter ui-meter-${state.tone}` : "ui-meter"}
          role="meter"
          aria-labelledby={labelId}
          aria-valuemin={0}
          aria-valuemax={limit}
          aria-valuenow={Math.min(used, limit)}
          aria-valuetext={valueText}
        >
          <span style={{ width: `${ratio * 100}%` }} />
        </div>
      ) : null}
    </div>
  );
}
