import type { ReactElement } from "react";
import { Outlet, Route } from "react-router-dom";
import PolicyPage from "./PolicyPage";
import PricingPage from "./PricingPage";
import PublicLayout from "./PublicLayout";

const POLICIES = ["terms", "privacy", "refunds", "contact", "about", "shipping"] as const;

/**
 * The public pages (/pricing, /terms, …; plans-billing.md 10): <Route> elements, rendered inside <Routes>. One layout
 * route holds them, so the frame and the business details stay while moving between pages.
 */
export const publicRoutes: ReactElement[] = [
  <Route
    key="public"
    element={
      <PublicLayout>
        <Outlet />
      </PublicLayout>
    }
  >
    <Route path="/pricing" element={<PricingPage />} />
    {POLICIES.map((slug) => (
      <Route key={slug} path={`/${slug}`} element={<PolicyPage key={slug} slug={slug} />} />
    ))}
  </Route>,
];
