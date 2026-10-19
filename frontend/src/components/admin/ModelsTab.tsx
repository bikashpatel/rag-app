import { useId, useState } from "react";
import { api } from "../../api/client";
import type { AdminModel, AdminModelUpdate, ModelKind, ModelTestResult } from "../../api/types";
import { formatNumber } from "../../lib/format";
import Spinner from "../ui/Spinner";
import { deleteResource, errorMessage } from "./adminApi";
import ConfirmAction from "./ConfirmAction";
import FlashMessage from "./FlashMessage";
import { useFlash, useResource, type Flash } from "./hooks";
import { AlertCircleIcon, CheckCircleIcon, CloseIcon, PlusIcon } from "../ui/icons";
import ModelFormModal from "./ModelFormModal";
import { EmptyState, ResourceGate } from "./StateViews";

type SectionInfo = { kind: ModelKind; title: string; description: string; emptyHint: string };

const SECTIONS: SectionInfo[] = [
  {
    kind: "chat",
    title: "Chat models",
    description: "Answer questions. Users choose among the enabled ones; the default is preselected.",
    emptyHint: "Add one so users can start chatting.",
  },
  {
    kind: "embedding",
    title: "Embedding models",
    description: "The active model embeds every document and search query.",
    emptyHint: "Add one and make it active so documents can be indexed.",
  },
  {
    kind: "vision",
    title: "Vision models",
    description:
      "Read PDF pages that have no text layer, and describe images such as charts, diagrams and photos on pages with text. The active one is used for new PDF uploads; page images are sent to its provider and cost tokens per page. Disable it to turn this off.",
    emptyHint: "Add one and make it active to read scanned PDFs and describe images in PDFs.",
  },
  {
    kind: "image",
    title: "Image models",
    description:
      "Generate images when users ask for one in chat (OpenAI or Azure OpenAI GPT image models). The active one is used; each image costs about $0.01 to $0.20 depending on quality and size and counts toward the user's monthly image limit. Disable it to turn image generation off.",
    emptyHint: "Add one and make it active so users can ask for images in chat.",
  },
];

/** Chat and vision models write text, so they have an output limit; embedding models have dimensions instead. */
function hasSizeColumn(kind: ModelKind): boolean {
  return kind !== "image";
}

/** Every kind but embedding has separate input and output prices (image models: prompt text and image tokens). */
function hasOutputPrice(kind: ModelKind): boolean {
  return kind !== "embedding";
}

function columnCount(kind: ModelKind): number {
  return hasSizeColumn(kind) ? 7 : 6;
}

/** Users pick among chat models (the default is preselected); exactly one embedding, vision or image model is used. */
function defaultLabel(kind: ModelKind): string {
  return kind === "chat" ? "Default" : "Active";
}

/** What the active model of a kind does, for the "Make active" button and its error. */
const ROLES: Record<ModelKind, string> = {
  chat: "default chat model",
  embedding: "active embedding model",
  vision: "active vision model",
  image: "active image model",
};

/** Insert or replace `saved`; a new default clears the flag on the other models of its kind. */
function applySaved(list: AdminModel[], saved: AdminModel): AdminModel[] {
  const next = list.map((model) =>
    model.id === saved.id
      ? saved
      : saved.is_default && model.kind === saved.kind && model.is_default
        ? { ...model, is_default: false }
        : model,
  );
  return list.some((model) => model.id === saved.id) ? next : [...next, saved];
}

function formatPrice(usdPerMillion: number | null): string {
  if (usdPerMillion === null) return "auto";
  return `$${usdPerMillion.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`;
}

type ModelActions = {
  edit: (model: AdminModel) => void;
  setEnabled: (model: AdminModel, enabled: boolean) => Promise<void>;
  /** Rejects on failure so the caller can show the error where the action started. */
  makeDefault: (model: AdminModel) => Promise<void>;
  remove: (model: AdminModel) => Promise<void>;
  tested: (model: AdminModel, result: ModelTestResult) => void;
};

export default function ModelsTab({ onChanged }: { onChanged: () => void }) {
  const models = useResource<AdminModel[]>("/admin/models", "Couldn't load models.");
  const chatFlash = useFlash();
  const embeddingFlash = useFlash();
  const visionFlash = useFlash();
  const imageFlash = useFlash();
  const [editing, setEditing] = useState<{ model: AdminModel | null; kind: ModelKind } | null>(null);
  const [toggling, setToggling] = useState<ReadonlySet<number>>(new Set());

  const flashes: Record<ModelKind, Flash> = {
    chat: chatFlash,
    embedding: embeddingFlash,
    vision: visionFlash,
    image: imageFlash,
  };
  const replace = (saved: AdminModel) => models.setData((list) => list && applySaved(list, saved));
  const patchLocal = (id: number, patch: Partial<AdminModel>) =>
    models.setData((list) => list && list.map((model) => (model.id === id ? { ...model, ...patch } : model)));

  const actions: ModelActions = {
    edit: (model) => setEditing({ model, kind: model.kind }),

    setEnabled: async (model, enabled) => {
      setToggling((ids) => new Set(ids).add(model.id));
      patchLocal(model.id, { enabled });
      try {
        replace(await api.patch<AdminModel>(`/admin/models/${model.id}`, { enabled } satisfies AdminModelUpdate));
        flashes[model.kind].success(
          model.is_default && !enabled && model.kind === "vision"
            ? `${model.display_name} disabled. Scanned PDFs can't be read until a vision model is active.`
            : model.is_default && !enabled && model.kind === "image"
              ? `${model.display_name} disabled. Image generation in chat is off until an image model is active.`
              : `${model.display_name} ${enabled ? "enabled" : "disabled"}.`,
        );
        if (model.kind === "vision" || model.kind === "image" || (model.kind === "embedding" && model.is_default)) {
          onChanged();
        }
      } catch (err) {
        patchLocal(model.id, { enabled: model.enabled });
        flashes[model.kind].error(
          errorMessage(err, `Couldn't ${enabled ? "enable" : "disable"} ${model.display_name}.`),
        );
      } finally {
        setToggling((ids) => {
          const next = new Set(ids);
          next.delete(model.id);
          return next;
        });
      }
    },

    makeDefault: async (model) => {
      replace(await api.patch<AdminModel>(`/admin/models/${model.id}`, { is_default: true } satisfies AdminModelUpdate));
      if (model.kind === "chat") {
        flashes[model.kind].success(`${model.display_name} is now the default chat model.`);
        return;
      }
      flashes[model.kind].success(
        model.kind === "embedding"
          ? `${model.display_name} is now the active embedding model. Re-index documents so search can find them.`
          : model.kind === "vision"
            ? `${model.display_name} is now the active vision model. New PDF uploads use it for scanned pages and images.`
            : `${model.display_name} is now the active image model.`,
      );
      onChanged();
    },

    remove: async (model) => {
      await deleteResource(`/admin/models/${model.id}`);
      models.setData((list) => list && list.filter((m) => m.id !== model.id));
      flashes[model.kind].success(`Deleted ${model.display_name}.`);
      onChanged();
    },

    tested: (model, result) => {
      if (result.ok && result.embedding_dim !== null) patchLocal(model.id, { embedding_dim: result.embedding_dim });
    },
  };

  function handleSaved(saved: AdminModel, created: boolean) {
    setEditing(null);
    replace(saved);
    flashes[saved.kind].success(created ? `Added ${saved.display_name}.` : `Saved changes to ${saved.display_name}.`);
    if (created || saved.kind !== "chat") onChanged();
  }

  return (
    <div className="admin-stack">
      <ResourceGate resource={models} loadingLabel="Loading models">
        {(list) =>
          SECTIONS.map((section) => (
            <ModelSection
              key={section.kind}
              {...section}
              models={list.filter((model) => model.kind === section.kind)}
              flash={flashes[section.kind]}
              toggling={toggling}
              actions={actions}
              onAdd={() => setEditing({ model: null, kind: section.kind })}
            />
          ))
        }
      </ResourceGate>
      {editing ? (
        <ModelFormModal
          model={editing.model}
          kind={editing.kind}
          onClose={() => setEditing(null)}
          onSaved={handleSaved}
        />
      ) : null}
    </div>
  );
}

type ModelSectionProps = SectionInfo & {
  models: AdminModel[];
  flash: Flash;
  toggling: ReadonlySet<number>;
  actions: ModelActions;
  onAdd: () => void;
};

function ModelSection({ kind, title, description, emptyHint, models, flash, toggling, actions, onAdd }: ModelSectionProps) {
  const titleId = `admin-models-${kind}-title`;
  return (
    <section className="card admin-card" aria-labelledby={titleId}>
      <div className="admin-card-header">
        <div className="admin-card-heading">
          <h2 id={titleId} className="admin-card-title">
            {title}
          </h2>
          <p className="admin-card-subtitle">{description}</p>
        </div>
        <span className="spacer" />
        <button type="button" className="btn btn-sm" onClick={onAdd}>
          <PlusIcon size={15} />
          Add {kind} model
        </button>
      </div>
      <FlashMessage flash={flash} />
      {models.length === 0 ? (
        <EmptyState title={`No ${kind} models yet.`}>
          <p className="muted">{emptyHint}</p>
        </EmptyState>
      ) : (
        <div className="table-wrap">
          <table className="table admin-table">
            <thead>
              <tr>
                <th scope="col">Model</th>
                <th scope="col">LiteLLM id</th>
                <th scope="col">Enabled</th>
                <th scope="col">{defaultLabel(kind)}</th>
                {hasSizeColumn(kind) ? (
                  <th scope="col" className="num">
                    {kind === "embedding" ? "Dimensions" : "Max output"}
                  </th>
                ) : null}
                <th scope="col" className="num">
                  {hasOutputPrice(kind) ? "Price / 1M in · out" : "Price / 1M"}
                </th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {models.map((model) => (
                <ModelRow
                  key={model.id}
                  model={model}
                  toggling={toggling.has(model.id)}
                  actions={actions}
                  flash={flash}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

type TestState =
  | { status: "running" }
  | { status: "done"; result: ModelTestResult }
  | { status: "failed"; message: string };

type ModelRowProps = {
  model: AdminModel;
  toggling: boolean;
  actions: ModelActions;
  flash: Flash;
};

function ModelRow({ model, toggling, actions, flash }: ModelRowProps) {
  const [test, setTest] = useState<TestState | null>(null);
  const lockHintId = useId();
  const name = model.display_name;
  // Only the active embedding model can't be disabled (the API refuses it); disabling the active vision model
  // simply turns PDF page reading off.
  const enabledLocked = model.kind === "embedding" && model.is_default && model.enabled;

  async function runTest() {
    setTest({ status: "running" });
    try {
      const result = await api.post<ModelTestResult>(`/admin/models/${model.id}/test`);
      setTest({ status: "done", result });
      actions.tested(model, result);
    } catch (err) {
      setTest({ status: "failed", message: errorMessage(err, "The test request failed.") });
    }
  }

  const apiDetails = [model.api_key_env, model.api_base].filter(Boolean).join(" · ");
  return (
    <>
      <tr>
        <td>
          <div className="admin-model-name">{name}</div>
          {apiDetails ? <div className="admin-subtext admin-mono">{apiDetails}</div> : null}
        </td>
        <td>
          <code className="admin-code">{model.litellm_model}</code>
        </td>
        <td>
          <label className="switch" title={enabledLocked ? "Make another embedding model active first" : undefined}>
            <input
              type="checkbox"
              role="switch"
              aria-label={`Enable ${name}`}
              aria-describedby={enabledLocked ? lockHintId : undefined}
              aria-busy={toggling || undefined}
              checked={model.enabled}
              disabled={toggling || enabledLocked}
              onChange={(e) => void actions.setEnabled(model, e.target.checked)}
            />
            <span aria-hidden="true" />
          </label>
          {enabledLocked ? (
            <span id={lockHintId} className="sr-only">
              The active embedding model can't be disabled. Make another embedding model active first.
            </span>
          ) : null}
        </td>
        <td>
          <DefaultControl model={model} actions={actions} flash={flash} />
        </td>
        {hasSizeColumn(model.kind) ? (
          <td className="num">
            {model.kind !== "embedding" ? (
              model.max_output_tokens === null ? (
                <span className="muted">default</span>
              ) : (
                formatNumber(model.max_output_tokens)
              )
            ) : model.embedding_dim === null ? (
              <span className="muted" title="Detected on first use, or run Test">
                —
              </span>
            ) : (
              formatNumber(model.embedding_dim)
            )}
          </td>
        ) : null}
        <td className="num admin-nowrap">
          <Price value={model.input_cost_per_mtok} />
          {hasOutputPrice(model.kind) ? (
            <>
              <span className="muted"> · </span>
              <Price value={model.output_cost_per_mtok} />
            </>
          ) : null}
        </td>
        <td className="admin-actions-cell">
          <span className="admin-actions">
            <ConfirmAction
              label="Delete"
              ariaLabel={`Delete ${name}`}
              message={
                <>
                  Delete <strong>{name}</strong>?{deleteNote(model)}
                </>
              }
              confirmLabel="Delete model"
              onConfirm={() => actions.remove(model)}
            >
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                aria-label={`Test ${name}`}
                disabled={test?.status === "running"}
                onClick={() => void runTest()}
              >
                Test
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                aria-label={`Edit ${name}`}
                onClick={() => actions.edit(model)}
              >
                Edit
              </button>
            </ConfirmAction>
          </span>
        </td>
      </tr>
      {test ? (
        <tr className="admin-subrow">
          <td colSpan={columnCount(model.kind)}>
            <TestResult name={name} kind={model.kind} state={test} onDismiss={() => setTest(null)} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

/** What else a delete does, as a sentence appended to the confirmation. */
function deleteNote(model: AdminModel): string {
  if (model.kind === "embedding") return " Its stored vectors are removed too.";
  if (model.kind === "vision" && model.is_default && model.enabled) {
    return " PDF page reading turns off until another vision model is active.";
  }
  if (model.kind === "image" && model.is_default && model.enabled) {
    return " Image generation in chat turns off until another image model is active.";
  }
  return "";
}

function Price({ value }: { value: number | null }) {
  return value === null ? <span className="muted">auto</span> : <>{formatPrice(value)}</>;
}

function DefaultControl({ model, actions, flash }: { model: AdminModel; actions: ModelActions; flash: Flash }) {
  const [busy, setBusy] = useState(false);
  const label = defaultLabel(model.kind);

  if (model.is_default) {
    return model.enabled ? (
      <span className={model.kind === "chat" ? "badge badge-accent" : "badge badge-success"}>{label}</span>
    ) : (
      <span className="badge badge-warning" title="Enable this model to use it">
        {label} · disabled
      </span>
    );
  }

  const disabledReason = model.enabled ? undefined : "Enable the model first";
  // Switching the embedding model leaves every document to be re-indexed, so it asks first.
  if (model.kind === "embedding") {
    return (
      <ConfirmAction
        label="Make active"
        ariaLabel={`Make ${model.display_name} the active embedding model`}
        tone="primary"
        disabled={!model.enabled}
        title={disabledReason}
        message="Index and search with this model from now on? Existing documents must then be re-indexed."
        confirmLabel="Make active"
        onConfirm={() => actions.makeDefault(model)}
      />
    );
  }

  // Chat, vision and image models switch without a confirmation: nothing already stored has to be redone.
  const role = ROLES[model.kind];
  async function makeDefault() {
    setBusy(true);
    try {
      await actions.makeDefault(model);
    } catch (err) {
      flash.error(errorMessage(err, `Couldn't make ${model.display_name} the ${role}.`));
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      type="button"
      className="btn btn-sm"
      aria-label={`Make ${model.display_name} the ${role}`}
      disabled={!model.enabled || busy}
      title={disabledReason}
      onClick={() => void makeDefault()}
    >
      {busy ? <Spinner size={12} label="Saving" /> : null}
      Make {label.toLowerCase()}
    </button>
  );
}

type TestResultProps = { name: string; kind: ModelKind; state: TestState; onDismiss: () => void };

function TestResult({ name, kind, state, onDismiss }: TestResultProps) {
  if (state.status === "running") {
    return (
      <div className="admin-test admin-test-running" role="status">
        <Spinner size={14} label="Testing" />
        <span className="admin-test-text">
          {kind === "image"
            ? `Generating a small test image with ${name}… (up to a minute, about $0.01)`
            : `Sending a short test ${kind === "vision" ? "image" : "request"} to ${name}…`}
        </span>
      </div>
    );
  }
  const result = state.status === "done" ? state.result : null;
  const ok = result?.ok ?? false;
  const message = result ? result.message : state.status === "failed" ? state.message : "";
  const facts = [
    result && result.latency_ms > 0 ? `${formatNumber(Math.round(result.latency_ms))} ms` : null,
    result?.embedding_dim ? `${formatNumber(result.embedding_dim)} dimensions` : null,
  ].filter(Boolean);
  return (
    <div className={`admin-test ${ok ? "admin-test-ok" : "admin-test-fail"}`} role={ok ? "status" : "alert"}>
      {ok ? <CheckCircleIcon size={16} /> : <AlertCircleIcon size={16} />}
      <span className="admin-test-text">
        <strong>{ok ? `${name} works` : `${name} failed`}</strong>
        {facts.length ? <span className="admin-test-facts"> · {facts.join(" · ")}</span> : null}
        {message ? <span className="admin-test-message">{message}</span> : null}
        {result?.image_data_url ? (
          <img className="admin-test-image" src={result.image_data_url} alt={`Test image from ${name}`} width={96} />
        ) : null}
      </span>
      <button type="button" className="btn btn-ghost btn-icon admin-test-close" aria-label="Dismiss test result" onClick={onDismiss}>
        <CloseIcon size={15} />
      </button>
    </div>
  );
}
