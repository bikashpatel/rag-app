# Brief: Self-service sign-up, Google sign-in, password reset and transactional email

> Requirements for opening AskNuvexusDoc to the public: email + password sign-up with email verification, "Continue
> with Google" (OpenID Connect), sign-in by email or username, "Forgot password", adding, changing, confirming and
> undoing a change of one's email, Cloudflare Turnstile against bots, and one transactional mailer (Resend over SMTP)
> used by every email the app sends users: verification, reset, security notices, receipts and plan reminders. Also:
> the admin's sign-up controls, the front door for new visitors, the data model and migration 0004, nginx, HTTPS and
> cookies, monitoring, the tests, the builder split and the owner's setup steps. `SPEC.md` is the binding contract.
> Section 11 of this brief is the contract change; stage 0 (section 16) applies it to `SPEC.md`,
> `frontend/src/api/types.ts` and the backend schemas before any feature code is written. After that, if this brief and
> `SPEC.md` disagree, `SPEC.md` wins and the brief is fixed. Facts about Google, Cloudflare, Resend, Brevo, OWASP, NIST
> and HIBP come from [`signup-research.md`](signup-research.md) ("research" below, with its section numbers and
> recommendations R1 to R13). General rules: [`README.md`](README.md). Plans, checkout and access follow
> [`plans-billing.md`](plans-billing.md) ("the plans brief"); this brief changes it only where S25, S31 and 4.12 say. Looks
> follow [`signup-auth-visual.md`](signup-auth-visual.md) ("the visual brief"), itself built on
> [`ui-visual-design.md`](ui-visual-design.md). On looks the visual brief wins; on behaviour, accessibility and the
> contract this brief wins.
>
> **Revision 2** (2026-11-01), final: designed and reviewed, not built. Revision 1 was reviewed for security (14
> findings, SEC-01 to SEC-14), for product (15 findings, UX-01 to UX-15) and by the designer (16 suggestions, B1 to B16
> of the visual brief). Section 0.2 records the response to each; 0.3 the parts not taken and why.
>
> Nothing here is legal advice. The privacy policy and terms are the owner's to write (section 17).
>
> **Names, 2026-11-02 (the owner's decision, after the build):** sign-up asks for the person's name, "Your name", on
> the email form and on Google's "Create your account" step, and Settings → Account → **Name** changes it. It is
> `users.display_name` (migration 0006; `pending_signups.display_name` until the account exists), shown by one helper:
> the name, else a username that isn't a generated handle, else the email's local part, else the username; the chat
> greets with its first word. This supersedes S3's and S45's "people see their email as their name" (3.1 and 3.3):
> the generated handle stays, but people now see their name. `SPEC.md` (sections 4, 6 and 8) has the rules; mails
> still name the account by its address, and Google's `profile` scope is still not asked for (the name is typed).

The owner asked for (2026-10-28):

- **Self-service sign-up**, now that the app is public at `https://asknuvexusdoc.com` (a Cloudflare named tunnel from
  the owner's Mac; DNS on Cloudflare) and sells Starter, Standard and Professional through Razorpay. Today only the
  admin creates accounts.

The owner decided, when asked:

- **Two ways to sign up**, the user's choice: **email + password with email verification**, and **"Continue with
  Google"** (OpenID Connect).
- **A transactional email service** (Resend or Brevo, free tiers) sending from the owner's domain (for example
  `no-reply@asknuvexusdoc.com`) with SPF, DKIM and DMARC, used for verification and "Forgot password", and also for
  **receipts and plan reminders** (which today need SMTP settings).
- **Anyone can sign up.** A new account has **no plan until it pays** (the plans brief's rules: only the plan page,
  checkout, the account and Settings).
- **Bot protection with Cloudflare Turnstile** on sign-up and wherever else it is needed.

## 0. Decisions

| # | Decision | Why |
|---|---|---|
| S1 | **Two sign-up methods into one `users` table**: email + password (verified before the account exists) and Google. Both create an ordinary user with **no plan**; after sign-up the user lands on the page they were heading for (`next`, a path on this site, kept through verification) or `/plans`. Admin-created users stay (S15) | Owner's choices. One account model keeps billing, limits and deletion unchanged (UX-03) |
| S2 | **`users.email` only ever holds a verified address**, with a unique `email_canonical`. An address nobody has proven yet lives elsewhere: in `pending_signups` (no account yet) or `pending_emails` (an account's unconfirmed address). **Nothing unverified ever identifies, signs in, links or receives account mail** | Closes the pre-hijacking family (research 4.1) by construction: an unverified address can't be squatted, merged or reset |
| S3 | **Usernames stay** (`^[a-z0-9_.-]+$`, never an `@`), but a new user's is a **random handle** (`user-` and 8 base32 characters), never derived from the email; it isn't chosen or editable in version 1. A deleted user's username is **never reissued** (`username_tombstones`) | The local part is often a name; it would leak into logs, keys and receipts, predict the username, let anyone pick an official-looking one, and a reused name would inherit a deleted user's billing records (SEC-11) |
| S4 | **One sign-in field, "Email or username".** A value with `@` is an email (looked up by canonical form, verified addresses only); anything else is a username. One generic error ("Incorrect email, username or password.") with two static hints that never depend on the account | OWASP allows email login once verified (research 4.7); `@` can't be in a username, so there is no ambiguity |
| S5 | **A sign-up is a pending row bound to the chosen password and to the browser** (R10). It becomes an account when the emailed link's token is presented **with** either that browser's sign-up cookie (one click) or the chosen password (any device). Up to **three live pending rows per address**, each with its own password, browser and links: a new sign-up never kills another's link. 5 wrong passwords delete that row (`signup_reset`); rows are deleted 7 days after creation | An attacker's earlier sign-up for a victim's address can never become the victim's account (research 4.1), a stranger can't kill the victim's link (SEC-07), and the common case needs no retyping (UX-01) |
| S6 | **Enumeration-safe by construction**: sign-up, "resend" and "forgot password" do the **same work in the request** whatever the address (validation, network limit, Turnstile, the password hash on sign-up, at most one outbox row) and answer **202 with the same body**. The **worker** decides what to send: a verification link, an "already have an account" email, a reset link, or nothing. A public job counts against the day's mail cap when it is **enqueued**, whatever it resolves to, and takes the same worker time sent or cancelled | OWASP and ASVS 6.3.8: same status, body and timing (research 4.4). The cap and the worker's pace can't tell a sent mail from a cancelled one (SEC-10) |
| S7 | **Tokens**: 32 random bytes, **minted by the worker when it sends** (never stored in clear, not even in the outbox), kept as SHA-256 in `auth_tokens` with the **SHA-256 of the address they were mailed to**, sent in the link's **URL fragment**, spent by a **POST after a click** on our page. Purposes and lifetimes: `signup` 24 h, `email` 24 h, `reset` 30 min, `email_undo` 7 days. Single use; spending a `signup`, `email` or `reset` token requires its address hash to still match the pending row's or the account's address; **every write to `users.email` or to a pending email revokes the user's other `reset` and `email` tokens**, and a password change revokes `reset` tokens. An `email_undo` token is bound to the address it restores, at most one is live per user (minted at the first change, not at later ones), and only an undo or its expiry ends it | Research R8 and 4.5. A fragment reaches neither nginx's log nor a Referer; scanners' prefetches spend nothing; a backup or a leaked outbox holds no live link; a link never outlives the address it was sent to (SEC-04); an intruder can't cancel the owner's undo by changing the email again or resetting the password |
| S8 | **Google sign-in in a new, separate Google Cloud project** ("AskNuvexusDoc": External, In production, brand-verified), scope **`openid email` only**. **Server-side authorization-code flow with PKCE S256, `state`, `nonce` and a browser-binding cookie**; the ID token's **signature is verified against Google's JWKS**, plus `iss`, `aud`, `azp`, `exp`, `iat`, `nonce`. Users are keyed on **(issuer, subject)**. No Google JavaScript on our pages; no Google token is kept | Research R1, R2, 1.1 to 1.6; the name and picture were never kept, so `profile` isn't asked for (SEC-14). The Gmail project stays in Testing and unchanged (S37) |
| S9 | **A new Google account is created only after a "Create your account" step** with the unticked terms checkbox (`/signup/google`); the callback holds the claims in a 15-minute, single-use, cookie-bound ticket until then. Reached from the sign-in page, the step says no account uses that address yet and offers **I already have an account** as an equal choice | Google's consent isn't consent to our terms (research 4.11, R13); a stray click doesn't create an account, and a returning user who picked another Google account isn't led into a duplicate (UX-09) |
| S10 | **Linking Google to an existing account automatically** happens only for a **`@gmail.com`** address (Google never reissues a deleted Gmail username) that equals the account's verified email, when the account has no Google identity. Every other case, Workspace (`hd`) addresses included, answers `link_required`: the user signs in with the password and connects Google in Settings (password re-entered). Never with a pending or unverified address; **one Google identity per user**; unlinking needs a password and a verified email to remain | Research R3 and 4.9. A lapsed Workspace domain bought by someone else recreates the address under a new `sub`; auto-linking on it would hand over the old account silently (SEC-03) |
| S11 | **Google's address claims, by kind**: `gmail` (`@gmail.com`, verified) and `workspace` (verified with `hd`) give a **new** account a verified email at once. Any other address is `unverified`: the callback **never looks it up among users**; the account is created (signed in by Google) with the address as a **pending email** and our own confirmation is sent; until confirmed the account has no email | Google: `email_verified` alone doesn't prove current ownership (research 1.3); not looking it up keeps the result from revealing an account to whoever holds that Google account (SEC-10) |
| S12 | **"Forgot password" takes an email address** (Turnstile, enumeration-safe). The reset **ends every session** (`token_version`), **doesn't sign in**, cancels a pending email change, clears the login-failure counters, sends a "password changed" notice and offers to **disconnect a linked Google sign-in** (ticked in advance when its address differs from the account's). A Google-only account with a verified email can use it to set a password. For an address with only a pending sign-up, the worker sends a fresh verification instead | OWASP Forgot Password and the pre-hijacking mitigations (research 4.5, 4.10); a reset after a compromise must be able to remove the intruder's Google identity (SEC-01); a user who lost the "check your inbox" tab still gets their link (UX-05) |
| S13 | **Changing or adding an email** needs the current password, or, for an account without one, a **Google step-up** (a fresh Google sign-in as the linked identity, valid 5 minutes, once). It is confirmed by a link to the **new** address that **only the signed-in owner of that account can spend**, and the **old** address gets a notice, the first one in 7 days with an **undo link** (that address back, the password removed, every session ended, a reset link mailed to it, a notice to the address it replaces). It doesn't end sessions | ASVS 7.5.1 re-authentication and OWASP's change-email advice (research 4.10). A confirmation clicked by whoever reads the mailbox would let a stranger or a typo'd address take the account (SEC-01); a stolen session alone can't change the recovery address of a Google-only account (SEC-12) |
| S14 | **Google-only accounts set a first password in Settings** after a Google step-up: no email round trip, and no session ends (no password existed to be compromised) | One re-authentication mechanism for password-less accounts (S13); fewer mails (S22); no surprise sign-out (UX-15) |
| S15 | **Admin-created users stay exactly as today** (username and password, now under the password policy). Version 1 has **no admin-set emails, invites or admin Google disconnect**: users add their own email in Settings → Account | Self sign-up replaces invites; an address typed by the admin needs the user's confirmation anyway, and each admin path doubled the token and mail cases (UX-13) |
| S16 | **The env admin has no email, no Google identity and no reset**; sign-up never yields `is_admin`. Bootstrap **refuses to start** when `ADMIN_USERNAME` names a self-signed-up user or one with an email or Google identity, and **warns** when `ADMIN_PASSWORD` is under 15 characters, common or contains the app's name. `ADMIN_SIGNIN_FROM=local` (optional) refuses the env admin's sign-in from anywhere but the box itself | Otherwise renaming the admin onto a self-signed-up user would hand the admin account over; the admin's username is public and its password skips the policy (SEC-05) |
| S17 | **Passwords**: at least `PASSWORD_MIN_LENGTH` (default 8, the owner may raise it to 15), at most 256 (1,024 accepted at sign-in), any characters, no composition rules, **NFC-normalized** when hashed and verified, a **bundled common-password blocklist** (at least 3,000 entries) plus context words, and a **HIBP range check** (`Add-Padding`, fails open). The same rule everywhere a password is set, the admin's forms included | ASVS 6.2.1, 6.2.4, 6.2.12, NIST 800-63B-4 (research 4.2, 4.3, R11). No raw-hash fallback: the two existing passwords are ASCII, where NFC changes nothing (UX-13) |
| S18 | **Turnstile** (Managed widget, explicit rendering) always on **sign-up, resend and forgot password**, failing closed. On **sign-in** only when Turnstile is configured and the request isn't local: after **3 failures for the identifier from the same network**, 10 for the network, or while the whole site sees over 50 failures in 5 minutes; at sign-in a Turnstile outage **falls back to the hard limits** instead of refusing. Verified on the server right after the network limit and before any other work (`success`, `hostname`, `action`, 10 s, one retry with the same idempotency key) | Research R4, R5, 2.4, 2.8. A stranger's failures must not force a challenge on the owner's browser, lock out people whose blockers stop Cloudflare, or break the admin's local sign-in, where the widget can't render (SEC-05, SEC-06) |
| S19 | **CSP**: only `/login`, `/signup` and `/forgot-password` get a second CSP adding `https://challenges.cloudflare.com` to `script-src` and `frame-src`; every other document keeps today's. The widget mounts only after `/auth/me` answered 401 **in that document**; leaving those pages for the app is a **full page load**, at once after a challenged sign-in; a page that needs the widget in a strict document **reloads itself once** | Research 2.7 option B. CSP separates documents, not the origin: Cloudflare's script can still call our API while it runs, so it runs only in documents that hold no session yet and only as long as needed. Cloudflare already terminates TLS for the site (research 2.7), so this narrows a supply-chain path rather than removing a party (SEC-13) |
| S20 | **Email through Resend's free tier over SMTP**, behind provider-neutral `MAIL_*` settings (Brevo is the fallback and the runbook's answer to a Resend pause, Cloudflare Email Sending a later option). **One mailer for every user email**; alerts keep their own `ALERT_*` channels; `BILLING_EMAIL_FROM` is removed (ignored with a warning). The one Resend-specific part is the optional bounce and complaint webhook (S42) | Research R6, R7, 3.2, 3.4: switching provider is a settings change; a paused provider can't silence the monitor |
| S21 | **A durable `email_outbox` table drained by the worker**: no secret, token or link in it; retries with backoff under one idempotency key per job; priorities; a `not_after` per kind; the recipient's address is **cleared when the job finishes** (a hash stays for counting) and rows are deleted 48 hours later. The request path only inserts a row | A provider timeout never fails or slows a request (S6); Redis can lose a minute of writes (`--save 60 1`); the outbox doesn't become a list of addresses (SEC-14) |
| S22 | **`MAIL_DAILY_LIMIT` (80) per UTC day**, below Resend's 100, shared by **who can trigger the mail**: `public` (anything a visitor or an **unpaid** account, one with no live or offline payment applied yet, can cause) up to 40%; `reset` up to 30%; all non-billing classes together up to 80%, so **receipts and reminders always have 20%**. Beyond its share, sign-up and "forgot password" answer **503 `mail_unavailable`** (the same for every address; the page offers Google), and the monitor warns at 80%. **No welcome email** | Anonymous visitors and free accounts must not be able to use up the day that receipts and resets need (SEC-02); a welcome mail doubled the cost of each sign-up (UX-04) |
| S23 | **Limits**: per **network** (an IPv4 address, an IPv6 /64, with wider buckets on the /24 and /48 at four times the limit), explicit 429s with `Retry-After`; per recipient and **per kind** (sign-up, reset, confirmation), **silent**, keyed by a delivery key that folds Gmail dots and `+tags`; global, the mail cap. nginx adds a coarse per-address `limit_req` on every unauthenticated auth endpoint | Email bombing and token guessing (research 4.6). IPv6 rotation would otherwise make every per-IP limit unlimited (SEC-06); a stranger's requests can't use up a person's own resets, and address variants can't multiply a mailbox's quota (SEC-07) |
| S24 | **Templates**: plain text plus one HTML frame (the visual brief's section 10: inline styles, one button with the full link written out under it), brand AskNuvexusDoc, **no images, tracking pixels, external resources or click tracking**; the action first, the warning after; links built from the **first `https` origin** of `PUBLIC_BASE_URLS` | Gmail's sender rules; click tracking would rewrite token links (research 3.2, 3.3); a link must never point at `localhost` (SEC-09, UX-14) |
| S25 | **Receipts (PDF attached) and plan reminders go to the account's verified email**, each with an on/off switch in Settings → Plan & billing (default on). The billing profile's separate email field is **retired** and dropped by migration 0004 (the owner re-adds his own address in Settings → Account) | The owner wants receipts by email; one verified address per account instead of an unverified side address (supersedes the plans brief's B25 and its "receipt emails" refusal; UX-13) |
| S26 | **Other emails**: security notices only: password changed or reset, password added, email changed (to the old address, with the undo link the first time in 7 days), email change undone (to the address it replaced), Google sign-in added or removed. They have no opt-out | ASVS 6.3.7 notifications |
| S27 | **Admin sign-up controls** (Admin → Users → Sign-up): open or closed (**closed at deploy**), and the email and Google switches. Effective availability also needs the mailer and Turnstile configured (Google: its client). Opening while a warning stands (visitor addresses, **payments not live**, the mail webhook, the admin password, draft policy pages) needs a second click | The owner opens sign-up once the policy pages, mail, payments and keys are ready (UX-02); domain lists are left out (UX-13) |
| S28 | **Terms consent**: an unticked checkbox on both sign-up paths, recorded on the user as `TERMS_VERSION` and the time. The real privacy policy, terms and about page come before sign-up opens and before Google's brand verification | Research R13, 1.4, 3.6 (DPDP consent notice) |
| S29 | **Audit**: `users.signup_source` (`env`, `admin`, `email`, `google`); `login_events.method` (`password`, `google`); a new `auth_events` table for account changes (sign-up, confirmations, resets, email changes and undos, links, step-ups, the admin's sign-up settings), with approximate IPs (cleared after 90 days) and masked addresses, in the user's export and deleted with them | Sign-in history already exists; account changes need a trail too (SEC-14) |
| S30 | **Data minimisation**: from Google only `sub`, the email, `email_verified` and `hd` (scope `openid email`; no name, picture or tokens); no Turnstile token stored; addresses masked in logs (`j***@example.com`); Redis keys and finished outbox rows hold address hashes, never addresses | Research 4.11 and the email cheat sheet (SEC-14) |
| S31 | **Abuse is bounded**: an account without a plan can't spend model money (the plans brief); its mail is in the `public` class; **checkout needs a verified email**, and payment links are capped per unpaid user (3 an hour, 6 a day) and for all unpaid users together (30 an hour) | Sign-up abuse would otherwise cost Razorpay API calls and reconciler work, delaying real customers' confirmations (SEC-08) |
| S32 | **Migration 0004** is additive except one column: new tables and nullable columns or server defaults, `password_hash` nullable, `signup_source` backfilled, username tombstones backfilled from billing records of deleted users, and `user_billing_profiles.email` dropped (counted in the log). Rehearsed on a restored backup | The plans brief's migration practice (its 11.1, 11.2) |
| S33 | **No new dependency**: stdlib `smtplib` (shared with the alert channel) and `hmac` (the webhook signature), PyJWT's `PyJWKSet` over `httpx` for Google's keys, `idna` pinned top-level (already locked through `httpx`), hand-written fakes (SMTP sink, Turnstile, HIBP, Google OIDC, webhook signer) | The pinning rule (CLAUDE.md) |
| S34 | **`GET /api/auth/config`** (public) gives the UI the Turnstile sitekey and what is available (sign-up methods, Google sign-in, reset, the password minimum, the terms version, the contact email) | Keys and switches change without a frontend rebuild (research 5) |
| S35 | **nginx**: `Strict-Transport-Security` on every response; the Google callback never access-logged and its errors only at `crit`; a coarse per-address zone on the public auth endpoints and one on `/api/billing/checkout`; the token pages (`/verify-email`, `/confirm-email`, `/reset-password`, `/undo-email`) send `Referrer-Policy: no-referrer`; the mail webhook logged by path | As the plugin callbacks and the Razorpay return (plans brief 6.11); SEC-08, SEC-09 |
| S36 | **A `mail` monitor check** (Admin → System): failures, a stalled outbox, the daily cap, a refused SMTP login, bounces and complaints, the public class paused, Turnstile verification errors, a sign-in failure surge, and sign-up left open without a working mailer or Turnstile | Mail problems must alert like the rest (SPEC section 10) |
| S37 | **The Gmail plugin is unchanged and separate**: its own Google project and client (`GOOGLE_OAUTH_*`), callback, cookie and Redis keys. Signing in with Google never grants Gmail access, and connecting Gmail never links a sign-in identity | Two release decisions that must not couple (research 1.5) |
| S38 | **Sessions**: completing an email sign-up and finishing a Google sign-up sign the user in with a **new** session (a `login_events` row, a fresh `sid`); a Google sign-in does too; a reset and an undo don't sign in and end every session; linking, unlinking, step-ups, email changes and setting a first password keep the current session | ASVS 7.2.4, 7.4.3 (research 4.10); UX-15 |
| S39 | **Test keys and test endpoints can't run on the public site**: a Turnstile test secret, a non-default siteverify, Google sign-in, HIBP or SMTP-without-TLS setting stops the backend at start-up when `PUBLIC_BASE_URLS` holds a public `https` origin | Cloudflare: "Never use test credentials in production" (research 2.5); the plans brief's live-key rule |
| S40 | **Real visitor addresses are a precondition for opening sign-up**: the sign-up card warns when the admin's own request arrives at a public host from a private address (`NGINX_REAL_IP_FROM` unset), and the going-live list (17) includes it | Every per-network limit is otherwise one global limit (HANDOFF "Known limitations") |
| S41 | **HTTPS and cookies**: nginx sends HSTS; Cloudflare's **Always Use HTTPS** and a **www → apex** redirect are going-live blockers (www leaves `PUBLIC_BASE_URLS`); with `COOKIE_SECURE` the session cookie is `__Host-rag_session` and the auth cookies carry `__Secure-`; Cloudflare features that inject scripts stay off | Public sign-up makes SSL stripping and planted cookies worth an attacker's time; a same-origin injected script would pass `script-src 'self'` (SEC-09) |
| S42 | **Bounces and complaints**: Resend's signed webhook (`email.bounced`, `email.complained`) and SMTP-time 5xx refusals fill `mail_suppressions`; the worker never mails a suppressed address; 3 or more bounces that are at least 2% of the day's sends **pause the `public` class** until 00:00 UTC and warn | Resend pauses the whole account at 4% bounces, receipts included; fake and typo'd addresses are the likely bounces (SEC-02, UX-08) |
| S43 | **The front door for new visitors**: while sign-up is open, "New to AskNuvexusDoc? **Create an account**" sits directly under the sign-in lede, the public top bar has **Sign up** beside Sign in, and `/pricing`'s button reads "Create an account to choose a plan" | `/` sends signed-out visitors to the sign-in page; on a phone the card's foot is below the fold (UX-03) |
| S44 | **Forms help instead of blocking**: submit buttons stay enabled and explain what is missing on submit; a "Did you mean …?" suggestion catches common email-domain typos; a pending robot check is said in words and the form goes out when it finishes; inside Instagram, Facebook, LinkedIn and similar in-app browsers the Google button becomes a notice with **Copy link** | Disabled buttons give no reason and can't take focus (UX-11); typos bounce (UX-08); Google refuses embedded browsers with `disallowed_useragent` (UX-07) |
| S45 | **The display name is the verified email** where there is one, else the username: the avatar menu, the Settings header and profile, and Admin → Billing (under the username). Mails name the account by its address, never by the username | The random handle of S3 means nothing to its owner, and an unfamiliar handle in a reset mail looks like phishing (UX-10) |

### 0.1 Rejected alternatives

- **Magic links or emailed codes instead of passwords.** The owner chose email + password; a link-only login makes the
  mailbox the only factor and every sign-in a mail (against the 100-a-day cap).
- **Accounts created unverified with limited access** (sign in, "check your inbox" banner). It makes unverified
  addresses part of `users`, which reopens squatting and merging (S2), and needs a third access state beside the plan
  states.
- **A 6-digit code typed into the sign-up tab.** Codes need attempt counters and a 10-minute life; the link plus the
  browser cookie or the password (S5) gives the same binding with one mechanism, and works on another device.
- **Google Identity Services JavaScript (One Tap, the rendered button).** Third-party script beside the session and a
  CSP exception on every page; the server-side flow needs neither (R2).
- **Sign-in in the Gmail plugin's Google project.** Publishing status, brand and verification are per project, and
  publishing it would put the restricted Gmail scopes in front of every user (research 1.5).
- **Turnstile allowed site-wide** (one CSP for all pages) or **a second HTML entry for auth pages**. The first lets
  Cloudflare's script into signed-in documents; the second is more build for the same isolation (research 2.7 A, C).
- **Turnstile on "Continue with Google"**: Google already screens its sign-ins; the start and the finish are
  rate-limited per network.
- **Turnstile Invisible mode**: Managed is Cloudflare's recommendation and needs no privacy-addendum reference.
- **Tokens minted in the request and kept in the outbox** (even encrypted). Minting at send time (S7) keeps every
  stored artefact free of live links; a resend or a retry simply mints another token.
- **A Redis list as the mail queue**: Redis saves every 60 s, so a crash could lose a reset mail silently.
- **Resend's HTTP API instead of SMTP**: better quota errors, but provider-specific; SMTP keeps the switch to Brevo or
  Cloudflare a settings change (research 3.4). The monitor reads failures from the outbox either way.
- **One SMTP server for alerts and user mail** (`ALERT_SMTP_*` reused): a paused or exhausted provider would silence
  the alerts about it (research 3.4).
- **Merging accounts on a matching email**, or linking a second Google identity: OWASP forbids the first (research 4.9);
  the second has no use for one person and complicates unlinking.
- **Choosing a username at sign-up**: one more field and a "taken" error that enumerates usernames.
- **A password strength meter (zxcvbn)**: a new npm package; the server's blocklist and breach check give the
  reasons that matter.
- **Self-service free trials**: the owner keeps "no plan until it pays"; the admin's dated grants remain the trial.
- **A recent-sign-in window instead of a step-up** (revision 1's 15 minutes): a stolen fresh session cookie would pass
  it (SEC-12).
- **A welcome email**: it doubled the mail cost of each sign-up; `/plans` already says "Your account is ready. Pick a
  plan…" (S22).

### 0.2 Review of revision 1: responses

Three reviews: security (SEC-01 to SEC-14), product (UX-01 to UX-15) and the designer's suggestions (B1 to B16 of the
visual brief, its section 0.2). Every point is answered: **34 accepted** as proposed or with the variant named, **11
adapted** (accepted in part or in another form). None is rejected outright; 0.3 lists the parts not taken.

| # | Point (severity) | Response | Where |
|---|---|---|---|
| SEC-01 | A confirmation link is spent by whoever reads the mailbox, without the account's session; a reset keeps a linked Google identity (high) | **Accepted.** `POST /auth/email/verify` needs the session of the account that asked; the page says "Sign in to confirm" otherwise. The reset page offers "Also disconnect Google sign-in", ticked in advance when that Google address isn't the account's email. Confirmation mails say we'll ask to sign in. Chains A and B are tests | S12, S13, 4.7, 4.8, 6.4, 13.5, 13.6, 15.1 |
| SEC-02 | Anonymous and unpaid users can use up the one free Resend day and pause it with bounces (high) | **Adapted.** Classes by who can trigger the mail (`public` 40%, `reset` 30%, a 20% floor for billing); no welcome mail; Google finish and unpaid accounts' email changes count in the per-network sign-up bucket with stricter per-user limits (no Turnstile in signed-in documents, S19); Resend's bounce and complaint webhook, a suppression list and an automatic pause of `public` mail at 2% bounces. No second provider for billing mail: the runbook switches all mail to Brevo, and Resend Pro comes before any announcement | S22, S26, S42, 6.5, 6.6, 8.1, 17 |
| SEC-03 | Workspace (`hd`) auto-linking allows takeover after a domain changes hands (medium) | **Accepted.** Auto-link only `@gmail.com`; `hd` addresses answer `link_required`; a test with an `hd` claim, a new `sub` and an existing verified address | S10, 4.5, 15.1 |
| SEC-04 | Tokens are bound to rows, not to the address they were mailed to (medium) | **Accepted.** Every token stores the hash of its address and is spent only while it still matches; every write to an email revokes the user's `reset` and `email` tokens (an undo link is never cancelled by a later change). The invite and admin-removal cases are gone with UX-13 | S7, 4.2, 4.7, 4.8, 9.1, 15.1 |
| SEC-05 | Anyone can force the sign-in challenge on any account, and it fails closed (medium) | **Adapted.** The challenge exists only with Turnstile configured, counts per identifier **and network**, never applies to local requests, and falls back to the hard limits when Cloudflare is down. The admin password is checked (length and blocklist at start-up, HIBP when the Sign-up card loads). `ADMIN_SIGNIN_FROM=local` is a setting and a going-live recommendation, not the default | S16, S18, 4.4, 10, 17.5 |
| SEC-06 | Per-IP limits key on full IPv6 addresses; email sign-in invites credential stuffing (medium) | **Accepted.** Network keys (IPv4 /32, IPv6 /64) with /24 and /48 buckets at four times the limit; a site-wide failure surge turns the challenge on for everyone and alerts; HIBP stays on. nginx zones stay a coarse backstop | S18, S23, 4.4, 8.1 |
| SEC-07 | The per-address limit can be turned against the address's owner; a new sign-up kills the victim's link; Gmail variants evade it (medium) | **Accepted.** One bucket per kind; a delivery key folding `+tags` and Gmail dots; up to three pending rows per address; a test that a stranger's sign-up neither kills a link nor uses a reset | S5, S23, 3.2, 3.6, 4.1, 15.1 |
| SEC-08 | Unpaid accounts can create Razorpay payment links at will (medium) | **Accepted.** Checkout needs a verified email (409 `email_required`); unpaid users get 3 links an hour and 6 a day; a site-wide cap of 30 an hour for unpaid users with a warning; an nginx zone on checkout | S31, 4.12, 8.5, 12 |
| SEC-09 | HTTPS isn't enforced; cookies can be planted; several origins; Cloudflare script injection (medium) | **Adapted.** HSTS from nginx; Always Use HTTPS and www → apex as blockers; mail links from the first https origin (chosen, so nothing to refuse); `__Host-`/`__Secure-` cookie names; Rocket Loader, Email Obfuscation, Zaraz and injected Web Analytics off | S24, S35, S41, 8.3, 12, 17.1 |
| SEC-10 | Account-existence signals remain in the cap, the worker's timing and Google results (low) | **Accepted.** Public jobs count when enqueued; public jobs take a fixed 2 s of worker time; unverified Google claims are never looked up among users | S6, S11, 6.3, 6.5, 8.2 |
| SEC-11 | Usernames generated from the email leak and are predictable; deleted names are reissued (low) | **Accepted.** Random handles, tombstones for deleted usernames, one failure counter per account whichever identifier is typed | S3, 3.3, 4.4, 4.11, 9 |
| SEC-12 | A Google-only account changes its email on a 15-minute-old sign-in (low) | **Accepted.** A Google step-up (the linked `sub`, 5 minutes, once; `max_age=0` and `auth_time` checked when Google returns it; Google has no `prompt=login`), and the old address's notice carries a 7-day undo link | S13, S14, 4.5, 4.8, 4.9 |
| SEC-13 | The Turnstile CSP isolation is overstated (low) | **Accepted.** The widget mounts only after a 401 from `/auth/me` in that document; a challenged sign-in leaves at once; S19 reworded; the spike tries a path-scoped `script-src` | S19, 7.3, 7.4, 15.3, 18.2 |
| SEC-14 | Data received or kept without need (low) | **Accepted.** Scope `openid email`; outbox addresses cleared at the end and rows deleted after 48 hours; event IPs cleared after 90 days; the policy states retention. (Invites are gone, so their lifetime is moot) | S8, S21, S29, 3.7, 8.6, 17.1 |
| UX-01 | Retyping "the password you chose" fails across devices and in mail apps; the 5th miss says "link expired" (high) | **Accepted.** A same-browser cookie finishes in one click; the password step keeps a read-only email field for password managers; "Forgot it? Sign up again"; a distinct `signup_reset` after 5 misses | S5, 4.2, 13.3 |
| UX-02 | Sign-up can open while payments aren't live (high) | **Adapted.** `payments_live` is a Sign-up card check that needs "Open anyway", and live Razorpay keys are on the going-live list. Opened early, non-testers see "Paid plans open soon. Check back in a few days." (no "we'll email you" list in version 1) | S27, 4.12, 13.9, 17.1 |
| UX-03 | New visitors land on "Welcome back"; the sign-up link is below the fold; `next` is lost (high) | **Accepted.** "Create an account" under the lede, Sign up in the public top bar, the pricing button, `next` kept on the pending row, a 390×664 check | S1, S43, 4.1, 13.1, 13.10 |
| UX-04 | The welcome mail halves sign-ups per day; no alternative when mail runs out (high) | **Accepted.** No welcome mail; Continue with Google shown first on `mail_unavailable`, `challenge_unavailable` and `too_many_attempts`; "About N sign-up emails left today" on the card; Resend Pro before any promotion | S22, 13.2, 13.9, 17.5 |
| UX-05 | Unverified users hit dead ends (medium) | **Accepted.** A reset request for a pending-only address mails a fresh verification; the 401 hint links to "send it again", carrying the typed address in `sessionStorage`, not the URL | S12, 4.3, 4.7, 13.1 |
| UX-06 | Silent limits mislead; "Start again" within 60 s is dropped; 5 sign-ups an hour per IP blocks offices and CGNAT (medium) | **Accepted.** The page mirrors the per-address limit with countdowns and says after the 3rd mail what to do; the network `signup` bucket is 20 an hour and 50 a day, with resend in its own bucket; 429s offer Google | 4.3, 8.1, 13.2 |
| UX-07 | Google refuses in-app browsers (medium) | **Accepted.** User-Agent detection replaces the Google button with a notice and Copy link; a spike on Instagram, LinkedIn and WhatsApp | S44, 13.1, 18.2 |
| UX-08 | Nothing catches email typos, which bounce (medium) | **Accepted.** "Did you mean …?" on every email field; bounces suppressed and counted (S42) | S42, S44, 13.2 |
| UX-09 | Continue with Google on `/login` can create a duplicate account (medium) | **Accepted.** The flow carries `from`; from sign-in, `/signup/google` offers "I already have an account" as an equal choice | S9, 4.5, 13.4 |
| UX-10 | The generated username becomes the visible identity (medium) | **Accepted.** The verified email is the display name everywhere; mails name the address; Admin → Billing shows it under the username | S45, 3.3, 6.4, 13.7, 13.9 |
| UX-11 | Disabled buttons give no reason (medium) | **Accepted.** Buttons stay enabled; per-field errors on submit with focus on the first; a visible "Checking you're not a robot…" that sends the form when the check finishes; "(opens in a new tab)" | S44, 7.3, 13 |
| UX-12 | Admin → Users isn't built for public sign-up (medium) | **Adapted.** A search box, the "Signed up" column with a "Last 7 days" filter, and counts on the Sign-up card. Bulk actions and a daily digest are follow-ups | 13.9, 1 |
| UX-13 | Parts beyond a one-owner business's needs (medium) | **Accepted, all five cuts**: no migrated billing email, no invites or admin email editing or admin Google disconnect, no domain lists, no NFC raw fallback, an enumeration test without SQL shapes | S15, S17, S25, S27, 15.1 |
| UX-14 | Mail copy leads with the warning; no button; no "reply" line (low) | **Accepted** (the welcome copy is moot) | S24, 6.4 |
| UX-15 | Setting a first password through the reset flow signs the user out (low) | **Adapted**, superseded by SEC-12's step-up: a first password is set in Settings with no mail and no sign-out; a reset through Forgot password still ends sessions | S14, 4.9 |
| B1 | Google first on `/login` | **Accepted** | 13.1 |
| B2 | "Create an account" as a small button in the foot block | **Adapted** by UX-03: a line directly under the lede; the foot block keeps only today's admin note, shown while sign-up is closed | S43, 13.1 |
| B3 | Turnstile in the app's resolved theme | **Accepted** | 7.3 |
| B4 | `PasswordRules({password, minimum, rejected})`, the refusal in place of the rule | **Accepted** | 13, 16.3 |
| B5 | Resend on demand with the countdown on the button | **Accepted**, with UX-06's client-side limits | 4.3, 13.2 |
| B6 | The three-step "Check your inbox" copy | **Adapted**: step 2 says one click finishes in this browser, the password elsewhere (UX-01) | 13.2 |
| B7 | `/verify-email` lede, address row, "Forgot it? Sign up again" | **Accepted**; the password field only when the browser doesn't match | 13.3 |
| B8 | `AuthCard` gains `lede` and `mark` | **Accepted** | 16.3 |
| B9 | `GoogleButton` gains `size` and "Sign in again with Google" | **Adapted**: `size` accepted; the label isn't, because Google's branding allows only "Sign in with", "Sign up with" and "Continue with Google". Every Google button says **Continue with Google**, so the `label` prop goes | 16.3 |
| B10 | Phones: the sent and closed states drop the picture | **Accepted** | 13.2 |
| B11 | Settings → Account shows the address once | **Adapted** with UX-10: the address is the profile's name; the Email fact shows its status; the Email row holds the action and the pending notice | 13.7 |
| B12 | The email as the user cell's second line, a "Signed up" column, a source select | **Accepted**, with UX-12's search box and "Last 7 days" option | 13.9 |
| B13 | "Open anyway" inside a warning notice | **Accepted** | 13.9 |
| B14 | The card's tiles from returned fields only | **Adapted**: Methods, Sign-ups, Email today, Checks (the domains tile goes with UX-13) | 11.1, 13.9 |
| B15 | Copy for states the brief left open | **Accepted** | 13.3 to 13.6 |
| B16 | One HTML frame, one button, the link written out | **Accepted**, with UX-14's copy | S24, 6.4 |

### 0.3 Review suggestions not taken, and why

- **Turnstile on `/me/email` for unpaid accounts** (SEC-02): it would put Cloudflare's script in a document that holds
  a session (S19). Unpaid accounts instead get 3 email changes a day, count in the per-network sign-up bucket, and
  their mail is in the capped `public` class.
- **A second mail provider for billing mail** (SEC-02): two sets of SMTP settings and two domains' records for one
  owner. The 20% billing floor, the suppression list and the automatic pause keep the bounce rate down; if Resend
  pauses anyway, the README runbook moves all mail to Brevo in a few minutes (S20), and the going-live list moves to
  Resend Pro before any announcement.
- **`ADMIN_SIGNIN_FROM=local` by default** (SEC-05): it would stop the owner administering from outside the Mac
  without warning. It is a setting, recommended on the going-live list.
- **HIBP for `ADMIN_PASSWORD` at start-up** (SEC-05): no network call during boot; the Sign-up card checks it once a
  day instead.
- **nginx zones keyed by network prefix** (SEC-06): nginx can't reliably derive an IPv6 /64 from the compressed text
  form; the backend's network keys do the real limiting.
- **Refusing to start when the link base isn't the https origin** (SEC-09): links now use the first https origin
  whatever the order, so there is nothing to refuse.
- **`prompt=login`** for the step-up (SEC-12): Google supports only `none`, `consent` and `select_account`
  (research 1.1).
- **"We'll email you when you can subscribe"** (UX-02): a notify list is promotional mail with its own consent; the
  admin can grant plans instead.
- **Bulk deactivate and delete, and a daily sign-up digest** (UX-12): follow-ups (section 1).
- **The welcome mail's copy** (UX-14): there is no welcome mail.
- **"Sign in again with Google"** (B9): Google's button text rules.

## 1. Scope and non-goals

**In scope:** the identity model (verified emails, random usernames and their tombstones, Google identities, pending
sign-ups and emails, tokens, consent, sources); email sign-up with verification (one click in the same browser, the
chosen password elsewhere); Google sign-up and sign-in, linking, unlinking and the step-up; sign-in by email or
username with an adaptive Turnstile challenge; forgot and reset password; adding, changing, confirming and undoing an
email; setting a first password; the mailer, outbox, templates, limits, bounces and complaints, with receipts and plan
reminders moved onto it; Turnstile; the admin's sign-up controls and mail status; the Settings → Account and Plan &
billing changes; the front door for new visitors (sign-in card, public top bar, pricing button); the checkout guards
for unpaid accounts; nginx, HSTS and cookie names; migration 0004; monitoring; tests; the owner's setup steps.

**Non-goals** (each can come later without undoing this design):

- **Two-factor authentication (TOTP, passkeys, WebAuthn): a follow-up.** `auth_tokens` and the step-up (S13) are
  where it would plug in; a Google sign-in counts as one factor.
- Other social logins (Apple, Microsoft, GitHub): `user_identities` has a `provider` column, but only Google is built.
- Magic links and emailed one-time codes (0.1).
- Self-service account deletion: users ask through the Contact page and the admin deletes (as today). The privacy
  policy says how to ask (17).
- Changing one's username; display names or avatars from Google.
- Asking existing or admin-created users to accept the terms, and asking everyone again when the terms change
  (open question 18.1.1).
- **Admin invites, admin-set or removed emails, admin "Disconnect Google"** (UX-13): users add their own email and
  manage Google in Settings. A follow-up if the owner needs them.
- **Domain allow and block lists** for sign-up (UX-13).
- **Bulk deactivate or delete in Admin → Users, a daily sign-up digest** (UX-12).
- A "tell me when paid plans open" list (UX-02); marketing or newsletter mail, unsubscribe lists; mail to more than one
  recipient at once.
- A second mail provider for billing mail (0.3).
- An admin view of users' sign-in histories, auth events or suppressed addresses (they are in each user's Settings and
  export, and the `mail` check counts suppressions).
- Changing the Gmail plugin's OAuth (S37), including its unsigned ID-token read.
- Free trials, referral codes, or a plan given at sign-up (the plans brief's B39 is superseded only in "no self
  sign-up").

## 2. Flows at a glance

```
config:     GET /auth/config → {signup, google_signin, password_reset, turnstile_site_key, password rules, terms}
sign-up:    /signup (Turnstile "signup") → POST /auth/signup (+ rag_signup browser cookie) → 202 → "Check your inbox"
            worker: address has an account? "you already have an account" mail : mint token → verify mail
verify:     link /verify-email#t=… → POST /auth/signup/check → same browser: one click; elsewhere: the password
            → POST /auth/signup/complete → user created, signed in (new session) → full page load → next or /plans
resend:     "Check your inbox" → Resend (Turnstile "resend") → POST /auth/signup/resend → 202
sign-in:    /login → POST /auth/login {identifier, password[, turnstile_token]} → Me → full page load → / or /plans
            (3 failures for the identifier from one network, 10 for the network, or a site-wide surge
             → 401 challenge_required → widget → retry with token; never for local requests)
google:     "Continue with Google" → POST /auth/google/start → {authorize_url} + rag_gsignin cookie → Google
            → GET /api/auth/google/callback?code&state (no session cookie arrives; state + cookie bind the browser)
            → verify ID token (JWKS, nonce) → known identity: sign in → 303 next
                                             → gmail address of an account without Google: link, sign in
                                             → gmail or workspace address of an account otherwise: /login?google=link_required
                                             → new (or any unverified-kind address): ticket → 303 /signup/google
                                               → terms → POST /auth/google/finish → user created, signed in → next
link:       Settings → Account → Connect Google (password) → POST /auth/google/start {intent: link} → … → /?settings=account
step-up:    Settings (no password) → POST /auth/google/start {intent: reauth} → Google → callback checks the linked sub
            → 5-minute grant for this session → change email / set a first password
forgot:     /forgot-password (Turnstile "reset") → POST /auth/password/forgot → 202 → worker: active user with that
            verified email? reset mail : pending sign-up for it? verification mail : nothing
reset:      link /reset-password#t=… → POST /auth/password/check → new password twice (+ disconnect Google?)
            → POST /auth/password/reset → every session ends, notice mailed → "Sign in with your new password"
email:      Settings → Account → Change email (password or step-up) → POST /me/email → 202 → confirm mail to the new
            address → /confirm-email#t=… → signed in as that account → Confirm → POST /auth/email/verify
            → users.email set; notice with an undo link to the old address
undo:       old address's link /undo-email#t=… → POST /auth/email/undo → old address back, password removed,
            every session ends, reset link mailed
mail:       any of the above → email_outbox row (in the caller's transaction) → worker MailLoop → SMTP (Resend)
            → sent | retry (1 min, 5 min, 30 min, 2 h, 6 h) | failed | expired (past not_after) | cancelled
bounces:    Resend webhook (signed) or an SMTP 5xx → mail_suppressions; 3+ bounces at 2%+ of the day → public paused
billing:    apply_payment → receipt row → outbox "receipt" (PDF attached); reminders → outbox "plan_reminder"
```

## 3. Identity model

### 3.1 The user's sign-in data

| Field | Rule |
|---|---|
| `username` | unchanged: unique, lowercase, `^[a-z0-9_.-]+$`, 3 to 64 characters. Random for new users (3.3); never one in `username_tombstones` |
| `password_hash` | **nullable** now: NULL = no password (a Google-only account, or after an undo). A NULL hash never matches; sign-in still burns the dummy-hash time |
| `email` | the verified address as given (local part as typed, domain lowercase ASCII), at most 254 characters; NULL when the account has none |
| `email_canonical` | `emails.parse(email).canonical` (3.2), **unique**; the key for sign-in, uniqueness and linking |
| `email_verified_at` | when the address was proven (our link, or Google's `gmail` or `workspace` claim); set whenever `email` is set |
| `signup_source` | `env` (the admin from `.env`), `admin` (created in Admin → Users, every existing non-admin user), `email`, `google` |
| `terms_version`, `terms_accepted_at` | the consent record of a self sign-up (S28); NULL for `env` and `admin` users |

`users.email` changes only through a confirmed change (4.8) or an undo (4.8); nothing else writes it.

**Display name** (S45): `email` when set, else `username`. The UI uses it for the avatar menu, the Settings header and
profile; mails address the account by its email. (Superseded on 2026-11-02 by the person's name: see "Names" at the
top.)

### 3.2 Email addresses: validation, canonical form and delivery key (`services/auth/emails.py`)

One function, `emails.parse`, used by sign-up, resend, forgot, sign-in, change and Google's claims (research 4.8,
R12):

1. Trim; refuse control characters and anything over 254 characters; NFC-normalize.
2. Exactly one `@`. The local part is 1 to 64 characters of `A-Z a-z 0-9 . ! # $ % & ' * + / = ? ^ _ ` { | } ~ -`
   (ASCII only, no quoted strings), with no leading, trailing or doubled dot.
3. The domain is converted with `idna` (UTS 46, transitional off) to lowercase ASCII; it needs at least two labels,
   each 1 to 63 characters of letters, digits and inner hyphens, and an alphabetic top-level label. No IP literals.
4. `address` = local part as typed + `@` + ASCII domain; `canonical` = the whole address lowercased. No DNS lookup.
5. `delivery_key` (for throttles only, never for identity): the canonical form with a `+tag` removed from the local
   part for every domain, and for `gmail.com` and `googlemail.com` the dots removed and the domain written `gmail.com`.
   `j.ohn+1@GMail.com` and `john@googlemail.com` share one delivery key.

Invalid input answers 422 with `detail` "Enter a valid email address." The UI shows and sends the address as stored
(an internationalized domain appears in its `xn--` form; rare enough for version 1). Redis keys and outbox rows use
`sha256(canonical)` or `sha256(delivery_key)`, never the address.

### 3.3 Usernames (`services/auth/usernames.py`)

- **Generated** for every self sign-up: `user-` followed by 8 characters drawn with `secrets.choice` from
  `abcdefghijklmnopqrstuvwxyz234567` (40 bits). Taken or tombstoned: draw again, up to 5 times; the insert's unique
  constraint is the backstop (one more draw on a collision). Nothing about the email goes into it.
- **Tombstones**: deleting a user inserts its username into `username_tombstones`; generation skips them, and the
  admin's create form refuses one (409 `username_retired` "That username belonged to a deleted account. Choose
  another."). Migration 0004 backfills the usernames kept on billing records of already-deleted users.
- Users rarely see the handle: the display name is the email (3.1); Settings → Account shows it as a fact ("also works
  for sign-in"). Since 2026-11-02 they see their own name instead ("Names" at the top).

### 3.4 Google identities (`user_identities`)

One row per linked Google account: `provider` `google`, `issuer` `https://accounts.google.com` (the
`accounts.google.com` form is normalized to it), `subject` (`sub`, at most 255 case-sensitive ASCII characters),
`email_at_link`, `email_kind` (`gmail`, `workspace` or `unverified`, as Google's claims said at link time),
`created_at`, `last_used_at`. Unique on (`issuer`, `subject`) and on (`user_id`, `provider`). The identity's address is
informational: an account's address is `users.email` alone. A Google account whose address later changes keeps signing
in (its `sub` is stable).

**Kinds** (S11): `gmail` = the address ends in `@gmail.com` and `email_verified`; `workspace` = `email_verified` with
a non-empty `hd`; anything else `unverified`. Only `gmail` auto-links to an existing account (S10); `gmail` and
`workspace` give a **new** account a verified email; `unverified` addresses are never looked up among users.

### 3.5 The env admin

Defined by `ADMIN_USERNAME`/`ADMIN_PASSWORD` and synced on every start, as today; `signup_source` `env`. It has no
email (Settings hides the email and sign-in-method sections with "The admin account is managed in .env"), can't link
Google, can't use "forgot password", and isn't reachable by an email sign-in. `_sync_admin`:

- **refuses to start** (a clear error naming the setting) when the user it would promote has `signup_source` `email`
  or `google`, an email, a pending email or a Google identity (S16);
- **warns** (one log line, and `admin_password_weak` on the Sign-up card) when `ADMIN_PASSWORD` is under 15
  characters, in the blocklist or contains a context word (5). The card also checks it against HIBP once a day (11.1).

`ADMIN_SIGNIN_FROM` (`any`, the default, or `local`): with `local`, the env admin's password sign-in from a request
that isn't local (8.1) answers the generic 401 after the dummy-hash time.

### 3.6 Pending sign-ups and pending emails

- `pending_signups`: one row per sign-up attempt, **at most three live rows per canonical address** (a fourth deletes
  the oldest, its tokens cascading): the address, the argon2 hash of the chosen password, `browser_sha256` (the hash of
  the browser's sign-up cookie, 4.1), `next_path`, the terms version and time, failed completion attempts, an
  approximate IP, `created_at`, `expires_at` = 7 days after creation. Never a user; can't sign in; reserves nothing.
- `pending_emails`: at most one per user: the address, canonical form, `source` (`user` or `google`), `sent_at` (the
  last confirmation mail), `expires_at` (24 h after the last mail). Claims reserve nothing: an address held by a
  verified user is refused at confirmation, and **only the account that asked can confirm** (4.8).

### 3.7 Lifecycle and clean-up (the worker's hourly sweep, `services/mail/sweeps.py`)

| Row | Deleted or cleared when |
|---|---|
| `pending_signups` | `expires_at` passed (7 days); a fourth row for the address arrived; the address became an account (any completion, a Google sign-up, a confirmed change); 5 wrong passwords |
| `pending_emails` | `expires_at` passed, a newer request for the same user, confirmed, cancelled, a reset or an undo |
| `auth_tokens` | used or expired more than 1 day ago (kept a day so "already used" can be told apart in logs) |
| `email_outbox` | `to_address` and `canonical` cleared when the job finishes (`sent`, `failed`, `cancelled`, `expired`); the row deleted 48 hours later |
| `auth_events` | `ip` cleared after 90 days; the row deleted after 365 days, or with the user |
| `mail_suppressions` | a bounce after 30 days; a complaint when a user confirms that address again; otherwise kept |
| `username_tombstones` | never |
| Redis tickets, states and grants | by TTL (Google state 10 min, ticket 15 min, step-up grant 5 min) |

Deleting a user (`DELETE /admin/users/{id}`) cascades identities, pending emails, tokens, outbox rows and auth
events, and inserts a username tombstone; the address is free again at once. `test_deletion_completeness.py` lists the
new tables.

## 4. Flows

### 4.1 Email sign-up (`POST /auth/signup`)

The page (`/signup`, 13.2) sends `{email, password, accept_terms: true, turnstile_token, next?}`. In this order:

1. **Validation** (pydantic, `extra="forbid"`): `email` 1 to 320 characters before parsing, `password` 1 to 1,024,
   `accept_terms` must be `true` (422 "Accept the terms to create an account."), `turnstile_token` 1 to 2,048, `next`
   at most 512 characters.
2. **Availability**: email sign-up open (S27) → else 403 `signup_closed`.
3. **Network limit** `signup` (8.1) → 429 `too_many_attempts` with `Retry-After`.
4. **Turnstile**, action `signup` (7.2) → 400 `challenge_failed` or 503 `challenge_unavailable`.
5. **Parse** the address (3.2) → 422.
6. **Password policy** (5) with the email and its local part as context words → 422 `password_rejected`.
7. **Mail availability** for the `public` class (6.5) → 503 `mail_unavailable` "We can't send emails right now. Try
   again later, or continue with Google."
8. **Hash the password** (argon2, off the event loop), always.
9. **The browser cookie** `rag_signup` (`__Secure-rag_signup` with `COOKIE_SECURE`; HttpOnly, `SameSite=Strict`, Path
   `/api/auth/signup`, Max-Age 7 days): the request's value is reused when it is 43 base64url characters, else 32
   random bytes are minted. It is set on the response whatever happens next.
10. **Per-address limit** (silent, kind `signup`, 8.1). Within it, in one transaction: insert a `pending_signups` row
    (the address, the hash, `browser_sha256` = SHA-256 of the cookie value, `next_path` = `safe_next(next)` or NULL,
    the terms record, the approximate IP, `expires_at` +7 days); delete the oldest live rows for the canonical address
    beyond three; enqueue an outbox job `signup` (`pending_signup_id`) to the address. Over the limit: none of it, and
    nothing else differs.
11. **202** `{"status": "accepted"}`.

`safe_next` keeps a path that starts with `/`, not `//` or `/api/`, with no backslash or control character; anything
else is NULL. The request never reads `users`. The worker (6.4) decides: an account already has this verified address
→ the "already have an account" mail (in its variant: active with a password, Google-only, or turned off) and that
pending row is deleted; otherwise it mints a `signup` token (24 h, bound to the row and the address) and sends the
verification mail.

### 4.2 Finishing an email sign-up (`/verify-email`)

The link is `<base>/verify-email#t=<token>`. The page reads the token from the fragment, removes the fragment from the
address bar (`history.replaceState`), and calls `POST /auth/signup/check {token}` → `{email, expires_at,
needs_password, next}` (400 `link_invalid` "This link has expired or was already used." for a missing, used, expired
or unknown token). `needs_password` is false when the request's `rag_signup` cookie hashes to the row's
`browser_sha256`. The page shows "Finish creating your account" with the address; the field **The password you
chose** appears only when `needs_password`. Then `POST /auth/signup/complete {token, password?}`:

1. Network limit `token` → 429.
2. Lock the token row (unused, unexpired, purpose `signup`, its address hash equal to the row's canonical address) and
   its pending row → else 400 `link_invalid`.
3. **The binding**: the cookie matches the row → go on. Otherwise the password must match the row's hash. Wrong or
   missing: 400 `wrong_password` "That isn't the password you chose when you signed up." (a missing one isn't counted);
   each wrong one adds to `password_failures`, and the 5th deletes the row and its tokens and answers 400
   `signup_reset` "Too many wrong passwords. Sign up again to choose a new one."
4. Sign-up must still be open → 403 `signup_closed`.
5. The canonical address must not belong to a user (a Google sign-up may have taken it meanwhile) → every pending row
   for the address is deleted, 409 `email_taken` "This address already has an account. Sign in instead."
6. Create the user: a generated username, the row's hash as `password_hash`, `email`, `email_canonical`,
   `email_verified_at` now, `signup_source` `email`, the terms record. Delete every pending row for the address (their
   tokens cascade), record `signup.completed`, record a sign-in (`login_events`, method `password`), commit.
7. Set the session cookie, clear `rag_signup`, answer `Me` (the plan state is `none`). The page leaves with a full page
   load to `next` (from the check) or `/plans`.

The user can open the link on another device: the password is the binding there. A user who never signed up and gets
the mail can ignore it; nothing exists until the link **and** that browser or that password are used. The password
step has a read-only email field (`autocomplete="username"`) beside the password (`autocomplete="current-password"`),
so password managers offer the saved entry, and "Forgot it? **Sign up again**" (to `/signup` with the address carried
in `sessionStorage`).

### 4.3 Resending the verification (`POST /auth/signup/resend`)

From the "Check your inbox" state: `{email, turnstile_token}` (action `resend`). Steps 2 to 5 and 7 of 4.1, with the
network bucket `resend`; then the silent per-address limit (kind `signup`, shared with sign-up); then one `SELECT` of
the newest live pending row for the canonical address whose `browser_sha256` matches the request's cookie (the same
query whatever the address; `users` isn't read), and one outbox job `signup` with that row's id or none; 202. The
worker sends the "already have an account" mail when a user has the address; else a new verification link for that
row if it is still live, else for the newest live row; else nothing. Earlier links stay valid until they expire.

The page mirrors the per-address limit (it applies to whatever was typed, so it reveals nothing): **Resend** shows a
countdown until 60 s after the last send; after the third mail it shows "We've sent 3 emails. Check Spam and Gmail's
Promotions and Updates tabs, use Continue with Google, or try again in an hour." Any of the emails works.

### 4.4 Signing in (`POST /auth/login`)

`{identifier, password, turnstile_token?}` (`username` is still accepted as an alias of `identifier`, so scripts and
the existing suites keep working).

1. **Normalize**: trim, drop NUL. With `@`: `emails.parse` → the user whose `email_canonical` matches (an invalid
   address finds nobody); otherwise the lowercased username.
2. **Keys** (Redis, `LOGIN_WINDOW_SECONDS`): the identifier key is `acct:<user id>` when the identifier found a user
   (the email and username forms share it), else `email:<sha256(canonical)>` or `user:<username>`; the network key and
   the wide-network key of 8.1.
3. **Hard limits**: over `LOGIN_MAX_ATTEMPTS` failures for the identifier key, `LOGIN_MAX_ATTEMPTS_PER_IP` for the
   network or four times that for the wide network → 429 as today.
4. **Challenge** (only when Turnstile is configured and the request isn't local, 8.1): when the failures for the
   identifier key **from this network** reach `LOGIN_CHALLENGE_AFTER` (3), the network's reach
   `LOGIN_CHALLENGE_AFTER_PER_IP` (10), or the whole site's in the last 5 minutes exceed `LOGIN_SITE_CHALLENGE_AFTER`
   (50), a request without a token answers **401 `challenge_required`** "Confirm you're not a robot to continue."
   without checking the password or counting. With a token: Turnstile action `login`; `challenge_failed` → 400;
   **`challenge_unavailable` → carry on without the challenge** (the hard limits still apply; counted for the
   monitor). Counters exist for unknown identifiers too, so the challenge reveals nothing.
5. Count the attempt (identifier key, identifier-and-network key, network, wide network, site), verify
   (`verify_password_async`; a NULL hash or no user burns the dummy hash), and refuse an inactive user and, with
   `ADMIN_SIGNIN_FROM=local`, the env admin from a non-local request, all with **401 "Incorrect email, username or
   password."**
6. Success as today (the identifier's counters cleared, rehash, `last_login_at`, `login_events` with method
   `password`, the cookie), returning `Me`.

The page shows two static hints under every 401: "Just signed up? Open the link we emailed you, or **send it again**."
(a link to `/signup?resend=1`; the typed identifier goes along in `sessionStorage` when it holds an `@`) and "Signed up
with Google? Use **Continue with Google**." After a successful sign-in the page leaves at once with a full page load
(S19).

### 4.5 Google sign-up, sign-in and step-up

**Start** (`POST /auth/google/start`, a same-origin `fetch`; research 1.8): `{intent: "signin", next?, from?}` without
a session (`from`: `login` or `signup`), `{intent: "link", password}` or `{intent: "reauth"}` with one (4.6, 4.9).

1. Google sign-in configured (`GOOGLE_SIGNIN_CLIENT_ID`/`_SECRET`) → else 503 `google_unavailable`. Network limit
   `google_start` (20 a minute) → 429. `link` checks the password as 4.6 says; `reauth` needs a session with a `sid`
   and a linked Google identity (else 400).
2. The redirect base: the request's `Origin`, normalized, must be one of `PUBLIC_BASE_URLS` (as the Gmail connect,
   `plugins/oauth.normalize_origin`) → else 400 "Use <first https origin> to sign in with Google."
3. `next` (sign-in and `reauth`): `safe_next` (4.1), default `/` (`/?settings=account` for `reauth`).
4. Mint `state` (32 bytes), the PKCE `verifier` (64), the OIDC `nonce` (32) and a browser-binding value (32). Store
   `auth:google:<sha256(state)>` (TTL 600 s, `SET NX`): `{intent, from, user_id?, token_version?, sid?, verifier,
   nonce, binding_sha256, redirect_uri, base, next, created_at}`. Set cookie `rag_gsignin` (`__Secure-` with
   `COOKIE_SECURE`) = the binding value (HttpOnly, `SameSite=Lax` so the cross-site callback carries it, Path
   `/api/auth/google/callback`, Max-Age 600).
5. Answer `{authorize_url}`: Google's authorize URL with `response_type=code`, `client_id`, `redirect_uri` =
   `<base>/api/auth/google/callback`, `scope=openid email`, `state`, `nonce`, `code_challenge` (S256),
   `prompt=select_account`; for `reauth` also `login_hint=<the linked sub>` and `max_age=0`; never
   `access_type=offline`. The page does `window.location.assign(authorize_url)` after checking the URL starts with the
   configured authorize URL.

**Callback** (`GET /api/auth/google/callback?code&state` or `?error&state`): never raises; always clears
`rag_gsignin` and answers **303** to a fixed set of pages on the stored base (or the first https origin):

1. `GETDEL` the state; missing → `/login?google=expired`. The cookie's SHA-256 must equal `binding_sha256`
   (`hmac.compare_digest`) → else `?google=error`. `error=access_denied` → `?google=denied`; another error →
   `?google=error` (logged by its code only).
2. Exchange the code (`code`, `client_id`, `client_secret`, `redirect_uri`, `grant_type`, `code_verifier`; 10 s) →
   `?google=error` on failure. Keep only the `id_token`.
3. **Verify the ID token** (`services/auth/google.verify_id_token`): RS256 only; the key by `kid` from Google's JWKS
   (fetched with `httpx`, cached by `Cache-Control` max-age between 5 minutes and 24 hours, refetched once for an
   unknown `kid`); `iss` in {`https://accounts.google.com`, `accounts.google.com`}; `aud` equals the client id (a
   list must contain it and then `azp` must equal it); `exp` in the future and `iat` not more than 10 minutes ahead
   (60 s leeway); `nonce` equals the stored nonce; `sub` 1 to 255 characters; `email` present and parseable;
   `email_verified` true or `"true"` (anything else counts as unverified). The kind (3.4) follows. Any failure →
   `?google=error` (logged as the failed check's name).
4. Decide (intent `signin`):

| Case | Result |
|---|---|
| The identity (`iss`, `sub`) is linked to an active non-admin user | sign in: `last_used_at`, `login_events` method `google`, session cookie, 303 `<base><next>` |
| … to an inactive user (or, impossibly, the admin) | 303 `/login?google=unavailable` |
| No identity; kind `gmail`; a user holds this **verified** canonical address, has **no** Google identity, is active and isn't the admin | **link** (`google.linked`, method `auto`), `google_linked` notice to the address, then sign in as above |
| No identity; kind `gmail` or `workspace`; a user holds the address but any other condition fails (every `workspace` case included) | 303 `/login?google=link_required` (no change) |
| No identity; kind `gmail` or `workspace` and no user holds the address, or kind `unverified` (never looked up among users); Google sign-up closed (S27) | 303 `/login?google=signup_closed` |
| … otherwise | store the ticket `auth:gticket:<sha256(ticket)>` (TTL 900 s): `{issuer, subject, email, kind, next, from}`; cookie `rag_gticket` (`__Secure-` with `COOKIE_SECURE`; HttpOnly, `SameSite=Strict`, Path `/api/auth/google/`, Max-Age 900); 303 `/signup/google` |

Intent `link` is 4.6. Intent **`reauth`** (the step-up): the state's user must still exist, be active and have the same
`token_version`; the token's (`iss`, `sub`) must equal that user's linked identity (else 303
`/?settings=account&google=reauth_mismatch`); when the token carries `auth_time` it must be at most 5 minutes old (else
`?google=expired`). Then `SET auth:stepup:<user_id>:<sid> 1 EX 300`, record `google.reauth`, update `last_used_at`,
and 303 `<base><next>` with `google=reauth_ok` added to its query (`next` defaults to `/?settings=account`). A grant is
spent (`GETDEL`) by the action it was for (4.8, 4.9) and works only in the session that asked; the env admin and a
session token without a `sid` (issued before sign-in history existed) can't get one.

**Finish** (`/signup/google`, 13.4): `GET /auth/google/pending` → `{email, email_verified, from, expires_at}` (400
`link_invalid` without a live ticket; `email_verified` is true for kinds `gmail` and `workspace`).
`POST /auth/google/finish {accept_terms: true}` (network bucket `signup`, 8.1): `GETDEL` the ticket; re-check
everything in one transaction (the identity unused; for `gmail` and `workspace`, the address not taken by a verified
user, else 409 `email_taken` "This address already has an account. Sign in with your password, then connect Google in
Settings."; sign-up open, else 403 `signup_closed`); create the user (generated username, `password_hash` NULL,
`signup_source` `google`, the terms record) and the identity; for `gmail` and `workspace`, `email` and
`email_verified_at` now and every `pending_signups` row for the address deleted (research 4.1); for `unverified`, a
`pending_emails` row (`source` `google`) and an `email_confirm` job (S11; nobody is told whether the address is taken
until its holder confirms). Record `signup.completed` (method `google`) and a sign-in; set the session cookie; clear
`rag_gticket`; answer `Me`. The page leaves with a full page load to the ticket's `next` or `/plans`.

"Use a different Google account" calls start again; "Cancel" and, from the sign-in page, **I already have an account**
drop the ticket (`POST /auth/google/cancel` → 204) and go to `/login` (the latter with `?google=use_password`).

### 4.6 Connecting and disconnecting Google (Settings → Account)

- **Connect**: only for an account with a password (a Google-only account already has its identity). The user enters
  the password; `POST /auth/google/start {intent: "link", password}` checks it under the login counter for that
  account (as `change-password`: a wrong one → 400 `wrong_password`, and 429 past the limit), stores `user_id`,
  `token_version` and `sid` in the state, and continues as 4.5. At the callback the user must still exist, be active
  and have the same `token_version` (else `expired`). Results, as `303 /?settings=account&google=<result>`: `linked`
  (identity created, `google_linked` notice to the account's email if any, `google.linked` method `manual`); `in_use`
  (the identity belongs to another user); `already_linked`; `denied`, `error`, `expired`. Linking never replaces
  `users.email`; when the account has no email and the claim's kind is `gmail` or `workspace` for an address no one
  holds, that address becomes the verified email (a pending email for the same address is dropped).
- **Disconnect**: `DELETE /me/google {password}` → 204; only with a password and a verified email remaining
  (409 `last_sign_in_method` "Set a password and confirm an email first, so you can still sign in."). `google_unlinked`
  notice; `google.unlinked` (`self`). After unlinking, that Google account can sign up again only under the normal
  rules (a `gmail` or `workspace` address is taken, so `link_required`).

### 4.7 Forgot and reset password

**Forgot** (`/forgot-password`, `POST /auth/password/forgot {email, turnstile_token}`): password reset available
(mailer and Turnstile configured) → else 503 `mail_unavailable`; network limit `forgot`; Turnstile action `reset`;
parse (422); mail availability (`reset` class); the silent per-address limit (kind `reset`); one outbox job
`password_reset` (`canonical`); 202 `{"status": "accepted"}`. The worker finds an **active, non-admin** user with that
**verified** address and mints a `reset` token (30 min); otherwise, a live pending sign-up for the address gets a
fresh `signup` token and the "finish creating your account" mail (UX-05); otherwise nothing. A username isn't
accepted: the copy says "Use the email address on your account. No email on your account? Ask the administrator."

**Reset** (`/reset-password#t=…`): the page clears the fragment, calls `POST /auth/password/check {token}` →
`{email, username, has_password, expires_at, google}` (or 400 `link_invalid`), where `google` is
`{email: <masked address at link time>, suggest_disconnect: <its canonical form differs from the account's>}` or null.
It asks for the new password twice and, with `google`, shows **Also disconnect Google sign-in (a***@gmail.com)**,
ticked when `suggest_disconnect`. `POST /auth/password/reset {token, new_password, disconnect_google}` → 204:

1. Network limit `token`; lock the token (purpose `reset`, unused, unexpired, its address hash equal to the user's
   current `email_canonical`) and the user (active, non-admin) → else 400 `link_invalid`.
2. The password policy (5) with the email and its local part as context → 422 `password_rejected`.
3. Set `password_hash`; spend the token and revoke the user's other `reset` tokens (an `email_undo` token survives); **bump
   `token_version`** (every session ends, the caller's included); delete a `pending_emails` row (an unexpired email
   change dies with a reset); clear the user's login-failure counters; with `disconnect_google`, delete the identity
   and record `google.unlinked` (`reset`).
4. Record `password.reset`; enqueue `password_changed` (it says whether Google was disconnected); commit.

The page then says "Your password was changed and you were signed out everywhere." with **Sign in** (a full load of
`/login`). It never signs in. For an account that had no password the words are "Your password is set."

### 4.8 Adding, changing, confirming and undoing an email

- **Request** (Settings → Account): `POST /me/email {email, password?}`. Not the env admin (400). Re-authentication:
  an account with a password must send it (checked under the login counter; wrong → 400 `wrong_password`); an account
  without one must hold a **step-up grant** for this session (4.5; spent here) → else 403 `reauth_required` "Confirm
  it's you with Google first." Then: parse (422), the same as the current email → 422 "That's already your email.",
  per-user limit (5 a day; 3 for an unpaid account, which also counts in the network `signup` bucket)
  → 429. Replace the user's `pending_emails` row (`source` `user`, revoking the user's `email` tokens) and enqueue
  `email_confirm`; 202. The request doesn't check whether another account holds the address (that is decided at
  confirmation).
- **Resend** `POST /me/email/resend` → 202 (the per-user limit and the silent per-address limit, kind `confirm`,
  apply); **cancel** `DELETE /me/email/pending` → 204 (its tokens revoked).
- **Confirm** (`/confirm-email#t=…`): the page keeps the token in `sessionStorage` (10 minutes, this tab) and calls
  `POST /auth/email/check {token}` → `{email, account, signed_in}`, where `account` names the account that asked (its
  current email masked, or its username when it has none) and `signed_in` says whether this request's session is that
  account's. Signed in as that account: "Confirm <address> as your email" with **Confirm this email** (a click, so a
  scanner running scripts can't spend it). Not signed in: **Sign in to confirm** (to `/login?next=/confirm-email`;
  the page picks the token up again). Signed in as another account: "You're signed in to another account" with **Sign
  out**. `POST /auth/email/verify {token}` (network limit `token`; **the session is required**): lock the token
  (purpose `email`, its address hash equal to the pending row's canonical address) and its pending row; the session's
  user must be the row's user → else 403 `wrong_account` "Sign in to the account that asked for this change."; the
  canonical address held by another user → delete the pending row, 409 `email_taken`; otherwise set `email`,
  `email_canonical`, `email_verified_at`, delete the pending row and every `pending_signups` row for the address,
  revoke the user's `reset` tokens, delete a suppression for the address (a confirmation proves delivery), record
  `email.confirmed` (or `email.changed` with both addresses masked), enqueue `email_changed` to the **old** address
  when there was one (the worker adds an undo link only when the user has no live `email_undo` token, so the first
  address of a chain of changes keeps the undo); answer `{email}`. Sessions are kept.
- **Undo** (`/undo-email#t=…`, the link in the `email_changed` notice; no session needed, since the old mailbox was
  the account's verified address until the change): `POST /auth/email/undo-check {token}` → `{old_email, new_email}`
  (the new one masked); **Undo the change** → `POST /auth/email/undo {token}` → 204 (network limit `token`): lock the
  token (purpose `email_undo`, unused, unexpired) and the user (active, non-admin); the token's address now held by
  another user → 409 `email_taken` "That address is now used by another account. Contact us."; otherwise, in one
  transaction: `email` back to the token's address (verified now), the pending email deleted, `password_hash` NULL,
  `token_version` bumped (every session ends), every `reset`, `email` and `email_undo` token of the user revoked,
  record `email.change_undone`, enqueue `password_reset` for the user (the worker mails a reset link to the restored
  address; the reset page then offers to disconnect a Google identity whose address isn't the account's) and
  `email_undone` to the address it replaced. The page says "Your email is back to <old address>. We signed out every
  session and removed the password. Check your inbox for a link to choose a new one."

### 4.9 Setting a first password (accounts without one)

Settings → Account → **Set a password**: without a step-up grant the panel offers **Continue with Google** (start,
intent `reauth`, `next` `/?settings=account&setup=password`); with one, the form (new password twice,
`PasswordRules`). `POST /me/password/setup {new_password}` → 204: the step-up grant is spent (else 403
`reauth_required`); the account must have no password (409 `has_password` "You already have a password. Use Change
password."); the password policy (422); set the hash, clear the login-failure counters, record `password.set`,
enqueue `password_added` to the verified email if any. **No session ends** (UX-15). `POST /auth/change-password` for an
account without a password answers 400 `no_password` "You don't have a password yet. Use Set a password."

### 4.10 Admin-created users

- Created as today (`signup_source` `admin`, username and password), with the password policy applied (422
  `password_rejected` with its sentence under the field) and tombstoned usernames refused (409 `username_retired`).
  The user adds an email themselves in Settings → Account.
- The admin's password reset (`PATCH … {password}`) stays; it now applies the password policy and also revokes the
  user's `reset` tokens and clears their login-failure counters (closes the HANDOFF item).
- Admin → Users shows each user's email (or "Pending: …"), source, Google mark and sign-up date, read-only (13.9).

### 4.11 Deactivated and deleted users

- **Deactivated** (`is_active` false): password sign-in answers the generic 401; Google sign-in goes to
  `/login?google=unavailable` ("We couldn't sign you in. If you think this is a mistake, contact us."); the worker
  sends no reset; an "already have an account" mail says the account is turned off and gives the contact address; a
  Google finish, an email confirmation or an undo for that user answers `link_invalid`. Reactivation restores
  everything.
- **Deleted**: everything keyed by the user is deleted (3.7) and the username tombstoned; the address and Google
  identity can sign up again as a new account. Billing records stay with the username (plans brief B30), which is never
  reissued.

### 4.12 After sign-up, and checkout

Both sign-up paths end signed in with `Me.plan.state` `none`, so `RequireAuth`/`PlanGate` send the user to `/plans`
(plans brief 8.1) unless `next` names another page a plan-less user may open (`/plans/checkout/<code>`). Payments
follow the plans brief with three changes:

- **Checkout needs a verified email** (SEC-08): `POST /billing/checkout` without one answers 409 `email_required` "Add
  and confirm your email in Settings → Account first. Your receipt goes there." `/plans` and checkout show that
  sentence with a link instead of the buy buttons' errors.
- **Payment-link caps for unpaid users** (no live or offline payment applied yet): 3 an hour and 6 a day per user
  (instead of 5 and 15); all unpaid users together 30 an hour (`CHECKOUT_UNPAID_PER_HOUR`), beyond which checkout
  answers 503 `checkout_busy` "Checkout is busy. Try again in a few minutes." and the `billing` check warns.
- **While payments aren't live** (UX-02): a non-tester's unavailable option reads "Paid plans open soon. Check back in
  a few days." instead of "Online payment isn't set up yet." (the admin's own pages keep the latter).

## 5. Passwords (`services/auth/passwords.py`)

Applied wherever a password is set: email sign-up, reset, a first password, change-password, the admin's create and
reset. Never at sign-in (any stored password still signs in).

| Rule | Detail |
|---|---|
| Length | at least `PASSWORD_MIN_LENGTH` (default 8, range 8 to 64) and at most 256 code points after NFC; sign-in accepts up to 1,024 (a long `ADMIN_PASSWORD`) |
| Characters | any Unicode, spaces included; no composition rules; never trimmed or case-changed |
| Normalization | `security.hash_password` and `verify_password` both use the **NFC** form. No raw fallback: hashes made before 0004 are of ASCII passwords, where NFC changes nothing (UX-13); the rehearsal (14.2) signs in as `bikash` to prove it |
| Blocklist | `backend/app/data/common-passwords.txt`: a SecLists common-password list (MIT licence; the builder records the file's URL and commit in its header), lowercased, deduplicated, filtered to entries of at least 8 characters, at least 3,000 entries. Rejected when the lowercased password is in it |
| Context words | `asknuvexusdoc`, `nuvexus`, `nuvexusdoc`, the username, the email and the email's local part (each when 4 characters or more; the whole email since review UX-08). Rejected when the password, lowercased with everything but letters removed, equals one of them |
| Breached passwords | with `PASSWORD_BREACH_CHECK` (default true): `GET <PASSWORD_BREACH_CHECK_URL><first 5 hex of SHA-1>` with `Add-Padding: true` and a user agent, 2 s timeout; a matching suffix with a count above 0 rejects. Any failure **allows** the password (logged without it) |
| Errors | 422 `password_rejected` with `reason` `too_short`, `too_long`, `common`, `context` or `breached`, and a sentence: "Use at least 8 characters.", "Use at most 256 characters.", "This password is too common. Choose one that's harder to guess.", "Don't use the app's name, your username or your email in the password.", "This password has appeared in a data breach. Choose a different one." |

The admin password from `.env` is exempt (it is the owner's); 3.5 says how it is checked.

## 6. Email

### 6.1 Provider

**Resend, free tier** (3,000 a month, 100 a UTC day), over **SMTP** (`smtp.resend.com`, port 465 implicit TLS, user
`resend`, password a sending-only API key), From `AskNuvexusDoc <no-reply@asknuvexusdoc.com>`, Reply-To
`support@asknuvexusdoc.com` (routed to the owner by Cloudflare Email Routing), open and click tracking off. Region:
Tokyo (`ap-northeast-1`), the closest to India. A Resend **webhook** for `email.bounced` and `email.complained` feeds
the suppression list (6.6). **Brevo** is the documented fallback (`smtp-relay.brevo.com:587` STARTTLS, its SMTP key;
free mail carries its branding and its IP allow-list must be off for a home connection), and the runbook's answer if
Resend pauses the account. Moving to Resend Pro comes before any announcement or paid promotion, and whenever daily
volume nears 80. The owner's steps are in section 17.

### 6.2 The mailer (`app/services/mail/`)

- `transport.py`: `send_message(message: EmailMessage, smtp: SmtpConfig) -> None`, the SMTP code moved out of
  `app/ops/channels.EmailChannel` (stdlib `smtplib` in a thread, `ssl`/`starttls`/`none`, 15 s per operation),
  raising `MailError(kind, reason)` with `kind` `temporary` (connection failures, timeouts, 4xx), `permanent` (5xx
  for the recipient or the message), `auth` (535 and other login refusals). `EmailChannel` keeps its behaviour and
  calls it.
- `templates.py`: one function per kind returning `(subject, text, html)`, all in the one HTML frame of the visual
  brief's section 10; 6.4 lists the kinds and their copy.
- `outbox.py`: `enqueue`, `available`, `wake`, `suppressed` (6.3, 6.5, 6.6).
- `jobs.py`: what each kind resolves to at send time (the address, a token minted, or nothing).
- `drain.py`: the worker's `MailLoop`.
- `webhook.py`: the Resend event check and handling (6.6).
- `sweeps.py`: the hourly clean-up of 3.7.

**Every message**: `From` `MAIL_FROM`; `Reply-To` `MAIL_REPLY_TO` when set; one `To`; `Date`; `Message-ID` on the
From domain; `Auto-Submitted: auto-generated`; `MIME-Version`; `multipart/alternative` with the text first and the
HTML second (attachments make it `multipart/mixed`); the `MAIL_IDEMPOTENCY_HEADER` header (default
`Resend-Idempotency-Key`; empty: none) set to `mail-<public_id>`. Subjects start with nothing but the subject (the
From name carries the brand).

**Settings and unification**: user mail uses `MAIL_*` only; alerts keep `ALERT_SMTP_*` (the owner may point both at
Resend, but README recommends ntfy or a webhook for alerts, so a paused Resend account is still reported).
`BILLING_EMAIL_FROM` is removed: a value still in `.env` is ignored with one start-up warning ("BILLING_EMAIL_FROM is
ignored: plan reminders and receipts now use MAIL_FROM and the MAIL_SMTP_ settings"). `PaymentsInfo.email_reminders`
now means "the mailer is configured".

### 6.3 The outbox and the worker

`outbox.enqueue(db, kind, *, to, canonical=None, user_id=None, pending_signup_id=None, pending_email_id=None,
params=None)` adds a row in the caller's transaction (it never commits) with a fresh `public_id`
(`secrets.token_urlsafe(18)`), `recipient_sha256` (SHA-256 of the canonical address), the kind's class (6.5),
priority and `not_after`, `next_attempt_at` now; after the caller's commit, `outbox.wake()` pushes to the Redis list
`rag:mail_wake` (best effort). `params` holds only non-secret values (ids, a plan name, a reminder kind); a test checks
no `params` value looks like a token or a link.

The worker runs `MailLoop` beside the billing loop (`worker.py`), one job at a time:

1. Wait on `BLPOP rag:mail_wake` for up to 5 s, then pick the next due job: `status = queued`, `next_attempt_at <=
   now`, ordered by `priority`, `next_attempt_at`, `id`; claim it (`queued → sending`, `attempts + 1`, commit). On
   start-up **and every minute** (review DATA-02), rows left in `sending` for over 2 minutes go back to `queued` (the
   same idempotency key, so Resend drops a duplicate of a message that did go out). A stopping worker gives the loop a
   few seconds to finish the mail it is sending, then cancels it, which puts the claimed job back in the queue; the
   hourly sweep expires a stranded `sending` job past its `not_after` and clears its address.
2. Past `not_after` → `expired`. The class over its share of the day (6.5) or paused (6.6) → `next_attempt_at` = the
   next 00:00 UTC.
3. The recipient suppressed (6.6) → `cancelled` (`suppressed`). Resolve (6.4); nothing to send → `cancelled` (with
   the reason, e.g. `no_account`, `pending_gone`, `pref_off`). Resolving may mint a token (committed before sending, so
   a crash after the send leaves a valid link).
4. Render and send, outside any transaction. Then: `sent` (`sent_at`); `temporary` → back to `queued` with
   `next_attempt_at` +1 min, +5 min, +30 min, +2 h, +6 h for attempts 1 to 5, then `failed`; `permanent` → `failed`
   at once, and a recipient refusal (550 to 553) is also a bounce (6.6); `auth` → back to `queued` and **every** job
   paused for 15 minutes (the monitor warns). `last_error` is our short reason ("SMTP login refused", "recipient
   refused (550)"), never the server's text with the address.
5. A finished job (`sent`, `failed`, `cancelled`, `expired`) has `to_address` and `canonical` cleared in the same
   write; `recipient_sha256` stays for counting.
6. **Pacing**: a `public` or `reset` job takes at least 2 s of the loop's time from its claim, whether it was sent or
   cancelled (S6).
7. `pending_emails.sent_at` and `expires_at` (+24 h) are updated when an `email_confirm` job is sent.

Tokens minted for attempts that never arrived stay valid until they expire; a retry mints a new one, so each mail
carries a working link.

### 6.4 Kinds, recipients and templates

| Kind | Class (priority) | `not_after` | Resolves at send time to |
|---|---|---|---|
| `signup` | public (1) | 6 h | a user holding the verified canonical address → the "already have an account" variant (active with a password; Google only; turned off), and the pending row deleted; else the given pending row if live, else the newest live row for the address → a new `signup` token → the verification; else nothing |
| `password_reset` | reset (0) | 6 h | an active non-admin user with this verified canonical address (or the `user_id` of an undo, 4.8) → a new `reset` token → the reset mail ("set" wording without a password); else a live pending sign-up for the address → a new `signup` token → the "finish creating your account" mail; else nothing |
| `email_confirm` | public; account once the user has paid (0) | 6 h | the user's pending email (still the same row) → a new `email` token → the confirmation (wording by `source`: user, google); else nothing |
| `email_changed` | public; account once the user has paid (0) | 3 days | the old address; with a new `email_undo` token (7 days) when the user has no live one, else without the undo link |
| `email_undone` | public; account once the user has paid (0) | 3 days | the address the undo replaced |
| `password_changed`, `password_added`, `google_linked`, `google_unlinked` | public; account once the user has paid (0) | 3 days | the account's verified email (none → nothing) |
| `receipt` | billing (2) | 3 days | the user's verified email and `email_receipts` preference → the receipt mail with the PDF attached (rendered from the receipt row, as `GET /billing/receipts/{id}`); else nothing |
| `plan_reminder` | billing (2) | 1 day | the user's verified email and `email_plan_reminders` preference → the reminder of `params.reminder` (the plans brief's text, with the new footer); else nothing |
| `test` | account (2) | 1 h | the admin's typed address |

"Paid" means at least one live or offline payment applied (`billing_payments`, mode `live` or `offline`; `paid.has_paid`);
`enqueue` decides the class from the kind and that fact.

**Copy** (the MAIL builder renders the HTML from the same lines; the button label is in brackets; every mail ends with
"AskNuvexusDoc · <contact email from Admin → Billing → Business & payments, when set> · asknuvexusdoc.com", a line
saying why the mail was sent and, with `MAIL_REPLY_TO`, "Questions? Reply to this email."). Exactly:

- **Verification** — "Finish creating your AskNuvexusDoc account": "Confirm your email to finish signing up for
  AskNuvexusDoc." [Finish sign-up] "The link works for 24 hours. In the browser where you signed up, one click
  finishes; anywhere else, you'll be asked for the password you chose." / "Didn't sign up? Ignore this email; no
  account is created." The "finish creating" variant (from Forgot password) opens with "You asked to reset your
  password, but your account isn't created yet."
- **Already registered** — "You already have an AskNuvexusDoc account": "Someone, hopefully you, tried to sign up
  with <address>, but it already has an account." [Sign in] "Forgot your password? Reset it here:" <base>/forgot-password.
  The Google-only variant: "Sign in with Continue with Google, or set a password with Forgot password."; the turned-off
  variant (no button): "This account is turned off. Contact us if you need it:" <contact> / "Didn't try to sign up?
  Ignore this email; nothing was changed."
- **Reset** — "Reset your AskNuvexusDoc password": "Reset the password for <address>." [Choose a new password] "The
  link works for 30 minutes. Resetting signs you out everywhere." / "Didn't ask for this? Ignore this email; your
  password stays the same." Without a password: "Set a password for <address>." [Set a password].
- **Password changed** — "Your AskNuvexusDoc password was changed": "The password for <address> was changed on <date
  and time, IST>." (+ "Google sign-in was disconnected." when it was) "If that wasn't you, reset your password now:"
  <base>/forgot-password "Then contact us:" <contact>.
- **Password added** — "A password was added to your AskNuvexusDoc account": "You can now sign in with <address> and
  a password, or keep using Google. Added on <date and time, IST>." "If that wasn't you, contact us right away:"
  <contact>.
- **Email changed** (to the old address) — "Your AskNuvexusDoc email address was changed": "The email of your account
  changed from <old address> to <masked new address> on <date and time, IST>." [This wasn't me: undo it] "The link
  works for 7 days. It puts <old address> back, signs out every session and asks you to choose a new password."
  Without an undo token (a later change in the same 7 days): no button, and "If that wasn't you, contact us right
  away:" <contact>.
- **Email change undone** (to the address the undo replaced) — "Your AskNuvexusDoc email change was undone": "The
  email of your account was changed back to <masked restored address> on <date and time, IST>, and every session was
  signed out." "If that wasn't you, contact us right away:" <contact>.
- **Confirm email** — "Confirm your new AskNuvexusDoc email": "Confirm <address> as the email of your AskNuvexusDoc
  account." [Confirm this email] "We'll ask you to sign in to that account first. The link works for 24 hours." /
  "Didn't ask for this? Ignore this email; nothing changes." (google: "You signed up with Google using this address.
  Confirm it so we can send you receipts and reminders and you can reset your password.")
- **Google sign-in added / removed** — "Google sign-in was added to your AskNuvexusDoc account": "<masked Google
  address> can now sign in to your account (<address>). Added on <date and time, IST>." "If that wasn't you, sign in,
  disconnect it in Settings → Account, change your password and contact us:" <contact> / "… was removed …".
- **Receipt** ("Receipt <number> for your <plan> plan": amount, period, method; "The receipt is attached and in
  Settings → Plan & billing."; footer "Turn receipt emails off in Settings → Plan & billing."), **reminders** (the plans
  brief's 4.7 text; footer "Turn plan reminder emails off in Settings → Plan & billing."), **test** ("Test email from
  AskNuvexusDoc": "Mail from AskNuvexusDoc reaches this address.").

A link or the contact (the business's email, else `<base>/contact`) never ends a sentence: it follows its sentence,
which ends in ":", on a line of its own (an anchor in the HTML; review UX-07).

Links: `<base>/verify-email#t=<token>`, `/confirm-email#t=`, `/reset-password#t=`, `/undo-email#t=`; `<base>` is the
**first `https` entry** of `PUBLIC_BASE_URLS`, else the first entry (development). Dates in mails are IST, written as in
the plans brief ("25 Nov 2026, 14:05 IST").

### 6.5 Classes, the daily cap and availability (`outbox.available`)

| Class | What | Share of `MAIL_DAILY_LIMIT` | Counted |
|---|---|---|---|
| `public` | everything a visitor or an unpaid account can cause: sign-up mails, and confirmations and notices for unpaid users | 40% | jobs **created** today, whatever they became (S6), and an earlier day's jobs sent or due today (review DATA-03) |
| `reset` | forgot-password jobs (whatever they resolve to) | 30% | likewise |
| `account` | confirmations and notices for paid users, the admin's test mail | together with `public` and `reset`, at most 80% | `sent` today plus `queued`/`sending` due today |
| `billing` | receipts and plan reminders | 100% (so at least 20% is always theirs) | `sent` today plus `queued`/`sending` due today |

- `available(db, cls)` is what sign-up, resend, forgot and the email-change request check (503 `mail_unavailable`;
  the email change answers 503 too), and what a security notice or a Google sign-up's confirmation checks before it is
  queued (skipped without room); the worker holds a job past its share until the next UTC day (6.3), where its
  `not_after` usually expires it. A class's jobs past its share count only up to the share in the 80% and in
  `billing`'s room, so they never take another class's room (review DATA-01).
- `public_left(db)`: how many `public` jobs still fit today; the Sign-up card shows "About N sign-up emails left today".
- The monitor warns from 80% of the limit (14).

### 6.6 Bounces and complaints (`services/mail/webhook.py`, S42)

- **Resend webhook** `POST /api/mail/resend-events` (no session; `MAIL_WEBHOOK_SECRET` unset → 404): the body at most
  64 KB; the Svix headers `svix-id`, `svix-timestamp` (within 5 minutes of now) and `svix-signature` (one or more
  `v1,<base64>`); the signature is HMAC-SHA256 over `<svix-id>.<svix-timestamp>.<raw body>` with the base64-decoded
  secret after its `whsec_` prefix, compared with `hmac.compare_digest` → else 401. A `svix-id` seen in the last 24 h
  (Redis `SET NX`) → 200 and nothing. Types: `email.bounced` (only permanent bounces: the spike 18.2.6 confirms the
  field; a transient one is ignored) and `email.complained`; any other type → 200 and nothing. Each `to` address of the
  event: upsert `mail_suppressions` (`recipient_sha256`, `reason` `bounce` or `complaint`, `source` `webhook`) and add
  to the day's counters. Answer 200 within 5 s (the work is two small writes). Logs: the type and the masked address.
- **SMTP refusals**: a `permanent` failure with 550 to 553 for the recipient suppresses that address (`source` `smtp`)
  and counts as a bounce.
- **Effects**: the worker cancels jobs to a suppressed address (6.3), except one opt-in a day per address (a sign-up's
  verification or an `email_confirm`: someone just asked for mail at that address, and confirming it is the way out;
  review DATA-04); a bounce suppression lasts 30 days, a complaint until a user confirms that address again (4.8) or
  finishes a sign-up with its link. When today's bounces reach 3 **and** at least 2% of today's sent
  mails, the `public` class is **paused until 00:00 UTC** (`available("public")` false: email sign-up and resend answer
  `mail_unavailable`, Google sign-up keeps working) and the `mail` check warns. Complaints always warn.
- Without the webhook only SMTP refusals count, which miss most bounces (Resend accepts the message first); the
  Sign-up card warns `mail_webhook_missing` while sign-up is open.

### 6.7 Logging and privacy

Logs name the job (`mail <public_id> kind=signup sent in 820 ms`, `… failed: recipient refused (550)`), never the
address, a subject line with a name, token, link or SMTP credentials. Where an address must appear (admin messages,
auth events), it is masked: the first character, `***`, `@`, the domain. A finished outbox row keeps only the
recipient's hash (3.7); backups hold those rows and no live link (S7). README's privacy notes list Resend (and the
region chosen) as the processor of every user email.

### 6.8 Fakes for tests

- **Backend unit tests**: `tests/fake_mail.py` replaces `transport.send_message` with an in-memory sink that records
  each `EmailMessage` and can be told to raise `temporary`, `permanent` or `auth` errors; helpers extract the link and
  token from the last mail to an address; `svix_sign(secret, body)` builds webhook headers.
- **Integration**: `integration/fake_mail/server.py`, a small asyncio SMTP server written in the repo (EHLO, MAIL,
  RCPT, DATA, RSET, NOOP, QUIT, AUTH PLAIN/LOGIN against fixed test credentials; no TLS, so the stack runs
  `MAIL_SMTP_SECURITY=none`), with an HTTP side: `GET /_mail?to=` (the parsed messages: headers, text, HTML,
  attachment names), `POST /_control` (fail the next N with 451 or 550, refuse logins, drop the connection after
  DATA), `POST /_control/event {type, to}` (posts a Svix-signed Resend event to the backend's webhook with the stack's
  test secret), `DELETE /_mail`.

## 7. Turnstile

### 7.1 Where

| Page and endpoint | `action` | When |
|---|---|---|
| `/signup` → `POST /auth/signup` | `signup` | always |
| `/signup` ("Check your inbox") → `POST /auth/signup/resend` | `resend` | always |
| `/forgot-password` → `POST /auth/password/forgot` | `reset` | always |
| `/login` → `POST /auth/login` | `login` | after `challenge_required` (4.4) |

Not on token pages (the token is the proof), the Google start and finish (rate-limited), or anything signed in.

### 7.2 Server-side verification (`services/auth/turnstile.verify`)

`POST TURNSTILE_SITEVERIFY_URL` (form: `secret`, `response`, `remoteip` = the client address as nginx saw it,
`idempotency_key` = a fresh UUID), 10 s timeout. `internal-error`, a timeout or a 5xx → one retry with the same
idempotency key; still failing → **503 `challenge_unavailable`** "We couldn't check that you're not a robot. Try again
in a minute." and a Redis counter for the monitor (sign-in instead carries on, 4.4). `success` false (any error code,
including `timeout-or-duplicate`) → **400 `challenge_failed`** "The check expired or failed. Try again." Success needs
`action` equal to the expected one and `hostname` equal to the host of a `PUBLIC_BASE_URLS` entry (both skipped with a
test secret, whose answers carry `example.com`). The token is never stored or logged; log lines carry only the error
codes.

### 7.3 The client (`components/auth/Turnstile.tsx`)

- Loads `https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit` once, from that exact URL (no copy,
  proxy or SRI), only when a widget mounts, only when the document allows it (7.4) and **only after `/auth/me`
  answered 401 in this document** (`AuthContext` status `signed-out`).
- `turnstile.render(container, {sitekey, action, theme: <the app's resolved theme, "light" or "dark">, size:
  "flexible", appearance: "always", "refresh-expired": "auto", "response-field": false, language: "auto", callback,
  "expired-callback", "error-callback"})`; re-rendered when the app's theme changes; `turnstile.remove(id)` on
  unmount; `turnstile.reset(id)` after every submit (tokens are single use) and after `challenge_failed`.
- **Submit buttons stay enabled** (UX-11). Submitting before a token exists shows "Checking you're not a robot…"
  (visible, `aria-live="polite"`) and sends the form as soon as the token arrives; missing fields get their own
  errors first. A script that doesn't load in 15 s, or an `error-callback`, shows "The security check couldn't load.
  Check your connection or turn off blockers for this site, then try again." with **Try again** (re-renders) and, where
  Google is available, "or continue with Google".
- The sitekey comes from `GET /auth/config` (`turnstile_site_key`); null → the forms that need it show their
  unavailable state.

### 7.4 CSP and page loads

nginx serves `index.html` at **exactly** `/login`, `/signup` and `/forgot-password` with today's CSP plus
`https://challenges.cloudflare.com` in `script-src` and a `frame-src https://challenges.cloudflare.com` (12). If the
spike (18.2) shows Turnstile also needs `connect-src https://challenges.cloudflare.com`, it is added to those three
locations only; if a path-scoped `script-src https://challenges.cloudflare.com/turnstile/v0/` works, it replaces the
host. Every other document keeps today's CSP. The SPA follows three rules (`lib/authDocument.ts`):

1. `turnstileAllowed()` is true when the document was loaded at one of the three paths (read once at start-up from
   `window.location.pathname`).
2. A page that needs the widget in a document that doesn't allow it calls `reloadForTurnstile()`: one
   `window.location.reload()` (the URL is already the right path; React Router's history state survives a reload),
   guarded by a `sessionStorage` flag so it never loops; when the flag is already set, the page shows the "couldn't
   load" state.
3. **Leaving an auth page for the app is a full page load** (`leaveForApp(path)` → `window.location.assign`): after
   sign-in (at once, the challenged one included), sign-up completion and Google finish. Links between signed-out
   pages ("Create an account", "Forgot password?", "Sign in", the public top bar's **Sign up**) are plain `<a href>`
   (full loads), so each lands with its own CSP.

### 7.5 Test keys and outages

- **Development** (`.env.example`): sitekey `1x00000000000000000000AA`, secret `1x0000000000000000000000000000000AA`
  (always pass, dummy token `XXXX.DUMMY.TOKEN.XXXX`). **Backend tests**: a monkeypatched siteverify (pass, fail,
  `timeout-or-duplicate`, 500, timeout, wrong action, wrong hostname). **Integration**: `integration/fake_turnstile/`
  (also serving the HIBP range route) with `TURNSTILE_SITEVERIFY_URL` and `PASSWORD_BREACH_CHECK_URL` pointing at it.
  **e2e**: a route stub for `api.js` that defines `window.turnstile` and calls back with the dummy token; the
  endpoints that would verify it are route-mocked (15.3).
- A test secret (`1x…`, `2x…`, `3x…`), a non-default `TURNSTILE_SITEVERIFY_URL`, or a sitekey without a secret,
  stops the backend at start-up when `PUBLIC_BASE_URLS` holds a public `https` origin (S39).
- When Cloudflare is unreachable: the widget shows its "couldn't load" state; sign-up, resend and reset answer
  `challenge_unavailable` and wait; sign-in carries on under the hard limits; Google sign-in keeps working.
- Turnstile on `http://localhost:8080` of the live box fails Cloudflare's hostname check (the production widget lists
  only `asknuvexusdoc.com`); local requests are never challenged at sign-in, and sign-up there is done at the public
  address. README says so.

## 8. Security and privacy

### 8.1 Throttles

Redis counters (fixed windows, as the login counters) unless stated (`services/auth/throttle.py`):

- **Client address**: `request.client.host` after nginx's real-IP handling (`CF-Connecting-IP` from the tunnel with
  `NGINX_REAL_IP_FROM`).
- **Network**: an IPv4 address itself; an IPv6 address's /64 (an IPv4-mapped IPv6 address counts as IPv4). **Wide
  network**: the IPv4 /24 or the IPv6 /48; every network limit below also applies to the wide network at **four
  times** the number.
- **Local request** (`is_local`): the client address is loopback or private **and** the request's `Host` names a
  `PUBLIC_BASE_URLS` entry that isn't `https` (for example `localhost:8080`). Through the tunnel the host is the public
  one, so a visitor can't pass as local.

| Key | Limit | Over the limit |
|---|---|---|
| `signup` per network (sign-up, Google finish, email changes of unpaid accounts) | 20 an hour, 50 a day | 429 `too_many_attempts`, `Retry-After` |
| `resend` per network | 10 an hour | 429 |
| `forgot` per network | 10 an hour | 429 |
| `token` per network (`signup/check`, `signup/complete`, `password/check`, `password/reset`, `email/check`, `email/verify`, `email/undo-check`, `email/undo`, `google/pending`) | 30 an hour | 429 |
| `google_start` per network | 20 a minute | 429 |
| wrong passwords per pending sign-up | 5 in total | the row deleted, 400 `signup_reset` |
| mails per recipient, **per kind** (`signup`: sign-up and resend; `reset`: forgot; `confirm`: email changes, their resends and a Google sign-up's unverified address), keyed by `sha256(delivery_key)` | 1 a minute, 3 an hour, 6 a day each | **silent**: the request answers as usual and enqueues nothing |
| security notices per account (`password_changed`, `password_added`, `google_linked`, `google_unlinked`; review DATA-01) | 3 an hour, 10 a day; also none while the notice's class has no room today | **silent**: the change goes ahead without its notice |
| email change per user | 5 a day; 3 for an unpaid account | 429 |
| admin test mail | 5 an hour | 429 |
| sign-in, hard | 10 failures per identifier key (per account when the identifier found one), 100 per network (400 per wide network), per 15 min (unchanged settings) | 429 |
| sign-in, challenge | 3 failures per identifier key **from one network**, 10 per network, or more than 50 site-wide in 5 minutes; only with Turnstile configured, never for local requests | 401 `challenge_required` |
| payment links, unpaid users | 3 an hour and 6 a day per user; 30 an hour for all of them | 429 (as today) / 503 `checkout_busy` |
| all mail | `MAIL_DAILY_LIMIT` per UTC day, by class (6.5) | 503 `mail_unavailable` |
| nginx `auth_public` (every public auth endpoint, 12) | 30 requests a minute per address, burst 20 (a coarse backstop) | 429 |
| nginx `billing_checkout` (`POST /api/billing/checkout`) | 10 a minute per address, burst 5 | 429 |

The existing login keys (`login:fail:user:<username>`, `login:fail:ip:<address>`) are replaced by these; nothing
migrates (a window is 15 minutes).

### 8.2 Enumeration

- Sign-up, resend and forgot: S6. The request path is the same for every address (a test asserts the same status,
  headers and bytes and that the request never reads `users`, 15.1); the only differences are in the worker, after the
  response, and they don't show in the cap (public jobs count when enqueued) or in the worker's pace (2 s per public
  job).
- Sign-in: one 401 message for unknown, wrong, inactive, password-less, admin-from-outside and admin-via-email cases;
  the dummy hash runs when no hash is checked; the challenge counters exist for unknown identifiers.
- Token pages reveal an address only to a holder of a valid token (who received it at that address); the email check
  names the asking account only masked.
- `link_required` and finish's `email_taken` appear only for `gmail` and `workspace` claims, whose holder Google says
  controls the address now; `unverified` claims are never looked up (S11). Confirmation's `email_taken` reaches only
  someone who holds both the account's session and the mailbox.
- Closed-sign-up errors describe the site's rules, not an account.
- Accepted residue: the silent per-address limit is a global behaviour, not a per-account one; a determined stranger
  can use up an address's daily sign-up mails (not its resets or confirmations), and that person can still sign up
  with Google.

### 8.3 Tokens, CSRF, redirects, HTTPS and sessions

- Tokens: S7. Compared by looking up the SHA-256 (unique index), then the address hash; the plaintext exists only in
  the worker's memory and the mail. Pages remove the fragment once read; the confirmation page keeps its token only in
  this tab's `sessionStorage` for 10 minutes while the user signs in.
- CSRF: every new write is a same-origin `fetch` with a JSON body (FastAPI refuses `text/plain` bodies, so a cross-site
  form can't post one); nginx's `$xsite_write` guard refuses cross-site writes; the session stays `SameSite=Strict`.
  Login CSRF through Google is stopped by `state` + the `rag_gsignin` binding cookie (an attacker's callback URL fails
  in the victim's browser). The webhook is a server-to-server POST checked by its signature.
- Open redirects: the Google flow redirects only to a stored path on a `PUBLIC_BASE_URLS` origin (4.5);
  `leaveForApp` and `next` accept only same-site paths (`safeNextPath`, `safe_next`); mail links are built from
  settings, never from `Host`.
- **HTTPS** (S41): nginx sends `Strict-Transport-Security: max-age=31536000` on every response (browsers ignore it
  over plain http, so `localhost:8080` is unaffected); Cloudflare's Always Use HTTPS and a `www` → apex redirect are
  going-live blockers (17.1); Rocket Loader, Email Obfuscation, Zaraz and automatic Web Analytics stay off, since a
  script Cloudflare injects is same-origin and would pass `script-src 'self'`.
- **Cookies** (S41): with `COOKIE_SECURE`, the session cookie is `__Host-rag_session` (Path `/`, Secure, no Domain) and
  the auth cookies are `__Secure-rag_gsignin`, `__Secure-rag_gticket` and `__Secure-rag_signup`; without it, the
  names have no prefix. The name changes once at deploy, so everyone signs in again once.
- Session rotation: S38. Bumping `token_version` (reset, undo, admin reset or deactivation) ends every session, Bearer
  tokens included.
- Google tokens: only the ID token is read, in memory; access tokens are discarded; no refresh token is requested.

### 8.4 Audit (`auth_events`)

Types: `signup.completed` (`method` email or google), `email.confirmed` (`source`), `email.changed`,
`email.change_undone`, `email.pending`, `password.reset`, `password.set`, `password.changed`, `google.linked` (`auto`
or `manual`), `google.unlinked` (`self` or `reset`), `google.reauth`, `signup.settings` (the admin's change, before and
after; no user). `actor`: `self`, `admin:<username>` or `system`. `ip`: approximate (as `login_events`), cleared after
90 days. `detail`: masked addresses only. Shown in the user's data export (`account.json` → `security_events`);
deleted with the user. Sign-ins stay in `login_events` (`method` added) and Settings shows "with Google" or "with
password".

### 8.5 Abuse handling

- The admin closes sign-up or one method in Admin → Users → Sign-up; existing accounts keep working.
- The admin finds new accounts with the search box and the "Signed up" column and filter, and deactivates or deletes
  them as today.
- Bounded cost (S31): no plan, no model call; checkout needs a verified email and is capped for unpaid users; the
  `public` class, the suppression list and the automatic pause bound mail; Google sign-ups cost no mail unless their
  address needs a confirmation.
- The monitor (14) reports a mail day running out, bounces and complaints, Turnstile verification failures, a sign-in
  failure surge and the payment-link cap.

### 8.6 Privacy

What is stored, and for how long:

| Data | Where | Kept |
|---|---|---|
| Verified email, canonical form, when verified | `users` | with the account |
| Google `sub`, address at link time and its kind | `user_identities` | until unlinked or the account is deleted |
| Terms version and time of acceptance | `users` | with the account |
| Pending sign-up: address, password hash, browser hash, next page, terms record, approximate IP | `pending_signups` | 7 days at most |
| An unconfirmed address | `pending_emails` | 24 h after the last mail |
| Token hashes and address hashes | `auth_tokens` | a day after use or expiry |
| Recipient address and kind | `email_outbox` | until the job finishes (then only a hash), the row 48 h more |
| Suppressed address hashes | `mail_suppressions` | 30 days (bounces); complaints until reconfirmed |
| Account changes | `auth_events` | approximate IPs 90 days, the rows 365 days or until the account is deleted |
| Usernames of deleted accounts | `username_tombstones` | always (no other data) |
| Sign-ins (approximate IP, browser, OS, device, method) | `login_events` | newest 20 (unchanged) |
| Throttle counters (network prefixes; address hashes) | Redis | their window (at most a day) |

Never stored: Google's name, picture or tokens (not even requested); Turnstile tokens; full IPs beyond the counters;
reset or verification links. Processors the privacy policy must name (section 17): Resend (all user email, US-stored
account data, message content kept 30 days), Cloudflare (Turnstile signals: IP, TLS fingerprint, User-Agent; and the
tunnel), Google (sign-in), and Have I Been Pwned (a 5-character hash prefix of new passwords). The data export gains the
email, the pending email, the linked Google address and date, the terms record and the security events.

### 8.7 Payments and Gmail

- Razorpay: checkout as 4.12. Receipts and reminders go to the verified email (S25); the receipt row's buyer email is
  the account email at the time (it was the profile email). Test-mode checkout stays limited to testers.
- Gmail: S37. The README's Google section explains the two projects.

## 9. Data model and migration 0004

### 9.1 Models (`models.py`)

```python
class User(Base):                       # changed (3.1)
    password_hash: String(255) | None            # None: no password
    email: String(254) | None                    # verified only
    email_canonical: String(254) | None          # unique (uq_users_email_canonical)
    email_verified_at: UTCDateTime | None
    signup_source: String(16) = "admin"          # env | admin | email | google; server default 'admin'
    terms_version: String(32) | None; terms_accepted_at: UTCDateTime | None
    identities: relationship("UserIdentity", cascade="all, delete-orphan", lazy="selectin")

class LoginEvent(Base):                 # changed
    method: String(16) = "password"              # password | google; server default 'password'

class UserIdentity(Base):               # 3.4
    __tablename__ = "user_identities"   # UniqueConstraint(issuer, subject), UniqueConstraint(user_id, provider)
    id: int (pk); user_id: FK users CASCADE; provider: String(16)            # google
    issuer: String(64); subject: String(255); email_at_link: String(254)
    email_kind: String(16)              # gmail | workspace | unverified
    created_at; last_used_at: UTCDateTime | None

class PendingSignup(Base):              # 3.6, 4.1
    __tablename__ = "pending_signups"   # Index(canonical, created_at); Index on expires_at
    id: int (pk); address: String(254); canonical: String(254); password_hash: String(255)
    browser_sha256: String(64) | None; next_path: String(512) | None
    terms_version: String(32); terms_accepted_at: UTCDateTime
    password_failures: Integer = 0; ip: String(64) | None
    created_at; expires_at

class PendingEmail(Base):               # 3.6, 4.8
    __tablename__ = "pending_emails"
    id: int (pk); user_id: FK users CASCADE, unique; address: String(254); canonical: String(254) (index)
    source: String(16)                  # user | google
    created_at; sent_at: UTCDateTime | None; expires_at

class AuthToken(Base):                  # S7
    __tablename__ = "auth_tokens"       # Index(user_id, purpose); Index on expires_at
    id: int (pk); purpose: String(16)   # signup | email | reset | email_undo
    token_sha256: String(64) unique; address_sha256: String(64)    # sha256 of the canonical address it was mailed to
    user_id: FK users CASCADE | None; pending_signup_id: FK pending_signups CASCADE | None
    pending_email_id: FK pending_emails CASCADE | None
    created_at; expires_at; used_at: UTCDateTime | None

class EmailOutbox(Base):                # 6.3
    __tablename__ = "email_outbox"      # Index(status, next_attempt_at); Index(mail_class, created_at); Index(sent_at)
    id: int (pk); public_id: String(32) unique                               # the idempotency key
    kind: String(32); priority: SmallInteger; mail_class: String(16)         # public | reset | account | billing
    to_address: String(254) | None; canonical: String(254) | None            # cleared when the job finishes
    recipient_sha256: String(64)
    user_id: FK users CASCADE | None; pending_signup_id: FK pending_signups SET NULL | None
    pending_email_id: FK pending_emails SET NULL | None
    params: JSON = {}                   # never a token, link or secret
    status: String(16)                  # queued | sending | sent | failed | cancelled | expired
    attempts: Integer = 0; next_attempt_at; not_after; last_error: String(200) | None
    created_at; sent_at: UTCDateTime | None; finished_at: UTCDateTime | None

class AuthEvent(Base):                  # 8.4
    __tablename__ = "auth_events"       # Index(user_id, created_at); Index(type, created_at)
    id: int (pk); created_at; user_id: FK users CASCADE | None; type: String(48); actor: String(64)
    ip: String(64) | None; detail: JSON = {}

class MailSuppression(Base):            # 6.6
    __tablename__ = "mail_suppressions"
    recipient_sha256: String(64) (pk); reason: String(16)                    # bounce | complaint
    source: String(16)                  # webhook | smtp
    created_at; expires_at: UTCDateTime | None                               # None: until reconfirmed

class UsernameTombstone(Base):          # 3.3
    __tablename__ = "username_tombstones"
    username: String(64) (pk); deleted_at: UTCDateTime
```

`UserBillingProfile` loses `email`. `app_state` gains `signup_settings` (JSON, 11.1 `SignupSettings`; missing = the
defaults: closed, both methods on). `Preferences` gains `email_plan_reminders` and `email_receipts` (booleans, default
true; stored in `user_preferences.data`, no migration).

### 9.2 Migration 0004 (`backend/migrations/versions/0004_signup_auth.py`)

In one transaction (`app.db.init_db`), forward only:

1. Create `pending_signups`, `pending_emails`, `auth_tokens`, `user_identities`, `email_outbox`, `auth_events`,
   `mail_suppressions` and `username_tombstones` with their indexes and foreign keys.
2. `users`: add `email`, `email_canonical`, `email_verified_at`, `terms_version`, `terms_accepted_at` (nullable),
   `signup_source` (server default `'admin'`); make `password_hash` nullable (batch mode on SQLite, in place on
   Postgres); create the unique index `uq_users_email_canonical`. Then `UPDATE users SET signup_source = 'env' WHERE
   is_admin`.
3. `login_events`: add `method` (server default `'password'`).
4. Data: insert into `username_tombstones` every distinct `username` of `billing_orders` and `billing_payments` rows
   whose `user_id` is NULL (deleted users), skipping names a current user has; `deleted_at` = the migration time. The
   log line gives the count.
5. Count the non-empty `user_billing_profiles.email` values (the log line gives the count, never an address), then
   drop the column (batch mode on SQLite).
6. `alembic check` finds no difference afterwards (`test_migrations.py`).

The migration imports no app module. No email is invented for anyone; the env admin gets none. On the live database
this means: `admin` → `signup_source` `env`; `bikash` → `admin`; an address `bikash` saved for reminders (if any) is
dropped, and the owner adds it again in Settings → Account (17.5). Bootstrap's admin guard (3.5) runs at start-up.

## 10. Settings

`.env` (`config.py`; `.env.example` gets an "Accounts and sign-up" block and an "Email" block; ranges stop the
backend at start-up like the others):

| Setting | Default | Range / rule | What it does |
|---|---|---|---|
| `MAIL_SMTP_HOST` | empty | | with `MAIL_FROM`: the mailer is configured |
| `MAIL_SMTP_PORT` | 465 | 1 to 65535 | |
| `MAIL_SMTP_SECURITY` | `ssl` | `ssl`, `starttls`, `none` (`none` refused with a public https origin) | |
| `MAIL_SMTP_USERNAME`, `MAIL_SMTP_PASSWORD` | empty | | Resend: `resend` and a sending-only API key; never logged or shown |
| `MAIL_FROM` | empty | `Name <address>` or an address | the From of every user email |
| `MAIL_REPLY_TO` | empty | an address | also adds "Questions? Reply to this email." |
| `MAIL_DAILY_LIMIT` | 80 | 1 to 100,000 | mails per UTC day (6.5) |
| `MAIL_IDEMPOTENCY_HEADER` | `Resend-Idempotency-Key` | a header name or empty | |
| `MAIL_WEBHOOK_SECRET` | empty | `whsec_` and base64, or empty | the Resend webhook's signing secret (6.6); empty: the endpoint answers 404 |
| `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY` | empty | both or neither; test keys refused with a public https origin | Turnstile configured |
| `TURNSTILE_SITEVERIFY_URL` | `https://challenges.cloudflare.com/turnstile/v0/siteverify` | another value refused with a public https origin | the fake in tests |
| `GOOGLE_SIGNIN_CLIENT_ID`, `GOOGLE_SIGNIN_CLIENT_SECRET` | empty | both or neither | Google sign-in configured (a separate client from `GOOGLE_OAUTH_*`) |
| `GOOGLE_SIGNIN_AUTHORIZE_URL`, `GOOGLE_SIGNIN_TOKEN_URL`, `GOOGLE_SIGNIN_JWKS_URL` | Google's | others refused with a public https origin | the fake in tests |
| `PASSWORD_MIN_LENGTH` | 8 | 8 to 64 | S17 |
| `PASSWORD_BREACH_CHECK` | true | | the HIBP range check |
| `PASSWORD_BREACH_CHECK_URL` | `https://api.pwnedpasswords.com/range/` | another value refused with a public https origin | the fake in tests |
| `LOGIN_CHALLENGE_AFTER`, `LOGIN_CHALLENGE_AFTER_PER_IP` | 3, 10 | 1 to `LOGIN_MAX_ATTEMPTS`, 1 to `LOGIN_MAX_ATTEMPTS_PER_IP` | 4.4 |
| `LOGIN_SITE_CHALLENGE_AFTER` | 50 | 10 to 10,000 | failures in 5 minutes across the site that turn the challenge on for everyone (4.4) |
| `ADMIN_SIGNIN_FROM` | `any` | `any`, `local` | 3.5 |
| `CHECKOUT_UNPAID_PER_HOUR` | 30 | 1 to 1,000 | payment links per hour for all unpaid users together (4.12) |
| `TERMS_VERSION` | `2026-11-01` | 1 to 32 of `A-Za-z0-9._-` | recorded with each acceptance; change it when the terms change |

**Removed**: `BILLING_EMAIL_FROM` (6.2). **Unchanged**: `ALERT_SMTP_*` (alerts only), `PUBLIC_BASE_URLS` (now also
the Google sign-in redirect bases; mail links use its first https entry; drop `www` once Cloudflare redirects it,
17.1), `COOKIE_SECURE` (true on the public site; it now also picks the cookie prefixes), `COOKIE_NAME` (the base name;
the prefix is added), `NGINX_REAL_IP_FROM` (required before opening sign-up, S40).

## 11. API (the contract change)

Stage 0 applies this section to `SPEC.md` (sections 1, 2, 3, 4, 6, 8, 9 and 10), `frontend/src/api/types.ts` and a new
`backend/app/schemas_auth.py`; shapes that would break today's frontend build before its builder adapts it land with
that builder's first change (16.2). Every body is validated with `extra="forbid"`; times are ISO 8601 UTC.

### 11.1 Shapes

```ts
type SignupAvailability = { open: boolean /* email || google */; email: boolean; google: boolean }
type AuthConfig = { signup: SignupAvailability;
  google_signin: boolean /* Continue with Google works for existing accounts (configured), even with sign-up closed */;
  password_reset: boolean /* the mailer and Turnstile are configured */;
  turnstile_site_key: string|null; password_min_length: number; password_max_length: number /* 256 */;
  terms_version: string; contact_email: string|null /* Admin → Billing → Business & payments */ }
type LoginRequest = { identifier: string /* email or username; "username" accepted as an alias */; password: string;
  turnstile_token?: string|null }
type SignupRequest = { email: string; password: string; accept_terms: true; turnstile_token: string;
  next?: string|null /* a path on this site, kept through verification */ }
type EmailWithChallenge = { email: string; turnstile_token: string }      // resend and forgot
type Accepted = { status: "accepted" }                                     // 202, the same for every address
type TokenRequest = { token: string /* 43 characters, base64url */ }
type SignupCheck = { email: string; expires_at: string;
  needs_password: boolean /* false when this browser started the sign-up */; next: string|null }
type SignupComplete = { token: string; password?: string|null /* required when needs_password */ }
type PasswordTokenInfo = { email: string; username: string; has_password: boolean; expires_at: string;
  google: { email: string /* masked */; suggest_disconnect: boolean } | null }
type PasswordReset = { token: string; new_password: string; disconnect_google?: boolean /* false */ }
type EmailTokenInfo = { email: string; account: string /* the asking account: its email masked, or its username */;
  signed_in: boolean /* this session is that account's */ }
type EmailConfirmed = { email: string }
type UndoInfo = { old_email: string; new_email: string /* masked */ }
type GoogleStart = { intent: "signin"|"link"|"reauth"; next?: string|null /* signin, reauth: a path on this site */;
  from?: "login"|"signup"|null /* signin */; password?: string|null /* link: required */ }
type GoogleStarted = { authorize_url: string }
type GooglePending = { email: string; email_verified: boolean /* gmail or workspace: confirmed at once */;
  from: "login"|"signup"; expires_at: string }
type GoogleFinish = { accept_terms: true }
type SignupSource = "env"|"admin"|"email"|"google"
type PendingEmailInfo = { address: string; source: "user"|"google"; sent_at: string|null; expires_at: string }
type GoogleLink = { email: string /* at link time */; linked_at: string; last_used_at: string|null }
type NewPassword = { new_password: string }                                // POST /me/password/setup
// changed:
type Me = { …; email: string|null /* verified; the display name when set */ }
type LoginEvent = { …; method: "password"|"google" }
type AccountInfo = { …; email: string|null; email_verified_at: string|null; pending_email: PendingEmailInfo|null;
  has_password: boolean; google: GoogleLink|null; signup_source: SignupSource; terms_accepted_at: string|null;
  email_change_needs: "password"|"google"|"not_allowed" /* google: a step-up; not_allowed: the env admin, or no way to re-authenticate */;
  stepup_until: string|null /* this session holds a step-up grant until then */ }
type Preferences = { …; email_plan_reminders: boolean /* true */; email_receipts: boolean /* true */ }
type BillingProfile = { name: string|null }                               // email removed
type PaymentsInfo = { …; email_reminders: boolean /* now: the mailer is configured */ }
type UserRef = { id: number|null; username: string; email: string|null /* the account's verified email now */ }
type EmailChange = { email: string; password?: string|null /* required when email_change_needs is "password" */ }
type PasswordConfirm = { password: string }
// admin:
type SignupSettings = { open: boolean; email: boolean; google: boolean }
type SignupWarning = "real_ip" | "payments_not_live" | "mail_webhook_missing" | "admin_password_weak"
type SignupStatus = { settings: SignupSettings; effective: SignupAvailability;
  mail_configured: boolean; mail_webhook_configured: boolean; turnstile_configured: boolean;
  google_configured: boolean; payments_live: boolean;
  mail_sent_today: number; mail_queued: number; mail_daily_limit: number;
  public_mail_left: number /* about how many sign-up emails still fit today */; public_paused: boolean /* bounces */;
  bounces_today: number; complaints_7d: number;
  mail_last_error: string|null /* our short reason and when, e.g. "SMTP login refused" */;
  signups_today: number; signups_7d: number; paid_7d: number /* of the last 7 days' sign-ups, how many have paid */;
  warnings: SignupWarning[] /* the policy-draft check is the page's own (13.9) */ }
type MailTest = { to: string }
type AdminUser = { …; email: string|null; email_verified_at: string|null;
  pending_email: { address: string; sent_at: string|null } | null;
  signup_source: SignupSource; has_password: boolean; google_linked: boolean; terms_accepted_at: string|null }
// AdminUserCreate and AdminUserUpdate are unchanged (the password policy applies; 409 username_retired)
```

### 11.2 New endpoints

Public (a session is neither needed nor used, except where noted):

- `GET /auth/config` → `AuthConfig` (`Cache-Control: no-store`).
- `POST /auth/signup` `SignupRequest` → 202 `Accepted` and the `rag_signup` cookie (4.1).
- `POST /auth/signup/resend` `EmailWithChallenge` → 202 `Accepted` (4.3).
- `POST /auth/signup/check` `TokenRequest` → `SignupCheck` · `POST /auth/signup/complete` `SignupComplete` → `Me`
  and the session cookie (4.2).
- `POST /auth/password/forgot` `EmailWithChallenge` → 202 `Accepted` · `POST /auth/password/check` `TokenRequest` →
  `PasswordTokenInfo` · `POST /auth/password/reset` `PasswordReset` → 204 (4.7).
- `POST /auth/email/check` `TokenRequest` → `EmailTokenInfo` · `POST /auth/email/verify` `TokenRequest` →
  `EmailConfirmed` (**needs the session** of the asking account) · `POST /auth/email/undo-check` `TokenRequest` →
  `UndoInfo` · `POST /auth/email/undo` `TokenRequest` → 204 (4.8).
- `POST /auth/google/start` `GoogleStart` → `GoogleStarted` and `rag_gsignin` (intents `link` and `reauth` need the
  session) · `GET /auth/google/callback` → 303 (4.5) · `GET /auth/google/pending` → `GooglePending` (the `rag_gticket`
  cookie) · `POST /auth/google/finish` `GoogleFinish` → `Me` and the session cookie · `POST /auth/google/cancel` → 204.
- `POST /mail/resend-events` → 200 (Resend's signed webhook, 6.6; 404 without `MAIL_WEBHOOK_SECRET`).

Session (the caller's own; the env admin gets 400 "The admin account is managed in .env" on all of them):

- `POST /me/email` `EmailChange` → 202 `Accepted` · `POST /me/email/resend` → 202 · `DELETE /me/email/pending` → 204.
- `POST /me/password/setup` `NewPassword` → 204.
- `DELETE /me/google` `PasswordConfirm` → 204.

Admin:

- `GET /admin/signup-settings` → `SignupStatus` · `PUT /admin/signup-settings` `SignupSettings` → `SignupStatus`
  (an `auth_events` `signup.settings` row).
- `POST /admin/mail/test` `MailTest` → 202 (503 when the mailer isn't configured).

### 11.3 Changed endpoints

- `POST /auth/login`: `LoginRequest`; email sign-in; the challenge and its fallback; `ADMIN_SIGNIN_FROM`; the new 401
  message (4.4).
- `POST /auth/change-password`: the password policy (422 `password_rejected`); 400 `no_password`; revokes the user's
  `reset` tokens; enqueues `password_changed`.
- `GET /auth/me` → `Me` with `email`. `GET /me/account` → the new `AccountInfo` (sign-ins carry `method`).
- `PATCH /me/preferences`: the two email switches. `GET/PUT /billing/profile`: `BillingProfile` without `email`.
- `POST /me/export`: `account.json` gains `email`, `pending_email`, `google`, `terms` and `security_events`.
- `POST /billing/checkout`: 409 `email_required`; the unpaid caps (429) and 503 `checkout_busy` (4.12). The
  unavailable reason for non-testers while payments aren't live (4.12).
- Admin billing lists: `UserRef.email` on orders, payments and the summary's lists.
- Admin users: the new `AdminUser` fields; the password policy on create and reset; 409 `username_retired` on create;
  the delete writes a tombstone.

### 11.4 Errors

New `code`s (`errors.AppError`; clients branch on `code`):

| Status | `code` | `detail` (examples) |
|---|---|---|
| 401 | `challenge_required` | "Confirm you're not a robot to continue." |
| 400 | `challenge_failed` | "The check expired or failed. Try again." |
| 503 | `challenge_unavailable` | "We couldn't check that you're not a robot. Try again in a minute." |
| 429 | `too_many_attempts` | "Too many attempts. Try again in 40 minutes." (`Retry-After`) |
| 403 | `signup_closed` | "New sign-ups are closed right now." |
| 422 | `password_rejected` (+ `reason`) | 5's sentences |
| 503 | `mail_unavailable` | "We can't send emails right now. Try again later, or continue with Google." |
| 400 | `link_invalid` | "This link has expired or was already used." |
| 400 | `wrong_password` | "That isn't the password you chose when you signed up." / "Your password is incorrect." |
| 400 | `signup_reset` | "Too many wrong passwords. Sign up again to choose a new one." |
| 409 | `email_taken` | "This address already has an account. Sign in instead." |
| 403 | `wrong_account` | "Sign in to the account that asked for this change." |
| 503 | `google_unavailable` | "Google sign-in isn't set up." |
| 403 | `reauth_required` | "Confirm it's you with Google first." |
| 409 | `last_sign_in_method` | "Set a password and confirm an email first, so you can still sign in." |
| 400 | `no_password` | "You don't have a password yet. Use Set a password." |
| 409 | `has_password` | "You already have a password. Use Change password." |
| 409 | `username_retired` | "That username belonged to a deleted account. Choose another." |
| 409 | `email_required` | "Add and confirm your email in Settings → Account first. Your receipt goes there." |
| 503 | `checkout_busy` | "Checkout is busy. Try again in a few minutes." |

`AppError` gains an optional `reason` (rendered like `feature`). The access table of SPEC section 6 gains: the public
auth routes ("no session needed"; `email/verify` needs the asking account's session), the webhook, the `/me/email*`,
`/me/password/setup` and `/me/google` routes (401 / 200 / 200 / 400 for the admin), and the new admin routes (401 /
403 / 403 / 200). None of them needs a plan.

## 12. nginx (`frontend/nginx.conf`), exactly

- At the top level, beside the billing zones:

  ```nginx
  # Coarse per-address backstops; the backend's network limits (an IPv6 /64, an IPv4 address) do the real limiting.
  limit_req_zone $binary_remote_addr zone=auth_public:1m rate=30r/m;
  limit_req_zone $binary_remote_addr zone=billing_checkout:1m rate=10r/m;
  limit_req_zone $binary_remote_addr zone=mail_events:1m rate=60r/m;
  ```

- **HSTS**: `add_header Strict-Transport-Security "max-age=31536000" always;` at server level beside the other
  headers, and repeated in **every** location that sets its own `add_header` (the existing ones and those below), since
  such a location inherits none. No `includeSubDomains` and no `preload`.

- The three Turnstile pages (regex locations win over `location /`; repeat every header):

  ```nginx
  # The signed-out pages that may show Cloudflare Turnstile: the same index.html with a CSP that also allows
  # challenges.cloudflare.com. Every other document, the signed-in app included, keeps the strict CSP.
  location ~ ^/(login|signup|forgot-password)$ {
      try_files /index.html =404;
      add_header Cache-Control "no-cache" always;
      add_header Strict-Transport-Security "max-age=31536000" always;
      add_header X-Content-Type-Options "nosniff" always;
      add_header X-Frame-Options "DENY" always;
      add_header Referrer-Policy "same-origin" always;
      add_header Content-Security-Policy "default-src 'self'; script-src 'self' https://challenges.cloudflare.com; frame-src https://challenges.cloudflare.com; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'" always;
  }

  # Pages opened from emailed links (the token is in the fragment): no Referer at all.
  location ~ ^/(verify-email|confirm-email|reset-password|undo-email)$ {
      try_files /index.html =404;
      add_header Cache-Control "no-cache" always;
      add_header Strict-Transport-Security "max-age=31536000" always;
      add_header X-Content-Type-Options "nosniff" always;
      add_header X-Frame-Options "DENY" always;
      add_header Referrer-Policy "no-referrer" always;
      add_header Content-Security-Policy "<today's strict CSP, unchanged>" always;
  }
  ```

- The Google callback, the checkout and the mail webhook, before `location /api/plugins`:

  ```nginx
  # Google redirects the browser here: the query holds a one-time code and the state, so neither the access log nor
  # an upstream error (which prints the request line) may record it.
  location = /api/auth/google/callback {
      limit_req zone=auth_public burst=20 nodelay;
      limit_req_status 429;
      access_log off;
      error_log /var/log/nginx/error.log crit;
      proxy_pass http://backend:8000;
      <the usual proxy_set_header lines and Connection "">
  }

  location ~ ^/api/auth/(login|signup|signup/resend|signup/check|signup/complete|password/forgot|password/check|password/reset|email/check|email/verify|email/undo-check|email/undo|google/start|google/pending|google/finish|google/cancel)$ {
      limit_req zone=auth_public burst=20 nodelay;
      limit_req_status 429;
      proxy_pass http://backend:8000;
      <the usual proxy_set_header lines and Connection "">
  }

  location = /api/billing/checkout {
      limit_req zone=billing_checkout burst=5 nodelay;
      limit_req_status 429;
      proxy_pass http://backend:8000;
      <the usual proxy_set_header lines and Connection "">
  }

  # Resend's signed server-to-server events (bounces, complaints): no Sec-Fetch-Site header, so the cross-site guard
  # lets them through; the backend checks the signature.
  location = /api/mail/resend-events {
      limit_except POST { deny all; }
      limit_req zone=mail_events burst=30 nodelay;
      limit_req_status 429;
      client_max_body_size 64k;
      access_log /var/log/nginx/access.log path_only;
      proxy_pass http://backend:8000;
      <the usual proxy_set_header lines and Connection "">
  }
  ```

- `main.py`'s access-log redaction adds `/api/auth/google/callback` (its query becomes `[redacted]`).
- The `$xsite_write` guard already covers every new browser write. The CSP of `location /` and `/legal/` is unchanged.

## 13. UI (functional)

The visual brief ([`signup-auth-visual.md`](signup-auth-visual.md)) gives every screen's look, sizes and the class
map; this section gives behaviour and the copy that carries it. New routes in `App.tsx`: `/signup`, `/forgot-password`
(signed-out only, as `/login`: a session goes to the app), `/verify-email`, `/confirm-email`, `/reset-password`,
`/undo-email`, `/signup/google` (with or without a session). Shared parts in `components/auth/` (16.3): `Turnstile`,
`GoogleButton`, `AuthCard` (the centred card of every task page, with a form layout and a result layout),
`PasswordRules` (the live length rule and the server's rule, a refusal shown in place of the rule it broke),
`IdentityRow`, `TermsCheck`, `EmailField` (an email input with the "Did you mean …?" suggestion), and
`pages/auth.css` (`auth-` prefix).

Every form: visible labels; `PasswordInput` (show/hide); hints and errors linked by `aria-describedby`; errors
`role="alert"`; focus moved to the new heading on each state change; works at 390 px. **Submit buttons stay enabled**
(UX-11): on submit, missing or invalid fields get their own error ("Enter your email.", "Tick the box to accept the
terms.") and focus moves to the first; a pending robot check shows "Checking you're not a robot…" and the form goes
out when it finishes (7.3). Errors render from `code` with the server's `detail`. Links that open a new tab say so
(visually hidden "(opens in a new tab)").

**Email fields** (`EmailField`, `lib/emailTypos.ts`): on blur, when the domain is within edit distance 2 of a common
domain and not equal to it, "Did you mean **name@gmail.com**?" appears under the field; one tap applies it; it never
blocks. The list: `gmail.com`, `googlemail.com`, `yahoo.com`, `yahoo.co.in`, `yahoo.in`, `outlook.com`,
`hotmail.com`, `live.com`, `icloud.com`, `me.com`, `rediffmail.com`, `proton.me`, `protonmail.com`, `aol.com`,
`zoho.com`. Used on sign-up, resend, forgot and change email.

**In-app browsers** (`lib/authDocument.inAppBrowser()`): a User-Agent with `FBAN`, `FBAV`, `Instagram`,
`LinkedInApp`, `Line/` or Android's `; wv)` replaces every Google button with the notice "Google sign-in doesn't work
inside this app. Open asknuvexusdoc.com in Chrome or Safari." and **Copy link** (the current URL; "Link copied").
Email sign-up stays.

### 13.1 Sign-in (`/login`): changes that keep the hero, copy and tour

The owner's layout stays as built (visual spec 6.28): the form card first in the DOM and on the right, the aside with
the hero picture, the copy and the product tour on the left, the phone order unchanged, the credit line and policy
links at the end of the card. Inside the card only, top to bottom:

- "Welcome back" and the lede, as today. **While sign-up is open**, directly under the lede: "New to AskNuvexusDoc?
  **Create an account**" (a plain `<a href="/signup">`, S43).
- A `?google=<result>` notice (`role="alert"`, then the parameter is removed): `denied` "Google sign-in was
  cancelled.", `expired` "That took too long. Try again.", `error` "We couldn't sign you in with Google. Try again.",
  `link_required` "An account already uses this email. Sign in with your password, then connect Google in Settings →
  Account.", `use_password` "Sign in with your email or username, then connect Google in Settings → Account.",
  `signup_closed` "New sign-ups are closed right now.", `unavailable` "We couldn't sign you in. If you think this is a
  mistake, contact us." Unknown values are ignored. Tones and icons are the visual brief's.
- **Continue with Google** (`GoogleButton`, `from: "login"`, full width) and an "or" rule, when `config.google_signin`
  (the in-app notice instead, as above).
- The form: **Email or username** (`autoComplete="username"`, `inputMode="email"`); **Password** with **Forgot
  password?** at the right end of its label row (a plain `<a href="/forgot-password">`, when `config.password_reset`);
  the 401 block or the challenge; **Sign in**.
- When `challenge_required` arrives: the caption "Confirm you're not a robot, then sign in again." and the widget above
  **Sign in**; the typed values are kept.
- The 401 block: "Incorrect email, username or password." and the two hints of 4.4 in the same block.
- The foot block keeps today's note ("Accounts are created by your administrator. Ask them if you need access or a new
  password.") **only while sign-up is closed**; with sign-up open it isn't shown.
- After a successful sign-in: `leaveForApp(postLoginPath(state))` at once.

The integrator checks that **Continue with Google and "Create an account" fit in a 390×664 viewport**, Sign in stays
on the first screen at 390×844 and 360×740, and the desktop card stays within the visual brief's measurements.

### 13.2 Sign-up (`/signup`)

The same split layout as `/login` (the card on the right, the same aside): the layout parts move into a shared
`pages/login/AuthSplit.tsx` used by both pages, with the DOM order unchanged.

- Heading **"Create your account"**; lede "Chat with your documents. Choose a plan after you sign up."; under it
  "Already have an account? **Sign in**" (a plain `<a href="/login">`).
- **Continue with Google** (`from: "signup"`, `next` from the URL) and "or sign up with email", when `signup.google`.
- **Email** (`EmailField`, `type="email"`, `autoComplete="email"`), **Password** (`autoComplete="new-password"`,
  `PasswordRules`), the **terms checkbox** (unticked): "I agree to the [Terms] and the [Privacy policy].", the Turnstile
  widget, **Create account**. `?next=` (a path, `safeNextPath`) goes into the request.
- Errors by `code`: `password_rejected` in the rule it broke; address errors under the email; `challenge_failed`
  resets the widget; `too_many_attempts`, `mail_unavailable`, `challenge_unavailable` and `signup_closed` above the
  button, and with the first three **Continue with Google** is repeated under the message when `signup.google`.
- **Sent** state (same URL): "Check your inbox", "We sent an email to **<address>**." and three steps: "Open the link
  in it within **24 hours**." / "In this browser that's all it takes. On another device, enter the **password you just
  chose**." / "Choose a plan to start chatting with your documents." Then "**Didn't get it?** Check your spam folder
  too." with **Resend** ("Resend in 0:42" until 60 s after the last send, then "Resend email"; a click mounts the
  widget and the request goes when its token arrives; "Sent again"); after the third mail the help sentence of 4.3;
  and "Wrong address? **Start again**" (back to the form with the email kept and the password cleared; **Create
  account** shows the same countdown when the address is unchanged). The address and the send times live in
  `sessionStorage`, so a reload keeps the state.
- **`?resend=1`** (from the sign-in hint): the sent state for the address carried in `sessionStorage`, with
  **Resend** available at once; without one, an email field and **Send the link again**.
- **Closed** (`signup.open` false): "New sign-ups are closed right now. If you have an account, sign in." with **Sign
  in**. Email only or Google only shows only that method.

### 13.3 Verify email (`/verify-email`)

States: checking ("Checking your link…"); **form** ("Finish creating your account", the address in an identity row
"Confirmed by this link"; with `needs_password` the lede "One last step: enter the password you chose when you signed
up.", a read-only email field for password managers, **The password you chose** and "Forgot it? **Sign up again**";
without it the lede "Your email is confirmed. Create your account to continue."; **Create account**);
`wrong_password` (under the field); `signup_reset` ("Too many wrong passwords" with **Sign up again**);
`link_invalid` ("This link has expired" with **Sign up again** and **Sign in**); `email_taken` ("You already have an
account" with **Sign in** and "Forgot your password?"); `signup_closed`; no token in the URL ("This link is
incomplete. Open it from the email again, or copy the whole link."). Success → `leaveForApp(next ?? "/plans")`. A
signed-in visitor sees "Finishing signs you out of <display name>." "Sign up again" carries the address in
`sessionStorage`.

### 13.4 Google sign-up (`/signup/google`)

States: checking; **form** ("Create your AskNuvexusDoc account", "You're signing up with Google as" and the identity
row; when not `email_verified`, "We'll email that address so you can confirm it. Until then, receipts and password
resets can't use it."; the terms checkbox; **Create account**; **Use a different Google account**; **Cancel**). With
`from: "login"` the title is "No AskNuvexusDoc account uses this address yet" and **I already have an account** is a
full-width secondary button under **Create account** (4.5); **Cancel** goes. Expired ("That took too long", "For your
safety, the Google sign-up waits 15 minutes at most.", **Continue with Google**, "Back to sign in"); `email_taken` (the
4.5 message with **Sign in**); `signup_closed`. Success → `leaveForApp(next ?? "/plans")`.

### 13.5 Forgot and reset password

- `/forgot-password` (`AuthCard`): "Reset your password", lede "Enter the email address on your account and we'll send
  you a link to choose a new password."; **Email** (`EmailField`, hint "No email on your account? Ask the
  administrator."); the widget; **Send reset link**. Sent: "Check your email", "If an account uses **<address>**,
  we've emailed it a link to reset the password. The link works for 30 minutes." and "Nothing arrived? Check your spam
  folder, or make sure this is the address on your account." with **Back to sign in**. Unavailable
  (`password_reset` false): "Password reset isn't available right now. Ask the administrator."
- `/reset-password`: checking; form ("Choose a new password", or "Set your password" without `has_password`; the
  identity row with the display name and username; **New password** with `PasswordRules` and **Repeat it** with
  "The passwords don't match."; with `google`, **Also disconnect Google sign-in (<masked address>)**, ticked when
  `suggest_disconnect`, hint "Do this if someone else may have connected it."; **Save password**); done ("Password
  changed": "Your password was changed and you were signed out everywhere. Sign in with the new one." or "Your password
  is set"; **Sign in** as a full load); `link_invalid` ("Reset links work once, for 30 minutes. Ask for a new one." with
  **Ask for a new link** and "Back to sign in").

### 13.6 Confirm and undo an email

- `/confirm-email`: checking; signed in as the asking account: "Confirm your email", "Use **<address>** as the email
  of your account?" with **Confirm this email** and a quiet "Not now"; not signed in: "Sign in to confirm", "Sign in to
  the account <account> to confirm **<address>**." with **Sign in** (to `/login?next=/confirm-email`; the token waits
  in this tab's `sessionStorage` for 10 minutes); signed in to another account: "You're signed in to another account",
  "Sign out, then sign in to <account> to confirm this email." with **Sign out**; done ("Your email is confirmed",
  **Continue** to `/?settings=account`); `link_invalid` ("Ask for a new link in Settings → Account."); `email_taken`
  ("That address is taken", "Another account already uses this address."); `wrong_account`.
- `/undo-email`: checking; "Undo the email change?", "Put **<old address>** back as the email of your account? This
  signs out every session and removes the password; we'll email you a link to choose a new one." with **Undo the
  change** and a quiet "Not now"; done ("Your email is back", the sentence of 4.8, **Go to sign in**); `link_invalid`
  ("Undo links work once, for 7 days. Contact us if you still need help."); `email_taken` (4.8's sentence and the
  contact address).

### 13.7 Settings → Account

For the env admin: today's content, plus "The admin account is managed in .env" where the new sections would be. For
everyone else:

- **Profile**: the name is the display name (S45); the role line's text becomes the source ("You signed up with
  Google", "You signed up with email", "Your account was created by the administrator"); the facts gain **Username**
  ("also works for sign-in") and **Email** (a "Confirmed" badge, or "No email"); "Last sign-in" says "with Google" or
  "with password". The address appears once, as the name.
- **Email** group: a description and **Change email…** or **Add email…**. The inline form: new address
  (`EmailField`), and the current password when `email_change_needs` is `password`; when it is `google` and
  `stepup_until` is null or past, "For your safety, confirm it's you with Google before changing your email." and
  **Continue with Google** (intent `reauth`, `next` `/?settings=account&change=email`), after which the form opens
  without a password field. A pending address shows "Waiting for you to confirm **<address>**", when it was sent and
  how long the link works, with **Resend** and **Cancel**.
- **Sign-in methods** group: **Password** ("Change password…" as today; without a password, **Set a password**: the
  Google step-up as above with `next` `/?settings=account&setup=password`, then **New password**, **Repeat it**,
  `PasswordRules` and **Save password**, 4.9); **Google** ("Connected as <address> since <date>" with
  **Disconnect…** (password; disabled with the `last_sign_in_method` reason when it would leave no way in), or
  **Connect Google…** (password, then **Continue with Google**; hidden for an account without a password)).
  `?google=linked|in_use|already_linked|denied|error|expired|reauth_mismatch` shows its message in this group;
  `reauth_ok` opens the form that asked for it.
- **Recent sign-ins**: each entry says "with Google" or "with password".
- **Sessions**: unchanged.

The avatar menu and the Settings dialog's header show the display name.

### 13.8 Settings → Plan & billing, the plan page and the return page

- Plan & billing: a new **Emails** section: "Receipts and plan reminders go to **<address>**. **Change it in
  Account**" and two switches (**Email me a receipt for each payment**, **Email me before my plan ends**); with no
  verified email, "Add an email in **Settings → Account** to get receipts and reminders by email." with **Add email**;
  nothing when the mailer isn't configured. The billing profile keeps only **Name on receipts**.
- `/plans` and checkout: with no verified email, the `email_required` sentence with a link to Settings → Account above
  the options (4.12).
- `BillingReturnPage`'s "Remind me before my plan ends" email ask is removed; with no verified email and a configured
  mailer it shows one line linking to Settings → Account.

### 13.9 Admin → Users and Admin → Billing

- **Sign-up** card above the list: the status (Open or Closed), four tiles from `SignupStatus`: **Methods** ("Email
  and Google", "Email only", "Google only", "None"), **Sign-ups** ("3 today", "14 in 7 days · 2 paid"), **Email
  today** ("12 of 80", "About 20 sign-up emails left · resets 05:30 IST", or "Sign-up emails paused: too many
  bounces"), **Checks** (Email, Bounce reports, Turnstile, Google), the warnings (`real_ip`: "Visitor addresses aren't
  reaching the app: set NGINX_REAL_IP_FROM before opening sign-up."; `payments_not_live`: "Online payments aren't live:
  new accounts can't buy a plan yet."; `mail_webhook_missing`: "Bounce reports aren't set up: add Resend's webhook and
  MAIL_WEBHOOK_SECRET."; `admin_password_weak`: "The admin password is short or common: change ADMIN_PASSWORD in
  .env."; the policy pages still drafts, which the card checks itself by fetching `/legal/privacy.md` and
  `/legal/terms.md` for the draft marker; and sign-up open while the mailer or Turnstile isn't configured, read from
  the status's fields), and **Edit…**.
- The dialog: **Open sign-up** (with a warning present, turning it on shows a warning notice listing them with **Open
  anyway**); **Email and password** and **Continue with Google** (each disabled with "Not configured: <setting names>"
  when its prerequisites are missing); the checks list (Email with "12 of 80 sent today", the last error, **Send a test
  email…**; Bounce reports; Turnstile; Google sign-in; Visitor addresses; Online payments; Privacy policy and terms;
  Admin password); **Cancel** and **Save**.
- The list: a **search box** (username or email, filtering the loaded list); the email as the user cell's second line
  (confirmed, "Pending: <address>", "No email", or "Managed in .env"); a **Signed up** column (the source, with the
  Google mark, over the date; sortable, newest first); a select **All sign-ups / Last 7 days / Email / Google /
  Admin** beside the plan chips.
- The forms: **New user** as today (the password refusal under the field; `username_retired` under the username);
  **Edit user** gains a read-only facts box (signed up how and when, the terms accepted, Google connected or not,
  password set or not, the email and its state).
- **Admin → Billing**: wherever a `UserRef` is shown (payments, orders, attention items), its email is the second line
  under the username.

### 13.10 Public pages

- The public top bar gains **Sign up** (a plain `<a href="/signup">`, primary small) beside **Sign in** while sign-up
  is open (it reads `/auth/config`).
- `/pricing`'s foot button reads "Create an account to choose a plan" (a plain `<a href="/signup?next=/plans">`) while
  sign-up is open, with "Already have an account? Sign in" under it; closed, today's "Sign in to choose a plan".
- The policy pages stay owner-written Markdown (plans brief B31). ADMIN adds to the `privacy.md` and `terms.md` drafts
  (still marked draft) a section listing the facts of 8.6 for the owner to keep or rewrite. `/about` is the homepage
  URL given to Google (17.4); it must describe the app and link the privacy policy (the public layout's footer does).

## 14. Monitoring, deploy and backups

### 14.1 The `mail` check (`app/ops/checks.py`, "Email")

| Result | When |
|---|---|
| `ok` | "Email not set up" while sign-up is closed and no billing mail is due; or nothing below |
| `warning` | a job `failed` in the last 24 h; a due job waiting over 10 minutes (the worker or the provider is stuck); a job that couldn't be prepared, created over 10 minutes ago and still retried (preparation failures count as attempts, review DATA-06); sending paused after a refused SMTP login; today's count at or above 80% of `MAIL_DAILY_LIMIT`; the `public` class paused by bounces; 3 or more bounces today; any complaint in the last 7 days; 10 or more Turnstile verification errors (`challenge_unavailable`) in the last hour; the site-wide sign-in challenge turned on in the last hour; sign-up open (setting) while the mailer or Turnstile isn't configured, or without the mail webhook |
| `unknown` | the database can't be read |

Counts only, never addresses. Admin → System shows it; the sign-up card links to it. The plans brief's `billing`
check gains a warning when the unpaid payment-link cap (4.12) was reached in the last hour.

### 14.2 Deploy, rollback and backups

- **Rehearse** 0004 on a restored copy of the latest live backup in a throwaway Compose project: row counts kept;
  `admin` → `env`, `bikash` → `admin`; the tombstone and dropped-email counts in the log; `alembic check` clean; the app
  works with no `MAIL_*`, `TURNSTILE_*` or `GOOGLE_SIGNIN_*` (sign-up closed, sign-in by username unchanged); `bikash`
  and `admin` sign in with their current passwords (NFC, 5).
- **Deploy**: a verified backup, then `APP_VERSION=$(git describe --always --dirty) docker compose up -d --build`
  (every service: the backend, the worker **and the monitor**, which is built from `./backend` too and only then has
  the `mail` check and the unpaid-cap billing warning), then recreate the frontend. Afterwards Admin → System and the
  monitor's round both report the `mail` ("Email") check. Everyone signs in again once (the cookie's new name, 8.3).
  Sign-up stays closed until the owner opens it (17.5).
- **Rollback** = restore the pre-deploy backup.
- **Backups**: the new tables are in `pg_dump` automatically; they hold no live link, token or finished job's address
  (S7, S21).

## 15. Tests and CI

No test talks to real Google, Cloudflare, Resend or HIBP.

### 15.1 Backend (`backend/tests/`)

- `conftest.py`: `fake_mail` (6.8), `fake_turnstile` (pass, fail, duplicate, 500, timeout, wrong action or hostname),
  `fake_hibp`, a `google_oidc` fixture (an RSA key, a JWKS, a token minter with chosen claims), helpers
  `signup(email, password)`, `last_link(address)`, `drain_mail()` (runs the MailLoop's job processing in-process),
  `svix_sign`.
- `test_auth_emails.py`: parsing and canonical forms (case, IDN, dots, plus, quotes, control characters, lengths);
  delivery keys (Gmail dots, `googlemail.com`, `+tags` on any domain); masking.
- `test_auth_passwords.py`: length in code points after NFC, blocklist, context words, HIBP hit, miss, padding
  entries with count 0, timeout and 500 failing open; NFC hashing and verifying; the admin password check; the admin
  form and change-password apply the policy.
- `test_auth_tokens.py`: 256-bit tokens, only hashes stored, expiry per purpose, single use, the address binding (a
  token refused once its address changed), revocation on every email write and password change, one live
  `email_undo` per user that neither a later change nor a reset revokes, the fragment links on the first https origin.
- `test_auth_throttle.py`: networks (IPv4, IPv6 /64, IPv4-mapped), wide networks at four times, `is_local` (private
  address with a localhost host, a private address with the public host, a public address), per-kind address limits
  with delivery keys.
- `test_signup.py`: the 4.1 order (a closed sign-up never calls Turnstile; the network limit before Turnstile;
  Turnstile before parsing); 202 bodies and the cookie on every answer; up to three pending rows and the fourth
  deleting the oldest; completion in the same browser without a password, elsewhere with the right and wrong password,
  a missing one, 5 failures → `signup_reset`; expired and used links; the address taken meanwhile; sign-up closed
  meanwhile; `next` kept and sanitized; the session and `Me` (plan `none`); no welcome mail; pruning after 7 days;
  **a stranger's sign-up for the same address neither kills the first link nor uses the address's reset quota**
  (SEC-07).
- `test_signup_enumeration.py`: for a new address, an existing active account, a Google-only one, a turned-off one
  and a pending one: identical status, headers (but the cookie's random value) and bytes for sign-up, resend and
  forgot; a statement hook asserts the request never reads `users`; only the worker's results differ (the right mail
  or none), and the day's `public` and `reset` counts move the same way for all of them.
- `test_login_identifier.py`: email or username; unverified and pending addresses never sign in; password-less
  accounts; the generic 401; one counter per account for both identifier forms; the challenge per identifier **and
  network** (a stranger's failures from another network don't challenge the owner), per network, and the site-wide
  surge; no challenge without Turnstile configured or for local requests; a Turnstile outage at sign-in falls back to
  the hard limits; counters for unknown identifiers; `ADMIN_SIGNIN_FROM=local`; the `username` alias.
- `test_google_signin.py`: start (origin, `next` sanitizing, rate limit, the cookie's attributes and prefix, `scope`
  exactly `openid email`); callback: missing or replayed state, wrong cookie, `access_denied`, exchange failure, every
  ID-token check (signature with another key, `alg` none and HS256, unknown `kid` then refetch, `iss`, `aud` list and
  `azp`, expired, `iat` ahead, nonce, `email_verified` false and `"true"`), the kinds, each row of 4.5's table,
  inactive users, sign-up closed; **an `unverified` claim never queries `users` by email**; the ticket's single use and
  expiry; finish and its re-checks; pending sign-ups deleted for a `gmail` or `workspace` address; an `unverified`
  address held as pending with a confirmation queued; `from` carried; no Google token stored anywhere.
- `test_google_linking.py`: auto-link only for `gmail` (**a `workspace` claim with a new `sub` and an existing verified
  address doesn't link**, SEC-03); manual link with password (wrong password counted), a session ended meanwhile,
  `in_use`, `already_linked`; unlink rules; notices; the step-up (`reauth`: the linked `sub` only, a different one →
  `reauth_mismatch`, `auth_time` too old → `expired`, the grant bound to the session's `sid`, single use, 5 minutes);
  the pre-hijacking scenarios of research 4.1 (classic-federated merge, trojan identifier, unexpired email change,
  non-verifying IdP) and **SEC-01's chain A** (an attacker's account asks to change its email to the victim's; the
  victim clicking the link without the attacker's session confirms nothing) each blocked.
- `test_password_reset.py`: forgot (enumeration as above), the worker's choice (a user, a pending-only address → the
  verification mail, nothing), the reset (sessions ended, no session issued, other tokens revoked, pending email
  cancelled, counters cleared, notice queued), the Google disconnect choice and `suggest_disconnect`, a token whose
  address no longer matches, `no_password`.
- `test_email_change.py`: re-authentication by password and by a step-up grant (none, spent, another session's);
  confirmation **only with the asking account's session** (none → refused, another account → `wrong_account`); the
  address taken meanwhile; notices to the old address; reset tokens revoked; a suppression cleared on confirmation;
  **SEC-01's chain B** (a typo'd address's holder can't confirm); the undo (old address back, password removed,
  sessions ended, tokens revoked, the reset mail and the `email_undone` notice queued, the address taken meanwhile →
  409, single use, 7 days); **an intruder's second change (A → B → C) or a reset doesn't cancel A's undo**, and only
  A gets an undo link.
- `test_password_setup.py`: setting a first password with a step-up grant (no session ended, `password_added`
  queued), without one (403), with a password already (409).
- `test_admin_signup.py`: sign-up settings (validation, effective availability, audit event), every `SignupStatus`
  field and warning (`real_ip_warning` for a public `Host` with a private client, `payments_not_live`,
  `mail_webhook_missing`, `admin_password_weak` with HIBP faked), the counts, the mail test, the password policy and
  `username_retired` in the user forms, the tombstone on delete, the new `AdminUser` fields.
- `test_bootstrap.py` additions: the admin guard (3.5) refuses each forbidden case with a clear message; the weak
  admin password warning.
- `test_mail_enqueue.py` (CORE): enqueue never commits and stores no token or link; the class per kind and paid
  state; `available` per class with the 40%, 30%, 80% and billing rules; public and reset counted at enqueue whatever
  they became; `public_left`; `suppressed`; the bounce pause.
- `test_mail_outbox.py` (MAIL): the drain order; `not_after`; retries and backoff; permanent and auth errors (the
  15-minute pause); a crash in `sending` recovered with the same idempotency key; the idempotency header; tokens minted
  per attempt; addresses cleared at finish; the 2 s pacing of public jobs (with a fake clock); each kind's resolution
  (6.4), preferences, missing emails, suppressed recipients; sweeps (3.7).
- `test_mail_webhook.py`: the Svix signature (good, wrong secret, old timestamp, several signatures, missing headers),
  replays, permanent and transient bounces, complaints, unknown types, the 404 without a secret, the body limit; SMTP
  550 suppressing.
- `test_mail_templates.py`: every kind renders text and HTML in the frame; one button and the written-out link; no
  `<img`, `http` resource or tracking parameter in the HTML; links on the first https origin; no other user's address;
  dates in IST; the reply line only with `MAIL_REPLY_TO`.
- `test_mail_transport.py` (from `test_ops_channels.py`'s SMTP tests): `ssl`, `starttls`, `none`, the error kinds; the
  alert `EmailChannel` still passes its tests.
- `test_billing_reminders.py`, `test_billing_receipts.py`: reminders and receipts through the outbox to the verified
  email, honouring the switches; no email → nothing; the receipt's buyer email; the billing floor (a receipt goes out
  when the other classes are spent).
- `test_billing_checkout.py` additions: `email_required`; the unpaid per-user caps; the site-wide cap and
  `checkout_busy`; the non-tester sentence while payments aren't live; `UserRef.email`.
- `test_config.py`: every S39 refusal; `BILLING_EMAIL_FROM` warning; the cookie names with and without
  `COOKIE_SECURE`; the new settings' ranges.
- `test_migration_0004.py`: a database at 0003 with the env admin, an admin-created user, profile emails (counted,
  then the column gone), billing rows of a deleted user (a tombstone) → the columns, `signup_source`, the tables,
  `alembic check` clean (SQLite and the `postgres` mark).
- `test_route_guards.py`: a row per new route; `test_deletion_completeness.py`: the new tables and the tombstone;
  `test_export.py`: the new `account.json` fields; `test_ops_*`: the `mail` check's results and the `billing` check's
  new warning.
- `postgres`-marked races: two completions of one sign-up; a completion against a Google finish for the same address;
  two confirmations of one address by two users; an undo against a new change; the outbox claim under two loops.

### 15.2 Integration (`integration/`)

`compose.it.yml` gains `fake_mail` (6.8), `fake_turnstile` (siteverify with tokens `pass-*` succeeding, `fail-*`
failing, a reused token `timeout-or-duplicate`, a control route for outages; and the HIBP range route), and OIDC on
`fake_google`: an authorize page that redirects back at once for the account chosen by `POST /_control/signin`
(`sub`, `email`, `email_verified`, `hd`, `auth_time`), a token endpoint returning an RS256 ID token with the request's
nonce, and `/oauth2/v3/certs`. The stack sets `MAIL_*` (security `none`), `MAIL_WEBHOOK_SECRET` (a test value),
`TURNSTILE_*` with the fake URL, `GOOGLE_SIGNIN_*` with the fake URLs, `PASSWORD_BREACH_CHECK_URL`, and opens sign-up
through the admin API at the start of the file. `integration/tests/test_18_signup.py` (FLOWS, items 1 to 8) and
`test_19_mail.py` (MAIL, items 9 to 13):

1. sign-up → the verification mail at the fake → complete with the same client's cookie (no password) → session,
   `Me.plan.state` `none`, no other mail; a second sign-up completed from another client with the password;
2. sign-up for an existing address → "already have an account" mail, no new user, the same 202; a stranger's later
   sign-up for a pending address leaves the first link working;
3. resend and its silent limits; the network 429;
4. sign-in by email and by username; the challenge after 3 failures from one network with a fake token, none from
   another network; a Turnstile outage at sign-in falls back;
5. forgot → reset → old sessions get 401, no session issued, the "password changed" mail, Google disconnected when
   ticked; forgot for a pending-only address → a verification mail;
6. Google: a new `@gmail.com` account through `/signup/google`; an existing verified Gmail address auto-linked; a
   `workspace` address with an existing account → `link_required`; an unverified-kind address held as pending and
   confirmed only with the session; manual link and unlink; the step-up then a first password;
7. email change → confirmation at the new address (refused without the session, accepted with it) → the notice at
   the old one → undo → old address back, sessions ended, a reset mail;
8. the admin's create form refuses a common password and a retired username; deleting a user tombstones the name;
9. mail failures: 451 twice then delivered once, 550 → failed and suppressed; the daily cap → 503
   `mail_unavailable` for sign-up while a reset and a receipt still go out;
10. a billing receipt mail with the PDF after a fake Razorpay payment; a reminder (periods moved with `harness.sql`);
    checkout refused without a verified email;
11. bounces: `POST /_control/event` sends three signed `email.bounced` events → suppressions, the `public` class
    paused, the next sign-up 503, Google sign-up unaffected; a complaint warns;
12. sweeps (rows aged with `harness.sql`); finished jobs hold no address; deletion of a user removes their
    identities, tokens and outbox rows;
13. the `mail` monitor warnings delivered to the fake alert receiver.

### 15.3 Frontend (Vitest) and end-to-end (Playwright)

- **Vitest** next to the code: `lib/authDocument.ts` (allowed paths, the one reload and its guard, `safeNextPath`,
  `inAppBrowser` with real User-Agent strings, the stash with its expiry); `lib/emailTypos.ts` (hits, misses, exact
  domains, edit distance 2); `Turnstile` (script loaded once and **not before `/auth/me` answered 401**, render
  options and the app's theme, re-render on a theme change, reset after submit, timeout and error states, remove on
  unmount; a fake `window.turnstile`); `GoogleButton` (start with each intent, the authorize URL check, errors, the
  in-app notice and Copy link, both sizes); `PasswordRules` (the live length rule, a refusal replacing its row, editing
  restoring it); `AuthCard` (both layouts); the login page (label, the "Create an account" line by config, forgot link,
  the hints and their link, challenge, Google button first, `?google=` messages, the foot note only while closed,
  full-load exit; the hero and tour still rendered in order); every new page's states (13.2 to 13.6), enabled submit
  buttons and their per-field errors; Settings → Account's groups, the step-up return (`reauth_ok`) and every result
  message; Plan & billing's switches and lines; the plan page's `email_required` line; the public top bar and pricing
  button by config; the admin Sign-up card, tiles and dialog, the search box, column, filter and forms; Admin →
  Billing's email lines.
- **e2e** (`e2e/`, against the stack): `signup.spec.ts` stubs `https://challenges.cloudflare.com/turnstile/v0/api.js`
  with a small script (registered before the outside-host guard) and **route-mocks every endpoint that would send mail
  or call Cloudflare or Google** (`/api/auth/config`, `signup*`, `password/*`, `email/*`, `google/*`, `/api/me/email*`,
  `/api/me/password/setup`); the fixtures add those to the blocked list so an unmocked call fails the test. Covered:
  sign-up → sent → resend; verify page in both forms → plans (mocked `Me`); forgot and reset pages; confirm (signed in,
  signed out) and undo pages; the Google finish page from both entries; the login challenge; **no request to
  `challenges.cloudflare.com` when `/auth/me` answers 200**; real nginx headers (the extended CSP on `/signup`,
  `/login`, `/forgot-password` only, `no-referrer` on the four token pages, HSTS everywhere, the strict CSP on `/` and
  `/plans`); Settings → Account with a mocked `AccountInfo`; the admin Sign-up card read-only. `a11y.spec.ts` adds the
  new pages and their error states. No request leaves for `challenges.cloudflare.com`, `accounts.google.com` or
  Resend.

### 15.4 CI

No new job: the backend, Postgres, frontend, e2e and integration jobs pick up the new tests; `compose.it.yml` gains
the two fakes. No new package (S33); `idna` moves into `requirements.in` at its locked version.

## 16. Work split and interfaces

Five builders on disjoint files. **CORE**: the contract, the data model and the shared auth and mail foundations.
**FLOWS**: the backend flows, routers, Google, nginx and the integration fakes for them. **MAIL**: the mailer, the
worker loop, templates, the webhook, billing mail and checkout guards, monitoring and the mail fake. **APP**:
frontend, user-facing. **ADMIN**: frontend admin and the policy drafts. Builders never build into the repo, never
change git state, keep test users named `smoke-*`, `it-*` or `e2e-*`, and report a contract bug instead of editing the
contract after stage 0.

### 16.1 Stages

```
stage 0:  CORE                       the contract: SPEC.md, types.ts, schemas_auth.py, additive fields
stage 1:  CORE (backend foundation) ∥ APP (frontend foundation)
stage 2:  FLOWS ∥ MAIL ∥ APP (pages, Settings, public) ∥ ADMIN
then:     integrator: README, HANDOFF, CLAUDE.md, briefs index, 0004 rehearsal, Docker rebuild, browser pass,
          the spikes of 18.2 with the owner's keys
```

Each stage ends with every suite green (`pytest`, `tsc --noEmit`, `vite build`, Vitest) and `alembic check` clean.

### 16.2 Ownership (each file has exactly one owner per stage)

| Builder | Owns |
|---|---|
| **CORE**, stage 0 | `SPEC.md` (all of section 11, the data model, settings and route table); `frontend/src/api/types.ts` (11.1 except `AdminUser`, `SignupSettings`, `SignupStatus` and `SignupWarning`, which land with ADMIN, and `AccountInfo` and `BillingProfile`, which land with APP; `PasswordReason` included); `backend/app/schemas_auth.py` (new: every public auth, Google, account-email and admin sign-up shape); additive fields with defaults: `schemas.py` (`Me.email = None`; `Preferences.email_plan_reminders` and `email_receipts`, both `True`, with their lines in `services/account.py`'s preference reader and writer), `schemas_account.py` (`LoginEventInfo.method = "password"`), `schemas_billing.py` (`UserRef.email = None`); `errors.py` (`reason`); the typed test factories (`frontend/src/test/**`, `e2e/support/fakes.ts`) |
| **CORE**, stage 1 | `backend/app/models.py`; `backend/migrations/versions/0004_signup_auth.py`; `backend/app/config.py` (section 10, the S39 checks, the warnings); `.env.example` (both new blocks; `BILLING_EMAIL_FROM` removed); `backend/requirements.in` and the locks (`idna` top-level); `backend/app/security.py` (NFC); `backend/app/deps.py` (the session cookie's name and prefix); `backend/app/bootstrap.py` (`signup_source`, the admin guard, the admin password warning); `backend/app/services/auth/{__init__,emails,passwords,tokens,throttle,turnstile,usernames,audit,signup_settings,stepup,cookies}.py` (new); `backend/app/services/billing/paid.py` (new: `has_paid`, `payments_live`); `backend/app/data/common-passwords.txt` (new, with its source header); `backend/app/services/mail/{__init__,outbox}.py` (new: the kind and class tables, `enqueue`, `available`, `public_left`, `public_paused`, `suppressed`, `suppress`, `unsuppress`, `mail_status`, `wake`); `backend/tests/conftest.py` and `backend/tests/{fake_mail,fake_turnstile,fake_hibp,google_oidc}.py` (new); tests `test_auth_emails.py`, `test_auth_passwords.py`, `test_auth_tokens.py`, `test_auth_throttle.py`, `test_mail_enqueue.py`, `test_migration_0004.py`, the bootstrap and config additions |
| **APP**, stage 1 | `frontend/src/lib/{authDocument,emailTypos}.ts` (new); `src/api/client.ts` (`authApi`, `accountEmailApi`, the codes); `src/components/auth/{Turnstile,GoogleButton,AuthCard,PasswordRules,IdentityRow,TermsCheck,EmailField}.tsx`, `google-g.svg` and `auth-components.css` (new); `src/components/ui/icons.tsx` (the visual brief's three icons); `src/pages/login/AuthSplit.tsx` (new: the split layout taken out of `LoginPage.tsx`, which APP also changes in this stage only to use it); their Vitest files |
| **FLOWS**, stage 2 | `backend/app/routers/auth.py` (login, config, change-password); `backend/app/routers/{auth_signup,auth_google,auth_email,admin_auth}.py` (new); `backend/app/services/auth/{login,signup,google,identities,reset,email_change,undo}.py` (new); `backend/app/routers/me.py` (the email, password-setup and Google routes); `backend/app/services/account.py` (`AccountInfo`, `record_login(method)`); `backend/app/services/export.py`; `backend/app/routers/admin.py` and `backend/app/schemas_admin.py` (the `AdminUser` fields, the policy, tombstones); `backend/app/schemas_account.py`; `backend/app/main.py` (routers, MAIL's `mail_events` router included, redaction); `frontend/nginx.conf` (12); `backend/tests/fake_google.py` and `integration/fake_google/**` (OIDC); `integration/fake_turnstile/**` (new); `integration/compose.it.yml` (all three fakes; `fake_mail` as MAIL's 16.3 says); `integration/gen_env.py`; `integration/tests/test_18_signup.py`; backend tests `test_signup*.py`, `test_login_identifier.py`, `test_google_*.py`, `test_password_reset.py`, `test_password_setup.py`, `test_email_change.py`, `test_admin_signup.py`, `test_route_guards.py`, `test_deletion_completeness.py`, `test_export.py` and existing tests that need the new shapes |
| **MAIL**, stage 2 | `backend/app/services/mail/{transport,templates,jobs,drain,webhook,sweeps}.py` (new); `backend/app/routers/mail_events.py` (new: the webhook); `backend/app/ops/channels.py` (uses `transport`); `backend/app/worker.py` (MailLoop, sweeps); `backend/app/services/billing/{reminders,payments,business,receipts,checkout,summary}.py` (reminders and receipt mail through the outbox; `email_reminders()`; the checkout guards and caps of 4.12; `UserRef.email` filled); `backend/app/routers/billing.py`, `backend/app/routers/admin_billing.py` and `backend/app/schemas_billing.py` (`BillingProfile` without email, `PaymentsInfo`); the two `Preferences` switches (read only; CORE adds them in stage 0); `backend/app/ops/checks.py` and `backend/app/schemas_system.py` (the `mail` check, the `billing` check's warning); `integration/fake_mail/**` (new); backend tests `test_mail_outbox.py`, `test_mail_webhook.py`, `test_mail_templates.py`, `test_mail_transport.py`, `test_billing_reminders.py`, `test_billing_receipts.py`, `test_billing_checkout.py`, `test_ops_channels.py`, `test_ops_*` for the checks; `integration/tests/test_19_mail.py` |
| **APP**, stage 2 | `src/pages/LoginPage.tsx`, `src/pages/login.css`; `src/pages/{SignupPage,VerifyEmailPage,GoogleSignupPage,ForgotPasswordPage,ResetPasswordPage,ConfirmEmailPage,UndoEmailPage}.tsx` and `src/pages/auth.css` (new); `src/App.tsx` (routes); `src/auth/{AuthContext.tsx,loginRedirect.ts}`; `src/api/types.ts` (`AccountInfo` and `BillingProfile`, with the screens that use them); `src/components/settings/**` (Account's groups, Plan & billing's section); `src/components/chat/AvatarMenu.tsx` (the display name); `src/pages/{PlansPage,CheckoutPage,BillingReturnPage}.tsx` (the `email_required` line, the reminder ask); `src/pages/public/{PublicLayout.tsx,PricingPage.tsx,public.css}`; `e2e/**` (`signup.spec.ts`, the fixtures' blocked list, `auth.spec.ts`, `a11y.spec.ts`); their Vitest files |
| **ADMIN**, stage 2 | `src/api/types.ts` (`AdminUser`, `SignupSettings`, `SignupStatus`, `SignupWarning`, with the admin UI); `src/components/admin/**` (`SignupCard.tsx` and `SignupSettingsModal.tsx` new; `UsersTab`, `UserFormModal`, `adminApi.ts`, `admin.css`, and Billing's payment, order and attention views for `UserRef.email`, changed); `frontend/public/legal/{privacy,terms}.md` (the draft sections of 13.10); their Vitest files |
| **Integrator** | `README.md` (configuration, the Google section with both projects, email, the webhook and Turnstile, HTTPS and the Cloudflare settings, the Brevo runbook, security and privacy notes, operations), `docs/HANDOFF.md`, `docs/briefs/README.md`, `CLAUDE.md` (the owner's 2026-10-28 decision replaces "Users are created only from the admin page"; a line on sign-up and mail); the rehearsal of 14.2; the Docker rebuild (recreate the frontend after the backend); a browser pass of section 13 against the visual brief with route mocks or the fakes, both themes, 390 and 1440 px, the login page compared with its current screenshots and the 390×664 check of 13.1; with the owner's keys and approval, the spikes of 18.2 |

`src/api/types.ts` is the one file APP and ADMIN both change in stage 2, in disjoint blocks (their own types only).
The two `Preferences` switches and `UserRef.email` are CORE's in stage 0, so MAIL, APP and ADMIN only use them.

### 16.3 Frozen interfaces

**Backend** (CORE writes them in stage 1; FLOWS and MAIL rely on them):

```python
# app/services/auth/emails.py
class EmailError(ValueError): ...                                   # the user-facing sentence
@dataclass(frozen=True)
class Email: address: str; canonical: str; local: str; domain: str; delivery_key: str
def parse(value: str) -> Email: ...                                 # 3.2; raises EmailError
def digest(value: str) -> str: ...                                  # sha256 hex of a canonical address or delivery key
def mask(address: str) -> str: ...                                  # "j***@example.com"

# app/services/auth/passwords.py
PasswordReason = Literal["too_short", "too_long", "common", "context", "breached"]
async def check(password: str, *, context: Sequence[str] = ()) -> None: ...   # AppError 422 password_rejected (reason)
def weak_admin_password(password: str) -> bool: ...               # under 15, blocklisted or a context word; no network
async def breached(password: str) -> bool | None: ...              # HIBP; None when it couldn't tell

# app/security.py (changed): hash_password and verify_password use the NFC form

# app/services/auth/tokens.py
TokenPurpose = Literal["signup", "email", "reset", "email_undo"]
TTL: Final[Mapping[TokenPurpose, timedelta]]                        # 24 h, 24 h, 30 min, 7 days
async def mint(db: AsyncSession, purpose: TokenPurpose, *, address_canonical: str, user_id: int | None = None,
               pending_signup_id: int | None = None, pending_email_id: int | None = None) -> str: ...   # flushes
async def find(db: AsyncSession, purposes: Collection[TokenPurpose], token: str, *,
               lock: bool = False) -> AuthToken | None: ...         # unused and unexpired only
def address_matches(row: AuthToken, canonical: str) -> bool: ...
async def spend(db: AsyncSession, row: AuthToken) -> None: ...
async def revoke(db: AsyncSession, purposes: Collection[TokenPurpose], *, user_id: int | None = None,
                 pending_signup_id: int | None = None, pending_email_id: int | None = None) -> None: ...
def link(purpose: TokenPurpose, token: str) -> str: ...             # <first https origin>/<page>#t=<token>

# app/services/auth/throttle.py
Rule = Literal["signup", "resend", "forgot", "token", "google_start", "email_change", "mail_test"]
AddressKind = Literal["signup", "reset", "confirm"]
def client_ip(request: Request) -> str: ...
def network(ip: str) -> str: ...                                    # IPv4 address or IPv6 /64
def wide_network(ip: str) -> str: ...                               # IPv4 /24 or IPv6 /48
def is_local(request: Request) -> bool: ...
async def check(request: Request, rule: Rule, *, subject: str | None = None) -> None: ...  # AppError 429 too_many_attempts
async def address_allowed(kind: AddressKind, delivery_key: str) -> bool: ...   # silent; counts when allowed
async def login_wait(request: Request, identifier_key: str) -> int | None: ...       # seconds for the 429, or None
async def login_challenge_due(request: Request, identifier_key: str) -> bool: ...
async def login_failed(request: Request, identifier_key: str) -> None: ...
async def login_cleared(request: Request, identifier_key: str) -> None: ...

# app/services/auth/turnstile.py
TurnstileAction = Literal["signup", "resend", "reset", "login"]
def configured() -> bool: ...
async def verify(token: str | None, action: TurnstileAction, remote_ip: str | None) -> None: ...  # 400 / 503 AppError

# app/services/auth/usernames.py
async def generate(db: AsyncSession) -> str: ...                    # "user-" + 8 base32, never taken or retired
async def retired(db: AsyncSession, username: str) -> bool: ...
async def tombstone(db: AsyncSession, username: str) -> None: ...   # never commits

# app/services/auth/stepup.py
async def grant(user_id: int, sid: int) -> datetime: ...            # 5 minutes; returns the expiry
async def until(user_id: int, sid: int | None) -> datetime | None: ...
async def spend(user_id: int, sid: int | None) -> bool: ...         # GETDEL

# app/services/auth/cookies.py
def cookie_name(base: str) -> str: ...                              # "__Secure-" + base with COOKIE_SECURE
def set_auth_cookie(response: Response, base: str, value: str, *, path: str, max_age: int,
                    samesite: Literal["lax", "strict"]) -> None: ...
def clear_auth_cookie(response: Response, base: str, *, path: str) -> None: ...

# app/deps.py (changed)
def session_cookie_name() -> str: ...                               # "__Host-" + COOKIE_NAME with COOKIE_SECURE

# app/services/auth/audit.py
async def record(db: AsyncSession, type: str, *, actor: str, user_id: int | None = None,
                 request: Request | None = None, detail: Mapping[str, object] | None = None) -> None: ...  # never commits

# app/services/auth/signup_settings.py
async def load(db: AsyncSession) -> SignupSettings: ...
async def save(db: AsyncSession, value: SignupSettings, *, actor: str) -> None: ...
async def availability(db: AsyncSession) -> SignupAvailability: ...    # the settings and every prerequisite

# app/services/billing/paid.py
async def has_paid(db: AsyncSession, user_id: int) -> bool: ...    # a live or offline payment applied
async def payments_live() -> bool: ...                              # live keys, not rejected by Razorpay

# app/services/mail/outbox.py
MailKind = Literal["signup", "password_reset", "password_changed", "password_added", "email_confirm",
                   "email_changed", "email_undone", "google_linked", "google_unlinked", "receipt", "plan_reminder",
                   "test"]
MailClass = Literal["public", "reset", "account", "billing"]
@dataclass(frozen=True)
class MailStatus:
    sent_today: int; queued: int; limit: int; public_left: int; public_paused: bool
    bounces_today: int; complaints_7d: int; last_error: str | None
def configured() -> bool: ...
async def available(db: AsyncSession, cls: MailClass) -> bool: ...
async def public_left(db: AsyncSession) -> int: ...
async def public_paused(db: AsyncSession) -> bool: ...
async def enqueue(db: AsyncSession, kind: MailKind, *, to: str, canonical: str | None = None,
                  user_id: int | None = None, pending_signup_id: int | None = None,
                  pending_email_id: int | None = None,
                  params: Mapping[str, str | int | bool | None] | None = None) -> None: ...   # never commits
async def wake() -> None: ...                                       # after the caller's commit; best effort
async def suppressed(db: AsyncSession, recipient_sha256: str) -> bool: ...
async def suppress(db: AsyncSession, recipient_sha256: str, *, reason: Literal["bounce", "complaint"],
                   source: Literal["webhook", "smtp"]) -> None: ...  # also counts the day's bounce
async def unsuppress(db: AsyncSession, recipient_sha256: str) -> None: ...
async def mail_status(db: AsyncSession) -> MailStatus: ...
```

MAIL's frozen parts for others: `app.routers.mail_events.router` (FLOWS includes it in `main.py`); the `fake_mail`
service (`integration/fake_mail/Dockerfile`, SMTP on port 2525, HTTP on 8025, the routes of 6.8), which FLOWS adds to
`compose.it.yml`.

**Frontend** (APP writes them in stage 1; APP's and ADMIN's stage 2 rely on them):

```ts
// src/lib/authDocument.ts
export const TURNSTILE_PATHS: readonly ["/login", "/signup", "/forgot-password"];
export function turnstileAllowed(): boolean;
export function reloadForTurnstile(): boolean;          // true when it reloads; false when it already did once
export function safeNextPath(value: unknown): string;   // "/" unless a same-site path (not //, not /api/)
export function leaveForApp(path: string): void;        // window.location.assign(safeNextPath(path))
export function readFragmentToken(): string | null;     // reads #t=…, then removes the fragment
export function inAppBrowser(userAgent?: string): boolean;
export function stash(key: "email" | "confirm_token", value: string, ttlMs: number): void;  // this tab's sessionStorage
export function take(key: "email" | "confirm_token"): string | null;                        // null when absent or expired
// src/lib/emailTypos.ts
export function suggestEmail(value: string): string | null;
// src/components/auth/Turnstile.tsx (mounts nothing until AuthContext's status is "signed-out")
export default function Turnstile(props: { siteKey: string; action: "signup"|"resend"|"reset"|"login";
  onToken(token: string|null): void; resetSignal?: number }): JSX.Element;
// src/components/auth/GoogleButton.tsx (always "Continue with Google"; the in-app notice instead when inAppBrowser())
export default function GoogleButton(props: { intent: "signin"|"link"|"reauth"; next?: string;
  from?: "login"|"signup"; password?: string; size?: "lg"|"sm"; onError(message: string): void;
  disabled?: boolean }): JSX.Element;
// src/components/auth/AuthCard.tsx (with mark: the centred result layout)
export default function AuthCard(props: { title: string; lede?: ReactNode;
  mark?: { icon: ReactNode; tone: "accent"|"success"|"warning"|"neutral" } | "busy";
  children?: ReactNode; footer?: ReactNode }): JSX.Element;
// src/components/auth/PasswordRules.tsx
export default function PasswordRules(props: { id: string; password: string; minimum: number;
  rejected?: { reason: PasswordReason; message: string } | null }): JSX.Element;
// src/components/auth/IdentityRow.tsx
export default function IdentityRow(props: { kind: "email"|"user"|"google"; primary: string;
  secondary?: string }): JSX.Element;
// src/components/auth/TermsCheck.tsx
export default function TermsCheck(props: { checked: boolean; onChange(checked: boolean): void;
  error?: string|null }): JSX.Element;
// src/components/auth/EmailField.tsx (with the "Did you mean …?" suggestion)
export default function EmailField(props: { id: string; label: string; value: string; onChange(value: string): void;
  error?: string|null; hint?: ReactNode; autoFocus?: boolean }): JSX.Element;
// src/pages/login/AuthSplit.tsx: the login page's split layout (form card first, aside with hero, copy and tour)
export default function AuthSplit(props: { children: ReactNode /* the card's content */;
  compactOnPhone?: boolean /* the sent and closed states: no picture on phones */ }): JSX.Element;
// src/api/client.ts
export const authApi: { config(): Promise<AuthConfig>; signup(b: SignupRequest): Promise<Accepted>;
  resend(b: EmailWithChallenge): Promise<Accepted>; checkSignup(token: string): Promise<SignupCheck>;
  completeSignup(b: SignupComplete): Promise<Me>; forgot(b: EmailWithChallenge): Promise<Accepted>;
  checkPasswordToken(token: string): Promise<PasswordTokenInfo>; resetPassword(b: PasswordReset): Promise<void>;
  checkEmailToken(token: string): Promise<EmailTokenInfo>; confirmEmail(token: string): Promise<EmailConfirmed>;
  checkUndo(token: string): Promise<UndoInfo>; undoEmail(token: string): Promise<void>;
  googleStart(b: GoogleStart): Promise<GoogleStarted>; googlePending(): Promise<GooglePending>;
  googleFinish(b: GoogleFinish): Promise<Me>; googleCancel(): Promise<void> };
export const accountEmailApi: { change(b: EmailChange): Promise<Accepted>; resend(): Promise<Accepted>;
  cancel(): Promise<void>; setupPassword(b: NewPassword): Promise<void>; unlinkGoogle(b: PasswordConfirm): Promise<void> };
```

## 17. Owner actions

Dashboard names are as of 2026-11; if a menu has moved, search the dashboard for the setting's name. None of these
blocks the build: the code is built, tested and deployed with sign-up closed, and opening it waits for them.

### 17.1 Before opening sign-up (these block going live)

1. **Write the real privacy policy, terms and about page** (`frontend/public/legal/privacy.md`, `terms.md`,
   `about.md`; Claude can draft them for review). The privacy policy covers 8.6: what is stored and for how long, the
   processors (Resend, Cloudflare Turnstile and the tunnel, Google sign-in, Have I Been Pwned's prefix check,
   Razorpay), what is received from Google at sign-in (`sub`, email, whether it is verified, `hd`) and that nothing
   else is kept, how to ask for deletion (Contact), and the consent notice for India's DPDP Rules (with counsel). The
   about page describes the app and links the privacy policy (Google's brand check, research 1.4). Remove the draft
   marker, rebuild the frontend, and set `TERMS_VERSION` to the terms' date.
2. **Fill in the contact email** in Admin → Billing → Business & payments (mails and error pages show it).
3. **Razorpay live**: finish KYC, put the live keys and the webhook secret in `.env`, and check one real payment (the
   plans brief, 14.2). Until then the Sign-up card warns "Online payments aren't live" and new accounts see "Paid plans
   open soon".
4. **Cloudflare, HTTPS** (zone `asknuvexusdoc.com`):
   - SSL/TLS → Edge Certificates → **Always Use HTTPS**: on.
   - Rules → Redirect Rules → **Create rule** "www to apex": when *Hostname equals* `www.asknuvexusdoc.com`, dynamic
     redirect to `concat("https://asknuvexusdoc.com", http.request.uri.path)`, status **301**, preserve the query
     string. Then remove `https://www.asknuvexusdoc.com` from `PUBLIC_BASE_URLS` (17.5).
   - Leave **off**: Rocket Loader (Speed → Optimization), Email Address Obfuscation (Scrape Shield), Zaraz, and the
     automatic Web Analytics snippet (Analytics → Web Analytics: no automatic setup for this site). Each would inject a
     script into our pages.
   - HSTS comes from nginx (S41); nothing to switch on for it in Cloudflare.
5. **Visitor addresses**: `NGINX_REAL_IP_FROM=172.16.0.0/12` is already in `.env` (HANDOFF); the Sign-up card checks
   it.

### 17.2 Email: Resend and Cloudflare DNS

1. Create an account at **resend.com**. **Domains → Add domain** `asknuvexusdoc.com`, region **Tokyo
   (ap-northeast-1)**.
2. In Cloudflare → DNS → Records (each **DNS only**, grey cloud, TTL Auto), add exactly what Resend's Records tab shows
   (research 3.3); expect:

   | Type | Name | Value | Priority |
   |---|---|---|---|
   | MX | `send` | `feedback-smtp.ap-northeast-1.amazonses.com` | 10 |
   | TXT | `send` | `v=spf1 include:amazonses.com ~all` | |
   | TXT | `resend._domainkey` | the `p=…` key Resend shows | |
   | TXT | `_dmarc` | `v=DMARC1; p=none; rua=mailto:dmarc@asknuvexusdoc.com;` | |

   Resend's "Sign in to Cloudflare" button (Domain Connect) can add the first three for you; the `_dmarc` record is
   yours. Keep the `google-site-verification` TXT on `@` (17.4). Click **Verify** in Resend.
3. Cloudflare → Email → **Email Routing**: enable it for the apex (it adds its own MX and SPF on `@`; they don't clash
   with Resend's on `send`), add your own mailbox as a destination and verify it, then route `support@` and `dmarc@`
   to it.
4. Resend → the domain → **open and click tracking off** (off by default; check).
5. Resend → **API Keys → Create**: name "AskNuvexusDoc SMTP", permission **Sending access**, domain
   `asknuvexusdoc.com`. Copy it (shown once): it is `MAIL_SMTP_PASSWORD`.
6. Resend → **Webhooks → Add endpoint**: URL `https://asknuvexusdoc.com/api/mail/resend-events`, events
   **email.bounced** and **email.complained**. Copy its signing secret (`whsec_…`): it is `MAIL_WEBHOOK_SECRET`.
7. After two weeks of clean DMARC reports at `dmarc@`, change `_dmarc` to `p=quarantine`; later `p=reject`.
8. **Before any announcement or paid promotion**, move to Resend Pro ($20 a month, no daily cap).

**If Resend pauses the account** (runbook): switch to Brevo: add its domain records from its dashboard, turn "Block
unknown IP addresses" off, then set `MAIL_SMTP_HOST=smtp-relay.brevo.com`, `MAIL_SMTP_PORT=587`,
`MAIL_SMTP_SECURITY=starttls`, its SMTP login and key, `MAIL_IDEMPOTENCY_HEADER=` (empty), and run
`docker compose up -d backend worker monitor` (the monitor's `mail` check reads the same settings). Bounce reports then
come only from SMTP refusals.

### 17.3 Turnstile

Cloudflare dashboard → **Turnstile → Add widget**: name "AskNuvexusDoc", hostname `asknuvexusdoc.com` only (no `www`,
which redirects, and no `localhost`), widget mode **Managed**, pre-clearance **No**. Create, then copy the **Site key**
into `TURNSTILE_SITE_KEY` and the **Secret key** into `TURNSTILE_SECRET_KEY`. Both go in `.env` only: the site key
reaches browsers through `/api/auth/config`, and the secret never leaves the backend.

### 17.4 Google sign-in (research 1.6)

**A separate Google Cloud project is needed** (R1): the Gmail plugin's project stays in Testing with its restricted
scopes and is not touched.

1. **Search Console** (search.google.com/search-console): add a **Domain** property `asknuvexusdoc.com`, choose the
   TXT method, add the record on `@` in Cloudflare DNS (DNS only), and verify (it can take a few days to show). Use the
   Google account that will own the new project.
2. **console.cloud.google.com → New project** "AskNuvexusDoc" (not the Gmail project).
3. **Google Auth Platform → Get started**: app name AskNuvexusDoc, user support email, Audience **External**, developer
   contact email.
4. **Branding**: logo (optional; a square 120×120 PNG of at most 1 MB), home page `https://asknuvexusdoc.com/about`,
   privacy policy `https://asknuvexusdoc.com/privacy`, terms `https://asknuvexusdoc.com/terms`, authorized domain
   `asknuvexusdoc.com`.
5. **Data access → Add or remove scopes**: only `openid` and `…/auth/userinfo.email` (both non-sensitive; no
   `userinfo.profile`).
6. **Clients → Create client → Web application** "AskNuvexusDoc web": no JavaScript origins; one **authorized
   redirect URI**, exactly `https://asknuvexusdoc.com/api/auth/google/callback`. Copy the client ID and secret (the
   secret may not be shown again) into `GOOGLE_SIGNIN_CLIENT_ID` and `GOOGLE_SIGNIN_CLIENT_SECRET`.
7. **Audience → Publish app** (In production). No warning screen is expected with these scopes.
8. **Branding → Verify branding** once the real privacy policy and about page are live; when it reads "Ready to
   publish", **Publish branding** within 7 days. Until then the consent screen shows the domain instead of the name and
   logo; sign-in still works.
9. Optional, for a development stack: in the existing Gmail project add a second Web client with
   `http://localhost:8080/api/auth/google/callback` and use it only in that stack's `.env`. Use each client at least
   once every six months: Google deletes clients unused for that long.

### 17.5 `.env`, deploy and opening sign-up

1. `.env` (keep the existing lines; never commit it):

   ```
   PUBLIC_BASE_URLS=https://asknuvexusdoc.com,http://localhost:8080
   COOKIE_SECURE=true
   NGINX_REAL_IP_FROM=172.16.0.0/12
   MAIL_SMTP_HOST=smtp.resend.com
   MAIL_SMTP_PORT=465
   MAIL_SMTP_SECURITY=ssl
   MAIL_SMTP_USERNAME=resend
   MAIL_SMTP_PASSWORD=<the Resend API key>
   MAIL_FROM=AskNuvexusDoc <no-reply@asknuvexusdoc.com>
   MAIL_REPLY_TO=support@asknuvexusdoc.com
   MAIL_WEBHOOK_SECRET=<whsec_… from the Resend webhook>
   TURNSTILE_SITE_KEY=<the site key>
   TURNSTILE_SECRET_KEY=<the secret key>
   GOOGLE_SIGNIN_CLIENT_ID=<the client ID>
   GOOGLE_SIGNIN_CLIENT_SECRET=<the client secret>
   TERMS_VERSION=<the terms' date, e.g. 2026-11-20>
   ```

   Optional: `PASSWORD_MIN_LENGTH` (18.1), `PASSWORD_BREACH_CHECK`, `ADMIN_SIGNIN_FROM=local` (recommended if you
   administer only from this Mac), `MAIL_DAILY_LIMIT` (raise it after moving to Resend Pro). Remove
   `BILLING_EMAIL_FROM` if present. Make `ADMIN_PASSWORD` at least 15 characters and not a common one. Then
   `docker compose up -d backend worker monitor` (the monitor's `mail` check reads the same settings) and
   `docker compose up -d --force-recreate frontend`.
2. Admin → Users → **Sign-up**: the checks read "configured", no warnings; **Send a test email** to your address (it
   arrives, isn't in spam, and Gmail's "Show original" says SPF, DKIM and DMARC "PASS").
3. As `bikash`: Settings → Account → **Add email**, then open the confirmation link while signed in as `bikash`
   (receipts, reminders and checkout need it; an address saved earlier for reminders was not carried over).
4. **Open sign-up** (email and Google), then at the public URL sign up once with a throwaway address and once with a
   Google account, and delete both users afterwards.

## 18. Open questions

### 18.1 For the owner

**Before the build** (decided):

1. **Terms for existing and admin-created users.** Decided by the owner on 2026-11-01: only self-sign-ups accept the
   terms (S28); `bikash` and the users the admin creates are covered by the owner's own arrangement with them, and
   nobody is asked again when the terms change (a follow-up). No sign-in gate is built.

**Can wait until going live** (settings or setup; the build doesn't change):

2. The minimum password length: 8 (proposed; ASVS level 1, with the blocklist and the breach check) or 15 (NIST for
   passwords used alone; it would also apply to `bikash`'s next change and the admin's forms).
3. The breach check (Have I Been Pwned): on (proposed). It sends the first 5 characters of the password's SHA-1 to a
   third party (no account data); the privacy policy names it.
4. The sending address: `no-reply@asknuvexusdoc.com` (proposed, as you said) or a `mail.` subdomain, which Resend
   recommends for reputation isolation (the records move to `send.mail` and `resend._domainkey.mail`; it doesn't
   protect against an account-wide pause).
5. `ADMIN_SIGNIN_FROM=local` (only from this Mac) or `any` (the default).
6. Whether to offer self-service account deletion later (a follow-up; until then users ask through Contact and you
   delete them).

### 18.2 To check in a spike (the integrator, with the owner's keys; research 6)

1. Turnstile under the 12 CSP: is `connect-src` needed (browser console)? Does `Referrer-Policy: same-origin` affect
   the hostname check (error 110200)? Does a path-scoped `script-src https://challenges.cloudflare.com/turnstile/v0/`
   work? Change the three locations only.
2. The widget after an SPA route change between the three pages, `turnstile.remove` on unmount, a theme change, and
   the one-reload rule after sign-out.
3. Google's consent screen for the new project before and after brand publishing (domain versus name and logo), and
   whether brand verification accepts `/about` as the homepage.
4. Whether an In-production External client accepts an `http://localhost:8080` redirect URI without affecting brand
   verification. If yes, the live box can offer Google sign-in at localhost too (add the URI); if no, Google sign-in
   works only at the public address, as 17.4 assumes.
5. The Google button: Google's branding guidelines with the app's sans font and the bundled "G" SVG (shown in the
   mockups), or Google Sans Medium from the pre-approved asset if the guidelines or the font licence require it
   (research 6.7).
6. Resend: the `Resend-Idempotency-Key` header de-duplicates a retried SMTP message; the free tier adds no branding;
   the webhook's payload (which field marks a permanent bounce, the `to` list, the Svix headers); a bounce to a typo
   address arrives as `email.bounced` and lands in Resend's suppression list.
7. Google's step-up: whether `max_age=0` forces a fresh sign-in and the ID token then carries `auth_time`; whether
   `login_hint` accepts a `sub`. Without `auth_time`, the step-up still proves a live Google sign-in for the linked
   account in that browser.
8. In-app browsers: Google sign-up from Instagram, LinkedIn and WhatsApp on Android and iOS; whether `inAppBrowser()`
   catches each, and what WhatsApp's browser does.
9. Through the tunnel: `curl -sI https://asknuvexusdoc.com` shows `Strict-Transport-Security`; the `__Host-` session
   cookie is set and sent; `http://` and `www` redirect.

### 18.3 Risks to keep in view

- **Resend's free day is 100 mails.** A bot wave or a busy day uses the `public` share (32 of 80); sign-up then
  answers "We can't send emails right now" (S22) until 00:00 UTC (05:30 IST), while Google sign-up, resets and receipts
  keep working. Move to Resend Pro ($20 a month) before announcing.
- **Bounces pause Resend at 4%**, for the whole account. The suppression list, the automatic pause at 2% and Turnstile
  keep fake addresses down; without the webhook only SMTP refusals are seen. The runbook (17.2) switches to Brevo.
- **Everything still runs on the owner's Mac.** If it sleeps, mails wait in the outbox and go out on waking (reset
  links are minted then, so they are still fresh); tokens in mails sent before a long sleep may expire.
- **Turnstile depends on Cloudflare**, and some privacy blockers block it; those users can still sign up with Google,
  and sign-in never depends on it.
- **Google can rate-limit new sign-ins** for a new project (`rate_limit_exceeded`; research 1.4); an increase can be
  requested.
- **A determined stranger can use up one address's daily sign-up mails** (8.2); that person can still sign up with
  Google.
- **Per-network limits need `NGINX_REAL_IP_FROM`**; without it every visitor shares one address and one visitor's
  failures challenge or block everyone (S40).
