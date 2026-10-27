import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, publicApi } from "../../api/client";
import type { PublicPlan } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import type { LoginRedirectState } from "../../auth/loginRedirect";
import PlanCard, { PlanGridSkeleton } from "../../components/billing/PlanCard";
import PlanCompareTable from "../../components/billing/PlanCompareTable";
import { DraftBand, PageError, PolicyProse, usePageTitle, usePolicyDocument } from "./PolicyPage";
import { usePublicSite } from "./PublicLayout";

const TO_PLANS: LoginRedirectState = { from: { pathname: "/plans", search: "", hash: "" } };

type PlansState =
  { status: "loading" } | { status: "ready"; plans: PublicPlan[] } | { status: "error"; message: string };

/** /pricing: the intro from legal/pricing.md, the plan cards and the compare table (plans-billing.md 10). */
export default function PricingPage() {
  const { user } = useAuth();
  const site = usePublicSite();
  const intro = usePolicyDocument("pricing");
  const [plans, setPlans] = useState<PlansState>({ status: "loading" });
  const [round, setRound] = useState(0);
  const doc = intro.state.status === "ready" ? intro.state.doc : null;
  usePageTitle(doc?.title ?? "Pricing", site.business_name);

  useEffect(() => {
    const controller = new AbortController();
    setPlans({ status: "loading" });
    publicApi.plans(controller.signal).then(
      (list) => setPlans({ status: "ready", plans: list }),
      (err: unknown) => {
        if (!controller.signal.aborted)
          setPlans({ status: "error", message: err instanceof ApiError ? err.message : "Couldn't load the plans." });
      },
    );
    return () => controller.abort();
  }, [round]);
  const retry = useCallback(() => setRound((n) => n + 1), []);

  const recommended = plans.status === "ready" ? (plans.plans.find((plan) => plan.recommended)?.code ?? null) : null;
  return (
    <>
      {doc?.draft ? <DraftBand /> : null}
      <main className="public-main public-main-wide" id="main">
        <div className="public-intro">
          <h1 className="public-title">{doc?.title ?? "Pricing"}</h1>
          {doc?.updated ? <p className="public-updated">{doc.updated}</p> : null}
          {doc?.body ? <PolicyProse markdown={doc.body} /> : null}
        </div>
        {plans.status === "loading" ? <PlanGridSkeleton /> : null}
        {plans.status === "error" ? <PageError message={plans.message} onRetry={retry} /> : null}
        {plans.status === "ready" && plans.plans.length === 0 ? (
          <p className="public-state">
            No plans are available yet.
            {site.contact_email ? (
              <>
                {" "}
                Write to <a href={`mailto:${site.contact_email}`}>{site.contact_email}</a>.
              </>
            ) : null}
          </p>
        ) : null}
        {plans.status === "ready" && plans.plans.length > 0 ? (
          <>
            <ul className="plans-grid" aria-label="Plans">
              {plans.plans.map((plan, index) => (
                <PlanCard
                  key={plan.code}
                  plan={plan}
                  emphasis={plan.code === recommended ? "recommended" : null}
                  lower={plans.plans[index - 1] ?? null}
                />
              ))}
            </ul>
            <PlanCompareTable plans={plans.plans} emphasisedCode={recommended} />
          </>
        ) : null}
        <div className="public-pricing-foot">
          <p>
            Payments are processed by Razorpay. Prices are per 30 days, paid in advance; plans don't renew
            automatically.
          </p>
          {user ? (
            <Link className="btn btn-primary" to="/plans">
              Choose a plan
            </Link>
          ) : (
            <Link className="btn btn-primary" to="/login" state={TO_PLANS}>
              Sign in to choose a plan
            </Link>
          )}
        </div>
      </main>
    </>
  );
}
