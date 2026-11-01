# Research: self-service sign-up (email and Google), Turnstile and transactional email

> Facts **as documented on 2026-10-28**, collected for the sign-up design: email + password sign-up with email
> verification, "Continue with Google" (OpenID Connect), Cloudflare Turnstile against bots, and a transactional email
> service for verification, "Forgot password", receipts and plan reminders. Every page in section 7 was read on that
> date from the vendor's or standard body's own site (Google, Cloudflare, Resend, Brevo, OWASP, NIST, HIBP). Where a
> statement is an inference, a browser fact, a reading of this repo or a third-party report, the text says so. This is
> research only: the decisions belong to the sign-up brief. Nothing here is legal advice.

Repo facts this builds on (read in code on 2026-10-28): users have a `username` (`^[a-z0-9_.-]+$`, so never an `@`)
and an argon2 `password_hash` (NOT NULL); login compares against a dummy hash for unknown users
(`security.verify_password_async`) and throttles failures per username (10) and per IP (100) per 15 minutes; the
session is a JWT cookie (httpOnly, SameSite=Strict, `token_version`); the Gmail plugin already runs an OAuth
authorization-code flow with state, PKCE S256 and a SameSite=Lax browser-binding cookie (`app/plugins/oauth.py`) but
reads Google's ID token with `verify_signature: False`; SMTP sending exists in `app/ops/channels.EmailChannel`
(stdlib `smtplib` in a thread, used by alerts and plan reminders); nginx sends one strict CSP
(`script-src 'self'`, `connect-src 'self'`, `form-action 'self'`, `frame-ancestors 'none'`) and
`Referrer-Policy: same-origin` on every HTML response and refuses cross-site writes; the policy pages
(`frontend/public/legal/privacy.md`, `about.md`, …) are still **draft placeholders**.

## 0. Recommendations

| # | Recommendation | Why |
|---|---|---|
| R1 | **Put Google sign-in in a new, separate Google Cloud project** ("AskNuvexusDoc"), user type External, **In production**, brand-verified, declaring only `openid`, `userinfo.email`, `userinfo.profile`. Leave the Gmail plugin's project as it is (Testing). | Publishing status and user type are per project, and so is scope verification (1.4). Publishing the Gmail project would put its restricted Gmail scopes in front of every user as an unverified production app with a lifetime 100-user cap. Google recommends separate projects for testing and production (1.4). |
| R2 | **Server-side authorization-code flow with PKCE S256, `state` and `nonce`**, no Google JavaScript; identify users by `(iss, sub)`; **verify the ID token's signature** against Google's JWKS (PyJWT's `PyJWKClient`, already pinned with `cryptography`) plus `iss`, `aud`, `exp`, `nonce`; never store Google tokens. | Google requires `nonce` and says to key accounts on `sub`, never `email` (1.2, 1.3). Skipping the signature check is allowed only for a token fetched directly from Google (1.2); ASVS 6.8.2 asks for it always, and account creation is the most sensitive use. |
| R3 | **Auto-link a Google identity to an existing account only when that account's email is verified and Google is authoritative for the address** (`@gmail.com`, or `email_verified` true with `hd` present). Otherwise require the user to sign in with the password first and link from Settings. Never link to, or merge with, an unverified account. | Google's own rule for when its address claim proves ownership (1.3); OWASP forbids linking on a matching email alone (4.9); the pre-hijacking study's "Classic-Federated Merge" (4.1). |
| R4 | **Turnstile, Managed mode, explicit rendering, loaded on demand**, only on the signed-out pages. nginx serves those routes with a **second CSP** that adds `https://challenges.cloudflare.com` to `script-src` and `frame-src`; the signed-in app keeps today's CSP. Crossing between the two is a full page load. | Turnstile needs only those two CSP additions (2.3). CSP is per document, so per-location headers plus full page loads keep Cloudflare's script out of every signed-in page (2.7). |
| R5 | **Verify every Turnstile token on the server before any other work** (siteverify, 10 s timeout, `idempotency_key` on retry), and check `success`, `hostname` and `action`. Fail closed on sign-up, reset and resend; use the documented **test keys** in dev, CI and e2e. | Tokens are forgeable without siteverify, last 300 s and work once (2.4); the dummy keys exist for automated tests (2.5). |
| R6 | **Resend** as the email provider (free: 3,000 per month, 100 per day), sending from the owner's domain with SPF, DKIM and DMARC set up in Cloudflare by hand. Upgrade to Resend Pro when daily volume nears the cap. **Brevo** is the fallback (300 per day free). Cloudflare Email Sending is a candidate once it leaves beta. | Best fit for low volume: HTTPS API and SMTP, idempotency keys, Domain Connect for Cloudflare, no IP allowlisting surprise (3.1, 3.2). |
| R7 | **One provider-neutral mailer over SMTP** (generalize `EmailChannel`), fed by a Redis job queue and sent by the worker, never in the request path. Alerts keep a channel that doesn't depend on that provider. | Switching provider becomes four settings; the request's timing no longer depends on whether a mail was sent (4.4); a paused provider can't silence the monitor (3.5). |
| R8 | **Tokens:** 32 random bytes, sent in the link's **URL fragment**, stored as SHA-256, single use, verification 24 h, reset 30 min, consumed by a POST from the page; every outstanding reset token dies on use and on any password change. **Sessions:** a reset bumps `token_version` (all sessions end), never logs in automatically, and sends a notice. | OWASP and ASVS token rules (4.5, 4.10). A fragment never reaches nginx logs or a Referer, and a POST keeps mail scanners' GET prefetches from spending the token (4.5, inference). |
| R9 | **Enumeration resistance:** identical status, body and timing for a new and an existing email on sign-up and reset ("If that address can be used, we've sent you an email"); the existing owner gets an "you already have an account" email instead. Login keeps one generic message. | OWASP and ASVS 6.3.8 (4.4). |
| R10 | **Bind email verification to the password chosen at sign-up:** keep unverified sign-ups as pending rows (not users), finish sign-up only when the link's token **and** that row's password are presented, delete pending rows after 7 days. | Otherwise an attacker's earlier sign-up for the victim's address can turn into the victim's account (4.1). |
| R11 | **Passwords:** keep the 8-character floor (ASVS L1) with no composition rules and at least 64 allowed; add the mandatory **top-passwords blocklist** (≥3,000 entries plus context words) and an optional **HIBP range check** with `Add-Padding`, failing open. Raising the floor to 15 (NIST for single-factor) is an owner decision. | ASVS 6.2.1, 6.2.4, 6.2.12; NIST SP 800-63B-4 (4.2, 4.3). |
| R12 | **Email canonical form:** trim, NFC, IDNA domain to lowercase punycode, local part lowercased for comparison; store the original for sending; no Gmail dot or plus stripping. **Login by email or username:** an `@` means email (usernames can't contain one). | OWASP Email Validation cheat sheet (4.7, 4.8). |
| R13 | **Explicit terms and privacy consent** at sign-up (checkbox, stored with the terms version and time), also on a "finish creating your account" step for Google; **write the real privacy policy and about page first.** | Google's brand verification checks the homepage and privacy policy (1.4); India's DPDP Rules require a consent notice (3.6). |

## 1. Google sign-in (OpenID Connect)

### 1.1 Endpoints and the authentication request

- **Discovery:** `https://accounts.google.com/.well-known/openid-configuration`. Google says to hard-code this URI and
  respect its caching headers. It lists `issuer` `https://accounts.google.com`, `authorization_endpoint`
  `https://accounts.google.com/o/oauth2/v2/auth`, `token_endpoint` `https://oauth2.googleapis.com/token`, `jwks_uri`
  `https://www.googleapis.com/oauth2/v3/certs`, `userinfo_endpoint`
  `https://openidconnect.googleapis.com/v1/userinfo`, `code_challenge_methods_supported` `["plain", "S256"]`,
  `id_token_signing_alg_values_supported` `["RS256"]`, scopes `openid email profile`, and `response_modes`
  `query`, `fragment`, `form_post`.
- **Parameters** (Google's OIDC page): `client_id`, `response_type=code`, `scope` ("must begin with the `openid`
  value and then include the `profile` value, the `email` value, or both"), `redirect_uri` (exact match), `state`
  ("strongly recommended", anti-CSRF), **`nonce` (Required)**, optional `login_hint` (email or `sub`), `hd` (a UI hint
  only: "Don't rely on this UI optimization to control who can access your app"), `prompt` (`none`, `consent`,
  `select_account`), `access_type` (`offline` only if a refresh token is wanted), `include_granted_scopes`.
- **PKCE:** the OIDC page's parameter table doesn't list `code_challenge`; only discovery advertises S256. The Gmail
  plugin already sends PKCE S256 with a Web client and a secret (repo fact), so the same flow applies.
- **Redirect URI rules:** HTTPS (localhost exempt), no raw IPs (localhost exempt), a public-suffix TLD, no userinfo,
  fragment, wildcard, path traversal or open redirect.
- **For sign-in:** `scope=openid email profile`, `prompt=select_account`, no `access_type=offline`. Nothing but the ID
  token is needed, so the access token can be discarded (inference).

### 1.2 Token exchange and ID-token checks

- Exchange: `POST https://oauth2.googleapis.com/token` with `code`, `client_id`, `client_secret`, `redirect_uri`,
  `grant_type=authorization_code` and `code_verifier` (as the plugin already does).
- **Google's validation list:** verify the signature with a key from `jwks_uri`; `iss` equals
  `https://accounts.google.com` **or** `accounts.google.com`; `aud` equals your client ID; `exp` not passed; if you sent
  `hd`, the `hd` claim matches. Nonce isn't in the numbered list but the claim's description says to "protect against
  replay attacks by presenting this value only once"; ASVS 10.5.1 requires matching it to the request.
- **Signature:** "since you are communicating directly with Google over an intermediary-free HTTPS channel and using
  your client secret … you can be confident that the token you receive really comes from Google and is valid." Tokens
  passed to other components must be validated. ASVS 6.8.2: always validate signatures. Google recommends a client
  library or "a general-purpose JWT library"; `tokeninfo` is for debugging only.
- **Type quirk:** Google's sample token has `"email_verified": "true"` (a string) while the claim table says boolean;
  accept both `true` and `"true"`, nothing else (inference).

### 1.3 Claims: the stable id, the email and when Google vouches for it

- **`sub`:** "unique among all Google Accounts and never reused", "Maximum length of 255 case-sensitive ASCII
  characters", unchanged when the email changes. "Don't use the `email` field as a unique identifier for a user.
  Always use the `sub` field." ASVS 6.8.1 and OWASP: key federated identities on issuer + subject.
- **`email`:** only with the `email` scope; "may not be unique to this account and could change over time"; the
  domain doesn't identify Workspace users (use `hd`).
- **`email_verified`:** true if the address has been verified.
- **`hd`:** present only for a Google Workspace/Cloud organization account.
- **`name`, `picture`:** with `profile`, "never guaranteed to be present".
- **When Google is authoritative** (Sign in with Google docs): "`email` has a `@gmail.com` suffix" or "`email_verified`
  is true and `hd` is set". Otherwise "Google is not authoritative and password or other challenge methods are
  recommended to verify the user. `email_verified` can also be true as Google initially verified the user when the
  Google account was created, however ownership of the third party email account may have since changed."

### 1.4 Publishing status, verification and branding (the crucial part)

From Google's "Manage App Audience", "Manage OAuth App Branding", "Manage App Data Access", the brand-verification
page and the verification help centre:

- **Testing:** "limited to up to 100 test users"; test users see a warning; "Authorizations by a test user will expire
  seven days from the time of consent", refresh tokens included.
- **The basic-scope exception (verbatim):** "The only exception to this behavior is if your app requests a subset of
  the following: name, email address, and user profile (through the userinfo.email, userinfo.profile, openid scopes or
  their OpenID Connect equivalents). For such requests, your users do not need to be in the trusted user list, they
  will not see a warning message, and their authorizations will not expire after 7 days. If your app uses Sign in with
  Google to authenticate users then this exception also applies. If your app requests any other OAuth scopes, then
  this exception does not apply." So a sign-in-only request works for **any** Google user even in a Testing project.
- **In production:** "available to any user with a Google Account"; reached with **Publish app**. "Your project's
  configuration may be subject to verification before its name and logo are displayed on an authorization screen or
  before it may request authorization of sensitive or restricted scopes."
- **Unverified apps and the cap:** the unverified-app warning appears when "your project's OAuth clients request
  authorization of scopes considered sensitive or restricted before your project has completed verification for those
  scopes". The new-user cap is "100 new users in total, after the app presents the unverified app screen"; it "applies
  over the entire lifetime of the project, and it cannot be reset or changed". Separately, every app that requests user
  data has a **new-user authorization rate limit** set by "application history, developer reputation, and riskiness"
  (error `403: rate_limit_exceeded`; increases can be requested, about 5 business days).
- **Everything is per project:** the audience page speaks of "your project's publishing status" affecting the scopes
  requested by "your project's OAuth clients". For sensitive or restricted scope verification, "Your video must include
  all OAuth clients that you assigned to this project." Verification e-mails go to the project's owners and editors.
- **Separate projects:** "It is a recommended best practice to have separate cloud projects for development/testing
  and production/publishing"; apps in development/testing/staging "are not subject to verification", and only the
  production project should be submitted.
- **Non-sensitive scopes only:** "If your app utilizes only non-sensitive scopes, it is not mandatory for your app to
  complete the app verification process", but showing a name and logo needs the lighter **brand verification**.
- **Branding:** "Your brand must be verified if you want your application logo and application name to be visible to
  users on the consent screen. **Without verification, only your application domain will be visible to users.**"
  Brand verification applies to apps with user type External and status Published. Requirements:
  - homepage "hosted on a verified domain you own", describing the app; it "can not be only a login page" and must
    link the privacy policy shown on the consent screen;
  - privacy policy on the homepage's domain, linked from the homepage and the consent screen, disclosing how the app
    "accesses, uses, stores, and/or shares Google user data" (Limited Use applies);
  - every authorized domain verified in **Google Search Console** by a project Owner or Editor;
  - logo: square, 120×120 px recommended, ≤1 MB, JPG/PNG/BMP; a new logo on an External production app re-triggers
    verification;
  - app name must not use or imitate Google brands; homepage, privacy and terms links "are required for all external
    production apps".
- **Process and timing:** Branding page → **Verify Branding** (automated, "usually completes in a few minutes"; a
  manual review "usually takes 2-3 business days") → status **Ready to publish** → **Publish branding** within 7 days
  ("Compliant verification results are valid for 7 days"). Branding can't be edited during a review. A published brand
  is required before data-access (scope) verification can start.
- **Clients:** "OAuth 2.0 clients that have been inactive for six months are automatically deleted". A client deleted
  by hand can be restored within 30 days.

### 1.5 Same project or a separate one

**Can the Gmail project be published In production without restricted-scope verification if only another client asks
for Gmail scopes?** No, not cleanly. Publishing status is per project. Once published, the Gmail client would show
the unverified-app screen to every connecting user and draw on the project's **lifetime** 100-user cap. A production
project "should complete the verification process" when it meets the criteria. A verification demo must cover
**all** of the project's clients. Restricted Gmail scopes also need yearly re-verification and a security assessment.
The users would no longer hit the 7-day expiry, but the plugin's status would change for reasons that have nothing to
do with sign-in.

**Could sign-in simply stay in the Testing project?** Technically yes: thanks to the basic-scope exception, anyone can
sign in and nothing expires after 7 days. But consent would show the domain rather than "AskNuvexusDoc" with a logo,
because brand verification needs a published app. It would also tie the public sign-in to a project that holds
restricted scopes. Inference: that weakens the brand review and couples two unrelated release decisions.

**Recommendation (R1):**

| Use | Project | Status | Client |
|---|---|---|---|
| Production sign-in | **new** "AskNuvexusDoc" | External, In production, brand published | Web, redirect `https://asknuvexusdoc.com/api/auth/google/callback` (path set by the brief) |
| Local development sign-in | existing Gmail project | Testing (the basic-scope exception lets any account sign in) | Web, redirect `http://localhost:8080/api/auth/google/callback` |
| Gmail plugin | existing Gmail project | Testing, unchanged | unchanged |

### 1.6 The owner's console steps (production sign-in)

**Blockers first:** `privacy.md` and `about.md` are placeholders. Write the real privacy policy, saying what the app
receives from Google at sign-in: `sub`, email, name and picture. Write a real public homepage that describes the app
and links the privacy policy. `/about` once written, or a landing page, will do; the sign-in page alone risks "only a
login page".

1. **Search Console:** add a **Domain** property `asknuvexusdoc.com`. Choose TXT and add the given value as a TXT
   record on `@` in Cloudflare DNS, then click Verify. Google says the record can take "up to two or three days" to
   show, and the record must stay in place. Use the Google account that will own the Cloud project.
2. **Cloud console → New project** "AskNuvexusDoc" (any project ID).
3. **Google Auth Platform → Get started:** app name **AskNuvexusDoc**, user support email, Audience **External**,
   developer contact email.
4. **Branding:** logo (square 120×120 PNG, at most 1 MB), homepage `https://asknuvexusdoc.com/<public page>`, privacy
   `https://asknuvexusdoc.com/privacy`, terms `https://asknuvexusdoc.com/terms`, authorized domain
   `asknuvexusdoc.com`.
5. **Data Access → Add or remove scopes:** only `openid`, `.../auth/userinfo.email`, `.../auth/userinfo.profile`. All
   three are non-sensitive.
6. **Clients → Create client → Web application** ("AskNuvexusDoc web"). Authorized redirect URI exactly as in 1.5.
   JavaScript origins aren't needed for a server-side flow. Copy the client ID and secret now, because the secret may
   not be shown again (`docs/gmail-setup.md`).
7. **Audience → Publish app** (In production). Expect no warning screen, since only non-sensitive scopes are declared.
8. **Branding → Verify Branding.** When the status reads **Ready to publish**, click **Publish branding** within 7 days.
9. **Development:** in the existing Gmail project, add a second Web client with the localhost redirect.
10. Put both client IDs and secrets in `.env` under names separate from `GOOGLE_OAUTH_*` (the Gmail client). Keep each
    client in use, because Google deletes a client after six months without use.

### 1.7 The button without Google's JavaScript

Google allows "Sign in with Google", "Sign up with Google" or "Continue with Google" (localized is fine). The standard
colour "G" can't be recoloured or resized. The themes are light (#FFFFFF fill, #747775 1 px stroke, #1F1F1F text),
dark (#131314, #8E918F, #E3E3E3) and neutral (#F2F2F2, no stroke). Text is Google Sans Medium 14/20. Padding is 12 px
before the logo, 10 px after it and 12 px after the text. The button must be shown "at least as prominently as other
third-party sign-in options". A pre-approved `signin-assets.zip` (SVG and PNG) exists; its SVGs need the Google Sans
font. Bundle the asset or the font locally so `font-src 'self'` holds. The font's licence is open question 6.7.

### 1.8 Browser and codebase notes (inference and repo facts, not Google's docs)

- The callback is a **cross-site top-level GET**, so the SameSite=Strict session cookie doesn't arrive. Bind the flow to
  the browser as `oauth.py` does: server-side state in Redis plus a SameSite=Lax nonce cookie. That also stops login
  CSRF, where an attacker would sign the victim into the attacker's account.
- Start the flow with a same-origin `fetch` POST that returns the authorize URL (as Gmail's connect does), then
  `window.location.assign`. Don't use a form POST answered by a redirect to Google: CSP `form-action 'self'` may block
  redirects after a form submission. MDN says browsers differ, and Chrome blocks them.
- nginx must keep the callback's query (`code`, `state`) out of the logs, as it does for the plugin callbacks. Build
  the redirect URI only from `PUBLIC_BASE_URLS`.
- The integration suite's `fake_google` would need a signing key and a JWKS route once signatures are checked (R2).

## 2. Cloudflare Turnstile

### 2.1 Widgets and keys

- Each widget has a public **sitekey** and a **secret key**, a mode and at least one hostname. Hostnames are FQDNs,
  with no scheme, port, path or wildcard, and a hostname covers its subdomains. Free plan: up to 20 widgets,
  10 hostnames per widget, unlimited challenges, 7-day analytics. Cloudflare's advice: "Separate your environments",
  restrict hostnames, rotate secrets, and keep `localhost` off production widgets ("Dummy sitekeys can be used from
  any domain").
- **Modes:** Managed (recommended; shows a checkbox only when risk requires it, never images or text), Non-Interactive
  and Invisible. Invisible mode requires you to "reference Cloudflare's Turnstile Privacy Addendum in your own privacy
  policy".
- Turnstile works whether or not the site is proxied by Cloudflare.
- **Owner steps:** Cloudflare dashboard → Turnstile → Add widget "AskNuvexusDoc", hostname `asknuvexusdoc.com`, mode
  Managed, pre-clearance off. Copy the sitekey and secret into `.env`. Don't add `localhost`; development uses the
  test keys (2.5).

### 2.2 The client script and rendering

- Script: `https://challenges.cloudflare.com/turnstile/v0/api.js`. "The `api.js` file must be fetched from the exact
  URL shown above. Proxying or caching this file will cause Turnstile to fail when future updates are released." It
  can't be self-hosted or pinned with SRI (inference from that sentence).
- **Implicit rendering** scans for `.cf-turnstile` at page load. **Explicit rendering** (`api.js?render=explicit`,
  then `turnstile.render(container, {sitekey, callback, …})`) is the documented choice "for dynamic content and
  single-page applications (SPAs)". `turnstile.reset(id)`, `turnstile.getResponse(id)` and `turnstile.remove(id)`
  manage the widget.
- **Options:**
  - `action`: at most 32 characters of `[A-Za-z0-9_-]`, returned by siteverify.
  - `cData`: at most 255 characters, returned too.
  - `appearance`: `always`, `execute` or `interaction-only`.
  - `execution`: `render` or `execute`.
  - `refresh-expired`: `auto`, `manual` or `never`.
  - `response-field`: false for a JSON API.
  - `language`: `auto` by default.
  - `theme`: light, dark or auto.
  - `size`: normal, flexible or compact.
  - `error-callback`: without one, an error throws. Error codes come in families: 110200 means the domain isn't
    authorized, 200500 means the iframe couldn't load.

### 2.3 CSP

Cloudflare's CSP page lists `script-src https://challenges.cloudflare.com` and `frame-src
https://challenges.cloudflare.com`. Alternatively it recommends a nonce on the `api.js` tag, which Turnstile
propagates, and it supports `strict-dynamic`. `connect-src 'self'` is needed only for pre-clearance. The widget
concept page adds: "Your application will connect to this origin". The CSP page names no `connect-src` entry, so check
the browser console in the spike (6.3). Our `style-src 'self' 'unsafe-inline'` already admits inline styles. The
outbound headers `X-Frame-Options: DENY` and `frame-ancestors 'none'` concern framing *our* pages, not our pages
framing Cloudflare.

### 2.4 Siteverify

- `POST https://challenges.cloudflare.com/turnstile/v0/siteverify`, form or JSON: `secret`, `response` (the token),
  optional `remoteip`, optional **`idempotency_key`** (a UUID "to safely retry validation requests").
- **Token:** at most 2,048 characters, "valid for 300 seconds (5 minutes)", "Single use: Each token can only be
  validated once". A replay or expiry gives `timeout-or-duplicate`.
- **Response:** `success`, `challenge_ts`, `hostname`, `error-codes`, `action`, `cdata`; `metadata.ephemeral_id` is
  Enterprise only. Error codes: `missing-input-secret`, `invalid-input-secret`, `missing-input-response`,
  `invalid-input-response`, `bad-request`, `timeout-or-duplicate`, `internal-error` (retry).
- "You must call the Siteverify API … The client-side widget alone does not protect your forms." "Tokens can be
  forged." Check `action` and `hostname`; "Set reasonable timeouts"; "Have fallback behavior for API failures"; keep
  the secret on the server; log failures without secrets.
- `remoteip`: behind the tunnel this is nginx's real-IP result (`CF-Connecting-IP` with `NGINX_REAL_IP_FROM`, repo
  fact).

### 2.5 Test keys (CI, e2e, local)

| Sitekey | Behaviour |
|---|---|
| `1x00000000000000000000AA` | always passes, visible |
| `2x00000000000000000000AB` | always fails, visible |
| `1x00000000000000000000BB` | always passes, invisible |
| `2x00000000000000000000BB` | always fails, invisible |
| `3x00000000000000000000FF` | forces an interactive challenge |

| Secret | Behaviour |
|---|---|
| `1x0000000000000000000000000000000AA` | always passes |
| `2x0000000000000000000000000000000AA` | always fails |
| `3x0000000000000000000000000000000AA` | returns `timeout-or-duplicate` |

Test sitekeys produce the token `XXXX.DUMMY.TOKEN.XXXX`. "Test secret keys: Only accept the dummy token … Production
secret keys: Only accept real tokens, reject dummy tokens." Automated browsers "are detected as bots". The guidance is
"Never use test credentials in production … Add checks to prevent test credentials in production." Inference for this
repo:
- the backend should refuse to start, or warn loudly, when a `1x…`/`2x…`/`3x…` secret meets an https
  `PUBLIC_BASE_URLS`;
- e2e (route mocks) can stub `api.js` so no Cloudflare request leaves the browser;
- the isolated integration stack needs a `fake_turnstile` and an overridable siteverify URL, like the Google and
  Razorpay fakes.

### 2.6 Privacy

The Turnstile Privacy Addendum (last updated 18 June 2025) says Turnstile processes "client IP address, TLS
Fingerprint, User-Agent Header and Sitekey and associated origin". These signals are used "not to identify, profile or
target any individuals", only to detect bots and to improve Turnstile. Cloudflare is a **processor** for bot detection
and a **controller** for improving Turnstile. For cookies the addendum points to Cloudflare's Cookie Policy and the
developer docs rather than stating that none are set. List Cloudflare (Turnstile) as a processor in the privacy
policy; that is required in Invisible mode and sensible in any mode.

### 2.7 Keeping Turnstile off the signed-in app

CSP applies per **document**. In an SPA, a client-side route change keeps the CSP of the document's first load
(browser fact). Options:

| Option | How | Verdict |
|---|---|---|
| A. Allow it site-wide | add `challenges.cloudflare.com` to the one CSP; inject `api.js` only when the widget mounts | Simplest, but every page, the signed-in app included, then allows Cloudflare's script, and once loaded it stays for the life of the document |
| **B. Per-location CSP + full page loads** | nginx `location = /signup`, `/forgot-password`, `/reset-password`, `/verify-email` (and `/login` if login gets an adaptive challenge) serve the same `index.html` with the extended CSP; every other route keeps today's CSP. The SPA crosses the boundary with `window.location.assign` (after sign-in, and to reach these pages from the app). These pages first check for a session and, if one exists, leave with a full load before loading `api.js`. | **Recommended.** No multi-page build, and the signed-in document never allows or loads third-party script. Note that nginx `add_header` in a location replaces the server-level headers, so repeat all of them, as the existing locations do. |
| C. Separate HTML entry | a second Vite entry (`auth.html`) for the signed-out pages | Same isolation as B with a stronger separation of code, but more build work |
| D. Sandboxed iframe | a same-origin `/turnstile-frame.html` with `sandbox allow-scripts` (opaque origin) passing the token by `postMessage` | Not documented by Cloudflare. An opaque origin may break its hostname check (110200), and it needs `frame-ancestors 'self'` on that path. Only after a spike. |
| E. Nonce + `strict-dynamic` (Cloudflare's preferred CSP) | nginx `sub_filter` puts `$request_id` into `index.html` and the header | Possible, but it changes the CSP model for every page. Not needed for B. |

Context (inference): the site is served through a Cloudflare Tunnel, so Cloudflare already terminates TLS and could
read or alter every response. Turnstile's script adds a supply-chain path (an unversioned file), not a new party to
trust. B keeps even that away from pages that hold a session.

### 2.8 Where to require it (recommendation)

Always on **sign-up**, **forgot password** and **resend verification**, with distinct `action`s (`signup`, `reset`,
`resend`). On **login** only adaptively, after a few failures for the account or IP. OWASP: "It may be more
user-friendly to only require a CAPTCHA be solved after a small number of failed login attempts". Not on the Google
start, which is rate-limited per IP instead. CAPTCHA is "defense-in-depth … rather than as a preventative" (OWASP), so
the rate limits in 4.6 still apply.

## 3. Transactional email

### 3.1 Comparison

| | **Resend** | **Brevo** | **Cloudflare Email Sending** (Email Service) |
|---|---|---|---|
| Free tier | **3,000/month, 100/day** (a UTC calendar day, sent plus received emails, each recipient counts); 3 domains; no overage on Free | **300 emails/day** "once we approve your account for sending" | none: needs **Workers Paid** ($5/month minimum), which includes 3,000/month, then $0.35 per 1,000 |
| First paid step | Pro $20/month for 50,000 (no daily cap), $0.90 per 1,000 overage | Starter "from 5,000 emails per month" (the pricing page also sells "No Brevo logo", so free mail carries Brevo branding per third-party reviews; unconfirmed for transactional mail) | usage above |
| API | `POST` email API, **`Idempotency-Key`** (1–256 chars, kept 24 h), 10 requests/s per team, quota headers (`x-resend-daily-quota`), 429 `daily_quota_exceeded` | `POST /v3/smtp/email` (1,000 RPS) | REST API and Workers binding |
| SMTP | `smtp.resend.com`, 465/2465 (implicit TLS) or 25/587/2587 (STARTTLS), user `resend`, password = API key; `Resend-Idempotency-Key` header works over SMTP | `smtp-relay.brevo.com`, 587/2525 or 465 (SSL/TLS); an SMTP key, not the API key | `smtp.mx.cloudflare.net:465`, user `api_token`, password a Cloudflare API token with **Email Sending: Edit** |
| Domain records | `send` MX → `feedback-smtp.<region>.amazonses.com` (priority 10), `send` TXT `v=spf1 include:amazonses.com ~all`, TXT `resend._domainkey` (DKIM key); DMARC added by you; Domain Connect "Sign in to Cloudflare" can add them | Brevo code TXT, DKIM, DMARC; automatic setup via the DNS provider; exact records not readable (the help centre answered 403 or a JS challenge). Third-party guides show TXT `brevo-code:…` on `@`, CNAME `brevo1._domainkey`/`brevo2._domainkey`, and a DMARC TXT. | added automatically on `cf-bounce` (3 MX, SPF TXT, DKIM TXT `cf-bounce._domainkey`) **plus `_dmarc` `v=DMARC1; p=reject;`** |
| Account health | bounce rate under **4%** and spam rate under **0.08%**, "or may result in a temporary pause in sending"; automatic suppression list (bounce, complaint) | not checked | new accounts start with a "conservative daily quota" that rises with reputation |
| Gotchas | regions us-east-1, eu-west-1, sa-east-1, ap-northeast-1 (no India region); account data is stored in the US whatever the region; email content kept 30 days; open and click tracking off by default | **Authorized IPs**: requests from IPs off the list are blocked when "Block unknown IP addresses" is on, and one guide says it is on by default for accounts created after 16 May 2024. That matters for a home connection whose IP changes. | **Beta**; transactional only; the automatic `p=reject` DMARC would bite any other sender on the domain |

**Cloudflare Email Routing** (inbound) forwards incoming mail to verified addresses or Workers and is free. It can
send only to *verified destination addresses* in the account, so for real users it is receive-only. Sending to
arbitrary recipients is the separate **Email Sending** product above.

### 3.2 Recommendation (R6)

**Resend, free tier**, From `AskNuvexusDoc <no-reply@asknuvexusdoc.com>`. Resend "strongly recommend[s] sending emails
from a subdomain" for reputation isolation, which would make the From address `no-reply@mail.asknuvexusdoc.com`. The
owner chooses; the records differ only by the subdomain. Region: Resend says to pick the region "closest to the
majority of your recipients". For India that is Tokyo (`ap-northeast-1`) by geography (inference); data stays in the
US either way. Leave open and click tracking **off**: click tracking would rewrite the verification and reset links.

Watch two numbers. The 100/day cap can be exhausted by bot sign-ups, so the app needs its own daily cap below it with
an admin alert (4.6). And bounces from fake addresses count toward the 4% that pauses the account, which is one more
reason for Turnstile and per-address limits. Move to Pro ($20) when real volume nears about 80 a day. Brevo is the
fallback if the free 300/day matters more than the branding and the IP allowlist. Cloudflare Email Sending is worth
revisiting after its beta, because the domain is already on Cloudflare.

### 3.3 DNS records the owner adds in Cloudflare (Resend, sending from the apex)

Values come from Resend's **Records** tab. In Cloudflare, set every mail-related record to **DNS only** (grey cloud)
and TTL Auto.

| Type | Name (in the `asknuvexusdoc.com` zone) | Value | Purpose |
|---|---|---|---|
| MX | `send` | `feedback-smtp.<region>.amazonses.com`, priority 10 | bounces (Return-Path domain) |
| TXT | `send` | `v=spf1 include:amazonses.com ~all` | SPF for the Return-Path domain |
| TXT | `resend._domainkey` | `p=…` from Resend | DKIM (aligned with the From domain) |
| TXT | `_dmarc` | start `v=DMARC1; p=none; rua=mailto:dmarc@asknuvexusdoc.com;`, then `p=quarantine`, later `p=reject` once reports show only passing mail | DMARC (Resend's staged advice) |
| TXT | `@` | `google-site-verification=…` | Search Console, for Google's brand verification (1.6); keep it |

With a sending subdomain `mail`, the first three names become `send.mail` and `resend._domainkey.mail`. A `_dmarc`
record on the apex also covers subdomains (the DMARC organizational-domain fallback; inference from the standard).

To *receive* DMARC reports, replies or `support@`, turn on **Cloudflare Email Routing** for the apex. It adds its own
root MX and SPF records; Resend's SPF sits on `send`, so the two don't clash. Then route `dmarc@` and `support@` to the
owner's mailbox. A `no-reply` From with a monitored `Reply-To: support@…` is common practice (inference).

**Gmail's sender rules** (since 1 February 2024): every sender needs SPF **or** DKIM, valid forward and reverse DNS,
TLS, RFC 5322 formatting and a spam rate under 0.3%. Senders of more than 5,000 a day to Gmail also need SPF **and**
DKIM, DMARC (`p=none` allowed) and From alignment, and one-click unsubscribe for marketing. Resend's DKIM on the From
domain plus DMARC covers all of it.

### 3.4 SMTP (stdlib) or the provider's HTTP API (R7)

**Recommend SMTP through one mailer module**, generalized from `app/ops/channels.EmailChannel` (`smtplib` in
`asyncio.to_thread`, implicit TLS on 465 or STARTTLS on 587):

- It is provider-neutral: Resend, Brevo and Cloudflare all speak it, so switching is four settings and no code.
- It reuses tested code and adds no dependency.
- Resend's idempotency works over SMTP (`Resend-Idempotency-Key`).

Its costs: coarser errors than an HTTP 429 `daily_quota_exceeded`, one TLS handshake per mail, and the integration
stack needs an SMTP fake (the current fakes are HTTP). The HTTP API would add quota headers and structured errors;
Resend's is one `POST`. Either way, mails go through a Redis job (the worker already runs `BLPOP` loops), never inside
the request:
- it keeps request timing equal for enumeration resistance (4.4);
- a provider timeout can't fail the sign-up;
- retries reuse one idempotency key.

Keep monitor alerts on a channel that doesn't depend on this provider (ntfy or webhook), so a paused or exhausted
provider still gets reported (inference).

### 3.5 Unifying today's mail

Plan reminders use `BILLING_EMAIL_FROM` with the `ALERT_SMTP_*` connection, and receipts are PDFs. The brief can move
both onto the new mailer (`MAIL_*` settings, one From). Receipts and reminders are transactional mail, so no
unsubscribe link is required; Gmail requires one-click unsubscribe only for marketing and subscribed mail from senders
of more than 5,000 a day.

### 3.6 India-specific notes

- No provider here has an India sending region (Resend: Virginia, Ireland, São Paulo, Tokyo). Resend keeps account
  data in the US, and Brevo is an EU (France) company (general knowledge).
- **DPDP Rules, 2025** were notified on 13/14 November 2025 with an 18-month phased compliance window (PIB releases).
  They require a separate, plain-language consent notice stating the purpose, and consent that can be withdrawn. That
  supports R13 and recording consent at sign-up. Check the Rules' text with counsel for cross-border transfer and
  retention; not researched here.
- Nothing found on Indian mailbox providers' sending rules; Gmail's rules (3.3) dominate in practice (inference).

## 4. Auth best practices (OWASP ASVS 5.0, OWASP cheat sheets, NIST SP 800-63B-4)

### 4.1 Sign-up and verification flow, and account pre-hijacking

- OWASP Email Validation cheat sheet: "Email ownership must be verified before enabling account use … Do not activate
  accounts before verification is completed"; tokens random, single-use, time-limited.
- **Pre-hijacking** (Sudhodanan and Paverd, USENIX Security 2022; at least 35 of 75 popular services vulnerable):
  - *Classic-Federated Merge*: the attacker signs up with the victim's email and a password, the victim later uses SSO,
    and both end up in one account;
  - *Unexpired Session*: the attacker's session survives the victim's password reset;
  - *Trojan Identifier*: the attacker links their own federated identity before the victim takes over;
  - *Unexpired Email Change*;
  - *Non-verifying IdP*.

  The root cause is "failure to verify ownership of the claimed identifier". The mitigations are:
  - require verification "before allowing any further actions associated with the account";
  - on reset, "Sign out all other sessions", cancel pending email changes and review linked identities;
  - when merging classic and federated accounts, "ensure that the user currently controls both accounts";
  - prune unverified accounts.
- **Design consequence (R10, inference):** keep pending sign-ups apart from `users`. A link alone must not activate an
  account whose password someone else may have chosen. Finish only with the link's token **and** the pending row's
  password, entered on the verify page; that also signs the user in with a fresh session. A Google sign-up where Google
  is authoritative for the address deletes pending rows for that address.

### 4.2 Password rules

| Source | Rule |
|---|---|
| ASVS 6.2.1 (L1) | at least 8 characters, "a minimum of 15 characters is strongly recommended" |
| NIST 800-63B-4 §3.1.1.2 | single-factor passwords "SHALL" be at least 15; 8 allowed only as part of MFA; SHOULD allow at least 64; accept printing ASCII, space and Unicode (each code point one character); SHOULD apply **NFC** before hashing |
| ASVS 6.2.4 (L1) | check against "at least, the top 3000 passwords which match the application's password policy" |
| ASVS 6.2.12 (L2) | check against breached passwords |
| ASVS 6.1.2/6.2.11 (L2) | block a documented list of context words (product name, …) |
| ASVS 6.2.5, NIST | no composition rules |
| ASVS 6.2.7–6.2.10 | allow paste and password managers; verify exactly as received (no truncation or case change); allow at least 64 characters; no periodic rotation |
| NIST | blocklist rejection must state the reason and give guidance; rate-limit failed attempts |
| ASVS 6.2.6, NIST | masked input (`type=password`) with an optional show toggle |
| OWASP | a strength meter (zxcvbn-ts) is suggested |

The repo today has an 8-character minimum and a 256-character maximum for new passwords, no blocklist and no NFC
(repo fact). Normalizing to NFC changes the hash of a non-NFC password, so legacy hashes need a raw-input fallback
(inference).

### 4.3 Breached-password check (HIBP Pwned Passwords)

`GET https://api.pwnedpasswords.com/range/{first 5 hex of SHA-1}` returns every matching suffix with a count. Only
the 5-character prefix leaves the server (k-anonymity), and the API needs no key: "There is no rate limit on the Pwned
Passwords API". Send **`Add-Padding: true`** so every response holds 800–1,000 entries, which hides the prefix from
anyone watching response sizes; padded entries have a count of 0. A user agent is required. Check the full password
on submit, not per keystroke. An outage must not block sign-up, so fail open and rely on the local blocklist
(inference). Privacy: the request reveals to HIBP (behind Cloudflare) only a 5-hex prefix shared by hundreds of
passwords.

### 4.4 Account-enumeration resistance

- OWASP: "respond with a generic error message regardless of whether … The user ID or password was incorrect … The
  account does not exist … The account is locked or disabled". It names registration explicitly. Correct examples:
  "Login failed; Invalid user ID or password", "If that email address is in our database, we will send you an email
  to reset your password", "A link to activate your account has been emailed to the address provided."
- HTTP status codes and **processing time** must not differ. "making sure that the same logic is followed, instead of
  using a quick exit method". ASVS 6.3.8 (L3) covers login, registration and forgot-password.
- **Inference for this codebase:**
  - run Turnstile verification first on both paths;
  - hash the submitted password on both sign-up paths (the login already uses a dummy hash);
  - enqueue a mail job on both paths (a "verify" mail or a "you already have an account" mail);
  - answer 202 with the same body;
  - keep login's single message ("Invalid email/username or password"), and return it too for unverified or
    Google-only accounts.

### 4.5 Verification and reset tokens

- **OWASP (Forgot Password):**
  - CSPRNG tokens, long enough, linked to one user, "Invalidated after they have been used", stored securely;
  - build the URL from a fixed or validated base, never from the `Host` header;
  - HTTPS only;
  - `Referrer-Policy: no-referrer` on the reset page;
  - rate-limit token guessing;
  - make no change to the account until a valid token arrives (no lockout);
  - confirm the new password twice;
  - send a "password changed" email;
  - "Don't automatically log the user in";
  - offer or force the end of other sessions.
- **OWASP (Email cheat sheet):** never log tokens or full verification or reset URLs, and mask emails in logs
  (`j***@example.com`).
- **ASVS 6.5.2** (written for lookup secrets): a secret with at least 112 bits of entropy may be stored with a plain
  hash such as SHA-256. **ASVS 6.4.1:**
  activation codes "expire after a short period of time or after they are initially used". ASVS 6.5.5 caps
  out-of-band *authentication* codes at 10 minutes; an email link used only for recovery isn't one, but it shows the
  direction.
- **Recommendation (R8, inference beyond the sources):**
  - 32 random bytes (`secrets.token_urlsafe(32)`), stored as SHA-256;
  - verification valid 24 h, reset 30 min;
  - links like `https://asknuvexusdoc.com/reset-password#t=<token>`. The fragment never reaches nginx's access log
    (which records query strings) or a Referer;
  - the page POSTs the token (same-origin, so it passes the cross-site guard); a mail scanner's GET prefetch spends
    nothing;
  - a reset invalidates every other reset token of the user; a password change does too;
  - Resend's 30-day copy of the mail then holds only dead links.

### 4.6 Rate limits and email bombing

OWASP: limit reset requests "on a per-account basis, requiring a CAPTCHA, or other controls. Otherwise an attacker
could make thousands of password reset requests per hour for a given account, flooding the user's intake system".
Throttle counters "should be associated with the account itself, rather than the source IP address". Use exponential
back-off rather than permanent lockout, because lockout enables denial of service. NIST requires rate-limiting failed
attempts. The suggested numbers below are not from a source; they are for the brief to set:

| Key | Suggested |
|---|---|
| sign-up per IP | 5/hour, 20/day |
| mails per recipient address (verify, reset, "already registered") | 3/hour, 6/day, and at most one per 60 s |
| reset requests per IP | 10/hour |
| verify/reset POSTs per IP (token guessing) | 30/hour |
| Google sign-in starts per IP | 20/minute (like plugin connects) |
| **global outbound mail per UTC day** | below the provider cap (for example 80 on Resend free), admin alert at 80% |
| login | unchanged: 10 failures per identifier, 100 per IP, per 15 min; adaptive Turnstile after a few |

Behind the tunnel, per-IP limits need `NGINX_REAL_IP_FROM` (repo fact).

### 4.7 Login by email or username

OWASP allows email as the login identifier "provided the email is verified during sign-up", with an option to choose a
username. Usernames here can't contain `@` (repo fact), so `@` means "email" without ambiguity. Throttle on the
canonical identifier and give the same error either way. A Google-only account has no password; the
`password_hash` NOT NULL column needs a nullable value or an unusable marker (brief decision). Plan for each new email
user to get a username, either generated or chosen, because exports, receipts and admin pages show it (repo fact).

### 4.8 Email canonicalization

OWASP:
- lowercase the domain; convert IDN domains to punycode;
- "Normalize Unicode input"; watch for homoglyphs;
- the local part is "technically case-sensitive under SMTP, though many providers do not enforce this";
- store the original for sending and a canonical form for comparison;
- avoid provider tricks such as Gmail's dot removal "unless fully controlled";
- apply one documented policy everywhere: sign-up, login, reset, recovery and linking.

**Recommendation (R12):** trim, NFC, `idna` (already a dependency) for the domain, lowercase the whole address for the
unique canonical key, accept only ASCII local parts at first (simpler and no homoglyph local parts; an owner decision),
and use a maintained validation library.

### 4.9 Linking Google to an existing account

OWASP "Secure Federated Account Linking":
- key the identity on `iss` + `sub`;
- "Do not automatically link accounts solely because their `email` … match";
- "Require an authenticated session with the existing application account before adding or removing linked
  identities", with re-authentication;
- validate the new ID token and bind it to the linking session;
- after unlinking, refuse that identity;
- notify the user when an identity is added or removed.

The pre-hijacking study: when merging, "the user should be required to provide or reset the password for the classic
account".

Google lets you skip "password or other challenge methods" only when it is authoritative (1.3).

**R3** combines these: auto-link only when the local email is verified *and* Google is authoritative for it. Then
notify the address, end the old sessions (since a password might be known to someone else, inference), and show
linked identities in Settings with an Unlink. In every other case, ask for the password, then link. An account created
through Google with a non-authoritative address keeps that email **unverified** until the app's own email check
passes. Until then it can't be offered a password reset (inference).

### 4.10 Sessions after verification, reset and linking

- ASVS 7.2.4: a new session token on every authentication.
- ASVS 7.4.3: offer to end all other sessions after a credential change "including password change via reset".
- ASVS 7.4.2: end sessions on deactivation.
- ASVS 7.5.1: re-authenticate before changing sensitive attributes such as the email address.
- ASVS 6.3.7 (L3): notify after credential changes.
- OWASP's change-email process: confirm at both the old and new addresses, with the current password.

In this repo, bumping `token_version` ends all sessions. A reset should bump it and not sign in. Verification signs in
fresh (R10). The existing "Log out other sessions" remains.

### 4.11 Terms consent, data minimisation, pruning

- Record terms acceptance at account creation: the version, the time, and the method (email or Google). For Google,
  ask on an interstitial before the user row is created. Google's consent screen is not consent to the app's terms
  (inference). DPDP requires a consent notice (3.6).
- Data minimisation: store `sub`, the email, the display name if wanted, and the consent record. Leave out the
  picture URL unless it is shown. Google tokens aren't needed after the callback, so drop them (ASVS 10.1.1: tokens
  only where needed).
- Prune pending (unverified) sign-ups after **7 days**, which suggests a worker sweep (the study's "Unverified-Account
  Pruning"; the number is a suggestion).
- Logging: no tokens, no full links; emails masked (OWASP).

## 5. Inputs for the brief (inference, not from the sources)

- Suggested settings (alongside the existing Gmail ones; names are the brief's to fix):
  - `SIGNUP_ENABLED`;
  - `GOOGLE_SIGNIN_CLIENT_ID`, `GOOGLE_SIGNIN_CLIENT_SECRET`;
  - `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY`, and `TURNSTILE_SITEVERIFY_URL` (tests only);
  - `MAIL_SMTP_HOST`, `MAIL_SMTP_PORT`, `MAIL_SMTP_SECURITY`, `MAIL_SMTP_USERNAME`, `MAIL_SMTP_PASSWORD`;
  - `MAIL_FROM`, `MAIL_REPLY_TO`, `MAIL_DAILY_LIMIT`.
- Serve the sitekey from a public config endpoint, so test and production keys switch without a rebuild.
- Data, probably needing an Alembic migration after head 0003:
  - `users.email` and its canonical unique form, plus `email_verified_at`;
  - a nullable or unusable `password_hash`;
  - `user_identities` (`provider`, `issuer`, `subject`, unique; `email_at_link`; `created_at`);
  - `pending_signups` (canonical email, password hash, token hash, expiry, terms version);
  - `auth_tokens` (purpose, user, token hash, expiry, used_at);
  - consent records.
- nginx:
  - the per-location CSP of 2.7 option B;
  - the Google callback location with logging off, like the plugin callbacks;
  - `limit_req` zones for the unauthenticated auth endpoints.
- Tests:
  - backend: unit tests with Turnstile's dummy keys and a fake siteverify; JWKS-signed fake ID tokens;
  - enumeration: equal status and body, and timing within a tolerance;
  - token single use, expiry and fragment handling;
  - pre-hijack scenarios 4.1 and 4.9;
  - Playwright: the `api.js` stub;
  - integration: `fake_turnstile`, a fake SMTP server, and a JWKS route on `fake_google`.

## 6. Open questions to check in a spike

1. Whether Google's consent screen for the brand-verified production project shows "AskNuvexusDoc" and the logo with
   only basic scopes, and what a Testing project shows for the development client (domain or name).
2. Whether Google's brand review accepts the chosen homepage. It must not be "only a login page", and community
   reports (not Google) describe rejections when the name on the homepage differs from the consent screen.
3. Turnstile under our CSP: are `script-src` and `frame-src` enough, or does the console show a blocked `connect-src`?
   Does `Referrer-Policy: same-origin` affect the hostname check (110200)?
4. Turnstile behaviour across the B-option boundary: the widget after an SPA route change within the signed-out
   pages, and `turnstile.remove` on unmount.
5. Resend: domain at the apex or a `mail` subdomain; region choice; whether the free tier adds any branding (none
   found); bounce handling for typos at sign-up.
6. Brevo (only if chosen): the exact DNS records shown in the account, whether free transactional mail carries the
   Brevo footer, and the Authorized-IPs default for a new account.
7. The Google Sans font licence for self-hosting, or use the pre-approved PNG/SVG asset.
8. The minimum password length: ASVS L1 (8, current) or NIST single-factor (15). This is the owner's call.

## 7. Sources (all read 2026-10-28)

**Google**
- OpenID Connect: <https://developers.google.com/identity/openid-connect/openid-connect>
- Discovery document: <https://accounts.google.com/.well-known/openid-configuration>
- Verify the Google ID token (authoritative cases): <https://developers.google.com/identity/gsi/web/guides/verify-google-id-token>
- OAuth 2.0 for web server apps (redirect URI rules): <https://developers.google.com/identity/protocols/oauth2/web-server>
- Manage App Audience (Testing exception, cap): <https://support.google.com/cloud/answer/15549945>
- Manage OAuth App Branding: <https://support.google.com/cloud/answer/15549049>
- Manage App Data Access (demo video covers all clients): <https://support.google.com/cloud/answer/15549135>
- Manage OAuth Clients (six-month deletion): <https://support.google.com/cloud/answer/15549257>
- Submit for brand verification: <https://developers.google.com/identity/protocols/oauth2/production-readiness/brand-verification>
- OAuth App Verification Help Center: <https://support.google.com/cloud/answer/13463073>, <https://support.google.com/cloud/answer/9110914>
- Verification requirements: <https://support.google.com/cloud/answer/13464321>
- When verification is not needed: <https://support.google.com/cloud/answer/13464323>
- Submitting your app for verification: <https://support.google.com/cloud/answer/13461325>
- Requesting minimum scopes (separate projects): <https://support.google.com/cloud/answer/13807380>
- Unverified apps: <https://support.google.com/cloud/answer/7454865>
- OAuth Application Rate Limits: <https://support.google.com/cloud/answer/9028764>
- Sign in with Google branding guidelines: <https://developers.google.com/identity/branding-guidelines>
- Search Console ownership verification: <https://support.google.com/webmasters/answer/9008080>
- Gmail email sender guidelines: <https://support.google.com/a/answer/81126>

**Cloudflare**
- Turnstile get started: <https://developers.cloudflare.com/turnstile/get-started/>
- Embed the widget: <https://developers.cloudflare.com/turnstile/get-started/client-side-rendering/>
- Widget configurations: <https://developers.cloudflare.com/turnstile/get-started/client-side-rendering/widget-configurations/>
- Validate the token: <https://developers.cloudflare.com/turnstile/get-started/server-side-validation/>
- Content Security Policy: <https://developers.cloudflare.com/turnstile/reference/content-security-policy/>
- Testing: <https://developers.cloudflare.com/turnstile/troubleshooting/testing/>
- Exclude from E2E tests: <https://developers.cloudflare.com/turnstile/tutorials/excluding-turnstile-from-e2e-tests/>
- Widgets (modes): <https://developers.cloudflare.com/turnstile/concepts/widget/>
- Hostname management: <https://developers.cloudflare.com/turnstile/additional-configuration/hostname-management/>
- Plans: <https://developers.cloudflare.com/turnstile/plans/>
- Client-side error codes: <https://developers.cloudflare.com/turnstile/troubleshooting/client-side-errors/error-codes/>
- Turnstile Privacy Addendum: <https://www.cloudflare.com/turnstile-privacy-policy/>
- Email Service: <https://developers.cloudflare.com/email-service/>; pricing <https://developers.cloudflare.com/email-service/platform/pricing/>; limits <https://developers.cloudflare.com/email-service/platform/limits/>; domains <https://developers.cloudflare.com/email-service/configuration/domains/>; SMTP <https://developers.cloudflare.com/email-service/examples/email-sending/smtp/>; FAQ <https://developers.cloudflare.com/email-service/reference/faq/>
- Workers pricing: <https://developers.cloudflare.com/workers/platform/pricing/>

**Resend**
- Quotas and limits: <https://resend.com/docs/knowledge-base/account-quotas-and-limits>
- Pricing: <https://resend.com/docs/knowledge-base/what-is-resend-pricing>
- Usage limits (API): <https://resend.com/docs/api-reference/rate-limit>
- Add and verify a domain: <https://resend.com/docs/add-a-domain>
- Cloudflare DNS guide: <https://resend.com/docs/guides/dns/cloudflare>
- DMARC: <https://resend.com/docs/dashboard/domains/dmarc>
- Regions: <https://resend.com/docs/dashboard/domains/regions>
- SMTP: <https://resend.com/docs/send-with-smtp>
- Idempotency keys: <https://resend.com/docs/dashboard/emails/idempotency-keys>
- Subdomain vs root: <https://resend.com/docs/knowledge-base/is-it-better-to-send-emails-from-a-subdomain-or-the-root-domain>
- Custom Return-Path: <https://resend.com/docs/dashboard/domains/custom-return-path>
- Tracking: <https://resend.com/docs/dashboard/domains/tracking>
- Suppressions: <https://resend.com/docs/dashboard/emails/email-suppressions>

**Brevo**
- Pricing (free 300/day, "No Brevo logo"): <https://www.brevo.com/pricing/>
- SMTP relay: <https://developers.brevo.com/docs/smtp-integration>
- Rate limits: <https://developers.brevo.com/docs/api-limits>
- IP security: <https://developers.brevo.com/docs/ip-security>
- Supabase SMTP guide (host `smtp-relay.brevo.com`): <https://developers.brevo.com/docs/supabase-smtp-integration>
- Domain authentication (not readable: 403 or JS challenge; known only from search results): <https://help.brevo.com/hc/en-us/articles/12163873383186>
- Authorized IPs (search result only): <https://help.brevo.com/hc/en-us/articles/5740111683858>

**Standards and others**
- OWASP ASVS 5.0 V6 Authentication: <https://github.com/OWASP/ASVS/blob/master/5.0/en/0x15-V6-Authentication.md>
- OWASP ASVS 5.0 V7 Session Management: <https://github.com/OWASP/ASVS/blob/master/5.0/en/0x16-V7-Session-Management.md>
- OWASP ASVS 5.0 V10 OAuth and OIDC: <https://github.com/OWASP/ASVS/blob/master/5.0/en/0x19-V10-OAuth-and-OIDC.md>
- OWASP Authentication Cheat Sheet: <https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html>
- OWASP Forgot Password Cheat Sheet: <https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html>
- OWASP Email Validation and Verification Cheat Sheet: <https://cheatsheetseries.owasp.org/cheatsheets/Email_Validation_and_Verification_Cheat_Sheet.html>
- OWASP Input Validation Cheat Sheet: <https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html>
- NIST SP 800-63B-4, password verifiers: <https://pages.nist.gov/800-63-4/sp800-63b/authenticators/>; appendix on password strength: <https://pages.nist.gov/800-63-4/sp800-63b/passwords/>
- HIBP Pwned Passwords API: <https://haveibeenpwned.com/API/v3#PwnedPasswords>
- Pre-hijacked accounts (USENIX Security 2022): <https://www.usenix.org/conference/usenixsecurity22/presentation/sudhodanan>, <https://arxiv.org/abs/2205.10174>
- MDN, CSP `form-action` and redirects: <https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Security-Policy/form-action>
- DPDP Rules, 2025 notified (search result only): <https://www.pib.gov.in/PressReleasePage.aspx?PRID=2190014>
