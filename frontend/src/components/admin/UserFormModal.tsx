import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { api, ApiError } from "../../api/client";
import type {
  AdminConfig,
  AdminModel,
  AdminPlan,
  AdminUser,
  AdminUserCreate,
  AdminUserUpdate,
  LimitOverrides,
  UserLimits,
} from "../../api/types";
import { formatNumber } from "../../lib/format";
import Modal from "../ui/Modal";
import PasswordInput from "../ui/PasswordInput";
import { ButtonSpinner } from "../ui/Spinner";
import { AlertCircleIcon, AlertIcon, InfoIcon } from "../ui/icons";
import { errorMessage } from "./adminApi";
import { Field, SwitchField } from "./Field";
import { FormValidator, MAX_INT32, type FieldErrors } from "./form";
import UserPlanSection from "./UserPlanSection";

const USERNAME_PATTERN = /^[a-zA-Z0-9_.-]+$/;
const MAX_GRANT_DAYS = 90;

type LimitKey = keyof UserLimits;
type ErrorKey = "username" | "password" | "grantDays" | "grantReason" | LimitKey;

const LIMIT_FIELDS: { key: LimitKey; label: string; max: number; min?: number }[] = [
  { key: "monthly_token_limit", label: "Chat allowance (standard tokens)", max: Number.MAX_SAFE_INTEGER },
  { key: "monthly_image_limit", label: "Images per period", max: MAX_INT32 },
  { key: "vision_monthly_token_limit", label: "Scanned pages (vision tokens)", max: Number.MAX_SAFE_INTEGER },
  { key: "rate_limit_per_min", label: "Requests per minute", max: MAX_INT32 },
  { key: "max_upload_mb", label: "Max file size (MB)", max: MAX_INT32, min: 1 },
  { key: "max_documents", label: "Max documents", max: MAX_INT32 },
  { key: "max_storage_mb", label: "Max storage (MB)", max: MAX_INT32 },
];

type FormState = {
  username: string;
  password: string;
  is_active: boolean;
  restrict_models: boolean;
  allowed_model_ids: number[];
  billing_tester: boolean;
  /** "" = the plan's value. */
  overrides: Record<LimitKey, string>;
  grant: boolean;
  grantPlanId: string;
  grantDays: string;
  grantOpenEnded: boolean;
  grantReason: string;
};

const overrideText = (overrides: LimitOverrides | null, key: LimitKey) => {
  const value = overrides?.[key];
  return value === null || value === undefined ? "" : String(value);
};

function initialState(user: AdminUser | null, plans: AdminPlan[], paymentsOpen: boolean): FormState {
  const professional =
    plans.find((plan) => plan.code === "professional") ?? [...plans].sort((a, b) => b.rank - a.rank)[0];
  return {
    username: user?.username ?? "",
    password: "",
    is_active: user?.is_active ?? true,
    restrict_models: user?.restrict_models ?? false,
    allowed_model_ids: user?.allowed_model_ids ?? [],
    billing_tester: user?.billing_tester ?? false,
    overrides: Object.fromEntries(
      LIMIT_FIELDS.map(({ key }) => [key, overrideText(user?.overrides ?? null, key)]),
    ) as Record<LimitKey, string>,
    grant: user === null && !paymentsOpen && professional !== undefined,
    grantPlanId: professional ? String(professional.id) : "",
    grantDays: "30",
    grantOpenEnded: false,
    grantReason: "New account",
  };
}

const digits = (raw: string) => raw.replace(/[,\s_]/g, "");

/** A typed override that is a whole number, else null (blank or not yet valid). */
function typedNumber(raw: string): number | null {
  const text = digits(raw);
  if (!/^\d+$/.test(text)) return null;
  const value = Number(text);
  return Number.isSafeInteger(value) ? value : null;
}

/** An override is more generous than the plan's value: a higher number, or unlimited (0) against a limit. */
const isAbove = (value: number, plan: number) => plan !== 0 && (value === 0 || value > plan);

type UserFormModalProps = {
  /** null = create a new user. */
  user: AdminUser | null;
  config: AdminConfig;
  /** Chat models for the access checklist; null while loading. */
  chatModels: AdminModel[] | null;
  modelsError: string | null;
  /** Every plan (grants and the plan's values); null while loading. */
  plans: AdminPlan[] | null;
  /** Regular users can pay online (live keys); without it a new user starts with a grant. */
  paymentsOpen: boolean;
  startGrant?: boolean;
  onClose: () => void;
  onSaved: (user: AdminUser, created: boolean) => void;
  /** A billing change (grant, revoke, …) was applied at once. */
  onPlanChanged: (message: string) => void;
  onNavigate: (hash: string) => void;
};

export default function UserFormModal({
  user,
  config,
  chatModels,
  modelsError,
  plans,
  paymentsOpen,
  startGrant,
  onClose,
  onSaved,
  onPlanChanged,
  onNavigate,
}: UserFormModalProps) {
  const formId = useId();
  const formRef = useRef<HTMLFormElement>(null);
  const planList = plans ?? [];
  const [form, setForm] = useState(() => initialState(user, planList, paymentsOpen));
  const [errors, setErrors] = useState<FieldErrors<ErrorKey>>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [unlimitedConfirmed, setUnlimitedConfirmed] = useState(false);
  const [planMessage, setPlanMessage] = useState<string | null>(null);
  const [invalidSubmits, setInvalidSubmits] = useState(0);
  const creating = user === null;
  const initial = useRef(form.overrides);

  useEffect(() => {
    if (invalidSubmits > 0) formRef.current?.querySelector<HTMLElement>("[aria-invalid='true']")?.focus();
  }, [invalidSubmits]);

  function update<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((current) => ({ ...current, [key]: value }));
    setErrors((current) => (current[key as ErrorKey] ? { ...current, [key]: undefined } : current));
  }

  function updateOverride(key: LimitKey, value: string) {
    setForm((current) => ({ ...current, overrides: { ...current.overrides, [key]: value } }));
    setErrors((current) => (current[key] ? { ...current, [key]: undefined } : current));
    if (key === "monthly_token_limit") setUnlimitedConfirmed(false);
  }

  /** The plan's value a blank override stands for. */
  function planValue(key: LimitKey): number {
    if (creating) {
      const granted = form.grant ? planList.find((plan) => String(plan.id) === form.grantPlanId) : undefined;
      return granted?.limits[key] ?? config.defaults[key];
    }
    if (user.overrides[key] === null) return user.limits[key];
    const plan = planList.find((p) => p.id === user.plan.plan?.id);
    return plan?.limits[key] ?? config.defaults[key];
  }

  function aboveFlag(key: LimitKey): boolean {
    const text = form.overrides[key];
    if (!creating && text === initial.current[key]) return user.above_plan.includes(key);
    const value = typedNumber(text);
    return value !== null && isAbove(value, planValue(key));
  }

  const chatOverride = typedNumber(form.overrides.monthly_token_limit);
  const asksUnlimited = chatOverride === 0 && (creating || initial.current.monthly_token_limit !== "0");

  function validate(): { username: string; fields: AdminUserUpdate; grant: AdminUserCreate["grant"] } | null {
    const v = new FormValidator<ErrorKey>();
    const username = creating ? v.text("username", form.username, 3, 64).toLowerCase() : "";
    if (creating && !USERNAME_PATTERN.test(username)) {
      v.fail("username", "Use only letters, digits, dots, dashes and underscores.");
    }
    const password = form.password;
    if (creating && !password) v.fail("password", "This field is required.");
    else if (password && password.length < 8) v.fail("password", "Use at least 8 characters.");
    else if (password.length > 256) v.fail("password", "Use at most 256 characters.");

    const overrides: Partial<LimitOverrides> = {};
    for (const { key, max, min = 0 } of LIMIT_FIELDS) {
      const text = form.overrides[key];
      if (!creating && text === initial.current[key]) continue;
      const limit = key === "max_upload_mb" ? config.max_upload_mb_hard : max;
      const value = text.trim() ? v.optionalNumber(key, digits(text), min, limit, true) : null;
      if (creating && value === null) continue;
      overrides[key] = value;
    }
    if (asksUnlimited && !unlimitedConfirmed)
      v.fail("monthly_token_limit", "Confirm an unlimited chat allowance below.");

    let grant: AdminUserCreate["grant"];
    if (creating && form.grant) {
      const days = form.grantOpenEnded ? null : v.integer("grantDays", form.grantDays, 1, MAX_GRANT_DAYS);
      const reason = v.text("grantReason", form.grantReason, 1, 200);
      grant = { plan_id: Number(form.grantPlanId), days, reason };
    }

    setErrors(v.errors);
    if (!v.valid) {
      setInvalidSubmits((n) => n + 1);
      return null;
    }
    const fields: AdminUserUpdate = {
      is_active: form.is_active,
      restrict_models: form.restrict_models,
      allowed_model_ids: form.allowed_model_ids,
      billing_tester: form.billing_tester,
      ...(Object.keys(overrides).length ? { overrides } : {}),
      ...(asksUnlimited ? { confirm_unlimited: true } : {}),
      ...(password ? { password } : {}),
    };
    return { username, fields, grant };
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (saving) return;
    setServerError(null);
    const valid = validate();
    if (!valid) return;
    setSaving(true);
    try {
      const saved =
        user === null
          ? await api.post<AdminUser>("/admin/users", {
              ...valid.fields,
              username: valid.username,
              password: form.password,
              ...(valid.grant ? { grant: valid.grant } : {}),
            } satisfies AdminUserCreate)
          : await api.patch<AdminUser>(`/admin/users/${user.id}`, valid.fields);
      onSaved(saved, creating);
    } catch (err) {
      if (creating && err instanceof ApiError && err.status === 409) {
        setErrors({ username: err.message });
        setInvalidSubmits((n) => n + 1);
      } else {
        setServerError(errorMessage(err, "Couldn't save the user. Please try again."));
      }
      setSaving(false);
    }
  }

  const plansByRank = [...planList].sort((a, b) => a.rank - b.rank);

  return (
    <Modal
      open
      title={user === null ? "New user" : `Edit ${user.username}`}
      onClose={onClose}
      width={720}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button
            type="submit"
            form={formId}
            className="btn btn-primary"
            disabled={saving}
            aria-busy={saving || undefined}
          >
            {saving ? <ButtonSpinner size={14} /> : null}
            {creating ? "Create user" : "Save changes"}
          </button>
        </>
      }
    >
      <form id={formId} ref={formRef} className="admin-form" onSubmit={onSubmit} noValidate>
        <fieldset className="admin-form-section">
          <legend>Account</legend>
          <div className="admin-form-grid">
            {creating ? (
              <Field
                label="Username"
                hint="3–64 letters, digits, dots, dashes or underscores. Stored in lowercase."
                error={errors.username}
              >
                {(control) => (
                  <input
                    {...control}
                    className="input"
                    autoComplete="off"
                    autoCapitalize="none"
                    spellCheck={false}
                    value={form.username}
                    onChange={(e) => update("username", e.target.value)}
                  />
                )}
              </Field>
            ) : null}
            <Field
              label={creating ? "Password" : "Set new password"}
              hint={
                creating
                  ? "At least 8 characters."
                  : "Leave blank to keep the current password. A new password signs the user out everywhere."
              }
              error={errors.password}
              className={creating ? undefined : "admin-span-2"}
            >
              {(control) => (
                <PasswordInput
                  {...control}
                  autoComplete="new-password"
                  value={form.password}
                  onChange={(e) => update("password", e.target.value)}
                />
              )}
            </Field>
          </div>
          <SwitchField
            label="Active"
            hint="Inactive users can't sign in. Deactivating ends their current sessions."
            checked={form.is_active}
            onChange={(checked) => update("is_active", checked)}
          />
        </fieldset>

        <fieldset className="admin-form-section">
          <legend>Plan</legend>
          {creating ? (
            <>
              {paymentsOpen ? null : (
                <div className="ui-notice">
                  <InfoIcon size={15} />
                  <span className="ui-notice-text">
                    This user can't use the app until they have a plan, and online payment isn't set up yet.
                  </span>
                </div>
              )}
              <SwitchField
                label="Start with a complimentary plan"
                hint={
                  form.grant
                    ? "From now, at no charge."
                    : "Without a plan the user sees the plan page and pays to start."
                }
                checked={form.grant}
                disabled={planList.length === 0}
                onChange={(checked) => update("grant", checked)}
              />
              {form.grant ? (
                <div className="admin-form-grid">
                  <Field label="Plan">
                    {(control) => (
                      <select
                        {...control}
                        className="select"
                        value={form.grantPlanId}
                        onChange={(e) => update("grantPlanId", e.target.value)}
                      >
                        {plansByRank.map((plan) => (
                          <option key={plan.id} value={plan.id}>
                            {plan.name}
                            {plan.status === "retired" ? " (retired)" : ""}
                          </option>
                        ))}
                      </select>
                    )}
                  </Field>
                  <Field label="Days" hint={`1 to ${MAX_GRANT_DAYS}.`} error={errors.grantDays}>
                    {(control) => (
                      <input
                        {...control}
                        className="input"
                        inputMode="numeric"
                        disabled={form.grantOpenEnded}
                        placeholder={form.grantOpenEnded ? "No end date" : undefined}
                        value={form.grantOpenEnded ? "" : form.grantDays}
                        onChange={(e) => update("grantDays", e.target.value)}
                      />
                    )}
                  </Field>
                  <Field
                    label="Reason"
                    error={errors.grantReason}
                    className="admin-span-2"
                    hint="Kept in the billing record."
                  >
                    {(control) => (
                      <input
                        {...control}
                        className="input"
                        maxLength={200}
                        autoComplete="off"
                        value={form.grantReason}
                        onChange={(e) => update("grantReason", e.target.value)}
                      />
                    )}
                  </Field>
                  <div className="admin-span-2">
                    <SwitchField
                      label="No end date"
                      hint="The plan's current values, allowances per calendar month, until you end it."
                      checked={form.grantOpenEnded}
                      onChange={(checked) => update("grantOpenEnded", checked)}
                    />
                  </div>
                </div>
              ) : null}
            </>
          ) : (
            <>
              <UserPlanSection
                user={user}
                plans={planList}
                startGrant={startGrant}
                onNavigate={onNavigate}
                onChanged={(message) => {
                  setPlanMessage(message);
                  onPlanChanged(message);
                }}
              />
              <div className="admin-flash-region" role="status" aria-live="polite">
                {planMessage ? <p className="admin-plan-message">{planMessage}</p> : null}
              </div>
            </>
          )}
          <SwitchField
            label="Can make test payments"
            hint="While Razorpay's test keys run, only these users can check out (no real money moves)."
            checked={form.billing_tester}
            onChange={(checked) => update("billing_tester", checked)}
          />
        </fieldset>

        <fieldset className="admin-form-section">
          <legend>Usage limits</legend>
          <p className="field-hint admin-form-note">
            Blank uses the plan's value. 0 = unlimited. Allowances count per period (per calendar month on a plan with
            no end date).
          </p>
          <div className="admin-form-grid">
            {LIMIT_FIELDS.map(({ key, label }) => (
              <OverrideField
                key={key}
                label={label}
                value={form.overrides[key]}
                planValue={planValue(key)}
                above={aboveFlag(key)}
                hint={
                  key === "max_upload_mb" ? `1 to ${formatNumber(config.max_upload_mb_hard)} MB per file.` : undefined
                }
                error={errors[key]}
                onChange={(value) => updateOverride(key, value)}
              />
            ))}
          </div>
          {asksUnlimited ? (
            unlimitedConfirmed ? (
              <p className="admin-text-warning">
                <AlertIcon size={14} />
                Unlimited chat allowance allowed: this user has no cost ceiling.
              </p>
            ) : (
              <div className="ui-notice ui-notice-warning">
                <AlertIcon size={15} />
                <span className="ui-notice-text">Unlimited chat allowance: this user has no cost ceiling.</span>
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() => {
                    setUnlimitedConfirmed(true);
                    setErrors((current) => ({ ...current, monthly_token_limit: undefined }));
                  }}
                >
                  Allow unlimited
                </button>
              </div>
            )
          ) : null}
        </fieldset>

        <fieldset className="admin-form-section">
          <legend>Model access</legend>
          <SwitchField
            label="Restrict to selected models"
            hint={
              form.restrict_models
                ? "The user can only use the checked models that their plan also includes."
                : "The user can use every chat model their plan includes."
            }
            checked={form.restrict_models}
            onChange={(checked) => update("restrict_models", checked)}
          />
          {form.restrict_models ? (
            <ModelChecklist
              models={chatModels}
              error={modelsError}
              selected={form.allowed_model_ids}
              onChange={(ids) => update("allowed_model_ids", ids)}
            />
          ) : null}
          {user?.no_models && form.restrict_models ? (
            <div className="ui-notice ui-notice-warning">
              <AlertIcon size={15} />
              <span className="ui-notice-text">
                No model left: the user's list and the plan's models don't overlap.
              </span>
            </div>
          ) : null}
        </fieldset>

        {serverError ? (
          <div className="ui-notice ui-notice-danger" role="alert">
            <AlertCircleIcon size={15} />
            <span className="ui-notice-text">{serverError}</span>
          </div>
        ) : null}
      </form>
    </Modal>
  );
}

type OverrideFieldProps = {
  label: string;
  value: string;
  planValue: number;
  above: boolean;
  hint?: string;
  error?: string;
  onChange: (value: string) => void;
};

/** A limit override: blank shows the plan's value as the placeholder; a set value is "Custom", maybe "Above plan". */
function OverrideField({ label, value, planValue, above, hint, error, onChange }: OverrideFieldProps) {
  const id = useId();
  const custom = value.trim() !== "";
  const describedBy = [error ? `${id}-error` : null, hint ? `${id}-hint` : null].filter(Boolean).join(" ") || undefined;
  return (
    <div className="field">
      <div className="admin-override-head">
        <label className="field-label" htmlFor={id}>
          {label}
        </label>
        {custom ? <span className="badge badge-accent">Custom</span> : null}
        {custom && above ? <span className="badge badge-warning">Above plan</span> : null}
        {custom ? (
          <button
            type="button"
            className="btn btn-ghost btn-xs admin-override-reset"
            aria-label={`Reset ${label} to the plan's value`}
            onClick={() => onChange("")}
          >
            Reset to plan
          </button>
        ) : null}
      </div>
      <input
        id={id}
        className="input"
        inputMode="numeric"
        autoComplete="off"
        placeholder={`Plan: ${planValue === 0 ? "unlimited" : formatNumber(planValue)}`}
        value={value}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        onChange={(e) => onChange(e.target.value)}
      />
      {error ? (
        <span id={`${id}-error`} className="error-text">
          <AlertCircleIcon size={13} />
          {error}
        </span>
      ) : null}
      {hint ? (
        <span id={`${id}-hint`} className="field-hint">
          {hint}
        </span>
      ) : null}
    </div>
  );
}

type ModelChecklistProps = {
  models: AdminModel[] | null;
  error: string | null;
  selected: number[];
  onChange: (ids: number[]) => void;
};

function ModelChecklist({ models, error, selected, onChange }: ModelChecklistProps) {
  if (!models) {
    return error ? (
      <p className="error-text">
        <AlertCircleIcon size={13} />
        {error}
      </p>
    ) : (
      <p className="field-hint">Loading models…</p>
    );
  }
  if (models.length === 0) {
    return <p className="field-hint">No chat models are configured yet. Add one in the Models tab.</p>;
  }
  const noneSelected = !models.some((model) => selected.includes(model.id));
  return (
    <fieldset className="admin-checklist">
      <legend className="field-label">Allowed chat models</legend>
      {models.map((model) => (
        <label key={model.id} className="admin-check">
          <input
            type="checkbox"
            className="ui-check"
            checked={selected.includes(model.id)}
            onChange={(e) =>
              onChange(e.target.checked ? [...selected, model.id] : selected.filter((id) => id !== model.id))
            }
          />
          <span className="admin-check-name">{model.display_name}</span>
          <code className="admin-check-id">{model.litellm_model}</code>
          {model.enabled ? null : <span className="badge badge-neutral">disabled</span>}
        </label>
      ))}
      {noneSelected ? (
        <p className="admin-text-warning">
          <AlertIcon size={14} />
          With no models selected, this user can't chat.
        </p>
      ) : null}
    </fieldset>
  );
}
