import { useMemo, useState } from "react";
import type { AdminConfig, AdminModel, AdminUser, UserLimits } from "../../api/types";
import {
  formatBytes,
  formatCompact,
  formatCost,
  formatDateTime,
  formatNumber,
  formatRelativeTime,
} from "../../lib/format";
import { deleteResource } from "./adminApi";
import ConfirmAction from "./ConfirmAction";
import FlashMessage from "./FlashMessage";
import { useFlash, useResource } from "./hooks";
import Avatar from "../ui/Avatar";
import { AlertCircleIcon, AlertIcon, PlusIcon } from "../ui/icons";
import { meterState } from "../ui/Meter";
import { RefreshError, ResourceGate } from "./StateViews";
import UserFormModal from "./UserFormModal";

const MB = 1024 * 1024;
const ENV_MANAGED = "Managed in .env";

function sortUsers(users: AdminUser[]): AdminUser[] {
  return [...users].sort((a, b) => Number(b.is_admin) - Number(a.is_admin) || a.username.localeCompare(b.username));
}

/** The admin bypasses every usage check, so its stored limits don't apply. */
function effectiveLimits(user: AdminUser, hardCap: number | undefined): UserLimits {
  if (!user.is_admin) return user;
  return {
    monthly_token_limit: 0,
    rate_limit_per_min: 0,
    max_upload_mb: hardCap ?? user.max_upload_mb,
    max_documents: 0,
    max_storage_mb: 0,
    monthly_image_limit: 0,
  };
}

export default function UsersTab({ onChanged }: { onChanged: () => void }) {
  const users = useResource<AdminUser[]>("/admin/users", "Couldn't load users.");
  const config = useResource<AdminConfig>("/admin/config", "Couldn't load the default user limits.");
  const models = useResource<AdminModel[]>("/admin/models", "Couldn't load the model list.");
  const flash = useFlash();
  const [editing, setEditing] = useState<AdminUser | "new" | null>(null);

  const chatModels = useMemo(() => models.data?.filter((model) => model.kind === "chat") ?? null, [models.data]);
  const modelNames = useMemo(
    () => new Map((models.data ?? []).map((model) => [model.id, model.display_name])),
    [models.data],
  );

  async function deleteUser(user: AdminUser) {
    await deleteResource(`/admin/users/${user.id}`);
    users.setData((list) => list && list.filter((u) => u.id !== user.id));
    flash.success(`Deleted ${user.username} and their documents.`);
    onChanged();
  }

  function handleSaved(saved: AdminUser, created: boolean) {
    setEditing(null);
    users.setData((list) => list && sortUsers([...list.filter((u) => u.id !== saved.id), saved]));
    flash.success(created ? `Created ${saved.username}.` : `Saved changes to ${saved.username}.`);
    if (created) onChanged();
  }

  return (
    <section className="card admin-card" aria-labelledby="admin-users-title">
      <div className="admin-card-header">
        <div className="admin-card-heading">
          <h2 id="admin-users-title" className="admin-card-title">
            Users
          </h2>
          <p className="admin-card-subtitle">
            Accounts, monthly token budgets and image limits, rate limits and upload quotas.
          </p>
        </div>
        <span className="spacer" />
        <button type="button" className="btn btn-primary" disabled={!config.data} onClick={() => setEditing("new")}>
          <PlusIcon size={16} />
          New user
        </button>
      </div>
      {config.error && !config.data ? <RefreshError message={config.error} onRetry={() => void config.reload()} /> : null}
      <FlashMessage flash={flash} />
      <ResourceGate resource={users} loadingLabel="Loading users">
        {(list) => (
          <>
            <div className="table-wrap">
              <table className="table admin-table">
                <thead>
                  <tr>
                    <th scope="col">User</th>
                    <th scope="col">Tokens this month</th>
                    <th scope="col">Images this month</th>
                    <th scope="col" className="num">
                      Cost
                    </th>
                    <th scope="col">Rate limit</th>
                    <th scope="col">Uploads</th>
                    <th scope="col">Models</th>
                    <th scope="col">Last login</th>
                    <th scope="col">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {list.map((user) => (
                    <UserRow
                      key={user.id}
                      user={user}
                      limits={effectiveLimits(user, config.data?.max_upload_mb_hard)}
                      modelNames={modelNames}
                      canEdit={config.data !== null}
                      onEdit={() => setEditing(user)}
                      onDelete={() => deleteUser(user)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
            {list.length <= 1 ? (
              <p className="admin-card-footnote">No other users yet. Use “New user” to create the first account.</p>
            ) : null}
          </>
        )}
      </ResourceGate>
      {editing && config.data ? (
        <UserFormModal
          user={editing === "new" ? null : editing}
          config={config.data}
          chatModels={chatModels}
          modelsError={models.error}
          onClose={() => setEditing(null)}
          onSaved={handleSaved}
        />
      ) : null}
    </section>
  );
}

type UserRowProps = {
  user: AdminUser;
  limits: UserLimits;
  modelNames: Map<number, string>;
  canEdit: boolean;
  onEdit: () => void;
  onDelete: () => Promise<void>;
};

function UserRow({ user, limits, modelNames, canEdit, onEdit, onDelete }: UserRowProps) {
  return (
    <tr>
      <td>
        <div className="admin-user-cell">
          <Avatar name={user.username} />
          <span className="admin-user-name">{user.username}</span>
          {user.is_admin ? <span className="badge badge-accent">admin</span> : null}
          {user.is_active ? null : <span className="badge badge-neutral">inactive</span>}
        </div>
      </td>
      <td>
        <LimitMeter used={user.usage.chat_tokens} limit={limits.monthly_token_limit} noun="tokens" format={formatCompact} />
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
        <UploadsSummary user={user} limits={limits} />
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

/** What this month's cost is made of (only chat tokens count toward the budget; images toward the image limit). */
function tokenBreakdown(usage: AdminUser["usage"]): string {
  const { chat_tokens, embedding_tokens, vision_tokens, images } = usage;
  return `Chat ${formatNumber(chat_tokens)} · Embedding ${formatNumber(embedding_tokens)} · Vision ${formatNumber(vision_tokens)} tokens · Images ${formatNumber(images)}`;
}

type LimitMeterProps = { used: number; limit: number; noun: string; format: (n: number) => string };

/**
 * This month's use against a monthly limit (0 = unlimited): the numbers over a 5 px bar. From 90% the value starts with
 * a warning icon, at 100% an alert icon (with the words for screen readers and in the tooltip), never colour alone.
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
      <span className={state ? `ui-meter admin-meter ui-meter-${state.tone}` : "ui-meter admin-meter"} aria-hidden="true">
        <span style={{ width: `${Math.min(100, ratio * 100)}%` }} />
      </span>
    </div>
  );
}

function UploadsSummary({ user, limits }: { user: AdminUser; limits: UserLimits }) {
  const docsMax = limits.max_documents ? formatNumber(limits.max_documents) : "∞";
  const storageMax = limits.max_storage_mb ? formatBytes(limits.max_storage_mb * MB) : "∞";
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
  if (user.is_admin || !user.restrict_models) return <>All models</>;
  const count = user.allowed_model_ids.length;
  if (count === 0) return <span className="badge badge-warning">No models</span>;
  const names = user.allowed_model_ids.map((id) => modelNames.get(id)).filter(Boolean);
  return (
    <span title={names.join(", ") || undefined}>
      {count} model{count === 1 ? "" : "s"}
    </span>
  );
}
