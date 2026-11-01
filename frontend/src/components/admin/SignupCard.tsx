import { useId, useState } from "react";
import type { SignupStatus } from "../../api/types";
import { formatNumber } from "../../lib/format";
import { AlertIcon, CheckCircleIcon, SettingsIcon } from "../ui/icons";
import { SIGNUP_SETTINGS_PATH } from "./adminApi";
import FlashMessage from "./FlashMessage";
import { useFlash, useResource } from "./hooks";
import { SIGNUP_POLICY_PAGES, usePolicyPages } from "./policyPages";
import { cardWarnings, mailDayResetIst, methodsLabel, offeredMethods } from "./signupChecks";
import SignupSettingsModal from "./SignupSettingsModal";
import { ResourceGate } from "./StateViews";

type SignupCardProps = {
  /** Shows the user list filtered to the last 7 days' sign-ups. */
  onShowRecent: () => void;
  onNavigate: (hash: string) => void;
};

/** Admin → Users → Sign-up: whether visitors can create accounts and how, the day's mail, the checks, the warnings. */
export default function SignupCard({ onShowRecent, onNavigate }: SignupCardProps) {
  const status = useResource<SignupStatus>(SIGNUP_SETTINGS_PATH, "Couldn't load the sign-up settings.");
  const policy = usePolicyPages(SIGNUP_POLICY_PAGES);
  const flash = useFlash();
  const [editing, setEditing] = useState(false);
  const titleId = useId();
  const loaded = status.data;

  return (
    <section className="card admin-card admin-signup" aria-labelledby={titleId}>
      <div className="admin-card-header">
        <div className="admin-card-heading">
          <div className="admin-signup-title">
            <h2 id={titleId} className="admin-card-title">
              Sign-up
            </h2>
            {loaded ? <SignupState status={loaded} /> : null}
          </div>
          <p className="admin-card-subtitle">Who can create an account, and how. New accounts start without a plan.</p>
        </div>
        <button type="button" className="btn btn-sm" disabled={!loaded} onClick={() => setEditing(true)}>
          <SettingsIcon size={15} />
          Edit…
        </button>
      </div>
      <FlashMessage flash={flash} />
      <ResourceGate resource={status} loadingLabel="Loading the sign-up settings">
        {(data) => {
          const warnings = cardWarnings(data, policy.pages);
          return (
            <>
              <dl className="admin-signup-facts">
                <div>
                  <dt>Methods</dt>
                  <MethodsTile status={data} />
                </div>
                <div>
                  <dt>Sign-ups</dt>
                  <dd>
                    {formatNumber(data.signups_today)} today
                    <small>
                      <button type="button" className="admin-link-button admin-signup-recent" onClick={onShowRecent}>
                        {formatNumber(data.signups_7d)} in 7 days · {formatNumber(data.paid_7d)} paid
                      </button>
                    </small>
                  </dd>
                </div>
                <div>
                  <dt>Email today</dt>
                  <MailTile status={data} />
                </div>
                <div>
                  <dt>Checks</dt>
                  <dd className="admin-signup-checks">
                    <Check ok={data.mail_configured} name="Email" />
                    <Check ok={data.mail_webhook_configured} name="Bounce reports" />
                    <Check ok={data.turnstile_configured} name="Turnstile" />
                    <Check ok={data.google_configured} name="Google" />
                  </dd>
                </div>
              </dl>
              {warnings.length ? (
                <div className="admin-card-footnote">
                  {warnings.map((warning) => (
                    <div key={warning.text} className="ui-notice ui-notice-warning">
                      <AlertIcon size={15} />
                      <span className="ui-notice-text">
                        {warning.text}
                        {warning.mail ? (
                          <>
                            {" "}
                            <button type="button" className="admin-link-button" onClick={() => onNavigate("system")}>
                              See Admin → System
                            </button>
                          </>
                        ) : null}
                      </span>
                    </div>
                  ))}
                </div>
              ) : null}
            </>
          );
        }}
      </ResourceGate>
      {editing && loaded ? (
        <SignupSettingsModal
          status={loaded}
          pages={policy.pages}
          onRecheckPages={policy.reload}
          onClose={() => setEditing(false)}
          onSaved={(saved) => {
            setEditing(false);
            status.setData(saved);
            flash.success(saved.settings.open ? "Saved. Sign-up is open." : "Saved. Sign-up is closed.");
          }}
        />
      ) : null}
    </section>
  );
}

function SignupState({ status }: { status: SignupStatus }) {
  if (status.effective.open) return <span className="ui-status ui-status-connected">Open</span>;
  if (status.settings.open) return <span className="ui-status ui-status-attention">Open, but no method is ready</span>;
  return <span className="ui-status ui-status-off">Closed</span>;
}

function MethodsTile({ status }: { status: SignupStatus }) {
  const label = methodsLabel(offeredMethods(status));
  return (
    <dd>
      {label}
      {status.settings.open || label === "None" ? null : <small>When sign-up opens</small>}
    </dd>
  );
}

function MailTile({ status }: { status: SignupStatus }) {
  if (!status.mail_configured) {
    return (
      <dd>
        Not configured
        <small>Set MAIL_SMTP_HOST and MAIL_FROM in .env.</small>
      </dd>
    );
  }
  const left = status.public_mail_left;
  const leftText =
    left > 0 ? `About ${formatNumber(left)} sign-up email${left === 1 ? "" : "s"} left` : "No sign-up emails left";
  return (
    <dd>
      {status.public_paused ? <AlertIcon size={15} className="admin-check-warn" /> : null}
      {formatNumber(status.mail_sent_today)} of {formatNumber(status.mail_daily_limit)}
      <small>
        {status.public_paused
          ? "Sign-up emails paused: too many bounces"
          : `${leftText} · resets ${mailDayResetIst()} IST`}
      </small>
    </dd>
  );
}

function Check({ ok, name }: { ok: boolean; name: string }) {
  return (
    <span>
      {ok ? (
        <CheckCircleIcon size={15} className="admin-check-done" />
      ) : (
        <AlertIcon size={15} className="admin-check-warn" />
      )}
      <span className="sr-only">{ok ? "Done: " : "Not set up: "}</span>
      {name}
    </span>
  );
}
