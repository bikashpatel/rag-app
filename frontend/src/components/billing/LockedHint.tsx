import type { ReactNode } from "react";
import { MenuLink } from "../ui/Menu";
import { ChevronRightIcon, LockIcon } from "../ui/icons";
import "./billing.css";

// A feature the plan doesn't include stays visible as an action to the plans page (plans-billing-visual.md 3.5, V13):
// the usual icon tile with a lock badge, "Included in <plan>", and an Upgrade pill. The badge and pill are decorative.

/** The 34 px menu icon tile with the lock badge at its corner. */
export function LockedIcon({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span className={className ? `ui-menu-icon billing-locked-icon ${className}` : "ui-menu-icon billing-locked-icon"}>
      {children}
      <span className="billing-lock-badge" aria-hidden="true">
        <LockIcon size={11} />
      </span>
    </span>
  );
}

/** "Upgrade ›", the plugins menu's "Connect ›" look. */
export function UpgradePill() {
  return (
    <span className="billing-upgrade-pill" aria-hidden="true">
      Upgrade
      <ChevronRightIcon size={15} />
    </span>
  );
}

/** "Included in Standard", with ". Opens the plans page." for screen readers. */
export function IncludedIn({ plan }: { plan: string }) {
  return (
    <>
      Included in {plan}
      <span className="sr-only">. Opens the plans page.</span>
    </>
  );
}

type LockedMenuItemProps = {
  /** The feature's usual icon. */
  icon: ReactNode;
  /** An extra class for the icon tile (Gmail's red). */
  iconClassName?: string;
  /** The cheapest plan that includes it. */
  plan: string;
  children: ReactNode;
};

/** A menu item for a feature outside the plan: activating it closes the menu and opens the plans page. */
export default function LockedMenuItem({ icon, iconClassName, plan, children }: LockedMenuItemProps) {
  return (
    <MenuLink
      to="/plans"
      className="billing-locked-item"
      icon={<LockedIcon className={iconClassName}>{icon}</LockedIcon>}
      description={<IncludedIn plan={plan} />}
      end={<UpgradePill />}
    >
      {children}
    </MenuLink>
  );
}
