import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { api } from "../../api/client";
import type { AdminModel, AdminModelCreate, AdminModelUpdate, ModelKind } from "../../api/types";
import Modal from "../ui/Modal";
import Spinner from "../ui/Spinner";
import { errorMessage } from "./adminApi";
import { Field, SwitchField } from "./Field";
import { FormValidator, numberText, type FieldErrors } from "./form";
import { AlertCircleIcon, AlertIcon } from "../ui/icons";

// Same rules as the backend schema.
const API_BASE_PATTERN = /^https?:\/\/[^\s\x00-\x1f\x7f/?#]+[^\s\x00-\x1f\x7f]*$/;
const API_KEY_ENV_PATTERN = /^[A-Z][A-Z0-9_]*_API_KEY$/;
const RESERVED_KEY_ENVS = new Set(["QDRANT_API_KEY"]);
const OLLAMA_API_BASE = "http://host.docker.internal:11434";

const EXAMPLES: Record<ModelKind, { model: string; apiBase?: string }[]> = {
  chat: [
    { model: "openai/gpt-5.4-mini" },
    { model: "anthropic/claude-sonnet-5-5" },
    { model: "gemini/gemini-2.5-flash" },
    { model: "ollama/llama3.1", apiBase: OLLAMA_API_BASE },
  ],
  embedding: [{ model: "openai/text-embedding-3-small" }, { model: "ollama/nomic-embed-text", apiBase: OLLAMA_API_BASE }],
  vision: [
    { model: "anthropic/claude-sonnet-5-5" },
    { model: "openai/gpt-5.4-mini" },
    { model: "gemini/gemini-2.5-flash" },
    { model: "ollama_chat/llama3.2-vision", apiBase: OLLAMA_API_BASE },
  ],
  image: [{ model: "openai/gpt-image-2" }],
};

const KINDS: Record<ModelKind, { label: string; namePlaceholder: string; switchLabel: string }> = {
  chat: { label: "Chat", namePlaceholder: "e.g. GPT-5.4 mini", switchLabel: "Default chat model" },
  embedding: {
    label: "Embedding",
    namePlaceholder: "e.g. OpenAI text-embedding-3-small",
    switchLabel: "Active embedding model",
  },
  vision: { label: "Vision", namePlaceholder: "e.g. Claude Sonnet 5.5 (vision)", switchLabel: "Active vision model" },
  image: { label: "Image", namePlaceholder: "e.g. GPT Image 2", switchLabel: "Active image model" },
};

const MODEL_HINT = "provider/model, exactly as LiteLLM names it. Ollama runs on the host, so give it the API base below.";
const VISION_MODEL_HINT =
  "provider/model, exactly as LiteLLM names it; the model must accept images (Test checks it). Ollama runs on the host, so give it the API base below.";
const IMAGE_MODEL_HINT =
  "OpenAI (openai/…) or Azure OpenAI (azure/<deployment>) GPT image models only; the server checks the name.";
const MODEL_HINTS: Record<ModelKind, string> = {
  chat: MODEL_HINT,
  embedding: MODEL_HINT,
  vision: VISION_MODEL_HINT,
  image: IMAGE_MODEL_HINT,
};

type TextKey =
  | "display_name"
  | "litellm_model"
  | "api_base"
  | "api_key_env"
  | "max_output_tokens"
  | "temperature"
  | "input_cost_per_mtok"
  | "output_cost_per_mtok";

type FormState = Record<TextKey, string> & { kind: ModelKind; enabled: boolean; is_default: boolean };

/** Everything the form sends except the create-only `kind` and the separately handled `is_default`. */
type ModelFields = Omit<AdminModelCreate, "kind" | "is_default">;

type TextFieldOptions = { placeholder?: string; mono?: boolean; inputMode?: "numeric" | "decimal" };

function initialState(model: AdminModel | null, kind: ModelKind): FormState {
  return {
    kind: model?.kind ?? kind,
    display_name: model?.display_name ?? "",
    litellm_model: model?.litellm_model ?? "",
    api_base: model?.api_base ?? "",
    api_key_env: model?.api_key_env ?? "",
    max_output_tokens: numberText(model?.max_output_tokens),
    temperature: numberText(model?.temperature),
    input_cost_per_mtok: numberText(model?.input_cost_per_mtok),
    output_cost_per_mtok: numberText(model?.output_cost_per_mtok),
    enabled: model?.enabled ?? true,
    is_default: model?.is_default ?? false,
  };
}

/** Hint under the Default / Active switch. `locked`: the model already is the default of its kind. */
function defaultSwitchHint(kind: ModelKind, locked: boolean, enabled: boolean, selected: boolean): string {
  if (kind === "chat") {
    if (locked) return "This is the default. Make another model the default to change it.";
    return enabled ? "Preselected for new chats." : "Enable the model to make it the default.";
  }
  if (locked && !enabled) return "Selected as the active model; it takes effect once the model is enabled.";
  if (!enabled) return "Enable the model to make it active.";
  if (kind === "vision") {
    return locked
      ? "This is the active vision model. Make another vision model active, or disable this one to turn image reading off."
      : "Turn on to read scanned pages and images in new PDF uploads with this model.";
  }
  if (kind === "image") {
    return locked
      ? "This is the active image model. Make another image model active, or disable this one to turn image generation off."
      : "Turn on to generate images in chat with this model.";
  }
  if (locked) return "This is the active model. Make another embedding model active to change it.";
  return selected
    ? "Existing documents must be re-indexed after switching; search skips them until then."
    : "Turn on to index documents and search queries with this model.";
}

type ModelFormModalProps = {
  /** null = add a new model. */
  model: AdminModel | null;
  /** Preselected kind when adding. */
  kind: ModelKind;
  onClose: () => void;
  onSaved: (model: AdminModel, created: boolean) => void;
};

export default function ModelFormModal({ model, kind, onClose, onSaved }: ModelFormModalProps) {
  const formId = useId();
  const formRef = useRef<HTMLFormElement>(null);
  const [form, setForm] = useState(() => initialState(model, kind));
  const [errors, setErrors] = useState<FieldErrors<TextKey>>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [invalidSubmits, setInvalidSubmits] = useState(0);

  const creating = model === null;
  const isChat = form.kind === "chat";
  /** Chat and vision models write text: they get the output limit and temperature. */
  const writesText = form.kind === "chat" || form.kind === "vision";
  /** Every kind but embedding has an output price (image models: image tokens). */
  const pricesOutput = form.kind !== "embedding";
  const isImage = form.kind === "image";
  const defaultLocked = model?.is_default ?? false;
  const enabledLocked = model !== null && model.kind === "embedding" && model.is_default && model.enabled;
  const indexInvalidated =
    model !== null &&
    model.kind === "embedding" &&
    (form.litellm_model.trim() !== model.litellm_model || (form.api_base.trim() || null) !== model.api_base);

  useEffect(() => {
    if (invalidSubmits > 0) formRef.current?.querySelector<HTMLElement>("[aria-invalid='true']")?.focus();
  }, [invalidSubmits]);

  function update<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((current) => ({ ...current, [key]: value }));
    setErrors((current) => (current[key as TextKey] ? { ...current, [key]: undefined } : current));
  }

  function applyExample(example: { model: string; apiBase?: string }) {
    update("litellm_model", example.model);
    if (example.apiBase && !form.api_base.trim()) update("api_base", example.apiBase);
  }

  function validate(): ModelFields | null {
    const v = new FormValidator<TextKey>();
    const displayName = v.text("display_name", form.display_name, 1, 100);
    const litellmModel = v.text("litellm_model", form.litellm_model, 1, 200);
    if (/\s/.test(litellmModel)) v.fail("litellm_model", "Must not contain spaces.");
    const apiBase = v.optionalText("api_base", form.api_base, 500);
    if (apiBase && !API_BASE_PATTERN.test(apiBase)) v.fail("api_base", "Enter a full http:// or https:// URL.");
    const apiKeyEnv = v.optionalText("api_key_env", form.api_key_env, 100);
    if (apiKeyEnv && !API_KEY_ENV_PATTERN.test(apiKeyEnv)) {
      v.fail("api_key_env", "Use capital letters, digits and underscores, ending in _API_KEY.");
    } else if (apiKeyEnv && RESERVED_KEY_ENVS.has(apiKeyEnv)) {
      v.fail("api_key_env", `${apiKeyEnv} is reserved for the app's own infrastructure.`);
    }
    const fields: ModelFields = {
      display_name: displayName,
      litellm_model: litellmModel,
      api_base: apiBase,
      api_key_env: apiKeyEnv,
      enabled: form.enabled,
      input_cost_per_mtok: v.optionalNumber("input_cost_per_mtok", form.input_cost_per_mtok, 0, Infinity),
      ...(writesText
        ? {
            max_output_tokens: v.optionalNumber("max_output_tokens", form.max_output_tokens, 1, 1_000_000, true),
            temperature: v.optionalNumber("temperature", form.temperature, 0, 2),
          }
        : {}),
      ...(pricesOutput
        ? { output_cost_per_mtok: v.optionalNumber("output_cost_per_mtok", form.output_cost_per_mtok, 0, Infinity) }
        : {}),
    };
    setErrors(v.errors);
    if (!v.valid) {
      setInvalidSubmits((n) => n + 1);
      return null;
    }
    return fields;
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (saving) return;
    setServerError(null);
    const fields = validate();
    if (!fields) return;
    const makeDefault = form.enabled && form.is_default;
    setSaving(true);
    try {
      const saved =
        model === null
          ? await api.post<AdminModel>("/admin/models", {
              ...fields,
              kind: form.kind,
              is_default: makeDefault,
            } satisfies AdminModelCreate)
          : await api.patch<AdminModel>(`/admin/models/${model.id}`, {
              ...fields,
              ...(makeDefault && !model.is_default ? { is_default: true } : {}),
            } satisfies AdminModelUpdate);
      onSaved(saved, creating);
    } catch (err) {
      setServerError(errorMessage(err, "Couldn't save the model. Please try again."));
      setSaving(false);
    }
  }

  const textField = (key: TextKey, label: string, hint: string, options: TextFieldOptions = {}) => (
    <Field label={label} hint={hint} error={errors[key]}>
      {(control) => (
        <input
          {...control}
          className={options.mono ? "input admin-mono" : "input"}
          inputMode={options.inputMode}
          autoComplete="off"
          spellCheck={false}
          placeholder={options.placeholder}
          value={form[key]}
          onChange={(e) => update(key, e.target.value)}
        />
      )}
    </Field>
  );

  const defaultHint = defaultSwitchHint(form.kind, defaultLocked, form.enabled, form.is_default);

  return (
    <Modal
      open
      title={model === null ? "Add model" : `Edit ${model.display_name}`}
      onClose={onClose}
      width={640}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button type="submit" form={formId} className="btn btn-primary" disabled={saving}>
            {saving ? <Spinner size={14} label="Saving" /> : null}
            {creating ? "Add model" : "Save changes"}
          </button>
        </>
      }
    >
      <form id={formId} ref={formRef} className="admin-form" onSubmit={onSubmit} noValidate>
        {creating ? (
          <fieldset className="admin-segmented">
            <legend className="field-label">Kind</legend>
            <div className="ui-segmented admin-segmented-options">
              {(Object.keys(KINDS) as ModelKind[]).map((option) => (
                <label key={option} className="ui-segment">
                  <input
                    type="radio"
                    name={`${formId}-kind`}
                    value={option}
                    checked={form.kind === option}
                    onChange={() => update("kind", option)}
                  />
                  <span>{KINDS[option].label}</span>
                </label>
              ))}
            </div>
          </fieldset>
        ) : null}

        {textField(
          "display_name",
          "Display name",
          isChat ? "Shown to users in the model picker." : "Shown in the admin pages.",
          { placeholder: KINDS[form.kind].namePlaceholder },
        )}

        <div className="stack admin-gap-sm">
          {textField("litellm_model", "LiteLLM model", MODEL_HINTS[form.kind], {
            placeholder: EXAMPLES[form.kind][0].model,
            mono: true,
          })}
          <div className="admin-examples">
            <span className="field-hint">Examples:</span>
            {EXAMPLES[form.kind].map((example) => (
              <button
                key={example.model}
                type="button"
                className="admin-chip"
                onClick={() => applyExample(example)}
                title={example.apiBase ? `Also sets the API base to ${example.apiBase}` : undefined}
              >
                {example.model}
              </button>
            ))}
          </div>
        </div>

        <div className="admin-form-grid">
          {textField("api_base", "API base (optional)", "For self-hosted or proxy endpoints.", {
            placeholder: isImage ? "Provider default" : OLLAMA_API_BASE,
            mono: true,
          })}
          {textField(
            "api_key_env",
            "API key env var (optional)",
            "Name of a server env var holding the key, ending in _API_KEY. Blank = the provider's default key, or no key when an API base is set.",
            { placeholder: isImage ? "AZURE_API_KEY" : "GROQ_API_KEY", mono: true },
          )}
          {writesText
            ? textField(
                "max_output_tokens",
                "Max output tokens",
                form.kind === "vision"
                  ? "Per page, thinking included. Blank = server default (8192)."
                  : "Blank = server default.",
                { placeholder: "Server default", inputMode: "numeric" },
              )
            : null}
          {writesText
            ? textField(
                "temperature",
                "Temperature",
                "0 to 2. Blank = provider default. Claude 5.5 models reject a custom temperature.",
                { placeholder: "Provider default", inputMode: "decimal" },
              )
            : null}
          {textField(
            "input_cost_per_mtok",
            isImage
              ? "Prompt cost per 1M tokens (USD)"
              : pricesOutput
                ? "Input cost per 1M tokens (USD)"
                : "Cost per 1M tokens (USD)",
            "Blank = automatic, from LiteLLM's price list.",
            { placeholder: "Automatic", inputMode: "decimal" },
          )}
          {pricesOutput
            ? textField(
                "output_cost_per_mtok",
                isImage ? "Image output cost per 1M tokens (USD)" : "Output cost per 1M tokens (USD)",
                "Blank = automatic, from LiteLLM's price list.",
                { placeholder: "Automatic", inputMode: "decimal" },
              )
            : null}
        </div>

        <div className="admin-form-section admin-switches">
          <SwitchField
            label="Enabled"
            hint={
              enabledLocked
                ? "Make another embedding model active before disabling this one."
                : isChat
                  ? "Disabled models are hidden from users."
                  : "Only an enabled model can be the active one."
            }
            checked={form.enabled}
            disabled={enabledLocked}
            onChange={(checked) => update("enabled", checked)}
          />
          <SwitchField
            label={KINDS[form.kind].switchLabel}
            hint={defaultHint}
            checked={defaultLocked || (form.enabled && form.is_default)}
            disabled={defaultLocked || !form.enabled}
            onChange={(checked) => update("is_default", checked)}
          />
        </div>

        {indexInvalidated ? (
          <div className="ui-notice ui-notice-warning">
            <AlertIcon size={15} />
            <span className="ui-notice-text">
              Changing the model or API base discards this model's search index. Documents indexed with it will need
              re-indexing.
            </span>
          </div>
        ) : null}

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
