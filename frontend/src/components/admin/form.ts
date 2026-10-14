import { formatNumber } from "../../lib/format";

/** Upper bound of a Postgres INTEGER column. */
export const MAX_INT32 = 2_147_483_647;

const WHOLE = /^-?\d+$/;
const DECIMAL = /^-?(\d+(\.\d*)?|\.\d+)$/;

export type FieldErrors<K extends string> = Partial<Record<K, string>>;

/** Reads raw form strings into typed values, keeping the first error message per field. */
export class FormValidator<K extends string> {
  readonly errors: FieldErrors<K> = {};

  get valid(): boolean {
    return Object.keys(this.errors).length === 0;
  }

  fail(field: K, message: string): void {
    this.errors[field] ??= message;
  }

  /** Trimmed text of `min`..`max` characters. */
  text(field: K, raw: string, min: number, max: number): string {
    const value = raw.trim();
    if (!value && min > 0) this.fail(field, "This field is required.");
    else if (value.length < min) this.fail(field, `Use at least ${min} characters.`);
    else if (value.length > max) this.fail(field, `Use at most ${max} characters.`);
    return value;
  }

  /** Trimmed text, or null when blank. */
  optionalText(field: K, raw: string, max: number): string | null {
    const value = raw.trim();
    if (value.length > max) this.fail(field, `Use at most ${max} characters.`);
    return value || null;
  }

  integer(field: K, raw: string, min: number, max: number): number {
    return this.number(field, raw.trim(), WHOLE, "a whole number", min, max);
  }

  /** A number in [min, max], or null when blank. */
  optionalNumber(field: K, raw: string, min: number, max: number, integer = false): number | null {
    const text = raw.trim();
    if (!text) return null;
    return integer ? this.number(field, text, WHOLE, "a whole number", min, max) : this.number(field, text, DECIMAL, "a number", min, max);
  }

  private number(field: K, text: string, pattern: RegExp, kind: string, min: number, max: number): number {
    const value = Number(text);
    if (!pattern.test(text)) this.fail(field, `Enter ${kind}.`);
    else if (value < min) this.fail(field, `Must be at least ${formatNumber(min)}.`);
    else if (value > max) this.fail(field, `Must be at most ${formatNumber(max)}.`);
    return value;
  }
}

/** Number field state shown in an input: "" for null. */
export function numberText(value: number | null | undefined): string {
  return value === null || value === undefined ? "" : String(value);
}
