import { useEffect, useId, useMemo, useRef, useState, type FormEvent } from "react";
import { ApiError } from "../../api/client";
import type {
  AdminModel,
  AdminPlan,
  AdminPlugin,
  CostInputs,
  PlanCostEstimate,
  PlanInput,
  PlanSaved,
  UserLimits,
} from "../../api/types";
import { formatInr } from "../../lib/billing";
import { formatNumber } from "../../lib/format";
import Modal from "../ui/Modal";
import { ButtonSpinner } from "../ui/Spinner";
import { AlertCircleIcon, AlertIcon } from "../ui/icons";
import { createPlan, errorMessage, updatePlan } from "./adminApi";
import { percent, roundedInr } from "./billingUi";
import { Field, SegmentedField, SwitchField } from "./Field";
import { FormValidator, MAX_INT32, type FieldErrors } from "./form";
import { answersFor, blendedPrice, costEstimate, HIGH_SHARE, planDefaultModel, scannedPages } from "./planCost";

const CODE_PATTERN = /^[a-z][a-z0-9_-]{1,31}$/;
const MAX_PRICE_RUPEES = 100_000;

type LimitKey = keyof UserLimits;
type ErrorKey = "code" | "name" | "description" | "price" | "rank" | "models" | LimitKey;

type LimitSpec = { key: LimitKey; label: string; unit: string; max: number; min?: number };

/** The seven limits in the editor's order (two columns). */
const LIMITS: LimitSpec[] = [
  { key: "monthly_token_limit", label: "Chat allowance", unit: "tokens", max: Number.MAX_SAFE_INTEGER },
  { key: "monthly_image_limit", label: "Images", unit: "images", max: MAX_INT32 },
  { key: "vision_monthly_token_limit", label: "Scanned PDF pages", unit: "tokens", max: Number.MAX_SAFE_INTEGER },
  { key: "rate_limit_per_min", label: "Requests a minute", unit: "per min", max: MAX_INT32 },
  { key: "max_documents", label: "Documents", unit: "documents", max: MAX_INT32 },
  { key: "max_storage_mb", label: "Storage", unit: "MB", max: MAX_INT32 },
  { key: "max_upload_mb", label: "Largest file", unit: "MB", max: MAX_INT32, min: 1 },
];

const NEW_PLAN_LIMITS: UserLimits = {
  monthly_token_limit: 2_000_000,
  rate_limit_per_min: 20,
  max_upload_mb: 25,
  max_documents: 200,
  max_storage_mb: 1_000,
  monthly_image_limit: 30,
  vision_monthly_token_limit: 500_000,
};

type FormState = {
  code: string;
  name: string;
  description: string;
  price: string; // whole rupees
  rank: string;
  status: "active" | "retired";
  recommended: boolean;
  files: boolean;
  images: boolean;
  plugins: string[];
  limits: Record<LimitKey, string>;
  restrict: boolean;
  modelIds: number[];
  defaultId: number | null;
  applyToCurrent: boolean;
};

function initialState(plan: AdminPlan | null, plans: AdminPlan[]): FormState {
  const limits = plan?.limits ?? NEW_PLAN_LIMITS;
  const nextRank = Math.max(0, ...plans.map((p) => p.rank)) + 10;
  return {
    code: plan?.code ?? "",
    name: plan?.name ?? "",
    description: plan?.description ?? "",
    price: plan ? String(plan.price_paise / 100) : "",
    rank: String(plan?.rank ?? nextRank),
    status: plan?.status ?? "active",
    recommended: plan?.recommended ?? false,
    files: plan?.features.files ?? false,
    images: plan?.features.images ?? false,
    plugins: plan?.features.plugins ?? [],
    limits: Object.fromEntries(LIMITS.map(({ key }) => [key, String(limits[key])])) as Record<LimitKey, string>,
    restrict: plan ? plan.chat_model_ids !== null : false,
    modelIds: plan?.chat_model_ids ?? [],
    defaultId: plan?.default_chat_model_id ?? null,
    applyToCurrent: false,
  };
}

/** "3,000,000" or "3 000 000" as typed → "3000000". */
const digits = (raw: string) => raw.replace(/[,\s_]/g, "");

/** A typed whole number for the live estimate (0 while it isn't one yet). */
function liveNumber(raw: string): number {
  const value = Number(digits(raw));
  return Number.isSafeInteger(value) && value >= 0 ? value : 0;
}

type PlanFormModalProps = {
  /** null: a new plan. */
  plan: AdminPlan | null;
  plans: AdminPlan[];
  /** Every chat model (disabled ones are marked). */
  chatModels: AdminModel[];
  plugins: AdminPlugin[];
  inputs: CostInputs;
  hardCap: number;
  onClose: () => void;
  onSaved: (saved: PlanSaved, created: boolean) => void;
};

export default function PlanFormModal({
  plan,
  plans,
  chatModels,
  plugins,
  inputs,
  hardCap,
  onClose,
  onSaved,
}: PlanFormModalProps) {
  const formId = useId();
  const formRef = useRef<HTMLFormElement>(null);
  const [form, setForm] = useState(() => initialState(plan, plans));
  const [errors, setErrors] = useState<FieldErrors<ErrorKey>>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<string | null>(null);
  const [confirmAsks, setConfirmAsks] = useState(0);
  const applyRef = useRef<HTMLButtonElement>(null);
  const [saving, setSaving] = useState(false);
  const [invalidSubmits, setInvalidSubmits] = useState(0);
  const creating = plan === null;
  const others = plans.filter((p) => p.id !== plan?.id);
  const affected = plan ? plan.current_users + plan.scheduled_periods : 0;

  useEffect(() => {
    if (invalidSubmits > 0) formRef.current?.querySelector<HTMLElement>("[aria-invalid='true']")?.focus();
  }, [invalidSubmits]);

  useEffect(() => {
    if (confirmAsks > 0) applyRef.current?.focus();
  }, [confirmAsks]);

  function update<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((current) => ({ ...current, [key]: value }));
    setErrors((current) => (current[key as ErrorKey] ? { ...current, [key]: undefined } : current));
    setConfirmation(null);
  }

  function updateLimit(key: LimitKey, value: string) {
    setForm((current) => ({ ...current, limits: { ...current.limits, [key]: value } }));
    setErrors((current) => (current[key] ? { ...current, [key]: undefined } : current));
    setConfirmation(null);
  }

  function toggleModel(id: number, checked: boolean) {
    setForm((current) => {
      const modelIds = checked ? [...current.modelIds, id] : current.modelIds.filter((m) => m !== id);
      const defaultId = !checked && current.defaultId === id ? null : current.defaultId;
      return { ...current, modelIds, defaultId };
    });
    setErrors((current) => (current.models ? { ...current, models: undefined } : current));
    setConfirmation(null);
  }

  const liveLimits = useMemo(
    () => Object.fromEntries(LIMITS.map(({ key }) => [key, liveNumber(form.limits[key])])) as unknown as UserLimits,
    [form.limits],
  );
  const estimate = costEstimate(liveNumber(form.price) * 100, liveLimits, form.images, inputs);
  const enabledModels = chatModels.filter((model) => model.enabled);
  const defaultModel = planDefaultModel(enabledModels, form.restrict ? form.modelIds : null, form.defaultId);
  const answers = defaultModel
    ? answersFor(liveLimits.monthly_token_limit, blendedPrice(defaultModel), form, inputs)
    : null;

  function limitHint(key: LimitKey): string | undefined {
    const value = liveLimits[key];
    const saved = plan?.limits[key];
    const was = saved !== undefined && form.limits[key] !== String(saved) ? `Was ${formatNumber(saved)}.` : "";
    let note = "";
    if (key === "monthly_token_limit") {
      if (value === 0) note = "Unlimited: no cost ceiling.";
      else if (answers !== null && defaultModel)
        note = `About ${formatNumber(answers)} answers with ${defaultModel.display_name}.`;
      else note = "Standard tokens: each model counts at its rate.";
    } else if (key === "vision_monthly_token_limit") {
      note = value === 0 ? "Unlimited." : `About ${formatNumber(scannedPages(value))} pages.`;
    } else if (key === "monthly_image_limit" && !form.images) {
      note = "Used only with Images from chat.";
    } else if (key === "max_upload_mb") {
      note = `Up to ${formatNumber(hardCap)} MB (the server's cap).`;
    }
    return [was, note].filter(Boolean).join(" ") || undefined;
  }

  function validate(): PlanInput | null {
    const v = new FormValidator<ErrorKey>();
    if (creating) {
      const code = form.code.trim();
      if (!CODE_PATTERN.test(code)) {
        v.fail("code", "Use 2 to 32 lowercase letters, digits, dashes or underscores, starting with a letter.");
      } else if (others.some((p) => p.code === code)) {
        v.fail("code", "Another plan has this code.");
      }
    }
    const name = v.text("name", form.name, 1, 60);
    const description = v.text("description", form.description, 0, 300);
    const rupees = v.integer("price", digits(form.price), 1, MAX_PRICE_RUPEES);
    const rank = v.integer("rank", digits(form.rank), 0, MAX_INT32);
    const clash = others.find((p) => p.rank === rank);
    if (clash) v.fail("rank", `${clash.name} has rank ${formatNumber(rank)}. Ranks must differ.`);
    if (form.status === "active" && Number.isInteger(rupees) && Number.isInteger(rank)) {
      const price = rupees * 100;
      const misordered = others.find(
        (p) =>
          p.status === "active" &&
          ((p.rank < rank && p.price_paise >= price) || (p.rank > rank && p.price_paise <= price)),
      );
      if (misordered) {
        v.fail(
          "price",
          `A higher plan must cost more: ${misordered.name} (rank ${formatNumber(misordered.rank)}) costs ${formatInr(misordered.price_paise)}.`,
        );
      }
    }
    const limits = Object.fromEntries(
      LIMITS.map(({ key, max, min = 0 }) => [
        key,
        v.integer(key, digits(form.limits[key]), min, key === "max_upload_mb" ? hardCap : max),
      ]),
    ) as unknown as UserLimits;
    if (form.restrict && form.modelIds.length === 0) v.fail("models", "Choose at least one chat model.");
    setErrors(v.errors);
    if (!v.valid) {
      setInvalidSubmits((n) => n + 1);
      return null;
    }
    const registered = new Set(plugins.map((plugin) => plugin.id));
    return {
      name,
      description,
      price_paise: rupees * 100,
      rank,
      status: form.status,
      recommended: form.status === "active" && form.recommended,
      features: { files: form.files, images: form.images, plugins: form.plugins.filter((id) => registered.has(id)) },
      limits,
      chat_model_ids: form.restrict ? form.modelIds : null,
      default_chat_model_id: form.defaultId,
    };
  }

  async function save(confirmReduction: boolean) {
    if (saving) return;
    setServerError(null);
    const input = validate();
    if (!input) return;
    setSaving(true);
    try {
      const saved =
        plan === null
          ? await createPlan({ ...input, code: form.code.trim() })
          : await updatePlan(plan.id, {
              ...input,
              apply_to_current: form.applyToCurrent,
              ...(confirmReduction ? { confirm_reduction: true } : {}),
            });
      onSaved(saved, creating);
    } catch (err) {
      if (err instanceof ApiError && err.code === "confirmation_required") {
        setConfirmation(err.message);
        setConfirmAsks((n) => n + 1);
      } else {
        setServerError(errorMessage(err, "Couldn't save the plan. Please try again."));
      }
      setSaving(false);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void save(false);
  }

  const recommendedElsewhere = others.find((p) => p.recommended);
  const rankField = (
    <Field label="Rank" hint="Higher ranks are upgrades and must cost more." error={errors.rank}>
      {(control) => (
        <input
          {...control}
          className="input"
          inputMode="numeric"
          autoComplete="off"
          value={form.rank}
          onChange={(e) => update("rank", e.target.value)}
        />
      )}
    </Field>
  );
  const limitField = ({ key, label, unit }: LimitSpec) => (
    <Field key={key} label={label} hint={limitHint(key)} error={errors[key]}>
      {(control) => (
        <div className="admin-input-unit">
          <input
            {...control}
            className="input"
            inputMode="numeric"
            autoComplete="off"
            value={form.limits[key]}
            onChange={(e) => updateLimit(key, e.target.value)}
          />
          <span aria-hidden="true">{unit}</span>
        </div>
      )}
    </Field>
  );

  return (
    <Modal
      open
      title={creating ? "New plan" : `Edit plan: ${plan.name}`}
      onClose={onClose}
      width={980}
      className="admin-plan-editor"
      bare
      footer={
        <>
          <button type="button" className="btn" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          {/* aria-disabled while saving: the focus stays here for an error, or moves on to "Apply anyway". */}
          <button
            type="submit"
            form={formId}
            className="btn btn-primary"
            aria-disabled={saving || undefined}
            aria-busy={saving || undefined}
          >
            {saving ? <ButtonSpinner size={14} /> : null}
            {creating ? "Create plan" : "Save changes"}
          </button>
        </>
      }
    >
      <div className="modal-body admin-plan-editor-body">
        <div className="admin-plan-editor-grid">
          <form id={formId} ref={formRef} className="admin-form admin-plan-editor-form" onSubmit={onSubmit} noValidate>
            <fieldset className="admin-form-section">
              <legend>Plan</legend>
              <div className="admin-form-grid">
                {creating ? (
                  <Field
                    label="Code"
                    hint="Set once and never changed: lowercase letters, digits, dashes or underscores."
                    error={errors.code}
                  >
                    {(control) => (
                      <input
                        {...control}
                        className="input admin-mono"
                        autoComplete="off"
                        autoCapitalize="none"
                        spellCheck={false}
                        value={form.code}
                        onChange={(e) => update("code", e.target.value)}
                      />
                    )}
                  </Field>
                ) : null}
                <Field label="Name" error={errors.name}>
                  {(control) => (
                    <input
                      {...control}
                      className="input"
                      autoComplete="off"
                      value={form.name}
                      onChange={(e) => update("name", e.target.value)}
                    />
                  )}
                </Field>
                <Field label="Price per 30 days" hint="Whole rupees; what one period costs." error={errors.price}>
                  {(control) => (
                    <div className="admin-input-unit">
                      <input
                        {...control}
                        className="input"
                        inputMode="numeric"
                        autoComplete="off"
                        value={form.price}
                        onChange={(e) => update("price", e.target.value)}
                      />
                      <span aria-hidden="true">₹</span>
                    </div>
                  )}
                </Field>
                {creating ? rankField : null}
                <Field
                  label="Description"
                  hint={`One line on the plan card. ${formatNumber(form.description.trim().length)} of 300 characters.`}
                  error={errors.description}
                  className="admin-span-2"
                >
                  {(control) => (
                    <input
                      {...control}
                      className="input"
                      autoComplete="off"
                      value={form.description}
                      onChange={(e) => update("description", e.target.value)}
                    />
                  )}
                </Field>
                {creating ? null : rankField}
                <SegmentedField
                  label="Status"
                  hint={
                    form.status === "active"
                      ? "Active: offered to everyone."
                      : "Retired: nobody can buy or renew it; current periods run on."
                  }
                  value={form.status}
                  options={[
                    { value: "active", label: "Active" },
                    { value: "retired", label: "Retired" },
                  ]}
                  onChange={(status) => update("status", status)}
                />
              </div>
              <SwitchField
                label="Recommended"
                hint={
                  form.status === "retired"
                    ? "A retired plan can't be the recommended one."
                    : recommendedElsewhere
                      ? `One plan at a time: saving this takes the flag from ${recommendedElsewhere.name}.`
                      : "Emphasised on the plan page for users without a plan."
                }
                checked={form.status === "active" && form.recommended}
                disabled={form.status === "retired"}
                onChange={(checked) => update("recommended", checked)}
              />
            </fieldset>

            <fieldset className="admin-form-section">
              <legend>Features</legend>
              <div className="admin-switch-rows">
                <SwitchField
                  label="Files from chat"
                  hint="PDF, Word, Excel and Markdown files the assistant creates."
                  checked={form.files}
                  onChange={(checked) => update("files", checked)}
                />
                <SwitchField
                  label="Images from chat"
                  hint="Images made with the active image model."
                  checked={form.images}
                  onChange={(checked) => update("images", checked)}
                />
                {plugins.map((plugin) => (
                  <SwitchField
                    key={plugin.id}
                    label={`${plugin.name} plugin`}
                    hint={
                      !plugin.configured
                        ? `${plugin.description} Not set up yet: users see it as not available.`
                        : !plugin.enabled
                          ? `${plugin.description} Turned off in the Plugins tab.`
                          : plugin.description
                    }
                    checked={form.plugins.includes(plugin.id)}
                    onChange={(checked) =>
                      update(
                        "plugins",
                        checked ? [...form.plugins, plugin.id] : form.plugins.filter((id) => id !== plugin.id),
                      )
                    }
                  />
                ))}
              </div>
            </fieldset>

            <fieldset className="admin-form-section">
              <legend>Limits per 30 days · 0 = unlimited</legend>
              <div className="admin-limit-grid">{LIMITS.map(limitField)}</div>
            </fieldset>

            <fieldset className="admin-form-section">
              <legend>Models</legend>
              <SegmentedField
                label="Chat models"
                value={form.restrict ? "only" : "all"}
                options={[
                  { value: "all", label: "All chat models" },
                  { value: "only", label: "Only these" },
                ]}
                onChange={(value) => update("restrict", value === "only")}
                hint={
                  form.restrict
                    ? "New chat models join this plan only when you tick them here."
                    : "Every enabled chat model, including ones added later."
                }
              />
              {form.restrict ? (
                <ModelChecks
                  models={chatModels}
                  selected={form.modelIds}
                  defaultId={form.defaultId}
                  error={errors.models}
                  onToggle={toggleModel}
                  onDefault={(id) => update("defaultId", id)}
                />
              ) : (
                <Field label="New chats start with">
                  {(control) => (
                    <select
                      {...control}
                      className="select"
                      value={form.defaultId ?? ""}
                      onChange={(e) => update("defaultId", e.target.value ? Number(e.target.value) : null)}
                    >
                      <option value="">The server's default model</option>
                      {enabledModels.map((model) => (
                        <option key={model.id} value={model.id}>
                          {model.display_name}
                        </option>
                      ))}
                    </select>
                  )}
                </Field>
              )}
            </fieldset>

            {serverError ? (
              <div className="ui-notice ui-notice-danger" role="alert">
                <AlertCircleIcon size={15} />
                <span className="ui-notice-text">{serverError}</span>
              </div>
            ) : null}
          </form>
          <EstimateAside estimate={estimate} inputs={inputs} />
        </div>
      </div>
      {!creating && affected > 0 ? (
        <div className="admin-apply-choice">
          <div className="admin-apply-options" role="radiogroup" aria-label="Apply the changes to">
            <label>
              <input
                type="radio"
                className="ui-radio"
                name={`${formId}-apply`}
                checked={!form.applyToCurrent}
                onChange={() => update("applyToCurrent", false)}
              />
              Apply to new periods only
            </label>
            <label>
              <input
                type="radio"
                className="ui-radio"
                name={`${formId}-apply`}
                checked={form.applyToCurrent}
                onChange={() => update("applyToCurrent", true)}
              />
              Also apply to the {formatNumber(affected)} current and scheduled {plan.name} period
              {affected === 1 ? "" : "s"}
            </label>
          </div>
          {confirmation ? (
            <div className="ui-notice ui-notice-warning" role="alert">
              <AlertIcon size={15} />
              <span className="ui-notice-text">{confirmation}</span>
              <button
                ref={applyRef}
                type="button"
                className="btn btn-sm"
                aria-disabled={saving || undefined}
                aria-busy={saving || undefined}
                onClick={() => void save(true)}
              >
                Apply anyway
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </Modal>
  );
}

type ModelChecksProps = {
  models: AdminModel[];
  selected: number[];
  defaultId: number | null;
  error?: string;
  onToggle: (id: number, checked: boolean) => void;
  onDefault: (id: number) => void;
};

/** The plan's chat models: a two-column checklist; one ticked model can be the plan's default. */
function ModelChecks({ models, selected, defaultId, error, onToggle, onDefault }: ModelChecksProps) {
  const errorId = useId();
  if (models.length === 0) return <p className="field-hint">No chat models yet. Add one in the Models tab.</p>;
  return (
    <fieldset className="admin-model-checks-group" aria-describedby={error ? errorId : undefined}>
      <legend className="sr-only">The plan's chat models</legend>
      <div className="admin-model-checks">
        {models.map((model) => {
          const checked = selected.includes(model.id);
          return (
            <div key={model.id} className="admin-model-check">
              <label>
                <input
                  type="checkbox"
                  className="ui-check"
                  checked={checked}
                  aria-invalid={error ? true : undefined}
                  onChange={(e) => onToggle(model.id, e.target.checked)}
                />
                <span>{model.display_name}</span>
              </label>
              {model.enabled ? null : <span className="badge badge-neutral">disabled</span>}
              {checked && defaultId === model.id ? (
                <span className="badge badge-accent">Default</span>
              ) : checked ? (
                <button
                  type="button"
                  className="btn btn-ghost btn-xs admin-make-default"
                  aria-label={`Make ${model.display_name} the default`}
                  onClick={() => onDefault(model.id)}
                >
                  Make default
                </button>
              ) : null}
            </div>
          );
        })}
      </div>
      {error ? (
        <span id={errorId} className="error-text">
          <AlertCircleIcon size={13} />
          {error}
        </span>
      ) : null}
    </fieldset>
  );
}

type EstimateAsideProps = { estimate: PlanCostEstimate; inputs: CostInputs };

/** The live AI cost of one period against what the owner keeps (worst case, typical), as the server computes it. */
function EstimateAside({ estimate, inputs }: EstimateAsideProps) {
  const titleId = useId();
  const noCeiling = estimate.unlimited.length > 0;
  const high = !noCeiling && estimate.worst_share >= HIGH_SHARE;
  const kept = estimate.kept_paise;
  /** No share before a price is typed. */
  const shareOf = (value: number) => (kept > 0 ? percent(value) : "—");
  const share = (paise: number) => (kept > 0 ? Math.min(100, (paise / kept) * 100) : 0);
  const typicalWidth = share(estimate.typical_paise);
  const worstWidth = Math.max(0, share(estimate.worst_case_paise) - typicalWidth);
  return (
    <aside className="admin-plan-estimate" aria-labelledby={titleId}>
      <div className="admin-plan-estimate-inner">
        <h3 id={titleId} className="admin-estimate-label">
          AI cost per period
        </h3>
        {high ? (
          <p className="admin-estimate-warning">
            <AlertIcon size={14} />
            High: over {percent(HIGH_SHARE)} of what you keep
          </p>
        ) : null}
        {noCeiling ? (
          <p className="admin-estimate-warning">
            <AlertIcon size={14} />
            No ceiling: {estimate.unlimited.length === 1 ? "an allowance is" : "some allowances are"} unlimited
          </p>
        ) : null}
        <p className="admin-estimate-figure">
          {noCeiling ? "No ceiling" : roundedInr(estimate.worst_case_paise)} <span>worst case</span>
        </p>
        <div className="admin-estimate-bar" aria-hidden="true">
          <span className="admin-estimate-typical" style={{ width: `${typicalWidth}%` }} />
          <span className="admin-estimate-worst" style={{ width: `${noCeiling ? 0 : worstWidth}%` }} />
        </div>
        <dl className="admin-estimate-legend">
          <div>
            <dt>
              <span className="admin-swatch admin-swatch-typical" aria-hidden="true" />
              Typical
            </dt>
            <dd>
              {roundedInr(estimate.typical_paise)} · {shareOf(estimate.typical_share)}
            </dd>
          </div>
          <div>
            <dt>
              <span className="admin-swatch admin-swatch-worst" aria-hidden="true" />
              Worst case
            </dt>
            <dd>
              {noCeiling ? "No ceiling" : `${roundedInr(estimate.worst_case_paise)} · ${shareOf(estimate.worst_share)}`}
            </dd>
          </div>
          <div>
            <dt>
              <span className="admin-swatch admin-swatch-kept" aria-hidden="true" />
              You keep
            </dt>
            <dd>{formatInr(kept)}</dd>
          </div>
        </dl>
        <p className="admin-estimate-note">
          Typical: 40% of every allowance; worst case: all of it, on any model. Chat counts at $
          {inputs.standard_usd_per_mtok.toFixed(2)} per million standard tokens, scanned pages at $
          {inputs.vision_usd_per_mtok.toFixed(2)} per million and images at ${inputs.image_usd} each. What you keep is
          the price after Razorpay's {(inputs.fee_bp / 100).toFixed(2)}% fee; hosting isn't included. At ₹
          {formatNumber(inputs.usd_inr)} to the dollar; updates as you type.
        </p>
      </div>
    </aside>
  );
}
