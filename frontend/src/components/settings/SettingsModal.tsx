import { useEffect, useId, useRef, useSyncExternalStore, type KeyboardEvent, type ReactNode } from "react";
import type { Me, PluginInfo } from "../../api/types";
import { SETTINGS_TABS, type SettingsTab } from "../../lib/settingsLink";
import Avatar from "../ui/Avatar";
import { CardIcon, ChartIcon, CloseIcon, PlugIcon, SlidersIcon, UserIcon } from "../ui/icons";
import Modal from "../ui/Modal";
import AccountTab from "./AccountTab";
import GeneralTab from "./GeneralTab";
import PlanBillingTab from "./PlanBillingTab";
import PluginsDataTab, { type PluginsIntent } from "./PluginsDataTab";
import "./settings.css";
import UsageTab from "./UsageTab";

const TAB_ICON: Record<SettingsTab, ReactNode> = {
  general: <SlidersIcon />,
  account: <UserIcon />,
  billing: <CardIcon />,
  usage: <ChartIcon />,
  plugins: <PlugIcon />,
};

const PHONE_QUERY = "(max-width: 799.98px)";

function subscribePhone(listener: () => void): () => void {
  const query = window.matchMedia(PHONE_QUERY);
  query.addEventListener("change", listener);
  return () => query.removeEventListener("change", listener);
}

/** Under 800 px the tabs are a horizontal strip under the title (the dialog is a full-screen sheet). */
function useIsPhone(): boolean {
  return useSyncExternalStore(subscribePhone, () => window.matchMedia(PHONE_QUERY).matches);
}

type SettingsModalProps = {
  user: Me;
  tab: SettingsTab;
  onSelectTab: (tab: SettingsTab) => void;
  onClose: () => void;
  pluginsIntent: PluginsIntent;
  /** Changes with each new request to open Plugins & data with a banner or a card to focus. */
  pluginsIntentKey: string;
  onBrowse: (plugin: PluginInfo) => void;
  onAllChatsDeleted: () => void;
};

/**
 * Settings (docs/briefs/ui-shell-settings.md 10.1): a dialog with a tab rail (a horizontal strip on phones) and one
 * panel. Tabs activate on arrow keys; only the selected tab is a tab stop. Each panel mounts when selected, so it shows
 * its skeleton at once and loads afresh.
 */
export default function SettingsModal({
  user,
  tab,
  onSelectTab,
  onClose,
  pluginsIntent,
  pluginsIntentKey,
  onBrowse,
  onAllChatsDeleted,
}: SettingsModalProps) {
  const id = useId();
  const titleId = `${id}-title`;
  const tabId = (value: SettingsTab) => `${id}-tab-${value}`;
  const panelId = `${id}-panel`;
  const tabRefs = useRef(new Map<SettingsTab, HTMLButtonElement>());
  const selectedRef = useRef<HTMLButtonElement | null>(null);
  const isPhone = useIsPhone();

  // On phones, keep the selected tab in view in the scrolling strip.
  useEffect(() => {
    if (isPhone) tabRefs.current.get(tab)?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [tab, isPhone]);

  function onTabKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    const index = SETTINGS_TABS.findIndex((item) => item.id === tab);
    const last = SETTINGS_TABS.length - 1;
    let next: number;
    switch (event.key) {
      case "ArrowDown":
      case "ArrowRight":
        next = index === last ? 0 : index + 1;
        break;
      case "ArrowUp":
      case "ArrowLeft":
        next = index === 0 ? last : index - 1;
        break;
      case "Home":
        next = 0;
        break;
      case "End":
        next = last;
        break;
      default:
        return;
    }
    event.preventDefault();
    const target = SETTINGS_TABS[next].id;
    tabRefs.current.get(target)?.focus();
    if (target !== tab) onSelectTab(target);
  }

  let panel: ReactNode;
  switch (tab) {
    case "general":
      panel = <GeneralTab onClose={onClose} />;
      break;
    case "account":
      panel = <AccountTab onClose={onClose} />;
      break;
    case "billing":
      panel = <PlanBillingTab user={user} onClose={onClose} />;
      break;
    case "usage":
      panel = <UsageTab user={user} onClose={onClose} />;
      break;
    case "plugins":
      panel = (
        <PluginsDataTab
          user={user}
          intent={pluginsIntent}
          intentKey={pluginsIntentKey}
          onBrowse={onBrowse}
          onAllChatsDeleted={onAllChatsDeleted}
          onClose={onClose}
        />
      );
      break;
  }

  return (
    <Modal
      open
      title="Settings"
      onClose={onClose}
      variant="sheet"
      className="settings-dialog"
      hideHeader
      labelledBy={titleId}
      bare
      initialFocus={selectedRef}
    >
      <div className="settings-nav">
        <div className="settings-title-row">
          <h1 className="settings-title" id={titleId}>
            Settings
          </h1>
          <button type="button" className="btn btn-ghost btn-icon settings-close" aria-label="Close settings" onClick={onClose}>
            <CloseIcon />
          </button>
        </div>
        <div className="settings-tabs-wrap">
          <div
            className="settings-tabs"
            role="tablist"
            aria-labelledby={titleId}
            aria-orientation={isPhone ? "horizontal" : "vertical"}
          >
            {SETTINGS_TABS.map((item) => {
              const selected = item.id === tab;
              return (
                <button
                  key={item.id}
                  ref={(element) => {
                    if (element) tabRefs.current.set(item.id, element);
                    else tabRefs.current.delete(item.id);
                    if (selected) selectedRef.current = element;
                  }}
                  type="button"
                  role="tab"
                  id={tabId(item.id)}
                  className="settings-tab"
                  aria-selected={selected}
                  aria-controls={selected ? panelId : undefined}
                  tabIndex={selected ? 0 : -1}
                  onClick={() => {
                    if (!selected) onSelectTab(item.id);
                  }}
                  onKeyDown={onTabKeyDown}
                >
                  {TAB_ICON[item.id]}
                  {item.label}
                </button>
              );
            })}
          </div>
        </div>
        <p className="settings-nav-foot">
          <Avatar name={user.username} />
          <span>
            <strong>{user.username}</strong>
            Saved to your account
          </span>
        </p>
      </div>
      <section key={tab} className="settings-panel" role="tabpanel" id={panelId} aria-labelledby={tabId(tab)}>
        {panel}
      </section>
    </Modal>
  );
}
