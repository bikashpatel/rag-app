# Brief: Direct UPI payments to the owner's UPI ID

> Customers pay the owner's own UPI ID by QR code or a UPI app link, then type the payment's 12-digit UTR; the owner
> finds the credit in the bank or UPI app and confirms it in Admin; only then the plan starts, through the same
> exactly-once apply as every other payment. Razorpay stays the automatic option once its KYC is done. Builds on
> [`plans-billing.md`](plans-billing.md) ("the billing brief", its section numbers as "PB 6.5") and its visual brief
> [`plans-billing-visual.md`](plans-billing-visual.md) ("PBV"); `SPEC.md` is the binding contract and already carries
> this brief's contract (stage 0, section 13). If this brief and `SPEC.md` disagree, `SPEC.md` wins and the brief is
> fixed.
>
> **Revision 1** (2026-11-01): designed, contract applied (stage 0). **Built 2026-11-02** (stage 1 and the review
> fixes; deployed 2026-11-02, off until the owner sets the UPI ID); sections 2.3 and 10.2 describe the UTR field and the payment notes as built. Nothing here
> is legal or tax advice; the policy texts and anything about tax are for the owner and their accountant (section 14).

The owner decided (D1 to D10, binding): add direct UPI next to Razorpay; the customer pays by QR or app link and
enters the UTR; the owner checks the bank or UPI app and confirms in Admin; then the plan starts.

## 0. Decisions

| # | Decision | Detail and why |
|---|---|---|
| D1 | **Settings** in Admin → Billing → Business & payments, stored in `app_state` `billing_settings` (not `.env`): `upi_enabled`, `upi_id`, `upi_payee_name` | `upi_id` is validated (`^[a-z0-9][a-z0-9._-]{0,79}@[a-z][a-z0-9]{1,63}$` after trimming and lowercasing, at most 100 characters); `upi_payee_name` 1 to 50 of `A-Z a-z 0-9 space . , & ' ( ) / -` with spaces collapsed (ASCII, because apps show the bank's own name anyway, 2.4). Turning it on without both is refused (422 "Add your UPI ID and the payee name to turn on UPI payments."). Direct UPI is offered only while on with both set (`upi.ready`). Every change is audited (`settings.updated`, before and after) |
| D2 | **Checkout offers "Pay with UPI" first and "Pay online" second**, each only when available (`PaymentsInfo.upi_direct`, `PaymentsInfo.available`); neither: today's "Paid plans open soon" | One quote for both: the same `CheckoutOption` (dates, amount, credit, changes). `available` keeps its meaning (online payment for this caller) so nothing that reads it changes meaning; an option is buyable when either is available |
| D3 | **A direct-UPI order is a billing order** (`mode` `offline`, `channel` `upi_direct`) with its own reference `AN-XXXX-XXXX` from `23456789ABCDEFGHJKMNPQRSTUVWXYZ` (31 characters: no 0, O, 1, I, L), unique, which is the UPI note `tn`; amount and credit as for Razorpay orders (whole rupees). The request is exactly `upi://pay?pa=<upi_id>&pn=<payee name>&am=<rupees>.00&cu=INR&tn=<reference>` | `tr`, `mc`, `mode` and `sign` are left out (research 2.2: `tr` and `mc` mark a merchant transaction and can fail against a personal or small-merchant UPI ID; `sign` needs keys registered with NPCI). 31^8 ≈ 8.5 × 10^11 references, drawn with `secrets`, retried on the unique constraint |
| D4 | **The checkout's UPI panel**: a QR code (server-rendered SVG with segno, same origin, so the CSP stays `img-src 'self' data:`), "Open UPI app" on phones, the UPI ID, amount and reference with copy buttons, and a 12-digit UTR field (digits only) | Added: a **PNG** of the same QR for "Save QR image" (scan it from the UPI app's gallery on the same phone, or on an iPhone where the link opens the wrong app, 2.5), and an **admin ₹1 test QR** for the owner's own test before turning it on (11). The panel lives on the order's own page (`/billing/return?order=<id>`), one URL that survives a reload, a banner click or a closed tab |
| D5 | **Submitting the UTR** (exactly 12 ASCII digits; spaces dropped) moves the order to `submitted` ("We're checking your payment"); no plan starts. **One open direct-UPI order per user**; without a UTR it expires 24 hours after it was made; once submitted it waits for the admin (no expiry, no cancel) | **Uniqueness**: a partial unique index over `upi_utr` of orders `submitted`, `paid` or `review`: no UTR can wait twice or be confirmed twice, ever. A **rejected** order's UTR is free again, so an owner's mistaken "not received" can be put right by the customer resubmitting the same UTR on a new order (0.1) |
| D6 | **Admin → Billing → UPI**: the UTRs to check (count in Needs attention), with the reminder to find a credit of exactly that amount with that UTR. **Confirm** records the payment (method `upi`, mode `offline`, `offline_reference` = UTR, `recorded_by` = the admin) and applies it once through the apply path; **Reject** takes a reason (`not_received`, `amount_differs`, `utr_used`, `other` with text) that the customer sees, and they can start a new order. Both audited (`upi.confirmed`, `upi.rejected`) | Confirm sends the UTR the admin looked at; a different one is refused (409 `utr_mismatch`), so a wrong row or a stale dialog can't confirm another payment. Double clicks and two admins: the user lock, the order's row lock and a status re-check make the second confirm a no-op (200, the same order) |
| D7 | **No confirmed email needed** for direct UPI (receipts are always in Settings; emailed when there is a verified email and mail works). Abuse guards instead | One open direct-UPI order per user; one UTR per order; 5 orders per IST day (3 for an unpaid account); 10 UTR submissions a day per user; 3 rejections in 30 days pause direct UPI for that user (409 `upi_paused`); unpaid accounts together have at most 20 UTRs waiting for the admin (503 `checkout_busy`, checked when an order is made, never when a UTR is sent: a customer who paid can always send it; orders without a UTR don't count, since they cost nothing to make; a refusal makes the billing check warn for an hour); unpaid accounts of one network make at most 10 direct-UPI orders an hour and 30 a day (`upi_unpaid`, 429 `too_many_attempts`) |
| D8 | **The owner is told**: the Needs-attention count, the monitor's `billing` check (a UTR waiting over 24 hours), and an email to the business contact email when the mailer is configured (outbox `upi_submitted`; no address logged) | The email carries amount, plan, username, reference, UTR and the Admin link; the job has no `user_id`, so it survives the customer's deletion and never counts as the customer's mail |
| D9 | **Refunds**: the owner pays back by UPI outside the app and records it with the existing offline refund (`POST /admin/billing/payments/{id}/refund`: recorded only, `processed` at once); access follows the existing refund rules (PB 4.10) | The path already exists (`refunds.start`, `mode == "offline"`); only the dialog's text changes (10.9). No new endpoint |
| D10 | **Receipts** read "UPI (direct)" with the UTR; the **policy pages** (`frontend/public/legal/`: refunds, terms, privacy, shipping, contact) mention direct UPI, keeping their `<!-- draft -->` markers and style | Receipt: method "UPI (direct)", reference row "UPI UTR", the plan line's detail "UPI (direct), UTR 630512345678". Page texts in 10.11 |
| D11 | **Two new order statuses**, `submitted` and `rejected` (`OrderStatus`), and a derived **`channel`** (`razorpay`, `upi_direct`, `bank_transfer`) on orders, pending orders and payments | Every existing rule that keys on `pending` (Razorpay supersede, cancel, reuse, reconcile, reminders) keeps working unchanged and leaves a waiting UTR alone. `channel` needs no column: `upi_reference` set → `upi_direct`; mode `offline` without it → `bank_transfer`; else `razorpay` |
| D12 | **While a UTR waits, no new checkout of either channel** (409 `upi_waiting`) | The customer may well have paid; a second payment would be a refund for the owner. A pending (unpaid) order of either channel is replaced by a new one, as Razorpay orders are today |
| D13 | **A `now` order (upgrade, or a new period of the same plan) by direct UPI** is checked at confirmation like a Razorpay one (usage up to the confirmation, the time left as quoted, against the replaced period's own window), with adjustments for the hours an owner may take: usage after the UTR's submission counts only **beyond the share of the period that passed while the owner checked** (the allowance at the plan's pace: chatting normally while waiting isn't held against the customer, using the allowance up is), and when the replaced period **ended by itself** meanwhile (unrevoked, plan and end as quoted) the same check runs and the 30 days are added at max(now, paid through), with the amount paid as the charge basis, instead of going to review. A period paid in test mode gives no credit (409 `checkout_unavailable` at the order, `review` at the confirmation) | The credit for a period that ended between the quote and the confirmation was for at most those hours (a few rupees); sending such orders to review would make the owner refund or grant for nothing. Counting usage only up to the UTR (revision 1) let a customer use a whole allowance while the owner checked and still get the full credit. A period that was revoked or changed, or a credit that fell because of usage before the UTR or beyond the pace after it, still goes to `review` (the money was received; the admin refunds or grants, PB 6.6) |
| D14 | **The customer sees a rejection** for 7 days (`Me.plan.rejected_order`), on the order page, in a chat banner, on `/plans` and in Settings, and by email (`upi_rejected`, with `email_receipts`) | The customer typically waits away from the app; the chat banner is where they come back |
| D15 | **`captured_at` of a direct-UPI payment is the UTR's submission time** | The payment was made then; the month in the summary and CSV and "Paid on" on the receipt follow the money, not the owner's check |
| D16 | **An open order keeps the UPI ID and name it was made with** (`upi_vpa`, `upi_payee_name` on the order), until it expires | A customer who paid the old ID must still be able to send the UTR; the admin list shows which ID a payment went to when it differs from the current one |
| D17 | **The billing loop always runs** in the worker, and its tick marks expired direct-UPI orders (`order.expired`); every read treats a `pending` one past `expires_at` as `expired` already | Direct UPI can be on without Razorpay or mail, and its settings live in the database, which `BillingLoop.wanted()` can't read at start |

### 0.1 Where this design adds to or departs from D1 to D10

- **D5 "unique across every direct-UPI payment ever"**: kept for every UTR that is waiting, confirmed or under review
  (a partial unique index in the database), but a **rejected** UTR is released. Why: "not received" can be the
  owner's mistake (a bank app that showed the credit late); without the release the customer could never be
  credited for a real payment except by a manual bank transfer record. A fraudster gains nothing from the release:
  each resubmission is checked again, and 3 rejections pause their direct UPI (D7).
- **D3 `tr` and `mc`**: researched (2.2) and left out on purpose.
- **D4 additions**: the PNG download and the admin's ₹1 test QR.
- **D6 addition**: the UTR echo on confirm.
- **D7 specifics**: the caps above; `CHECKOUT_UNPAID_PER_HOUR` (a Razorpay link cap) isn't reused, because the harm
  here is the owner's queue, which a cap on waiting UTRs bounds directly (each costs its sender a rejection, and 3
  pause the account); orders without a UTR cost nothing and don't count, and the network limit slows throwaway
  accounts.
- **D8 and D14**: also an email to the customer on rejection.
- **D13**: the two `now` adjustments.

## 1. Scope and non-goals

**In scope**: the settings, the UPI checkout and order, the QR codes and link, UTR submission, expiry, the admin's
check (confirm, reject), the apply, receipts, refunds (text only), Needs attention, the monitor, two emails, the CSV
and the data export, policy pages, migration 0005, tests.

**Not in scope** (each can come later without undoing this): automatic matching from a bank statement or a bank's
API; UPI collect requests (P2P collect ended on 1 Oct 2025, 2.3); signed intents (`sign`, `orgid`) and merchant
parameters (`mc`, `tr`); app-specific iPhone buttons (`tez://`, `phonepe://`, `paytmmp://`; unverified for personal
IDs, 2.5); partial payments or confirming an amount other than the order's; storing the payer's UPI ID or name.

## 2. Research (as read on 2026-11-01)

Labels: **spec** (NPCI's own document), **vendor** (a payments company's documentation), **report** (a developer's
write-up; unverified). The owner's ₹1 tests (11) settle the app behaviour that only reports describe.

### 2.1 The UPI link (NPCI UPI Linking Specifications 1.6, November 2017, draft; spec)

- Format `upi://pay?param=value&…`; every PSP app must listen to these links for QR, intent, NFC and so on.
- `pa` (payee VPA) and `pn` (payee name) are mandatory. `am` is the amount "in decimal format"; without `am` the
  amount is editable; `mam` (a minimum amount) makes it editable, and without `mam` the amount "should NOT be
  editable". `cu`: only `INR`. `tn`: a short note. `tr`: a transaction reference, "Mandatory for Merchant
  transactions and dynamic URL generation". `mc`: the merchant code, "if present then needs to be passed as it is".
  `mode` (00 default, 01 QR, 02 secure QR, 04 intent, 05 secure intent…), `sign` and `orgid` belong to signed
  intents; "if signature is not present in intent then the application should show warning message to user that
  the 'source of intent could not be verified' and shall request for passcode": an unsigned link still works, with
  the PIN asked as always.
- Spaces must be `%20` (RFC 3986), not `+`; apps also read a bare `%` as a space for old links. Null values must not
  be passed as the text "null".
- Source: [NPCI UPI Linking Specifications 1.6](https://www.labnol.org/files/linking.pdf) (a copy; NPCI's own site
  didn't come up). Google's Pay-for-India intent guide lists the same parameters and adds that the amount in the
  app's answer "might not be the amount you requested", to be checked with the PSP:
  [Google Pay in-app payments](https://developers.google.com/pay/india/api/android/in-app-payments) (vendor).

### 2.2 Personal and merchant UPI IDs

- Merchant (P2M) links need `mc` and a unique `tr` per payment; a missing or reused `tr` is reported to show as a
  generic "limit exceeded" or security error ([technetexperts, 14 June 2026](https://www.technetexperts.com/fix-upi-limit-exceeded-error/),
  report). A P2P link needs only the VPA and amount (same source).
- Leave `mc` and `tr` out of a link to a personal UPI ID: they "can trigger merchant-verification failures against
  unregistered VPAs"; use whole rupees, since "several apps drop or round the paise component on P2P intents";
  don't `+`-encode spaces; don't percent-encode the `@` in `pa`
  ([DEV, "a payments page with no payment processor"](https://dev.to/shivams136/what-i-learned-building-a-payments-page-with-no-payment-processor-a-free-self-hosted-zero-lne),
  report).
- Browser-launched links: the same report says Google Pay and PhonePe restrict `upi://` intents launched from
  mobile browsers, undocumented and silent ("exceeds limit for this merchant" or nothing); its sentence is
  ambiguous about which IDs are affected. Another says "some apps show warnings or limits for link-initiated
  payments to non-merchant UPI IDs", that a payer may be able to change a prefilled amount in some apps, and that
  "a 50-character cap is a sensible default" for `tn` ([DEV, "How UPI deep links work"](https://dev.to/srvmhptr/how-upi-deep-links-work-upipay-build-a-payment-link-and-qr-code-without-a-payment-gateway-g8m),
  report). Both say the **QR code and "copy UPI ID"** work everywhere, since scanning inside a UPI app isn't a
  browser intent. Hence the design: the QR (desktop) and the UPI ID with Copy (everywhere) are always shown; the
  link is an extra on phones; the admin checks the exact amount.
- Small merchants on their own account (P2PM, e.g. the business apps' "merchant" IDs) have inward limits of ₹10,000
  per payment, ₹25,000 per 24 hours and ₹1,00,000 per 30 days, by an NPCI circular that acquirers had to comply with
  by 30 April 2024 ([Lexplosion's summary](https://lexplosion.in/npci-fixes-maximum-upi-inward-credit-limit-for-p2pm-merchants-acquiring-banks-and-payment-service-providers-to-comply-with-directions-before-30th-april-2024/),
  secondary). At ₹2,000 a Standard payment, ₹1,00,000 a month is 50 payments (owner question 14.1).

### 2.3 Rules that touch this flow

- **P2P collect requests ended on 1 October 2025** (NPCI circular of 29 July 2025): the app can't ask for money; the
  payer must push it, as this design does ([Business Today](https://www.businesstoday.in/amp/personal-finance/banking/story/npci-to-tighten-upi-rules-peer-to-peer-collect-feature-to-end-in-october-489244-2025-08-13),
  [The Week](https://www.theweek.in/wire-updates/business/2025/08/14/dcm65-biz-npci-collect.html)).
- **Verified names**: from 30 June 2025 UPI apps show the beneficiary's name from the bank's records (CBS), not a
  nickname or the QR's name, for P2P and P2PM payments
  ([Angel One](https://www.angelone.in/news/economy/upi-appsto-to-show-only-real-beneficiary-names-from-june-30-2025)).
  So the customer sees the bank's name for the owner's UPI ID whatever `pn` says: the payee name should read like
  it, and the panel tells the customer which name to expect.
- **New payers**: a bank may cap a recently added account, a new device or a reset PIN at ₹5,000 for 24 hours
  (PhonePe's help page, seen in search results; it didn't load for us:
  [PhonePe help](https://cms.phonepe.com/en/chat-bubble/u16_npci_txnrisk_00966-payment-failed)). Professional
  (₹5,000) is right at that cap. A payment the app shows as failed or pending can still have taken the money for a
  while (a failed transfer is reversed by itself), so the panel's note doesn't promise that no money was taken: it
  asks the payer not to pay again straight away, to check the bank balance, and to enter the UTR if the app later
  shows the payment as successful (10.2).
- Usual limits: about ₹1,00,000 a day and 20 transfers in 24 hours per payer, set by NPCI and banks (same search).

### 2.4 The UTR

- A UPI payment's reference (UTR, RRN, "UPI transaction ID", "UPI Ref No.") is a **12-digit number**, shown on the
  success screen and in the app's history, generated once per transfer
  ([PhonePe Business](https://business.phonepe.com/articles/utr-number-meaning-what-every-merchant-needs-to-know),
  vendor). Its internal layout (a date part) is described for NEFT, not reliably for UPI, so the app checks only
  "12 ASCII digits" (Rust's `\d` would accept Devanagari digits: the pattern is `[0-9]`).

### 2.5 Phones

- Android shows a chooser of the UPI apps installed for `upi://pay`; an iPhone has no chooser, and the browser
  can't tell which apps are installed; payment companies use app-specific schemes there (`tez://upi/pay`,
  `phonepe://pay`, `paytmmp://upi/pay`, `bhim://upi/pay`) and fail when the app isn't installed
  ([Juspay mWeb intent](https://docs.juspay.io/mweb-intent/docs/upi-intent-on-mweb/enable-upi-intent-on-mweb),
  vendor, for merchant intents). Version 1 offers the generic link with the iPhone note, plus the PNG for "scan from
  gallery".

### 2.6 segno

- [segno](https://pypi.org/pypi/segno/json) 1.6.6 (uploaded 2025-03-12), pure Python, no dependency on Python 3.10+,
  writes SVG and PNG itself (no Pillow). It is the newest release before the lock's cutoff (2026-09-30).

## 3. Flows

```
customer:  /plans → /plans/checkout/<code>?kind= → "Pay with UPI" → POST /billing/upi/checkout → 201 OrderView
           → /billing/return?order=<id>: QR (desktop) / "Open UPI app" (phones), UPI ID, amount, reference
           → pays in the UPI app → types the UTR → POST /billing/orders/<id>/utr → submitted: "We're checking"
           → (owner confirms) → paid: plan active, receipt; (owner rejects) → rejected: reason, "Start a new payment"
owner:     email "UPI payment to check" / Needs attention "2 UPI payments to check" → Admin → Billing → UPI
           → bank or UPI app: a credit of exactly ₹2,000 with UTR 6305 1234 5678 (and note AN-7K4M-Q9TX)?
           → Confirm… (tick, UTR echoed) → POST …/upi-confirm → apply → receipt → customer's plan starts
           → or Reject… (reason, note) → POST …/upi-reject → the customer is told
expiry:    no UTR 24 h after the order → reads as expired; the billing loop writes expired (order.expired)
           → a customer who paid late starts a new order for the same plan and sends that UTR there
refund:    owner pays back by UPI from the bank app → Admin → Payments → Refund… (recorded only) → access per PB 4.10
deletion:  pending direct-UPI orders are cancelled with the user's other pending orders; a submitted one stays in the
           owner's list (user deleted): Confirm answers 409 user_deleted; Reject it and pay the money back by UPI
```

## 4. The direct-UPI order

```
          POST /billing/upi/checkout                          the user cancels, or a new checkout replaces it
                   │                                        ┌──────────────────────────────────▶ cancelled
                   ▼                                        │
               pending ──────────────────────────────────────┤ 24 h after creation, no UTR
                   │                                        └──────────────────────────────────▶ expired
                   │ POST …/utr (12 digits, not held by another waiting or confirmed order)
                   ▼
              submitted ── admin rejects (reason) ───────────────────────────────────────────────▶ rejected
                   │
                   │ admin confirms (the UTR echoed): payment recorded, apply under the user's lock, once
                   ▼
           paid  (period added; receipt)     or     review  (a `now` order whose period changed or whose credit
                                                             fell before the UTR: the admin refunds or grants)
```

- **Open** = `pending` and `expires_at > now`, or `submitted`. At most one per user (D12).
- `expires_at` = created + 24 h; `watch_until` = `expires_at`; `next_check_at` null (the reconciler never sees it);
  `origin` as Razorpay orders; `terms_accepted_at` = the quote's time; `created_at` = the quote's time.
- `paid` and `review` are final for the order; refunds act on the payment (PB 6.7).

## 5. Data model and migration 0005

### 5.1 `billing_orders` (models.py), new columns, all nullable

| Column | Type | Meaning |
|---|---|---|
| `upi_reference` | String(12), `UniqueConstraint("upi_reference", name="uq_billing_orders_upi_reference")` | `AN-XXXX-XXXX`; set ⇔ a direct-UPI order |
| `upi_vpa` | String(100) | the payee UPI ID the order asks to pay (from the settings at creation) |
| `upi_payee_name` | String(50) | the payee name, likewise |
| `upi_utr` | String(12) | the customer's UTR |
| `upi_submitted_at` | UTCDateTime | when the UTR arrived (also the payment's `captured_at`) |
| `upi_decided_at` | UTCDateTime | confirmed or rejected |
| `upi_decided_by` | String(64) | the admin's username |
| `upi_rejection` | String(16) | `not_received` \| `amount_differs` \| `utr_used` \| `other` |
| `upi_rejection_note` | String(200) | the admin's note (shown to the customer) |

Indexes (in `__table_args__`, so the drift guard sees them):

```python
Index("uq_billing_orders_upi_utr", "upi_utr", unique=True,
      sqlite_where=text("upi_utr IS NOT NULL AND status IN ('submitted', 'paid', 'review')"),
      postgresql_where=text("upi_utr IS NOT NULL AND status IN ('submitted', 'paid', 'review')")),
Index("ix_billing_orders_status_submitted", "status", "upi_submitted_at"),
```

`status` gains the values `submitted` and `rejected` (a plain String(16); no check constraint exists). An order's
status that leaves `submitted`, `paid` or `review` for another value releases its UTR; only `rejected` does so by
design (a submitted order of a deleted user stays `submitted` until rejected).

`billing_payments` is unchanged: a direct-UPI payment is `razorpay_payment_id` null, `method` `upi`, `mode`
`offline`, `offline_reference` the UTR, `recorded_by` the admin, `captured_at` the submission, `amount_paise` and
`currency` the order's, `status` `captured`, `applied_at` set by the apply.

### 5.2 Migration `backend/migrations/versions/0005_upi_direct.py`

`--rev-id 0005`, down_revision `0004`, forward only (downgrade raises, as 0003 and 0004), in the start-up
transaction: `op.add_column` for the nine columns (batch mode on SQLite via env.py), the unique constraint, both
indexes (the partial one with `sqlite_where`/`postgresql_where`). It changes no row and imports no app module. The
migrations README gets its row. `test_migrations.py`'s drift guard must pass on SQLite and Postgres; if Alembic's
comparison reports the partial index (it compares names, columns and uniqueness, not `WHERE`), check
`COMPARE_OPTIONS` rather than dropping the predicate. `test_migration_0005.py`: from 0004 with rows, every column
null on old orders, the partial index refuses a second waiting order with the same UTR and accepts it once the first
is `rejected`, on both databases.

## 6. API (applied to `SPEC.md` in stage 0)

Shapes (`schemas_billing.py`, `types.ts`): `OrderChannel`; `PendingOrder.channel`, `.status` (`pending` |
`submitted`); `UpiRejectReason`, `UpiRejection`, `RejectedOrder`, `PlanAccess.rejected_order`; `PaymentsInfo.upi_direct`;
`OrderStatus` + `submitted`, `rejected`; `UpiOrderInfo`; `OrderView.channel`, `.upi`; `PaymentView.channel`;
`UtrSubmission`; `BillingSettings.upi_enabled`, `.upi_id`, `.upi_payee_name`; `AdminOrder.upi_decided_by`;
`UpiConfirmRequest`, `UpiRejectRequest`; `AttentionKind` + `upi_to_check`. Every new response field has a pydantic
default, so today's code keeps working until the builders fill it.

| Endpoint | Request → response | Errors (code) |
|---|---|---|
| `POST /billing/upi/checkout` | `CheckoutRequest` → 201 `OrderView` (`pending`, `upi.uri`) | 403 `admin_exempt`, 404, 409 `checkout_unavailable`, 409 `upi_waiting`, 409 `upi_paused`, 422, 429 `too_many_checkouts`, 503 `upi_unavailable`, 503 `checkout_busy` |
| `POST /billing/orders/{public_id}/utr` | `UtrSubmission` → `OrderView` (`submitted`) | 404, 409 `order_not_open`, 409 `utr_used`, 422, 429 `too_many_attempts` |
| `GET /billing/orders/{public_id}/upi-qr.svg` / `.png` | → `image/svg+xml` / `image/png` (attachment) | 404 |
| `POST /billing/checkout` (changed) | as today | + 409 `upi_waiting` |
| `POST /billing/orders/{public_id}/cancel` (changed) | also a `pending` direct-UPI order | 409 for `submitted` (as for any non-pending) |
| `GET /billing/orders/{public_id}` (changed) | `channel`, `upi`; `?refresh=true` never calls Razorpay for it | |
| `GET /billing/plans`, `/billing/status`, `/auth/me` (changed) | `PaymentsInfo.upi_direct`; options buyable with either channel; `pending_order`/`open_order` include `submitted`; `rejected_order` | |
| `GET /billing/payments` (changed) | `channel`; description "Standard plan, 30 days (UPI direct, UTR 630512345678)" | |
| `GET /admin/billing/orders?channel=` (changed) | `channel` filter; `status=submitted` oldest submission first | 422 |
| `POST /admin/billing/orders/{public_id}/upi-confirm` | `UpiConfirmRequest` → `AdminOrder` | 404, 409 `upi_not_waiting`, 409 `utr_mismatch`, 409 `user_deleted`, 422 |
| `POST /admin/billing/orders/{public_id}/upi-reject` | `UpiRejectRequest` → `AdminOrder` | 404, 409 `upi_not_waiting`, 422 |
| `GET /admin/billing/upi-preview.svg` | → `image/svg+xml` (₹1, `tn=AN-TEST`, the saved ID and name) | 404 without both |
| `PUT /admin/billing/settings` (changed) | the three fields | 422 (on without both; bad ID or name) |

**Messages** (the `detail` the UI shows as is; `<email>` is the business contact email, the sentence after it is
left out without one):

| Code | Detail |
|---|---|
| `upi_unavailable` | "Paying by UPI isn't available right now. Contact <email>." |
| `upi_waiting` | "We're still checking your UPI payment of ₹2,000 (AN-7K4M-Q9TX). You can start another payment once it's confirmed or rejected." |
| `upi_paused` | "Paying by UPI is paused for your account because recent payments couldn't be confirmed. Write to <email>." (+ " Or pay online." while online payment is available) |
| `too_many_checkouts` | "Too many payment requests today. Try again tomorrow." |
| `checkout_busy` | "Many UPI payments are waiting to be checked. Try again in a few hours." (+ " Or pay online.") |
| `order_not_open` | expired or cancelled: "This payment request has expired. If you paid, start a new payment for the same plan and enter this UTR there." (cancelled: "…was cancelled…"); submitted, paid, rejected, review: "This payment was already sent for checking." |
| `utr_used` | "This UTR is already used for another payment. Check the 12-digit number in your UPI app." |
| `too_many_attempts` | "Too many tries today. Try again tomorrow." (+ " Or write to <email>.") |
| `upi_not_waiting` | "This payment isn't waiting for a check: it is <status word>." |
| `utr_mismatch` | "The UTR on this payment is 6305 1234 5678. Check it again." |
| `user_deleted` | "The account was deleted. If you received this payment, pay it back by UPI, then reject it here." |

Rejection messages (`UpiRejection.message`, the customer's; amounts with `format_inr`, the UTR grouped 4-4-4): as in
`SPEC.md` (Billing, Direct UPI).

## 7. The payment request and the QR codes (`services/billing/upi.py`)

```python
def request_uri(upi_id: str, payee_name: str, amount_paise: int, note: str) -> str:
    assert amount_paise > 0 and amount_paise % 100 == 0
    return (f"upi://pay?pa={upi_id}&pn={quote(payee_name, safe='')}"
            f"&am={amount_paise // 100}.00&cu=INR&tn={note}")
```

- `pa` verbatim (validated, so nothing to encode, and the `@` stays); `pn` percent-encoded (`%20` for a space, `&` as
  `%26`); `am` whole rupees with `.00` (every amount is whole rupees: prices are, credits are floored to rupees, a
  charge is at least ₹1); `cu=INR`; `tn` the reference (`A-Z 2-9 -`, nothing to encode). Parameter order as shown.
  Exact strings are unit-tested.
- The order's request is rendered **from the stored order only** (`upi_vpa`, `upi_payee_name`, `amount_paise`,
  `upi_reference`), never from request input.
- **SVG**: `segno.make(uri, error="m", micro=False)`, saved as SVG with `scale=1`, `border=4`, `dark="#000"`,
  `light="#fff"` (a white background in the file itself, so a dark theme can't make it unscannable),
  `xmldecl=False`, `omitsize=True` (a `viewBox`, no width or height: the page sizes it), no title or class; no
  script, so even opened directly it does nothing. **PNG**: `scale=10`, `border=4`. Both with `Cache-Control:
  private, no-store`, `X-Content-Type-Options: nosniff`, `Content-Security-Policy: default-src 'none'; style-src
  'unsafe-inline'; sandbox`; the PNG with `Content-Disposition: attachment; filename="upi-<reference>.png"`.
- `segno==1.6.6` in `backend/requirements.in` (a comment: "QR codes of direct UPI requests (services/billing/upi.py)"),
  both locks regenerated with the exact commands of the lock headers and `--exclude-newer 2026-09-30T12:00:00Z`.

## 8. Behaviour in the backend

### 8.1 Starting an order (`upi.start`)

1. Admin → 403; not `upi.ready` → 503 `upi_unavailable`; unknown plan → 404 (rollback first, as `checkout.start`).
2. `lock_user`; `checkout.context` and `checkout.options` (with `can_pay` = online or direct UPI, 8.6); the option of
   `body.kind`, else 409 `checkout_unavailable` with its reason.
3. A `submitted` direct-UPI order of the user → 409 `upi_waiting`. Three of the user's direct-UPI orders with
   `upi_rejection` set and `upi_decided_at` within 30 days → 409 `upi_paused`.
4. Reuse: an open direct-UPI order of the same plan, kind, amount and `upi_vpa` = the current `upi_id`, with at least
   an hour left → commit, 201 with it.
5. Caps, counted in the database under the lock: direct-UPI orders the user made since IST midnight ≥ 5 (3 when
   `paid.has_paid` is false) → 429 `too_many_checkouts`, `Retry-After` to IST midnight; for an unpaid user,
   `submitted` direct-UPI orders of unpaid users ≥ 20 → 503 `checkout_busy` (`Retry-After: 3600`; Redis
   `billing:upi-unpaid-busy` for an hour, which the billing check reports), then the network's `upi_unpaid` limit
   (429 `too_many_attempts`, counted last, so only an order that is made counts). A `now` option whose credit comes
   from a test-mode period → 409 `checkout_unavailable` (before the caps).
6. Insert the order (4) with a fresh reference (insert in a savepoint; an `IntegrityError` on the reference draws a
   new one, at most 5 times); cancel the user's other `pending` orders by a conditional update returning their
   Razorpay link ids (events `order.cancelled` with `replaced_by`); event `order.created` (`channel`, `reference`,
   plan, kind, amount; not the UPI ID); commit; then cancel those links at Razorpay, best effort (as
   `checkout._cancel_link`). 201 `OrderView`.

### 8.2 The UTR (`upi.submit_utr`)

1. The caller's own direct-UPI order (else 404). `INCR rl:upi-utr:<user>:<IST date>` (expires after 2 days); over 10
   → 429 `too_many_attempts`.
2. `lock_user`, the order `FOR UPDATE` (`populate_existing`). `submitted` with the same UTR → commit, 200. Not
   `pending`, or past `expires_at` → 409 `order_not_open` (8.5 writes `expired` when it is).
3. A payment that didn't fail whose `offline_reference` carries the UTR (`payments.reference_utrs`: its digits when
   there are 12, or a run of exactly 12 digits; a bank transfer the admin recorded) → 409 `utr_used`. In a
   savepoint: `upi_utr`, `upi_submitted_at = now`, `status = "submitted"`, flush; an `IntegrityError` (the partial
   index) → 409 `utr_used`.
4. Event `upi.submitted` (source `user`, actor the username, detail reference, UTR, amount). When the mailer is
   configured and the settings have a contact email: `outbox.enqueue(db, "upi_submitted", to=<contact email>,
   params={"order_id": order.id})`. Commit; `outbox.wake()`. 200 `OrderView`.

### 8.3 Confirming (`upi.confirm`) and the shared apply

1. The order by public id (404); not direct UPI or not `submitted` → if `paid` with this UTR: 200 (no change), else
   409 `upi_not_waiting`. `body.utr != order.upi_utr` → 409 `utr_mismatch`. `user_id` null → 409 `user_deleted`.
2. `lock_user`, the order `FOR UPDATE`, re-check step 1 under the lock (a racing confirm sees `paid` and returns); a
   recorded payment carrying the UTR (8.2 step 3) → 409 `utr_used`. Recording a bank transfer whose reference
   carries the UTR of a `submitted`, `paid` or `review` order is refused the same way (409 `utr_used`).
3. Insert the payment (5.1). Apply with the code `apply_payment` uses after its checks, factored out of
   `payments.apply_payment` into one private function both call (no copy): kind `period` → `periods.add_period` at
   max(now, paid through), `days` 30, mode `offline`, basis the amount; kind `now` → a credit from a test-mode
   period → `_review`; `_credit_fell` over the replaced period's own window (`entitlements.of_period`) with usage up
   to `upi_submitted_at` in full and after it beyond the share of the period the wait took, and the time left as
   quoted, then `periods.start_now(mode="offline")` (its `mode` literal gains `"offline"`); `PeriodChanged` because
   the replaced period ended by itself (unrevoked, `ends_at` = `replaces_ends_at` ≤ now, plan as quoted) →
   `add_period` as for a `period` order with basis the amount paid; any other `PeriodChanged`, or a fallen credit →
   `_review` (the existing reasons). Razorpay's `_mismatch` (mode, currency, Razorpay order) doesn't apply: the admin attests the
   amount.
4. `_paid` (receipt, `payment.captured`, `period.granted` or `period.started_now`), then `upi_decided_at`,
   `upi_decided_by`; event `upi.confirmed` (actor, reference, UTR, amount, the outcome). Commit; `outbox.wake()`.

### 8.4 Rejecting (`upi.reject`)

Under `lock_user` (when the user exists) and the order lock: `rejected` already → 200; not `submitted` → 409
`upi_not_waiting`. Set `status = "rejected"`, `upi_rejection`, `upi_rejection_note`, `upi_decided_*`; event
`upi.rejected` (actor, reason, note, reference, UTR); when the user has a verified email and the mailer is
configured, `outbox.enqueue(db, "upi_rejected", to=user.email, canonical=user.email_canonical, user_id=user.id,
params={"order_id": order.id})`. Commit; wake.

### 8.5 Expiry, views and the loop

- `upi.expire_due()` in `BillingLoop.tick`: a conditional update of `pending` direct-UPI orders past `expires_at` to
  `expired` returning their ids, one `order.expired` event each. `BillingLoop.wanted()` goes (the loop always runs).
- `checkout.order_view`: `channel`; `upi` (`uri` only while `pending` and not expired; `rejection` with its message);
  a `pending` direct-UPI order past `expires_at` reads `expired`; `_status` knows the new statuses.
  `checkout.admin_order`: `upi_decided_by`.
- `entitlements._pending_order` and `summary.billing_status`'s `open_order`: the newest open order (4).
  `entitlements.plan_access`: `rejected_order` (7 days, no newer order of any channel).
- `payments.payment_view`: `channel` from its order; the description gains " (UPI direct, UTR 630512345678)" as bank
  transfers gain " (bank transfer, ref …)".
- `cancel_user_links` (user deletion) already cancels `pending` orders, direct UPI included; `submitted` ones stay.

### 8.6 Who can buy

`business.upi_ready(settings)`; `business.payments_info`: `upi_direct` = ready (the admin too, as a preview).
`checkout.context.can_pay` = `business.can_pay(user)` or ready (never for the admin: options stay empty). The reason
when neither is available stays "Online payment isn't set up yet." / "Paid plans open soon. Check back in a few
days." `checkout.period_days` becomes per caller: 1 only when the caller pays online under test keys (a tester), else
30 (`SPEC.md`, Billing, Buying: a tester under test keys sees 1-day dates even for direct UPI, which buys 30).

### 8.7 Receipts, CSV, export, summary, monitor, mail

- **Receipts** (`receipts.py`): for method `upi` with mode `offline`: the line's detail "UPI (direct), UTR
  630512345678", the PDF's method "UPI (direct)", the reference row "UPI UTR"; the email facts' method "UPI
  (direct)". Live series, as offline payments.
- **CSV**: method `upi_direct` for those payments, the UTR in the reference column, whose header becomes "Razorpay
  payment id, bank reference or UTR".
- **Data export** (`billing.json`): each order's `channel` and, for direct UPI, `reference`, `utr`, `status`,
  `submitted_at`, `decided_at`, `rejection`.
- **Needs attention** (`summary.attention`): one `upi_to_check` item while orders are `submitted`: message "2 UPI
  payments to check" ("1 UPI payment to check"), detail "The oldest: ₹2,000 from maya.chen, sent 1 Nov, 15:10 IST
  (over a day ago). Find each credit in your bank or UPI app before you confirm it.", `count`, `order_public_id` of
  the oldest. `payments_not_configured` stays as is.
- **Monitor** (`summary.problems`, `ops/checks.check_billing`): "1 UPI payment waiting over 24 hours" (counts only);
  the check runs while Razorpay **or** direct UPI is set up; its `ok` detail: "Payments in live mode; direct UPI on;
  nothing needs attention" (the parts that apply).
- **Mail** (`services/mail`): kinds `upi_submitted` (class `billing`, priority 1, `not_after` 1 day) and
  `upi_rejected` (class `billing`, priority 2, 3 days; the worker checks `email_receipts`, as for `receipt`); the
  worker re-reads the order when it sends and sends nothing when the order is no longer `submitted` (for the
  first) or `rejected` (for the second). Plain text and the HTML frame (signup-auth visual brief):
  - `upi_submitted`, subject "UPI payment to check: ₹2,000 (AN-7K4M-Q9TX)": "maya.chen says they paid ₹2,000 by UPI
    for Standard, 30 days. / UTR: 6305 1234 5678 / Reference: AN-7K4M-Q9TX (in the payment's note, if their app
    kept it) / Before you confirm, open your bank or UPI app and find a credit of exactly ₹2,000 with this UTR. /
    Confirm or reject it: <first PUBLIC_BASE_URLS entry>/admin#billing/upi". Footer: "You get this email because
    your address is the contact email in Admin → Billing → Business & payments."
  - `upi_rejected`, subject "We couldn't confirm your UPI payment": the rejection's message and note, "Open the
    payment: <origin>/billing/return?order=<public_id>", the contact line, the receipts footer.

### 8.8 nginx (`frontend/nginx.conf`, BACKEND's)

`location = /api/billing/upi/checkout` with `limit_req zone=billing_checkout burst=5 nodelay;` and the same proxy
lines as `/api/billing/checkout`. No CSP change: the QR is an `<img>` from `/api/…` (`img-src 'self'`) and the app
link a plain `<a href="upi://…">` (navigation isn't restricted by the CSP). The `$xsite_write` guard already covers
every new `POST`.

## 9. Security and privacy

- **IDOR**: customer endpoints use the caller's own order (404 otherwise, also for a Razorpay order on the UPI
  endpoints); the admin endpoints sit behind `get_current_admin`.
- **Double apply**: user lock → order lock → status re-check → the payment insert → `applied_at`; a second confirm
  returns the order unchanged; a racing reject sees `paid` and answers 409.
- **UTR reuse**: the partial unique index (submission) is the database's guarantee; the UTR echo on confirm stops a
  confirm of the wrong order. A customer typing someone else's UTR seen on a screenshot: the owner's check of the
  amount and, where the bank app shows it, the note (the reference differs) is the defence; the confirm dialog says
  so; the real payer then gets `utr_used` and writes to the owner. A bank transfer and a direct-UPI payment can't
  carry the same UTR (8.2, 8.3).
- **Amount and plan changes**: the amount and snapshot are fixed at the quote; a retired plan or a new price changes
  nothing for an open order; `now` orders follow D13; a payment of a different amount is rejected (`amount_differs`)
  and paid back outside the app (the app records only money it confirmed).
- **Rate limits**: D7 (with the network's `upi_unpaid` limit), plus nginx's checkout zone on the UPI checkout.
- **Logging**: never a UTR, UPI ID or payee name in logs; events hold the reference, UTR and amount (they are the
  audit trail), never the payer's name or UPI ID (the app never has them: the admin dialog doesn't ask for them and
  the note's hint says not to type them). Event detail keys must not be named `vpa` or `upi` (audit's payer filter
  drops them): use `reference`, `utr`, `upi_id`.
- **Injection**: the request URI is built only from validated settings and the stored order; the frontend uses
  `upi.uri` as an `href` only when it starts with `upi://pay?`; the note and names render as text.
- **Settings**: changing the UPI ID moves future money, so it is audited with before and after values; open orders
  keep theirs (D16).

## 10. UI (frontend; on PBV's design system, no new tokens)

Prefixes: `plans-upi-` (checkout and the order page), `billing-` (banners, Settings), `admin-upi-` (Admin). New
icon `QrIcon` (24 × 24, stroke 1.75: `<rect x="3.5" y="3.5" width="6.5" height="6.5" rx="1"/><rect x="14"
y="3.5" width="6.5" height="6.5" rx="1"/><rect x="3.5" y="14" width="6.5" height="6.5" rx="1"/><path d="M14
14h2.5v2.5H14zM18 18h2.5v2.5H18zM14 20.5h2.5M20.5 14v2.5"/>`); existing ones reused: `Copy`, `Check`,
`ArrowUpRight` (Open UPI app), `Download` (Save QR image), `Phone`, `Clock`, `Shield`, `AlertCircle`, `Info`. Phones
= under 800 px or `(pointer: coarse)` (`useMediaQuery`). Copy buttons use `navigator.clipboard.writeText` and an
`aria-live="polite"` "Copied <what>" for 2 s; without clipboard access they select the text.

### 10.1 Checkout (`/plans/checkout/:code`), the summary (PBV 5.3)

- With `upi_direct`: the primary 48 px `btn-primary plans-pay` "**Pay ₹2,000 with UPI**" (`QrIcon`), under it 13.5
  `--text-3` "From any UPI app, straight to <business name>. Your plan starts once we've checked the payment,
  usually within a few hours." Busy: spinner, "Preparing your UPI payment…". Success: navigate to
  `/billing/return?order=<id>` (same tab, router push).
- With online payment too: a hairline with "or" (13 `--text-3`), then `btn plans-pay-online` (44 px, full width,
  `LockIcon`) "Pay online · ₹2,000", under it "UPI, cards, netbanking or wallets on Razorpay's secure page. Starts as
  soon as you've paid.", the UPI-limit note (PBV 5.3) only here, the methods pills only here, and the email notice
  (`EmailNeededNotice`) only for this button: "Paying online needs a confirmed email." with "Add email". Only online:
  today's summary.
- The secure line for UPI (shield, `--success`): "Your money goes straight to <business name>'s bank account. We
  never see your UPI PIN or bank details." The consent line stays under the buttons.
- Phones' pay bar (PBV 5.4): its button is "Pay with UPI" when direct UPI is available (else today's); the summary
  hides its own primary button there but keeps "Pay online".
- Errors under the pressed button (`error-text`, focused): by code, with actions: `upi_waiting` → "Open it" (after
  `refresh()`, to the order of `Me.plan.pending_order`); `checkout_unavailable` → "Back to plans"; others the detail.

### 10.2 The order page for direct UPI (`BillingReturnPage`, `channel === "upi_direct"`)

No `?refresh=true` polling: the page's first load asks with it, as for any order (the backend never calls Razorpay for
a direct-UPI order), then a direct-UPI order takes over. `pending`: no polling. `submitted`: `GET
/billing/orders/<id>` every 30 s while the tab is visible, for 30 minutes, then "Check again". Focus moves to the
title on each state change; the main is `aria-live="polite"` in every state but `pending` (the panel isn't announced
while the customer types).

**Pending, 1440 px** (`.plans-main-upi`: max 760, padding-top 48): title serif 32/500 "Pay ₹2,000 by UPI", sub 15
`--text-2` "Standard plan, 30 days · this request is open until 21 Oct, 14:58 IST" (a `now` order: "Standard plan, 30
days from when it's confirmed · …"). Card `.plans-upi` (`--surface`, 1 px `--border`, `--radius-xl`, `--shadow`,
padding 28) as a grid `232px 1fr`, gap 32:

- Left `.plans-upi-qr`: a 232 px tile (1 px `--border`, `--radius-lg`, padding 10) with `<img
  src="/api/billing/orders/<id>/upi-qr.svg" width="212" height="212" alt="QR code: pay ₹2,000 to Asha Rao by
  UPI">`; under it 13.5 `--text-3` "Scan with any UPI app" and a ghost xs "Save QR image" (`Download`, an `<a
  download>` of `upi-qr.png`, saved as `upi-AN-7K4M-Q9TX.png`).
- Right: `ol.plans-upi-steps` (each a 24 px numbered circle as `.plans-step-num`, 15/1.45): 1 "Open any UPI app
  (Google Pay, PhonePe, Paytm, BHIM or your bank's) and scan the code." 2 "Check that it shows **Asha Rao** and
  **₹2,000**, then pay with your UPI PIN." 3 "Enter the 12-digit UTR from the payment's details below."; then
  `dl.plans-upi-facts` (rows, hairlines, 14.5; term `--text-3`, value 15/600 tabular, a ghost xs copy button with the
  name "Copy UPI ID" / "Copy amount" / "Copy note"): **Pay to** "Asha Rao" over the UPI ID in 14 px mono; **Amount**
  ₹2,000 (copies "2000"); **Note** AN-7K4M-Q9TX with 13 `--text-3` "Add it as the note if your app asks; it helps
  us find your payment."
- Under the grid, `.plans-upi-utr` (a top hairline, padding-top 22): label 15/600 "UTR (12-digit UPI reference
  number)"; `input.input` mono 18 px, letter-spacing 0.08em, `inputmode="numeric"`, `autocomplete="off"`,
  `enterkeyhint="send"`, `spellcheck="false"`, `maxlength="40"` (room for a pasted label, "UPI Ref No: 6305 1234
  5678", and for a longer wrong number, which the echo then names), placeholder "6305 1234 5678", `aria-describedby`
  the error (while shown), the hint and the echo; hint (`field-hint`) "12 digits, numbers only. Your UPI app shows it
  in the payment's details as UTR, UPI Ref No. or UPI transaction ID."
- **What was typed stays as typed**; nothing is dropped from the field. The page reads it without a leading label
  ("UTR", "UPI Ref No.", "Ref No:", "RRN", "UPI transaction ID", "Txn ID", with or without a colon), spaces and dashes,
  and keeps every digit, so a wrong number shows. The echo (13.5 `--text-3`): "8 of 12 digits" while short;
  "6305 1234 5678 ✓" at 12; otherwise the problem with `AlertCircle`: "That's 14 digits. A UTR has exactly 12: look
  for UPI Ref No. or UTR in the payment's details." (more digits), "This looks like your app's own transaction ID.
  Look for the 12-digit UTR or UPI reference number." (letters), "A UTR has only the digits 0 to 9." (anything
  else). Screen readers hear "12 digits entered" when the twelfth digit arrives,
  and the count or the problem when they leave the field, never on each key.
- Primary `btn-primary` "I've paid · Send UTR" (`aria-disabled` until 12 digits; pressed early, the problem, or "Enter
  all 12 digits." while short, becomes the error under the field, focused, in place of the echo's problem). Only the
  12 digits are sent; the server takes exactly 12 ASCII digits after dropping whitespace (`UtrSubmission`, 422
  otherwise). Under it, ghost sm "Cancel this request" and, while online payment is available, a link "Pay online
  instead" (to the checkout). Errors under the field, focused (`utr_used`, `order_not_open` then reload,
  `too_many_attempts`).
- `.plans-result-note` (PBV 6.1, `Info`) under the card: "Pay only once. If your app says the payment failed or is
  pending, don't pay again straight away: check your bank balance. Money taken for a failed payment comes back by
  itself, usually within a day. If the app later shows it as successful, enter its UTR here."

**Pending, 390 px**: title 27; card padding 20 16, one column, in this order: primary full-width 48 px `a.btn
btn-primary` "**Open UPI app**" (`ArrowUpRight`; `href` = `upi.uri`), under it 13 `--text-3` "Choose your UPI app,
check the name and ₹2,000, and pay."; on an iPhone (user agent) also "On iPhone this opens one UPI app. If it isn't
yours, copy the UPI ID below or save the QR image."; then "Or pay to this UPI ID" with the facts (term above value,
copy buttons 40 px); a toggle `button[aria-expanded]` "Show QR code" revealing the 200 px tile, "Save QR image" and
"To pay from this phone, save the image and open it from your UPI app's scanner (Upload from gallery)."; the UTR
block full width with a full-width button; the note.

**Submitted** (both widths; PBV 6.1's result card): tone info, mark `Clock`; title "We're checking your payment";
text "We'll match your UTR with the ₹2,000 that reached **Asha Rao**'s account. This usually takes a few hours during
the day (Indian time). Your Standard plan starts as soon as it's confirmed."; slip: Plan "Standard, 30 days" (a `now`
order adds "Starts when confirmed, in place of your current period"), Amount, Reference, UTR (grouped), Sent ("Today,
15:10"); note "Please don't pay again." plus "We'll email you when it's confirmed." with a verified email and
`email_reminders`, else "You can close this page: you'll see it here and in Settings → Plan & billing."; actions "Back
to plans", primary "Back to chat" (with access) or "Done" (to `/plans`); foot line "Wrong UTR? Write to <contact
email> with the right one."

**Paid**: PBV 6.2's paid state; slip "Paid with" "UPI (direct)"; foot line "Your receipt is in Settings → Plan &
billing." (no Razorpay sentence).

**Rejected**: tone danger, `AlertCircle`; title "We couldn't confirm your payment"; text `rejection.message`; the
note (when given) in a `.plans-result-note` "Note from <business name>: …"; slip Amount, Reference, UTR; actions
"Back to plans", primary "Start a new payment" (to `/plans/checkout/<plan code>?kind=<kind>`); the contact line.

**Expired / cancelled** (no UTR): tone neutral, `Clock`; title "This payment request has expired" ("…was
cancelled"); text "No UTR was sent, so your plan hasn't changed. If you already paid, start a new payment for the
same plan and enter that payment's UTR there."; actions "Back to plans", primary "Start a new payment".

**Review** (a confirmed `now` order that couldn't apply): PBV 6.2's review state.

### 10.3 Banners and notices

- `PendingCheckoutBanner` (chat) and `PendingCheckoutNotice` (`/plans`, Settings) by `channel` and `status`:
  direct UPI `pending`: info "Finish your UPI payment for **Standard**: send its UTR." + `btn-sm` "Continue" (+ ghost
  sm "Cancel" in the notice); `submitted`: info with `Clock` "We're checking your UPI payment of ₹2,000 for
  **Standard**." + `btn-sm` "Details". Razorpay: as today.
- `Me.plan.rejected_order`: a danger notice "We couldn't confirm your UPI payment of ₹2,000 for **Standard**." +
  `btn-sm` "See why" (to the order). The chat banner is dismissible for the session
  (`sessionStorage["rag.upiRejectedSeen"]` = the public id); `/plans` and Settings always show it.

### 10.4 Settings → Plan & billing

The notices of 10.3 above the plan card; the payment history's second line ends "· UPI (direct)" for
`channel === "upi_direct"`; methods everywhere through one `methodLabel(method, channel)`.

### 10.5 Admin → Billing → UPI (`#billing/upi`)

The segmented control: Overview, **UPI**, Payments, Orders, Business & payments (the label "UPI · 2" while UTRs wait).
Needs attention's `upi_to_check` row: warning tone, action "Check payments" → `billing/upi`.

- **Card "UPI payments to check"** (`admin-card`, a warning count badge): a warning `ui-notice` (`Shield`) "Before you
  confirm, open your bank or UPI app and find a credit of **exactly the amount** with **this UTR**. If your app shows
  the payment's note, it should be the reference. Confirming starts the plan and issues a receipt."
- **1440**: `admin-table` (`GET /admin/billing/orders?status=submitted`): Sent ("1 Nov, 15:10" over 13 `--text-3`
  "2 h ago"; over 24 h "Waiting over a day" in `--warning`), User (`UserCell`), Plan ("Standard" over "30 days" or
  "Upgrade"), Amount (`.num` 600), Reference (`admin-code`; "to old.id@ybl" under it when `upi.upi_id` differs from
  the current setting), UTR (`admin-code`, grouped, copy), and sticky actions `btn-sm btn-primary` "Confirm…" and
  `btn-sm` "Reject…".
- **390**: each row a card `.admin-upi-row`: the amount serif 22 with the plan, the user line, Reference and UTR rows
  with copy, Sent; the two buttons side by side, full width.
- Empty: `--text-3` "Nothing to check. New UPI payments show up here and in Needs attention."; direct UPI off: "Direct
  UPI is off. Turn it on in Business & payments." (a link).
- **Card "Recent UPI payments"** (`?channel=upi_direct`, 20 newest): Created, User, Plan, Amount, Status (badges: info
  "Waiting for UTR", warning "To check", success "Paid", danger "Rejected" with the reason as a 13 px second line,
  neutral "Expired"/"Cancelled", warning "Review"), "Open" (the order dialog).

**Confirm dialog** (`Modal` 520, "Confirm UPI payment"): lead "Confirm only after you've found this payment in your
bank or UPI app."; facts: Amount (serif 28), UTR (mono 17, grouped, copy), Reference, Paid to (the order's UPI ID),
From (user and email), Plan ("Standard, 30 days" or "Upgrade to Professional: replaces the current period"), Sent; a
required `ui-check` "I found a credit of exactly ₹2,000 with UTR 6305 1234 5678."; footer ghost "Cancel", primary
"Confirm and start the plan" (`aria-disabled` until ticked; busy). Success: close, flash "Confirmed ₹2,000 from
maya.chen. Their plan has started." (status `review`: "Recorded ₹2,000 from maya.chen. It needs your review: <reason>").
Errors as `error-text` in the dialog.

**Reject dialog** (`Modal` 520, "Reject UPI payment"): lead "The customer sees why and can start a new payment.";
`fieldset` "Why" with radios "No payment with this UTR arrived" (`not_received`), "The amount is different"
(`amount_differs`), "This UTR belongs to another payment" (`utr_used`), "Something else" (`other`), each with the
customer's sentence as a 13 px `--text-3` preview; for `amount_differs` a warning notice "Send what they paid back to
the payer by UPI from your bank app."; "Note to the customer" `textarea` (≤ 200; required for "Something else"; hint
"The customer reads this. Don't include anyone's bank details."); footer ghost "Cancel", `btn-danger` "Reject
payment".

### 10.6 The order dialog, Orders and Payments

Orders: the new badges ("To check" warning, "Rejected" danger), the channel as a 13 px second line under Kind
("Period · UPI"); the order dialog for direct UPI shows Reference, Paid to, UTR, Sent, Decided (who, when, the reason
and note) and, while `submitted`, "Confirm…" and "Reject…". Payments: Method "UPI (direct)"; the dialog shows "UTR"
(`admin-code`, copy) where Razorpay payments show their id; Fee "—".

### 10.7 Business & payments: the "UPI payments" card

A card (`admin-card`) beside Razorpay's (one column under 940): title "UPI payments", sub "Customers pay your UPI ID
directly. You check each payment in your bank app, then confirm it here."; a `switch` "Accept UPI payments"; "UPI ID"
(mono, placeholder "yourname@okhdfcbank", hint "The UPI ID of the bank account the money should reach.", lowercased
and checked as you type with the server's pattern); "Payee name" (`maxlength` 50, hint "As your bank shows the
account's name: your customers' UPI apps show your bank's name for this UPI ID."); primary sm "Save" (the whole
`BillingSettings`, as the business form does, with the latest saved values of the other fields). After saving, with
both fields set: "Test it before customers use it" with the ₹1 QR (`<img src="/api/admin/billing/upi-preview.svg?v=<the
saved UPI ID and name>">`, a 160 px tile) and "Scan this with another phone and pay ₹1. Check that your app shows your
bank's name for this UPI ID and that ₹1 reaches your account. Try Google Pay, PhonePe and Paytm." Footnotes:
"Requests already made keep the UPI ID they were made with for up to 24 hours." and "Email alerts: on, to <contact
email>" / "off: set the contact email in Business details and the mail settings". The going-live checklist gains the
instruction row "Test direct UPI with ₹1 from Google Pay, PhonePe and Paytm" while it is on.

### 10.8 Users

`UserPlanSection`'s orders use the same badges; deleting a user who has a `submitted` UPI order adds to the
confirmation: "maya.chen has a UPI payment of ₹2,000 waiting for your check. Confirm or reject it first."

### 10.9 The refund dialog for direct UPI

The warning notice becomes "Pay ₹2,000 back to the payer by UPI from your bank app first (the payment with UTR 6305
1234 5678), then record it here. No money moves through the app."; the button "Record refund of ₹2,000"; the reason
field's hint "For example the UTR of your refund payment."

### 10.10 Accessibility and states

Copy buttons have names; the QR has an `alt` naming payee and amount; "Open UPI app" is a link; the UTR field has its
label, hint and live echo; errors `role="alert"` and focused; dialogs keep focus, the tick is required; radios in a
`fieldset` with a legend; touch targets 40 px or more on phones; the light and dark themes and the five accents come
from the existing tokens (the QR's own white background keeps it scannable in dark mode); `prefers-reduced-motion`
as PBV 15. Loading: the result card's skeleton (PBV 6).

### 10.11 Policy pages (keep `<!-- draft -->` and the files' style)

- `refunds.md`: in "How refunds are made": "A payment made directly by UPI is refunded by UPI to the UPI ID it came
  from, usually within 3 working days." In "When we refund": "a UPI payment we couldn't match to your order (for
  example a different amount) is sent back to you by UPI."
- `terms.md` "Paying": "through Razorpay … or directly by UPI to our UPI ID: you pay from your UPI app and enter the
  payment's 12-digit UTR here, and your plan starts when we've confirmed the payment, usually within a few hours
  during the day (Indian time). Paying online needs a confirmed email address, because your receipt goes there;
  paying by UPI doesn't (your receipt is always in Settings → Plan & billing)."
- `privacy.md` billing records: "For a direct UPI payment we keep the 12-digit UTR you enter and our order reference.
  Our bank or UPI app shows us the name and UPI ID you paid from, as for any UPI payment; we don't copy them into
  AskNuvexusDoc." Razorpay's processor paragraph: "Direct UPI payments don't go through Razorpay."
- `shipping.md`: "Paid directly by UPI: your plan starts when we confirm the payment, usually within a few hours
  during the day (Indian time). If it hasn't started within 24 hours, email <support address> with the UTR."
- `contact.md` "For a payment": "… or, for a UPI payment, the UTR and the reference (AN-…)."

## 11. Owner setup (for the integrator to walk through with the owner)

1. **Choose the UPI ID**: the one of the bank account the money should reach. A business current account's UPI ID,
   or a business app's (Google Pay for Business, PhonePe Business, Paytm for Business, BharatPe) shows a business
   name and notifies each payment; a small-merchant ID of that kind is limited to ₹10,000 a payment, ₹25,000 a day
   and ₹1,00,000 a month received (2.2). A personal savings account's UPI ID works too, but banks may ask about many
   business payments into it. Ask the bank and the accountant (14.1).
2. **Payee name**: as the bank shows the account's name (customers see the bank's name anyway, 2.3).
3. **Contact email** in Business details, and the mailer, for the "UPI payment to check" emails.
4. **Test before turning it on**: save the ID and name with the switch off; from another phone scan the ₹1 test QR
   with Google Pay, PhonePe and Paytm; check the name shown and the ₹1 credit and where the UTR and the note appear in
   the owner's bank app. Then, switched on, a `smoke-*` user buys Starter by UPI from a phone ("Open UPI app" on
   Android and on an iPhone if available), sends the UTR, the owner confirms, checks the receipt, refunds by UPI and
   records the refund (access ends).
5. **Daily**: Admin → Billing → UPI (or the email); for each row find the credit by amount and UTR, then Confirm or
   Reject. A payment of a different amount: reject "The amount is different" and send it back by UPI.

## 12. Tests

**Backend unit** (`backend/tests/`, users `smoke-*`; SQLite and, where marked, Postgres):

- `test_upi_schemas.py`: UTR (spaces, 11, 13, letters, Devanagari digits, non-strings), UPI ID (case, length,
  handles, two `@`), payee name (charset, collapse, 50), settings on without both, reject note for `other`, extra
  fields refused.
- `test_upi_request.py`: `request_uri` exact strings (space, `&`, apostrophe in `pn`; `am` formatting; no `tr`/`mc`);
  the reference alphabet and format; a reference collision retried; the SVG (no script, `viewBox`, white
  background) and PNG (signature, headers); QR endpoints 404 for another user's, a Razorpay, a submitted or an
  expired order.
- `test_upi_checkout.py`: options buyable with direct UPI only; no email needed; `upi_unavailable`; reuse; caps (5,
  unpaid 3, `Retry-After`); the unpaid cap on waiting UTRs (orders without a UTR don't count; the billing check's
  warning) and the network's cap; `upi_paused` after 3 rejections, lifted after 30 days; a pending
  Razorpay order cancelled (its link cancelled after the commit, fake Razorpay) and vice versa; `upi_waiting` on both
  checkouts; admin 403; a tester under test keys (no credit from a test-mode period).
- `test_upi_utr.py`: submit; the same UTR again 200; `utr_used` across users and the race (two sessions, Postgres);
  a rejected UTR accepted again; expired and cancelled orders 409 with the message; the attempts cap; the outbox
  job to the contact email (none without a contact email or mailer); no UTR in the logs (`caplog`).
- `test_upi_confirm.py`: a `period` order (period from paid through, mode `offline`, 30 days, payment fields,
  receipt line and number, events, `has_paid` true, `captured_at`); idempotent second confirm; two concurrent
  confirms apply once (Postgres, as `test_billing_apply_race.py`); `utr_mismatch`; `upi_not_waiting`;
  `user_deleted`; a UTR recorded for a bank transfer refused both ways; `now` orders: usage after the submission at
  the plan's pace ignored and beyond it (the allowance used up) → review, usage before it lowering the credit →
  review, a replaced period that ended by itself → added at paid through (basis the amount paid) unless usage since
  the quote lowered the credit, a credit from a test-mode period → review, a revoked one → review.
- `test_upi_reject.py`: each reason's message (with and without a contact email), the note, events, the
  `upi_rejected` job (with a verified email; the worker skips it with `email_receipts` off), `Me.plan.rejected_order`
  for 7 days and gone after a newer order.
- `test_upi_views.py`: `pending_order`/`open_order` with `submitted`, lazy expiry, `expire_due` and its events, the
  attention item, `problems()` over 24 h, the billing check with direct UPI only, CSV (method, reference, header),
  `billing.json`, the user deletion rules, offline refund of a direct-UPI payment ending access.
- `test_migration_0005.py` (5.2); the drift guard; the deletion-completeness guard unchanged.

**Integration** (`integration/tests/test_21_upi_direct.py`, users `it-*`, the isolated stack): the admin saves the
settings (422 without both); a user's UPI checkout; the SVG and PNG served through nginx with the headers (and
`img-src` unchanged); the UTR; the `upi_submitted` mail at the fake mail server; Needs attention; confirm → `Me.plan`
active, receipt PDF text "UPI (direct)" and the UTR; a second user's same UTR → 409; a reject → `rejected_order` and
the `upi_rejected` mail; a recorded refund ends access; the billing check with direct UPI only.

**Frontend Vitest** (`frontend/src/**`, factories `makeUpiOrder`, `makeSubmittedUpiOrder`, `makeUpiInfo`,
`makeRejectedOrder`, `makePendingOrder` in `src/test/billing.ts`): checkout with both, UPI only, online only, neither;
UPI without email; the phone pay bar; error codes; the order page's states (pending at 390 and 1440 via the media
query mock, copy feedback with a mocked clipboard, the UTR field and echo, submit and its errors, polling while
submitted with fake timers, rejected, expired, paid); banners and notices; Settings history; Admin UPI list (table and
cards), confirm (tick required, the UTR sent, flash, review outcome), reject (note required for other); the business
UPI card (validation, save body, the preview); refund dialog text; `methodLabel`. Coverage thresholds as in
`vite.config.ts`.

**End-to-end** (`e2e/`, route mocks with `fakeUpiOrder`, users `e2e-*`): checkout → "Pay with UPI" → the panel →
UTR → "We're checking"; phone viewport (390): "Open UPI app" has the `upi://pay?` href; the admin's confirm and reject
dialogs; the a11y scan of the panel and the dialogs in both themes.

## 13. Work split

Two builders, in their own copies or the same tree on disjoint files, no git changes, test users `smoke-*`, `e2e-*`,
`it-*`; integration stacks only with the project name and ports their prompts give, torn down afterwards.

| Stage | Who | What |
|---|---|---|
| 0 | designer (done) | `SPEC.md` (billing), `frontend/src/api/types.ts`, `backend/app/schemas_billing.py` (additive defaults), the factories (`frontend/src/test/billing.ts`, `fixtures.ts`, `e2e/support/fakes.ts`) and the compile fixes they needed (`BillingBusiness.tsx` passes the three settings through, `billingUi.tsx` labels the two statuses, `PendingCheckoutBanner.tsx` takes `PendingOrder \| OrderView`; `makePendingOrder` in `ChatPage.test.tsx` and `PlansPage.test.tsx`; the exact-shape assertions in `BillingBusiness.test.tsx`, `test_admin_billing.py` and `test_billing_checkout.py`); the request-rule tests in `test_billing_schemas.py`; this brief |
| 1 | **BACKEND** ∥ **FRONTEND** | below |
| 2 | integrator | review, the rehearsal of 0005 on a copy of the live database, the deploy with the owner's approval (backup first, frontend recreated after the backend), a browser pass at 390 and 1440 in both themes, the owner's tests (11), README and HANDOFF |

- **BACKEND** owns `backend/**` (models, migration 0005 and the migrations README, `services/billing/upi.py` and the
  changes of section 8 in `checkout`, `payments`, `periods`, `entitlements`, `business`, `summary`, `receipts`,
  `csv_export`, `reconcile`, `refunds` (none expected), `services/export.py`, `services/mail/**`, the routers,
  `ops/checks.py`, `requirements*.{in,txt}`, the tests of 12), `integration/**` (`test_21_upi_direct.py`, the fake
  mail checks), `scripts/**` (none expected), `frontend/nginx.conf` (8.8) and contract changes after stage 0.
- **FRONTEND** owns `frontend/**` except `nginx.conf` (section 10, `src/api/client.ts`'s calls, `icons.tsx`, the
  policy pages, Vitest) and `e2e/**`.

**Frozen interfaces** (from stage 0; neither builder changes them alone):

1. The HTTP contract in `SPEC.md` (shapes, paths, status codes, error `code`s and the messages of 6, which the UI
   shows verbatim; the rejection `message` is the server's).
2. Routes the backend links to: `/billing/return?order=<public_id>` (the customer's order page), `/admin#billing/upi`
   (the admin's list), `/plans/checkout/<code>?kind=<kind>` (start again).
3. The QR paths `/api/billing/orders/<public_id>/upi-qr.svg` and `.png`, `/api/admin/billing/upi-preview.svg`.
4. The status words: `pending` "Waiting for UTR", `submitted` "To check" (admin) / "We're checking" (customer),
   `rejected` "Rejected".
5. A contract change after stage 0 is made by BACKEND in `SPEC.md` and the schemas and sent to FRONTEND, who mirrors
   it in `types.ts` in the same stage; both suites green before either reports done.

Each builder finishes with: backend `pytest --cov --cov-fail-under=94` and the drift guard; frontend `tsc --noEmit`,
`vite build`, `npm run test:coverage`; `e2e` `tsc --noEmit` (and, against an isolated stack, the new specs);
`integration/run.sh` with the new test.

## 14. Open questions

### 14.1 For the owner

1. **Which UPI ID?** A business current account's, a business app's (small-merchant limits: ₹10,000 a payment,
   ₹25,000 a day, ₹1,00,000 a month received), or a personal one (bank terms). This decides the limits; the app works
   with any of them, after the ₹1 tests.
2. **How fast will you check?** The copy promises "usually within a few hours during the day (Indian time)". Change
   it if that's not right.
3. **Refunds by UPI** within "3 working days" (refunds policy)?
4. **Pausing direct UPI** for an account after 3 rejected payments in 30 days: right number?
5. **Where the "UPI payment to check" email goes**: the business contact email (Business details). Is that the inbox
   you watch?
6. Tax and GST on these payments: for your accountant (receipts stay "Receipt", without GST, as today).

### 14.2 To check in the owner's tests (11)

- Whether Google Pay, PhonePe and Paytm open "Open UPI app" from a phone browser for the chosen ID with the amount
  filled in, or show a limit or security message (2.2); the QR scan and the copied ID are the fallback either way.
- Whether the note (`tn`) reaches the owner's bank app, and what an iPhone opens for `upi://`.
