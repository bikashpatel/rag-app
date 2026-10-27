import { Link } from "react-router-dom";
import "./billing.css";

const LINKS = [
  { to: "/pricing", label: "Pricing", compact: true },
  { to: "/terms", label: "Terms", compact: true },
  { to: "/privacy", label: "Privacy", compact: true },
  { to: "/refunds", label: "Cancellation and refunds", compact: true },
  { to: "/contact", label: "Contact", compact: true },
  { to: "/about", label: "About", compact: false },
  { to: "/shipping", label: "Shipping", compact: false },
];

/** The public policy pages (docs/briefs/plans-billing.md 10); `compact`: the login page's five. */
export default function PolicyLinks({ className, compact = false }: { className?: string; compact?: boolean }) {
  return (
    <nav className={className} aria-label="Policies">
      <ul className="billing-policy-links">
        {LINKS.filter((link) => !compact || link.compact).map((link) => (
          <li key={link.to}>
            <Link to={link.to}>{link.label}</Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
