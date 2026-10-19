import { useId, useLayoutEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import type { MyUsage } from "../../api/types";
import { formatCompact, formatCost, formatMonthName, formatNumber, formatUtcDay } from "../../lib/format";
import { ClockIcon, RefreshIcon } from "../ui/icons";
import SegmentedControl from "../ui/SegmentedControl";

type Day = MyUsage["daily"][number];
type Metric = "chat_tokens" | "images" | "cost_usd";

const METRICS: readonly { value: Metric; label: string }[] = [
  { value: "chat_tokens", label: "Chat tokens" },
  { value: "images", label: "Images" },
  { value: "cost_usd", label: "Cost" },
];

// Geometry in CSS pixels (the viewBox is the measured size, so one unit is one pixel and labels stay 12px).
const HEIGHT = 176;
const LEFT = 40; // y labels sit right-aligned at x 32
const RIGHT = 8;
const TOP = 20;
const BASE = 150;
const LABEL_Y = 172;
const BAR_SHARE = 0.62;
/** Under this plot width the x axis shows only the 1st, today and the last day. */
const NARROW = 420;
const LOCALE = "en-GB";

type DailyUsageChartProps = {
  month: string; // "YYYY-MM"
  daily: MyUsage["daily"];
  rateLimitPerMin: number;
  resetsAt: string;
};

function daysInMonth(month: string): number {
  const [year, monthNumber] = month.split("-").map(Number);
  const days = new Date(Date.UTC(year, monthNumber, 0)).getUTCDate();
  return Number.isFinite(days) && days >= 28 ? days : 31;
}

/** A round step for two intervals over `max` (0, step, 2 × step); whole numbers for counts. */
function niceStep(max: number, whole: boolean): number {
  const raw = whole ? Math.max(max / 2, 1) : max / 2;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  for (const factor of [1, 2, 2.5, 5, 10]) {
    const step = factor * magnitude;
    if (whole && !Number.isInteger(step)) continue;
    if (step >= raw - 1e-12) return step;
  }
  return 10 * magnitude;
}

function costLabel(value: number): string {
  if (value === 0) return "$0";
  if (value >= 0.01) return `$${value.toFixed(2)}`;
  return `$${Number(value.toPrecision(2))}`;
}

function axisLabel(metric: Metric, value: number): string {
  if (metric === "cost_usd") return costLabel(value);
  return value === 0 ? "0" : formatCompact(value);
}

function valueText(metric: Metric, value: number): string {
  if (metric === "cost_usd") return formatCost(value);
  return formatCompact(value);
}

function plural(count: number, one: string, many: string): string {
  return `${formatNumber(count)} ${count === 1 ? one : many}`;
}

function readout(day: Day): { date: string; rest: string } {
  return {
    date: formatUtcDay(day.date, "long"),
    rest: `${plural(day.chat_tokens, "chat token", "chat tokens")} · ${plural(day.images, "image", "images")} · ${formatCost(day.cost_usd)}`,
  };
}

/** "Resets 1 Nov, 00:00 UTC". */
function resetLabel(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "Resets on the 1st, 00:00 UTC";
  const date = new Intl.DateTimeFormat(LOCALE, { day: "numeric", month: "short", timeZone: "UTC" }).format(d);
  const time = new Intl.DateTimeFormat(LOCALE, { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: "UTC" }).format(d);
  return `Resets ${date}, ${time} UTC`;
}

/** The rounded-top bar from the baseline. */
function barPath(x: number, width: number, height: number): string {
  const r = Math.min(4, width / 2, height);
  const top = BASE - height;
  return `M${x} ${BASE}V${top + r}a${r} ${r} 0 0 1 ${r} ${-r}h${width - 2 * r}a${r} ${r} 0 0 1 ${r} ${r}V${BASE}z`;
}

/** Estimated label width at 12px, for keeping x labels apart. */
function labelWidth(text: string): number {
  return text.length * 7 + 4;
}

type XLabel = { index: number; text: string; strong: boolean };

/** The 1st (with the month), every 7th day, today and the last day; never two labels on top of each other. */
function xLabels(days: number, today: number, slot: number, plotWidth: number, monthShort: string): XLabel[] {
  const last = days - 1;
  const wanted: XLabel[] = [{ index: today, text: "Today", strong: true }];
  const others = plotWidth < NARROW ? [0, last] : [0, last, 7, 14, 21, 28];
  for (const index of others) {
    if (index > last || index === today) continue;
    wanted.push({ index, text: index === 0 ? `1 ${monthShort}` : String(index + 1), strong: false });
  }
  const kept: XLabel[] = [];
  for (const label of wanted) {
    const clear = kept.every(
      (other) => Math.abs(other.index - label.index) * slot >= (labelWidth(other.text) + labelWidth(label.text)) / 2 + 6,
    );
    if (clear && (label.strong || Math.abs(label.index - today) > 2)) kept.push(label);
  }
  return kept.sort((a, b) => a.index - b.index);
}

/**
 * Usage per day of the month as bars (inline SVG, no chart library), docs/briefs/ui-visual-design.md 6.23. The plot is
 * one tab stop (a labelled group): Left and Right move the day the readout shows; the table is the screen-reader path.
 */
export default function DailyUsageChart({ month, daily, rateLimitPerMin, resetsAt }: DailyUsageChartProps) {
  const id = useId();
  const plotRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState<number | null>(null);
  const [metric, setMetric] = useState<Metric>("chat_tokens");
  const todayIndex = Math.max(daily.length - 1, 0);
  const [selected, setSelected] = useState(todayIndex);
  const [hovered, setHovered] = useState<number | null>(null);
  const [spoken, setSpoken] = useState("");
  const [table, setTable] = useState(false);

  const empty = daily.every(
    (day) =>
      day.chat_tokens === 0 &&
      day.images === 0 &&
      day.cost_usd === 0 &&
      day.requests === 0 &&
      day.vision_tokens === 0 &&
      day.embedding_tokens === 0,
  );

  useLayoutEffect(() => {
    const plot = plotRef.current;
    if (!plot) return;
    const measure = (value: number) => setWidth(value > 0 ? Math.round(value) : null);
    measure(plot.getBoundingClientRect().width);
    const observer = new ResizeObserver((entries) => measure(entries[0].contentRect.width));
    observer.observe(plot);
    return () => observer.disconnect();
  }, [empty]);

  const days = daysInMonth(month);
  const monthName = formatMonthName(month);
  const monthShort = monthName.slice(0, 3);
  const metricLabel = METRICS.find((option) => option.value === metric)?.label ?? "";
  const values = daily.map((day) => day[metric]);
  const max = Math.max(0, ...values);
  const total = values.reduce((sum, value) => sum + value, 0);
  const peakIndex = max > 0 ? values.indexOf(max) : -1;
  const step = max > 0 ? niceStep(max, metric === "images") : 0;
  const yMax = step * 2;
  const shownIndex = Math.min(hovered ?? selected, todayIndex);
  const shownDay = daily[shownIndex];

  const summary =
    total > 0
      ? `${metricLabel} per day, ${monthName}: ${valueText(metric, total)} in total, most on ${formatUtcDay(daily[peakIndex].date, "long").replace(/^\S+ /, "")} with ${valueText(metric, max)}.`
      : `${metricLabel} per day, ${monthName}: none yet.`;

  function select(index: number) {
    const next = Math.min(Math.max(index, 0), todayIndex);
    setSelected(next);
    setHovered(null);
    const day = daily[next];
    if (day) {
      const text = readout(day);
      setSpoken(`${text.date} · ${text.rest}`);
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const moves: Record<string, number> = {
      ArrowLeft: selected - 1,
      ArrowRight: selected + 1,
      Home: 0,
      End: todayIndex,
    };
    if (!(event.key in moves)) return;
    event.preventDefault();
    select(moves[event.key]);
  }

  function dayAt(event: PointerEvent<HTMLDivElement>): number | null {
    if (width === null) return null;
    const rect = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const slot = (width - LEFT - RIGHT) / days;
    if (x < LEFT - 4 || x > width - RIGHT + 4) return null;
    return Math.min(Math.max(Math.floor((x - LEFT) / slot), 0), todayIndex);
  }

  let plot = null;
  if (width !== null && !empty) {
    const plotWidth = width - LEFT - RIGHT;
    const slot = plotWidth / days;
    const barWidth = Math.max(slot * BAR_SHARE, 1);
    const scale = yMax > 0 ? (BASE - TOP) / yMax : 0;
    const centre = (index: number) => LEFT + index * slot + slot / 2;
    const gridValues = yMax > 0 ? [0, step, yMax] : [0];
    const labels = xLabels(days, todayIndex, slot, plotWidth, monthShort);
    plot = (
      <svg viewBox={`0 0 ${width} ${HEIGHT}`} width={width} height={HEIGHT} aria-hidden="true" focusable="false">
        {gridValues.map((value) => {
          const y = BASE - value * scale;
          return (
            <g key={value}>
              <line className="settings-chart-grid" x1={LEFT} x2={width - RIGHT} y1={y + 0.5} y2={y + 0.5} />
              <text className="settings-chart-axis" x={32} y={y + 4} textAnchor="end">
                {axisLabel(metric, value)}
              </text>
            </g>
          );
        })}
        <rect
          className="settings-chart-slot"
          x={LEFT + shownIndex * slot + 1}
          y={TOP - 8}
          width={Math.max(slot - 2, 1)}
          height={BASE - TOP + 8}
          rx={4}
        />
        {Array.from({ length: days }, (_, index) => {
          const x = centre(index) - barWidth / 2;
          if (index > todayIndex) {
            return <rect key={index} className="settings-chart-future" x={x} y={BASE - 2} width={barWidth} height={2} rx={1} />;
          }
          const value = values[index] ?? 0;
          if (value <= 0) return null;
          const height = Math.max(value * scale, 2);
          return (
            <path
              key={`${metric}-${index}`}
              className={index === shownIndex ? "settings-chart-bar settings-chart-bar-active" : "settings-chart-bar"}
              d={barPath(x, barWidth, height)}
            />
          );
        })}
        {peakIndex >= 0 ? (
          <text className="settings-chart-peak" x={centre(peakIndex)} y={BASE - max * scale - 6} textAnchor="middle">
            {valueText(metric, max)}
          </text>
        ) : (
          <text className="settings-chart-none" x={LEFT + plotWidth / 2} y={(TOP + BASE) / 2} textAnchor="middle">
            No {metricLabel.toLowerCase()} this month
          </text>
        )}
        <circle className="settings-chart-today" cx={centre(todayIndex)} cy={157} r={2} />
        {labels.map((label) => {
          const half = labelWidth(label.text) / 2;
          let x = centre(label.index);
          let anchor: "middle" | "start" | "end" = "middle";
          if (x + half > width) {
            x = width;
            anchor = "end";
          } else if (x - half < 0) {
            x = 0;
            anchor = "start";
          }
          return (
            <text
              key={label.index}
              className={label.strong ? "settings-chart-axis settings-chart-axis-strong" : "settings-chart-axis"}
              x={x}
              y={LABEL_Y}
              textAnchor={anchor}
            >
              {label.text}
            </text>
          );
        })}
      </svg>
    );
  }

  const shown = shownDay ? readout(shownDay) : null;
  return (
    <figure className="settings-chart" aria-labelledby={`${id}-title`}>
      <div className="settings-chart-head">
        <h3 id={`${id}-title`}>Per day</h3>
        {empty ? null : (
          <SegmentedControl label="Chart metric" size="sm" value={metric} options={METRICS} onChange={setMetric} />
        )}
      </div>
      {empty ? (
        <p className="settings-chart-empty">No usage yet this month.</p>
      ) : (
        <>
          <p className="settings-chart-readout" aria-hidden="true">
            {shown ? (
              <>
                <b>{shown.date}</b> · {shown.rest}
              </>
            ) : null}
          </p>
          <p className="sr-only" aria-live="polite">
            {spoken}
          </p>
          <div
            ref={plotRef}
            className="settings-chart-plot"
            role="group"
            aria-roledescription="chart"
            aria-label={summary}
            aria-describedby={`${id}-keys`}
            tabIndex={0}
            onKeyDown={onKeyDown}
            onPointerMove={(event) => {
              if (event.pointerType === "mouse") setHovered(dayAt(event));
            }}
            onPointerDown={(event) => {
              const index = dayAt(event);
              if (index !== null) {
                setSelected(index);
                setHovered(null);
              }
            }}
            onPointerLeave={() => setHovered(null)}
          >
            {plot ?? <span className="ui-skeleton" aria-hidden="true" />}
          </div>
          <span id={`${id}-keys`} className="sr-only">
            Left and right arrow keys move between days.
          </span>
        </>
      )}
      <div className="settings-chart-foot">
        <span>
          <ClockIcon size={15} />
          {rateLimitPerMin > 0 ? `Up to ${plural(rateLimitPerMin, "request", "requests")} a minute` : "No request limit"}
        </span>
        <span>
          <RefreshIcon size={15} />
          {resetLabel(resetsAt)}
        </span>
        {empty ? null : (
          <button
            type="button"
            className="btn btn-ghost btn-xs"
            aria-expanded={table}
            aria-controls={table ? `${id}-table` : undefined}
            onClick={() => setTable((value) => !value)}
          >
            {table ? "Hide table" : "Show as table"}
          </button>
        )}
      </div>
      {table && !empty ? (
        <div className="table-wrap settings-chart-table" id={`${id}-table`}>
          <table className="table">
            <caption className="sr-only">Usage per day, {monthName} (UTC days)</caption>
            <thead>
              <tr>
                <th scope="col">Day</th>
                <th scope="col" className="num">
                  Chat tokens
                </th>
                <th scope="col" className="num">
                  Answers
                </th>
                <th scope="col" className="num">
                  Images
                </th>
                <th scope="col" className="num">
                  Vision tokens
                </th>
                <th scope="col" className="num">
                  Embedding tokens
                </th>
                <th scope="col" className="num">
                  Cost
                </th>
              </tr>
            </thead>
            <tbody>
              {daily.map((day) => (
                <tr key={day.date}>
                  <th scope="row">{formatUtcDay(day.date, "long")}</th>
                  <td className="num">{formatNumber(day.chat_tokens)}</td>
                  <td className="num">{formatNumber(day.requests)}</td>
                  <td className="num">{formatNumber(day.images)}</td>
                  <td className="num">{formatNumber(day.vision_tokens)}</td>
                  <td className="num">{formatNumber(day.embedding_tokens)}</td>
                  <td className="num">{formatCost(day.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </figure>
  );
}
