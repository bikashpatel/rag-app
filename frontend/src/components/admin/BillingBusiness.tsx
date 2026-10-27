import { useCallback, useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { ApiError, publicApi } from "../../api/client";
import type { BillingAdminConfig, BillingSettings, PageSlug } from "../../api/types";
import { DRAFT_MARKER, formatInr } from "../../lib/billing";
import { formatNumber, formatRelativeTime } from "../../lib/format";
import { ButtonSpinner } from "../ui/Spinner";
import { AlertCircleIcon, CheckCircleIcon, InfoIcon, RefreshIcon } from "../ui/icons";
import { errorMessage, reconcileNow, saveBillingSettings } from "./adminApi";
import { CopyButton, Fact, istToday, plural, TEST_LINK_CAP } from "./billingUi";
import { Field } from "./Field";
import FlashMessage from "./FlashMessage";
import { FormValidator, type FieldErrors } from "./form";
import { useFlash, type Resource } from "./hooks";
import { ResourceGate } from "./StateViews";

const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const PREFIX_PATTERN = /^[A-Z0-9]{1,4}$/;

/** The public pages, as their Markdown files name them (frontend/public/legal/<slug>.md). */
export const POLICY_PAGES: readonly { slug: PageSlug; title: string }[] = [
  { slug: "terms", title: "Terms of service" },
  { slug: "privacy", title: "Privacy policy" },
  { slug: "refunds", title: "Cancellation and refunds" },
  { slug: "shipping", title: "Shipping and delivery" },
  { slug: "contact", title: "Contact us" },
  { slug: "about", title: "About us" },
  { slug: "pricing", title: "Pricing" },
];

type PageCheck = { slug: PageSlug; title: string; state: "final" | "draft" | "missing" | "error" };

/** Reads each public page's Markdown for the draft marker (the going-live checklist). */
function usePolicyPages(): { pages: PageCheck[] | null; reload: () => void } {
  const [pages, setPages] = useState<PageCheck[] | null>(null);
  const [round, setRound] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setPages(null);
    void Promise.all(
      POLICY_PAGES.map(({ slug, title }) =>
        publicApi.page(slug, controller.signal).then(
          (text): PageCheck => ({ slug, title, state: text.trimStart().startsWith(DRAFT_MARKER) ? "draft" : "final" }),
          (err: unknown): PageCheck => ({
            slug,
            title,
            state: err instanceof ApiError && err.status === 404 ? "missing" : "error",
          }),
        ),
      ),
    ).then((checked) => {
      if (!controller.signal.aborted) setPages(checked);
    });
    return () => controller.abort();
  }, [round]);
  return { pages, reload: useCallback(() => setRound((n) => n + 1), []) };
}

/** "26-27": the Indian financial year (April to March) of today in IST. */
function financialYear(today: string): string {
  const [year, month] = today.split("-").map(Number);
  const start = month >= 4 ? year : year - 1;
  return `${String(start % 100).padStart(2, "0")}-${String((start + 1) % 100).padStart(2, "0")}`;
}

type BillingBusinessProps = { config: Resource<BillingAdminConfig>; onNavigate: (hash: string) => void };

export default function BillingBusiness({ config, onNavigate }: BillingBusinessProps) {
  const flash = useFlash();
  const policyPages = usePolicyPages();
  return (
    <ResourceGate resource={config} loadingLabel="Loading the billing settings">
      {(data) => (
        <div className="admin-stack">
          <FlashMessage flash={flash} />
          <div className="admin-billing-grid admin-billing-grid-even">
            <BusinessForm
              settings={data.settings}
              onSaved={(saved) => {
                config.setData(saved);
                flash.success("Saved the business details.");
              }}
            />
            <PaymentsSetupCard
              config={data}
              onChecked={(checked) => {
                flash.success(
                  checked === 0 ? "No pending payments needed checking." : `Checked ${plural(checked, "order")}.`,
                );
                void config.reload();
              }}
              onError={flash.error}
              onNavigate={onNavigate}
            />
          </div>
          <Checklist config={data} pages={policyPages.pages} onRecheckPages={policyPages.reload} />
        </div>
      )}
    </ResourceGate>
  );
}

type FormState = {
  business_name: string;
  address: string;
  contact_email: string;
  contact_phone: string;
  receipt_prefix: string;
  upi: string;
};
type ErrorKey = keyof FormState;

const formFrom = (settings: BillingSettings): FormState => ({
  business_name: settings.business_name,
  address: settings.address,
  contact_email: settings.contact_email ?? "",
  contact_phone: settings.contact_phone ?? "",
  receipt_prefix: settings.receipt_prefix,
  upi: String(settings.upi_limit_paise / 100),
});

function BusinessForm({
  settings,
  onSaved,
}: {
  settings: BillingSettings;
  onSaved: (config: BillingAdminConfig) => void;
}) {
  const [form, setForm] = useState(() => formFrom(settings));
  const [errors, setErrors] = useState<FieldErrors<ErrorKey>>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [invalidSubmits, setInvalidSubmits] = useState(0);
  const formRef = useRef<HTMLFormElement>(null);
  const prefix = PREFIX_PATTERN.test(form.receipt_prefix.trim()) ? form.receipt_prefix.trim() : settings.receipt_prefix;

  useEffect(() => {
    if (invalidSubmits > 0) formRef.current?.querySelector<HTMLElement>("[aria-invalid='true']")?.focus();
  }, [invalidSubmits]);

  function update(key: ErrorKey, value: string) {
    setForm((current) => ({ ...current, [key]: value }));
    setErrors((current) => (current[key] ? { ...current, [key]: undefined } : current));
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (saving) return;
    const v = new FormValidator<ErrorKey>();
    const body: BillingSettings = {
      business_name: v.text("business_name", form.business_name, 1, 100),
      address: v.text("address", form.address, 0, 500),
      contact_email: v.optionalText("contact_email", form.contact_email, 254),
      contact_phone: v.optionalText("contact_phone", form.contact_phone, 20),
      receipt_prefix: form.receipt_prefix.trim(),
      upi_limit_paise: v.integer("upi", form.upi.replace(/[,\s]/g, ""), 0, 100_000) * 100,
    };
    if (body.contact_email && !EMAIL_PATTERN.test(body.contact_email))
      v.fail("contact_email", "Enter an email address.");
    if (!PREFIX_PATTERN.test(body.receipt_prefix)) v.fail("receipt_prefix", "Use 1 to 4 capital letters or digits.");
    else if (body.receipt_prefix === "TEST") v.fail("receipt_prefix", "TEST is kept for test-mode receipts.");
    setErrors(v.errors);
    if (!v.valid) {
      setInvalidSubmits((n) => n + 1);
      return;
    }
    setSaving(true);
    setServerError(null);
    try {
      const saved = await saveBillingSettings(body);
      setForm(formFrom(saved.settings));
      onSaved(saved);
    } catch (err) {
      setServerError(errorMessage(err, "Couldn't save the business details. Please try again."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="card admin-card" aria-labelledby="admin-business-title">
      <div className="admin-card-header">
        <div className="admin-card-heading">
          <h2 id="admin-business-title" className="admin-card-title">
            Business details
          </h2>
          <p className="admin-card-subtitle">
            On receipts and the public pages. Receipts keep the details they were issued with.
          </p>
        </div>
      </div>
      <form ref={formRef} className="admin-form admin-card-body admin-business-form" onSubmit={onSubmit} noValidate>
        <div className="admin-form-grid">
          <Field label="Business name" error={errors.business_name} className="admin-span-2">
            {(control) => (
              <input
                {...control}
                name="business_name"
                className="input"
                autoComplete="organization"
                value={form.business_name}
                onChange={(e) => update("business_name", e.target.value)}
              />
            )}
          </Field>
          <Field label="Address" error={errors.address} className="admin-span-2" hint="Several lines are fine.">
            {(control) => (
              <textarea
                {...control}
                name="address"
                className="input admin-textarea"
                rows={3}
                autoComplete="street-address"
                value={form.address}
                onChange={(e) => update("address", e.target.value)}
              />
            )}
          </Field>
          <Field label="Contact email" error={errors.contact_email}>
            {(control) => (
              <input
                {...control}
                type="email"
                name="contact_email"
                className="input"
                autoComplete="email"
                value={form.contact_email}
                onChange={(e) => update("contact_email", e.target.value)}
              />
            )}
          </Field>
          <Field label="Contact phone" error={errors.contact_phone}>
            {(control) => (
              <input
                {...control}
                type="tel"
                name="contact_phone"
                className="input"
                autoComplete="tel"
                value={form.contact_phone}
                onChange={(e) => update("contact_phone", e.target.value)}
              />
            )}
          </Field>
          <Field
            label="Receipt prefix"
            error={errors.receipt_prefix}
            hint={`Receipts read ${prefix}/${financialYear(istToday())}/000001. 1 to 4 capital letters or digits.`}
          >
            {(control) => (
              <input
                {...control}
                className="input admin-mono"
                autoComplete="off"
                autoCapitalize="characters"
                spellCheck={false}
                value={form.receipt_prefix}
                onChange={(e) => update("receipt_prefix", e.target.value.toUpperCase())}
              />
            )}
          </Field>
          <Field
            label="UPI limit per payment"
            error={errors.upi}
            hint="Razorpay may limit UPI to ₹2,000 per payment for new accounts. Set 0 once Razorpay confirms there's no limit."
          >
            {(control) => (
              <div className="admin-input-prefix">
                <span aria-hidden="true">₹</span>
                <input
                  {...control}
                  className="input"
                  inputMode="numeric"
                  autoComplete="off"
                  value={form.upi}
                  onChange={(e) => update("upi", e.target.value)}
                />
              </div>
            )}
          </Field>
        </div>
        {serverError ? (
          <div className="ui-notice ui-notice-danger" role="alert">
            <AlertCircleIcon size={15} />
            <span className="ui-notice-text">{serverError}</span>
          </div>
        ) : null}
        <div className="admin-form-actions">
          <button type="submit" className="btn btn-primary" disabled={saving} aria-busy={saving || undefined}>
            {saving ? <ButtonSpinner size={14} /> : null}
            Save
          </button>
        </div>
      </form>
    </section>
  );
}

type PaymentsSetupCardProps = {
  config: BillingAdminConfig;
  onChecked: (checked: number) => void;
  onError: (message: string) => void;
  onNavigate: (hash: string) => void;
};

function PaymentsSetupCard({ config, onChecked, onError, onNavigate }: PaymentsSetupCardProps) {
  const setup = config.payments;
  const [checking, setChecking] = useState(false);
  const otherMode = setup.mode === "test" ? "live" : "test";

  async function check() {
    setChecking(true);
    try {
      onChecked((await reconcileNow()).checked);
    } catch (err) {
      onError(errorMessage(err, "Couldn't check the pending payments. Please try again."));
    } finally {
      setChecking(false);
    }
  }

  return (
    <section className="card admin-card" aria-labelledby="admin-razorpay-title">
      <div className="admin-card-header">
        <div className="admin-card-heading">
          <h2 id="admin-razorpay-title" className="admin-card-title">
            Razorpay
          </h2>
          <p className="admin-card-subtitle">
            Keys and webhook secrets live in .env; this shows what the server runs with.
          </p>
        </div>
      </div>
      <dl className="admin-billing-facts admin-card-body">
        <Fact term="Mode">
          {setup.mode === "live" ? (
            <span className="badge badge-success">Live</span>
          ) : setup.mode === "test" ? (
            <span className="badge badge-warning">Test</span>
          ) : (
            <>
              <span className="badge badge-neutral">Not set up</span>
              <span className="admin-fact-note">
                Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in .env, then restart the backend and worker.
              </span>
            </>
          )}
        </Fact>
        <Fact term="Key">{setup.key_id_hint ? <code className="admin-code">{setup.key_id_hint}</code> : "—"}</Fact>
        <Fact term="Webhook secret">
          {setup.webhook_secret_set ? (
            <span className="badge badge-success">Set</span>
          ) : (
            <span className="badge badge-neutral">Not set</span>
          )}
          {setup.previous_webhook_secret_set ? (
            <span className="admin-fact-note">The previous secret is also accepted.</span>
          ) : null}
        </Fact>
        <Fact term="Last webhook">{setup.last_webhook_at ? formatRelativeTime(setup.last_webhook_at) : "Never"}</Fact>
        <Fact term="Webhook URL">
          <span className="admin-copy-row">
            <code className="admin-code admin-code-wrap">{setup.webhook_url}</code>
            <CopyButton value={setup.webhook_url} label="Copy the webhook URL" />
          </span>
          <span className="admin-fact-note">Subscribe to these events:</span>
          <span className="admin-event-chips">
            {setup.webhook_events.map((event) => (
              <code key={event} className="admin-code">
                {event}
              </code>
            ))}
          </span>
        </Fact>
        {setup.mode === "test" ? (
          <Fact term="Test links">
            {formatNumber(setup.test_links_created)} of about {TEST_LINK_CAP}
            <span
              className={
                setup.test_links_created >= TEST_LINK_CAP * 0.8
                  ? "ui-meter ui-meter-warning admin-link-meter"
                  : "ui-meter admin-link-meter"
              }
              role="meter"
              aria-label="Test links used"
              aria-valuemin={0}
              aria-valuemax={TEST_LINK_CAP}
              aria-valuenow={Math.min(setup.test_links_created, TEST_LINK_CAP)}
              aria-valuetext={`${formatNumber(setup.test_links_created)} of about ${TEST_LINK_CAP}`}
            >
              <span style={{ width: `${Math.min(100, (setup.test_links_created / TEST_LINK_CAP) * 100)}%` }} />
            </span>
          </Fact>
        ) : null}
        <Fact term="Testers">
          {setup.testers === 0
            ? "Nobody can make test payments."
            : `${plural(setup.testers, "user")} can make test payments.`}{" "}
          <button type="button" className="admin-link-button" onClick={() => onNavigate("users")}>
            Mark testers in Users
          </button>
        </Fact>
        <Fact term="Payment checks">
          {setup.reconciler_last_run_at
            ? `Last run ${formatRelativeTime(setup.reconciler_last_run_at)}`
            : "Not run yet"}
        </Fact>
        {setup.paused_orders > 0 ? (
          <Fact term="Paused orders">
            {plural(setup.paused_orders, `${otherMode} order`)} can't be checked with {setup.mode ?? "these"} keys.
          </Fact>
        ) : null}
        <Fact term="Reminder emails">
          {setup.email_reminders ? "On" : "Off: set BILLING_EMAIL_FROM and the SMTP settings in .env to send them."}
        </Fact>
      </dl>
      {setup.last_error ? (
        <div className="ui-notice ui-notice-danger admin-inline-notice" role="alert">
          <AlertCircleIcon size={15} />
          <span className="ui-notice-text">{setup.last_error}</span>
        </div>
      ) : null}
      <div className="admin-card-actions">
        <button
          type="button"
          className="btn btn-sm"
          disabled={checking || !setup.configured}
          aria-busy={checking || undefined}
          onClick={() => void check()}
        >
          {checking ? <ButtonSpinner size={13} /> : <RefreshIcon size={15} />}
          Check pending payments now
        </button>
      </div>
    </section>
  );
}

type ChecklistProps = { config: BillingAdminConfig; pages: PageCheck[] | null; onRecheckPages: () => void };

function ChecklistRow({ done, optional, children }: { done: boolean | null; optional?: boolean; children: ReactNode }) {
  return (
    <li className="admin-check-row">
      {done === null ? (
        <InfoIcon size={20} className="admin-check-info" />
      ) : done ? (
        <CheckCircleIcon size={20} className="admin-check-done" />
      ) : (
        <span className="ui-dashed admin-check-todo" aria-hidden="true" />
      )}
      <span className="admin-check-text">
        {done === null ? null : (
          <span className="sr-only">{done ? "Done: " : optional ? "Optional, not done: " : "To do: "}</span>
        )}
        {children}
      </span>
    </li>
  );
}

/** What must be true before live payments (plans-billing.md 8.11); the last rows are reminders to confirm by hand. */
function Checklist({ config, pages, onRecheckPages }: ChecklistProps) {
  const { settings, payments } = config;
  const missing = (
    [
      ["business_name", "business name"],
      ["address", "address"],
      ["contact_email", "contact email"],
      ["contact_phone", "contact phone"],
    ] as const
  ).filter(([key]) => !settings[key]);
  const unfinished = pages?.filter((page) => page.state !== "final") ?? [];
  const titleId = useId();
  return (
    <section className="card admin-card" aria-labelledby={titleId}>
      <div className="admin-card-header">
        <div className="admin-card-heading">
          <h2 id={titleId} className="admin-card-title">
            Going-live checklist
          </h2>
          <p className="admin-card-subtitle">Razorpay reviews the website before it enables live payments.</p>
        </div>
      </div>
      <ul className="admin-checklist-rows">
        <ChecklistRow done={missing.length === 0}>
          Business details filled
          {missing.length ? (
            <small>
              Missing: {missing.map(([, label]) => label).join(", ")}.{" "}
              <button
                type="button"
                className="admin-link-button"
                onClick={() =>
                  document.querySelector<HTMLElement>(`.admin-business-form [name="${missing[0][0]}"]`)?.focus()
                }
              >
                Fill them in
              </button>
            </small>
          ) : null}
        </ChecklistRow>
        <ChecklistRow done={pages === null ? false : unfinished.length === 0}>
          No draft policy pages
          {pages === null ? (
            <small>Checking the pages…</small>
          ) : unfinished.length ? (
            <small>
              {unfinished.map((page, index) => (
                <span key={page.slug}>
                  {index ? ", " : ""}
                  <a href={`/${page.slug}`} target="_blank" rel="noreferrer">
                    {page.title}
                  </a>{" "}
                  ({page.state === "draft" ? "draft" : page.state === "missing" ? "missing" : "couldn't be read"})
                </span>
              ))}
              . Replace frontend/public/legal/&lt;page&gt;.md in the repo, rebuild the frontend, then{" "}
              <button type="button" className="admin-link-button" onClick={onRecheckPages}>
                check again
              </button>
              .
            </small>
          ) : null}
        </ChecklistRow>
        <ChecklistRow done={payments.mode === "live"}>
          Live keys in .env
          {payments.mode === "live" ? null : (
            <small>RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET from Razorpay's live mode, after KYC.</small>
          )}
        </ChecklistRow>
        <ChecklistRow done={payments.webhook_secret_set && payments.last_webhook_at !== null}>
          Webhook secret set and a webhook received
          {payments.webhook_secret_set && payments.last_webhook_at ? null : (
            <small>
              Register the webhook URL above with its events in Razorpay's Dashboard and set RAZORPAY_WEBHOOK_SECRET.
            </small>
          )}
        </ChecklistRow>
        <ChecklistRow done={null}>
          Confirm the UPI limit with Razorpay and set it above
          <small>
            Now: {settings.upi_limit_paise ? `${formatInr(settings.upi_limit_paise)} per payment` : "no limit"}.
          </small>
        </ChecklistRow>
        <ChecklistRow done={null}>Keep Customer Fee Bearer and Offers off in Razorpay's Dashboard</ChecklistRow>
        <ChecklistRow done={null}>
          Refund from here, not from Razorpay's Dashboard, so access follows the refund
        </ChecklistRow>
        <ChecklistRow done={payments.email_reminders} optional>
          Reminder emails set up <span className="admin-subtext">(optional)</span>
        </ChecklistRow>
      </ul>
    </section>
  );
}
