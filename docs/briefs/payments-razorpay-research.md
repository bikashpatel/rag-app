# Research: Razorpay for prepaid plan payments

> Facts about Razorpay **as documented on 2026-10-26**, collected for the plan-selection and checkout design
> (Starter Rs.500, Standard Rs.2000, Professional Rs.5000, each a prepaid 30-day period renewed by paying again).
> Every page was read on that date, from `razorpay.com/docs`. The docs also publish a Markdown copy of each page under
> `razorpay.com/docs/build/llm-docs/<same path>.md`, and that copy was the one read in full. Section 11 lists every
> URL used. Where a fact comes from somewhere other than Razorpay's docs (Razorpay's own `checkout.js`, its Python SDK
> source, its pricing page or browser behaviour), the text says so. Nothing here is legal or tax advice. This is
> research only: the decisions belong to the payments brief.

## 0. Recommendation

| # | Recommendation | Why |
|---|---|---|
| R1 | **Use Payment Links created through the API**, one Standard link per checkout attempt (`upi_link` false). The browser leaves the app for Razorpay's page (`rzp.io`) and comes back via `callback_url` (GET, signed query). | No Razorpay JavaScript runs on our origin, so the CSP and nginx stay unchanged (1.4). UPI intent works on phones (5.1). It works on localhost before a public domain exists, because the return trip is a browser redirect and Razorpay never has to reach the server. Desktop still gets cards, netbanking and UPI. |
| R2 | **Never activate a plan because of the redirect alone.** First check the callback signature. Then fetch the link or payment from the API, and activate only when the payment is `captured` and the amount and currency match what we stored. Activation is idempotent and keyed by the Razorpay payment id. | The docs say to check that the payment is `captured` and the order is `paid` "before providing the services" (2.6). A redirect can be lost, replayed or forged. |
| R3 | **Confirm in three ways, all leading to the same idempotent activation:** the return redirect; webhooks (`payment_link.paid`, `order.paid`, `payment.captured`, `payment.failed`, `refund.*`) once a public URL exists; and a reconciler that polls open checkouts. The reconciler keeps checking until the link's expiry plus the late-authorisation window. | Webhooks need a public URL (3.4). Delivery is at least once and in no fixed order (3.3). A payment can turn successful days after the customer gave up (2.2). |
| R4 | **Call the REST API with `httpx` directly.** Don't use the `razorpay` SDK. | The flow needs about six endpoints and two HMAC checks. The SDK depends on synchronous `requests` (7.4). |
| R5 | **Keep auto-capture on** (the account default), or pin it per order with `payment.capture = "automatic"`. | A payment that is authorized but never captured is refunded automatically. Fulfilment has to wait for the capture (2.2). |
| R6 | **Don't use Hosted Checkout or S2S.** Standard Checkout (`checkout.js`) stays a possible later upgrade. If adopted, it should run on a separate payment origin that holds no session cookie (1.1). | Hosted Checkout was deprecated in April 2025 and has no UPI intent. S2S requires PCI-DSS. Standard Checkout puts unversioned third-party JavaScript on the page that holds the session. |

## 1. Integration options for a one-time payment

| Option | How it works | CSP and app impact | Phones (UPI) | Localhost before a domain | Verdict |
|---|---|---|---|---|---|
| **Standard Checkout** | The server creates an Order. The page loads `https://checkout.razorpay.com/v1/checkout.js` and opens a modal (`new Razorpay(options).open()`). A JS `handler` receives `razorpay_payment_id`, `razorpay_order_id` and `razorpay_signature`, or, with `redirect: true`, the browser POSTs them to `callback_url`. | Needs `script-src`, `frame-src`, `connect-src` and `img-src` exceptions for Razorpay (1.1). The script runs with full access to our page. A POST `callback_url` comes from another site, so nginx's cross-site write guard refuses it (`$xsite_write` → 403). | Intent on mobile web (PhonePe and Google Pay, Chrome only), QR on desktop | Works in test mode with the JS handler. Live mode needs the domain whitelisted. | Best UX, worst isolation |
| **Payment Links (API)** | The server calls `POST /v1/payment_links` and redirects the browser to `short_url`. After payment, Razorpay redirects to `callback_url` with GET and signed query parameters. | None: it is a top-level navigation away and back. The GET callback passes nginx's write guard. | The docs list Payment Links as supporting UPI intent on mobile web | Expected to work, since the return is a browser redirect (open question 10.1) | **Recommended** |
| **Hosted Checkout** | A form POST to `https://api.razorpay.com/v1/checkout/embedded`. The result comes back as a POST to `callback_url`, and a cancel as a GET to `cancel_url`. | Needs `form-action` for `api.razorpay.com`. Its POST callback is refused by the cross-site guard. | "We do not support EMI, Offers and UPI intent on Hosted Checkout." | n/a | **Deprecated**: "Hosted Integration has been deprecated and is no longer supported" (Payments changelog, Apr 2025) |
| **S2S (JSON / Redirect API)** | The server collects the payment details and calls Razorpay directly. | n/a | Intent and QR have to be built by hand. | n/a | "PCI-DSS certification is required for all entities seeking to store, process, and transmit cardholder data." Not suitable. |

Payment Pages and Payment Button are no-code products and not a fit.

### 1.1 Standard Checkout details

- **Script:** `https://checkout.razorpay.com/v1/checkout.js`. Options: `key`, `amount` (paise), `currency`, `name`, `order_id` (mandatory), `prefill`, `notes`, `theme`, `modal.ondismiss`, `timeout` (seconds), `retry`, `config.display` (for example, hiding UPI Collect), `callback_url` and `redirect`. "Razorpay Checkout is not supported on the Internet Explorer browser."
- **Handler vs callback_url:** "The callback_url Checkout option is only for WebView and redirect flows. For standard web integrations, use the handler function … and webhooks for server-side confirmation." The `callback_url` domain must be allowlisted.
- **CSP:** Razorpay's docs **do not publish** the origins Checkout needs. A web search found no official page. Reading the live `checkout.js` on 2026-10-26 (not documentation, so it can change at any time) shows:
  - an iframe from `https://api.razorpay.com`, with `https://api-dark.razorpay.com` as an alternative environment;
  - images from `https://cdn.razorpay.com`;
  - telemetry to `https://lumberjack.razorpay.com` and `https://lumberjack-cx.razorpay.com` (XHR and `sendBeacon`);
  - references to `https://express.razorpay.com` and `https://checkout-static-next.razorpay.com`.

  So at minimum it needs `script-src https://checkout.razorpay.com`, `frame-src https://api.razorpay.com`, `img-src https://cdn.razorpay.com` and `connect-src` for the telemetry hosts.
- **Supply-chain fact:** the script URL is unversioned. The response on 2026-10-26 had `Last-Modified: Mon, 26 Oct 2026 10:24:50 GMT` and `cache-control: max-age=120`, so the file changes often and can't be pinned or protected with Subresource Integrity. Any script on our origin can call `/api/*` with the user's cookie (httpOnly stops it from reading the cookie, not from using it) and can read the chats and documents shown on the page. Our `Referrer-Policy: same-origin` would also strip the referrer from Razorpay's requests, and it is unknown whether live mode's website check depends on it (open question 10.5).
- **Netbanking** opens the bank's page in a pop-up. Firefox users may have to allow pop-ups (Standard Checkout FAQ 13).

### 1.2 Payment Links details

- "Payment Links can only be used to accept one-time payments." Amounts are fixed, and each link is for a single customer.
- The hosted page offers cards, UPI, netbanking, wallets and more. "The UPI intent on mobile web is available at Razorpay Standard Checkout and other products such as Payment Links, Payment Pages and Invoices."
- Customer email and phone "are not auto-populated on the Checkout section of the Payment Link hosted page", so the customer types them in. A **Report Payment Link** option is shown by default (risk policy).
- "UPI Payment Links will not redirect to `callback_url` on an Android device", and "UPI Payment Links is not supported in Test Mode". So use **Standard** links (`upi_link` absent or false).
- **Test mode allows "up to 30 Payment Links per business"**. More need a support request (4.3).
- Every link has an underlying Order. `GET /v1/payment_links/:id` returns `order_id` and `payments[]` (`payment_id`, `status`), and the `payment_link.paid` webhook carries the `payment_link`, `order` and `payment` entities.

### 1.3 How the recommendation meets the four criteria

1. **Security:** no third-party script on the origin that holds the session. The CSP (`script-src 'self'`, `connect-src 'self'`, `form-action 'self'`) and the cross-site write guard stay as they are. CSP doesn't restrict top-level navigation, so redirecting to `rzp.io` is allowed.
2. **UX:** a full-page redirect instead of a modal, and the customer types their contact details. That is acceptable once a month.
3. **Localhost:** `callback_url` is a browser redirect, so `http://localhost:8080/...` should work (open question 10.1). Webhooks are optional at first (3.4).
4. **Phones:** UPI intent is available on mobile web for Payment Links (5.1).

### 1.4 Browser and stack interactions (from the browser model and `frontend/nginx.conf`, not Razorpay's docs)

- The session cookie is `SameSite=Strict`, so a **cross-site top-level GET** (the Payment Link callback) arrives **without the cookie**. The callback endpoint must therefore identify the checkout from the signed `razorpay_payment_link_id`, never from the session. It verifies, then sends a 303 to an app page. The SPA's later `fetch` calls are same-site and do carry the cookie.
- nginx returns 403 for `POST|PUT|PATCH|DELETE` that the browser marks `same-site` or `cross-site`. A GET callback passes. Server-to-server webhooks carry no `Sec-Fetch-Site` header and pass too.

## 2. API flow

### 2.1 Orders

- **Create:** `POST https://api.razorpay.com/v1/orders`, JSON body, Basic auth.
  - `amount`: an integer in paise. Rs.500 is `50000`, Rs.2000 is `200000`, Rs.5000 is `500000`. The minimum is INR 1.00 (100 paise), and the amount must be a JSON integer, not `"100"` or `100.0`.
  - `currency`: `"INR"`.
  - `receipt`: at most 40 ASCII characters, "has to be unique".
  - `notes`: at most 15 pairs of 256 characters each.
  - `partial_payment`: default `false`. Leave it out.
  - Optional `payment`: `{"capture": "automatic", "capture_options": {"automatic_expiry_period": ≥12 (minutes), "manual_expiry_period": ≤7200 (default 7200), "refund_speed": "normal"}}`. This overrides the Dashboard capture settings for the order.
- **Response:** `id` (`order_…`), `entity`, `amount`, `amount_paid`, `amount_due`, `currency`, `receipt`, `status`, `attempts`, `notes`, `created_at`, `offer_id`.
- **Order states:**
  - `created`, until a payment is attempted;
  - `attempted`, until one payment is captured;
  - `paid`: "No further payment requests are permitted once the order moves to the `paid` state", and the order "stays in the `paid` state even if the payment … is refunded".
- **Retries:** "A Razorpay Order ID maps 1:1 to a payment attempt. If a customer's payment fails and they try again, you must create a new Order." The same docs also say orders "bind multiple payment attempts" (retries inside one open Checkout). Read together: a new checkout session gets a new order.
- **Idempotency:** the create page says `receipt` "is treated as an idempotency key, so a second create call with the same value is rejected" ("Duplicate request. This request has already been processed."). `GET /v1/orders?receipt=…` finds the existing order. This text is recent and hasn't been tested here (open question 10.4).
- **Other errors:** "Request failed because another order operation is in progress" means wait and retry. "Amount exceeds maximum amount allowed" means the account's transaction limit was hit.

### 2.2 Payment states and capture

- **Payment states:**
  - `created`: details sent, not yet processed;
  - `authorized`: the bank has authenticated it, and it must be captured before it settles;
  - `captured`: complete, and settles on the settlement cycle;
  - `refunded`;
  - `failed`: the customer retries.
- **Auto-capture** is the default ("This is the default setting for all customers"). It works only for payments created through the Orders API: "Payments made without an `order_id` cannot be captured and will be automatically refunded."
- **Auto-refund window:** an authorized payment that isn't captured is refunded automatically, at Normal speed (5-7 working days). The docs disagree on the window: the capture-settings page says **3 days**, while the Orders-API capture page says **5 days** (`manual_expiry_period` maximum 7200 minutes).
- **Late authorisation:** when the bank's status doesn't arrive, Razorpay keeps polling for 3 days (or 5, as the API page says), and the payment can still become `authorized`. With auto-capture it then becomes `captured`. The design must accept a capture that arrives days after checkout. The options are to extend the plan or to refund. Either way, the payment must never be lost.
- "You should deliver the products or services to your customers only after the payment is captured."
- **Payment entity fields used:** `id`, `amount`, `currency`, `status`, `order_id`, `method` (`card`, `netbanking`, `wallet`, `emi`, `upi`), `captured`, `amount_refunded`, `refund_status`, `fee` (including GST), `tax` (the GST on the fee), `error_code`, `error_reason`, `vpa`, `bank`, `created_at`.

### 2.3 Fetch endpoints (all `GET`, Basic auth)

| Endpoint | Purpose |
|---|---|
| `/v1/orders/:id` | the order and its `status` |
| `/v1/orders/:id/payments` | "all the authorised or failed payments for that order" |
| `/v1/payments/:id` | one payment by id |
| `/v1/orders?receipt=…` | look up an order by our receipt (`count` ≤ 100, `skip`, `from`/`to`, `expand[]=payments`) |
| `/v1/payment_links/:id` | the link's `status`, `amount_paid`, `order_id` and `payments[]` |
| `/v1/payment_links?reference_id=…` | look up a link by our reference |

### 2.4 Standard Checkout signature (for reference)

`generated_signature = hmac_sha256(order_id + "|" + razorpay_payment_id, key_secret)`, compared as a hex digest. The `order_id` must come from **our** stored order: "Do not use the `razorpay_order_id` returned by Checkout." Razorpay's Python SDK uses `"{}|{}".format(order_id, payment_id)`, the API key secret and `hmac.compare_digest`.

### 2.5 Payment Links: create, callback and signature

- **Create:** `POST https://api.razorpay.com/v1/payment_links`.
  - `amount` in paise and `currency` `INR`;
  - `accept_partial` false;
  - `reference_id`: at most 40 characters and unique; a duplicate gets "payment link creation with reference ID already attempted";
  - `description`: at most 2048 characters;
  - `customer` (`name`, `email`, `contact`), optional;
  - `notify`: `{sms, email}` booleans, where false means Razorpay doesn't send them;
  - `reminder_enable`;
  - `notes`: at most 15 pairs;
  - `expire_by`: a Unix time **at least 15 minutes ahead** and no more than 6 months (default 6 months);
  - `callback_url`, with `callback_method` **must be `get`** when `callback_url` is set.
- **Response:** `id` (`plink_…`), `short_url` (`https://rzp.io/i/…`), `status` and more. Link states are `created`, `partially_paid`, `expired`, `cancelled` and `paid` (the FAQ calls the unpaid state `issued`).
- **Other endpoints:** `POST /v1/payment_links/:id/cancel`, which only works while the link is unpaid, and `PATCH /v1/payment_links/:id`.
- **Callback query:** `razorpay_payment_id`, `razorpay_payment_link_id`, `razorpay_payment_link_reference_id` (empty when no `reference_id` was set), `razorpay_payment_link_status` and `razorpay_signature`.
- **Signature:** an HMAC-SHA256 hex digest with the **API key secret** over the link id, reference id, status and payment id, in that order. The docs list the four fields. The `|` separator comes from Razorpay's official Python SDK (`verify_payment_link_signature`):
  `hmac_sha256(payment_link_id + "|" + payment_link_reference_id + "|" + payment_link_status + "|" + razorpay_payment_id, key_secret)`
- "The `razorpay_payment_link_id` attribute should be stored in your system against an order, right after it is returned in the create Payment Link response."

### 2.6 Before fulfilling

The docs say (Standard Checkout best practices): "Check the payment/order status, that is if the payment's status is `captured` and the order's status is `paid` before proving [sic] the services to the customers."

## 3. Webhooks

### 3.1 Events for one-time payments

| Event | Meaning |
|---|---|
| `payment.authorized` | a payment was authorised |
| `payment.captured` | the payment was captured; the payload carries only the payment entity |
| `payment.failed` | a payment failed |
| `order.paid` | the order is paid; the payload carries the order **and** the payment entity |
| `payment_link.paid` | the link was paid; the payload carries `payment_link`, `order` and `payment` |
| `payment_link.partially_paid` | a partial payment was made on a link |
| `payment_link.expired` | a link expired |
| `payment_link.cancelled` | a link was cancelled |
| `refund.created`, `refund.processed`, `refund.failed`, `refund.speed_changed` | refund lifecycle; the payload carries the refund and payment entities |

The Standard Checkout best practices recommend `payment.captured`, `payment.failed` and `order.paid`.

### 3.2 Signature

- The header is `X-Razorpay-Signature`. Its value is the HMAC-SHA256 hex digest of the **raw request body**, keyed with the **webhook secret**. The webhook secret is set per webhook in the Dashboard and is separate from the API key secret. "Do not parse or cast the webhook request body."
- After rotating the secret, check retries of older events against the old secret.

### 3.3 Delivery

- The header `x-razorpay-event-id` "is unique per event". Use it to drop duplicates, because delivery is at least once.
- Respond with a 2xx **within 5 seconds**. Anything else, or a timeout, counts as a failure. Retries follow "exponential backoff policy for 24 hours after event creation timestamp". No retry count or intervals are published. After 24 hours of failures **the webhook is disabled** and must be re-enabled in the Dashboard, and an email goes to the Alert Email Address.
- Order is not guaranteed. "You may occasionally observe a `payment.failed` webhook followed by a `payment.captured` webhook for the same transaction." This happens with late authorisation and with UPI retries, so a failure must never cancel a checkout for good.
- Requirements:
  - ports 80 or 443 only;
  - HTTPS is recommended, with TLS 1.2 or later;
  - separate webhooks for Test and Live, up to 30 URLs;
  - test-mode setup asks for an OTP, which is `754081`;
  - the webhook source IPs are published (`35.154.217.40`, `35.154.22.73`, `13.126.238.192`, `13.127.201.109`, `13.232.194.134`, `35.154.143.15`, `52.66.151.218`, `52.66.75.174`, `52.66.76.63`), and Razorpay still recommends signature checks.

### 3.4 No public URL yet

- "Webhooks can only be delivered to public URLs". Saving a localhost URL fails.
- **Blacklisted hosts:** `localhost`, `.local`, `.internal`, `.corp`, `ngrok.io`, `loca.lt`, `webhook.site`, `requestbin.com` and others. The docs suggest a `zrok` tunnel. The Cloudflare quick tunnel this app already uses (`trycloudflare.com`) is **not** on the list, but its URL changes whenever the tunnel restarts.
- **Fallback documented by Razorpay:** "If a critical user-facing flow requires instant status, but the webhook notification has not arrived …, perform an immediate API Fetch call ([Payments], [Orders] and [Refunds])", and "For business-critical, synchronous use cases, you may choose to poll our payment APIs as well."
- **So:** a reconciler polls `GET /v1/payment_links/:id` (or `/v1/orders/:id/payments`) for every open checkout. It polls often while the customer is on the return page and less often until expiry plus the late-authorisation window. Webhooks become a faster path into the same idempotent activation, not the only one.

## 4. Test mode

### 4.1 Keys and KYC

- "The Test mode is available to you as soon as you complete the sign-up process. You can use Test mode as long as you want." Live mode needs an activated (KYC-verified) account. **KYC isn't needed for test mode.**
- Test and Live use separate key pairs. Generate them under Dashboard → Account & Settings → API Keys → Generate Key. The secret is shown once, so download it.
- Keys can be rolled, with the old ones deactivated at once or after 24 hours.
- If test keys are left in production, "customers will see a payment success screen but no payment will be captured or settled".

### 4.2 Test instruments

| Method | Success | Failure |
|---|---|---|
| UPI | `success@razorpay` | `failure@razorpay` |
| Cards | Visa `4100 2800 0000 1007`, Mastercard `5555 5100 0008 1006`, Mastercard prepaid `5180 2872 0009 1001`, RuPay `6527 6589 0000 1005` | `payment_timed_out` `4100 2800 0009 0000`, `insufficient_fund` `4100 2800 0008 0001`, `payment_cancelled` `4100 2800 0007 0002`, `card_declined` `4100 2800 0006 0003`, `authentication_failed` `4100 2800 0000 0009` (Mastercard equivalents are listed too) |
| Netbanking | any listed bank, then **Success** on the mock page | any listed bank, then **Failure** on the mock page |

- **Card details:** any future expiry and any CVV. A random OTP of 4 to 10 digits succeeds, and an OTP shorter than 4 digits fails. The new error-scenario cards were added in January 2026.
- **UPI warning:** "In test mode, payment cancellation will result in a successful payment." Test the cancel path in live mode.

### 4.3 Test webhooks and links

- Test events fire for test transactions, with the same payload shape as live.
- At most 30 test Payment Links per business. So the automated tests use a **fake Razorpay** (the project's pattern for Google and the LLM providers), and manual test-mode runs stay well under the cap. Use short `expire_by` values and cancel unused links (cancelled links probably still count).

## 5. Payment methods, limits, refunds

### 5.1 UPI

- **Collect is gone:** "According to NPCI guidelines, the UPI Collect flow is being deprecated effective 28 February 2026. Customers can no longer make payments by manually entering VPA/UPI id/mobile numbers." On Standard Checkout, "Razorpay will automatically handle the transition by removing the UPI Collect option and enabling Intent/QR flows".
- **Intent:** the UPI app opens with the details filled in.
  - It runs on mobile web, Android and iOS. On mobile web it covers PhonePe and Google Pay, on Google Chrome only.
  - "UPI Intent is not supported on web" (desktop).
  - By default the top UPI apps are listed "irrespective of the installation status".
- **QR:** "UPI QR code will be displayed automatically on desktop checkout" once Collect is hidden. On mobile web, QR is "an on-demand feature".
- **Limits:**
  - NPCI's P2M limit is ₹1,00,000 per transaction, and an unverified merchant can collect at most ₹2,000.
  - UPI apps usually allow ₹1,00,000 a day, or 10 transactions.
  - Rs.5000 is well inside these.

### 5.2 Netbanking, cards and bank transfer

- **Netbanking** is "available by default" and redirects to the bank's page.
- **Debit and credit cards** are available by default.
- **Bank Transfer** (NEFT/RTGS/IMPS to a Customer Identifier) "Requires Approval and Integration". Once enabled it appears in every Razorpay checkout.
- For the owner's "bank transfer", netbanking covers paying from a bank account without any approval.

### 5.3 Refunds API

- **Create:** `POST /v1/payments/:id/refund` with an optional `amount` (paise). Leaving it out refunds the full amount, and a smaller value refunds part of it.
- **Other fields:**
  - `speed`: `normal` (the default, 5-7 working days) or `optimum`, which is instant when possible and otherwise falls back to normal;
  - `notes`;
  - `receipt`.
- **Refund states:**
  - `pending`;
  - `processed`, the final state, which Razorpay may set before the bank's reference arrives unless support enables waiting for it;
  - `failed`. For example, "Normal refunds are not possible for a payment which is more than 6 months old."
- **Idempotency:** the header `X-Refund-Idempotency` takes a key of at least 10 characters, from `[A-Za-z0-9_-]`. A retry must use the same body. The same key with a different body is rejected.
- Refunds go back to the source: a UPI payment is refunded to the same VPA.
- "Instant refund is enabled by default for your account". Its fee isn't confirmed here (6.3).

## 6. Going live

### 6.1 Account and KYC

- **Sign-up:** sign up, then give basic details, business details (registered or unregistered), the PAN (personal PAN for an individual, or business PAN plus signatory PAN), documents for a registered business (one of GST or MSME, plus one more), KYC and a bank account. Razorpay verifies the bank account by depositing ₹1. GSTIN is optional ("I don't have a GSTIN").
- **KYC:** since **1 January 2026**, Central KYC can activate an account "in minutes" without video KYC. Video KYC is the fallback for individuals.
- **After activation:** you can "Accept payments without any amount limits".

### 6.2 Website review

You must provide your live website or mobile app details during account activation. "Without these details, you will not be able to access live mode API keys". Dashboard Payment Links still work without them.

- **Website form:** the Website URL, plus links to **About us, Contact us, Pricing details, Terms and conditions, Privacy policy and Refunds policy**, and a **sample invoice** (PNG, JPG or PDF). The tip also lists **Shipping Policy** and **Cancellation and Refunds**.
- **Logins:** if the site needs a login to pay, provide **test account credentials**. Here that means an account the admin creates for the reviewer.
- **Checks:** "If you add or update your primary website, instant checks are run to validate your website, policy pages and business industry."
- **Domain:** "Ensure your new website's domain is whitelisted in your Razorpay account to avoid payment disruptions once you switch to Live Mode."
- **Consequence:** the policy and pricing pages must be public, reachable without signing in, on the Cloudflare domain, before live mode.

### 6.3 Settlement and fees

- **Settlement:** the standard domestic cycle is **T+2 working days** after capture, and it needs a fully activated account (docs). The pricing page's FAQ says "typically T+1 or Instant". Expect T+2 until the Dashboard shows otherwise.
- **Fees** (from `razorpay.com/pricing`, read 2026-10-26):
  - the Standard plan's **platform fee is 2% + GST** across UPI, cards, netbanking and wallets;
  - corporate cards and credit card on UPI cost **2.15% + GST**;
  - UPI and RuPay debit carry "Zero MDR", but "a standard platform or technology fee of 2% will be applied";
  - there is no setup fee and no AMC;
  - the page currently advertises "0% fee (down from 2%) … for first 90 days";
  - custom pricing is available above ₹5 lakh a month;
  - "The transaction fee and applicable GST are automatically deducted from the customer's payment at the source".
- **GST on Razorpay's fee:** the payment entity shows `fee` (including GST) and `tax` (the GST). GST is charged on Razorpay's fee, and the pricing page states 18%.
- **GST on our own prices:** Razorpay's docs don't cover it. Razorpay's Learn articles (secondary) give a services registration threshold of **₹20 lakh** a year (₹10 lakh in special-category states) and an 18% rate for IT services. Whether the plan prices include GST, and whether the business must register, is for the owner's accountant.

## 7. REST API mechanics

### 7.1 Authentication

- Basic auth: `Authorization: Basic base64(key_id:key_secret)`, in exactly that format. `BASIC …`, `basic …` and quoted tokens fail.
- The base URL is `https://api.razorpay.com/v1/`.
- Calls must come from the server. Calling from the browser fails with CORS ("Blocked by CORS policy").

### 7.2 Errors

- Errors come back as `{"error": {"code", "description", "field", "source", "step", "reason", "metadata": {"payment_id", "order_id"}}}`, for example `BAD_REQUEST_ERROR` with a 400.
- "Authentication failed" is also a 400, and is often caused by mixing test and live keys.

### 7.3 Rate limits and idempotency

- **Rate limits:** **no numbers are published**. "Watch for HTTP status code 429 and build the retry mechanism", using exponential or stepped backoff with jitter.
- **Idempotency:**
  - Orders: `receipt` (2.1).
  - Payment Links: `reference_id` (2.5).
  - Refunds: the `X-Refund-Idempotency` header (5.3).
  - Payment creation needs nothing from us (Razorpay's page does it).

### 7.4 SDK vs plain HTTPS

- **SDK:** `razorpay` on PyPI is version **2.0.1** (2026-03-09; 2.0.0 on 2025-09-22 added `enable_retry`), with `requires_dist: ['requests']`, so it is synchronous.
- **Plain HTTPS:** nothing in the docs requires the SDK. Every endpoint is documented as plain HTTPS with curl. `httpx` (already pinned at 0.28.1) with Basic auth, JSON bodies, timeouts and `hmac.compare_digest` covers create order or link, fetch order, payments, payment and link, cancel link, and refund. That avoids a new dependency and keeps the async event loop free.

## 8. Recent changes and deprecations

| When | Change |
|---|---|
| Apr 2025 | Hosted Checkout deprecated ("no longer supported"), though its pages are still online |
| Sep 2025 | Python SDK 2.0.0 (retry support); 2.0.1 followed in Mar 2026 |
| Jan 2026 | Central KYC onboarding; new test cards for failure scenarios |
| 28 Feb 2026 | UPI Collect stopped (NPCI). Intent on mobile and QR on desktop replace it. Save VPA and Validate VPA went with it, and test UPI IDs may behave differently (open question 10.3) |
| Undated | Payment Link pages no longer prefill email and phone ("updated security policy") |
| Undated | Webhook URL blacklist now includes `ngrok.io`, `loca.lt` and the common request-bin sites |

**Inconsistencies in the docs:**

- the capture and late-authorisation window is 3 days on one page and 5 days on another (2.2);
- settlement is T+2 in the docs but "T+1 or Instant" on the pricing page (6.3);
- an order maps "1:1 to a payment attempt" on one page and "bind[s] multiple payment attempts" on another (2.1);
- the `receipt` idempotency rule is newly documented (2.1).

## 9. What this means for the design (inputs for the payments brief)

- **Settings:** add `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` and `RAZORPAY_WEBHOOK_SECRET` to `.env`, plus the mode (test or live) shown in the admin UI. Never log the secrets. The key id isn't secret, but it isn't needed in the browser for Payment Links either.
- **Data model:** a checkout row records the user, plan, amount in paise, currency, `reference_id` (our id, at most 40 ASCII characters), `plink_id`, `order_id`, `payment_id`, status, `expire_by` and timestamps. An append-only payment event log records the Razorpay event id or payment id, for idempotency. Activation is unique on `payment_id`.
- **Endpoints:**
  - `POST /api/billing/checkout`: creates the link (with a 15 to 30 minute `expire_by`) and returns `short_url`;
  - `GET /api/billing/razorpay/return`: no session needed; verifies the signature, fetches the status, activates if `captured`, then sends a 303 to the plan page;
  - `POST /api/billing/razorpay/webhook`: raw body, HMAC, event-id dedupe, replies 2xx fast and processes the event in the same transaction or queues it;
  - a reconciler job in the existing `worker` that polls open checkouts.
- **Amount check:** activate only when `amount`, `currency` and `notes` or `reference_id` match what was stored. Never trust the plan named in query parameters.
- **Late and duplicate payments:** a capture after the checkout expired, or a second payment, should still be recorded. Whether to extend the period or refund is a policy decision. Unrecognised captured payments should show up for the admin.
- **Tests:** a fake Razorpay server in the integration stack (create and fetch link, order and payment; signed callbacks; signed webhooks, including duplicates and out-of-order delivery), in line with the project's fake Google and LLM servers. Unit tests check both HMAC formulas against Razorpay's documented samples where they exist.
- **Public pages before live:** Terms, Privacy, Refund and Cancellation, Shipping/Delivery ("digital service, no shipping"), Contact, About and Pricing, reachable without signing in. Also a reviewer account.

## 10. Open questions to check in test mode

1. Does `POST /v1/payment_links` accept `callback_url` = `http://localhost:8080/...`, and does the redirect then work? If not, use the Cloudflare quick-tunnel URL until the domain exists.
2. Does the Payment Link page show UPI QR on desktop and UPI intent on Android Chrome?
3. Do `success@razorpay` and `failure@razorpay` still work on the test checkout now that Collect is gone, or is there a test QR or intent path?
4. Is a repeated `receipt` really rejected? Is a repeated `reference_id` rejected (documented: "already attempted")?
5. Standard Checkout only: does live-mode website validation work with our `Referrer-Policy: same-origin`?
6. Are Payment Link payments auto-captured? The documented samples show `captured`.

## 11. Sources (all read 2026-10-26)

Razorpay documentation (each page also exists as `https://razorpay.com/docs/build/llm-docs/<path>.md`):

- Standard Checkout integration steps: https://razorpay.com/docs/payments/payment-gateway/web-integration/standard/integration-steps/
- Standard Checkout best practices: https://razorpay.com/docs/payments/payment-gateway/web-integration/standard/best-practices/
- Standard Checkout troubleshooting and FAQs: https://razorpay.com/docs/payments/payment-gateway/web-integration/standard/troubleshooting-faqs/
- Callback URL: https://razorpay.com/docs/payments/payment-gateway/callback-url/
- Hosted Checkout: https://razorpay.com/docs/payments/payment-gateway/web-integration/hosted/ and https://razorpay.com/docs/payments/payment-gateway/web-integration/hosted/integration-steps/
- S2S integration: https://razorpay.com/docs/payments/payment-gateway/s2s-integration/
- Payments changelog: https://razorpay.com/docs/payments/changelog/ · API changelog: https://razorpay.com/docs/api/changelog/
- Orders, create: https://razorpay.com/docs/api/orders/create/ · entity: https://razorpay.com/docs/api/orders/entity/ · fetch payments: https://razorpay.com/docs/api/orders/fetch-payments/ · fetch by id: https://razorpay.com/docs/api/orders/fetch-with-id/ · fetch all: https://razorpay.com/docs/api/orders/fetch-all/
- Payments, fetch by id: https://razorpay.com/docs/api/payments/fetch-with-id/ · entity: https://razorpay.com/docs/api/payments/entity/
- Payment life cycle: https://razorpay.com/docs/payments/payments/
- Capture settings: https://razorpay.com/docs/payments/payments/capture-settings/ · via Orders API: https://razorpay.com/docs/payments/payments/capture-settings/api/
- Payment Links: https://razorpay.com/docs/payments/payment-links/ · APIs, callback and signature: https://razorpay.com/docs/payments/payment-links/apis/ · FAQs: https://razorpay.com/docs/payments/payment-links/faqs/ · webhooks: https://razorpay.com/docs/payments/payment-links/subscribe-to-webhooks/ and https://razorpay.com/docs/webhooks/payment-links/
- Payment Links API: https://razorpay.com/docs/api/payments/payment-links/ · create: https://razorpay.com/docs/api/payments/payment-links/create-standard/ · fetch: https://razorpay.com/docs/api/payments/payment-links/fetch-id-standard/ · fetch all: https://razorpay.com/docs/api/payments/payment-links/fetch-all-standard/
- Webhooks: https://razorpay.com/docs/webhooks/ · setup: https://razorpay.com/docs/webhooks/setup-edit-payments/ · validate and test: https://razorpay.com/docs/webhooks/validate-test/ · best practices: https://razorpay.com/docs/webhooks/best-practices/ · payment events: https://razorpay.com/docs/webhooks/payments/ · order events: https://razorpay.com/docs/webhooks/orders/ · refund events: https://razorpay.com/docs/webhooks/refunds/
- IPs and certificates: https://razorpay.com/docs/announcements/whitelists/ (also https://razorpay.com/docs/security/whitelists/)
- Test cards: https://razorpay.com/docs/payments/payments/test-card-details/ · test UPI: https://razorpay.com/docs/payments/payments/test-upi-details/ · test and live modes: https://razorpay.com/docs/payments/dashboard/test-live-modes/
- API authentication: https://razorpay.com/docs/api/authentication/ · API keys: https://razorpay.com/docs/payments/dashboard/account-settings/api-keys/ · understand the APIs (rate limiting): https://razorpay.com/docs/api/understand/ · errors: https://razorpay.com/docs/errors/
- UPI: https://razorpay.com/docs/payments/payment-methods/upi/ · UPI FAQs: https://razorpay.com/docs/payments/payment-methods/upi/faqs/ · Collect migration: https://razorpay.com/docs/announcements/upi-collect-migration/standard-integration/ · intent on mobile web: https://razorpay.com/docs/payments/payment-methods/upi/upi-intent/mobile-web/ · limits: https://razorpay.com/docs/payments/payment-methods/transaction-limits/upi/
- Netbanking: https://razorpay.com/docs/payments/payment-methods/netbanking/ · Bank Transfer: https://razorpay.com/docs/payments/payment-methods/bank-transfer/
- Refunds: https://razorpay.com/docs/payments/refunds/ · create normal: https://razorpay.com/docs/api/refunds/create-normal/ · create instant: https://razorpay.com/docs/api/refunds/create-instant/ · idempotent: https://razorpay.com/docs/api/refunds/normal-refunds-idempotent/
- Account set-up and KYC: https://razorpay.com/docs/payments/set-up/ · website details: https://razorpay.com/docs/payments/dashboard/account-settings/business-website-details/ · settlements: https://razorpay.com/docs/payments/settlements/ · GST details: https://razorpay.com/docs/payments/dashboard/account-settings/gst-details/

Other Razorpay sources (not docs pages):

- Pricing: https://razorpay.com/pricing/
- `checkout.js` as served (origins and caching headers): https://checkout.razorpay.com/v1/checkout.js
- Python SDK signature helpers: https://github.com/razorpay/razorpay-python/blob/master/razorpay/utility/utility.py · versions: https://pypi.org/pypi/razorpay/json
- GST (secondary, Razorpay Learn): https://razorpay.com/learn/gst-registration-eligibility-process-documents-and-penalties/ and https://razorpay.com/learn/gst-on-services/

## 12. Additions from the design review (2026-10-26)

The security review of the payments brief checked these facts against the same `llm-docs` pages (section 11). They
are recorded here so the brief can cite them; the open points go to the test-mode checks of the brief (14.2).

- **Disputes (chargebacks)** have their own webhooks: `payment.dispute.created`, `payment.dispute.under_review`,
  `payment.dispute.action_required`, `payment.dispute.won`, `payment.dispute.lost` and `payment.dispute.closed`. Each
  payload carries the payment id, the disputed amount and a `respond_by` deadline; a dispute not answered by then is
  lost by default. Responding happens in the Dashboard. A Disputes API exists (`GET /v1/disputes`, and by id); whether
  it answers with test keys, and whether test disputes can be raised, is to be checked in test mode.
- **What the webhook signature covers**: the HMAC is over the raw body only. The `x-razorpay-event-id` header isn't
  signed, and the body has no event id of its own, so a captured body can be replayed under a new header value.
  Deduplicate on the body as well, and treat every event as a prompt to fetch the entity rather than as the truth.
- **Personal data in payloads**: the payment entity in webhook bodies and fetch responses carries `email`, `contact`,
  `vpa`, `card` (last 4 digits, network) and `bank`. Store only the fields needed (ids, status, amount, currency,
  method, fee).
- **Refund idempotency**: Razorpay documents no validity window for the `X-Refund-Idempotency` key; a retry should use
  the same key and body soon after the first attempt.
- **Amounts with Customer Fee Bearer or Offers** (the review's reasoning, not checked in the docs): either Dashboard
  feature can change the amount a payment carries, so an exact amount check needs both off.
