import type { Me } from "../../api/types";
import type { SettingsTab } from "../../lib/settingsLink";
import { MenuIcon, PanelRightIcon } from "../ui/icons";
import AvatarMenu from "./AvatarMenu";
import ModelMenu from "./ModelMenu";
import PluginsMenu from "./PluginsMenu";
import type { ModelsState } from "./useModels";

export type PanelToggle = { open: boolean; controls: string; onToggle: () => void };

type TopBarProps = {
  title: string;
  user: Me | null;
  models: ModelsState;
  imagesOffered: boolean;
  /** Under 1100 px, where the conversation sidebar is a drawer. */
  sidebarToggle: PanelToggle | null;
  /** The documents panel: collapses the right column at 800 px and wider, opens the slide-over below. */
  documentsToggle: PanelToggle;
  openSettings: (tab: SettingsTab, focusPluginId?: string) => void;
  /** A preference saved from the bar (the avatar menu's theme) failed after its menu closed. */
  onPreferenceError: (message: string) => void;
  onLogout: () => void;
};

/**
 * The bar over the chat column, like ChatGPT's (functional brief 8.1, visual spec 6.10): [Conversations] · Model ·
 * title · Plugins · Documents · avatar. It compacts by the chat column's own width (container queries in chat.css):
 * labels go first, then the title; the model button is the only item that shrinks.
 */
export default function TopBar({
  title,
  user,
  models,
  imagesOffered,
  sidebarToggle,
  documentsToggle,
  openSettings,
  onPreferenceError,
  onLogout,
}: TopBarProps) {
  return (
    <header className="chat-topbar">
      {sidebarToggle ? (
        <button
          type="button"
          className="chat-tb-btn chat-tb-icon"
          onClick={sidebarToggle.onToggle}
          aria-expanded={sidebarToggle.open}
          aria-controls={sidebarToggle.controls}
          aria-label="Conversations"
          title="Conversations"
        >
          <MenuIcon size={20} />
        </button>
      ) : null}
      <div className="chat-topbar-model">
        <ModelMenu models={models} onOpenSettings={() => openSettings("general")} />
      </div>
      <h1 className="chat-topbar-title" title={title}>
        {title}
      </h1>
      <div className="chat-topbar-actions">
        <PluginsMenu
          isAdmin={user?.is_admin ?? false}
          onOpenPlugin={(pluginId) => openSettings("plugins", pluginId ?? undefined)}
        />
        <button
          type="button"
          className="chat-tb-btn chat-docs-toggle"
          onClick={documentsToggle.onToggle}
          aria-expanded={documentsToggle.open}
          aria-controls={documentsToggle.controls}
          aria-label="Documents"
          title={documentsToggle.open ? "Hide documents" : "Show documents"}
        >
          <PanelRightIcon size={18} />
          <span className="chat-tb-label">Documents</span>
        </button>
        {user ? (
          <AvatarMenu
            user={user}
            imagesOffered={imagesOffered}
            onOpenSettings={openSettings}
            onPreferenceError={onPreferenceError}
            onLogout={onLogout}
          />
        ) : null}
      </div>
    </header>
  );
}
