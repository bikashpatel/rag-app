import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { billingApi } from "../../api/client";
import type { PlanAccess } from "../../api/types";
import { formatInr } from "../../lib/billing";
import { ClockIcon } from "../ui/icons";
import "./billing.css";
import { istDayMonth, renewalOf, renewPath } from "./planText";

type PlanEndedPanelProps = {
  plan: PlanAccess;
  /** The unsent message was kept for when the plan is renewed. */
  draftKept: boolean;
};

/**
 * The composer's place when the plan ends on an open chat (8.7, visual 7.7): the page stays, the heading takes focus,
 * and Renew goes to the checkout of the last plan when it can be renewed.
 */
export default function PlanEndedPanel({ plan, draftKept }: PlanEndedPanelProps) {
  const titleId = useId();
  const headingRef = useRef<HTMLHeadingElement>(null);
  /** The renewal's price; null when the last plan can't be renewed (retired, or payments off); undefined until known. */
  const [renewPrice, setRenewPrice] = useState<number | null | undefined>(undefined);
  const code = plan.plan?.code ?? null;

  useEffect(() => headingRef.current?.focus(), []);

  useEffect(() => {
    if (code === null) return;
    const controller = new AbortController();
    billingApi.plans(controller.signal).then(
      (catalog) => {
        const option = renewalOf(catalog.plans.find((item) => item.code === code));
        setRenewPrice(option ? option.amount_paise : null);
      },
      () => undefined,
    );
    return () => controller.abort();
  }, [code]);

  const ended = plan.valid_through ?? plan.ended_at;
  const title =
    plan.plan === null
      ? "You don't have a plan"
      : `Your ${plan.plan.name} plan ended${ended ? ` on ${istDayMonth(ended)}` : ""}`;

  return (
    <section className="billing-ended" aria-labelledby={titleId}>
      <span className="billing-ended-icon" aria-hidden="true">
        <ClockIcon size={20} />
      </span>
      <div className="billing-ended-main">
        <h2 id={titleId} ref={headingRef} className="billing-ended-title" tabIndex={-1}>
          {title}
        </h2>
        <p className="billing-ended-text">
          {plan.plan === null || renewPrice === null ? "Choose a plan to continue." : "Renew to continue."} Your chats
          and documents are kept.
        </p>
        {draftKept ? (
          <p className="billing-ended-draft">Your unsent message is saved and comes back when you renew.</p>
        ) : null}
        <div className="billing-ended-actions">
          {code !== null && typeof renewPrice === "number" ? (
            <Link className="btn btn-primary btn-sm" to={renewPath(code)}>
              Renew {formatInr(renewPrice)}
            </Link>
          ) : null}
          <Link className="btn btn-sm" to="/plans">
            See plans
          </Link>
        </div>
      </div>
    </section>
  );
}
