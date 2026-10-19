import { type ReactNode, useEffect, useId, useState } from "react";
import {
  adminPluginActivityPath,
  disconnectAllPluginUsers,
  testRequestCheck,
  updateAdminPlugin,
} from "../../api/client";
import type {
  AdminModel,
  AdminPlugin,
  AdminPluginUpdate,
  PluginAction,
  PluginActionCount,
  PluginActivityReport,
  RequestCheckTestResult,
} from "../../api/types";
import { formatNumber } from "../../lib/format";
import Spinner from "../ui/Spinner";
import { errorMessage } from "./adminApi";
import ConfirmAction from "./ConfirmAction";
import { SwitchField } from "./Field";
import FlashMessage from "./FlashMessage";
import { useFlash, useResource } from "./hooks";
import { AlertCircleIcon, AlertIcon, CheckCircleIcon, CheckIcon, CloseIcon } from "../ui/icons";
import MonthPicker, { currentMonth, monthLabel } from "./MonthPicker";
import { EmptyState, ResourceGate } from "./StateViews";

const COPY_FEEDBACK_MS = 1600;

/** A long URL or scope with line-break opportunities after each "/", so it wraps between path segments. */
function breakAfterSlashes(text: string): ReactNode[] {
  return text.split(/(?<=\/)/).flatMap((part, index) => (index === 0 ? [part] : [<wbr key={index} />, part]));
}

const ACTION_LABELS: Record<PluginAction, string> = {
  connect: "Connected",
  disconnect: "Disconnected",
  reconnect_needed: "Needed reconnecting",
  search: "Searches",
  // Audit rows count actions, not emails: one read covers a message, several messages or a whole conversation.
  read: "Reads",
  import: "Imports",
  draft: "Drafts",
  send_proposed: "Emails proposed",
  send: "Emails sent",
  send_cancelled: "Proposals cancelled",
};
const ACTION_ORDER = Object.keys(ACTION_LABELS) as PluginAction[];
/** Columns of the per-user table (successful actions); failures and refusals are summed in their own columns. */
const USER_COLUMNS: PluginAction[] = ["search", "read", "import", "draft", "send_proposed", "send"];

function plural(count: number, noun: string, pluralNoun = `${noun}s`): string {
  return `${formatNumber(count)} ${count === 1 ? noun : pluralNoun}`;
}

export default function PluginsTab() {
  const plugins = useResource<AdminPlugin[]>("/admin/plugins", "Couldn't load plugins.");
  const replace = (saved: AdminPlugin) =>
    plugins.setData((list) => list && list.map((plugin) => (plugin.id === saved.id ? saved : plugin)));

  return (
    <div className="admin-stack">
      <div className="admin-section-header">
        <div>
          <h2 className="admin-card-title">Plugins</h2>
          <p className="admin-card-subtitle">
            Connectors each user links to their own account (Settings → Plugins & data, or the Plugins menu in chat).
            Turning a plugin or a capability off takes it out of chat right away; users' connections stay until they
            disconnect.
          </p>
        </div>
      </div>
      <ResourceGate resource={plugins} loadingLabel="Loading plugins">
        {(list) =>
          list.length === 0 ? (
            <section className="card admin-card">
              <EmptyState title="No plugins are available." />
            </section>
          ) : (
            list.map((plugin) => (
              <PluginSection key={plugin.id} plugin={plugin} onSaved={replace} onReload={() => void plugins.reload()} />
            ))
          )
        }
      </ResourceGate>
      <RequestCheckCard />
    </div>
  );
}

type PluginSectionProps = { plugin: AdminPlugin; onSaved: (saved: AdminPlugin) => void; onReload: () => void };

function PluginSection({ plugin: saved, onSaved, onReload }: PluginSectionProps) {
  const titleId = useId();
  const flash = useFlash();
  // The optimistic state while a switch is saved; rolled back (cleared) when the request fails.
  const [optimistic, setOptimistic] = useState<AdminPlugin | null>(null);
  const plugin = optimistic ?? saved;
  const { name, connections } = plugin;
  const saving = optimistic !== null;
  const connectionTotal = connections.connected + connections.reconnect_needed;

  async function save(update: AdminPluginUpdate, next: AdminPlugin, success: string, failure: string) {
    setOptimistic(next);
    try {
      onSaved(await updateAdminPlugin(saved.id, update));
      flash.success(success);
    } catch (err) {
      flash.error(errorMessage(err, failure));
    } finally {
      setOptimistic(null);
    }
  }

  const setEnabled = (enabled: boolean) =>
    void save(
      { enabled },
      { ...plugin, enabled },
      enabled
        ? `${name} is on. Users can connect their accounts in Settings → Plugins & data.`
        : `${name} is off. Its tools and screens are gone from chat; connections stay until users disconnect.`,
      `Couldn't turn ${name} ${enabled ? "on" : "off"}.`,
    );

  const setCapability = (id: string, label: string, enabled: boolean) =>
    void save(
      { capabilities: { [id]: enabled } },
      {
        ...plugin,
        capabilities: plugin.capabilities.map((capability) => (capability.id === id ? { ...capability, enabled } : capability)),
      },
      `${label} turned ${enabled ? "on" : "off"}.`,
      `Couldn't turn ${label} ${enabled ? "on" : "off"}.`,
    );

  async function disconnectAll() {
    const { disconnected } = await disconnectAllPluginUsers(saved.id);
    flash.success(disconnected === 0 ? "Nobody was connected." : `Disconnected ${plural(disconnected, "connection")}.`);
    onReload();
  }

  return (
    <section className="card admin-card" aria-labelledby={titleId}>
      <div className="admin-card-header">
        <div className="admin-card-heading">
          <h2 id={titleId} className="admin-card-title admin-plugin-title">
            {name}
            {plugin.configured ? (
              <span className="badge badge-success">Configured</span>
            ) : (
              <span className="badge badge-warning">Not configured</span>
            )}
            {plugin.test_endpoints ? <span className="badge badge-warning">Test endpoints</span> : null}
          </h2>
          <p className="admin-card-subtitle">{plugin.description}</p>
        </div>
        <div className="admin-plugin-enable">
          <SwitchField
            label="Enabled"
            hint={plugin.configured ? undefined : "Set the missing settings first"}
            checked={plugin.enabled}
            disabled={saving || (!plugin.configured && !plugin.enabled)}
            onChange={setEnabled}
          />
        </div>
      </div>
      <FlashMessage flash={flash} />
      {!plugin.configured ? (
        <div className="ui-notice ui-notice-warning admin-inline-notice">
          <AlertIcon size={15} />
          <span className="ui-notice-text">
            Missing in .env: {plugin.missing_settings.join(", ") || "unknown settings"}. Set them, then run{" "}
            <code className="admin-code">docker compose up -d backend</code>.
          </span>
        </div>
      ) : null}
      {plugin.test_endpoints ? (
        <div className="ui-notice ui-notice-warning admin-inline-notice">
          <AlertIcon size={15} />
          <span className="ui-notice-text">
            The OAuth or API endpoints aren't Google's (a test setup). Real accounts can't connect until the
            GOOGLE_OAUTH_*_URL and GMAIL_API_BASE_URL settings are removed.
          </span>
        </div>
      ) : null}

      <div className="admin-plugin-grid">
        <div className="admin-plugin-block">
          <h3 className="admin-plugin-heading">Capabilities</h3>
          <div className="admin-plugin-caps">
            {plugin.capabilities.map((capability) => (
              <div key={capability.id} className="admin-plugin-cap">
                <SwitchField
                  label={capability.name}
                  hint={
                    <>
                      {capability.description}
                      {capability.scopes.length > 0 ? (
                        <span className="admin-plugin-scopes">
                          {capability.scopes.map((scope) => (
                            <code key={scope} className="admin-code">
                              {breakAfterSlashes(scope)}
                            </code>
                          ))}
                        </span>
                      ) : null}
                    </>
                  }
                  checked={capability.enabled}
                  disabled={saving}
                  onChange={(value) => setCapability(capability.id, capability.name, value)}
                />
                {capability.note ? <p className="admin-plugin-note">{capability.note}</p> : null}
              </div>
            ))}
          </div>
        </div>

        <div className="admin-plugin-block">
          <h3 className="admin-plugin-heading">Google Cloud setup</h3>
          <div className="admin-plugin-field">
            <span className="admin-plugin-label">Scopes requested</span>
            <span className="admin-subtext">Tick these under Data Access in Google Cloud.</span>
            <ul className="admin-plugin-list">
              {plugin.requested_scopes.map((scope) => (
                <li key={scope}>
                  <code className="admin-code admin-plugin-wrap">{breakAfterSlashes(scope)}</code>
                </li>
              ))}
            </ul>
          </div>
          <div className="admin-plugin-field">
            <span className="admin-plugin-label">Redirect URIs to register in Google Cloud</span>
            <span className="admin-subtext">
              One per address in PUBLIC_BASE_URLS. A tunnel URL must be added both there and in Google.
            </span>
            {plugin.redirect_uris.length === 0 ? (
              <span className="admin-text-warning">No valid PUBLIC_BASE_URLS entry: connecting fails until one is set.</span>
            ) : (
              <ul className="admin-plugin-list">
                {plugin.redirect_uris.map((uri) => (
                  <li key={uri} className="admin-plugin-uri">
                    <code className="admin-code admin-plugin-wrap">{breakAfterSlashes(uri)}</code>
                    <CopyButton text={uri} />
                  </li>
                ))}
              </ul>
            )}
          </div>
          {plugin.testing_mode ? (
            <p className="admin-plugin-note">
              Testing mode: only the Google accounts you add as test users (Google Cloud → Audience) can connect; others
              see Google's “Access blocked” page. Connections end after 7 days while the Google app is in testing mode,
              and users must reconnect.
            </p>
          ) : null}
        </div>

        <div className="admin-plugin-block">
          <h3 className="admin-plugin-heading">Connections</h3>
          <p className="admin-plugin-text">
            {connectionTotal === 0
              ? "No users have connected."
              : `${formatNumber(connections.connected)} connected${
                  connections.reconnect_needed > 0 ? `, ${formatNumber(connections.reconnect_needed)} ${connections.reconnect_needed === 1 ? "needs" : "need"} reconnecting` : ""
                }`}
          </p>
          <p className="admin-subtext">Account emails are never shown here.</p>
          {connections.old_key > 0 ? (
            <div className="ui-notice ui-notice-warning">
              <AlertIcon size={15} />
              <span className="ui-notice-text">
                {plural(connections.old_key, "connection")} still {connections.old_key === 1 ? "uses" : "use"} an older
                encryption key: keep the old key in TOKEN_ENCRYPTION_KEY until this is 0.
              </span>
            </div>
          ) : null}
          {connectionTotal > 0 ? (
            <div>
              <ConfirmAction
                label="Disconnect all users"
                message={`Disconnect every user's ${name} account? Google access is revoked; users can connect again while ${name} is on.`}
                confirmLabel="Disconnect all"
                onConfirm={disconnectAll}
              />
            </div>
          ) : null}
        </div>
      </div>

      <PluginActivity pluginId={saved.id} name={name} />
    </section>
  );
}

function CopyButton({ text }: { text: string }) {
  const [result, setResult] = useState<"copied" | "failed" | null>(null);

  useEffect(() => {
    if (!result) return;
    const timer = window.setTimeout(() => setResult(null), COPY_FEEDBACK_MS);
    return () => window.clearTimeout(timer);
  }, [result]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setResult("copied");
    } catch {
      setResult("failed");
    }
  }

  return (
    <>
      <button type="button" className="btn btn-sm" onClick={() => void copy()} aria-label={`Copy ${text}`}>
        {result === "copied" ? <CheckIcon size={13} /> : null}
        {result === "copied" ? "Copied" : result === "failed" ? "Couldn't copy" : "Copy"}
      </button>
      <span className="sr-only" aria-live="polite">
        {result === "copied" ? "Copied to the clipboard" : result === "failed" ? "Couldn't copy" : ""}
      </span>
    </>
  );
}

function countsByAction(actions: PluginActionCount[]): Map<PluginAction, PluginActionCount> {
  return new Map(actions.map((count) => [count.action, count]));
}

function PluginActivity({ pluginId, name }: { pluginId: string; name: string }) {
  const titleId = useId();
  const [month, setMonth] = useState(currentMonth);
  const report = useResource<PluginActivityReport>(adminPluginActivityPath(pluginId, month), "Couldn't load the activity.");

  return (
    <div className="admin-plugin-activity" aria-labelledby={titleId} role="group">
      <div className="admin-section-header admin-plugin-activity-header">
        <h3 id={titleId} className="admin-plugin-heading">
          {name} activity in {monthLabel(month)}
        </h3>
        <span className="spacer" />
        <MonthPicker
          month={month}
          onChange={setMonth}
          loading={report.loading && report.data !== null}
          loadingLabel="Loading activity"
        />
      </div>
      <ResourceGate resource={report} loadingLabel="Loading activity">
        {(data) => <ActivityTables report={data} pending={data.month !== month} />}
      </ResourceGate>
    </div>
  );
}

function ActivityTables({ report, pending }: { report: PluginActivityReport; pending: boolean }) {
  const totals = countsByAction(report.totals);
  const rows = ACTION_ORDER.filter((action) => totals.has(action));
  if (rows.length === 0) {
    return <p className="admin-card-footnote">Nothing recorded in {monthLabel(report.month)}.</p>;
  }
  const users = [...report.by_user].sort((a, b) => a.username.localeCompare(b.username));
  return (
    <div className={pending ? "admin-stack admin-pending" : "admin-stack"} aria-busy={pending || undefined}>
      <div className="table-wrap">
        <table className="table admin-table">
          <caption className="sr-only">Actions this month</caption>
          <thead>
            <tr>
              <th scope="col">Action</th>
              <th scope="col" className="num">
                OK
              </th>
              <th scope="col" className="num">
                Failed
              </th>
              <th scope="col" className="num">
                Refused
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((action) => {
              const count = totals.get(action) as PluginActionCount;
              return (
                <tr key={action}>
                  <td>{ACTION_LABELS[action]}</td>
                  <td className="num">{formatNumber(count.ok)}</td>
                  <td className="num">{formatNumber(count.failed)}</td>
                  <td className="num">{formatNumber(count.refused)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {users.length > 0 ? (
        <div className="table-wrap">
          <table className="table admin-table">
            <caption className="sr-only">By user</caption>
            <thead>
              <tr>
                <th scope="col">User</th>
                {USER_COLUMNS.map((action) => (
                  <th key={action} scope="col" className="num">
                    {ACTION_LABELS[action]}
                  </th>
                ))}
                <th scope="col" className="num">
                  Failed
                </th>
                <th scope="col" className="num">
                  Refused
                </th>
              </tr>
            </thead>
            <tbody>
              {users.map((user) => {
                const counts = countsByAction(user.actions);
                const failed = user.actions.reduce((sum, count) => sum + count.failed, 0);
                const refused = user.actions.reduce((sum, count) => sum + count.refused, 0);
                return (
                  <tr key={user.user_id}>
                    <td>
                      <span className="admin-user-name admin-nowrap">{user.username}</span>
                    </td>
                    {USER_COLUMNS.map((action) => (
                      <td key={action} className="num">
                        {formatNumber(counts.get(action)?.ok ?? 0)}
                      </td>
                    ))}
                    <td className="num">{formatNumber(failed)}</td>
                    <td className="num">{formatNumber(refused)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
      <p className="admin-card-footnote">
        Counts are actions: each search, read or import counts once, however many emails it covered.
      </p>
    </div>
  );
}

type CheckState =
  | { status: "running"; model: string }
  | { status: "done"; model: string; result: RequestCheckTestResult }
  | { status: "failed"; model: string; message: string };

/** The request check gates side effects (drafts, sends, imports; files and images after email was read). */
function RequestCheckCard() {
  const titleId = useId();
  const selectId = useId();
  const models = useResource<AdminModel[]>("/admin/models", "Couldn't load the chat models.");
  const chatModels = (models.data ?? []).filter((model) => model.kind === "chat" && model.enabled);
  const [chosen, setChosen] = useState<number | null>(null);
  const selected =
    chatModels.find((model) => model.id === chosen) ?? chatModels.find((model) => model.is_default) ?? chatModels[0] ?? null;
  const [check, setCheck] = useState<CheckState | null>(null);

  async function run() {
    if (!selected) return;
    const model = selected.display_name;
    setCheck({ status: "running", model });
    try {
      setCheck({ status: "done", model, result: await testRequestCheck(selected.id) });
    } catch (err) {
      setCheck({ status: "failed", model, message: errorMessage(err, "The test request failed.") });
    }
  }

  return (
    <section className="card admin-card" aria-labelledby={titleId}>
      <div className="admin-card-header">
        <div className="admin-card-heading">
          <h2 id={titleId} className="admin-card-title">
            Request check
          </h2>
          <p className="admin-card-subtitle">
            Before the assistant drafts, proposes or imports email (and before it creates a file or image after reading
            email), a separate call to the chat model checks that the user asked for it in their own words. A model that
            fails this test refuses all of these. Test each chat model users can pick; the test sends one short request.
          </p>
        </div>
      </div>
      <div className="admin-plugin-check">
        {models.data === null ? (
          models.error ? (
            <span className="admin-text-warning">{models.error}</span>
          ) : (
            <Spinner size={16} label="Loading chat models" />
          )
        ) : chatModels.length === 0 ? (
          <span className="muted">No chat model is enabled.</span>
        ) : (
          <>
            <label className="field-label" htmlFor={selectId}>
              Chat model
            </label>
            <select
              id={selectId}
              className="select admin-plugin-select"
              value={selected?.id ?? ""}
              onChange={(e) => setChosen(Number(e.target.value))}
            >
              {chatModels.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.display_name}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="btn"
              onClick={() => void run()}
              disabled={!selected || check?.status === "running"}
            >
              Test
            </button>
          </>
        )}
      </div>
      {check ? (
        <div className="admin-plugin-check-result">
          <CheckResult state={check} onDismiss={() => setCheck(null)} />
        </div>
      ) : null}
    </section>
  );
}

function CheckResult({ state, onDismiss }: { state: CheckState; onDismiss: () => void }) {
  if (state.status === "running") {
    return (
      <div className="admin-test admin-test-running" role="status">
        <Spinner size={14} label="Testing" />
        <span className="admin-test-text">Running the request check with {state.model}…</span>
      </div>
    );
  }
  const result = state.status === "done" ? state.result : null;
  const ok = result?.ok ?? false;
  const message = result ? result.message : state.status === "failed" ? state.message : "";
  const asked = result?.answer
    ? Object.entries(result.answer)
        .filter(([, value]) => value)
        .map(([action]) => action)
    : null;
  const facts: string[] = [];
  if (result && result.latency_ms > 0) facts.push(`${formatNumber(Math.round(result.latency_ms))} ms`);
  if (asked) facts.push(asked.length === 0 ? "the model found no request" : `the model found: ${asked.join(", ")}`);
  return (
    <div className={`admin-test ${ok ? "admin-test-ok" : "admin-test-fail"}`} role={ok ? "status" : "alert"}>
      {ok ? <CheckCircleIcon size={16} /> : <AlertCircleIcon size={16} />}
      <span className="admin-test-text">
        <strong>{ok ? `The request check works with ${state.model}` : `The request check failed with ${state.model}`}</strong>
        {facts.length ? <span className="admin-test-facts"> · {facts.join(" · ")}</span> : null}
        {message ? <span className="admin-test-message">{message}</span> : null}
      </span>
      <button type="button" className="btn btn-ghost btn-icon admin-test-close" aria-label="Dismiss test result" onClick={onDismiss}>
        <CloseIcon size={15} />
      </button>
    </div>
  );
}
