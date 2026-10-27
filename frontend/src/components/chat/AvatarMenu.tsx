import { useId, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import type { Me, ThemePreference } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { formatCompact, formatNumber } from "../../lib/format";
import { THEMES } from "../../theme/appearance";
import { daysLeftBadge, expiringDays, planLine } from "../billing/planText";
import Avatar from "../ui/Avatar";
import Menu, { MenuGroup, MenuHead, MenuItem, MenuLink, MenuRadioItem, MenuSeparator } from "../ui/Menu";
import Meter, { meterState } from "../ui/Meter";
import {
  AlertCircleIcon,
  CardIcon,
  ChartIcon,
  LogoutIcon,
  MonitorIcon,
  MoonIcon,
  SettingsIcon,
  ShieldIcon,
  SunIcon,
} from "../ui/icons";

// The account menu behind the avatar at the top right (functional brief 8.5, visual spec 6.12; plans-billing.md 8.6):
// who is signed in and on which plan, this period's usage, Settings, Plan & billing, a quick theme switch, Admin and Log
// out. It replaces the sidebar footer (U18).

const THEME_ICONS: Record<ThemePreference, typeof SunIcon> = { system: MonitorIcon, light: SunIcon, dark: MoonIcon };

type AvatarMenuProps = {
  user: Me;
  /** A model the user can pick generates images: the images meter shows even without a limit. */
  imagesOffered: boolean;
  onOpenSettings: (tab: "general" | "billing" | "usage") => void;
  /** A theme save failed after the menu closed: the page shows the reason. */
  onPreferenceError: (message: string) => void;
  onLogout: () => void;
};

type Usage = {
  /** "this period" for users (an open-ended plan's period is the month); the admin's stays "this month" (8.9). */
  window: string;
  allowance: { used: number; limit: number };
  images: { used: number; limit: number } | null;
};

/** The meters, or null for a user without access (no usage block). */
function usageOf(user: Me, imagesOffered: boolean): Usage | null {
  if (!user.is_admin && !user.plan.access) return null;
  const imageLimit = user.is_admin ? 0 : user.limits.monthly_image_limit;
  const showImages = user.plan.features.images && (imageLimit > 0 || user.usage.images > 0 || imagesOffered);
  return {
    window: user.is_admin ? "this month" : "this period",
    allowance: { used: user.usage.chat_allowance_used, limit: user.is_admin ? 0 : user.limits.monthly_token_limit },
    images: showImages ? { used: user.usage.images, limit: imageLimit } : null,
  };
}

/** "Chat allowance this period: 412,380 of 1,000,000. Images this period: 12 of 30. Almost used up." */
function usageSentence(usage: Usage): string {
  const part = (label: string, { used, limit }: { used: number; limit: number }) => {
    if (limit <= 0) return `${label}: ${formatNumber(used)}, unlimited.`;
    const state = meterState(used, limit);
    return `${label}: ${formatNumber(used)} of ${formatNumber(limit)}.${state ? ` ${state.words}.` : ""}`;
  };
  return [
    part(`Chat allowance ${usage.window}`, usage.allowance),
    usage.images ? part(`Images ${usage.window}`, usage.images) : null,
  ]
    .filter(Boolean)
    .join(" ");
}

function nearLimit({ used, limit }: { used: number; limit: number }): boolean {
  return limit > 0 && used >= limit * 0.9;
}

export default function AvatarMenu({ user, imagesOffered, onOpenSettings, onPreferenceError, onLogout }: AvatarMenuProps) {
  const { preferences, updatePreferences } = useAuth();
  const usageId = useId();
  const themeErrorId = useId();
  const [themeError, setThemeError] = useState<string | null>(null);
  const themeAttempt = useRef(0);
  const menuOpen = useRef(false);
  const role = user.is_admin ? "Administrator" : "Member";
  const usage = usageOf(user, imagesOffered);
  const lapsed = !user.is_admin && !user.plan.access;
  const days = user.is_admin ? null : expiringDays(user.plan);
  const lastDay = days !== null && days <= 0;
  const warn = usage !== null && (nearLimit(usage.allowance) || (usage.images !== null && nearLimit(usage.images)));
  const attention = warn ? ", usage almost at the limit" : lastDay ? ", your plan ends today" : "";
  const buttonLabel = `Account: ${user.username}${attention}`;

  // A failed save reverts (AuthContext) and never silently (U23): the reason shows under the row, or in the page's
  // notice when the menu has closed meanwhile. Only the latest choice reports; a newer one decides by its own result.
  function chooseTheme(theme: ThemePreference) {
    const attempt = ++themeAttempt.current;
    setThemeError(null);
    updatePreferences({ theme }).catch((err: unknown) => {
      if (attempt !== themeAttempt.current) return;
      const message = `Couldn't save the theme. ${err instanceof ApiError ? err.message : "Please try again."}`;
      if (menuOpen.current) setThemeError(message);
      else onPreferenceError(message);
    });
  }

  return (
    <Menu
      label={`Account: ${user.username}, ${role}`}
      placement="bottom-end"
      className="chat-avatar-menu"
      onOpenChange={(open) => {
        menuOpen.current = open;
        if (!open) setThemeError(null);
      }}
      trigger={(props) => (
        <button {...props} type="button" className="chat-avatar-btn" aria-label={buttonLabel} title={user.username}>
          <Avatar name={user.username} />
          {warn || lastDay ? <span className="chat-tb-dot" aria-hidden="true" /> : null}
        </button>
      )}
    >
      <MenuHead
        className="chat-avatar-menu-head"
        icon={<Avatar name={user.username} size={40} />}
        title={user.username}
        subtitle={user.is_admin ? role : planLine(user.plan)}
      />
      {usage ? (
        <div className="chat-avatar-usage" aria-hidden="true">
          <Meter
            label={`Chat allowance ${usage.window}`}
            used={usage.allowance.used}
            limit={usage.allowance.limit}
            format={formatCompact}
            noun="standard tokens"
          />
          {usage.images ? (
            <Meter
              label={`Images ${usage.window}`}
              used={usage.images.used}
              limit={usage.images.limit}
              format={formatNumber}
              noun="images"
            />
          ) : null}
        </div>
      ) : null}
      <MenuItem
        icon={<ChartIcon size={18} />}
        describedBy={usage ? usageId : undefined}
        onSelect={() => onOpenSettings("usage")}
      >
        View usage
      </MenuItem>
      {usage ? (
        <span id={usageId} hidden>
          {usageSentence(usage)}
        </span>
      ) : null}
      <MenuItem
        icon={<CardIcon size={18} />}
        end={
          days !== null ? (
            <span className="badge badge-warning">{daysLeftBadge(days)}</span>
          ) : lapsed ? (
            <span className="badge">No plan</span>
          ) : undefined
        }
        onSelect={() => onOpenSettings("billing")}
      >
        Plan &amp; billing
      </MenuItem>
      <MenuSeparator />
      <MenuItem icon={<SettingsIcon size={18} />} onSelect={() => onOpenSettings("general")}>
        Settings
      </MenuItem>
      <MenuGroup label="Theme" orientation="horizontal" className="chat-theme-row">
        {THEMES.map((theme) => {
          const Icon = THEME_ICONS[theme.id];
          return (
            <MenuRadioItem
              key={theme.id}
              checked={preferences.theme === theme.id}
              showCheck={false}
              keepOpen
              icon={<Icon size={18} />}
              describedBy={themeError ? themeErrorId : undefined}
              onSelect={() => chooseTheme(theme.id)}
            >
              {theme.label}
            </MenuRadioItem>
          );
        })}
      </MenuGroup>
      {themeError ? (
        <p id={themeErrorId} className="error-text chat-theme-error" role="alert">
          <AlertCircleIcon size={15} />
          {themeError}
        </p>
      ) : null}
      <MenuSeparator />
      {user.is_admin ? (
        <MenuLink to="/admin" icon={<ShieldIcon size={18} />}>
          Admin
        </MenuLink>
      ) : null}
      <MenuItem icon={<LogoutIcon size={18} />} onSelect={onLogout}>
        Log out
      </MenuItem>
    </Menu>
  );
}
