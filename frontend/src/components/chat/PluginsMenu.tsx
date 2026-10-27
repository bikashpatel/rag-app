import type { ReactNode } from "react";
import type { PluginInfo, PlanUnlock } from "../../api/types";
import { UpgradePill } from "../billing/LockedHint";
import { pluginStatus, reloadPlugins, usePlugins, type PluginStatus } from "../connections/pluginStore";
import Menu, { MenuHead, MenuItem, MenuLink, MenuNote, MenuSeparator } from "../ui/Menu";
import Spinner from "../ui/Spinner";
import { ChevronDownIcon, ChevronRightIcon, LockIcon, MailIcon, PlugIcon, RefreshIcon, SettingsIcon } from "../ui/icons";

// The Plugins menu in the top bar (functional brief 8.3, visual spec 6.11): every registered plugin with its real
// status, whether or not it is set up or in the user's plan (plans-billing-visual.md 7.4). It never starts OAuth: each row opens the plugin's card in Settings → Plugins
// & data, which carries Connect, the testing-mode hint, errors and grants.

const SUBTITLE = "Apps the assistant can use for you in chat.";
/** Admin → Plugins, where a plugin is set up. */
const ADMIN_PLUGINS_PATH = "/admin#plugins";

type PluginsMenuProps = {
  isAdmin: boolean;
  /** Which plan includes a plugin outside the user's plan ("Included in Professional"). */
  unlock: PlanUnlock | null;
  /** Settings → Plugins & data, focused on that plugin's card (null: no particular card). */
  onOpenPlugin: (pluginId: string | null) => void;
};

/** The plugin's icon in a 40 px tile; Gmail's mail in its brand-like red. */
function PluginIcon({ plugin }: { plugin: PluginInfo }) {
  return (
    <span className={plugin.id === "gmail" ? "ui-menu-icon ui-menu-icon-lg chat-plugin-icon-gmail" : "ui-menu-icon ui-menu-icon-lg"}>
      {plugin.id === "gmail" ? <MailIcon size={20} /> : <PlugIcon size={20} />}
    </span>
  );
}

type Row = {
  /** The status line (ui-status): its variant and text. */
  tone: "connected" | "attention" | "neutral" | "off" | "unset";
  status: string;
  /** Lines under the status: a reason, the plugin's description. */
  detail: string | null;
  /** What activating it does. */
  action: "card" | "admin" | "none";
  end: ReactNode;
};

function rowFor(plugin: PluginInfo, status: PluginStatus, isAdmin: boolean, unlock: PlanUnlock | null): Row {
  const email = plugin.connection?.account_email;
  const quiet = <ChevronRightIcon size={16} />;
  switch (status) {
    case "not_in_plan": {
      const plan = unlock?.plugins[plugin.id] ?? null;
      const detail = [plan ? `Included in ${plan}.` : null, plugin.connection ? "Your connection is kept." : null]
        .filter(Boolean)
        .join(" ");
      return { tone: "off", status: "Not in your plan", detail: detail || null, action: "card", end: <UpgradePill /> };
    }
    case "connected":
      return {
        tone: "connected",
        status: `Connected · ${email}${plugin.connection?.chat_enabled === false ? " · off in chat" : ""}`,
        detail: null,
        action: "card",
        end: quiet,
      };
    case "reconnect_needed":
      return {
        tone: "attention",
        status: "Reconnect needed",
        detail: plugin.connection?.status_message ?? null,
        action: "card",
        end: (
          <span className="chat-plugin-cta">
            Reconnect
            <ChevronRightIcon size={14} />
          </span>
        ),
      };
    case "available":
      return {
        tone: "neutral",
        status: "Not connected",
        detail: null,
        action: "card",
        end: (
          <span className="chat-plugin-cta">
            Connect
            <ChevronRightIcon size={14} />
          </span>
        ),
      };
    case "disabled":
      return plugin.connection
        ? { tone: "off", status: "Turned off by the admin", detail: null, action: "card", end: quiet }
        : {
            tone: "off",
            status: "Turned off by the admin",
            detail: `The admin turned ${plugin.name} off.`,
            action: "none",
            end: null,
          };
    case "not_configured":
      if (plugin.connection) {
        return {
          tone: "unset",
          status: `Not set up · ${email} still connected`,
          detail: null,
          action: "card",
          end: quiet,
        };
      }
      return isAdmin
        ? {
            tone: "unset",
            status: "Not set up",
            detail:
              plugin.id === "gmail"
                ? "Needs a Google OAuth client before anyone can connect."
                : "Needs its server settings before anyone can connect.",
            action: "admin",
            end: null,
          }
        : {
            tone: "unset",
            status: "Not set up yet",
            detail: `The admin hasn't set up ${plugin.name} yet.`,
            action: "none",
            end: null,
          };
  }
}

function StatusLine({ tone, children }: { tone: Row["tone"]; children: ReactNode }) {
  return (
    <span className={`ui-status ui-status-${tone} chat-plugin-status`}>
      {tone === "off" ? <LockIcon size={13} /> : tone === "unset" ? <span className="ui-dashed" aria-hidden="true" /> : null}
      {children}
    </span>
  );
}

function PluginItem({
  plugin,
  isAdmin,
  unlock,
  onOpenPlugin,
}: {
  plugin: PluginInfo;
  isAdmin: boolean;
  unlock: PlanUnlock | null;
  onOpenPlugin: (pluginId: string) => void;
}) {
  const status = pluginStatus(plugin);
  const row = rowFor(plugin, status, isAdmin, unlock);
  const description = (
    <>
      {status === "available" && plugin.description ? (
        <span className="chat-plugin-desc" aria-hidden="true">
          {plugin.description}
        </span>
      ) : null}
      <StatusLine tone={row.tone}>{row.status}</StatusLine>
      {row.detail ? <span className="chat-plugin-detail">{row.detail}</span> : null}
      {row.action === "admin" ? <span className="chat-plugin-admin-link">Set up in Admin → Plugins</span> : null}
    </>
  );
  const common = {
    icon: <PluginIcon plugin={plugin} />,
    className: "chat-plugin-item",
    description,
    end: row.end,
  };
  if (row.action === "admin") {
    return (
      <MenuLink to={ADMIN_PLUGINS_PATH} {...common}>
        {plugin.name}
      </MenuLink>
    );
  }
  return (
    <MenuItem {...common} disabled={row.action === "none"} onSelect={() => onOpenPlugin(plugin.id)}>
      {plugin.name}
    </MenuItem>
  );
}

/** The top bar's Plugins button and menu; a dot and "N needs attention" when a connection needs to be reconnected. */
export default function PluginsMenu({ isAdmin, unlock, onOpenPlugin }: PluginsMenuProps) {
  const { plugins, error } = usePlugins();
  const attention = plugins?.filter((plugin) => pluginStatus(plugin) === "reconnect_needed").length ?? 0;
  const buttonLabel = attention > 0 ? `Plugins, ${attention} ${attention === 1 ? "needs" : "need"} attention` : "Plugins";
  const stateText =
    plugins === null ? (error ? error : "Loading plugins…") : plugins.length === 0 ? "No plugins are available." : null;

  return (
    <Menu
      label="Plugins"
      description={stateText ? `${SUBTITLE} ${stateText}` : SUBTITLE}
      placement="bottom-end"
      className="chat-plugins-menu"
      trigger={(props) => (
        <button {...props} type="button" className="chat-tb-btn chat-plugins-btn" aria-label={buttonLabel} title="Plugins">
          <span className="chat-tb-icon-wrap">
            <PlugIcon size={18} />
            {attention > 0 ? <span className="chat-tb-dot" aria-hidden="true" /> : null}
          </span>
          <span className="chat-tb-label">Plugins</span>
          <ChevronDownIcon size={14} className="chat-tb-chev" />
        </button>
      )}
    >
      <MenuHead title="Plugins" subtitle={SUBTITLE} />
      {plugins === null && error ? (
        <>
          <MenuNote className="chat-plugins-error">{error}</MenuNote>
          <MenuItem icon={<RefreshIcon size={18} />} onSelect={() => void reloadPlugins()}>
            Try again
          </MenuItem>
        </>
      ) : plugins === null ? (
        <MenuNote>
          <Spinner size={13} label="Loading plugins" />
          Loading plugins…
        </MenuNote>
      ) : plugins.length === 0 ? (
        <MenuNote>No plugins are available.</MenuNote>
      ) : (
        plugins.map((plugin) => (
          <PluginItem key={plugin.id} plugin={plugin} isAdmin={isAdmin} unlock={unlock} onOpenPlugin={onOpenPlugin} />
        ))
      )}
      <MenuSeparator />
      <MenuItem icon={<SettingsIcon size={18} />} end={<ChevronRightIcon size={16} />} onSelect={() => onOpenPlugin(null)}>
        Manage plugins…
      </MenuItem>
    </Menu>
  );
}
