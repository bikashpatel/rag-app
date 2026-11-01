import { useEffect, useMemo, useRef, useState } from "react";
import type {
  AdminConfig,
  AdminModel,
  AdminOrder,
  AdminPlan,
  AdminUser,
  AuthConfig,
  BillingAdminConfig,
  Page,
  SignupSource,
} from "../../api/types";
import { formatInr, formatIstDate } from "../../lib/billing";
import {
  formatBytes,
  formatCompact,
  formatCost,
  formatDate,
  formatDateTime,
  formatMegabytes,
  formatNumber,
  formatRelativeTime,
} from "../../lib/format";
import { GoogleMark } from "../auth/GoogleButton";
import Avatar from "../ui/Avatar";
import SegmentedControl from "../ui/SegmentedControl";
import {
  AlertCircleIcon,
  AlertIcon,
  ArrowDownIcon,
  ArrowUpIcon,
  CheckCircleIcon,
  ClockIcon,
  PlusIcon,
  SearchIcon,
} from "../ui/icons";
import { meterState } from "../ui/Meter";
import { BILLING_CONFIG_PATH, deleteResource, ordersPath } from "./adminApi";
import { PlanLine } from "./billingUi";
import ConfirmAction from "./ConfirmAction";
import FlashMessage from "./FlashMessage";
import { useFlash, useResource } from "./hooks";
import SignupCard from "./SignupCard";
import { RefreshError, ResourceGate } from "./StateViews";
import UserFormModal from "./UserFormModal";

const ENV_MANAGED = "Managed in .env";
const DAY_MS = 86_400_000;
/** The password minimum when /auth/config can't be read (PASSWORD_MIN_LENGTH's default). */
const DEFAULT_PASSWORD_MIN = 8;

type Filter = "all" | "active" | "expiring" | "lapsed" | "complimentary" | "none";

const FILTERS: { value: Filter; label: string; test: (user: AdminUser) => boolean }[] = [
  { value: "all", label: "All", test: () => true },
  { value: "active", label: "Active", test: (user) => !user.is_admin && user.plan.state === "active" },
  { value: "expiring", label: "Expiring", test: (user) => !user.is_admin && user.plan.expiring_soon },
  { value: "lapsed", label: "Lapsed", test: (user) => !user.is_admin && user.plan.state === "expired" },
  { value: "complimentary", label: "Complimentary", test: (user) => !user.is_admin && user.plan.complimentary },
  { value: "none", label: "No plan", test: (user) => !user.is_admin && user.plan.state === "none" },
];

type Source = "all" | "recent" | "email" | "google" | "admin";

const selfSignup = (user: AdminUser) => user.signup_source === "email" || user.signup_source === "google";

/** The sign-up select; "Last 7 days" counts self sign-ups, as the Sign-up card's tile does. */
const SOURCES: { value: Source; label: string; test: (user: AdminUser, now: number) => boolean }[] = [
  { value: "all", label: "All sign-ups", test: () => true },
  {
    value: "recent",
    label: "Last 7 days",
    test: (user, now) => selfSignup(user) && Date.parse(user.created_at) >= now - 7 * DAY_MS,
  },
  { value: "email", label: "Email", test: (user) => user.signup_source === "email" },
  { value: "google", label: "Google", test: (user) => user.signup_source === "google" },
  { value: "admin", label: "Admin", test: (user) => user.signup_source === "admin" },
];

const SOURCE_LABELS: Record<SignupSource, string> = { env: "Env", admin: "Admin", email: "Email", google: "Google" };

/** The search box: the username, the email or a pending address contains the text. */
function matchesSearch(user: AdminUser, query: string): boolean {
  if (!query) return true;
  return [user.username, user.email, user.pending_email?.address].some((value) =>
    value?.toLowerCase().includes(query),
  );
}

type Sort = { key: "signup" | "plan"; direction: 1 | -1 };

/** By sign-up date: -1 newest first. */
function bySignup(users: AdminUser[], direction: 1 | -1): AdminUser[] {
  return [...users].sort((a, b) => direction * (Date.parse(a.created_at) - Date.parse(b.created_at) || a.id - b.id));
}

const STATE_ORDER = { exempt: 0, active: 1, expired: 2, none: 3 } as const;

/** By plan: the admin, then users with access by plan rank and end date, then lapsed, then those without a plan. */
function byPlan(users: AdminUser[], ranks: Map<number, number>, direction: 1 | -1): AdminUser[] {
  const key = (user: AdminUser) => {
    const state = user.is_admin ? 0 : STATE_ORDER[user.plan.state];
    const rank = user.plan.plan ? (ranks.get(user.plan.plan.id) ?? 0) : 0;
    return { state, rank, end: user.plan.valid_through ?? "9999" };
  };
  return [...users].sort((a, b) => {
    const [x, y] = [key(a), key(b)];
    return (
      direction * (x.state - y.state || y.rank - x.rank || x.end.localeCompare(y.end)) ||
      a.username.localeCompare(b.username)
    );
  });
}

/** Paid time is left: the delete confirmation warns that no refund is made. */
function paidUntil(user: AdminUser): string | null {
  const { plan } = user;
  return plan.state === "active" && !plan.complimentary && plan.valid_through ? plan.valid_through : null;
}

type UsersTabProps = {
  onChanged: () => void;
  /** From the hash: "user" opens that user's form, "grant" with it opens the grant form. */
  params: URLSearchParams;
  onNavigate: (hash: string) => void;
};

type Editing = { user: AdminUser | null; startGrant: boolean };

export default function UsersTab({ onChanged, params, onNavigate }: UsersTabProps) {
  const users = useResource<AdminUser[]>("/admin/users", "Couldn't load users.");
  const config = useResource<AdminConfig>("/admin/config", "Couldn't load the default user limits.");
  const models = useResource<AdminModel[]>("/admin/models", "Couldn't load the model list.");
  const plans = useResource<AdminPlan[]>("/admin/plans", "Couldn't load the plans.");
  const billing = useResource<BillingAdminConfig>(BILLING_CONFIG_PATH, "Couldn't load the payments setup.");
  // Direct-UPI UTRs waiting for the admin: deleting their user is warned about (upi-direct.md 10.8).
  const toCheck = useResource<Page<AdminOrder>>(ordersPath("submitted", 1), "");
  const authConfig = useResource<AuthConfig>("/auth/config", "");
  const flash = useFlash();
  const [editing, setEditing] = useState<Editing | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [source, setSource] = useState<Source>("all");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<Sort>({ key: "signup", direction: -1 });
  const searchRef = useRef<HTMLInputElement>(null);
  const sourceRef = useRef<HTMLSelectElement>(null);
  const userParam = params.get("user");
  const grantParam = params.has("grant");

  const chatModels = useMemo(() => models.data?.filter((model) => model.kind === "chat") ?? null, [models.data]);
  const modelNames = useMemo(
    () => new Map((models.data ?? []).map((model) => [model.id, model.display_name])),
    [models.data],
  );
  const ranks = useMemo(() => new Map((plans.data ?? []).map((plan) => [plan.id, plan.rank])), [plans.data]);
  const paymentsOpen = billing.data?.payments.mode === "live";

  useEffect(() => {
    if (userParam === null || users.data === null || config.data === null) return;
    const target = users.data.find((user) => String(user.id) === userParam && !user.is_admin);
    if (target) setEditing({ user: target, startGrant: grantParam });
    onNavigate("users");
  }, [userParam, grantParam, users.data, config.data, onNavigate]);

  async function deleteUser(user: AdminUser) {
    await deleteResource(`/admin/users/${user.id}`);
    users.setData((list) => list && list.filter((u) => u.id !== user.id));
    flash.success(`Deleted ${user.username} and their documents.`);
    onChanged();
  }

  function handleSaved(saved: AdminUser, created: boolean) {
    setEditing(null);
    users.setData((list) => list && [...list.filter((u) => u.id !== saved.id), saved]);
    flash.success(created ? `Created ${saved.username}.` : `Saved changes to ${saved.username}.`);
    if (created) onChanged();
  }

  function toggleSort(key: Sort["key"]) {
    setSort((current) =>
      current.key === key
        ? { key, direction: current.direction === 1 ? -1 : 1 }
        : { key, direction: FIRST_DIRECTION[key] },
    );
  }

  function clearFilters() {
    setFilter("all");
    setSource("all");
    setQuery("");
    searchRef.current?.focus();
  }

  function showRecent() {
    setFilter("all");
    setQuery("");
    setSource("recent");
    sourceRef.current?.focus();
  }

  const setupError = [config, plans].find((resource) => resource.error && !resource.data);
  return (
    <>
      <SignupCard onShowRecent={showRecent} onNavigate={onNavigate} />
      <section className="card admin-card" aria-labelledby="admin-users-title">
        <div className="admin-card-header">
          <div className="admin-card-heading">
            <h2 id="admin-users-title" className="admin-card-title">
              Users
            </h2>
            <p className="admin-card-subtitle">
              Accounts and their plans, this period's use, and limits that override the plan's.
            </p>
          </div>
          <span className="spacer" />
          <button
            type="button"
            className="btn btn-primary"
            disabled={!config.data}
            onClick={() => setEditing({ user: null, startGrant: false })}
          >
            <PlusIcon size={16} />
            New user
          </button>
        </div>
        {setupError?.error ? (
          <RefreshError message={setupError.error} onRetry={() => void setupError.reload()} />
        ) : null}
        <FlashMessage flash={flash} />
        <ResourceGate resource={users} loadingLabel="Loading users">
          {(list) => {
            const now = Date.now();
            const needle = query.trim().toLowerCase();
            const sourceTest = SOURCES.find((option) => option.value === source)?.test ?? (() => true);
            const found = list.filter((user) => matchesSearch(user, needle) && sourceTest(user, now));
            const counts = new Map(FILTERS.map(({ value, test }) => [value, found.filter(test).length]));
            const test = FILTERS.find((option) => option.value === filter)?.test ?? (() => true);
            const shown = found.filter(test);
            const ordered =
              sort.key === "plan" ? byPlan(shown, ranks, sort.direction) : bySignup(shown, sort.direction);
            return (
              <>
                <div className="admin-toolbar">
                  <span className="admin-user-search">
                    <SearchIcon size={15} />
                    <input
                      ref={searchRef}
                      type="search"
                      className="input"
                      placeholder="Search name or email"
                      aria-label="Search users by username or email"
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                    />
                  </span>
                  <SegmentedControl
                    label="Show"
                    size="sm"
                    value={filter}
                    options={FILTERS.map(({ value, label }) => ({
                      value,
                      label: `${label} ${formatNumber(counts.get(value) ?? 0)}`,
                    }))}
                    onChange={setFilter}
                    className="admin-user-filters"
                  />
                  <select
                    ref={sourceRef}
                    className="select admin-source-filter"
                    aria-label="Signed up"
                    value={source}
                    onChange={(e) => setSource(e.target.value as Source)}
                  >
                    {SOURCES.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="table-wrap">
                  <table className="table admin-table">
                    <thead>
                      <tr>
                        <th scope="col">User</th>
                        <SortHeader label="Signed up" column="signup" sort={sort} onSort={toggleSort} />
                        <SortHeader label="Plan" column="plan" sort={sort} onSort={toggleSort} />
                        <th scope="col">Chat this period</th>
                        <th scope="col">Images this period</th>
                        <th scope="col" className="num">
                          Cost
                        </th>
                        <th scope="col">Rate limit</th>
                        <th scope="col">Uploads</th>
                        <th scope="col">Models</th>
                        <th scope="col">Last login</th>
                        <th scope="col" className="admin-actions-cell">
                          <span className="sr-only">Actions</span>
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {ordered.map((user) => (
                        <UserRow
                          key={user.id}
                          user={user}
                          modelNames={modelNames}
                          canEdit={config.data !== null}
                          onEdit={() => setEditing({ user, startGrant: false })}
                          onDelete={() => deleteUser(user)}
                          upiWaiting={toCheck.data?.items.find((order) => order.user.id === user.id) ?? null}
                        />
                      ))}
                      {ordered.length === 0 ? (
                        <tr>
                          <td colSpan={COLUMNS} className="admin-empty-row">
                            <span className="admin-empty-row-text">
                              No users match.
                              <button type="button" className="btn btn-ghost btn-sm" onClick={clearFilters}>
                                Clear filters
                              </button>
                            </span>
                          </td>
                        </tr>
                      ) : null}
                    </tbody>
                  </table>
                </div>
                {list.length <= 1 ? (
                  <p className="admin-card-footnote">
                    No other users yet. Use “New user” to create the first account.
                  </p>
                ) : null}
              </>
            );
          }}
        </ResourceGate>
        {editing && config.data ? (
          <UserFormModal
            user={editing.user}
            config={config.data}
            chatModels={chatModels}
            modelsError={models.error}
            plans={plans.data}
            paymentsOpen={paymentsOpen}
            passwordMin={authConfig.data?.password_min_length ?? DEFAULT_PASSWORD_MIN}
            startGrant={editing.startGrant}
            onClose={() => setEditing(null)}
            onSaved={handleSaved}
            onPlanChanged={() => void users.reload()}
            onNavigate={onNavigate}
          />
        ) : null}
      </section>
    </>
  );
}

/** The table's columns, for the "No users match." row. */
const COLUMNS = 11;

/** The order a column sorts in when chosen: Signed up newest first, Plan from the admin and the highest plan down. */
const FIRST_DIRECTION: Record<Sort["key"], 1 | -1> = { signup: -1, plan: 1 };

type SortHeaderProps = { label: string; column: Sort["key"]; sort: Sort; onSort: (column: Sort["key"]) => void };

function SortHeader({ label, column, sort, onSort }: SortHeaderProps) {
  const active = sort.key === column;
  return (
    <th scope="col" aria-sort={active ? (sort.direction === 1 ? "ascending" : "descending") : undefined}>
      <button type="button" className="admin-sort" onClick={() => onSort(column)}>
        {label}
        {active && sort.direction !== FIRST_DIRECTION[column] ? (
          <ArrowUpIcon size={13} />
        ) : (
          <ArrowDownIcon size={13} className={active ? undefined : "admin-sort-idle"} />
        )}
        <span className="sr-only">, sort</span>
      </button>
    </th>
  );
}

type UserRowProps = {
  user: AdminUser;
  modelNames: Map<number, string>;
  canEdit: boolean;
  onEdit: () => void;
  onDelete: () => Promise<void>;
  /** The user's direct-UPI payment waiting for the admin's check, if any. */
  upiWaiting: AdminOrder | null;
};

function UserRow({ user, modelNames, canEdit, onEdit, onDelete, upiWaiting }: UserRowProps) {
  const { limits } = user;
  const paid = paidUntil(user);
  return (
    <tr>
      <td>
        <div className="admin-user-cell">
          <Avatar name={user.username} />
          <span className="admin-user-ident">
            <span className="admin-user-line">
              <span className="admin-user-name" title={user.username}>
                {user.username}
              </span>
              {user.is_admin ? <span className="badge badge-accent">admin</span> : null}
              {user.is_active ? null : <span className="badge badge-neutral">inactive</span>}
              {user.billing_tester ? <span className="badge badge-neutral">tester</span> : null}
            </span>
            <EmailLine user={user} />
          </span>
        </div>
      </td>
      <td>
        <span className="admin-source">
          <b>
            {user.signup_source === "google" ? <GoogleMark size={14} /> : null}
            {SOURCE_LABELS[user.signup_source]}
          </b>
          <time className="admin-subtext" dateTime={user.created_at}>
            {formatDate(user.created_at)}
          </time>
        </span>
      </td>
      <td>
        <PlanLine plan={user.plan} isAdmin={user.is_admin} />
      </td>
      <td>
        <LimitMeter
          used={user.usage.chat_allowance_used}
          limit={limits.monthly_token_limit}
          noun="standard tokens"
          format={formatCompact}
        />
      </td>
      <td>
        <LimitMeter used={user.usage.images} limit={limits.monthly_image_limit} noun="images" format={formatNumber} />
      </td>
      <td className="num" title={tokenBreakdown(user.usage)}>
        {formatCost(user.usage.cost_usd)}
      </td>
      <td className="admin-nowrap">
        {limits.rate_limit_per_min ? `${formatNumber(limits.rate_limit_per_min)} / min` : "Unlimited"}
      </td>
      <td>
        <UploadsSummary user={user} />
      </td>
      <td className="admin-nowrap">
        <ModelAccess user={user} modelNames={modelNames} />
      </td>
      <td className="admin-nowrap">
        {user.last_login_at ? (
          <time dateTime={user.last_login_at} title={formatDateTime(user.last_login_at)}>
            {formatRelativeTime(user.last_login_at)}
          </time>
        ) : (
          <span className="muted">Never</span>
        )}
      </td>
      <td className="admin-actions-cell">
        {user.is_admin ? (
          <span className="admin-actions" title={ENV_MANAGED}>
            <button type="button" className="btn btn-ghost btn-sm" disabled title={ENV_MANAGED}>
              Edit
            </button>
            <button type="button" className="btn btn-ghost btn-sm" disabled title={ENV_MANAGED}>
              Delete
            </button>
            <span className="sr-only">{ENV_MANAGED}</span>
          </span>
        ) : (
          <span className="admin-actions">
            <ConfirmAction
              label="Delete"
              ariaLabel={`Delete ${user.username}`}
              message={
                <>
                  Delete <strong>{user.username}</strong> and all of their documents?
                  {paid ? (
                    <span className="admin-confirm-warning">
                      {" "}
                      {user.username} has a paid plan until {formatIstDate(paid)}. No refund is made automatically.
                    </span>
                  ) : null}
                  {upiWaiting ? (
                    <span className="admin-confirm-warning">
                      {" "}
                      {user.username} has a UPI payment of {formatInr(upiWaiting.amount_paise)} waiting for your check.
                      Confirm or reject it first.
                    </span>
                  ) : null}
                </>
              }
              confirmLabel="Delete user"
              onConfirm={onDelete}
            >
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                aria-label={`Edit ${user.username}`}
                disabled={!canEdit}
                onClick={onEdit}
              >
                Edit
              </button>
            </ConfirmAction>
          </span>
        )}
      </td>
    </tr>
  );
}

/** The user cell's second line: the confirmed email, a pending one, "No email", or the env admin's note. */
function EmailLine({ user }: { user: AdminUser }) {
  if (user.is_admin) return <span className="admin-user-email">{ENV_MANAGED}</span>;
  if (user.email) {
    return (
      <span className="admin-user-email admin-user-email-ok" title={`${user.email}, confirmed`}>
        <CheckCircleIcon size={13} />
        <span className="sr-only">Confirmed: </span>
        <span className="admin-user-email-text">{user.email}</span>
      </span>
    );
  }
  if (user.pending_email) {
    const text = `Pending: ${user.pending_email.address}`;
    return (
      <span className="admin-user-email admin-user-email-pending" title={text}>
        <ClockIcon size={13} />
        <span className="admin-user-email-text">{text}</span>
      </span>
    );
  }
  return <span className="admin-user-email">No email</span>;
}

/** What this period's cost is made of (the chat allowance counts each answer at its model's rate). */
function tokenBreakdown(usage: AdminUser["usage"]): string {
  const { chat_tokens, embedding_tokens, vision_tokens, images } = usage;
  return `Chat ${formatNumber(chat_tokens)} · Embedding ${formatNumber(embedding_tokens)} · Vision ${formatNumber(vision_tokens)} tokens · Images ${formatNumber(images)}`;
}

type LimitMeterProps = { used: number; limit: number; noun: string; format: (n: number) => string };

/**
 * This period's use against a limit (0 = unlimited): the numbers over a 5 px bar. From 90% the value starts with a
 * warning icon, at 100% an alert icon (with the words for screen readers and in the tooltip), never colour alone.
 */
function LimitMeter({ used, limit, noun, format }: LimitMeterProps) {
  if (limit === 0) {
    return (
      <span className="admin-meter-text" title={`${formatNumber(used)} ${noun}`}>
        {format(used)} <span className="muted">· Unlimited</span>
      </span>
    );
  }
  const ratio = used / limit;
  const state = meterState(used, limit);
  const summary = `${formatNumber(used)} of ${formatNumber(limit)} ${noun}${state ? `. ${state.words}` : ""}`;
  return (
    <div className="admin-meter-cell" title={summary}>
      <span className={state ? `admin-meter-text admin-meter-text-${state.tone}` : "admin-meter-text"}>
        {state ? state.tone === "danger" ? <AlertCircleIcon size={14} /> : <AlertIcon size={14} /> : null}
        <span>
          {format(used)} <span className="muted">/ {format(limit)}</span>
        </span>
        <span className="sr-only">
          {" "}
          {noun}, {Math.round(ratio * 100)}% used{state ? `. ${state.words}` : ""}
        </span>
      </span>
      <span
        className={state ? `ui-meter admin-meter ui-meter-${state.tone}` : "ui-meter admin-meter"}
        aria-hidden="true"
      >
        <span style={{ width: `${Math.min(100, ratio * 100)}%` }} />
      </span>
    </div>
  );
}

function UploadsSummary({ user }: { user: AdminUser }) {
  const { limits } = user;
  const docsMax = limits.max_documents ? formatNumber(limits.max_documents) : "∞";
  const storageMax = limits.max_storage_mb ? formatMegabytes(limits.max_storage_mb) : "∞";
  const docs = `${formatNumber(user.document_count)} of ${limits.max_documents ? docsMax : "unlimited"} documents`;
  const storage = `${formatBytes(user.storage_bytes)} of ${limits.max_storage_mb ? storageMax : "unlimited"} storage`;
  return (
    <div className="admin-uploads" title={`${docs} · ${storage}`}>
      <span>{formatNumber(limits.max_upload_mb)} MB per file</span>
      <span className="admin-subtext" aria-hidden="true">
        {formatNumber(user.document_count)}/{docsMax} docs · {formatBytes(user.storage_bytes)}/{storageMax}
      </span>
      <span className="sr-only">
        {docs}, {storage}
      </span>
    </div>
  );
}

function ModelAccess({ user, modelNames }: { user: AdminUser; modelNames: Map<number, string> }) {
  if (user.is_admin) return <>All models</>;
  if (user.no_models) return <span className="badge badge-warning">No model left</span>;
  if (!user.restrict_models) return <>Plan's models</>;
  const count = user.allowed_model_ids.length;
  if (count === 0) return <span className="badge badge-warning">No models</span>;
  const names = user.allowed_model_ids.map((id) => modelNames.get(id)).filter(Boolean);
  return (
    <span title={names.join(", ") || undefined}>
      {count} model{count === 1 ? "" : "s"}
    </span>
  );
}
