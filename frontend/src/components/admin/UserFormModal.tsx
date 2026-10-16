import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { api, ApiError } from "../../api/client";
import type { AdminConfig, AdminModel, AdminUser, AdminUserCreate, AdminUserUpdate } from "../../api/types";
import Modal from "../ui/Modal";
import Spinner from "../ui/Spinner";
import { errorMessage } from "./adminApi";
import { Field, SwitchField } from "./Field";
import { FormValidator, MAX_INT32, type FieldErrors } from "./form";

const USERNAME_PATTERN = /^[a-zA-Z0-9_.-]+$/;

type LimitKey =
  | "monthly_token_limit"
  | "monthly_image_limit"
  | "rate_limit_per_min"
  | "max_upload_mb"
  | "max_documents"
  | "max_storage_mb";
type ErrorKey = "username" | "password" | LimitKey;

type FormState = Record<LimitKey, string> & {
  username: string;
  password: string;
  is_active: boolean;
  restrict_models: boolean;
  allowed_model_ids: number[];
};

function initialState(user: AdminUser | null, config: AdminConfig): FormState {
  const limits = user ?? config.defaults;
  return {
    username: user?.username ?? "",
    password: "",
    is_active: user?.is_active ?? true,
    monthly_token_limit: String(limits.monthly_token_limit),
    monthly_image_limit: String(limits.monthly_image_limit),
    rate_limit_per_min: String(limits.rate_limit_per_min),
    max_upload_mb: String(limits.max_upload_mb),
    max_documents: String(limits.max_documents),
    max_storage_mb: String(limits.max_storage_mb),
    restrict_models: user?.restrict_models ?? false,
    allowed_model_ids: user?.allowed_model_ids ?? [],
  };
}

type UserFormModalProps = {
  /** null = create a new user. */
  user: AdminUser | null;
  config: AdminConfig;
  /** Chat models for the access checklist; null while loading. */
  chatModels: AdminModel[] | null;
  modelsError: string | null;
  onClose: () => void;
  onSaved: (user: AdminUser, created: boolean) => void;
};

export default function UserFormModal({ user, config, chatModels, modelsError, onClose, onSaved }: UserFormModalProps) {
  const formId = useId();
  const formRef = useRef<HTMLFormElement>(null);
  const [form, setForm] = useState(() => initialState(user, config));
  const [errors, setErrors] = useState<FieldErrors<ErrorKey>>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [invalidSubmits, setInvalidSubmits] = useState(0);
  const creating = user === null;

  useEffect(() => {
    if (invalidSubmits > 0) formRef.current?.querySelector<HTMLElement>("[aria-invalid='true']")?.focus();
  }, [invalidSubmits]);

  function update<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((current) => ({ ...current, [key]: value }));
    setErrors((current) => (current[key as ErrorKey] ? { ...current, [key]: undefined } : current));
  }

  function validate(): { username: string; fields: AdminUserUpdate } | null {
    const v = new FormValidator<ErrorKey>();
    const username = creating ? v.text("username", form.username, 3, 64).toLowerCase() : "";
    if (creating && !USERNAME_PATTERN.test(username)) {
      v.fail("username", "Use only letters, digits, dots, dashes and underscores.");
    }
    const password = form.password;
    if (creating && !password) v.fail("password", "This field is required.");
    else if (password && password.length < 8) v.fail("password", "Use at least 8 characters.");
    else if (password.length > 256) v.fail("password", "Use at most 256 characters.");

    const fields: AdminUserUpdate = {
      is_active: form.is_active,
      monthly_token_limit: v.integer("monthly_token_limit", form.monthly_token_limit, 0, Number.MAX_SAFE_INTEGER),
      monthly_image_limit: v.integer("monthly_image_limit", form.monthly_image_limit, 0, MAX_INT32),
      rate_limit_per_min: v.integer("rate_limit_per_min", form.rate_limit_per_min, 0, MAX_INT32),
      max_upload_mb: v.integer("max_upload_mb", form.max_upload_mb, 1, config.max_upload_mb_hard),
      max_documents: v.integer("max_documents", form.max_documents, 0, MAX_INT32),
      max_storage_mb: v.integer("max_storage_mb", form.max_storage_mb, 0, MAX_INT32),
      restrict_models: form.restrict_models,
      allowed_model_ids: form.allowed_model_ids,
      ...(password ? { password } : {}),
    };
    setErrors(v.errors);
    if (!v.valid) {
      setInvalidSubmits((n) => n + 1);
      return null;
    }
    return { username, fields };
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

  const limitField = (key: LimitKey, label: string, hint: string) => (
    <Field label={label} hint={hint} error={errors[key]}>
      {(control) => (
        <input
          {...control}
          className="input"
          inputMode="numeric"
          autoComplete="off"
          value={form[key]}
          onChange={(e) => update(key, e.target.value)}
        />
      )}
    </Field>
  );

  return (
    <Modal
      open
      title={user === null ? "New user" : `Edit ${user.username}`}
      onClose={onClose}
      width={640}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button type="submit" form={formId} className="btn btn-primary" disabled={saving}>
            {saving ? <Spinner size={14} label="Saving" /> : null}
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
                <input
                  {...control}
                  type="password"
                  className="input"
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
          <legend>Usage limits</legend>
          <div className="admin-form-grid">
            {limitField("monthly_token_limit", "Monthly token limit", "Chat tokens per calendar month (UTC). 0 = unlimited.")}
            {limitField(
              "monthly_image_limit",
              "Monthly image limit",
              "Images the user can generate per calendar month (UTC). 0 = unlimited.",
            )}
            {limitField("rate_limit_per_min", "Requests per minute", "0 = unlimited.")}
            {limitField("max_upload_mb", "Max file size (MB)", `1 to ${config.max_upload_mb_hard} MB per file.`)}
            {limitField("max_documents", "Max documents", "0 = unlimited.")}
            {limitField("max_storage_mb", "Max storage (MB)", "Total size of their uploads. 0 = unlimited.")}
          </div>
        </fieldset>

        <fieldset className="admin-form-section">
          <legend>Model access</legend>
          <SwitchField
            label="Restrict to selected models"
            hint={
              form.restrict_models
                ? "The user can only use the chat models checked below."
                : "The user can use every enabled chat model."
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
        </fieldset>

        {serverError ? (
          <div className="admin-notice admin-notice-danger" role="alert">
            <span className="admin-notice-text">{serverError}</span>
          </div>
        ) : null}
      </form>
    </Modal>
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
    return error ? <p className="error-text">{error}</p> : <p className="field-hint">Loading models…</p>;
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
      {noneSelected ? <p className="admin-text-warning">With no models selected, this user can't chat.</p> : null}
    </fieldset>
  );
}
