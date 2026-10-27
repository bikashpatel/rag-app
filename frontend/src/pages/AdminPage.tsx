import { useCallback, useEffect, type ComponentType, type KeyboardEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import type { AdminStats } from "../api/types";
import AdminHeader from "../components/admin/AdminHeader";
import BillingTab from "../components/admin/BillingTab";
import DocumentsTab from "../components/admin/DocumentsTab";
import { useResource } from "../components/admin/hooks";
import ModelsTab from "../components/admin/ModelsTab";
import PlansTab from "../components/admin/PlansTab";
import PluginsTab from "../components/admin/PluginsTab";
import StatsBar from "../components/admin/StatsBar";
import SystemTab from "../components/admin/SystemTab";
import UsageTab from "../components/admin/UsageTab";
import UsersTab from "../components/admin/UsersTab";
import {
  ChartIcon,
  CpuIcon,
  DocsIcon,
  MonitorIcon,
  PlugIcon,
  ReceiptIcon,
  TagIcon,
  UsersIcon,
  type IconProps,
} from "../components/ui/icons";
import "../components/admin/admin.css";

const TABS = [
  { id: "users", label: "Users", icon: UsersIcon },
  { id: "plans", label: "Plans", icon: TagIcon },
  { id: "billing", label: "Billing", icon: ReceiptIcon },
  { id: "models", label: "Models", icon: CpuIcon },
  { id: "usage", label: "Usage", icon: ChartIcon },
  { id: "documents", label: "Documents", icon: DocsIcon },
  { id: "plugins", label: "Plugins", icon: PlugIcon },
  { id: "system", label: "System", icon: MonitorIcon },
] as const satisfies readonly { id: string; label: string; icon: ComponentType<IconProps> }[];

type TabId = (typeof TABS)[number]["id"];

const tabElementId = (id: TabId) => `admin-tab-${id}`;
const panelElementId = (id: TabId) => `admin-panel-${id}`;

/** "#billing/payments?user=7": the tab, a section inside it and parameters (a record to open, a filter). */
function parseHash(hash: string): { tab: TabId; section: string | null; params: URLSearchParams } {
  const [path, search = ""] = hash.replace(/^#/, "").split("?");
  const [tabId, section = null] = path.split("/");
  const tab = TABS.find((candidate) => candidate.id === tabId)?.id ?? "users";
  return { tab, section, params: new URLSearchParams(search) };
}

export default function AdminPage() {
  const { hash } = useLocation();
  const navigate = useNavigate();
  const { tab: active, section, params } = parseHash(hash);
  const stats = useResource<AdminStats>("/admin/stats", "Couldn't load the overview.");
  const { reload: reloadStats } = stats;
  const refreshStats = useCallback(() => void reloadStats(), [reloadStats]);
  /** Within the page: "users?user=7" opens that user, "billing/payments" a Billing section. */
  const go = useCallback((target: string) => navigate({ hash: target }, { replace: true }), [navigate]);
  const selectTab = useCallback((id: TabId) => go(id), [go]);

  useEffect(() => {
    const previousTitle = document.title;
    document.title = "Admin · RAG Assistant";
    return () => {
      document.title = previousTitle;
    };
  }, []);

  function onTabKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    const index = TABS.findIndex((tab) => tab.id === active);
    const target =
      e.key === "ArrowRight"
        ? (index + 1) % TABS.length
        : e.key === "ArrowLeft"
          ? (index - 1 + TABS.length) % TABS.length
          : e.key === "Home"
            ? 0
            : e.key === "End"
              ? TABS.length - 1
              : -1;
    if (target === -1) return;
    e.preventDefault();
    const id = TABS[target].id;
    selectTab(id);
    document.getElementById(tabElementId(id))?.focus();
  }

  return (
    <div className="admin-page">
      <AdminHeader />
      <main className="admin-main">
        <div className="admin-heading">
          <h1 className="admin-title">Administration</h1>
          <p className="admin-lede">
            Accounts, plans and billing, models, usage, the document index, plugins and the system's health.
          </p>
        </div>
        <StatsBar stats={stats} onOpenModels={() => selectTab("models")} />
        <div className="admin-tabs" role="tablist" aria-label="Admin sections" onKeyDown={onTabKeyDown}>
          {TABS.map((tab) => (
            <button
              key={tab.id}
              id={tabElementId(tab.id)}
              type="button"
              role="tab"
              className="admin-tab"
              aria-selected={tab.id === active}
              aria-controls={tab.id === active ? panelElementId(tab.id) : undefined}
              tabIndex={tab.id === active ? 0 : -1}
              onClick={() => selectTab(tab.id)}
            >
              <tab.icon size={16} />
              {tab.label}
            </button>
          ))}
        </div>
        <div className="admin-panel" role="tabpanel" id={panelElementId(active)} aria-labelledby={tabElementId(active)}>
          {active === "users" ? <UsersTab onChanged={refreshStats} params={params} onNavigate={go} /> : null}
          {active === "plans" ? <PlansTab /> : null}
          {active === "billing" ? <BillingTab section={section} params={params} onNavigate={go} /> : null}
          {active === "models" ? <ModelsTab onChanged={refreshStats} /> : null}
          {active === "usage" ? <UsageTab /> : null}
          {active === "documents" ? <DocumentsTab onChanged={refreshStats} /> : null}
          {active === "plugins" ? <PluginsTab /> : null}
          {active === "system" ? <SystemTab /> : null}
        </div>
      </main>
    </div>
  );
}
