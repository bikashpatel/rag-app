import { useId, type ReactNode } from "react";
import type { PlanModel, PublicPlan } from "../../api/types";
import { formatInr, formatRate } from "../../lib/billing";
import { formatNumber } from "../../lib/format";
import { CheckIcon } from "../ui/icons";
import "./plan-card.css";

/** Vision tokens of one simple scanned page (plans-billing-visual.md 4.4). */
const TOKENS_PER_SCANNED_PAGE = 3_000;

const PLUGIN_DETAIL: Record<string, string> = { gmail: "Search, read, draft and send" };

type Row = { key: string; label: string; detail?: string; cell: (plan: PublicPlan) => ReactNode };
type Group = { label: string; rows: Row[] };

function Yes() {
  return (
    <span className="plans-yes">
      <CheckIcon size={17} />
      <span className="sr-only">Included</span>
    </span>
  );
}

function No({ label = "Not included" }: { label?: string }) {
  return (
    <>
      <span className="plans-no" aria-hidden="true">
        —
      </span>
      <span className="sr-only">{label}</span>
    </>
  );
}

const amountOrUnlimited = (value: number, unit = "") => (value === 0 ? "Unlimited" : `${formatNumber(value)}${unit}`);

/** Rounded to two significant figures, at least 1: "About 50", "About 170". */
function aboutPages(tokens: number): string {
  const pages = tokens / TOKENS_PER_SCANNED_PAGE;
  const scale = 10 ** Math.max(0, Math.floor(Math.log10(Math.max(pages, 1))) - 1);
  return `About ${formatNumber(Math.max(1, Math.round(pages / scale) * scale))}`;
}

/** Every chat model of any plan, once, by name (which leads with the provider's brand). */
function allModels(plans: PublicPlan[]): PlanModel[] {
  const byId = new Map<number, PlanModel>();
  plans.forEach((plan) => plan.chat_models.forEach((model) => byId.has(model.id) || byId.set(model.id, model)));
  return [...byId.values()].sort((a, b) => a.display_name.localeCompare(b.display_name, undefined, { numeric: true }));
}

/** "Counted at each model's rate: Claude Haiku 5.5 ×0.05, GPT-5.5 ×2.7" (the cheapest and the dearest). */
function rateDetail(models: PlanModel[]): string {
  const rated = models.filter((model) => formatRate(model.allowance_rate) !== null);
  if (rated.length === 0) return "Standard tokens";
  const byRate = [...rated].sort((a, b) => (a.allowance_rate ?? 0) - (b.allowance_rate ?? 0));
  const ends = [...new Set([byRate[0], byRate[byRate.length - 1]])];
  const rates = ends.map((model) => `${model.display_name} ${formatRate(model.allowance_rate)}`);
  return `Counted at each model's rate: ${rates.join(", ")}`;
}

function groupsOf(plans: PublicPlan[]): Group[] {
  const models = allModels(plans);
  const plugins = plans[0]?.plugins ?? [];
  return [
    {
      label: "Features",
      rows: [
        { key: "chat", label: "Chat with your documents", cell: () => <Yes /> },
        {
          key: "files",
          label: "Files from chat",
          detail: "PDF, Word, Excel, Markdown",
          cell: (plan) => (plan.features.files ? <Yes /> : <No />),
        },
        { key: "images", label: "Images from chat", cell: (plan) => (plan.features.images ? <Yes /> : <No />) },
        ...plugins.map((plugin) => ({
          key: `plugin:${plugin.id}`,
          label: `${plugin.name} plugin`,
          detail: PLUGIN_DETAIL[plugin.id],
          cell: (plan: PublicPlan) => {
            if (!plan.features.plugins.includes(plugin.id)) return <No />;
            return plugin.available ? <Yes /> : "Not available yet";
          },
        })),
      ],
    },
    {
      label: "Models",
      rows: [
        ...models.map((model) => ({
          key: `model:${model.id}`,
          label: model.display_name,
          cell: (plan: PublicPlan) => {
            const offered = plan.chat_models.find((item) => item.id === model.id);
            if (!offered) return <No />;
            if (plan.limits.monthly_token_limit === 0) return "Unlimited";
            return offered.answers === null ? <Yes /> : `About ${formatNumber(offered.answers)} answers`;
          },
        })),
        {
          key: "default-model",
          label: "New chats start with",
          cell: (plan: PublicPlan) => plan.default_chat_model?.display_name ?? <No label="None" />,
        },
      ],
    },
    {
      label: "Limits per 30 days",
      rows: [
        {
          key: "allowance",
          label: "Chat allowance",
          detail: rateDetail(models),
          cell: (plan) => amountOrUnlimited(plan.limits.monthly_token_limit),
        },
        {
          key: "image-limit",
          label: "Images",
          cell: (plan) => (plan.features.images ? amountOrUnlimited(plan.limits.monthly_image_limit) : <No />),
        },
        {
          key: "vision",
          label: "Scanned PDF pages read",
          cell: (plan) => {
            const tokens = plan.limits.vision_monthly_token_limit;
            return tokens === 0 ? "Unlimited" : aboutPages(tokens);
          },
        },
        { key: "rate", label: "Requests a minute", cell: (plan) => amountOrUnlimited(plan.limits.rate_limit_per_min) },
      ],
    },
    {
      label: "Documents",
      rows: [
        { key: "documents", label: "Documents", cell: (plan) => amountOrUnlimited(plan.limits.max_documents) },
        { key: "storage", label: "Storage", cell: (plan) => amountOrUnlimited(plan.limits.max_storage_mb, " MB") },
        { key: "largest-file", label: "Largest file", cell: (plan) => `${formatNumber(plan.limits.max_upload_mb)} MB` },
      ],
    },
  ];
}

type PlanCompareTableProps = {
  plans: PublicPlan[];
  /** The emphasised plan's column is tinted (the card with the flag). */
  emphasisedCode?: string | null;
};

/** "Compare plans" under the cards (visual brief 4.4): features, models with answers, limits, documents. */
export default function PlanCompareTable({ plans, emphasisedCode = null }: PlanCompareTableProps) {
  const headingId = useId();
  const featured = (plan: PublicPlan) => (plan.code === emphasisedCode ? "is-featured" : undefined);
  return (
    <section className="plans-compare">
      <h2 className="plans-h2" id={headingId}>
        Compare plans
      </h2>
      <p className="plans-h2-sub">Every plan includes the shared knowledge base and keeps your chats and documents.</p>
      <div className="plans-table-wrap" role="region" aria-labelledby={headingId} tabIndex={0}>
        <table className="plans-table">
          <caption className="sr-only">What each plan includes</caption>
          <colgroup>
            <col />
            {plans.map((plan) => (
              <col key={plan.code} />
            ))}
          </colgroup>
          <thead>
            <tr>
              <th scope="col">
                <span className="sr-only">Feature</span>
              </th>
              {plans.map((plan) => (
                <th key={plan.code} scope="col" className={featured(plan)}>
                  <span className="plans-table-plan">{plan.name}</span>
                  <span className="plans-table-price">{formatInr(plan.price_paise)} / month</span>
                </th>
              ))}
            </tr>
          </thead>
          {groupsOf(plans).map((group) => (
            <tbody key={group.label}>
              <tr className="plans-table-group">
                <th scope="rowgroup" colSpan={plans.length + 1}>
                  {group.label}
                </th>
              </tr>
              {group.rows.map((row) => (
                <tr key={row.key}>
                  <th scope="row">
                    {row.label}
                    {row.detail ? <small>{row.detail}</small> : null}
                  </th>
                  {plans.map((plan) => (
                    <td key={plan.code} className={featured(plan)}>
                      {row.cell(plan)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          ))}
        </table>
      </div>
    </section>
  );
}
