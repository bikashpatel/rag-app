// The plan editor's figures, computed in the browser with the server's formulas (backend/app/services/billing/money.py;
// docs/briefs/plans-billing.md 3.3): the AI cost of a period against what the owner keeps, and the answers estimate.

import type { AdminModel, CostInputs, PlanCostEstimate, UserLimits } from "../../api/types";

/** The typical estimate: this share of every allowance. */
const TYPICAL_USE = 0.4;
/** A model's blended price: RAG answers are mostly prompt. */
const INPUT_SHARE = 0.85;
const TOKENS_PER_ANSWER = 6000;
const FILES_PROMPT_TOKENS = 1550;
const IMAGES_PROMPT_TOKENS = 1040;
/** Vision tokens of one simple scanned page (plans-billing-visual.md 4.4). */
const TOKENS_PER_SCANNED_PAGE = 3_000;
/** At or above this share of what the owner keeps, the estimate warns. */
export const HIGH_SHARE = 0.6;

/** Python's round(): halves go to the even neighbour, so the browser's paise match the server's. */
function roundHalfEven(value: number): number {
  const floor = Math.floor(value);
  const diff = value - floor;
  if (diff > 0.5) return floor + 1;
  if (diff < 0.5) return floor;
  return floor % 2 === 0 ? floor : floor + 1;
}

/** What one period costs in AI calls with every allowance used up (worst case) and with 40% used (typical). */
export function costEstimate(
  pricePaise: number,
  limits: UserLimits,
  images: boolean,
  inputs: CostInputs,
): PlanCostEstimate {
  const kept = Math.floor((pricePaise * (10_000 - inputs.fee_bp)) / 10_000);
  const worstUsd =
    (limits.monthly_token_limit * inputs.standard_usd_per_mtok) / 1_000_000 +
    (limits.vision_monthly_token_limit * inputs.vision_usd_per_mtok) / 1_000_000 +
    (images ? limits.monthly_image_limit * inputs.image_usd : 0);
  const worst = roundHalfEven(worstUsd * inputs.usd_inr * 100);
  const typical = roundHalfEven(worstUsd * TYPICAL_USE * inputs.usd_inr * 100);
  const allowances: (keyof UserLimits)[] = [
    "monthly_token_limit",
    ...(images ? (["monthly_image_limit"] as const) : []),
    "vision_monthly_token_limit",
  ];
  return {
    kept_paise: kept,
    worst_case_paise: worst,
    worst_share: kept ? worst / kept : 0,
    typical_paise: typical,
    typical_share: kept ? typical / kept : 0,
    unlimited: allowances.filter((name) => limits[name] === 0),
  };
}

/** USD per million tokens of a typical answer (85% input, 15% output); null without a price. */
export function blendedPrice(model: Pick<AdminModel, "input_cost_per_mtok" | "output_cost_per_mtok">): number | null {
  const { input_cost_per_mtok: input, output_cost_per_mtok: output } = model;
  if (input === null || output === null) return null;
  const price = INPUT_SHARE * input + (1 - INPUT_SHARE) * output;
  return price > 0 ? price : null;
}

/** About how many answers an allowance gives with a model, rounded down to two significant figures; null when the
 * allowance is unlimited or the model has no price. */
export function answersFor(
  allowance: number,
  blended: number | null,
  features: { files: boolean; images: boolean },
  inputs: CostInputs,
): number | null {
  if (allowance <= 0 || blended === null) return null;
  const tokens =
    TOKENS_PER_ANSWER + (features.files ? FILES_PROMPT_TOKENS : 0) + (features.images ? IMAGES_PROMPT_TOKENS : 0);
  const count = (allowance * inputs.standard_usd_per_mtok) / blended / tokens;
  if (count < 1) return 0;
  const unit = 10 ** Math.max(0, Math.floor(Math.log10(count)) - 1);
  return Math.floor(count / unit + 1e-9) * unit;
}

/** Scanned pages a vision allowance reads, to two significant figures, at least 1. */
export function scannedPages(tokens: number): number {
  const pages = tokens / TOKENS_PER_SCANNED_PAGE;
  const scale = 10 ** Math.max(0, Math.floor(Math.log10(Math.max(pages, 1))) - 1);
  return Math.max(1, Math.round(pages / scale) * scale);
}

/**
 * The model a plan's new chats start with, as the server resolves it: the plan's default, else the server's default
 * chat model when the plan offers it, else the plan's first model.
 */
export function planDefaultModel(
  chatModels: AdminModel[],
  modelIds: number[] | null,
  defaultId: number | null,
): AdminModel | null {
  const offered = modelIds === null ? chatModels : chatModels.filter((model) => modelIds.includes(model.id));
  return (
    offered.find((model) => model.id === defaultId) ?? offered.find((model) => model.is_default) ?? offered[0] ?? null
  );
}
