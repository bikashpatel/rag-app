import { useId, useState } from "react";
import Spinner from "../ui/Spinner";
import { ChevronLeftIcon, ChevronRightIcon } from "./icons";

const MONTH_PATTERN = /^\d{4}-(0[1-9]|1[0-2])$/;

/** Budgets and reports use UTC calendar months. */
export function currentMonth(): string {
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

export function monthLabel(month: string): string {
  const [year, monthIndex] = parseMonth(month);
  return new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric", timeZone: "UTC" }).format(
    Date.UTC(year, monthIndex, 1),
  );
}

type MonthPickerProps = {
  month: string;
  onChange: (month: string) => void;
  /** Shows a spinner while the selected month's data loads over the previous month's. */
  loading?: boolean;
  loadingLabel?: string;
};

/** Previous/next buttons around a month input; months after the current one can't be chosen. */
export default function MonthPicker({ month, onChange, loading = false, loadingLabel = "Loading" }: MonthPickerProps) {
  const thisMonth = currentMonth();
  const [draft, setDraft] = useState(month);
  const inputId = useId();
  const draftValid = MONTH_PATTERN.test(draft) && draft <= thisMonth;

  function select(value: string) {
    setDraft(value);
    if (MONTH_PATTERN.test(value) && value <= thisMonth) onChange(value);
  }

  return (
    <div className="admin-month-picker">
      {loading ? <Spinner size={14} label={loadingLabel} /> : null}
      <button
        type="button"
        className="btn btn-ghost btn-icon"
        aria-label="Previous month"
        onClick={() => select(shiftMonth(month, -1))}
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
        onChange={(e) => select(e.target.value)}
      />
      <button
        type="button"
        className="btn btn-ghost btn-icon"
        aria-label="Next month"
        disabled={month >= thisMonth}
        onClick={() => select(shiftMonth(month, 1))}
      >
        <ChevronRightIcon />
      </button>
    </div>
  );
}
