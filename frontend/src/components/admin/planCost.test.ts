import { describe, expect, it } from "vitest";
import type { CostInputs, UserLimits } from "../../api/types";
import { makeSeededAdminPlans, makeSeededChatModels } from "../../test/adminTabs";
import { makeBillingAdminConfig } from "../../test/billing";
import { makeAdminModel } from "../../test/fixtures";
import { answersFor, blendedPrice, costEstimate, planDefaultModel, scannedPages } from "./planCost";

// What the server sends (money.cost_inputs): 3.20 / 1_000_000 * 1_000_000 in floats.
const inputs: CostInputs = {
  ...makeBillingAdminConfig().estimate,
  standard_usd_per_mtok: (3.2 / 1_000_000) * 1_000_000,
};
const [starter, standard, professional] = makeSeededAdminPlans();
const models = makeSeededChatModels();
const model = (name: string) => models.find((m) => m.display_name === name)!;
const zero: UserLimits = {
  monthly_token_limit: 0,
  rate_limit_per_min: 0,
  max_upload_mb: 1,
  max_documents: 0,
  max_storage_mb: 0,
  monthly_image_limit: 0,
  vision_monthly_token_limit: 0,
};

describe("costEstimate", () => {
  it("matches the server's figures for the seeded plans (money.cost_estimate; plans-billing.md 3.3)", () => {
    const figures = [starter, standard, professional].map((plan) => {
      const estimate = costEstimate(plan.price_paise, plan.limits, plan.features.images, inputs);
      return [
        estimate.kept_paise,
        estimate.worst_case_paise,
        estimate.typical_paise,
        Math.round(estimate.worst_share * 100),
      ];
    });
    expect(figures).toEqual([
      [48_820, 17_169, 6_868, 35],
      [195_280, 66_616, 26_646, 34],
      [488_200, 183_568, 73_427, 38],
    ]);
    expect(costEstimate(500_000, professional.limits, true, inputs).unlimited).toEqual([]);
  });

  it("counts images only when the plan includes them", () => {
    const limits = { ...zero, monthly_image_limit: 60 };
    expect(costEstimate(500_000, limits, false, inputs).worst_case_paise).toBe(0);
    expect(costEstimate(500_000, limits, true, inputs).worst_case_paise).toBe(Math.round(60 * 0.042 * 88 * 100));
  });

  it("lists unlimited allowances (no ceiling), images only with the feature", () => {
    expect(costEstimate(100, zero, false, inputs).unlimited).toEqual([
      "monthly_token_limit",
      "vision_monthly_token_limit",
    ]);
    expect(costEstimate(100, zero, true, inputs).unlimited).toEqual([
      "monthly_token_limit",
      "monthly_image_limit",
      "vision_monthly_token_limit",
    ]);
  });

  it("rounds halves to even, as Python's round() does", () => {
    const exact: CostInputs = { ...inputs, usd_inr: 1, image_usd: 0.125 };
    expect(costEstimate(100, { ...zero, monthly_image_limit: 1 }, true, exact).worst_case_paise).toBe(12);
    expect(costEstimate(100, { ...zero, monthly_image_limit: 3 }, true, exact).worst_case_paise).toBe(38);
  });

  it("has no share when nothing is kept", () => {
    const estimate = costEstimate(0, standard.limits, false, inputs);
    expect(estimate.kept_paise).toBe(0);
    expect(estimate.worst_share).toBe(0);
    expect(estimate.typical_share).toBe(0);
  });
});

describe("answers", () => {
  it("gives the brief's answers figures with each plan's models", () => {
    const answers = (allowance: number, name: string, features: { files: boolean; images: boolean }) =>
      answersFor(allowance, blendedPrice(model(name)), features, inputs);
    expect(answers(500_000, "Claude Haiku 5.5", starter.features)).toBe(1_600);
    expect(answers(500_000, "GPT-5.4 mini", starter.features)).toBe(200);
    expect(answers(2_000_000, "Claude Sonnet 5.5", standard.features)).toBe(260);
    expect(answers(2_000_000, "Claude Haiku 5.5", standard.features)).toBe(5_200);
    expect(answers(5_000_000, "Claude Opus 5.5", professional.features)).toBe(290);
    expect(answers(5_000_000, "Claude Haiku 5.5", professional.features)).toBe(11_000);
  });

  it("is null when the allowance is unlimited or the model unpriced, 0 under one answer", () => {
    expect(answersFor(0, 0.16, starter.features, inputs)).toBeNull();
    expect(answersFor(500_000, null, starter.features, inputs)).toBeNull();
    expect(answersFor(100, 6.4, starter.features, inputs)).toBe(0);
    expect(answersFor(30_000, 3.2, starter.features, inputs)).toBe(5);
  });

  it("blends 85% input and 15% output, without a price for unpriced or free models", () => {
    expect(blendedPrice(model("Claude Haiku 5.5"))).toBeCloseTo(0.16, 10);
    expect(blendedPrice(model("Claude Opus 5.5"))).toBeCloseTo(6.4, 10);
    expect(blendedPrice(makeAdminModel({ input_cost_per_mtok: null, output_cost_per_mtok: 1 }))).toBeNull();
    expect(blendedPrice(makeAdminModel({ input_cost_per_mtok: 0, output_cost_per_mtok: 0 }))).toBeNull();
  });

  it("reads a vision allowance as scanned pages, at least one", () => {
    expect(scannedPages(150_000)).toBe(50);
    expect(scannedPages(500_000)).toBe(170);
    expect(scannedPages(1_000_000)).toBe(330);
    expect(scannedPages(100)).toBe(1);
  });
});

describe("planDefaultModel", () => {
  it("prefers the plan's default, then the server's default when offered, then the first model of the catalogue", () => {
    expect(planDefaultModel(models, [4, 3], 4)?.display_name).toBe("Claude Haiku 5.5");
    expect(planDefaultModel(models, null, null)?.display_name).toBe("Claude Opus 5.5");
    expect(planDefaultModel(models, [5, 3], null)?.display_name).toBe("GPT-5.4 mini");
    expect(planDefaultModel(models, [], null)).toBeNull();
  });
});
