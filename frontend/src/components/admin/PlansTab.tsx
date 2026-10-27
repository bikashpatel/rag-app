import { useMemo, useState } from "react";
import type {
  AdminConfig,
  AdminModel,
  AdminPlan,
  AdminPlugin,
  BillingAdminConfig,
  PlanCostEstimate,
  PlanSaved,
} from "../../api/types";
import { formatInr } from "../../lib/billing";
import { formatCompact, formatNumber } from "../../lib/format";
import { AlertIcon, PlusIcon } from "../ui/icons";
import { BILLING_CONFIG_PATH, deleteResource } from "./adminApi";
import { percent, plural, roundedInr } from "./billingUi";
import ConfirmAction from "./ConfirmAction";
import FlashMessage from "./FlashMessage";
import { useFlash, useResource } from "./hooks";
import { answersFor, blendedPrice, HIGH_SHARE, planDefaultModel } from "./planCost";
import PlanFormModal from "./PlanFormModal";
import { EmptyState, RefreshError, ResourceGate } from "./StateViews";

const byRank = (plans: AdminPlan[]) => [...plans].sort((a, b) => a.rank - b.rank);

function savedMessage({ plan, applied_periods }: PlanSaved, created: boolean): string {
  if (created) return `Created ${plan.name}.`;
  const applied =
    applied_periods > 0 ? ` Also applied to ${plural(applied_periods, "current or scheduled period")}.` : "";
  return `Saved ${plan.name}.${applied}`;
}

export default function PlansTab() {
  const plans = useResource<AdminPlan[]>("/admin/plans", "Couldn't load the plans.");
  const billing = useResource<BillingAdminConfig>(BILLING_CONFIG_PATH, "Couldn't load the cost inputs.");
  const models = useResource<AdminModel[]>("/admin/models", "Couldn't load the model list.");
  const plugins = useResource<AdminPlugin[]>("/admin/plugins", "Couldn't load the plugins.");
  const config = useResource<AdminConfig>("/admin/config", "Couldn't load the server's upload cap.");
  const flash = useFlash();
  const [editing, setEditing] = useState<AdminPlan | "new" | null>(null);

  const pluginNames = useMemo(() => new Map((plugins.data ?? []).map((p) => [p.id, p.name])), [plugins.data]);
  const chatModels = useMemo(() => models.data?.filter((model) => model.kind === "chat") ?? null, [models.data]);
  const editor =
    billing.data && chatModels && plugins.data && config.data
      ? { inputs: billing.data.estimate, chatModels, plugins: plugins.data, hardCap: config.data.max_upload_mb_hard }
      : null;
  const setupError = [billing, models, plugins, config].find((resource) => resource.error && !resource.data);

  async function deletePlan(plan: AdminPlan) {
    await deleteResource(`/admin/plans/${plan.id}`);
    plans.setData((list) => list && list.filter((p) => p.id !== plan.id));
    flash.success(`Deleted ${plan.name}.`);
  }

  function handleSaved(saved: PlanSaved, created: boolean) {
    setEditing(null);
    plans.setData((list) => {
      const rest = (list ?? []).filter((p) => p.id !== saved.plan.id);
      const others = saved.plan.recommended ? rest.map((p) => ({ ...p, recommended: false })) : rest;
      return byRank([...others, saved.plan]);
    });
    flash.success(savedMessage(saved, created));
  }

  return (
    <section className="card admin-card" aria-labelledby="admin-plans-title">
      <div className="admin-card-header">
        <div className="admin-card-heading">
          <h2 id="admin-plans-title" className="admin-card-title">
            Plans
          </h2>
          <p className="admin-card-subtitle">
            What users can buy, by rank. Changes apply to new periods; current periods keep what was paid for unless you
            choose otherwise.
          </p>
        </div>
        <span className="spacer" />
        <button type="button" className="btn btn-primary" disabled={!editor} onClick={() => setEditing("new")}>
          <PlusIcon size={16} />
          New plan
        </button>
      </div>
      {setupError?.error ? <RefreshError message={setupError.error} onRetry={() => void setupError.reload()} /> : null}
      <FlashMessage flash={flash} />
      <ResourceGate resource={plans} loadingLabel="Loading plans">
        {(list) =>
          list.length === 0 ? (
            <EmptyState title="No plans">
              <p>Users can't pay until you create one.</p>
            </EmptyState>
          ) : (
            <>
              <div className="table-wrap">
                <table className="table admin-table">
                  <thead>
                    <tr>
                      <th scope="col">Plan</th>
                      <th scope="col">Status</th>
                      <th scope="col">Price</th>
                      <th scope="col">Users now · next · free</th>
                      <th scope="col">Features</th>
                      <th scope="col">Key limits</th>
                      <th scope="col">AI cost per period</th>
                      <th scope="col" className="admin-actions-cell">
                        <span className="sr-only">Actions</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {byRank(list).map((plan) => (
                      <PlanRow
                        key={plan.id}
                        plan={plan}
                        keyLimits={keyLimits(plan, chatModels, billing.data)}
                        pluginNames={pluginNames}
                        canEdit={editor !== null}
                        onEdit={() => setEditing(plan)}
                        onDelete={() => deletePlan(plan)}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="admin-card-footnote">
                Cost estimates use the models' prices and ₹{formatNumber(billing.data?.estimate.usd_inr ?? 88)} to the
                dollar, against what you keep after Razorpay's fee. A plan that has been bought can't be deleted; retire
                it instead.
              </p>
            </>
          )
        }
      </ResourceGate>
      {editing && editor ? (
        <PlanFormModal
          plan={editing === "new" ? null : editing}
          plans={plans.data ?? []}
          {...editor}
          onClose={() => setEditing(null)}
          onSaved={handleSaved}
        />
      ) : null}
    </section>
  );
}

/** "≈1,600 answers with Claude Haiku 5.5 · 50 docs · 200 MB · 2 models". */
function keyLimits(plan: AdminPlan, chatModels: AdminModel[] | null, billing: BillingAdminConfig | null): string {
  const { limits } = plan;
  const enabled = chatModels?.filter((model) => model.enabled) ?? [];
  const model = planDefaultModel(enabled, plan.chat_model_ids, plan.default_chat_model_id);
  const answers =
    model && billing
      ? answersFor(limits.monthly_token_limit, blendedPrice(model), plan.features, billing.estimate)
      : null;
  const chat =
    limits.monthly_token_limit === 0
      ? "Unlimited chat"
      : answers !== null && model
        ? `≈${formatNumber(answers)} answers with ${model.display_name}`
        : `${formatCompact(limits.monthly_token_limit)} tokens`;
  const docs = limits.max_documents ? `${formatNumber(limits.max_documents)} docs` : "Unlimited docs";
  const storage = limits.max_storage_mb ? `${formatNumber(limits.max_storage_mb)} MB` : "Unlimited storage";
  const models = plan.chat_model_ids === null ? "all models" : plural(plan.chat_model_ids.length, "model");
  return [chat, docs, storage, models].join(" · ");
}

type PlanRowProps = {
  plan: AdminPlan;
  keyLimits: string;
  pluginNames: Map<string, string>;
  canEdit: boolean;
  onEdit: () => void;
  onDelete: () => Promise<void>;
};

function PlanRow({ plan, keyLimits: limits, pluginNames, canEdit, onEdit, onDelete }: PlanRowProps) {
  const chips = [
    plan.features.files ? "Files" : null,
    plan.features.images ? "Images" : null,
    ...plan.features.plugins.map((id) => pluginNames.get(id) ?? id),
  ].filter((chip): chip is string => chip !== null);
  return (
    <tr>
      <td>
        <div className="admin-plan-name">
          <b>{plan.name}</b>
          <span className="admin-code">{plan.code}</span>
        </div>
      </td>
      <td>
        <span className="admin-badges">
          {plan.status === "active" ? (
            <span className="badge badge-success">Active</span>
          ) : (
            <span className="badge badge-neutral">
              <span className="ui-dashed" aria-hidden="true" />
              Retired
            </span>
          )}
          {plan.recommended ? <span className="badge badge-accent">Recommended</span> : null}
        </span>
      </td>
      <td className="admin-nowrap">
        <b className="admin-strong">{formatInr(plan.price_paise)}</b>
        <div className="admin-subtext">per 30 days</div>
      </td>
      <td
        className="admin-nowrap"
        title={`${plural(plan.current_users, "user")} now, ${plural(plan.scheduled_periods, "scheduled period")}, ${plural(plan.complimentary_users, "complimentary user")} with no end date`}
      >
        {formatNumber(plan.current_users)} · {formatNumber(plan.scheduled_periods)} next ·{" "}
        {formatNumber(plan.complimentary_users)} free
      </td>
      <td>
        {chips.length ? (
          <span className="admin-feature-chips">
            {chips.map((chip) => (
              <span key={chip} className="admin-feature-chip">
                {chip}
              </span>
            ))}
          </span>
        ) : (
          <span className="admin-subtext">Chat only</span>
        )}
      </td>
      <td className="admin-subtext admin-key-limits">{limits}</td>
      <td>
        <CostCell cost={plan.cost} />
      </td>
      <td className="admin-actions-cell">
        <span className="admin-actions">
          <ConfirmAction
            label="Delete"
            ariaLabel={`Delete ${plan.name}`}
            message={
              <>
                Delete <strong>{plan.name}</strong>? Nobody has used it yet.
              </>
            }
            confirmLabel="Delete plan"
            onConfirm={onDelete}
            disabled={plan.used}
            title={plan.used ? "Used: retire it instead" : undefined}
          >
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              aria-label={`Edit ${plan.name}`}
              disabled={!canEdit}
              onClick={onEdit}
            >
              Edit
            </button>
          </ConfirmAction>
          {plan.used ? (
            <span className="sr-only">{plan.name} has been used: retire it instead of deleting it.</span>
          ) : null}
        </span>
      </td>
    </tr>
  );
}

/** Worst case and typical AI cost of a period, with a meter of the worst case's share of what the owner keeps. */
function CostCell({ cost }: { cost: PlanCostEstimate }) {
  const noCeiling = cost.unlimited.length > 0;
  const high = !noCeiling && cost.worst_share >= HIGH_SHARE;
  const worstShare = percent(cost.worst_share);
  return (
    <div className="admin-cost">
      {high ? (
        <span className="admin-cost-state">
          <AlertIcon size={13} />
          High
        </span>
      ) : null}
      <div className={high || noCeiling ? "admin-cost-line admin-cost-line-high" : "admin-cost-line"}>
        <span>Worst case</span>
        <b>{noCeiling ? "No ceiling" : `${roundedInr(cost.worst_case_paise)} · ${worstShare}`}</b>
      </div>
      {noCeiling ? null : (
        <span
          className={high ? "ui-meter ui-meter-warning" : "ui-meter"}
          role="meter"
          aria-label="Worst case"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.min(100, Math.round(cost.worst_share * 100))}
          aria-valuetext={`${worstShare} of what you keep`}
        >
          <span style={{ width: `${Math.min(100, cost.worst_share * 100)}%` }} />
        </span>
      )}
      <div className={high ? "admin-cost-line admin-cost-line-high" : "admin-cost-line"}>
        <span>Typical</span>
        <b>
          {roundedInr(cost.typical_paise)} · {percent(cost.typical_share)}
        </b>
      </div>
    </div>
  );
}
