import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";
import { publicApi } from "../../api/client";
import type { PublicSite } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import PolicyLinks from "../../components/billing/PolicyLinks";
import "./public.css";

/** Before the business details load (or when they can't): the app's name only. */
const FALLBACK_SITE: PublicSite = {
  business_name: "RAG Assistant",
  contact_email: null,
  contact_phone: null,
  address: null,
};

const SiteContext = createContext<PublicSite>(FALLBACK_SITE);

/** The business details of the billing settings (GET /public/site), for the page and its footer. */
export function usePublicSite(): PublicSite {
  return useContext(SiteContext);
}

const NAV = [
  { to: "/pricing", label: "Pricing" },
  { to: "/about", label: "About" },
  { to: "/contact", label: "Contact" },
];

/** "email · phone", or whichever is set. */
export function contactLine(site: PublicSite): string | null {
  return [site.contact_email, site.contact_phone].filter(Boolean).join(" · ") || null;
}

/**
 * The frame of the public pages (plans-billing.md 10; visual brief 11): a top bar with the business name, the nav and
 * Sign in (Open the app when signed in), the page, and a footer with the contact details and every policy link.
 */
export default function PublicLayout({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const [site, setSite] = useState<PublicSite>(FALLBACK_SITE);

  useEffect(() => {
    const controller = new AbortController();
    publicApi.site(controller.signal).then(setSite, () => {
      /* the fallback name stays */
    });
    return () => controller.abort();
  }, []);

  const contact = contactLine(site);
  return (
    <SiteContext.Provider value={site}>
      <div className="public-page">
        <header className="public-topbar">
          <div className="public-topbar-inner">
            <Link to="/pricing" className="ui-brand public-brand" aria-label={`${site.business_name}: pricing`}>
              <span className="ui-brand-mark" aria-hidden="true">
                R
              </span>
              <span className="ui-brand-name public-brand-name">{site.business_name}</span>
            </Link>
            <span className="spacer" />
            <nav className="public-nav" aria-label="Site">
              {NAV.map((item) => (
                <NavLink key={item.to} to={item.to}>
                  {item.label}
                </NavLink>
              ))}
            </nav>
            {user ? (
              <Link className="btn btn-sm" to="/">
                Open the app
              </Link>
            ) : (
              <Link className="btn btn-sm" to="/login">
                Sign in
              </Link>
            )}
          </div>
        </header>
        {children}
        <footer className="public-footer">
          <div className="public-footer-inner">
            <div className="public-footer-contact">
              <b>{site.business_name}</b>
              {contact ? <span>{contact}</span> : null}
              {site.address ? <span className="public-address">{site.address}</span> : null}
            </div>
            <div className="public-footer-links">
              <PolicyLinks />
              <p>Payments are processed by Razorpay.</p>
            </div>
          </div>
        </footer>
      </div>
    </SiteContext.Provider>
  );
}
