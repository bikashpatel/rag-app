# Brief: Plans, checkout and plan-based access

> Requirements for paid plans: a plan page after sign-in, checkout and payment through Razorpay (UPI, netbanking,
> cards, wallets), prepaid 30-day periods renewed by paying again, and access to the app's features by plan, enforced
> by the backend and mirrored by the UI. Also: the admin's plan editor and billing pages, complimentary plans,
> receipts, disputes, the public policy pages Razorpay requires, the migration and the tests. `SPEC.md` is the binding
> contract. Section 7 of this brief is the contract change; stage 0 (section 13) applies it to `SPEC.md`,
> `frontend/src/api/types.ts` and the backend schemas before any feature code is written. After that, if this brief
> and `SPEC.md` disagree, `SPEC.md` wins and the brief is fixed. Razorpay facts come from
> [`payments-razorpay-research.md`](payments-razorpay-research.md) ("research" below, with its section numbers and
> recommendations R1 to R6). General rules: [`README.md`](README.md). Looks follow
> [`plans-billing-visual.md`](plans-billing-visual.md) ("the visual brief"), itself built on
> [`ui-visual-design.md`](ui-visual-design.md). On looks the visual brief wins; on behaviour, accessibility and the
> contract this brief wins.
>
> **Revision 2** (2026-10-26), final: designed and reviewed; built and deployed on 2026-10-27. Revision 1 was reviewed for security (15
> findings), for product (15 findings), and by the designer (15 suggestions, S1 to S15 of the visual brief). Section
> 0.2 records the response to each; 0.3 the suggestions not taken and why.
>
> Nothing here is legal or tax advice. The policy texts and anything about GST are for the owner and their
> accountant to confirm (section 14).

The owner asked for:

1. After login, users **select a plan** to use the app's features:
   - **Starter**, Rs.500 per month: normal access (chat with documents), without document generation or image
     generation;
   - **Standard**, Rs.2000 per month: adds document generation (files from chat);
   - **Professional**, Rs.5000 per month: full access, with document and image generation and the Gmail plugin.
2. Selecting a plan leads to **checkout and payment** by UPI, bank transfer and similar.
3. **Access to features follows the plan**, in the backend and the UI.

The owner decided, when asked:

- **Razorpay** (UPI, netbanking, cards). A plan activates automatically on a confirmed payment. The owner creates the
  Razorpay account (test mode now, KYC before live) and adds the keys to `.env` later. The public HTTPS domain
  (Cloudflare) is coming but isn't ready, so webhooks may not be reachable at first.
- **Prepaid 30-day periods**, renewed by paying again, with reminders before expiry. No auto-debit mandates.
- **No plan or an expired plan means no access** until the user pays: only the plan page, checkout, their account and
  Settings. Their chats and documents are kept and come back once they pay. The admin is exempt.
- **Plans also set the usage limits** (tokens, documents, storage, images and so on), with sensible defaults the admin
  can edit per plan, and per-user overrides.

## 0. Decisions

| # | Decision | Why |
|---|---|---|
| B1 | **Plans are rows of a `plans` table**: an immutable `code`, name, description, price in whole rupees (stored in paise), `rank`, status (`active` \| `retired`), `recommended`, features (`files`, `images`, `plugins: [ids]`), the seven limits, a chat-model list (or every model) and an optional default chat model. Starter, Standard and Professional are seeded at the first start with the values of 3.2; the admin edits them in Admin → Plans | Editable plans and limits (owner). A JSON `plugins` list means a later plugin needs no migration. Two statuses are enough for one owner (review PRD-15) |
| B2 | **Access comes from plan periods, not a subscription object.** A `plan_periods` row holds a **snapshot** of the plan's features, limits and models. A paid period starts when its payment is applied and **ends at the first midnight IST at or after 30 days later**, so "valid through 25 Nov" means the whole of 25 Nov (30 to 31 days). A user has access while one of their counting, unrevoked periods contains "now", or while they hold an open-ended complimentary plan (B23). Nothing renews by itself | Prepaid, renewed by paying again (owner). Periods make stacking, upgrades, comps and refunds plain row operations. A day boundary users can trust, with a few free hours instead of a cut-off at 03:12 (PRD-7) |
| B3 | **One function computes what a user may do**: `billing.entitlements.load(db, user) → Entitlements`. Every check in the backend uses it. The UI only mirrors `Me.plan` | One source of truth. A UI-only gate would be a bypass |
| B4 | **No access answers 402** with a machine-readable `code` (`plan_required`, `plan_expired`, `feature_not_in_plan`) on every route that uses the product. Sign-in, Settings (preferences, account, usage, export, delete all chats), billing, the plugin catalog, Disconnect, every delete, and listing and downloading one's own documents stay open (B43). Nothing is deleted on expiry | Owner: no access until they pay, data kept. Getting one's own data out never depends on payment |
| B5 | **Allowances run per period.** The chat allowance, images and vision tokens are counted from the start of the current period. The field names keep "monthly" (contract stability) and mean "per period". An open-ended complimentary plan and the admin count per UTC calendar month | Calendar months would give a user who pays on the 28th two allowances for one payment |
| B6 | **Per-user overrides live in the existing `users` limit columns, made nullable** (NULL = the plan's value; 0 = unlimited). `user_image_limits` folds into a new `users.monthly_image_limit`; `users.vision_monthly_token_limit` is new. **Model restrictions only narrow**: with `restrict_models`, the user's models are the plan's models **∩** the user's allowed list | A cheap plan must never carry a dearer plan's models (review SEC-2). A missed read of an old integer column fails loudly in tests |
| B7 | **Plans offer different chat models**: Starter gets Claude Haiku 5.5 and GPT-5.4 mini, Standard adds Claude Sonnet 5.5, Professional gets every enabled chat model. Each plan may name a default model (Starter Haiku, Standard Sonnet, Professional the server default) | A difference users can see between the plans. Cost safety no longer depends on it (B8) |
| B8 | **The chat allowance counts each answer's cost in "standard tokens"**: one standard token is $3.20 per million (Claude Sonnet 5.5's blended price), so an answer is counted as `ceil(cost_usd ÷ $0.0000032)`, and as its plain token count when the model has no price. Haiku answers count about 1/20 of their tokens, Opus about twice. The limit field keeps its name (`monthly_token_limit`); the UI calls it the **chat allowance** and shows it mainly as **answers** ("about 1,600 answers a month with Claude Haiku 5.5") | Token budgets sized for the dearest model gave Starter about 200 answers for ₹500 and made a higher plan give fewer Haiku answers than a lower one (PRD-6). Counting by cost bounds the owner's worst case for any model and any model list, and degrades to plain tokens when prices are unknown |
| B9 | **Plan edits apply to periods created afterwards.** A period keeps its snapshot. Saving can **also apply to current and scheduled periods** (the admin's explicit choice); lowering a limit or removing a feature for paid periods needs a second confirmation. A price change never touches a paid period. Open-ended complimentary plans follow the plan live. `retired` plans can't be bought or renewed; their periods run out. A plan that was ever used can't be deleted | A user who paid for a set of limits keeps them for that period (review SEC-14) |
| B10 | **No GST in version 1.** The app is built for a seller without a GSTIN: the price is the charge, receipts are titled "Receipt" and carry no tax line, and there are no GST settings. Tax invoices (SAC code, place of supply, CGST and SGST or IGST) are a separate later feature if the owner registers (14.1) | A receipt with one "GST at 18%" line wouldn't serve a registered seller anyway, and the likely launch case needs none (PRD-10, PRD-15) |
| B11 | **Two checkout kinds.** `period` adds one period of the chosen plan, starting now when the user has no current period, else when the paid time ends (renewal, and a switch to another plan at renewal). `now` starts a new 30-day period of a plan **now**, replacing the current period for a credit (4.4): an **upgrade** to a higher plan, or **"Start a new period now"** on the same plan once 90% of an allowance is used. Checkout offers at most **one** period scheduled after the current one | Covers new, renew, downgrade at renewal, upgrade and running out early without a subscription state machine (PRD-1, PRD-4, PRD-15) |
| B12 | **A `now` checkout costs the plan's full price minus a credit** for the current period: `credit = floor_to_rupee(basis × min(time left share, unused allowance share))`, at least ₹1 charged. The new period gets a fresh allowance. Scheduled periods keep their plan and move to after the new one; future periods are never upgraded | The allowance a user gets is always paid for in full: an upgrade on the last day can't buy Professional's allowance for ₹150 (SEC-1, PRD-1). One line at checkout instead of per-period proration |
| B13 | **Downgrades take effect at the next renewal**: a `period` of the lower plan, which starts when the paid time ends. No refund of the difference. The checkout lists **what will change** (features, models, a document count over the new limit) | Owner's rule. Nothing to undo mid-period. No surprise on the switch date (PRD-9) |
| B14 | **Razorpay Payment Links through the API (R1)**: one Standard link per checkout attempt, expiring after 20 minutes. The browser leaves the app in the same tab for Razorpay's page and comes back by a signed GET. **No Razorpay JavaScript on our origin, so the CSP doesn't change** | No third-party script beside the session. UPI intent on phones, QR on desktop. Works on localhost before the domain exists (to verify, 14.2) |
| B15 | **A plan activates only on a capture fetched from Razorpay** (R2): by the callback after its signature check, by the worker after a signed webhook, or by the reconciler. All of them call one `apply_payment`, which acts **exactly once per Razorpay payment id** and only when the amount, currency, order and mode match what was stored. **A webhook never changes state from its payload** | A redirect can be lost, replayed or forged; a webhook body can be replayed, and a leaked webhook secret alone must not create plans (SEC-7) |
| B16 | **A reconciler in the worker** polls open checkouts: every 30 s while the link is live; afterwards hourly while a payment is in flight, else at 1 h, 6 h and 24 h steps, until 5 days after expiry (late authorisation, research 2.2). A late capture is applied or goes to the admin's review. A payment is never lost | Webhooks need a public URL that doesn't exist yet (research 3.4). Captures can arrive days late. Fewer calls for links nobody paid (SEC-10) |
| B17 | **Webhooks are optional until a public URL exists, and required before live keys** (the going-live checklist and the monitor say so). When set up: HMAC over the raw body (current or previous secret), dedupe by event id **and** body hash, stale bodies ignored, the event recorded and the worker woken; 200 within milliseconds | A faster path into the same verified apply, never the only one. Disputes and Dashboard refunds reach the app only this way (B21, B41) |
| B18 | **The callback carries no session** (the cookie is `SameSite=Strict`): it finds the checkout by the signed link id and reference, never by a cookie, and always answers 303 to `/billing/return?order=<public id>` on the origin stored with the order (one of `PUBLIC_BASE_URLS`). No open redirect | Research 1.4. Same pattern as the Gmail OAuth callback |
| B19 | **One open checkout per user.** A new checkout creates its link first and only then cancels the previous one; an open order for the same plan, kind and amount with 10 minutes left is reused. At most 5 links per user per hour and 15 per day | A Razorpay outage never leaves the user without a working link; paying twice by accident is unlikely. Test mode allows about 30 links per business (SEC-10) |
| B20 | **Test and live mode** follow the key id (`rzp_test_` or `rzp_live_`). Orders, payments, periods and receipts record their mode. **With test keys only users the admin marks as testers can check out**, and a test period lasts 1 day. Periods paid in test mode don't count while the server runs live keys. Test receipts have their own number series and a watermark | Test keys on the public site must not hand every user a free plan (SEC-8). Switching to live keys leaves no free plans behind |
| B21 | **Refunds start in Admin → Billing** (Razorpay's refund API with an idempotency key). Each refund's state changes once; the payment's refunded amount is the sum of its processed refunds. **A full refund ends what the payment bought.** A partial refund changes access only when the admin ticks "End access". Refunds made in Razorpay's Dashboard are recorded from webhooks | The money went back, so the access goes. No double counting from the three sources (SEC-5). Refunding from the app is the documented way (PRD-15) |
| B22 | **Offline bank transfers** (NEFT, RTGS, IMPS to the owner's account) are recorded by the admin (at least ₹1; a note when below the plan's price) and go through the same apply path, with a receipt, method `bank_transfer` | The owner listed bank transfer. Razorpay's own Bank Transfer method needs approval (research 5.2) |
| B23 | **Complimentary plans**: a **dated** grant (1 to 90 days, stored as rows of at most 30 days; a row shorter than 30 days gets its allowances scaled to its length) or an **open-ended** one (no end date: the plan's live values, allowances per calendar month, until the admin ends it). A current period always takes precedence over an open-ended plan. The admin can also move a user's periods to another plan at no charge, and revoke a period | Trials, the Razorpay reviewer, family and staff, and the owner's own account without twelve yearly rows (PRD-3). A 1-day row must not carry a 30-day allowance (SEC-1) |
| B24 | **Rollout**: at the first start with this feature, every active non-admin user gets an **open-ended complimentary Professional plan** ("Existing account when plans were introduced"). Every per-user limit becomes NULL (the plan's value); the old values are kept in the audit trail. Users created later have no plan until they pay or get a grant | The only existing user is `bikash`, very likely the owner's own account, which must not lock itself out after 30 days (PRD-3). Legacy limits must not follow a user onto a cheaper plan (SEC-2) |
| B25 | **Reminders**: an in-app banner from 5 days before the paid time ends (`BILLING_REMINDER_DAYS`), and emails 5 days before, 1 day before and at the end, **only when** SMTP and `BILLING_EMAIL_FROM` are set and the user gave an email. None when a later period is paid or an open-ended plan follows | Owner: reminders before expiry. Users have no email address today, so email is opt-in and asked for only when it can be sent (PRD-12) |
| B26 | **Gmail outside the plan**: the connection is kept but unused (no Google call), shown as "Not in your plan" with Upgrade and Disconnect. It works again after an upgrade without reconnecting, unless Google ended it meanwhile | Owner: keep but disable, with reconnect later |
| B27 | **`create_document` and `create_image` are not offered without the feature.** The system prompt tells the model the user's plan doesn't include it and which plan does | The model must not promise a file it can't make. Starter answers get cheaper (about 1,550 and 1,040 fewer input tokens) |
| B28 | **Documents over a lowered limit are kept.** Adding (uploads, generated files, images, Gmail imports) is refused until the user is under the limit; search still uses everything. Generated files and images stay viewable and downloadable on every plan; reprocessing a document ignores the file-size limit | Data kept (owner). A file accepted once stays usable (PRD-9) |
| B29 | **One receipt per applied payment**, numbered `<prefix>/<yy>-<yy>/<seq>` per Indian financial year (April to March, IST) and per mode (`RA/26-27/000001`). The row is an immutable snapshot; the PDF is rendered on demand with fpdf2 and the bundled Noto Sans (which has ₹) | Sequential numbering per financial year is how Indian receipts are kept. A snapshot can't drift when settings change |
| B30 | **Billing records outlive the user** (`user_id` set null, the username kept). Minimal personal data: no customer details sent to Razorpay; no payer email, phone, UPI id, card or bank stored anywhere, events included; only the method | Accounting needs payments after a user is deleted. Razorpay's page collects what it needs itself (SEC-12) |
| B31 | **Public pages** `/pricing`, `/terms`, `/privacy`, `/refunds`, `/contact`, `/about`, `/shipping`, reachable without signing in. Their text is **Markdown files in the repo** (`frontend/public/legal/`), seeded as marked placeholders that show a "Draft" band; the owner's text replaces them through the repo. Pricing is generated from the plans; business details come from the billing settings | Razorpay's website review needs these pages before live keys (research 6.2). Seven pages that change rarely don't need an editor, a table and three endpoints (PRD-15) |
| B32 | **Lapsed users** (no plan or expired) land on `/plans` after sign-in and when they open an app route. A plan that ends while a page is open doesn't move the user: the chat shows an inline "plan ended" panel and keeps the unsent message | Owner: only the plan page, checkout, account and Settings. Losing a typed message to a redirect is worse than one more panel (PRD-7) |
| B33 | **`Me.plan`** tells the UI everything it gates on: state, access, plan, period, valid-through date, the next plan, expiring soon, features, an open checkout, whether a new period can start now, and `unlock` (the cheapest active plan that includes each locked feature) | One boot request, as with preferences |
| B34 | **Money is integer paise everywhere** (no floats), whole rupees for prices, shown in en-IN grouping (₹1,00,000). Periods are UTC instants; **every billing date users see, server messages and emails included, is an IST date** | Razorpay takes paise. India's financial year is IST, and one date must read the same in the banner, the error, the receipt and the email (PRD-7) |
| B35 | **No new dependency**: Razorpay is called with `httpx` (pinned) and HMAC from the standard library (R4); receipts use `fpdf2` (pinned) | The pinning rule. The SDK is synchronous |
| B36 | **Monitoring gets a `billing` check**: payments needing review, disputes to answer, a stalled reconciler, refunds pending over 7 days, webhook failures, and configuration risks (live keys without a webhook, test keys on a public origin, rejected keys) | Payment problems must alert like the rest (SPEC section 10) |
| B37 | **Migration 0003** changes the schema and clears per-user limits (B24). Seeding plans and the rollout run in `bootstrap.py`, once each (`app_state` flags) | A fresh database has no models when migrations run, so plan model lists are seeded after `_seed_models` |
| B38 | **nginx's upload check asks a new `GET /api/auth/upload-allowed`**, which answers 403 for a user without access; nginx turns that into a 402 before the upload body is read | FastAPI parses the multipart body before route dependencies run |
| B39 | **No self sign-up.** Users are still created only by the admin. Razorpay's reviewer gets an account the admin creates (and marks as a tester while test keys run) | CLAUDE.md. Sign-up would need email verification and abuse controls |
| B40 | **The admin can't buy a plan** (403 `admin_exempt`) and sees plans as a preview | The admin is exempt (owner, CLAUDE.md) |
| B41 | **Disputes (chargebacks) are tracked**: the six `payment.dispute.*` webhooks, a `billing_disputes` table, a "Needs attention" item and a monitor warning naming the response deadline; a lost dispute ends access like a full refund and is counted in the summary | A dispute ignored past its deadline is lost by default, and the user would keep the plan (SEC-4) |
| B42 | **Every per-user money or access change takes the user's row lock first**, in one lock order (user → order → payment → refund or dispute → receipt counter), and **no Razorpay call runs inside a transaction or under a lock** | Two payments, a grant and a revoke can otherwise race into overlapping periods; a webhook must never wait on a lock held over an 8 s HTTP call (SEC-3, SEC-13) |
| B43 | **"Your data" for lapsed users**: Settings → Plugins & data lists their own documents (download, delete) and chats (delete) | The owner's "account and Settings" rule, without holding the user's own files hostage (PRD-5) |
| B44 | **One recommended plan** (`plans.recommended`, seeded on Standard) is emphasised on the plan page for users without a plan | One focal point for a new user (designer S1) |
| B45 | **Every admin change that grants or removes value is audited** in `billing_events`: grants, comps, plan changes, revokes, overrides, model lists, tester flags, plan edits applied to periods, offline payments | Value must not move without a trace (SEC-14) |

### 0.1 Rejected alternatives

- **Standard Checkout (the `checkout.js` modal).** Unversioned third-party JavaScript on the page that holds the
  session, and CSP exceptions (research 1.1). A later option only on a separate payment origin (R6).
- **Razorpay Subscriptions, UPI Autopay or e-mandates.** The owner ruled out auto-debit.
- **A `subscriptions` table with a status machine.** Periods (B2) express renewals, stacking, upgrades, comps and
  refunds as row operations, and "has access" is one indexed query.
- **Revision 1's upgrade: time-prorated price, full target allowance.** It sold Professional's whole allowance for
  about ₹150 on a Starter period's last day, every month (SEC-1, PRD-1). Prorating every limit as well would fix the
  abuse but leave three-period upgrade arithmetic and a confusing checkout.
- **Plain token budgets.** Sized for the dearest allowed model, they gave poor value on the default models and
  inverted between plans once Starter got more (PRD-6). An allowance in rupees ("credits") is the same idea with a
  less familiar unit; standard tokens keep the word the app already uses.
- **Calendar-month allowances for paid periods.** Rejected by B5.
- **GST options in version 1** (inclusive or exclusive prices, a rate, a GSTIN, a tax line). B10.
- **A page editor for the policy pages** (a `site_pages` table, three endpoints, an editor with preview). B31.
- **A `hidden` plan status.** For one owner, a plan is offered or retired (PRD-15). A live payment test uses Starter
  with a `smoke-*` user and a full refund (14.2).
- **More than one period paid ahead.** Re-chaining and upgrade arithmetic for queues nobody asked for (PRD-15).
- **A separate per-user "billing exempt" switch.** Open-ended complimentary plans (B23) do the same with a plan and
  limits attached, in one concept.
- **Daily refund and dispute syncs from Razorpay's list APIs.** Live keys need the domain, and the webhook is a
  going-live requirement (B17); refunds are made from the app.
- **Plan edits applying immediately to every paid period.** A user would lose limits they paid for (B9).
- **Immediate downgrades with a prorated refund.** More refunds and fees for little gain (B13).
- **The Razorpay Python SDK.** Synchronous `requests` (R4).
- **Storing receipt PDFs.** A snapshot row renders the same PDF every time and is in every backup.
- **Activating on the webhook payload, or on the redirect alone.** B15.
- **A grace period after expiry.** The owner chose no access until payment. Reminders come first (B25).
- **Showing conversations read-only to lapsed users.** The owner chose no access. Titles can be listed and deleted
  (B43); transcripts are in the data export.

### 0.2 Review of revision 1: responses

Three reviews: security (SEC-1 to SEC-15), product (PRD-1 to PRD-15) and the designer's suggestions (DS-1 to DS-15,
the visual brief's S1 to S15). Every point is answered: 37 accepted as proposed or with the variant named, 7 accepted
in part, 1 superseded. 0.3 lists the parts not taken.

| # | Point (severity) | Response | Where |
|---|---|---|---|
| SEC-1 | Upgrades prorate the price but unlock the whole target allowance; short comp rows get full allowances (high) | Accepted, through PRD-1's rule: a `now` checkout starts a fresh period at full price minus a credit of `min(time left, allowance unused)`, so every allowance is paid for in full. Comp rows shorter than 30 days get scaled allowances | B12, B23, 4.4, 4.8, 12.1 |
| SEC-2 | `restrict_models` replaces the plan's list; legacy generous limits stay as permanent overrides (high) | Accepted in part: models narrow only (plan ∩ user list); the rollout clears every limit override, keeping the old values in the audit event, and rollout users get an open-ended Professional plan instead; Admin → Users flags overrides above the plan and asks to confirm an unlimited chat allowance. A separate `extra_model_ids` isn't needed (a grant or change-plan does it) | B6, B24, 4.11, 5.2, 8.12 |
| SEC-3 | Concurrent applies, grants and revokes can overlap periods (high) | Accepted: the user-row lock first in every period change, one lock order, a Postgres exclusion constraint, race tests for two orders and for a grant against a payment | B42, 6.5, 11.1, 12.1 |
| SEC-4 | Chargebacks aren't handled (high) | Accepted in part: dispute webhooks, a `billing_disputes` table, attention item and monitor warning with the deadline, lost = the full-refund access rule, `disputed_paise` in the summary, a disputes note in the research and the refunds placeholder. The daily dispute sync isn't adopted (0.3) | B41, 6.8, 10, 11.3 |
| SEC-5 | Refunds can be counted two or three times (high) | Accepted as proposed: refund row lock, a state change applied once, `refunded_paise` recomputed as the sum of processed refunds, `access_ended_at` set once, Dashboard refunds upserted | B21, 6.7, 12.1 |
| SEC-6 | Upgrade apply and revert don't check the periods' state (medium) | Accepted for the new upgrade: it applies only when the period it replaces is unchanged, else review; a refund restores that period only when its last history entry is this order's; a payment's refundable amount excludes credit a later upgrade used | 4.4, 4.10, 6.5, 6.7 |
| SEC-7 | Unsigned dedupe header, payment events applied from the payload, secret rotation (medium) | Accepted: dedupe by event id and body hash, bodies over 3 days old ignored, no state change from a payload (the worker fetches first), `RAZORPAY_WEBHOOK_SECRET_PREVIOUS` | B15, B17, 6.4 |
| SEC-8 | Every user gets free plans with test keys (medium) | Accepted: only testers check out with test keys and see the test instruments; test periods last 1 day; a warning when test keys run on a public origin | B20, 6.9, 8.2 |
| SEC-9 | The two unauthenticated endpoints have no rate limit and write rows (medium) | Accepted: nginx limits, real client IPs with the Cloudflare tunnel, the callback skips Razorpay when already applied and shares the refresh lock, Redis counters with one sample row per minute | 6.4, 6.11, 11.3 |
| SEC-10 | Checkout cancels the old link first; counters; timeouts; reconciler volume (medium) | Accepted, all four parts | B16, B19, 6.3, 6.4 |
| SEC-11 | API base with live keys, switching back to test keys, authentication errors, amount strictness (medium) | Accepted, all four parts; Customer Fee Bearer and Offers stay off (checklist) | 6.9, 6.10, 8.11 |
| SEC-12 | Payer data in stored events; one failing event can disable the webhook (low) | Accepted: ids, status, amount and method only, and a test that no payer field is ever stored. Async processing answers 200 once recorded, so a processing bug can't disable the webhook; an event failing 3 times goes to "Needs attention" | 6.4, 6.12, 12.1 |
| SEC-13 | Razorpay calls under locks; the receipt counter on Postgres (low) | Accepted: fetch outside any transaction, then apply in a short one; counters pre-created, missing ones inserted in a savepoint; a Postgres test across the financial-year boundary | B42, 6.5, 9, 12.1 |
| SEC-14 | Value-granting admin changes aren't audited; ₹0 offline payments (low) | Accepted: every override, model list, tester flag, grant, comp and plan change writes an event; lowering paid periods needs a confirmation; offline payments of at least ₹1, with a note when below the price | B9, B22, B45, 3.5, 4.9, 8.12 |
| SEC-15 | nginx error log and `pay_url` (low) | Accepted: `error_log … crit` on the return location; `pay_url` host checked on the server and again in the browser | 6.3, 6.11, 8.3 |
| PRD-1 | The upgrade formula; upgrades charging for future periods (high) | Accepted as proposed (the primary option) | B11, B12, 4.4 |
| PRD-2 | UPI may be capped at ₹2,000 for the owner's account; UPI Collect is gone (high) | Accepted: a `upi_limit_paise` billing setting (default ₹2,000), a checkout note above it, a going-live item; the test notice shows test cards only | 6.10, 8.3, 8.11, 14.2 |
| PRD-3 | `bikash` locked out after 30 days; new users stuck while payments aren't set up (high) | Accepted in part: open-ended complimentary plans instead of an exempt switch; the create-user form pre-selects a 30-day grant while payments aren't available. The one-time rollout banner isn't adopted (0.3) | B23, B24, 4.11, 8.12 |
| PRD-4 | A user out of allowance can't pay for more (high) | Accepted: "Start a new period now" on the current plan once 90% of an allowance is used; 429 wording without "renews" | B11, 4.3, 5.3, 8.6 |
| PRD-5 | Lapsed users can't reach their own files (high) | Accepted in part: "Your data" in Settings; listing and downloading one's own documents and listing chats stay open. "Include files" in the export isn't adopted (0.3) | B4, B43, 5.4, 8.8 |
| PRD-6 | Token limits sized for the dearest model; poor value (medium) | Accepted, the weights option in its exact form: the allowance counts cost in standard tokens; cards and the compare table show answers per model | B8, 3.2, 3.3, 5.3 |
| PRD-7 | Redirect loses the message; unclear end time; UTC dates (medium) | Accepted: periods end at midnight IST, IST dates everywhere, an inline panel instead of a redirect, the draft kept in `sessionStorage` | B2, B32, B34, 8.1, 8.7 |
| PRD-8 | "This month" strings in today's UI (medium) | Accepted: listed with their files for APP and ADMIN; Admin → Users shows this period against the effective limit | 8.9 |
| PRD-9 | Downgrades don't say what is lost (medium) | Accepted: `CheckoutOption.changes` from the server; generated files stay downloadable; reprocessing ignores the size limit | B13, B28, 4.5, 5.7, 8.3 |
| PRD-10 | One GST line wouldn't serve a registered seller (medium) | Accepted: version 1 has no GST; tax invoices are a later feature if the owner registers | B10, 1, 9, 14.1 |
| PRD-11 | The admin's daily questions (medium) | Accepted in part: expiring and lapsed lists, plan filters in Admin → Users, a CSV export, a retention question. "Copy payment link" isn't adopted (0.3) | 7.2, 8.11, 8.12, 14.1 |
| PRD-12 | Reminders rarely reach anyone (medium) | Accepted in part: the email field only when emails can be sent, asked once on the return page, the reminder channel stated in Settings, a checklist item. Receipt emails aren't adopted (0.3) | B25, 4.7, 8.4, 8.5 |
| PRD-13 | Too many locked items (low) | Accepted in part: one locked row per feature, none on the empty chat. The hide-locked setting and hiding the Gmail card aren't adopted (0.3) | 8.6 |
| PRD-14 | Too many steps on a phone (low) | Accepted: details collapsed, consent as a line under Pay recorded on the order (the owner may ask for a tick box, 14.1), sticky pay bar, Renew opens the checkout directly | 8.3, 8.5, 8.7 |
| PRD-15 | Heavy parts for a single owner (low) | Accepted, all six cuts | B1, B10, B11, B21, B31, 8.10 |
| DS-1 | `recommended` flag (S1) | Accepted | B44, 3.1, 7.1, 8.2, 8.10 |
| DS-2 | "₹2,000 / month" with "30 days, paid in advance" (S2) | Accepted (no GST line in version 1) | 8.2, 10 |
| DS-3 | One locked "Create a document"; capability tags follow the plan (S3) | Accepted; 7.3 says `ChatModel.can_create_*` stay server capabilities | 7.3, 8.6 |
| DS-4 | Redirect interstitial, phone pay bar, "Receipt details (optional)" (S4) | Accepted | 8.3 |
| DS-5 | `OrderView.receipt` and `method` (S5) | Accepted | 7.1, 8.4 |
| DS-6 | `PaymentView` period dates (S6) | Accepted | 7.1, 8.5 |
| DS-7 | `BillingSummary.users_without_plan`, `refunds`; `AttentionItem.detail` (S7) | Accepted | 7.1, 8.11 |
| DS-8 | Copy for "stopped checking" and "Please don't pay again." (S8) | Accepted | 8.4 |
| DS-9 | No per-plan "about N answers" (S9) | Superseded: the server computes the answers figures (PRD-6), so cards and the table show them | 3.3, 7.1, 8.2 |
| DS-10 | The plan line in the avatar menu (S10) | Accepted | 8.6 |
| DS-11 | Admin tab order (S11) | Accepted | 8.10 |
| DS-12 | Seed descriptions (S12) | Accepted (Standard's names Claude Sonnet) | 3.2 |
| DS-13 | `plan-card.css` (S13) | Accepted | 13.2 |
| DS-14 | Reminder email footer (S14) | Accepted | 4.7 |
| DS-15 | Resolved model lists in `PlanOffer`/`PublicPlan` (S15) | Accepted, with an answers figure per model | 7.1, 8.2 |

### 0.3 Review suggestions not taken, and why

- **Daily dispute sync** (SEC-4) and **daily refund sync** (revision 1): live keys need the Cloudflare domain, and a
  webhook is part of going live (the checklist and a monitor warning check it). Before that only test money moves.
- **`extra_model_ids` per user** (SEC-2): a grant or "change plan" gives one user more; a second override kind would
  re-open the hole SEC-2 closed.
- **A one-time "Plans are here" banner for rollout users** (PRD-3 c): the only existing user is very likely the
  owner; the avatar menu and Settings show the plan.
- **"Include files" in the data export** (PRD-5): it makes the export unbounded (`EXPORT_MAX_MB` exists because of
  the tunnel's idle limit); every document has Download in "Your data".
- **"Copy payment link"** (PRD-11): a third order source with a 7-day watch, against a 30-link test cap, for what the
  user does by signing in. The admin can send the `/plans` address.
- **Receipt emails** (PRD-12): Razorpay already emails the payer a confirmation; receipts are in Settings.
- **"Hide features from other plans"** and **hiding the Gmail card** (PRD-13): with one row per feature the noise is
  small, and the earlier Settings brief (U6) keeps every plugin visible with its real status.
- **Pre-rendered HTML policy pages**: not suggested by a review but considered with B31; the pages are SPA routes
  like the rest. If Razorpay's automated website check can't read them, pre-rendering is a small follow-up (14.3).

## 1. Scope and non-goals

**In scope:** plans and their editor; plan periods with stacking, `now` checkouts (upgrades and new periods), dated
and open-ended complimentary plans, revocations and expiry; checkout with Razorpay Payment Links; confirmation by
callback, webhook and reconciler; refunds and disputes; offline bank transfers; receipts; the chat allowance counted
in standard tokens; plan-based enforcement in every existing feature; the plan page, checkout, return page, Settings →
Plan & billing, "Your data" for lapsed users, locked states and banners; Admin → Plans, Admin → Billing and the plan
parts of Admin → Users; public pricing and policy pages; reminders; migration 0003, monitoring and tests.

**Non-goals** (each can come later without undoing this design):

- Self sign-up, email verification and self-service free trials (B39). The admin's dated grants replace trials.
- Auto-debit, mandates and Razorpay Subscriptions.
- GST: tax invoices, a GSTIN, CGST/SGST/IGST, e-invoicing and credit notes (B10).
- Coupons, discounts, referral credit, annual plans, buying several periods in one payment, add-on packs (a "Start a
  new period now" covers running out early).
- Currencies other than INR; international cards beyond what Razorpay's page offers by default.
- Razorpay's own Bank Transfer method (needs Razorpay's approval). Offline transfers are recorded by the admin (B22).
- Teams, seats, or one plan shared by several users. The app stays single-tenant.
- Prorated refunds computed by the app. The admin chooses the refund amount.
- Storing customer contact details or payment instruments; payment links sent outside the app.

## 2. Flows at a glance

```
sign-in:   POST /auth/login → Me.plan.access ? / (chat) : /plans
choose:    /plans (GET /billing/plans) → a card → /plans/checkout/<code>?kind=period|now
pay:       PUT /billing/profile (optional) → POST /billing/checkout → 201 {pay_url} → same tab to rzp.io
return:    rzp.io → GET /api/billing/razorpay/return?<signed query>   (no cookie)
           → verify signature → (not applied yet) fetch link and payment from Razorpay → apply_payment
           → 303 /billing/return?order=<id>
           /billing/return polls GET /billing/orders/<id>?refresh=true → paid → refresh Me → "Start chatting" → /
webhook:   Razorpay → POST /api/billing/razorpay/webhook (HMAC of the raw body) → dedupe → record → wake the worker
           worker → fetch the entity from Razorpay → apply_payment / refund / dispute (the same functions)
reconcile: worker, every 30 s → orders due → GET /v1/payment_links/<id> → new payments → apply | record failure | expire
renew:     /plans or a banner → "Renew" → kind=period → the new period starts when the paid time ends
upgrade:   /plans → "Upgrade now · ₹X" → kind=now → on apply the current period ends, a new one starts now
run out:   429 allowance_used → "Start a new period now" → kind=now on the same plan
expiry:    the period ends → the next request answers 402 plan_expired → the open page shows a panel; /plans next
refund:    Admin → Billing → Refund → POST /v1/payments/<id>/refund → pending → processed (once) → a full refund ends access
dispute:   webhook payment.dispute.* → worker fetches the dispute → attention item; lost → access ends
offline:   Admin → Billing → Record bank transfer → apply (method bank_transfer) → receipt
```

## 3. Plans

### 3.1 What a plan is

| Field | Rule |
|---|---|
| `code` | `^[a-z][a-z0-9_-]{1,31}$`, unique, set on create, never changed (seeded: `starter`, `standard`, `professional`) |
| `name` | 1 to 60 printable characters, shown everywhere |
| `description` | at most 300 characters, one line on the card |
| `price_paise` | a whole number of rupees in paise (a multiple of 100), 100 (₹1) to 10,000,000 (₹1,00,000): the charge for one period |
| `rank` | integer, unique among plans. Higher rank = higher plan: it decides what an upgrade is. Among `active` plans, a higher rank must have a higher price (else 422 "A higher plan must cost more.") |
| `status` | `active` (offered to everyone) or `retired` (nobody can buy or renew it; its periods and open-ended plans run on) |
| `recommended` | at most one plan, which must be `active`; saving one clears the others |
| `features` | `{files: bool, images: bool, plugins: string[]}`; `plugins` holds ids of registered plugins (`gmail`) |
| limits | `monthly_token_limit` (the **chat allowance** in standard tokens, B8), `rate_limit_per_min`, `max_upload_mb` (1 to `MAX_UPLOAD_MB_HARD`), `max_documents`, `max_storage_mb`, `monthly_image_limit`, `vision_monthly_token_limit` (plain vision tokens). 0 = unlimited. "Monthly" means per period (B5) |
| `restrict_models` + `chat_model_ids` | false: every enabled chat model; true: the listed chat models only |
| `default_chat_model_id` | optional; must be in the list when the list is restricted. The model a new chat starts with when the user hasn't chosen one (5.5) |

A feature is usable only when the plan includes it **and** the server offers it: `CHAT_FILE_TOOLS`, `CHAT_IMAGE_TOOLS`
with an active image model, the plugin set up and turned on by the admin, and a chat model with tools. Plan features
add a condition and never turn anything on.

### 3.2 The three plans and their default limits

Seeded by `bootstrap._seed_plans` (B37) when the `plans` table is empty. Model lists are matched by `litellm_model`;
a seeded model that doesn't exist is skipped.

| | Starter | Standard | Professional |
|---|---|---|---|
| `price_paise` (per period) | 50,000 (₹500) | 200,000 (₹2,000) | 500,000 (₹5,000) |
| `rank` / `recommended` | 10 / no | 20 / **yes** | 30 / no |
| `description` | "Ask questions across your documents, for everyday use." | "Adds files from chat and Claude Sonnet for regular work." | "Everything: files, images and Gmail, with every model." |
| Files from chat (`files`) | no | **yes** | **yes** |
| Images from chat (`images`) | no | no | **yes** |
| Plugins | none | none | **`gmail`** |
| Chat models | Claude Haiku 5.5, GPT-5.4 mini | + Claude Sonnet 5.5 | every enabled chat model |
| Default chat model | Claude Haiku 5.5 | Claude Sonnet 5.5 | none (the server default, Claude Opus 5.5) |
| Chat allowance (standard tokens per period) | 500,000 | 2,000,000 | 5,000,000 |
| … in answers with the default model | about 1,600 (Haiku) | about 260 (Sonnet) | about 290 (Opus) |
| … with the other models | GPT-5.4 mini about 200 | Haiku about 5,200; GPT-5.4 mini about 640 | Sonnet about 580; GPT-5.5 about 210; Haiku about 11,000 |
| Requests per minute | 10 | 20 | 30 |
| Max file size | 10 MB | 25 MB | 50 MB |
| Max documents | 50 | 200 | 500 |
| Max storage | 200 MB | 1,000 MB | 3,000 MB |
| Images per period | 30 (unused while `images` is off) | 30 (unused while `images` is off) | 60 |
| Vision tokens per period (scanned PDF pages) | 150,000 (about 50 simple pages) | 500,000 (about 170) | 1,000,000 (about 340) |

Every Professional limit is at least today's new-user default (`.env.example`: 1,000,000 plain tokens, 20 per minute,
25 MB, 100 documents, 500 MB, 30 images); 5,000,000 standard tokens are 2,500,000 Opus tokens. The vision allowance
was a server-wide 2,000,000 per user per month and was never set per user.

### 3.3 The chat allowance and what a plan costs the owner

**Counting** (`services/usage.py`, B8): every chat usage event stores `allowance_tokens`:

```python
STANDARD_TOKEN_USD = 3.20 / 1_000_000          # one standard token
allowance_tokens = ceil(cost_usd / STANDARD_TOKEN_USD) if cost_usd > 0 else total_tokens
```

The value is fixed when the event is written, so a later price change doesn't rewrite history. The budget sums
`allowance_tokens` over the allowance window; vision tokens, images and embeddings are counted as before (plain
vision tokens, images, nothing).

**A model's rate** (shown to users, `ChatModel.allowance_rate`, `PlanModel.allowance_rate`): its blended price (85%
input, 15% output tokens, as RAG answers are mostly prompt) divided by $3.20 per million, to two significant figures;
null when the model has no price (it then counts plain tokens, rate 1).

**Answers** (`answers_estimate` per plan, `PlanModel.answers` per model; server-side in `billing/plans.py`):
`allowance × $3.20/M ÷ the model's blended price ÷ tokens per answer`, rounded **down** to two significant figures;
tokens per answer = 6,000, plus 1,550 when the plan has files and 1,040 when it has images (the tools' prompt
overhead). Null when the allowance is unlimited or the model has no price.

Prices used (HANDOFF and README "costs", seeded model prices, LiteLLM's map for GPT):

| Item | Price used |
|---|---|
| Claude Haiku 5.5 / GPT-5.4 mini / Claude Sonnet 5.5 / Claude Opus 5.5 / GPT-5.5, per 1M tokens in / out | $0.10 / $0.50, $0.75 / $4.50, $2 / $10, $4 / $20, $5 / $30 |
| The same, blended (85% in, 15% out); rate | $0.16 (×0.05), $1.31 (×0.41), $3.20 (×1), $6.40 (×2), $8.75 (×2.7) |
| One image (GPT Image 2, landscape medium, the dearest `IMAGE_MAX_QUALITY` allows; measured) | $0.042 |
| Vision (Claude Sonnet 5.5, measured 8,632 tokens for $0.0202) | $2.34 per 1M tokens |
| Embeddings (text-embedding-3-small) | $0.02 per 1M tokens, bounded by storage: under $0.10 per period |

What the owner keeps per period after Razorpay's 2% fee plus 18% GST on the fee (2.36% of the charge, research 6.3),
at **₹88 = $1** (an assumption; `BILLING_USD_INR`), and the AI cost:

| | Starter | Standard | Professional |
|---|---|---|---|
| Kept per period | ₹488.20 ($5.55) | ₹1,952.80 ($22.19) | ₹4,882.00 ($55.48) |
| **Worst case**: every allowance used up | $1.95 (₹172): **35%** | $7.57 (₹666): **34%** | $20.86 (₹1,836): **38%** |
| **Typical**: 40% of every allowance | $0.78 (₹69): 14% | $3.03 (₹267): 14% | $8.34 (₹734): 15% |
| If the owner registers for GST later and keeps the prices (GST included) | worst 42% | worst 40% | worst 45% |

Because the allowance counts cost, the worst case no longer depends on which models a plan includes. Hosting isn't
included. The plan editor shows the same estimate (8.10), from the same inputs (`BillingAdminConfig.estimate`).

### 3.4 Prices and the charge

The charge for a `period` is the plan's price; for a `now` checkout it is 4.4's. No GST is added or shown (B10).
Every amount is integer paise; arithmetic is integer (`billing/money.py`: integer division and `Fraction`, never
floats). A changed price applies to new checkouts; a pending checkout keeps its amount; receipts keep what they were
issued with.

### 3.5 Editing plans and what happens to existing users

- **Periods snapshot the plan** when they are created (B9): features, limits (scaled for short comp rows, 4.2),
  model list, default model and the plan's name. Entitlements read only the snapshot. **Open-ended complimentary
  plans** read the plan's current values.
- **Save** offers "Apply to new periods only" (default) and, when the plan has current or scheduled periods, "Also
  apply to the N current and scheduled periods". The second rewrites those snapshots (not their bases), appends a
  `history` entry to each, and writes one `plan.applied_to_current` event with the count. When the change lowers a
  limit or removes a feature or model and any of those periods was paid for, the server answers 409
  `confirmation_required` unless `confirm_reduction: true` is sent; the UI asks inline ("This lowers what 3 users
  paid for until their periods end.").
- **Price changes** affect new checkouts only.
- **Retired** plans can't be bought or renewed: their holders choose another plan at renewal; their periods and
  open-ended plans run on.
- **Delete** only a plan no period, order or open-ended plan has ever used (409 "This plan has been used. Retire it
  instead.").
- **A model deleted** from the catalogue drops out of every list. **A new chat model** is in every unrestricted plan at
  once and in restricted plans only when the admin adds it.
- Every save writes a `plan.created` or `plan.updated` event with the changed fields before and after (B45).

## 4. Lifecycle

### 4.1 States

| State | Meaning | Access |
|---|---|---|
| `exempt` | the admin | everything, unlimited |
| `none` | no counting period ever, and no open-ended plan | none |
| `active` | a counting, unrevoked period contains now, **or** the user holds an open-ended complimentary plan | the period's snapshot (it takes precedence), else the open-ended plan |
| `expired` | had counting periods, none contains now, no open-ended plan | none |

Shown on top of these, never as a state: **expiring soon** (active by a period, the paid time ends within
`BILLING_REMINDER_DAYS`, nothing scheduled after it and no open-ended plan to fall back on), **pending payment** (an
open checkout), **complimentary** (the current period is a comp, or the open-ended plan applies), **open-ended**,
**test mode** (the current period was paid with test keys).

```
none ──pay / grant──▶ active ──(period ends, nothing after)──▶ expired ──pay──▶ active
                        │  ▲                                        ▲
                        │  └── renew (period) / now ────────────────┘ (data kept throughout)
                        └── full refund / lost dispute / revoke ──▶ expired (or still active on the next period)
```

### 4.2 Periods

- **End rule** (`periods.period_end(start, days)`): `ends_at` = the first midnight IST (18:30 UTC) at or after `start +
  days × 86,400 s`. A paid period has `days` 30, a test-mode period 1 (B20), a comp row 1 to 30. **Valid through** =
  the IST date of `ends_at − 1 s` ("valid through 25 Nov 2026"). The row stores `days` so re-chaining can recompute.
- **Counting**: periods of mode `live`, `offline` and `none` (comps) always count; `test` periods only while the
  server runs test keys (5.1). Everything below speaks of counting, unrevoked periods.
- **No overlap**: a user's periods never overlap (B42; on Postgres also an exclusion constraint for non-test periods,
  11.1). A new `period` starts at `max(now, paid through)`.
- **Current period** = the one with `starts_at ≤ now < ends_at`. **Paid through** = the end of the unbroken chain of
  periods that starts with the current one (null without a current period).
- **Re-chaining**: when a current or scheduled period is revoked or ended early, every later period moves to start at
  `max(now, the previous end)`, with its end recomputed from its `days`. Past periods never move.
- **Allowance window** (B5) = the current period's `[starts_at, ends_at)`; for an open-ended plan or the admin, the
  current UTC calendar month.
- **Scaled allowances**: a period row shorter than 30 days (a comp) gets `ceil(limit × days ÷ 30)` for the chat
  allowance, images and vision tokens; other limits and features are the plan's. Test-mode periods keep full
  allowances (testers are chosen by the admin).

### 4.3 Buying

`GET /billing/plans` lists, per plan, the options the user has now, each with its dates, amount, credit, the changes
it brings and, when it can't be bought, the reason. `POST /billing/checkout` checks the same rules again on the
server. Options need payments available to the user (6.9: keys set; with test keys, a tester).

| Option | Offered when | Starts | Label |
|---|---|---|---|
| `period` | the plan is `active`, and the user has no period scheduled after the current one | now when there is no current period (none, expired, or only an open-ended plan), else at paid through | no current period: "Choose <plan>" ("Renew <plan>" for the last plan when expired); same plan: "Renew"; lower plan: "Switch on <date>"; higher plan: the link "or switch on <date>" under the upgrade |
| `now`, upgrade | there is a current period; the plan is `active` and ranks higher than the current period's plan | now | "Upgrade now · ₹X" |
| `now`, new period | there is a current period of this plan, the plan is `active`, and 90% or more of one of its allowances is used (`Me.plan.can_start_new_period`) | now | "Start a new period now · ₹X" |

With only an open-ended plan, a `period` starts now and takes precedence while it runs; the open-ended plan applies
again after it (the checkout says "Your complimentary plan continues after it."). A dated comp period is replaced by
a `now` checkout like a paid one, with no credit ("Your complimentary Starter period ends when Professional starts.").

Reasons when an option is unavailable: "You already have a period paid ahead.", "This plan is no longer offered.",
"Online payment isn't set up yet.", "You're already on this plan or a higher one." The admin gets no options (B40).
A late payment of an older order may still add a period beyond the one-ahead rule (a payment is never refused).

### 4.4 The `now` charge, its credit and applying it

For target plan `T` with price `G_T` and the user's current period `P`:

```
time    = (P.ends_at − now) ÷ (P.ends_at − P.starts_at)                         # share of P left, 0..1
used    = max(used ÷ limit) over P's limited allowances: the chat allowance,    # 0..1; limits of 0 are left out;
          images (when P includes images), vision tokens                         # 0 when none is limited
credit  = floor(P.charge_basis_paise × min(time, 1 − used) ÷ 100) × 100          # whole rupees, exact fractions
charge  = max(100, G_T − credit)
```

`charge_basis_paise` is what the period's plan was worth when it got it: the amount paid for a `period` payment;
`G_T` for a `now` period; the amount received for an offline payment; 0 for comps (so replacing
a comp costs the full price). The credit uses it **less the refunds of the period's payment** that are `creating`,
`pending` or `processed` (a goodwill refund that kept access isn't credited a second time). Examples: a Starter period (₹500) with 60% of its time left and 30% of its chat
allowance used: credit `500 × min(0.6, 0.7)` = ₹300, Standard now costs ₹1,700 for a fresh period. With 90% of the
allowance used: credit ₹50, ₹1,950. The new period always carries a full, fresh allowance.

The order stores `replaces_period_id`, the quoted `replaces_plan_id` and `replaces_ends_at`, and `credit_paise`.
**Applying** (6.5, under the user lock): the order applies only when `P` is still the current period, unrevoked, with
the quoted plan and end. Then: `P.ends_at = now` with a `history` entry `{kind: "ended_by_order", order_id,
ends_at_before}`; a new period of `T` from now (`days` 30, the order's snapshot, basis `G_T`); the periods that were
scheduled after `P` re-chain after the new one, keeping their plans. Otherwise the order goes to review ("The plan
this payment was priced against has changed. Suggested: refund ₹X or grant instead."). Before that, the credit is
computed again with `P`'s usage and refunds at apply time and the time left as quoted (the order's `created_at` is the
quote's time); when it fell below the order's `credit_paise` (the allowance was used up, or the payment refunded,
while the link was open), the order goes to review too ("Usage or a refund since the quote lowered the credit to ₹0
(quoted ₹499). Suggested: refund ₹4,501 or grant instead.").

The checkout and the receipt show one line for the new period and one for the credit ("Credit for unused Starter, 26
Oct to 25 Nov: −₹300").

### 4.5 Downgrades and switching

A switch to any other plan is a `period` of that plan after the paid time (B13). The current period keeps its plan to
the end; `Me.plan.next_plan` shows what follows. Nothing is refunded. The option's `changes` (computed by the server
from the current entitlements, the target plan and the user's document count and storage) lists what the switch
removes, for example:

- "Gmail will stop working in chat. You stay connected."
- "Creating files and images from chat isn't included. Your existing ones stay."
- "Claude Opus 5.5 and GPT-5.5 won't be available."
- "You have 180 documents (2.1 GB); Starter allows 50 (200 MB). You keep them all but can't add more until you're
  under the limit."

### 4.6 Expiry

At paid through (and with no open-ended plan to fall back on) access ends; nothing runs at that instant. The next
request that needs access answers 402 `plan_expired`. An answer streaming at that moment finishes. Documents waiting
for ingestion are still processed (their scanned pages aren't read by the vision model, as when an allowance is used
up). Pending Gmail sends can't be sent (402) and expire on their own. Nothing is deleted: conversations, documents,
generated files, images, Gmail connections, preferences, usage history.

### 4.7 Reminders

- **In the app**: `Me.plan.expiring_soon` drives a banner over the chat (8.7) and a note in Settings → Plan & billing.
- **Email** (only when `BILLING_EMAIL_FROM` and the monitor's SMTP settings, `ALERT_SMTP_HOST` and the rest, are set,
  and the user's billing profile has an email; `PaymentsInfo.email_reminders` tells the UI whether to ask for one):
  the worker's billing loop sends, once each per chain, "ends in 5 days" (at `BILLING_REMINDER_DAYS`), "ends
  tomorrow" (1 day before) and "has ended" (within the hour after). None when a later period is scheduled or an
  open-ended plan follows. Plain text (visual brief 12), no tracking: the plan name, the IST date, a link to
  `<first PUBLIC_BASE_URLS entry>/plans`, "Your chats and documents are kept.", and the footer "You get this email
  because you gave this address for plan reminders. Remove it in Settings → Plan & billing to stop them." Each send
  writes a `reminder.sent` event (the period id and which reminder), which is also the dedupe. A failure is logged
  without the address and retried on the next loop for 6 hours.
- Razorpay itself emails the payer a confirmation (to the address they type on its page).

### 4.8 Complimentary plans and the admin's changes

Every action here takes the user lock (B42) and writes an event with the admin as `actor` (B45).

- **Dated grant** (`POST /admin/billing/users/{id}/grants`, `days` 1 to 90): plan (any status), `reason` (required,
  at most 200 characters), `start` `now` (409 when the user has a current period) or `after_current`. Creates
  `ceil(days ÷ 30)` rows of source `comp`, mode `none`, basis 0, each of at most 30 days, the last one shorter with
  scaled allowances (4.2); `granted_by` is the admin's username.
- **Open-ended grant** (the same endpoint, `days: null`): sets `users.comp_plan_id`, `comp_reason`, `comp_granted_by`,
  `comp_granted_at`, replacing an earlier open-ended plan. **End** (`POST …/end-complimentary`, reason) clears them.
- **Change plan at no charge** (`POST …/change-plan`, plan and reason): every unexpired period gets the plan's
  snapshot now (scaled for short rows), with a `history` entry; bases are kept. For mistakes and goodwill upgrades.
- **Revoke** (`POST /admin/billing/periods/{id}/revoke`, reason): sets `revoked_at`; later periods re-chain (4.2).
  Paid periods can be revoked without a refund (the dialog says so).
- **Creating a user** can include a grant (dated or open-ended). While regular users can't pay (no keys, or test
  keys), the form starts with "Professional, 30 days" selected and the note "This user can't use the app until they
  have a plan, and online payment isn't set up yet."

### 4.9 Offline bank transfers

`POST /admin/billing/offline-payments` (user, plan, amount in paise of at least 100, the date received, the bank
reference, a note that is required when the amount is below the plan's price): creates an order (`kind` `period`,
mode `offline`, status `paid`) and a payment (method `bank_transfer`, `razorpay_payment_id` null,
`offline_reference`), then the same apply as a Razorpay payment: one period from `max(now, paid through)`, basis the
amount received, and a receipt in the live series. With `apply: false` it records a receipt only (for a transfer that
pays for a "change plan"). Refunds of offline payments are recorded only (`initiated_by: "admin"`, no Razorpay call)
and end access by 4.10.

### 4.10 Refunds, disputes and access

| Event | Access |
|---|---|
| Full refund (the payment's processed refunds reach its amount) of a `period` payment | its period is revoked when it hasn't ended (`revoked_reason` "Refunded"), and also when a `now` order ended it (its last `history` entry is `ended_by_order`), so it can never be given back; later periods re-chain |
| Full refund of a `now` payment | its period is revoked; the period it replaced gets `ends_at_before` back when that is still in the future, the period is unrevoked, its last `history` entry is this order's and its own payment wasn't refunded in full, charged back or had its access ended; then later periods re-chain. Otherwise only the revoke, and an attention item |
| Partial refund | no change, unless the admin ticked **End access** (then as for a full refund) |
| Dispute lost (6.8) | as for a full refund |
| Refund made in Razorpay's Dashboard (webhook) | recorded; full → as above; partial → no change, listed under "Needs attention" |

`access_ended_at` on the refund or dispute is set once. A payment's **refundable amount** = its amount − refunds
`creating`, `pending` or `processed` − the credit later `now` orders took from its period (the refund dialog says
"₹300 of this payment went as credit to the upgrade on 4 Nov."). A lost dispute or a Dashboard refund isn't capped
that way: when the money returned plus the credit an upgrade still running took from the period passes the period's
basis, the upgrade is left as it is (the payer paid for most of it), the event `credit.reversed` is recorded and
"Needs attention" lists it for 30 days ("₹1,999 of a disputed payment by maya.chen went as credit to the upgrade on 4
Nov 2026"), for the admin to revoke the upgrade's period or change the plan. Lost disputes are listed there for 30
days as well. A refund never deletes the receipt; the receipt shows
it (9). Comps are never refunded.

### 4.11 Rollout

- **Migration 0003** sets every user's limit columns to NULL (the plan's value) and drops `user_image_limits`, first
  writing one `rollout.limits_cleared` event per non-admin user with their old values (and their
  `user_image_limits` value) in `detail`.
- **First start** (`bootstrap._rollout_plans`, once, `app_state` `plans_rollout`): every **active, non-admin** user
  without a period and without an open-ended plan gets an **open-ended complimentary Professional plan**, reason
  "Existing account when plans were introduced", `granted_by` "system". Inactive users get nothing (the admin can
  grant on reactivation). A database restored from a backup taken before 0003 is migrated and rolled out by the same
  steps.
- The rehearsal (11.2) reports to the owner any cleared value above Professional's, so the admin can set it again as
  an override.

### 4.12 Deleting or deactivating a user

- **Delete**: `billing.on_user_deleted(db, user)` cancels the user's open links (after the transaction, best effort)
  before the row goes. Periods and the billing profile go with the user (cascade). Orders, payments, refunds,
  disputes, receipts and events stay with `user_id` null and the recorded username (B30). A user who never had an order leaves no billing trail: their events (the admin's grants and edits) are deleted with them (2026-10-27, so test users made through the admin API leave nothing behind). A payment captured
  afterwards goes to review ("The user was deleted"). The delete confirmation says when the user has paid time left
  ("No refund is made automatically.").
- **Deactivate**: nothing changes in billing; periods keep running (no pausing).

## 5. Access enforcement (backend)

The backend decides; the UI only mirrors `Me.plan` (B3). Every path below uses the entitlements of 5.1 and nothing
else: no route or service reads `users.monthly_token_limit` (or another limit column), `settings.default_*` or
`VISION_MONTHLY_TOKEN_LIMIT` directly any more.

### 5.0 Entitlement matrix (the seeded plans)

| | Lapsed (`none`, `expired`) | Starter | Standard | Professional | Admin (`exempt`) |
|---|---|---|---|---|---|
| Chat with own documents and the shared knowledge base; read and continue conversations | – | ✓ | ✓ | ✓ | ✓ |
| Chat models (narrowed further by a user's allowed list, 5.2) | – | Haiku 5.5, GPT-5.4 mini | Haiku 5.5, GPT-5.4 mini, Sonnet 5.5 | every enabled chat model | every enabled chat model |
| Files from chat (`create_document`) | – | locked | ✓ | ✓ | ✓ |
| Images from chat (`create_image`) | – | locked | locked | ✓ | ✓ |
| Gmail: connect, chat tools, browse, import, send | – | locked | locked | ✓ | ✓ |
| Gmail: Disconnect, "Use in chat" switch, cancel a pending send | ✓ | ✓ | ✓ | ✓ | ✓ |
| Upload and reprocess documents (limits of 3.2) | – | ✓ | ✓ | ✓ | ✓ |
| List own documents; download them, generated files and images; list chat titles; delete documents and chats | ✓ (own documents only) | ✓ | ✓ | ✓ | ✓ |
| Settings (preferences, account, usage, export, delete all chats), billing, plugin catalog | ✓ | ✓ | ✓ | ✓ | ✓ |
| Buy a plan | ✓ | ✓ | ✓ | ✓ | – (403) |
| Admin page | – | – | – | – | ✓ |
| Chat allowance / images / vision per period | – | 500,000 / – / 150,000 | 2,000,000 / – / 500,000 | 5,000,000 / 60 / 1,000,000 | unlimited |

"Locked" means not offered, with the UI's lock and "Included in <plan>" (8.6); a direct API call answers 402
`feature_not_in_plan`. An edited plan follows its own features and limits; per-user overrides adjust the limits
(5.2). An open-ended complimentary plan gives its plan's column.

### 5.1 Entitlements (`app/services/billing/entitlements.py`, frozen interface, 13.3)

`load(db, user, *, now=None) → Entitlements` reads the user's current period (one indexed query on `(user_id,
ends_at)`) and, for `Me` only, the chain after it. The result is per request: `deps.get_entitled_user` stores it on
`request.state.entitlements`, the chat turn keeps it for the whole answer, the worker loads it per ingestion run.

- **Admin**: `state "exempt"`, `access` true, every feature, every enabled chat model, limits as `usage.user_limits`
  reports the admin today (0 = unlimited, `max_upload_mb = MAX_UPLOAD_MB_HARD`), the window = the current UTC
  calendar month.
- **Active by a period**: the current period's snapshot merged with the user's overrides (5.2); the window is the
  period.
- **Active by an open-ended plan** (no current period): the plan's current values merged with the overrides; the
  window is the current UTC calendar month.
- **None or expired**: `access` false, no features, no models; the limits are the last period's snapshot merged with
  the overrides (or the cheapest active plan's, for `none`), for information only; the window is the last period (or
  the current calendar month for `none`).
- Periods with `mode = "test"` count only while the server runs test keys; `live`, `offline` and `none` always count.

### 5.2 Merging overrides

For each of the seven limits: the user's column when it isn't NULL, else the snapshot's value; `max_upload_mb` is then
capped at `MAX_UPLOAD_MB_HARD`. **Models**: the plan's set (or every enabled chat model), **intersected** with the
user's allowed list when `users.restrict_models` is true (B6). An empty result leaves the user without a chat model
(400 "No chat model is available to you. Ask the admin." as today), and Admin → Users shows "No model left: the
user's list and the plan's models don't overlap." Features have no per-user override (a grant or "change plan" does
that). Overrides are edited in Admin → Users (8.12), and every change is audited (B45).

### 5.3 Allowance windows and counting (`services/usage.py`)

`check_budget`, `acquire_chat_slot`, `check_rate_limit`, `images_in_window`, `vision_tokens_in_window`,
`usage_summary` and `account.my_usage` take the entitlements and count usage events with `created_at >=
ent.window_start`. The chat budget sums `allowance_tokens` (3.3); `record_usage` writes it for chat events. `MyUsage`
and `UsageSummary` carry the window (`window_start`, `window_end`) and both counts (7.1); `MyUsage.daily` covers each
UTC day of the window through today; `month` stays the "YYYY-MM" of the window's start. Admin → Usage keeps calendar
months (reporting, not limits). Messages (IST dates, B34):

- chat allowance (429, `code: "allowance_used"`, `feature: "chat_tokens"`): "You've used this period's chat allowance
  (500,214 of 500,000)." then one of "It starts again with your next period on 26 Nov 2026." (a period is scheduled),
  "It starts again on 1 Dec 2026." (an open-ended plan), "Your Starter plan is valid through 25 Nov 2026." (nothing
  scheduled). The client adds "Start a new period now" and "Upgrade" by `Me.plan` (8.6); the text never says
  "renews";
- images (`feature: "images"`) and vision: the existing notes and messages with "this period" and the same endings
  instead of "this month" and "Ask the admin to raise …".

`Me.plan.can_start_new_period` is true when the user has a current period and 90% or more of its chat allowance,
images (when included) or vision tokens is used.

### 5.4 Route guards

`deps.get_entitled_user` = session (as `get_current_user`) + entitlements + `require_access` (402 `plan_required` or
`plan_expired`). Routers that are entirely gated get it as a router dependency; the open routes keep
`get_current_user` and load entitlements where they need them. `test_route_guards.py` gets one row per route.

| Routes | No session | Lapsed (`none`, `expired`) | Active | Admin |
|---|---|---|---|---|
| `GET /health`, `/health/ready`, `/public/*` | 200 | 200 | 200 | 200 |
| `GET /billing/razorpay/return`, `POST /billing/razorpay/webhook` | signature only | – | – | – |
| `POST /auth/login`, `/auth/logout` | as today | as today | as today | as today |
| `GET /auth/me`, `POST /auth/change-password`, `/auth/logout-others` | 401 | 200 | 200 | 200 |
| `GET /auth/upload-allowed` (nginx only) | 401 | 403 | 204 | 204 |
| `/me/preferences`, `/me/account`, `/me/usage`, `POST /me/export` | 401 | 200 | 200 | 200 |
| `/billing/*` (plans, status, checkout, orders, payments, receipts, profile) | 401 | 200 | 200 | 200 (checkout 403 `admin_exempt`) |
| `GET /plugins`, `PATCH`/`DELETE /plugins/{id}/connection`, `POST /plugins/gmail/sends/{id}/cancel` | 401 | 200 | 200 | 200 |
| `GET /conversations` (the list), `DELETE /conversations`, `DELETE /conversations/{id}`, `DELETE /documents/{id}` | 401 | 200 | 200 | 200 |
| `GET /documents` | 401 | 200, **own documents only** | 200 | 200 |
| `GET /documents/{id}/download`, `GET /documents/{id}/image` | 401 | 200 for own documents; **402** for shared ones | 200 | 200 |
| `GET /models`, `POST /chat/stream`, `GET`/`PATCH /conversations/{id}` | 401 | **402** | 200 | 200 |
| `GET /documents/config`, `POST /documents`, `POST /documents/{id}/reprocess`, `GET /documents/{id}/image/details` | 401 | **402** | 200 | 200 |
| `POST /plugins/{id}/connect`, the Gmail routes (`/plugins/gmail/threads/*`, `/import`, `GET /sends/{id}`, `/send`, `/revise`) | 401 | **402** | 200, or **402** `feature_not_in_plan` without the plugin | 200 |
| `/admin/*` | 401 | 403 | 403 | 200 |

`PATCH /me/preferences` validates `default_model_id` against the entitled models (lapsed users: any id but null → 422
"That model isn't available to you."). The OAuth callback isn't re-checked: a connect was entitled when it started
(10-minute state); the connection is then simply unused if the plan changed.

### 5.5 Chat

- **Entry** (`routers/chat.py`): `get_entitled_user`, then the rate limit, budget and slot with the entitlements. A
  conversation created before the expiry can't be continued without access (402).
- **Models** (`model_registry.list_chat_models_for_user(db, user, ent)`): enabled chat models in the entitled set,
  default first, then by display name. The default (no `model_id`) is the plan's `default_chat_model_id` when
  entitled, else the server default when entitled, else the first. A requested model that is enabled but outside the
  set: 402 `feature_not_in_plan` (`feature: "model"`, `plan`: the cheapest active plan that has it) when the plan
  excludes it; 403 "You are not allowed to use this model." when only the user's allowed list excludes it (as today).
  `ChatModel` gains `allowance_rate` (3.3); `can_create_files` and `can_create_images` stay **server capabilities**
  (the model and the server can), and the UI combines them with `Me.plan.features` (8.6).
- **Files** (`document_tool.plan(db, user, model, ent)`): when `CHAT_FILE_TOOLS` is on and the model gets tools but
  `ent.files` is false → no tool, and this note in the system prompt (`{unlock}` = "the Standard plan" or "a higher
  plan", from `unlock`):

  > The user's plan doesn't include creating files. If the user asks for a file (PDF, Word, Excel or Markdown), tell
  > them their plan doesn't include files from chat and that {unlock} does, which they can choose in Settings → Plan
  > & billing; never claim to have created a file. Notes in parentheses that start with "App note:" in earlier
  > answers were added by the app.

- **Images** (`image_tool.plan(db, user, model, ent)`): when images would otherwise be offered and `ent.images` is
  false → no tool, `with_app_note(...)` of:

  > The user's plan doesn't include generating images. If the user asks for an image, tell them their plan doesn't
  > include images from chat and that {unlock} does, which they can choose in Settings → Plan & billing; never claim
  > to have made an image.

  The PDF/DOCX embedding of images in `create_document` only ever sees images the user generated, so nothing else
  changes.
- **Gmail** (`registry.plan_chat(db, user, model, ent)`): a plugin that is enabled and set up but not in
  `ent.plugins` offers no tools; when the user is connected, its note is:

  > The user's plan doesn't include the Gmail plugin. If the user asks about their email, tell them their plan doesn't
  > include Gmail and that {unlock} does; you can't search, read, draft or send email in this conversation.

  Earlier email cards and sources in the history stay as they are.

### 5.6 Plugins (Gmail)

`registry.access_for(db, user, plugin, ent)` gains `PluginAccess.entitled` (`plugin.id in ent.plugins`, true for the
admin) and `usable` becomes empty when not entitled. `registry.require` answers 402 `feature_not_in_plan`
(`feature: "plugin:gmail"`) after its 403 checks (turned off by the admin wins) and before its 409 checks.
`POST /plugins/{id}/connect` answers the same 402. `GET /plugins` adds `PluginInfo.in_plan`. The connection is never
touched: no refresh, no Google call while not entitled; `testing_expired` still marks it reconnect-needed on read.
Disconnect, "Use in chat" and cancelling a pending send always work. The admin's Plugins tab counts connections as
today.

### 5.7 Uploads, documents and limits

- `POST /documents`: nginx's upload check (6.11) refuses a lapsed user before the body; the route also has
  `get_entitled_user`. Count, storage and size limits come from `ent.limits` (B28: over a lowered limit, uploads are
  refused with the existing 409 messages, which gain "Delete some documents or upgrade your plan.").
- `POST /documents/{id}/reprocess` doesn't check the file-size limit (the file was accepted when uploaded).
- Generated files, images and Gmail imports use the same limits through `document_tool._no_room`,
  `image_tool._no_image_room`, `plugins/importing.py` and `plugins/gmail/importer.py`, all given `ent`. Existing
  generated files and images stay viewable and downloadable on every active plan, whatever its features.
- Ingestion's vision allowance (`ingestion._vision_allowance`) loads the owner's entitlements: no access → treated as
  used up (message: "This PDF has scanned pages, but your plan has ended, so they weren't read. Renew your plan and
  reprocess the document."); else `ent.limits.vision_monthly_token_limit` in `ent`'s window. A run gets what is left
  when it starts and starts no new vision call once it has used that (the calls in flight finish, at most
  `VISION_CONCURRENCY`): scanned pages left unread fail the document with the allowance message, descriptions left
  unread are skipped, and the pages read stay cached for a later run. Shared documents (the admin's) are exempt as
  today.

### 5.8 Error shape and codes

Errors keep FastAPI's `{"detail": "message"}`. Errors of this feature add `code` and, where useful, `feature` and
`plan` (`app/errors.py: class AppError(HTTPException)` with an exception handler in `main.py`; the SPEC's error
paragraph says "some errors carry a machine-readable `code`").

| Status | `code` | `feature` / `plan` | `detail` (examples) |
|---|---|---|---|
| 402 | `plan_required` | – / – | "Choose a plan to start using the assistant." |
| 402 | `plan_expired` | – / the expired plan's name | "Your Standard plan ended on 25 Nov 2026. Renew it to continue; your chats and documents are kept." |
| 402 | `feature_not_in_plan` | `files`, `images`, `plugin:<id>`, `model` / the cheapest active plan that has it, or null | "Your Starter plan doesn't include Gmail. It's included in Professional." |
| 429 | `allowance_used` | `chat_tokens`, `images`, `vision_tokens` / – | 5.3 |
| 403 | `admin_exempt` | – | "The admin doesn't need a plan." |
| 409 | `checkout_unavailable` | – | the option's reason (4.3) |
| 409 | `confirmation_required` | – | "This lowers what 3 users paid for until their periods end." (plan edit, 3.5) |
| 429 | `too_many_checkouts` | – | "Too many payment attempts. Try again in an hour." / "…tomorrow." (`Retry-After`) |
| 503 | `payments_unavailable` | – | "Online payment isn't set up yet. Contact <business email>." |
| 502 | `payment_provider_error` | – | "The payment service didn't answer. Try again in a minute." (Razorpay's description is logged, never shown) |

Dates in messages are IST dates written "25 Nov 2026" (B34): the valid-through date of a period. Clients use `code`,
not the text.

### 5.9 What `GET /auth/me` returns

`Me` gains `plan: PlanAccess` (7.1); `Me.limits` gains `vision_monthly_token_limit` and is the merged limits (5.2);
`Me.usage` counts in the window and gains `chat_allowance_used`, `window_start` and `window_end`. `PlanAccess.unlock`
names, per locked feature, the cheapest `active` plan that includes it (by price; null when none does), and for models
the cheapest higher plan whose model set includes an enabled chat model the user can't use; it is computed from the
plans table (cached in the process for 60 s and cleared on every plan change). `build_me` stays a handful of indexed
queries.

## 6. Payments (Razorpay)

### 6.1 Integration

Payment Links created through the REST API (R1), one per checkout attempt, Standard links (`upi_link` absent). The
browser goes to the link's `short_url` in the same tab (a top-level navigation, which the CSP doesn't restrict) and
Razorpay sends it back to `callback_url` with a signed GET. The Razorpay page offers UPI (intent on phones, QR on
desktop; research 5.1), cards, netbanking and wallets. All calls: `httpx.AsyncClient`, base `RAZORPAY_API_BASE`,
`Authorization: Basic base64(key_id:key_secret)` exactly (research 7.1), JSON, timeout 10 s, **never inside a
database transaction or under a lock** (B42). `app/services/billing/razorpay.py` holds the client and both signature
checks:

```python
# payment link callback (key secret), research 2.5
expected = hmac_sha256_hex(key_secret, f"{plink_id}|{reference_id}|{status}|{payment_id}")
# webhook (webhook secret, then RAZORPAY_WEBHOOK_SECRET_PREVIOUS when set), research 3.2, over the raw body bytes
expected = hmac_sha256_hex(webhook_secret, raw_body)
# compared with hmac.compare_digest on lower-case hex
```

Client calls used: create, fetch and cancel a payment link, find links by `reference_id`, fetch a payment, create
and fetch a refund, fetch a dispute.

### 6.2 Data model (`models.py`; migration 0003)

New tables. Every `user_id` that must outlive the user is `ON DELETE SET NULL` with a `username` copy (B30); the
deletion-completeness guard lists them as kept rows (12.1).

```python
plan_chat_models = Table("plan_chat_models", Base.metadata,
    Column("plan_id", ForeignKey("plans.id", ondelete="CASCADE"), primary_key=True),
    Column("model_id", ForeignKey("llm_models.id", ondelete="CASCADE"), primary_key=True))

class Plan(Base):                       # 3.1
    __tablename__ = "plans"
    id: int (pk); code: String(32) unique; name: String(60); description: String(300) = ""
    price_paise: BigInteger; rank: Integer unique; status: String(16)          # active | retired
    recommended: Boolean = False
    features: JSON                                                             # {files, images, plugins: [ids]}
    monthly_token_limit: BigInteger; rate_limit_per_min: Integer; max_upload_mb: Integer
    max_documents: Integer; max_storage_mb: Integer; monthly_image_limit: Integer; vision_monthly_token_limit: BigInteger
    restrict_models: Boolean; chat_models: relationship(secondary=plan_chat_models, lazy="selectin")
    default_chat_model_id: FK llm_models SET NULL, nullable
    created_at, updated_at

class PlanPeriod(Base):                 # 4.2
    __tablename__ = "plan_periods"      # Index("ix_plan_periods_user_ends", "user_id", "ends_at")
    id: int (pk); user_id: FK users CASCADE; plan_id: FK plans SET NULL, nullable (the snapshot keeps the name)
    starts_at, ends_at: UTCDateTime; days: Integer           # 30 paid, 1 test, 1..30 comp rows
    source: String(16)                  # payment | comp
    mode: String(8)                     # test | live | offline | none
    order_id: FK billing_orders SET NULL, nullable          # the order that bought it
    snapshot: JSON                      # PlanSnapshot (below)
    charge_basis_paise: BigInteger      # 4.4
    history: JSON = []                  # [{at, kind: "ended_by_order"|"plan_applied"|"change"|"restored", by, order_id,
                                        #   snapshot_before?, basis_before?, ends_at_before?}]
    reason: String(200) | None; granted_by: String(64) | None
    revoked_at: UTCDateTime | None; revoked_reason: String(200) | None
    created_at, updated_at

class BillingOrder(Base):               # one checkout attempt (or one recorded transfer)
    __tablename__ = "billing_orders"    # Index on (user_id, status), on next_check_at
    id: int (pk); public_id: String(24) unique           # secrets.token_urlsafe(15): URLs, reference_id "rag-<public_id>"
    user_id: FK users SET NULL, nullable; username: String(64)
    plan_id: FK plans SET NULL, nullable; plan_snapshot: JSON          # what was quoted
    kind: String(16)                    # period | now
    replaces_period_id: FK plan_periods SET NULL, nullable; replaces_plan_id: Integer | None
    replaces_ends_at: UTCDateTime | None; credit_paise: BigInteger = 0          # 4.4
    amount_paise: BigInteger; currency: String(3) = "INR"
    mode: String(8)                     # test | live | offline
    status: String(16)                  # pending | paid | expired | cancelled | review
    review_reason: String(200) | None; reviewed_at | None; reviewed_by: String(64) | None; review_note: String(500) | None
    terms_accepted_at: UTCDateTime | None                    # the consent line (8.3)
    razorpay_link_id: String(40) unique, nullable; razorpay_order_id: String(40) indexed, nullable
    pay_url: String(200) | None; origin: String(200)    # the app origin the callback returns to
    expires_at: UTCDateTime             # the link's expire_by
    watch_until: UTCDateTime            # expires_at + BILLING_LATE_CAPTURE_DAYS
    next_check_at: UTCDateTime | None; check_step: Integer = 0; last_checked_at | None; checks: Integer = 0
    last_error: String(200) | None      # our own short reason ("Razorpay unreachable"), never a secret
    last_payment_error: String(200) | None   # the last failed attempt's error_description, for the user
    paid_at | None; payment_id: FK billing_payments SET NULL, nullable   # the applied payment
    created_at, updated_at

class BillingPayment(Base):
    __tablename__ = "billing_payments"
    id: int (pk); razorpay_payment_id: String(40) unique, nullable       # null for offline payments
    order_id: FK billing_orders SET NULL, nullable
    user_id: FK users SET NULL, nullable; username: String(64)
    status: String(24)                  # captured | failed | refunded | partially_refunded | disputed
    amount_paise: BigInteger; currency: String(3); method: String(24) | None   # upi | card | netbanking | wallet | emi | bank_transfer | …
    fee_paise, fee_tax_paise: BigInteger | None          # Razorpay's fee and the GST on it (payment entity)
    error_code: String(64) | None; error_reason: String(200) | None
    captured_at | None; applied_at | None                # applied_at: the exactly-once guard (6.5)
    refunded_paise: BigInteger = 0      # = SUM(processed refunds), recomputed, never incremented (6.7)
    mode: String(8); offline_reference: String(64) | None; recorded_by: String(64) | None
    created_at, updated_at

class BillingRefund(Base):
    __tablename__ = "billing_refunds"
    id: int (pk); payment_id: FK billing_payments CASCADE
    razorpay_refund_id: String(40) unique, nullable; idempotency_key: String(40) unique   # "rfd_<token>"
    amount_paise: BigInteger; status: String(16)        # creating | pending | processed | failed
    initiated_by: String(16)            # admin | razorpay
    admin_username: String(64) | None; reason: String(200) | None
    end_access: Boolean; access_ended_at | None; last_error: String(200) | None
    created_at, processed_at | None, updated_at

class BillingDispute(Base):             # 6.8
    __tablename__ = "billing_disputes"
    id: int (pk); razorpay_dispute_id: String(40) unique; payment_id: FK billing_payments CASCADE
    amount_paise: BigInteger; status: String(24)        # open | under_review | action_required | won | lost | closed
    reason_code: String(64) | None; respond_by: UTCDateTime | None; access_ended_at | None
    created_at, updated_at

class BillingReceipt(Base):             # 9
    __tablename__ = "billing_receipts"  # UniqueConstraint(mode, financial_year, seq)
    id: int (pk); number: String(32) unique; mode: String(8)   # live (Razorpay live and offline) | test
    financial_year: String(7); seq: Integer
    payment_id: FK billing_payments CASCADE, unique
    user_id: FK users SET NULL, nullable
    issued_at: UTCDateTime; seller: JSON; buyer: JSON; lines: JSON      # lines may hold a negative credit line
    amount_paise: BigInteger; method: String(24) | None
    created_at

class BillingEvent(Base):               # the audit trail, the webhook inbox and its dedupe
    __tablename__ = "billing_events"    # Index on (type, created_at), on (processed, source)
    id: int (pk); created_at
    source: String(16)                  # webhook | callback | reconcile | user | admin | system
    type: String(48)                    # e.g. payment.captured, order.created, period.granted, refund.processed, plan.updated
    razorpay_event_id: String(64) unique, nullable; body_sha256: String(64) unique, nullable   # webhook dedupe
    order_id, payment_id: FK SET NULL, nullable; user_id: FK users SET NULL, nullable; actor: String(64) | None
    detail: JSON = {}                   # ids, statuses, amounts, method, before/after values: never secrets,
                                        # bodies or payer details (email, contact, vpa, card, bank)
    processed: Boolean = True; attempts: Integer = 0; error: String(200) | None

class UserBillingProfile(Base):         # optional, the user's own
    __tablename__ = "user_billing_profiles"
    user_id: FK users CASCADE (pk); name: String(100) | None; email: String(254) | None
    updated_at
```

`PlanSnapshot` (JSON, pydantic in `schemas_billing.py`): `{plan_id, code, name, features, limits: UserLimits,
restrict_models, chat_model_ids, default_chat_model_id}`.

**Changed tables**:

- `users`: `monthly_token_limit`, `rate_limit_per_min`, `max_upload_mb`, `max_documents`, `max_storage_mb` become
  nullable (NULL = the plan's value); new nullable `monthly_image_limit` (Integer) and `vision_monthly_token_limit`
  (BigInteger); new `billing_tester` (Boolean, default false); new `comp_plan_id` (FK plans SET NULL, nullable),
  `comp_reason` (String(200)), `comp_granted_by` (String(64)), `comp_granted_at` (UTCDateTime), all nullable.
  `restrict_models` keeps its column; it now narrows the plan's models.
- `usage_events`: new `allowance_tokens` (BigInteger, default 0; 3.3).
- `user_image_limits` is dropped (4.11).

**`app_state` keys**: `billing_settings` (JSON, 6.10), `receipt_seq:<mode>:<fy>` (the last number, 9),
`plans_seeded`, `plans_rollout`.

### 6.3 Creating a checkout (`POST /billing/checkout`)

1. 403 for the admin; 503 `payments_unavailable` when payments aren't configured, or with test keys for a user who
   isn't a tester; 422 without `accept_terms: true`.
2. Caps: at most 5 links in any 60 minutes (Redis sorted set `rl:checkout:<user>`) and 15 per IST day
   (`rl:checkout-day:<user>:<date>`) → 429 `too_many_checkouts` with `Retry-After`. A link is counted when it is
   about to be made (step 3, under the user's lock, after the option and the reuse checks): each count is added first
   and taken back when it passes the cap, so checkouts at the same time can't pass a cap together.
3. In a transaction with the user's row locked (`with_for_update(key_share=True)`, B42): compute the option (4.3) and
   its amount and credit (4.4) **from the database only**; nothing about the amount or plan comes from the client
   except the plan id and kind. Unavailable → 409 `checkout_unavailable`. When an open order of the user has the same
   plan, kind and amount and at least 10 minutes left, return it (201, nothing created). Else insert the order
   (`pending`, mode from the key id, `origin` = the request's `Origin` when it is one of `PUBLIC_BASE_URLS`, else the
   first entry, `terms_accepted_at` and `created_at` = the quote's time, `expires_at` = now +
   `BILLING_CHECKOUT_MINUTES`, `next_check_at` = now + 30 s) and commit, so the reference exists before Razorpay does.
4. `POST /v1/payment_links` (no transaction open):

   ```json
   {"amount": 200000, "currency": "INR", "accept_partial": false,
    "reference_id": "rag-<public_id>", "description": "Standard plan, 30 days",
    "expire_by": <unix seconds of expires_at>, "reminder_enable": false,
    "notify": {"sms": false, "email": false},
    "notes": {"order": "<public_id>", "user_id": "<id>", "plan": "standard", "kind": "period"},
    "callback_url": "<origin>/api/billing/razorpay/return", "callback_method": "get"}
   ```

   No `customer` object (B30). `expire_by` is at least 16 minutes ahead (Razorpay requires 15). `reference_id` is our
   idempotency key at Razorpay (research 2.5).
5. Success: check `short_url` (`https://rzp.io/…` or `https://<sub>.razorpay.com/…`; any `https` origin only with test
   keys and a non-default `RAZORPAY_API_BASE`, for the fake); a failing check is treated as a Razorpay error. Under
   the user's lock, in one transaction: store `razorpay_link_id`, `pay_url` and `order_id` when the response has it
   (else the first fetch stores it) **only while the order is still `pending`** (a conditional update), event
   `order.created`, and mark the user's other `pending` orders `cancelled` (a conditional update returning their
   links). **Then** cancel their links: `POST /v1/payment_links/<id>/cancel` (a failure is ignored; the order stays
   watched until `watch_until`, and the reconciler cancels a cancelled order's link it finds still payable). Respond
   201 `CheckoutStarted`. When the order was cancelled while its link was made (another checkout of the user replaced
   it, or the user cancelled it), it keeps only the link id, the new link is cancelled and the answer is 409
   `checkout_unavailable` ("Another checkout was started at the same time. Open the plans page to see the one to
   pay."), event `order.superseded`.
6. A 4xx from Razorpay: the order becomes `cancelled` with `last_error`, event `order.create_failed` (Razorpay's
   `error.code` and `description`, which carry no secrets), 502 `payment_provider_error`. A timeout or 5xx: `GET
   /v1/payment_links?reference_id=rag-<public_id>`; a link found is stored and cancelled; the order becomes
   `cancelled`; 502. In both cases the previous pending order and its link stay as they were.

### 6.4 Confirmation

Every path fetches from Razorpay first, with no transaction open, then calls `payments.apply_payment(db, order,
payment)` (6.5). A Redis lock `billing:refresh:<order>` (`SET NX EX 15`) keeps two fetches off one order.

**Callback** (`GET /billing/razorpay/return`, no session):

1. Read `razorpay_payment_id`, `razorpay_payment_link_id`, `razorpay_payment_link_reference_id`,
   `razorpay_payment_link_status`, `razorpay_signature` (each at most 64 characters, else treated as missing).
2. Find the order by `razorpay_link_id`; its `reference_id` must equal `rag-<public_id>`. Unknown → 303
   `<first PUBLIC_BASE_URLS entry>/plans?payment=unknown`.
3. Verify the signature (6.1). A mismatch → 303 `<origin>/billing/return?order=<id>&check=failed`; nothing changes.
4. Unknown links and bad signatures increment hourly Redis counters (`billing:cb:unknown:<hour>`,
   `billing:cb:badsig:<hour>`, kept 48 h); at most one sample event per minute is written (`callback.unknown`,
   `callback.bad_signature`, no ids beyond the link id).
5. When the status is `paid`, the order isn't `paid` and the payment isn't applied, and the lock is free: fetch the
   link and the payment (at most 8 s in total) and apply when captured. A failed fetch sets `next_check_at` = now.
6. Always 303 to `<order.origin>/billing/return?order=<public_id>` with `Cache-Control: no-store` and
   `Referrer-Policy: no-referrer`.

**Webhook** (`POST /billing/razorpay/webhook`, no session). It only records and wakes; it never changes an order,
payment, refund or period from the payload (B15):

1. No `RAZORPAY_WEBHOOK_SECRET` → 503 (counted). Body over 256 KB → 413.
2. Verify `X-Razorpay-Signature` over the raw bytes with the secret, then with `RAZORPAY_WEBHOOK_SECRET_PREVIOUS` when
   set. Mismatch → 400, counter `billing:wh:badsig:<hour>`, at most one sample `webhook.bad_signature` event a minute.
3. Parse the JSON. A body whose `created_at` is more than 3 days old → 200, counter `billing:wh:stale:<hour>`.
4. Dedupe: an event with the same `x-razorpay-event-id` or the same `sha256(raw body)` exists → 200 at once.
5. Insert the event (`source` webhook, `type` the event name, `processed` false, `detail` with ids only: link, order,
   payment, refund, dispute, plus status, amount, currency and method), commit, then `RPUSH rag:billing_wake
   <event id>`; 200 `{"ok": true}`. A database error answers 500 (Razorpay retries; nothing was recorded).

**The worker** (the billing loop, `billing/reconcile.py`) takes woken event ids (`BLPOP rag:billing_wake`, timeout
`BILLING_RECONCILE_SECONDS`) and processes each:

- `payment_link.paid`, `payment_link.expired`, `payment_link.cancelled`: reconcile that order now (step 2 below).
- `payment.captured`, `payment.failed`: the order by `razorpay_order_id` → reconcile it now. No order matched yet →
  the event stays unprocessed; the reconciler re-matches unprocessed payment events whenever it stores an order's
  `razorpay_order_id`.
- `refund.created`, `refund.processed`, `refund.failed`: `GET /v1/refunds/<id>` → 6.7 (our refund by `notes.refund`,
  else by `razorpay_refund_id`, else a new row with `initiated_by: "razorpay"` for a payment we know).
- `payment.dispute.created`, `.under_review`, `.action_required`, `.won`, `.lost`, `.closed`: `GET
  /v1/disputes/<id>` → 6.8.
- anything else: marked processed (ignored).

Each processing marks the event `processed`, or increments `attempts` with `error`; after 3 failed attempts it stays
unprocessed and appears under "Needs attention" (the reconciler still covers the order). At start the loop re-queues
unprocessed events with fewer than 3 attempts. Events to subscribe in the Dashboard (Admin → Billing shows the list
and the URL): `payment_link.paid`, `payment_link.expired`, `payment_link.cancelled`, `payment.captured`,
`payment.failed`, `refund.created`, `refund.processed`, `refund.failed`, `payment.dispute.created`,
`payment.dispute.under_review`, `payment.dispute.action_required`, `payment.dispute.won`, `payment.dispute.lost`,
`payment.dispute.closed`.

**Reconciler** (the same loop, B16):

1. Every `BILLING_RECONCILE_SECONDS` (30) and on each wake, take up to 20 orders with `next_check_at <= now` (status
   `pending`, `expired` or `cancelled`, before `watch_until`) **whose mode matches the current keys** (others are
   paused, 6.9), oldest first. The backend reconciles single orders too (callback, `?refresh=true`, "Check now"); the
   `billing:refresh` lock keeps them apart.
2. Per order: `GET /v1/payment_links/<id>` → store `order_id`; for each listed payment not yet recorded with a final
   status, `GET /v1/payments/<payment_id>` → captured: apply; failed: record (and `last_payment_error`); created or
   authorized: wait. A failure followed by a capture is normal (research 3.3).
3. Link `expired`/`cancelled` and nothing captured → mark the order so (the user sees "No payment received"), keep
   watching.
4. Next check: every 30 s until `expires_at`, then `expires_at` + 1 min. After that, while a payment is `created` or
   `authorized`, hourly; otherwise after 1 h, 6 h and 24 h, then daily, until `watch_until`, then none (final).
   Razorpay unreachable, 429 or 5xx: `last_error`, the next check doubles from 30 s up to 15 min. **A 401, or a 400
   "Authentication failed"**, is a configuration error, not the order's: polling stops for every order, the error is
   shown in Admin → Billing and by the monitor, and the next start or "Check pending payments now" tries again.
5. Also: refunds `pending` for over 30 minutes are fetched; refunds left `creating` by a timeout are retried with the
   same key and body (6.7); hourly, the reminders (4.7). Each step catches its own errors and logs them without
   secrets; the loop never stops the worker.
6. `GET /billing/orders/<id>?refresh=true` runs step 2 for that order when the lock is free, so a waiting user
   doesn't wait for the loop: at most once every 10 s per order (`billing:refresh-recent:<order>`) and 30 times an
   hour per user (`billing:refresh-budget:<user>`); otherwise the stored order is answered. The admin's "Check now" is
   not limited.
7. A `cancelled` order whose link the fetch shows still payable (`created`, no payment in flight) gets the link
   cancelled, best effort (event `order.link_cancelled`).

### 6.5 Applying a payment exactly once

`apply_payment(db, order, payment)` takes a payment entity **already fetched** from Razorpay (or an offline payment);
its caller holds no transaction while fetching. In one short transaction:

1. Lock the user's row first (`SELECT … FROM users WHERE id = :uid FOR NO KEY UPDATE`; on SQLite the write lock
   serialises), then the order row (`FOR UPDATE`). Lock order everywhere: user → order → payment → refund or dispute
   → receipt counter (B42). Grants, revokes, change-plan, the `now` apply, refund and dispute access changes and the
   rollout all take the user lock first and compute "paid through" only after it.
2. Upsert `billing_payments` by `razorpay_payment_id` (insert in a savepoint; a unique-key race re-reads).
   `applied_at` already set → return (done before).
3. Check: `status == "captured"`; `currency == "INR"`; `amount == order.amount_paise` (Customer Fee Bearer and Offers
   stay off, 6.9); `payment.order_id == order.razorpay_order_id` (when known; else from the link fetch); the order's
   mode equals the server's current mode. Any mismatch → order `review` with the reason ("Amount differs: paid ₹X,
   expected ₹Y"), event `payment.mismatch`, nothing applied.
4. The order is already `paid` by another payment → `review` ("A second payment for one checkout"). The user was
   deleted → `review` ("The user was deleted").
5. Apply by kind: `period` → insert a period at `max(now, paid through)` with the order's snapshot (`days` 30, 1 in
   test mode), `charge_basis_paise = amount`; `now` → 4.4 (or `review` when the replaced period changed).
6. `applied_at = now`, order `paid`, `paid_at`, `payment_id`; the receipt (9); events `payment.captured` and
   `period.granted` or `period.started_now`. Commit.

Possible duplicates (two applied payments by one user within 24 hours, from different orders whose checkouts were
open at the same time: the second order was created before the first payment was applied) appear in Admin → Billing
→ Needs attention; both stay applied (the second added a period). A renewal or an upgrade bought after a payment
isn't one.

### 6.6 Failed, abandoned and late payments, and review

- **Failed attempt**: recorded (status `failed`, `error_code`, `error_reason`); the user may retry on the same link
  until it expires. The return page shows the last failure.
- **Abandoned**: the link expires after 20 minutes; the order shows `expired`; the reconciler keeps watching for 5
  days on the schedule of 6.4.
- **Late capture**: a `period` is applied when it arrives (it starts then); a `now` order goes to review when its
  replaced period changed meanwhile (4.4).
- **Review**: Admin → Billing lists `review` orders with the reason and three actions: **Refund** (6.7), **Grant
  instead** (opens a grant for the user), **Mark resolved** (a note, `reviewed_by`). The user's return page says "We
  received a payment and are checking it. Your plan starts as soon as it's confirmed. Please don't pay again."

### 6.7 Refunds

- **From the app** (`POST /admin/billing/payments/<id>/refund`, amount or null for all that is refundable, reason,
  `end_access`): under the user, payment and refund locks: 409 when nothing is refundable (4.10) or another refund of
  this payment is `creating`; insert the refund (`creating`, `idempotency_key`), commit; then, outside the
  transaction, `POST /v1/payments/<razorpay id>/refund` with `{"amount": …, "speed": "normal", "notes": {"refund":
  "<id>"}}` and the header `X-Refund-Idempotency: <key>` (research 5.3). Success → `pending` (or `processed`) with
  `razorpay_refund_id`. A timeout or 5xx → retried by the reconciler with the same key and body; a 4xx → `failed`
  with Razorpay's description. Offline payments: recorded only, `processed` at once.
- **A state change happens once**, whichever source sees it (the API response, a webhook fetch, the reconciler):
  lock the refund row (after the user and payment locks) and act only when the stored status differs. On reaching
  `processed`: recompute `payment.refunded_paise = SUM(amount_paise) WHERE status = 'processed'`, set the payment's
  status (`refunded`, `partially_refunded`), then apply 4.10's access rule from that sum, setting `access_ended_at`
  only when it is null; events `refund.processed`, and `period.revoked` or `period.restored`.
- **Dashboard refunds** are upserted by `razorpay_refund_id` (insert in a savepoint; a race re-reads).
- **Failed**: shown in Admin → Billing with Razorpay's reason; access unchanged.

### 6.8 Disputes

Fetched dispute entities (from a webhook wake) are upserted by `razorpay_dispute_id` under the user, payment and
dispute locks, with the payment found by its Razorpay id (an unknown payment: the event stays unprocessed and is
listed). Status changes apply once, as for refunds:

- `open` (created), `under_review`, `action_required`: a "Needs attention" item "Dispute of ₹2,000 by maya.chen:
  respond in Razorpay's Dashboard by 4 Dec 2026"; the monitor warns while any dispute needing action has
  `respond_by` within 3 days (11.3).
- `lost`: 4.10's full-refund access rule (once, `access_ended_at`), payment status `disputed`, counted in
  `BillingSummary.disputed_paise`.
- `won`, `closed`: resolved; the item goes.

Responding to a dispute happens in Razorpay's Dashboard; the app only tracks it. The refunds policy placeholder
mentions chargebacks (10).

### 6.9 Test and live mode

- `RAZORPAY_KEY_ID` starting `rzp_test_` → test, `rzp_live_` → live, anything else → payments off with an error at
  start-up ("RAZORPAY_KEY_ID must start with rzp_test_ or rzp_live_"). Both key id and secret are needed. **With a
  live key, the backend refuses to start unless `RAZORPAY_API_BASE` is `https://api.razorpay.com`.**
- **Test keys: testers only.** Users with `billing_tester` (set in Admin → Users) see the plans with working options
  and the test notice ("Test mode: no real money moves. Pay with card 4100 2800 0000 1007, any future expiry and any
  CVV." — cards only until 14.2(3) confirms a UPI path); everyone else sees "Online payment isn't set up yet." and
  gets 503 at checkout. Test periods last 1 day and count only while the keys are test keys. Test receipts are
  `TEST/26-27/000001` with a diagonal "TEST: not a real payment" watermark.
- **A warning** (Admin → Billing attention and the monitor) while test keys run and `PUBLIC_BASE_URLS` holds an
  `https` origin that isn't localhost, and while live keys run without `RAZORPAY_WEBHOOK_SECRET`.
- **Switching keys** (test to live or back) is a key change in `.env` plus `docker compose up -d backend worker`.
  Orders of the other mode are **paused**: never cancelled, applied or polled, kept watched, and resumed when the keys
  match again; Admin → Billing says "3 live orders can't be checked with test keys."
- **Amounts must match exactly**: Customer Fee Bearer and Offers stay off in Razorpay's Dashboard (the going-live
  checklist says so).

### 6.10 Settings

`.env` (`config.py`, `.env.example` in a new "Payments" block; ranges stop the backend at start-up like the others):

| Setting | Default | Range | What it does |
|---|---|---|---|
| `RAZORPAY_KEY_ID` | empty | | with the secret: payments available; the prefix sets the mode |
| `RAZORPAY_KEY_SECRET` | empty | | Basic auth and the callback signature; never logged or shown |
| `RAZORPAY_WEBHOOK_SECRET` | empty | | webhooks accepted only when set |
| `RAZORPAY_WEBHOOK_SECRET_PREVIOUS` | empty | | also accepted, for the day after a rotation; remove it then |
| `RAZORPAY_API_BASE` | `https://api.razorpay.com` | `https://` URL; with a live key only the default | the fake in tests |
| `BILLING_CHECKOUT_MINUTES` | 20 | 16 to 120 | a payment link's lifetime |
| `BILLING_LATE_CAPTURE_DAYS` | 5 | 1 to 10 | how long an unpaid checkout is still watched |
| `BILLING_REMINDER_DAYS` | 5 | 1 to 14 | the banner and the first reminder email |
| `BILLING_RECONCILE_SECONDS` | 30 | 2 to 300 | the reconciler's tick (tests use 2) |
| `BILLING_USD_INR` | 88 | 1 to 1000 | rupees per dollar, for the cost estimates only |
| `BILLING_EMAIL_FROM` | empty | an address | with the `ALERT_SMTP_*` server settings: reminder emails |

**Removed** (now per plan): `DEFAULT_MONTHLY_TOKEN_LIMIT`, `DEFAULT_RATE_LIMIT_PER_MIN`, `DEFAULT_MAX_UPLOAD_MB`,
`DEFAULT_MAX_DOCUMENTS`, `DEFAULT_MAX_STORAGE_MB`, `DEFAULT_MONTHLY_IMAGE_LIMIT`, `VISION_MONTHLY_TOKEN_LIMIT`. A value
still in `.env` is ignored with one warning at start-up naming it ("…is now set per plan in Admin → Plans").
`MAX_UPLOAD_MB_HARD` stays.

**Billing settings** (Admin → Billing → Business & payments; `app_state` `billing_settings`, validated by pydantic;
defaults in brackets): `business_name` (`APP_NAME`; 1 to 100), `address` (empty; at most 500 characters, several
lines), `contact_email` (empty), `contact_phone` (empty; at most 20), `receipt_prefix` (`RA`; 1 to 4 of `A-Z0-9`),
`upi_limit_paise` (200,000 = ₹2,000; 0 = no limit; research 5.1: an unverified merchant collects at most ₹2,000 per
UPI payment; the owner sets 0 once Razorpay confirms the account has no such limit).

### 6.11 nginx (`frontend/nginx.conf`), exactly

- **CSP: no change**, in both places (B14). No `script-src`, `frame-src`, `connect-src` or `form-action` exception.
- Rename `log_format plugin_path` to `path_only` (and its one use); it logs the path without the query.
- At the top level of the file (the `http` context, beside `log_format`):

  ```nginx
  limit_req_zone $binary_remote_addr zone=billing_return:1m rate=30r/m;
  limit_req_zone $binary_remote_addr zone=billing_webhook:1m rate=120r/m;
  ```

- Add, before `location /api/plugins`:

  ```nginx
  # Razorpay sends the browser back here (a cross-site GET: no session cookie arrives). The query holds payment ids
  # and a signature, so only the path is logged, and errors only at crit (an upstream error logs the request line).
  location = /api/billing/razorpay/return {
      limit_req zone=billing_return burst=20 nodelay;
      limit_req_status 429;
      access_log /var/log/nginx/access.log path_only;
      error_log /var/log/nginx/error.log crit;
      proxy_pass http://backend:8000;
      proxy_http_version 1.1;
      proxy_set_header Host $host;
      proxy_set_header X-Real-IP $remote_addr;
      proxy_set_header X-Forwarded-For $remote_addr;
      proxy_set_header X-Forwarded-Proto $scheme;
      proxy_set_header Connection "";
  }

  # Razorpay's server-to-server webhooks: no Sec-Fetch-Site header, so the cross-site guard lets them through; a
  # browser's cross-site POST here is still refused by it. The backend checks the HMAC signature.
  location = /api/billing/razorpay/webhook {
      limit_except POST { deny all; }
      limit_req zone=billing_webhook burst=60 nodelay;
      limit_req_status 429;
      client_max_body_size 256k;
      access_log /var/log/nginx/access.log path_only;
      proxy_pass http://backend:8000;
      proxy_http_version 1.1;
      proxy_set_header Host $host;
      proxy_set_header X-Real-IP $remote_addr;
      proxy_set_header X-Forwarded-For $remote_addr;
      proxy_set_header X-Forwarded-Proto $scheme;
      proxy_set_header Connection "";
      proxy_read_timeout 30s;
  }
  ```

- The upload check (B38): in `location = /_upload_auth`, `proxy_pass http://backend:8000/api/auth/upload-allowed;`
  (instead of `/api/auth/me`); in `location = /api/documents`, add `error_page 403 = @upload_plan_required;` beside
  the 401 one; and

  ```nginx
  location @upload_plan_required {
      default_type application/json;
      return 402 '{"detail":"You need an active plan to upload documents.","code":"plan_required"}';
  }
  ```

- The policy pages' Markdown (`/legal/*.md`, B31): `location /legal/ { types { text/markdown md; } charset utf-8;
  try_files $uri =404; }` with `Cache-Control "no-cache"` and the same security headers as `location /` (nginx doesn't
  inherit `add_header` into a location that sets its own).
- **Real client addresses**: until the Cloudflare tunnel runs, every tunnelled request has the tunnel's address, so
  the limits above are per tunnel (generous on purpose). When the integrator sets up the domain's tunnel, add
  `real_ip_header CF-Connecting-IP;` and `set_real_ip_from <the cloudflared container's network>;` so the limits
  (and `X-Real-IP`) key on the real client.

The `$xsite_write` guard already covers `POST /api/billing/checkout`, the cancel and every admin write. Public pages
are SPA routes.

### 6.12 Security and privacy

- **Secrets**: the key secret and both webhook secrets are never logged, never in an event, an error or the admin API
  (it shows `key_id_hint` "rzp_test_…AbCd", `webhook_secret_set` and `previous_webhook_secret_set`). `main.py`'s log
  redaction adds the `/api/billing/razorpay/return` query, like the plugin queries. The monitor's alert scrubbing
  already removes every environment value whose name looks secret (`*_SECRET`).
- **Integrity**: amounts are computed on the server only; activation needs a capture fetched from Razorpay whose
  amount, currency, order and mode match the stored order (6.5); signatures use `hmac.compare_digest`; the callback
  trusts its query only to locate the order; webhooks are deduplicated by id and body and never applied from their
  payload; `apply_payment`, refunds and disputes change state once; every period change holds the user lock.
- **No open redirect**: the callback redirects only to the order's stored origin, itself one of `PUBLIC_BASE_URLS`;
  `pay_url` must be a Razorpay URL (6.3), and the browser checks it again before leaving (8.3).
- **Authorization**: orders, payments and receipts are the caller's own (404 otherwise, as everywhere); the admin
  sees all.
- **Personal data**: Razorpay gets the amount, a description, our reference and notes with the numeric user id; no
  name, email or phone. From Razorpay we keep the payment id, status, amount, method, fee and error reason; never the
  payer's email, phone, UPI id, card or bank, in any table, events included (a test checks it). The billing profile
  is optional and the user's own. The data export gains `billing.json` (the user's periods, orders, payments and
  receipt numbers).
- **Retention**: billing records are kept after user deletion for the accounts (B30), with the username only.

## 7. API (the contract change)

Stage 0 applies this section to `SPEC.md` (sections 3, 4, 6, 8 and 10), `frontend/src/api/types.ts` and a new
`backend/app/schemas_billing.py`; the parts that would break today's frontend build before its builder adapts it
(`MyUsage`, the admin-user shapes) land with that builder's first change (13.2), so the contract is whole before
anything is committed (CLAUDE.md). Every request body is validated by pydantic with `extra="forbid"`; amounts are
JSON integers (paise); times ISO 8601 UTC; dates `YYYY-MM-DD` (billing dates are IST dates).

### 7.1 Shapes

```ts
// ---- shared: UserLimits, UsageSummary, MyUsage, ChatModel and Me in schemas.py / schemas_account.py; plan types in schemas_billing.py ----
type UserLimits = { monthly_token_limit: number /* the chat allowance, standard tokens */; rate_limit_per_min: number;
  max_upload_mb: number; max_documents: number; max_storage_mb: number; monthly_image_limit: number;
  vision_monthly_token_limit: number /* new */ }          // per period for subscribers; 0 = unlimited
type UsageSummary = { /* as today */ chat_allowance_used: number /* new: standard tokens counted in the window */;
  window_start: string; window_end: string /* new: the allowance window */ }   // monthly_token_limit counts chat_allowance_used
type MyUsage = { /* as today, except: */ chat_allowance: UsageAmount /* new: standard tokens against the allowance */;
  chat_tokens: number /* was UsageAmount: plain chat tokens, no limit */;
  period_start: string; resets_at: string /* the window's start and end */;
  daily: { /* as today */ chat_allowance: number /* new */ }[];
  by_model: { /* as today */ allowance_tokens: number /* new; 0 for non-chat kinds */ }[] }
type ChatModel = { /* as today */ allowance_rate: number|null /* new: ×rate against the allowance, 3.3; null: plain tokens */ }
type PlanFeatures = { files: boolean; images: boolean; plugins: string[] /* plugin ids */ }
type PlanRef = { id: number; code: string; name: string }
type PlanState = "exempt"|"none"|"active"|"expired"
type CheckoutKind = "period"|"now"
type PendingOrder = { public_id: string; kind: CheckoutKind; plan: PlanRef; amount_paise: number;
  created_at: string; expires_at: string; pay_url: string|null /* null once the link expired */ }
type PlanUnlock = { files: string|null; images: string|null; plugins: Record<string, string|null>;
  models: string|null }                                   // the name of the cheapest active plan that includes it
type PlanAccess = { state: PlanState; access: boolean /* exempt or active */;
  plan: PlanRef|null /* current (period or open-ended), or the last one when expired */;
  complimentary: boolean; open_ended: boolean; test_mode: boolean;
  period_start: string|null; period_end: string|null /* the current period; null for open-ended */;
  paid_through: string|null; valid_through: string|null /* IST date of the last day with access; null: open-ended */;
  next_plan: PlanRef|null; expiring_soon: boolean; ended_at: string|null /* expired: when access ended */;
  features: PlanFeatures /* exempt: everything; no access: nothing */; pending_order: PendingOrder|null;
  can_start_new_period: boolean /* 5.3 */; unlock: PlanUnlock }
type Me = { /* as today */ plan: PlanAccess }

// ---- billing (schemas_billing.py) ----
type PlanModel = { id: number; display_name: string; allowance_rate: number|null;
  answers: number|null /* about this many answers with this plan's allowance, 3.3; null: unlimited or unpriced */ }
type PlanPlugin = { id: string; name: string; available: boolean /* set up and turned on by the admin */ }
type CreditLine = { description: string /* "Credit for unused Starter, 26 Oct to 25 Nov" */; amount_paise: number }
type CheckoutOption = { kind: CheckoutKind; label: string; starts_at: string; ends_at: string;
  valid_through: string /* IST date */; amount_paise: number /* charged */; plan_price_paise: number;
  credit: CreditLine|null /* now only */; changes: string[] /* what the switch removes, 4.5; may be empty */;
  notes: string[] /* e.g. "Your complimentary plan continues after it." */; unavailable_reason: string|null }
type PlanOffer = { id: number; code: string; name: string; description: string; price_paise: number;
  recommended: boolean; features: PlanFeatures; plugins: PlanPlugin[]; limits: UserLimits;
  chat_models: PlanModel[] /* resolved: every enabled chat model when unrestricted */; all_chat_models: boolean;
  default_chat_model: PlanModel|null; answers_estimate: number|null /* with the default model */;
  status: "active"|"retired"; current: boolean; options: CheckoutOption[] }
type PaymentsInfo = { available: boolean /* for this caller: keys set, and a tester under test keys */;
  mode: "test"|"live"|null /* null when not available to this caller */; contact_email: string|null;
  upi_limit_paise: number /* 0: no limit */; email_reminders: boolean /* SMTP and BILLING_EMAIL_FROM set */ }
type PlanCatalog = { plans: PlanOffer[]; payments: PaymentsInfo }
type OrderStatus = "pending"|"paid"|"expired"|"cancelled"|"review"
type ReceiptRef = { id: number; number: string }
type OrderView = { public_id: string; kind: CheckoutKind; plan: PlanRef; amount_paise: number; credit_paise: number;
  status: OrderStatus; created_at: string; expires_at: string; paid_at: string|null;
  pay_url: string|null /* pending, not expired */; last_payment_error: string|null;
  period_start: string|null; period_end: string|null; valid_through: string|null /* what it bought */;
  receipt: ReceiptRef|null; method: string|null /* upi | card | netbanking | wallet | emi | bank_transfer */;
  test_mode: boolean }
type CheckoutRequest = { plan_id: number; kind: CheckoutKind; accept_terms: true }
type CheckoutStarted = { order: OrderView; pay_url: string }
type PaymentStatus = "captured"|"refunded"|"partially_refunded"|"disputed"
type PaymentView = { id: number; paid_at: string; plan: PlanRef|null; kind: CheckoutKind; description: string;
  amount_paise: number; method: string|null; status: PaymentStatus; refunded_paise: number;
  period_start: string|null; period_end: string|null /* null: a receipt-only offline payment */;
  receipt: ReceiptRef|null; test_mode: boolean }
type PeriodSource = "payment"|"comp"
type PeriodView = { id: number; plan: PlanRef|null; plan_name: string; starts_at: string; ends_at: string;
  valid_through: string; source: PeriodSource; current: boolean; test_mode: boolean; reason: string|null /* comps */ }
type BillingProfile = { name: string|null /* ≤100 */; email: string|null /* ≤254 */ }
type ComplimentaryView = { plan: PlanRef; reason: string; granted_by: string; granted_at: string }
type BillingStatus = { plan: PlanAccess; periods: PeriodView[] /* unexpired, in order */;
  complimentary: ComplimentaryView|null /* the open-ended plan */; open_order: OrderView|null;
  profile: BillingProfile; payments: PaymentsInfo }

// ---- public (schemas_billing.py) ----
type PublicPlan = Omit<PlanOffer, "id"|"status"|"current"|"options">
type PageSlug = "pricing"|"terms"|"privacy"|"refunds"|"contact"|"about"|"shipping"   // the Markdown files of section 10
type PublicSite = { business_name: string; contact_email: string|null; contact_phone: string|null; address: string|null }

// ---- admin (schemas_billing.py) ----
type PlanInput = { name: string; description: string; price_paise: number /* multiple of 100 */; rank: number;
  status: "active"|"retired"; recommended: boolean; features: PlanFeatures; limits: UserLimits;
  chat_model_ids: number[]|null /* null: every model */; default_chat_model_id: number|null }
type PlanCostEstimate = { kept_paise: number /* after Razorpay's fee */; worst_case_paise: number;
  worst_share: number /* of kept, 0..n */; typical_paise: number; typical_share: number;
  unlimited: string[] /* limit names set to 0: no ceiling */ }
type AdminPlan = PlanInput & { id: number; code: string; current_users: number; scheduled_periods: number;
  complimentary_users: number /* open-ended */; used: boolean /* can't be deleted */; cost: PlanCostEstimate;
  created_at: string; updated_at: string }
type PlanCreate = PlanInput & { code: string }
type PlanUpdate = Partial<PlanInput> & { apply_to_current?: boolean /* default false */;
  confirm_reduction?: boolean /* required when apply_to_current lowers paid periods, 3.5 */ }
type PlanSaved = { plan: AdminPlan; applied_periods: number }
type CostInputs = { usd_inr: number; standard_usd_per_mtok: number /* 3.20 */; vision_usd_per_mtok: number;
  image_usd: number; fee_bp: number /* 236 */ }           // the editor computes PlanCostEstimate from these, as the server does
type BillingSettings = { business_name: string; address: string; contact_email: string|null; contact_phone: string|null;
  receipt_prefix: string; upi_limit_paise: number }
type PaymentsSetup = { configured: boolean; mode: "test"|"live"|null; key_id_hint: string|null;
  webhook_secret_set: boolean; previous_webhook_secret_set: boolean; last_webhook_at: string|null;
  webhook_url: string /* to register */; webhook_events: string[]; test_links_created: number;
  reconciler_last_run_at: string|null; last_error: string|null /* e.g. keys rejected */;
  paused_orders: number /* of the other mode */; testers: number; email_reminders: boolean }
type BillingAdminConfig = { settings: BillingSettings; payments: PaymentsSetup; estimate: CostInputs }
type UserRef = { id: number|null /* null: deleted */; username: string }
type AdminOrder = OrderView & { id: number; user: UserRef; mode: "test"|"live"|"offline";
  razorpay_link_id: string|null; razorpay_order_id: string|null; review_reason: string|null;
  review_note: string|null; checks: number; last_checked_at: string|null; next_check_at: string|null;
  last_error: string|null; paused: boolean }
type RefundView = { id: number; amount_paise: number; status: "creating"|"pending"|"processed"|"failed";
  initiated_by: "admin"|"razorpay"; admin_username: string|null; reason: string|null; end_access: boolean;
  created_at: string; processed_at: string|null; last_error: string|null }
type DisputeStatus = "open"|"under_review"|"action_required"|"won"|"lost"|"closed"
type DisputeView = { id: number; razorpay_dispute_id: string; amount_paise: number; status: DisputeStatus;
  reason_code: string|null; respond_by: string|null; created_at: string; updated_at: string }
type AdminPayment = PaymentView & { user: UserRef; order_public_id: string|null; razorpay_payment_id: string|null;
  fee_paise: number|null; fee_tax_paise: number|null; refundable_paise: number; credit_used_paise: number;
  refunds: RefundView[]; disputes: DisputeView[]; offline_reference: string|null; mode: "test"|"live"|"offline" }
type RefundRequest = { amount_paise: number|null /* null: all that's refundable */; reason: string /* 1..200 */; end_access: boolean }
type OfflinePaymentRequest = { user_id: number; plan_id: number; amount_paise: number /* ≥100 */; received_on: string;
  reference: string /* 1..64 */; note: string|null /* ≤500; required below the plan's price */; apply: boolean /* false: receipt only */ }
type GrantRequest = { plan_id: number; days: number|null /* 1..90; null: open-ended */; reason: string /* 1..200 */;
  start: "now"|"after_current" /* ignored when open-ended */ }
type ChangePlanRequest = { plan_id: number; reason: string /* 1..200 */ }
type RevokeRequest = { reason: string /* 1..200 */ }
type EndComplimentaryRequest = { reason: string /* 1..200 */ }
type ResolveRequest = { note: string /* 1..500 */ }
type AttentionKind = "review"|"duplicate"|"refund_failed"|"refund_pending"|"partial_refund"|"dispute"|
  "dispute_lost"|"credit_reversed"|"webhook_errors"|"payments_not_configured"|"test_mode"|"test_keys_public"|
  "live_without_webhook"|"keys_rejected"|"paused_orders"
type AttentionItem = { kind: AttentionKind; message: string; detail: string|null; order_public_id: string|null;
  payment_id: number|null; count: number|null }
type UserPlanRow = { user: UserRef; plan: string|null; date: string /* IST: valid through, or ended */;
  last_payment_at: string|null }
type BillingSummary = { month: string /* YYYY-MM, IST */; gross_paise: number; fee_paise: number;
  refunded_paise: number; disputed_paise: number; net_paise: number /* gross − fees − refunds − disputes */;
  payments: number; refunds: number;
  by_plan: { plan: string; payments: number; gross_paise: number }[];
  current_users: { plan: string; paid: number; complimentary: number }[]; users_without_plan: number;
  months: { month: string; gross_paise: number; net_paise: number }[] /* the 12 months up to this one */;
  expiring: UserPlanRow[] /* valid through within 7 days, nothing after */;
  lapsed: UserPlanRow[] /* ended within the last 30 days */; attention: AttentionItem[] }
type UserBilling = { plan: PlanAccess; periods: PeriodView[] /* every one, newest first, at most 100 */;
  complimentary: ComplimentaryView|null; orders: AdminOrder[]; payments: AdminPayment[]; profile: BillingProfile;
  events: { at: string; source: string; type: string; actor: string|null; detail: Record<string, unknown> }[] /* newest 50 */ }

// ---- changed admin user shapes (schemas_admin.py) ----
type LimitOverrides = { [K in keyof UserLimits]: number|null }     // null: the plan's value
type AdminUserPlan = { state: PlanState; plan: PlanRef|null; valid_through: string|null; paid_through: string|null;
  complimentary: boolean; open_ended: boolean; test_mode: boolean; expiring_soon: boolean; ended_at: string|null }
type AdminUser = { id; username; is_admin; is_active; restrict_models; allowed_model_ids; created_at; last_login_at;
  document_count; storage_bytes;                                    // as today
  usage: { chat_tokens: number; chat_allowance_used: number /* new */; embedding_tokens: number;
    vision_tokens: number; images: number; cost_usd: number; window_start: string; window_end: string /* new */ };
  billing_tester: boolean; limits: UserLimits /* effective */; overrides: LimitOverrides;
  above_plan: (keyof UserLimits)[] /* overrides more generous than the plan's value */;
  no_models: boolean /* the allowed list and the plan don't overlap */; plan: AdminUserPlan }
  // replaces the five limit ints and monthly_image_limit
type AdminUserCreate = { username; password; is_active; restrict_models; allowed_model_ids;
  billing_tester?: boolean; overrides?: Partial<LimitOverrides>; grant?: Omit<GrantRequest, "start"> /* starts now */;
  confirm_unlimited?: boolean }
type AdminUserUpdate = { /* as today without the limit fields */ billing_tester?: boolean;
  overrides?: Partial<LimitOverrides> /* a key present sets it; null clears it; absent keys stay */;
  confirm_unlimited?: boolean /* required when an override sets monthly_token_limit to 0 (422 otherwise) */ }

// ---- plugins (schemas_plugins.py) ----
type PluginInfo = { /* as today */ in_plan: boolean /* the caller's plan includes it (true for the admin) */ }
```

`SystemCheck.id` (`schemas_system.CheckId`) gains `"billing"` (label "Payments").

### 7.2 New endpoints

User (`routers/billing.py`, prefix `/billing`; session required unless noted; lapsed users allowed):

| Method and path | Request → response | Errors |
|---|---|---|
| `GET /billing/plans` | → `PlanCatalog`: `active` plans plus the plan of the caller's current or latest period and open-ended plan (even when retired), by rank; `current` marks the plan that applies now; `options` per 4.3 (empty for the admin) | 401 |
| `GET /billing/status` | → `BillingStatus` | 401 |
| `POST /billing/checkout` | `CheckoutRequest` → 201 `CheckoutStarted` (6.3) | 401, 403 `admin_exempt`, 404 unknown plan, 409 `checkout_unavailable`, 422, 429 `too_many_checkouts`, 502, 503 |
| `GET /billing/orders/{public_id}` | `?refresh=true` checks with Razorpay first (when the lock is free) → `OrderView` | 401, 404 (not the caller's) |
| `POST /billing/orders/{public_id}/cancel` | → `OrderView` (`cancelled`; the link is cancelled best effort; a payment that still arrives is applied) | 401, 404, 409 when not `pending` |
| `GET /billing/payments` | → `PaymentView[]`, captured, refunded and disputed ones, newest first, at most 200 | 401 |
| `GET /billing/receipts/{id}` | → `application/pdf` (9) | 401, 404 |
| `GET /billing/profile` | → `BillingProfile` | 401 |
| `PUT /billing/profile` | `BillingProfile` → `BillingProfile` (blank strings become null; email `^[^@\s]+@[^@\s]+\.[^@\s]+$`) | 401, 422 |
| `GET /billing/razorpay/return` | **no session**; the signed query → 303 (6.4) | never an error page: always a 303 (nginx may answer 429) |
| `POST /billing/razorpay/webhook` | **no session**; raw body + `X-Razorpay-Signature` → 200 `{ok: true}` (6.4) | 400 bad signature, 413, 500 (Razorpay retries), 503 not configured |
| `GET /auth/upload-allowed` (in `routers/auth.py`) | → 204 when the session may upload now | 401, 403 (nginx turns it into 402) |

Public (`routers/public.py`, prefix `/public`, no session, `Cache-Control: public, max-age=60`):

| Method and path | Response |
|---|---|
| `GET /public/site` | `PublicSite` |
| `GET /public/plans` | `PublicPlan[]`: `active` plans by rank |

Admin (`routers/admin_billing.py`; admin only, 403 otherwise; every write takes the user lock where a user is
involved and writes a `billing_events` row with the admin as `actor`):

| Method and path | Request → response |
|---|---|
| `GET /admin/plans` | → `AdminPlan[]`, every status, by rank |
| `POST /admin/plans` | `PlanCreate` → 201 `PlanSaved` (409 duplicate code or rank; 422 rank/price order, price not whole rupees, unknown plugin or model, a recommended plan that is retired) |
| `PATCH /admin/plans/{id}` | `PlanUpdate` → `PlanSaved` (3.5; 409 `confirmation_required`) |
| `DELETE /admin/plans/{id}` | → 204 (409 when used) |
| `GET /admin/billing/config` / `PUT /admin/billing/settings` | → `BillingAdminConfig` / `BillingSettings` → `BillingAdminConfig` |
| `GET /admin/billing/summary?month=YYYY-MM` | → `BillingSummary` (IST month; default the current one) |
| `GET /admin/billing/export.csv?month=YYYY-MM` | → `text/csv; charset=utf-8`, `Content-Disposition: attachment; filename="payments-2026-10.csv"`: one row per payment and per processed refund or lost dispute in the IST month: date (IST), type, receipt number, user, plan, kind, amount (₹, two decimals), method, Razorpay payment id or bank reference, fee, fee GST, refunded. Cells starting with `=`, `+`, `-` or `@` are prefixed with `'` |
| `GET /admin/billing/orders?status=&user_id=&page=` | → `{items: AdminOrder[], total: number}`, newest first, 50 per page |
| `GET /admin/billing/orders/{public_id}` | → `AdminOrder & {events: {at, source, type, detail, error}[]}` |
| `POST /admin/billing/orders/{public_id}/check` | → `AdminOrder` (an immediate reconcile of that order) |
| `POST /admin/billing/orders/{public_id}/resolve` | `ResolveRequest` → `AdminOrder` (a `review` order) |
| `GET /admin/billing/payments?month=&user_id=&status=&page=` | → `{items: AdminPayment[], total}` |
| `POST /admin/billing/payments/{id}/refund` | `RefundRequest` → `AdminPayment` (6.7) |
| `POST /admin/billing/offline-payments` | `OfflinePaymentRequest` → 201 `AdminPayment` (4.9; 422 below ₹1 or without the note below the price) |
| `GET /admin/billing/users/{user_id}` | → `UserBilling` |
| `POST /admin/billing/users/{user_id}/grants` | `GrantRequest` → `UserBilling` (409 `start: now` with a current period; 404 the admin or unknown) |
| `POST /admin/billing/users/{user_id}/end-complimentary` | `EndComplimentaryRequest` → `UserBilling` (409 without an open-ended plan) |
| `POST /admin/billing/users/{user_id}/change-plan` | `ChangePlanRequest` → `UserBilling` (409 without an unexpired period) |
| `POST /admin/billing/periods/{id}/revoke` | `RevokeRequest` → `UserBilling` |
| `POST /admin/billing/reconcile` | → `{checked: number}` (every due order of the current mode now; also clears a keys-rejected stop) |
| `GET /admin/billing/receipts/{id}` | → `application/pdf`, any user's |

### 7.3 Changed endpoints

- `GET /auth/me`, `POST /auth/login`: `Me.plan`, `Me.limits.vision_monthly_token_limit`, `Me.usage.chat_allowance_used`
  and `window_*` (5.9).
- `GET /models`: 402 without access; the entitled models only; ordering and the default as 5.5; `allowance_rate`.
  `can_create_files` and `can_create_images` stay server capabilities (the UI combines them with the plan).
- `POST /chat/stream`: 402 before any stream; tools and notes by plan (5.5); 429 bodies gain `code` and `feature`.
- `GET /plugins`: `in_plan`. `POST /plugins/{id}/connect` and the Gmail routes: 402 (5.6).
- Conversations and documents: as 5.4 (the lists, downloads and deletes open to lapsed users); upload and
  generated-file limits from the plan (5.7); reprocess without the size check.
- `GET /me/usage`: the window, the allowance and its limit from the plan (`MyUsage`). `POST /me/export`:
  `billing.json`.
- `GET /admin/users`, `POST /admin/users`, `PATCH /admin/users/{id}`: the shapes of 7.1; every change of an
  override, the allowed list, `restrict_models` or `billing_tester` writes a `user.updated` billing event with the
  values before and after (B45); an override of 0 for `monthly_token_limit` needs `confirm_unlimited: true` (422
  "Confirm an unlimited chat allowance."). `DELETE /admin/users/{id}`: calls `billing.on_user_deleted` first (4.12).
- `GET /admin/system`: the `billing` check (11.3).

## 8. UI (functional)

Looks follow the visual brief (cards, buttons, notices, tables, dialogs, light and dark, the five accents). New CSS
prefixes: `plans-` (plans, checkout and return pages, and the plan card), `billing-` (Settings tab, banners, locked
hints, "Your data"), `public-` (public pages); admin additions keep `admin-`. Money is shown with `formatInr`
(`Intl.NumberFormat("en-IN", {style: "currency", currency: "INR"})`, without paise when they are zero: "₹2,000").
**Billing dates are IST dates** (`formatIstDate`, B34): "25 Nov 2026"; ranges "26 Oct to 25 Nov 2026". No `alert`,
`confirm` or `prompt` (CLAUDE.md).

### 8.1 Routes and gating

| Route | Who | Notes |
|---|---|---|
| `/plans` | signed in | the plan page; Settings opens over it (`?settings=`) |
| `/plans/checkout/:code?kind=period\|now` | signed in | the review before paying |
| `/billing/return?order=<public_id>` | signed in | after Razorpay; a deep link survives sign-in (`loginRedirect`) |
| `/pricing`, `/terms`, `/privacy`, `/refunds`, `/contact`, `/about`, `/shipping` | everyone | public layout (10); signed-in users see them too |
| `/`, `/c/:id` | signed in **with access** | without access → `<Navigate to="/plans" replace>` **when the route is entered** |
| `/admin` | the admin | unchanged |

- `RequireAuth` gains `needsPlan` (on `/` and `/c/:id`), evaluated when the route is entered (a navigation or a page
  load), **not** when `Me` changes on a page already shown (B32, 8.7). The admin always passes.
- `api/client.ts`: `ApiError` gains `code`, `feature` and `plan` from the error body. A 402 whose code is
  `plan_required` or `plan_expired` calls the plan handler (`setPlanRequiredHandler`, like the 401 handler): one
  shared `refresh()` of `Me`. `feature_not_in_plan` doesn't refresh (the caller shows it).
- After `paid_through` passes in an open tab, a timer (at most 24 days ahead, re-armed on each `Me`) refreshes `Me`.

### 8.2 The plan page (`/plans`)

- **Header**: the brand, "Back to chat" when the user has access, the avatar menu (Settings, Log out; Admin for the
  admin).
- **Intro by state**:
  - `none`: "Choose a plan" / "Your account is ready. Pick a plan to start chatting with your documents.";
  - `expired`: "Your <plan> plan has ended" / "It ended on <date>. Your chats and documents are kept: renew or choose
    another plan to pick up where you left off.";
  - `active` by a period: "Your plan: <plan>" / "Valid through <date>. Upgrade now with credit for what's left, or
    switch plans when this one ends." ("…, paid ahead" when stacked; "From <date>: <next plan>." when `next_plan`);
  - `active` by an open-ended plan: "Your plan: <plan>" / "Complimentary, with no end date. You can still choose a
    paid plan; your complimentary plan continues after it.";
  - `exempt`: "Plans" / "You're the admin, so plans don't apply to you. This is what users see." (buttons disabled).
  - Badges: "Ends in N days" (expiring soon), "Complimentary", "Test mode".
- **Notices** (above the cards, in this order): admin preview; payments unavailable ("Online payment isn't set up
  yet." plus the contact email; the admin gets "Set up payments in Admin → Billing"); test mode (testers only, 6.9);
  an open checkout ("A payment for <plan> is in progress." with **Check status** → the return page, **Resume payment**
  → `pay_url` while it lives, **Cancel**); with `?payment=unknown` (6.4): "We couldn't match that return from
  Razorpay. If you paid, it will be confirmed automatically within a few minutes."
- **Cards**, one per plan in rank order (a list), each with: name, the flag (8.2's emphasis below), description;
  price "₹2,000 / month" with "30 days, paid in advance"; "Included" features in words (Chat with your documents,
  Files from chat, Images from chat, Gmail plugin; missing ones "Not included"; a plugin with `available: false`
  "Gmail (not available yet)"); limits: **chat as answers** ("About 1,600 answers a month with Claude Haiku 5.5",
  detail "500,000 standard tokens; other models count more or less"; "Unlimited" for 0), documents, storage, file
  size, images (only with images), models ("All models", or the names); actions from `options`.
- **Emphasis**: one card: the current plan (`active`), else the last plan when it can be renewed (`expired`), else
  the `recommended` plan. At most one primary button on the page: that card's main action.
- **Actions**: the main option as a button (labels of 4.3; amounts on `now` options), the alternative as a link;
  unavailable options disabled with the reason as visible text. Choosing goes to the checkout page.
- **Compare plans** under the cards: features; one row per enabled chat model with "about N answers" per plan (from
  `PlanModel.answers`) or "Not included"; "New chats start with"; chat allowance, images, scanned pages, requests a
  minute, documents, storage, largest file.
- **How payment works** strip and the footer with `PolicyLinks` and "Payments are processed by Razorpay."
- **States**: skeleton cards while loading; an error with Try again; no plans: "No plans are available yet." with the
  contact email (the admin: "Create plans in Admin → Plans").

### 8.3 Checkout (`/plans/checkout/:code?kind=`)

- Loads the catalog, finds the plan and the option of that kind; missing or unavailable → the reason and "Back to
  plans". The steps "Choose a plan · Review and pay · Confirmation".
- **Your plan**: plan, short feature list, dates ("30 days: valid 26 Oct to 25 Nov 2026" or "Starts 26 Nov 2026,
  when your current plan ends"), `notes`, and for `now` options the credit ("Professional for 30 days from today:
  ₹5,000 − ₹300 credit for unused Starter = ₹4,700").
- **What changes on <date>** (only when `changes` is non-empty): a warning box listing them (4.5).
- **Summary**: the plan line, the credit line, the total, and **Pay ₹<amount>**. Under it the consent line "By paying
  you agree to the Terms of service and the Cancellation and refunds policy." (links open the public pages in a new
  tab), the secure-payment line and the methods as text. When `upi_limit_paise > 0` and the amount is above it: "UPI
  is limited to ₹2,000 per payment for now. Pay ₹5,000 by netbanking, debit card or credit card on the next page."
- **Receipt details (optional)**: a collapsed block (prefilled from the profile): "Name on the receipt" and, only
  when `email_reminders`, "Email for reminders" ("Used only to remind you before your plan ends."). Saved with
  `PUT /billing/profile` when changed, before the checkout.
- **Pay**: `POST /billing/checkout` with `accept_terms: true`; the `pay_url` must be `https:` on `rzp.io` or a
  `razorpay.com` subdomain (in test mode also the app's own origin, for the e2e mocks), else the page shows the error
  with **Check status**. Then the "Opening Razorpay…" interstitial (with "Continue to Razorpay" after 3 s) and
  `window.location.assign(pay_url)`. Errors inline under the button by `code` (409: the reason and "Back to plans";
  429; 502: "Try again"; 503).
- Phones: a sticky pay bar with the total and the button. The test notice as on the plan page (testers only).
- **Renew** from a banner or Settings opens this page directly for the `period` option of the current plan.

### 8.4 The return page (`/billing/return?order=…`)

- Polls `GET /billing/orders/<id>?refresh=true` at once, then every 3 s for 60 s, then every 15 s up to 10 minutes,
  then stops with **Check again**. Stops on `paid`, `expired`, `cancelled` or `review`. Status changes are announced
  (`aria-live="polite"`), and focus moves to the result heading on a final state.
- **pending**: "Confirming your payment…". After 60 s: "Still waiting for Razorpay" / "If you closed Razorpay's page
  before paying, you can go back to it. We keep checking either way." **Resume payment** (when `pay_url`), **Choose
  another plan**, **Cancel this payment**. After 10 minutes: "No answer from Razorpay yet" / "If you paid, your plan
  will start automatically within a few minutes. You can close this page." **Check again**, **Back to plans**. With
  `check=failed`: "We couldn't verify the return from Razorpay. If you paid, it will be confirmed automatically
  within a few minutes."
- **paid**: "Payment received" / "You're on <plan> until <valid through>." (or "Your <plan> plan continues from
  <date> to <date>." for a stacked period), the slip (plan, period, paid with, receipt number, total), **Start
  chatting** (`/`, after `refresh()` of `Me`), **Download receipt**. When `email_reminders` and the profile has no
  email: a one-line "Remind me before my plan ends" email field with **Save** (shown once; "Not now" hides it).
- **expired** or **cancelled**: "Payment not completed" / "No payment was received for this checkout, so your plan
  hasn't changed." plus `last_payment_error` when set, **Try again** (the same checkout page), **Back to plans**, and
  "If money left your account, it will be matched automatically within a few days, or Razorpay returns it to you."
- **review**: "We're checking your payment" / "We received a payment and are checking it. Your plan starts as soon as
  it's confirmed. Please don't pay again." **Back to plans**.
- **404**: "This payment isn't in your account." **Back to plans**.

### 8.5 Settings → Plan & billing (`?settings=billing`)

A new tab between Account and Usage (`SettingsTab` gains `"billing"`; `/settings/billing` redirects there).

- **Current plan**: state, plan, badges (Complimentary, Test mode), "valid 26 Oct to 25 Nov 2026", days left, a bar
  of the period, "Paid through <date>" when stacked, "Then: <next plan>", the comp reason, "No end date" for an
  open-ended plan, the expiring note. Actions: **Change plan** (→ `/plans`) and **Renew** (→ the checkout). Lapsed:
  "No active plan" + **Choose a plan**. Admin: "Plans don't apply to the admin."
- **Open checkout**: as the plan page's notice.
- **Coming up**: the scheduled periods (plan, dates, Paid or Complimentary).
- **Receipt details**: name, and the email only when `email_reminders`; a line stating the reminder channel ("Plan
  reminders: in the app and by email." / "Plan reminders: in the app. Add an email to get them by email too." / "Plan
  reminders: in the app.").
- **Payment history**: date, description with the period and method, amount, status ("Paid", "Refunded", "Refunded
  ₹500", "Disputed"), receipt number with a download link (`/api/billing/receipts/<id>`). Empty: "No payments yet."
- Loading, empty and error states like the other tabs (`useLoad`).

### 8.6 Locked features and the way to upgrade

The server is the gate; the UI shows what the plan allows, from `Me.plan.features` and `unlock`. "Upgrade" always goes
to `/plans`.

- **"+" menu**: a feature the server could offer but the plan doesn't include stays visible as **one** locked row
  with a lock and "Included in <unlock>": "Create an image", "Create a document" (no format chips while locked),
  and under Gmail one row "Search, draft and import email". Activating a locked row goes to `/plans` (an action, not
  disabled). Items hidden today (the feature doesn't exist on the server) stay hidden. The empty chat's examples
  never show locked features.
- **Model menu**: the "Files" and "Images" tags show only when the model can (`can_create_*`) **and** the plan
  includes it; a model whose `allowance_rate` is not 1 shows it as a tag ("×2", "×0.05"); when `unlock.models` is
  set, a last item "More models with <plan>".
- **Plugins menu and Settings → Plugins & data**: the status order becomes Not set up → Turned off by the admin →
  **Not in your plan** (`in_plan` false; action **Upgrade**; the card keeps **Disconnect**) → Reconnect needed →
  Connected → Not connected.
- **Avatar menu**: the plan line replaces the role line for users ("Starter · until 31 Oct", "Complimentary
  Professional", "No active plan"; the admin keeps "Administrator"); meters "Chat allowance this period" and "Images
  this period" (with images) for users with access; an item **Plan & billing** with a "5 days left" badge while
  expiring soon, or "No plan" when lapsed.
- **Chat errors**: a 402 `feature_not_in_plan` shows its `detail` with **See plans**; a 429 `allowance_used` shows its
  `detail` with **Start a new period now** (when `can_start_new_period`; the checkout of the current plan, kind
  `now`) and **Upgrade** (when a higher plan exists). Upload and import refusals show the server's message.
- **Documents panel**: over a lowered limit, a notice ("You have 62 documents; your plan allows 50. Delete some or
  upgrade to add more.") with **See plans**; the quota footer shows the plan's limits.

### 8.7 Banners, and a plan that ends while a page is open

Banners under the top bar, one at a time, in this order:

1. an open checkout: "A payment for <plan> is in progress." **Check status**;
2. expiring soon: "Your <plan> plan ends in <n> days, on <date>." ("ends tomorrow", "ends today"; "Your
   complimentary <plan> plan…") **Renew** (→ the checkout) / **Choose a plan** (retired plan). Dismissible for the
   day (`localStorage["rag.planBanner"]` holds the period end and the date dismissed; a storage error just shows it
   again).

**Plan ended in an open chat** (B32): when `Me.plan.access` turns false on the chat page (the timer, or a 402), the
page stays. The composer is replaced by an inline panel: "Your <plan> plan ended on <date>. Renew to continue. Your
chats and documents are kept." with **Renew ₹<price>** (the checkout of the last plan when it has a `period` option)
and **See plans**. Focus moves to the panel's heading, which is announced. The unsent text is kept in
`sessionStorage["rag.planDraft"]` (`{conversation_id, text}`) and put back into the composer the next time that chat
(or a new chat) opens with access; uploads already finished stay in My documents. The documents panel stops polling.
The next navigation or page load goes to `/plans` (8.1). An answer that is streaming finishes first.

### 8.8 "Your data" for lapsed users (Settings → Plugins & data)

Only when the user has no access (not for the admin): a section above the plugins with

- **Your documents**: name, size, date and source (Uploaded, Generated file, Image, From Gmail), **Download** and
  **Delete** (inline confirmation), from `GET /documents` (own documents only for lapsed users, 5.4) and the
  download URLs;
- **Your chats**: title and date, **Delete** (inline confirmation), from `GET /conversations`;
- the note "Everything comes back when you choose a plan. To keep a copy of your chats, use Export your data below."

Empty and error states as the other lists.

### 8.9 From "this month" to "this period" (PRD-8)

Allowances run per period (B5), so these strings and calculations change. For a user with access the label is "this
period" (an open-ended plan's period is the calendar month); the admin's stays "this month".

| File | Today | Becomes |
|---|---|---|
| `components/chat/PlusMenu.tsx` | `nextUsageReset(user.usage.month)`; "You've used this month's 30 images. The count starts again on 1 Dec."; "18 of 30 left this month" | the window's end (`usage.window_end`) as an IST date; "You've used this period's 30 images. The count starts again with your next period on 26 Nov." / "…Your plan is valid through 25 Nov." when nothing follows; "18 of 30 left this period" |
| `components/chat/AvatarMenu.tsx` | "Tokens this month", "Images this month" | "Chat allowance this period" (`chat_allowance_used` of `monthly_token_limit`), "Images this period" |
| `pages/ChatPage.tsx`, `components/chat/EmptyState.tsx` | "left this month" | "left this period" |
| `components/settings/UsageTab.tsx`, `DailyUsageChart.tsx` | "No usage yet this month", the month heading, "resets on" | "No usage yet this period", "This period: 26 Oct to 25 Nov 2026", "Your allowances start again on …" / "…end with your plan on …"; the chat meter is the allowance (`MyUsage.chat_allowance`) with "Counted at each model's rate." |
| `lib/format.ts` | `nextUsageReset` | removed once unused |
| `components/admin/UsersTab.tsx` (ADMIN) | "Tokens this month", "Images this month" | "This period": chat allowance used of the effective limit, images of the limit; Admin → Usage keeps calendar months |

### 8.10 Admin → Plans (new tab `#plans`)

Tab order: Users, Plans, Billing, Models, Usage, Documents, Plugins, System.

- A table by rank: name and code, status (Active, Retired, plus Recommended), price, users now · scheduled ·
  complimentary, features, key limits (the chat allowance as answers with the default model), the cost estimate
  ("Worst case ₹X, Y% of what you keep"; a warning style at 60% or more, "No ceiling" when a limit is unlimited),
  **Edit**, **Delete** (only unused).
- **New plan / Edit** dialog: code (create only), name, description, price in whole rupees, rank, status, Recommended,
  features (Files, Images, one switch per registered plugin), the seven limits (0 = unlimited; the chat allowance
  with the hint "About 1,600 answers with Claude Haiku 5.5"), models ("All chat models" or a checklist with a
  default), and the live cost estimate computed in the browser by `planCost.ts` from `BillingAdminConfig.estimate`
  with the same formula as the server's `PlanCostEstimate`. On save, when the plan has current or scheduled periods,
  the choice "Apply to new periods only" (default) or "Also apply to the <n> current and scheduled periods"; a 409
  `confirmation_required` shows its text inline with **Apply anyway**, which resends with `confirm_reduction: true`.
- States: loading, error, empty ("No plans. Users can't pay until you create one.").

### 8.11 Admin → Billing (new tab `#billing`)

Sub-sections in a segmented control, remembered in the hash (`#billing/overview` and so on):

- **Overview**: the month picker (IST months); notices (test mode with the test links count, payments not set up);
  **Needs attention** (`attention` items, each with its detail line and an action linking to the order, payment or
  setup); tiles: Gross, Razorpay fees, Refunds, Disputes lost, **Net**; payments by plan; users now by plan (paid,
  complimentary) and without a plan; a 12-month bar list (CSS, no chart library); **Expiring in 7 days** and **Lapsed
  in the last 30 days** (user, plan, date, last payment; each row opens the user); recent payments; **Export CSV**
  (`/api/admin/billing/export.csv?month=`).
- **Payments**: a paged table (date, user, plan, kind, amount, method, status, refunded, receipt); a row opens the
  payment with its refunds and disputes and **Refund** (amount prefilled with the refundable amount and "₹300 of this
  payment went as credit to an upgrade" when so, reason, "End access", "The refund goes back to the payer's account
  in 5 to 7 working days.") and **Download receipt**. **Record bank transfer** opens 4.9's form.
- **Orders**: a paged table filtered by status (pending, review, expired, cancelled, paid; the review filter and its
  count list the orders still to review, resolved ones stay under All), paused orders marked; a row opens the order
  with its events, **Check now**, and for `review` **Mark resolved** and **Grant instead**.
- **Business & payments**: the billing settings form (business name, address, contact email and phone, receipt prefix
  with a live example, UPI limit); the payments setup (mode, key id hint, webhook secrets set, last webhook, the
  webhook URL and the 14 events to subscribe, test links used of about 30, testers, the reconciler's last run and
  error, paused orders, reminder emails on or off, **Check pending payments now**); the **going-live checklist**:
  business details filled; no draft policy pages (the browser checks `/legal/*.md` for the draft marker); live keys
  in `.env`; webhook secret set and a webhook received; and three instructions to confirm by hand: "Confirm the UPI
  limit with Razorpay and set it above", "Keep Customer Fee Bearer and Offers off in Razorpay's Dashboard", "Refund
  from here, not from Razorpay's Dashboard, so access follows the refund"; reminder emails (optional).

### 8.12 Admin → Users

- The list gains a **Plan** column ("Standard · until 25 Nov", "Complimentary Professional", "Ended 25 Nov", "No
  plan", the admin "Exempt") and filter chips: All, Active, Expiring, Lapsed, Complimentary, No plan (in the browser);
  the usage columns are "this period" (8.9).
- The user form: a **Plan** section (from `UserBilling`): state, periods (with **Revoke…** per row), the open-ended plan
  with **End…**, **Grant a complimentary plan…** (plan, days or "No end date", reason, start), **Change plan…** (no
  charge), **View payments** (→ Billing → Payments filtered), recent billing events. A switch **Can make test
  payments** (`billing_tester`).
- **Limits** become overrides: each field shows the plan's value as its placeholder and is blank when not overridden,
  with a "Custom" badge and **Reset to plan**; an override in `above_plan` also shows "Above plan". Setting the chat
  allowance to 0 asks inline ("Unlimited chat allowance: this user has no cost ceiling.") and sends
  `confirm_unlimited: true`. The allowed-models list narrows the plan's models; `no_models` shows "No model left: the
  user's list and the plan's models don't overlap."
- Creating a user offers a grant (dated or open-ended), pre-selected as "Professional, 30 days" with 4.8's note while
  regular users can't pay.
- Delete confirmation: "<user> has a paid plan until <date>. No refund is made automatically." when so.

### 8.13 States and accessibility

- Every page and tab has loading, empty and error states with a retry, as the existing ones.
- Plan cards are a list with headings; prices read in full ("2,000 rupees a month, for 30 days"); included / not
  included in text; buttons name the plan ("Choose Standard", "Upgrade now to Professional · ₹4,700").
- The return page's status is a live region; focus moves to its heading on a final state. The plan-ended panel's
  heading takes focus and is announced.
- Phone widths: cards compact, tables scroll inside their own box, the checkout's pay bar; no page scrolls sideways
  (the existing e2e check).

## 9. Receipts

- **When**: one per applied payment (6.5 step 6; offline payments too, including `apply: false`), in the same
  transaction. Never for comps or failed payments.
- **Number**: `<prefix>/<yy>-<yy+1>/<seq:06d>`, for example `RA/26-27/000001`; test mode `TEST/26-27/000001`. The
  financial year runs 1 April to 31 March in IST, decided by the moment the payment is applied (2027-03-31 18:30 UTC
  is already FY 2027-28). `seq` comes from `app_state` `receipt_seq:<mode>:<fy>`: bootstrap creates the rows for the
  current and the next financial year in both modes; the apply reads the row `FOR UPDATE` (last in the lock order,
  B42) and increments it; a missing row is inserted inside a savepoint (`begin_nested`), and a racing insert re-reads.
  Live numbers have no gaps and no duplicates. Live and offline payments share the live series. At most 16 characters
  with a prefix of up to 4.
- **Snapshot** (the row, never changed; refunds and disputes are read when the PDF is rendered): `seller` (business
  name, address, contact email and phone, from the billing settings at that moment), `buyer` (the profile's name or
  else the username; the email when given), `lines` ("Standard plan, 30 days: 26 Oct to 25 Nov 2026"; for a `now`
  payment that line plus "Credit for unused Starter, 26 Oct to 25 Nov 2026" with a negative amount; an offline
  payment's line says "Bank transfer, ref <reference>"), the amount and the method.
- **PDF** (`billing/receipts.py`, fpdf2 in `asyncio.to_thread`, A4, Noto Sans from `app/fonts`, which has ₹; layout in
  the visual brief section 10): title "Receipt", the seller block, the number, the date (IST), "Billed to", the lines
  table, "Total paid", the method and Razorpay's payment id (or the bank reference), and under it "Refunded ₹X on
  <date>" and "Disputed ₹X, lost on <date>" lines. No GST line (B10). Test receipts carry the watermark. The footer:
  "This receipt was generated electronically and needs no signature." No logo.
- **Download**: `Content-Type: application/pdf`, `Content-Disposition: attachment;
  filename="receipt-RA-26-27-000001.pdf"` (slashes become dashes), `Cache-Control: private, no-store`,
  `X-Content-Type-Options: nosniff`, `Content-Security-Policy: sandbox`. The same PDF for the user and the admin.

## 10. Public site and policy pages

- **Routes** (8.1) render a `PublicLayout`: the business name (from `GET /public/site`), a nav (Pricing, About,
  Contact), **Sign in** (or **Open the app** when signed in), the page, and a footer with every page's link, the
  contact email, phone and address. The login page's footer links to Pricing, Terms, Privacy, Refunds and Contact.
- **Pages** are Markdown files in the repo, `frontend/public/legal/<slug>.md` for `terms`, `privacy`, `refunds`,
  `contact`, `about`, `shipping` and `pricing` (the pricing page's intro only), served as static files (6.11) and
  fetched by `PolicyPage`. The page title is the file's first `# ` heading. A file whose first line is `<!-- draft -->`
  shows the "Draft" band ("This page is a placeholder. The owner hasn't published its final text yet."). The seeded
  files carry that marker, a first paragraph "**Draft.** This page is a placeholder. Replace it with your own text
  before going live." and headings with bracketed prompts only, for example:
  - `refunds` "Cancellation and refunds": "Cancellation" / "[Say whether a plan can be cancelled and what happens to
    the remaining days]", "Refunds" / "[Say when you refund, how much, and how long it takes: Razorpay returns money
    to the payer's account in 5 to 7 working days]", "Chargebacks" / "[Say that a payment disputed with the bank ends
    the plan it paid for while the dispute is open or lost]";
  - `shipping` "Shipping and delivery": "[This is a digital service: say that access starts as soon as the payment is
    confirmed and nothing is shipped]";
  - `privacy` "Privacy policy": "[What you collect (username, chats, documents, usage, sign-in history, billing
    records), why, where it is stored, who processes it (OpenAI, Anthropic, Google for Gmail, Razorpay for payments),
    how long you keep it, including the data of users whose plan has ended, and how to ask for export or deletion]";
  - `terms` "Terms of service", `contact` "Contact us" (the page also shows the contact details from the settings),
    `about` "About us", `pricing` "Pricing" (an intro line).
  No legal text is written by the app or this brief. The owner's text replaces a file through the repo, followed by a
  frontend rebuild.
- **Pricing** (`/pricing`): the intro, the plan cards from `GET /public/plans` (the same `PlanCard`, without
  actions), the compare table, "Payments are processed by Razorpay. Prices are per 30 days, paid in advance; plans
  don't renew automatically.", and **Sign in to choose a plan**. Razorpay's reviewer reads this page.
- **Rendering**: the app's Markdown renderer (`react-markdown` with `remark-gfm`, as chat answers, without citation
  handling; raw HTML stays off). `PolicyPage` drops the marker line before rendering.
- Pages are public by design: nothing in them depends on a user.

## 11. Migration, deploy, backups and monitoring

### 11.1 Migration 0003 (`backend/migrations/versions/0003_plans_billing.py`)

In one transaction, like 0001 and 0002 (`app.db.init_db`), forward only:

1. Create `plans`, `plan_chat_models`, `billing_orders`, `billing_payments`, `billing_refunds`, `billing_disputes`,
   `billing_receipts`, `billing_events`, `plan_periods`, `user_billing_profiles` with their indexes and foreign keys
   (`plan_periods.order_id`, `billing_orders.payment_id` and `billing_orders.replaces_period_id` refer across, so the
   later ones are added after the tables exist).
2. `users`: add `monthly_image_limit`, `vision_monthly_token_limit`, `billing_tester` (server default false),
   `comp_plan_id`, `comp_reason`, `comp_granted_by`, `comp_granted_at`; make the five limit columns nullable (batch
   mode on SQLite, in place on Postgres). `usage_events`: add `allowance_tokens` (server default 0) and set it to
   `total_tokens` for existing `chat` rows.
3. Data (4.11): one `rollout.limits_cleared` event per non-admin user with their old limit values and their
   `user_image_limits` value in `detail`; then every user's limit columns to NULL; drop `user_image_limits`.
4. On Postgres only: `CREATE EXTENSION IF NOT EXISTS btree_gist` (a trusted extension, so the database owner may) and
   `ALTER TABLE plan_periods ADD CONSTRAINT ex_plan_periods_no_overlap EXCLUDE USING gist (user_id WITH =,
   tstzrange(starts_at, ends_at) WITH &&) WHERE (revoked_at IS NULL AND mode <> 'test') DEFERRABLE INITIALLY
   DEFERRED` (checked at commit, so one transaction may end a period and move the next). If the drift check reports
   it, `COMPARE_OPTIONS` gains an `include_object` that skips constraints named `ex_*`.
5. `alembic check` finds no difference against the models afterwards (`test_migrations.py`).

Seeding is in `bootstrap.py`, after `_seed_models`: `_seed_plans` (when no plan exists; `plans_seeded`), the receipt
counters (every start: the rows for the current and next financial year in both modes, when missing), and
`_rollout_plans` (`plans_rollout`, 4.11). `_backfill_image_limits` and its flag are removed.

### 11.2 Deploy, rollback and backups

- **Rehearse** on a copy of the latest live backup restored into a throwaway Compose project (as 0002 was): row
  counts kept; `bikash` gets the open-ended Professional plan; the `rollout.limits_cleared` events are read and any
  value above Professional's is reported to the owner; `alembic check` clean; the exclusion constraint present; the
  app works with no Razorpay keys (plans visible, "Online payment isn't set up yet").
- **Deploy**: a verified backup (`scripts/backup.sh`), then `docker compose up -d --build backend worker`, then the
  frontend (nginx resolves the backend at start). No keys are needed to deploy; the owner adds them later and runs
  `docker compose up -d backend worker`.
- **Rollback** = restore the pre-deploy backup (migrations only go forward; HANDOFF "Database").
- **Backups**: unchanged. The new tables are in `pg_dump` and in the manifest's row counts automatically; receipts are
  rendered from the database. The Razorpay secrets are in `.env`, which is never backed up: keep them in the password
  manager. A pre-0003 backup restores with `--migrate` (SPEC section 10), and the first start then runs the rollout
  for that data.

### 11.3 Monitoring

A new check `billing` ("Payments") in `app/ops/checks.py`, run by the monitor and shown in Admin → System (counts
only, never usernames or amounts):

| Result | When |
|---|---|
| `ok` | "Payments not set up" (no keys), or nothing below |
| `warning` | orders in `review`; a dispute needing action whose `respond_by` is within 3 days; an order of the current mode whose `next_check_at` is over 15 minutes in the past (the reconciler is stalled); the keys were rejected (polling stopped); a refund `pending` or `creating` for over 7 days, or `failed`; 20 or more bad webhook or callback signatures in the last hour (the Redis counters); a webhook event unprocessed after 3 attempts; webhooks configured and a payment captured in the last 24 hours that no webhook reported (Razorpay may have disabled the webhook); live keys without `RAZORPAY_WEBHOOK_SECRET`; test keys while `PUBLIC_BASE_URLS` holds a public `https` origin |
| `unknown` | the database can't be read |

## 12. Tests and CI

Every builder keeps the suites green with the commands in `CLAUDE.md`; no test calls the real Razorpay.

### 12.1 Backend unit and API tests (`backend/tests/`)

- **Existing tests stay green**: `conftest.create_user` gives each new user an **open-ended complimentary
  Professional plan** by default (`plan="professional"`; `plan=None` for a user without a plan), so its window is the
  calendar month as before; limits passed to it become overrides. Helpers: `grant_plan(user_id, code, days=30,
  start="now")` (a dated period), `end_plan(user_id)` (moves periods into the past and ends the open-ended plan),
  `make_tester(user_id)`, and `fake_razorpay` (a monkeypatched transport with the endpoints of 12.2). Tests that set
  limits through the admin API move to `overrides`; budget tests whose fake usage carries a cost assert allowance
  tokens (GATE lists them).
- `test_billing_money.py`: `period_end` (examples on both sides of 18:30 UTC, a start exactly at midnight IST, a
  1-day test period) and `valid_through`; the credit (time against used, unlimited allowances left out, comps at
  basis 0, whole rupees, the ₹1 minimum, the 4.4 examples); scaled comp allowances; `allowance_tokens` from cost and
  the fallback; the answers figures and the cost estimate of 3.2 and 3.3.
- `test_billing_signatures.py`: both HMACs against fixed vectors, wrong secret, the previous webhook secret, wrong
  order of fields, upper-case hex.
- `test_billing_periods.py`: stacking and the one-ahead rule; re-chaining after a revoke; dated grants split into rows
  with scaled allowances; open-ended plans and a period's precedence over them; change-plan history; test periods of
  1 day ignored under live keys; the `now` apply (the period ended, the new one, scheduled periods moved) and each
  review case (replaced period revoked, ended, given another plan, or already replaced by another `now`); **the SEC-1
  regression**: a `now` upgrade with 1 day left and an unused allowance costs at least the target price minus 1/30 of
  the basis, and a 1-day comp row carries 1/30 of each allowance.
- `test_entitlements.py`: admin; active by period; active by an open-ended plan; none; expired; overrides on each
  limit; model narrowing (a Starter user whose allowed list includes Opus gets Starter's models only; an empty
  intersection); 402 versus 403 for a model; windows; `unlock`; `can_start_new_period` at 89% and 90%.
- `test_usage_allowance.py`: `record_usage` writes `allowance_tokens`; the budget sums them in the window; the 429
  messages and codes of 5.3; `MyUsage` and `UsageSummary` fields.
- `test_billing_checkout.py`: options, labels, changes and reasons; server-side amounts and credit; testers only under
  test keys (503 otherwise); an open order reused; the new link created before the old one is cancelled, and the old
  one intact when the create fails; the caps (counted only before a create, 5 in 60 minutes, 15 per IST day); a
  timeout found by `reference_id` and cancelled; `pay_url` rejected unless Razorpay's; the admin's 403; 503 without
  keys; 502.
- `test_billing_confirm.py`: callback (good; bad signature and unknown link counted with one sample row a minute; an
  applied order fetched no more; the lock); webhook (each event type recorded and processed only after a fetch; a
  duplicate id; the same body under a new id; a stale body; the previous secret; a bad signature; unconfigured; an
  unmatched payment event re-matched later; 3 failures → attention); the reconciler's schedule (30 s, then 1 h, 6 h,
  24 h, hourly with a payment in flight) and backoff; a rejected key stops polling for all orders; orders of the other
  mode paused; late capture; mismatched amount, currency, order or mode → review; a second payment → review.
- `test_billing_apply_race.py` (`postgres`-marked): callback, webhook processing and reconciler applying one payment
  at once → one period, one receipt; two orders of one user applied at once → no overlap; a grant racing a payment →
  no overlap; a forced overlapping insert is refused by the constraint.
- `test_billing_refunds.py`: full and partial refunds with the idempotency key; a retried timeout; a refund seen
  processed by the API response, a webhook, the reconciler and a replayed body under a new event id → counted once,
  access ended once; the refundable amount without the credit a later upgrade used; a full refund of a `now` payment
  restoring the replaced period (and not when a later change sits on it); Dashboard refunds upserted under a race;
  offline refunds.
- `test_billing_disputes.py`: every status; lost → access ended once and `disputed_paise`; won and closed resolve;
  the attention item and the monitor's deadline warning.
- `test_billing_receipts.py`: numbering per mode and financial year (the IST boundary); no gaps under concurrency
  across 31 March 18:30 UTC (`postgres`-marked); PDF text by `pypdf` (seller, buyer, lines, the credit line, ₹, the
  test watermark, refund and dispute lines, no GST line); download headers; another user's receipt 404.
- `test_plan_gating.py`: every row of 5.4's table for no session, `none`, `expired`, active and admin (also added to
  `test_route_guards.py`), including lapsed users' own-document list and downloads and a shared document's 402; tools
  not offered and the notes present (the fake LLM sees the tool list); Gmail 402s, `in_plan`, the connection
  untouched; uploads, generated files and imports over a lowered limit; reprocess without the size check; the vision
  allowance for a lapsed owner; `/auth/upload-allowed`.
- `test_admin_plans.py` and `test_admin_billing.py`: plan CRUD and validation (whole rupees, one recommended plan,
  rank and price), apply-to-current and `confirm_reduction`, delete rules, cost estimates; dated and open-ended grants
  and their end, change plan, revoke, offline payments (₹1 minimum, the note below the price); the summary (IST
  months, expiring, lapsed, users without a plan, refunds and disputes); the CSV export (columns, formula cells
  prefixed); attention items; an audit event for every write, including override, allowed-list and tester changes
  in `routers/admin.py`; `confirm_unlimited`.
- `test_billing_privacy.py`: payment, refund and dispute payloads carrying `email`, `contact`, `vpa`, `card` and
  `bank` leave none of them in any billing table.
- `test_migration_0003.py`: a database at 0002 with users holding default, generous, unlimited and admin values and
  `user_image_limits` rows → every limit NULL and the old values in the events; `allowance_tokens` backfilled; the
  exclusion constraint on Postgres; `alembic check` clean; bootstrap seeding, counters and the rollout run once (an
  active user gets the open-ended Professional plan, an inactive one nothing).
- `test_deletion_completeness.py`: billing tables keyed by user are listed as kept with `user_id` null; periods and
  profiles cascade. `test_export.py`: `billing.json`. `test_ops_*`: the `billing` check's results. `test_config.py`:
  a live key with a non-default `RAZORPAY_API_BASE` refuses to start; the removed settings warn.

### 12.2 Integration (`integration/`)

A **fake Razorpay** (`integration/fake_razorpay/server.py`, a service in `compose.it.yml` like the fake Google): the
Payment Links (create, fetch, cancel, find by `reference_id`), payments, refunds and disputes endpoints with Basic auth
checked; a hosted page `/i/<link id>` with buttons Pay, Fail, Pay late (authorised now, captured on a control call)
that redirects to `callback_url` with a correctly signed query; control endpoints to capture or fail later, refund
from the "Dashboard", raise and resolve a dispute, tamper an amount, and deliver signed webhooks (duplicated, the same
body under a new id, stale, out of order, with a bad signature, with the previous secret). The stack runs with
`RAZORPAY_API_BASE` pointing at it, test keys, a webhook secret and `BILLING_RECONCILE_SECONDS=2`; the paying test
users are testers. `integration/tests/test_17_billing.py`:

1. a tester pays (callback) → active, a `TEST/<yy>-<yy>/…` receipt, chat works; a non-tester gets 503;
2. fail, then pay on the same link → one payment applied, the failure recorded;
3. abandon → `expired` once the link expires; a late capture inside the window → applied;
4. callback and webhooks at once (three copies, a replayed body under a new id, `payment.failed` after
   `payment.captured`) → one period;
5. tampered amount → `review`; bad callback signature → nothing; bad webhook signature → 400;
6. admin refund full → access ends (402), partial → no change; a Dashboard refund via webhook; a refund reported by
   the API and a webhook → counted once;
7. a dispute raised → attention item; lost → access ends;
8. renewal and the one-ahead cap; Starter, then right away a `now` upgrade to Standard: about ₹1,501 (the credit is
   just under ₹500);
9. expiry (periods moved into the past with `harness.sql`) → 402 everywhere of 5.4, own documents still listed and
   downloadable → renew → access, data intact;
10. no webhooks at all (secret unset) → activation by the reconciler alone;
11. a Gmail-connected user moved to Standard by "change plan" → Gmail 402, no tools, connection kept; moved back →
    works without reconnecting;
12. the monitor's `billing` warnings for a `review` order and a dispute near its deadline, delivered to the fake
    alert receiver.

The backup-restore drill already counts every table; it gains billing rows in its seed data.

### 12.3 Frontend (Vitest) and end-to-end (Playwright)

- **Vitest** next to the code: `ApiError` codes and the plan handler; `RequireAuth` redirecting on entry but not when
  `Me` changes; `lib/billing.ts` (money, IST dates, `isRazorpayUrl`); the plans page per state and option (emphasis,
  one primary button, answers); checkout (credit line, changes box, UPI note, consent line, collapsed details, errors
  by code, the interstitial, `pay_url` check); the return page's polling and every state (fake timers) and the
  reminder email ask; Settings → Plan & billing; "Your data"; the plan-ended panel and the kept draft; locked items in
  the "+", model and plugins menus and rate tags; banners and their dismissal; 8.9's strings; the admin Plans editor
  (validation, `planCost.ts` against the server's figures, apply-to-current and the reduction confirmation), Billing
  sub-sections (attention, expiring and lapsed lists, refund dialog with credit, checklist), the user form's overrides
  (above plan, unlimited confirmation), grants and tester switch; public pages with the draft marker.
- **e2e** (`e2e/`): the fixtures give every test user an open-ended plan through the admin API (the existing specs
  keep working). `billing.spec.ts` uses users without a plan and **route-mocks** the payment side: `GET
  /api/billing/plans` answers a catalog with payments available, `POST /api/billing/checkout` a `pay_url` on the app's
  own origin (`/__fake-razorpay/<id>`, itself fulfilled with a small page linking to `/billing/return?order=<id>`),
  and `GET /api/billing/orders/<id>` pending then paid; `Me` comes from a real grant made mid-test. Covered: login →
  `/plans` → checkout → "Razorpay" → return → chat; a lapsed user redirected from `/` and `/c/<id>`; Settings and
  "Your data" reachable over `/plans`; the plan ending in an open chat (the panel, the draft kept); locked "+" items
  leading to `/plans`; the public pages without a session; the admin's Plans and Billing tabs (read-only). No request
  leaves for `rzp.io` (the outside-host guard stays on).

### 12.4 CI

`.github/workflows/ci.yml` needs no new job: the backend, Postgres, frontend, e2e and integration jobs pick up the new
tests; `compose.it.yml` gains the fake Razorpay service. No new package (B35).

## 13. Work split and interfaces

Four builders. **CORE**: backend billing core, payments and API. **GATE**: backend enforcement in the existing paths
and usage counting. **APP**: frontend, user-facing. **ADMIN**: frontend admin and the public pages. Builders work in
the same tree on disjoint files, never build into the repo, never change git state, keep test users named `smoke-*`,
and report a contract bug instead of editing the contract after stage 0.

### 13.1 Stages

```
stage 0:  CORE                      the contract: SPEC.md, types.ts, schemas_billing.py, additive schema fields
stage 1:  CORE (backend foundation) ∥ APP (frontend foundation)
stage 2:  CORE (payments and API) ∥ GATE (enforcement) ∥ APP (user pages) ∥ ADMIN (admin and public pages)
then:     integrator: README, HANDOFF, briefs index, migration rehearsal, Docker rebuild, browser pass, test-mode checks
```

Each stage ends with every suite green (`pytest`, `tsc --noEmit`, `vite build`, Vitest) and `alembic check` clean.

### 13.2 Ownership (each file has exactly one owner per stage)

| Builder | Owns |
|---|---|
| **CORE**, stage 0 | `SPEC.md` (all of section 7); `frontend/src/api/types.ts` (all of 7.1 except `MyUsage` and the admin-user types, which would break today's `tsc` before APP and ADMIN change their screens); `backend/app/schemas_billing.py` (new: every billing, public and admin-billing shape, `PlanAccess` and `PlanSnapshot` included); the additive fields with defaults until GATE fills them: `schemas.py` (`UserLimits.vision_monthly_token_limit = 0`, `UsageSummary.chat_allowance_used = 0`, `window_start`/`window_end` optional), `schemas_plugins.py` (`PluginInfo.in_plan = True`), `routers/models.py` (`ChatModel.allowance_rate: float | None = None`, that line only); the typed test factories that must follow the contract (`frontend/src/test/**`, `e2e/support/fakes.ts`) |
| **CORE**, stage 1 | `backend/app/models.py`; `backend/migrations/versions/0003_plans_billing.py`; `backend/app/db.py` (only `COMPARE_OPTIONS`, if needed, 11.1); `backend/app/bootstrap.py`; `backend/app/config.py` (adds 6.10's settings; the old ones stay until GATE); `backend/app/errors.py` (new); `backend/app/main.py` (the `AppError` handler); `backend/app/services/billing/{__init__,entitlements,money,periods,locks,audit}.py` (new); the mechanical move from `UserImageLimit` to `users.monthly_image_limit` in `services/usage.py`, `services/account.py`, `services/export.py` and `routers/admin.py` (those lines only); `backend/tests/conftest.py` (open-ended plans by default, the helpers of 12.1); tests `test_entitlements.py`, `test_billing_money.py`, `test_billing_periods.py`, `test_migration_0003.py` |
| **APP**, stage 1 | `frontend/src/api/types.ts` (`MyUsage` only, with the compile fixes it needs in `UsageTab.tsx`, `DailyUsageChart.tsx`, `AvatarMenu.tsx` and `PlusMenu.tsx`, those lines only); `src/api/client.ts` (codes, the plan handler, the billing and public calls); `src/auth/AuthContext.tsx` (the handler, the expiry timer); `src/App.tsx` (the routes of 8.1 with placeholder pages, `RequireAuth needsPlan`, the public routes from `src/pages/public/routes.tsx`, created here as an empty list); `src/lib/settingsLink.ts` (`billing` in the `SettingsTab` type and the parser; it joins the visible tabs in stage 2 with its panel); `src/lib/billing.ts` (new); `src/components/ui/icons.tsx` (the visual brief's new icons); `src/components/billing/{PlanCard.tsx,plan-card.css,PlanCompareTable.tsx,PolicyLinks.tsx}` (new); their Vitest files |
| **CORE**, stage 2 | `backend/app/services/billing/{plans,business,razorpay,checkout,payments,webhooks,reconcile,refunds,disputes,receipts,reminders,summary,csv_export}.py` (new); `backend/app/routers/{billing,admin_billing,public}.py` (new); `backend/app/main.py` (router wiring, the return-query redaction); `backend/app/worker.py` (start the billing loop); `backend/app/ops/checks.py` and `backend/app/schemas_system.py` (the `billing` check); `frontend/nginx.conf` (6.11); `.env.example`; `integration/fake_razorpay/**`, `integration/compose.it.yml`, `integration/tests/test_17_billing.py`; the backend tests of 12.1 for these modules, `test_billing_privacy.py`, `test_config.py` (the payments part) |
| **GATE**, stage 2 | `backend/app/deps.py`; `backend/app/config.py` (removes the old default settings, adds the start-up warning); `backend/app/schemas.py` (`Me.plan`, `UsageSummary` filled); `backend/app/schemas_account.py` (`MyUsage`); `backend/app/schemas_admin.py`; `backend/app/schemas_plugins.py`; `backend/app/services/{usage,model_registry,document_tool,image_tool,account,export}.py`; `backend/app/services/ingestion.py` (the vision allowance only); `backend/app/plugins/{registry,importing}.py`; `backend/app/plugins/gmail/{router,importer,tools}.py` (the note and the guards only); `backend/app/routers/{auth,chat,conversations,documents,models,plugins,me,admin}.py`; tests `test_plan_gating.py`, `test_usage_allowance.py`, `test_route_guards.py`, `test_deletion_completeness.py`, `test_export.py` and every existing test that needs the new shapes |
| **APP**, stage 2 | `src/pages/{PlansPage,CheckoutPage,BillingReturnPage}.tsx` (new) and `src/pages/plans.css`; `src/components/billing/**` (new `PlanBanner`, `PendingCheckoutBanner`, `LockedHint`, `PlanEndedPanel`, `billing.css`; `PlanCard`, `PlanCompareTable` and `PolicyLinks` keep their frozen props, which ADMIN uses); `src/components/settings/**` (`PlanBillingTab` and `YourDataSection` new, `SettingsModal`, `UsageTab`, `DailyUsageChart`, the Plugins & data tab); `src/components/chat/**` (`PlusMenu`, `ModelMenu`, `PluginsMenu`, `AvatarMenu`, `TopBar`, `EmptyState`, `MessageItem`, the composer, `useChatStream`); `src/pages/ChatPage.tsx` (banners, the plan-ended panel); `src/components/connections/{pluginStore.ts,ConnectionsPanel.tsx}` (Not in your plan); `src/components/documents/**` (402 messages, the over-limit notice, polling off without access); `src/lib/format.ts` (`nextUsageReset` removed); `e2e/**` (open-ended plans in the fixtures, `billing.spec.ts`); their Vitest files |
| **ADMIN**, stage 2 | `frontend/src/api/types.ts` (only `LimitOverrides`, `AdminUserPlan`, `AdminUser`, `AdminUserCreate`, `AdminUserUpdate`, together with the admin UI); `src/pages/AdminPage.tsx` (tabs `plans`, `billing`, the tab order); `src/components/admin/**` (new `PlansTab`, `PlanFormModal`, `planCost.ts`, `BillingTab` and its sections; changed `UsersTab`, `UserFormModal`, `adminApi.ts`, `admin.css`); `src/pages/public/**` (`routes.tsx`, `PublicLayout.tsx`, `PricingPage.tsx`, `PolicyPage.tsx`, `public.css`); `frontend/public/legal/*.md` (the seven placeholders of 10); `src/pages/LoginPage.tsx` and `src/pages/login.css` (the footer links); `frontend/src/test/adminTabs.ts` (the two new tabs); their Vitest files |
| **Integrator** | `README.md`, `docs/HANDOFF.md`, `docs/briefs/README.md`, `CLAUDE.md` (a line on plans); the rehearsal of 11.2; the Docker rebuild (recreate the frontend after the backend); a browser pass of section 8 against the fake Razorpay or route mocks, in both themes at 390 and 1440 px, against the visual brief's screenshots; the contrast pairs of the visual brief section 17 rechecked on the built CSS; with the owner's test keys and approval, the checks of 14.2 (at most 10 links) |

### 13.3 Frozen interfaces

**Backend** (CORE writes them in stage 1; GATE and CORE's stage 2 rely on them):

```python
# app/errors.py
class AppError(HTTPException):
    def __init__(self, status_code: int, detail: str, code: str, *, feature: str | None = None,
                 plan: str | None = None, headers: dict[str, str] | None = None) -> None: ...
# main.py renders it as {"detail": …, "code": …, "feature"?: …, "plan"?: …}

# app/services/billing/money.py
STANDARD_TOKEN_USD: Final = 3.20 / 1_000_000
def allowance_tokens(cost_usd: float, total_tokens: int) -> int: ...          # 3.3
def format_inr(paise: int) -> str: ...                                          # "₹2,000", "₹1,234.50"
def ist_date(instant: datetime) -> date: ...
def format_ist_date(day: date) -> str: ...                                      # "25 Nov 2026"

# app/services/billing/entitlements.py
PlanState = Literal["exempt", "none", "active", "expired"]

@dataclass(frozen=True)
class Limits:
    monthly_token_limit: int; rate_limit_per_min: int; max_upload_mb: int; max_documents: int
    max_storage_mb: int; monthly_image_limit: int; vision_monthly_token_limit: int

@dataclass(frozen=True)
class Entitlements:
    user_id: int; is_admin: bool; state: PlanState; access: bool
    plan_id: int | None; plan_name: str | None; period_id: int | None
    complimentary: bool; open_ended: bool; test_mode: bool
    files: bool; images: bool; plugins: frozenset[str]
    chat_model_ids: frozenset[int] | None      # effective set (plan ∩ user list); None: every enabled chat model
    plan_model_ids: frozenset[int] | None      # the plan's set alone: outside it → 402, inside but not effective → 403
    default_chat_model_id: int | None
    limits: Limits
    window_start: datetime; window_end: datetime
    paid_through: datetime | None              # active by period: end of the chain; expired: when access ended
    valid_through: date | None                 # IST date; None for open-ended plans and the admin

async def load(db: AsyncSession, user: User, *, now: datetime | None = None) -> Entitlements: ...
async def load_many(db: AsyncSession, users: Sequence[User], *, now: datetime | None = None) -> dict[int, Entitlements]: ...
def require_access(ent: Entitlements) -> None: ...                       # AppError 402 plan_required | plan_expired
async def feature_error(db: AsyncSession, ent: Entitlements, feature: str, label: str) -> AppError: ...  # 402 with unlock
def allows_plugin(ent: Entitlements, plugin_id: str) -> bool: ...
def allows_model(ent: Entitlements, model_id: int) -> bool: ...
async def unlock(db: AsyncSession) -> PlanUnlock: ...                   # cached 60 s
def invalidate_unlock() -> None: ...
async def plan_access(db: AsyncSession, user: User, ent: Entitlements) -> PlanAccess: ...   # for Me (schemas_billing)
def user_limits(ent: Entitlements) -> UserLimits: ...                   # schemas.UserLimits

# app/services/billing/locks.py
async def lock_user(db: AsyncSession, user_id: int) -> None: ...        # FOR NO KEY UPDATE; first in every period change

# app/services/billing/audit.py
async def record_event(db: AsyncSession, *, source: str, type: str, actor: str | None = None,
                       user_id: int | None = None, order_id: int | None = None, payment_id: int | None = None,
                       detail: Mapping[str, object] | None = None) -> None: ...   # never commits

# app/services/billing/periods.py
def period_end(start: datetime, days: int) -> datetime: ...              # 4.2
def valid_through(ends_at: datetime) -> date: ...
async def grant(db, user, plan, *, days: int | None, reason: str, actor: str,
                start: Literal["now", "after_current"]) -> None: ...    # AdminUserCreate.grant uses it; None: open-ended
async def on_user_deleted(db, user) -> None: ...                         # stage 1: a no-op; stage 2: cancels links

# app/deps.py (GATE)
async def get_entitled_user(request: Request, user: User = Depends(get_current_user),
                            db: AsyncSession = Depends(get_db)) -> User: ...   # loads, stores, require_access
def current_entitlements(request: Request) -> Entitlements: ...
```

The services GATE changes take `ent: Entitlements` as their last positional parameter (`check_budget(db, user, ent)`,
`images_in_window(db, user_id, ent)`, `vision_tokens_in_window(db, user_id, ent)`, `document_tool.plan(db, user,
model, ent)`, `image_tool.plan(db, user, model, ent)`, `registry.plan_chat(db, user, model, ent)`,
`registry.access_for(db, user, plugin, ent=None)` (None: loads it), `list_chat_models_for_user(db, user, ent)`).
`routers/admin.py` writes its audit events through `audit.record_event`.

**Frontend** (APP writes them in stage 1; ADMIN relies on them):

```ts
// src/api/client.ts
export class ApiError extends Error { status: number; code: string | null; feature: string | null; plan: string | null }
export function setPlanRequiredHandler(handler: (() => void) | null): void;
export const billingApi: { plans(): Promise<PlanCatalog>; status(): Promise<BillingStatus>;
  checkout(body: CheckoutRequest): Promise<CheckoutStarted>; order(id: string, refresh?: boolean): Promise<OrderView>;
  cancel(id: string): Promise<OrderView>; payments(): Promise<PaymentView[]>; profile(): Promise<BillingProfile>;
  saveProfile(body: BillingProfile): Promise<BillingProfile> };
export function receiptUrl(id: number): string;                 // "/api/billing/receipts/<id>"
export const publicApi: { site(): Promise<PublicSite>; plans(): Promise<PublicPlan[]>;
  page(slug: PageSlug): Promise<string> /* the Markdown of /legal/<slug>.md */ };
// src/lib/billing.ts
export function formatInr(paise: number): string;
export function formatIstDate(value: string | Date): string;    // "25 Nov 2026"; a "YYYY-MM-DD" is taken as that day
export function formatIstRange(start: string, end: string): string;   // "26 Oct to 25 Nov 2026"
export function answersLabel(answers: number | null, model: string | null): string;  // "About 1,600 answers a month with Claude Haiku 5.5"
export function formatRate(rate: number | null): string | null;      // "×2", "×0.05"; null for 1 or unknown
export function isRazorpayUrl(url: string, testMode: boolean): boolean;
export const DRAFT_MARKER = "<!-- draft -->";
// src/components/billing/PlanCard.tsx (PlanOffer is assignable to PublicPlan)
export default function PlanCard(props: { plan: PublicPlan; emphasis?: "recommended" | "current" | "last" | null;
  badges?: ReactNode; actions?: ReactNode; headingLevel?: 2 | 3 }): JSX.Element;
// src/components/billing/PlanCompareTable.tsx
export default function PlanCompareTable(props: { plans: PublicPlan[]; emphasisedCode?: string | null }): JSX.Element;
// src/components/billing/PolicyLinks.tsx: Pricing, Terms, Privacy, Cancellation and refunds, Contact, About, Shipping
export default function PolicyLinks(props: { className?: string; compact?: boolean /* the login page's five */ }): JSX.Element;
// src/pages/public/routes.tsx (APP creates it empty in stage 1; ADMIN owns it in stage 2)
export const publicRoutes: ReactElement[];                      // <Route> elements, rendered inside <Routes>
// src/components/ui/icons.tsx: CardIcon, TagIcon, ReceiptIcon, BankIcon, GiftIcon, MinusIcon, ArrowUpRightIcon, FlaskIcon
// src/auth/AuthContext.tsx: useAuth().user.plan and useAuth().refresh(), as today
```

## 14. Open questions

### 14.1 For the owner

**Before the build** (each changes what is built; the brief's proposal is what gets built if the owner agrees):

1. **The chat allowance counted by model price, and the default limits** (3.2, 3.3): Starter about 1,600 Haiku
   answers a month, Standard about 260 Sonnet answers, Professional about 290 Opus answers, with worst-case AI costs
   of 35%, 34% and 38% of what you keep. If you'd rather count plain tokens, every model counts 1 and Starter drops
   back to about 200 answers. Also confirm the model lists (Starter without Sonnet and Opus, Standard without Opus).
   **Approved by the owner on 2026-10-26 as proposed**: counted by model price, with 3.2's limits and model lists.
2. **`bikash`** (looks like your own account): keep Professional free with **no end date** (proposed), or 30 days?
   **Approved by the owner on 2026-10-26 as proposed**: `bikash` gets Professional free with no end date at rollout.
3. **Consent at checkout**: the line "By paying you agree to the Terms…" under the Pay button, recorded with the order
   (proposed, fewer taps), or a tick box the user must tick first?
   **Approved by the owner on 2026-10-26 as proposed**: the consent line under Pay, recorded with the order.

**Can wait until going live** (the build doesn't change):

4. **GST**: are you GST-registered? Version 1 is built for a seller without a GSTIN (no GST on receipts). If you are
   registered or will be, tax invoices are a separate feature that must be built before you charge GST.
5. **Business details** for receipts and the public pages: legal name, address, contact email and phone, and the
   receipt prefix (proposed `RA`).
6. **Policy texts**: terms, privacy, refunds and cancellation (proposed: no automatic refunds, refunds at your
   discretion from Admin → Billing, a full refund ends that period, no refund when switching to a cheaper plan, a
   disputed payment ends its plan), shipping, about, contact. You write them; Claude can draft them for your review.
7. **Reminder emails**: wanted? They need a sender address on your SMTP server (`BILLING_EMAIL_FROM` and the
   `ALERT_SMTP_*` settings). Without them, reminders are in the app only.
8. **Offline bank transfers**: will you accept them (you record each in Admin → Billing)? If yes, the bank details go
   on your Contact page.
9. **Data of lapsed users**: how long do you keep chats and documents of users whose plan ended (for the privacy
   page)? The app deletes nothing by itself.

### 14.2 To check in Razorpay test mode (the integrator, with the owner's test keys; at most about 10 links)

1. A `callback_url` on `http://localhost:8080` is accepted and the redirect works (research 10.1); otherwise put the
   quick-tunnel origin first in `PUBLIC_BASE_URLS` until the domain exists.
2. The Payment Link page shows a UPI QR on desktop and UPI intent on Android Chrome (research 10.2).
3. Whether a test UPI path still exists after the end of UPI Collect (research 10.3); the test notice shows cards
   until then.
4. The create response includes `order_id`; payments are auto-captured (research 10.6); the payment entity carries
   `fee` and `tax`.
5. A repeated `reference_id` is refused (research 10.4) and `GET /v1/payment_links?reference_id=` finds a link.
6. `GET /v1/disputes/<id>` answers with test keys, and whether test disputes can be raised to see the webhooks.
7. Razorpay's website check (once the domain exists) reads the SPA policy pages; if it reports them missing, the
   pages are pre-rendered (14.3).
8. After KYC, live: the account's UPI limit per payment (set `upi_limit_paise`, or 0), then a Starter payment by a
   `smoke-*` user and a full refund (Razorpay keeps its fee, about ₹12).

### 14.3 Risks to keep in view

- **Test mode allows about 30 Payment Links per business.** Automated tests never use Razorpay; manual runs count
  their links (Admin → Billing shows the count).
- **Activation depends on the worker or the return page while webhooks are off.** If the Mac sleeps, payments are
  confirmed when it wakes (the reconciler catches up; nothing is lost).
- **The quick tunnel's URL changes on restart**, so a webhook registered on it breaks; register webhooks only on the
  Cloudflare domain.
- **Live keys need the website review** (research 6.2): the public pages filled in, the domain registered with
  Razorpay, and a reviewer account the admin creates and marks as a tester.
- **Policy pages are client-rendered.** An automated check that doesn't run JavaScript sees an empty page; the
  fallback is a small build step that renders `frontend/public/legal/*.md` to static HTML.
- **The standard token is tied to one price** ($3.20 per million). If model prices move a lot, the owner edits the
  allowances in Admin → Plans; the cost estimate shows the effect at once.
