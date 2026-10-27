import { useId, type ReactNode } from "react";
import type { PublicPlan } from "../../api/types";
import { formatInr } from "../../lib/billing";
import { formatNumber } from "../../lib/format";
import { CheckIcon, ClockIcon, MinusIcon, SparkleIcon } from "../ui/icons";
import "./plan-card.css";

export type PlanEmphasis = "recommended" | "current" | "last";

const FLAGS: Record<PlanEmphasis, { label: string; icon: ReactNode; className: string }> = {
  recommended: { label: "Recommended", icon: <SparkleIcon size={13} />, className: "plans-flag" },
  current: { label: "Current plan", icon: <CheckIcon size={13} />, className: "plans-flag plans-flag-current" },
  last: { label: "Your last plan", icon: <ClockIcon size={13} />, className: "plans-flag plans-flag-current" },
};

/** The second line of a plugin's feature row. */
const PLUGIN_DETAIL: Record<string, string> = { gmail: "Search, read, draft and send" };

type Feature = { id: string; label: string; short: string; included: boolean; detail?: string; unavailable?: boolean };

/** The features in the fixed order of the visual brief (V7): chat, files, images, then every registered plugin. */
function featuresOf(plan: PublicPlan): Feature[] {
  return [
    {
      id: "chat",
      label: "Chat with your documents",
      short: "Chat",
      included: true,
      detail: "and the shared knowledge base",
    },
    {
      id: "files",
      label: "Files from chat",
      short: "Files",
      included: plan.features.files,
      detail: "PDF, Word, Excel and Markdown",
    },
    { id: "images", label: "Images from chat", short: "Images", included: plan.features.images },
    ...plan.plugins.map((plugin) => ({
      id: `plugin:${plugin.id}`,
      label: `${plugin.name} plugin`,
      short: plugin.name,
      included: plan.features.plugins.includes(plugin.id),
      detail: PLUGIN_DETAIL[plugin.id],
      unavailable: !plugin.available,
    })),
  ];
}

function FeatureRow({ feature }: { feature: Feature }) {
  if (feature.included && !feature.unavailable) {
    return (
      <li className="plans-feature">
        <CheckIcon />
        <span>
          <span className="plans-feature-long">
            {feature.label}
            {feature.detail ? <small>{feature.detail}</small> : null}
          </span>
          <span className="plans-feature-short">{feature.short}</span>
        </span>
      </li>
    );
  }
  if (feature.included) {
    return (
      <li className="plans-feature plans-feature-off">
        <MinusIcon />
        <span>
          <span className="plans-feature-long">{feature.short} (not available yet)</span>
          <span className="plans-feature-short">
            {feature.short}
            <span className="sr-only">, not available yet</span>
          </span>
        </span>
      </li>
    );
  }
  return (
    <li className="plans-feature plans-feature-off">
      <MinusIcon />
      <span>
        <span className="plans-feature-long">{feature.label}</span>
        <span className="plans-feature-short">{feature.short}</span>
      </span>
      <span className="plans-feature-tag" aria-hidden="true">
        Not included
      </span>
      <span className="sr-only">, not included</span>
    </li>
  );
}

function Limit({ term, value, detail }: { term: ReactNode; value: ReactNode; detail?: string }) {
  return (
    <div>
      <dt>{term}</dt>
      <dd>
        {value}
        {detail ? <small>{detail}</small> : null}
      </dd>
    </div>
  );
}

const amountOrUnlimited = (value: number, unit = "") => (value === 0 ? "Unlimited" : `${formatNumber(value)}${unit}`);

const namesOrCount = (names: string[], unit: string) =>
  names.length <= 2 ? names.join(", ") : `${names.length} ${unit}`;

/** "All models"; "Starter's, plus Claude Sonnet 5.5" when the list holds the next lower plan's whole list; else the
 * one or two names, or "5 models". */
function modelsLabel(plan: PublicPlan, lower: PublicPlan | null): string {
  if (plan.all_chat_models) return "All models";
  if (plan.chat_models.length === 0) return "None";
  if (lower && !lower.all_chat_models && lower.chat_models.length > 0) {
    const ids = new Set(plan.chat_models.map((model) => model.id));
    const lowerIds = new Set(lower.chat_models.map((model) => model.id));
    const added = plan.chat_models.filter((model) => !lowerIds.has(model.id)).map((model) => model.display_name);
    if (added.length > 0 && [...lowerIds].every((id) => ids.has(id))) {
      return `${lower.name}'s, plus ${namesOrCount(added, "more models")}`;
    }
  }
  return namesOrCount(plan.chat_models.map((model) => model.display_name), "models");
}

/** Wide: "Chat" · "About 1,600 answers" · "a month with Claude Haiku 5.5"; a phone's tile: "1,600" over "Answers". */
function chatLimit(plan: PublicPlan): { term: ReactNode; value: ReactNode; detail?: string } {
  const allowance = plan.limits.monthly_token_limit;
  if (allowance === 0) return { term: "Chat", value: "Unlimited" };
  const answers = plan.answers_estimate;
  const model = plan.default_chat_model?.display_name;
  return {
    term: (
      <>
        <span className="plans-limit-wide">Chat</span>
        <span className="plans-limit-narrow">{answers === null ? "Chat tokens" : "Answers"}</span>
      </>
    ),
    value: (
      <>
        {answers === null ? null : <span className="plans-limit-wide">About </span>}
        {formatNumber(answers ?? allowance)}
        <span className="plans-limit-wide">{answers === null ? " standard tokens" : " answers"}</span>
      </>
    ),
    detail: answers !== null && model ? `a month with ${model}` : "a month",
  };
}

type PlanCardProps = {
  plan: PublicPlan;
  /** The one emphasised card of a page, with its flag (V3). */
  emphasis?: PlanEmphasis | null;
  /** State badges under the description. */
  badges?: ReactNode;
  /** The options (buttons, the alternative link, reasons); none on the public pricing page. */
  actions?: ReactNode;
  headingLevel?: 2 | 3;
  /** The next lower plan on the page, for the Models row's "<Lower plan>'s, plus …". */
  lower?: PublicPlan | null;
};

/** One plan as a list item of a `.plans-grid` list (visual brief 3.2): name, price, actions, features, limits. */
export default function PlanCard({
  plan,
  emphasis = null,
  badges,
  actions,
  headingLevel = 2,
  lower = null,
}: PlanCardProps) {
  const headingId = useId();
  const Heading = headingLevel === 2 ? "h2" : "h3";
  const Label = headingLevel === 2 ? "h3" : "h4";
  const flag = emphasis ? FLAGS[emphasis] : null;
  const chat = chatLimit(plan);
  const { limits } = plan;
  const className = ["plans-card", flag ? "plans-card-featured" : "", actions ? "" : "plans-card-static"]
    .filter(Boolean)
    .join(" ");
  return (
    <li className={className} aria-labelledby={headingId}>
      {flag ? (
        <span className={flag.className}>
          {flag.icon}
          {flag.label}
        </span>
      ) : null}
      <div className="plans-card-head">
        <Heading className="plans-card-name" id={headingId}>
          {plan.name}
        </Heading>
        {plan.description ? <p className="plans-card-desc">{plan.description}</p> : null}
        {badges ? <div className="plans-card-badges">{badges}</div> : null}
      </div>
      <div className="plans-price">
        <p className="sr-only">{formatInr(plan.price_paise).replace("₹", "")} rupees a month, for 30 days</p>
        <div className="plans-amount-row" aria-hidden="true">
          <span className="plans-amount">{formatInr(plan.price_paise)}</span>
          <span className="plans-per">/ month</span>
        </div>
        <p className="plans-price-note">30 days, paid in advance</p>
      </div>
      {actions ? <div className="plans-actions">{actions}</div> : null}
      <div className="plans-section">
        <Label className="plans-label">Included</Label>
        <ul className="plans-features">
          {featuresOf(plan).map((feature) => (
            <FeatureRow key={feature.id} feature={feature} />
          ))}
        </ul>
      </div>
      <div className="plans-section">
        <Label className="plans-label">Limits</Label>
        <dl className="plans-limits">
          <Limit term={chat.term} value={chat.value} detail={chat.detail} />
          <Limit
            term="Documents"
            value={amountOrUnlimited(limits.max_documents)}
            detail={`up to ${formatNumber(limits.max_upload_mb)} MB each`}
          />
          <Limit term="Storage" value={amountOrUnlimited(limits.max_storage_mb, " MB")} />
          {plan.features.images ? (
            <Limit
              term="Images"
              value={amountOrUnlimited(limits.monthly_image_limit)}
              detail={limits.monthly_image_limit === 0 ? undefined : "a month"}
            />
          ) : null}
          <Limit term="Models" value={modelsLabel(plan, lower)} />
        </dl>
      </div>
    </li>
  );
}

/** The cards while the plans load: three skeleton cards in the grid (visual brief 3.2). */
export function PlanGridSkeleton({ count = 3 }: { count?: number }) {
  return (
    <>
      <p className="sr-only" role="status">
        Loading plans…
      </p>
      <ul className="plans-grid" aria-label="Plans" aria-busy="true">
        {Array.from({ length: count }, (_, index) => (
          <li key={index} className="plans-card plans-card-skeleton" aria-hidden="true">
            <div className="plans-card-head">
              <span className="ui-skeleton plans-skeleton-name" />
              <span className="ui-skeleton plans-skeleton-desc" />
              <span className="ui-skeleton plans-skeleton-desc-short" />
            </div>
            <div className="plans-price">
              <span className="ui-skeleton plans-skeleton-price" />
            </div>
            <div className="plans-actions">
              <span className="ui-skeleton plans-skeleton-button" />
            </div>
            <div className="plans-section">
              {[0, 1, 2, 3].map((line) => (
                <span key={line} className="ui-skeleton plans-skeleton-line" />
              ))}
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}
