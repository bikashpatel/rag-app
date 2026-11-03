# AskNuvexusDoc

AskNuvexusDoc ([asknuvexusdoc.com](https://asknuvexusdoc.com)) is a self-hosted, single-tenant
Retrieval-Augmented-Generation app: upload documents and chat with them on a prepaid plan, and manage users, plans,
payments, models and usage from an admin page.

Designed and developed by Elarxsys AI Hub Pvt. Ltd. (Intelligent : Innovation : Intellect). © 2026 asknuvexusdoc.com. All rights reserved.

- **Backend:** FastAPI (Python 3.13) · Postgres 17 · Redis 7.4 · Qdrant 1.19 · a separate ingestion worker
- **Frontend:** React 19 + Vite 8 (TypeScript), served by nginx
- **Models:** every model call goes through [LiteLLM](https://docs.litellm.ai/), so
  switching between OpenAI, Anthropic, Gemini, Groq, Ollama and others means adding a
  model in the admin page
- **Everything runs in Docker Compose.** Only the web UI is published, on `127.0.0.1`.

`SPEC.md` is the binding contract (API, data model, SSE events, layout).
`docs/HANDOFF.md` has the project status, the decisions made during the build, and the
known limitations.

## Architecture

```
browser ──> nginx (frontend container, 127.0.0.1:8080)
              ├── /         static React build
              └── /api/* ─> FastAPI backend ──> Postgres  users, documents, chats, usage
                               │           ├──> Redis     ingest queue, login/rate limits, answer slots
                               │           ├──> Qdrant    one vector collection per embedding model
                               │           ├──> Google    sign-in (OpenID Connect); OAuth 2.0 + Gmail REST API (Gmail plugin)
                               │           ├──> Razorpay  Payment Links, payments, refunds, disputes (REST, httpx)
                               │           ├──> Turnstile Cloudflare's robot check (sign-up, resend, forgot password, sign-in)
                               │           ├──> HIBP      range check of new passwords (5 characters of the SHA-1)
                               │           └──> LiteLLM ─> OpenAI / Anthropic / Ollama / …
                               │           └──> file render child (create_document: PDF/DOCX/XLSX/MD;
                               │                                   create_image: validate and re-encode images)
                               └── uploads volume (shared)
              worker (same image) <── Redis list rag:ingest_queue
                 parse ─> [PDF pages: render (child process) ─> vision model (LiteLLM)] ─> chunk ─> embed (LiteLLM) ─> Qdrant
                 billing loop: open checkouts, refunds and webhook events ─> Razorpay; plan reminders queued as emails;
                               direct-UPI requests without a UTR expired after 24 h
                 mail loop: email_outbox (Postgres) ─> SMTP (Resend): verification, resets, notices, receipts,
                            reminders, direct-UPI alerts to the owner and rejections
              monitor (same image)  checks every service, the app through nginx, backups and disks ─> alert channels
              backup (postgres image)  daily: pg_dump + uploads + Qdrant snapshots ─> ./backups (BACKUP_DIR)
```

- **Upload:** nginx checks the session and the plan, then streams the file to the backend, which validates it,
  stores it in the `uploads` volume, creates the document row (`pending`) and pushes its id onto
  the Redis queue. The worker parses it (in a thread), chunks it, embeds it with the active
  embedding model, writes the vectors to Qdrant and marks the document `ready` (or `failed` with
  a readable error). For PDFs, pages without a text layer (scans) and pages with large images are
  rendered to JPEG in a memory- and time-limited child process and read by the active vision model,
  which transcribes scans and describes charts, diagrams and photos; that text is indexed and cited by
  page like the rest.
- **Chat:** `POST /api/chat/stream` checks the plan, the rate limit, the chat allowance and model access, saves the
  user message, retrieves passages from the user's private documents plus the shared knowledge
  base (every Qdrant search carries this access filter), and streams the answer as Server-Sent
  Events (`meta`, `sources`, `warning`, `delta`, `file`, `done` or `error`, plus `: keepalive` comments
  during silences). The answer and its token usage are saved at the end, including partial answers after
  Stop or an error.
- **Files from chat:** chat models with function calling get a `create_document` tool when the user's plan includes
  files. When the user
  asks for a PDF, Word, Excel or Markdown file, the backend validates the tool call, renders the file in a
  time- and memory-limited child process (fpdf2 with bundled Noto fonts for PDFs, python-docx, openpyxl),
  stores it as one of the user's private documents (quotas apply), queues it for indexing like an upload,
  and calls the model again with the result. The answer shows a file card with a Download button.
- **Images from chat:** in the same loop, chat models with function calling get a `create_image` tool while an image
  model is active and the user's plan includes images. When the user asks for an image, the backend counts it against
  the user's images per period,
  generates it with the active image model (an OpenAI GPT image model through LiteLLM), validates and re-encodes the
  bytes in the same child process, stores the image as one of the user's private documents (indexed by its prompt
  and description), and calls the model again with the result. The answer shows the image; `create_document` can
  embed it in PDF and Word files.
- **Plugins (Gmail):** a small plugin framework (`app/plugins/`) with Gmail as the first plugin. The admin turns a
  plugin on, and each user connects their own account with OAuth 2.0. Tokens are stored encrypted, and nobody sees or
  uses another user's connection. Connected users whose plan includes Gmail get Gmail tools in the same chat loop:
  search and read with emails cited as sources, import into their documents, drafts, and proposed emails that are sent
  only when the user clicks **Send**. A separate model call (the "request check") confirms that the user asked for each
  draft, proposal or import in their own words. Setup: [`docs/gmail-setup.md`](docs/gmail-setup.md).
- **Accounts:** one admin account, defined in `.env`. Other users are created by the admin, or sign up themselves
  (email and password with a verification email, or Google) once the admin opens sign-up (section 10).
- **Plans and payments:** a user's access, features and limits come from their plan (seeded: Starter, Standard,
  Professional; edited in Admin → Plans), held as prepaid 30-day periods paid through Razorpay Payment Links or by
  direct UPI to the owner's UPI ID (a QR code rendered on the server, the customer's UTR, the admin's confirm), granted
  free by the admin, or paid by a bank transfer the admin records. The backend enforces it on every route
  (`billing.entitlements`); the admin needs no plan (section 9).
- **Operations:** the backend migrates the database with Alembic when it starts, the `backup` service backs up daily
  with a verified, tested restore, and the `monitor` service alerts when something breaks (section 8).
- **Account and Settings:** each user's preferences (theme, accent, chat font size, default model, Enter to send) are
  saved in their account (`/api/me/preferences`). A small script, `theme-boot.js`, applies the cached theme before the
  first paint, within the strict CSP. The app also keeps a short sign-in history per user and offers this period's
  usage, a ZIP export of the user's own data and "delete all my chats" (`/api/me/*`, `DELETE /api/conversations`).
- **Interface:** a top bar over the chat (model, Plugins, Documents and avatar menus), a "+" menu in the composer, a
  Settings dialog, the plan, checkout and public pages, and the admin page, in a warm editorial design ("Paper & Clay":
  serif answers, light and dark, five accent colours). The fonts are Latin subsets of Source Serif 4 and Source Sans 3,
  bundled with their licences in `frontend/src/assets/fonts/`; nothing is loaded from a CDN.

Code layout:

| Path | What |
|---|---|
| `backend/app/main.py`, `config.py`, `db.py`, `models.py`, `errors.py` | app factory, settings, async SQLAlchemy (and the migrations run at start-up), ORM, errors with a machine-readable `code` |
| `backend/migrations/`, `backend/alembic.ini` | Alembic migrations ([README](backend/migrations/README.md)) |
| `backend/app/routers/` | `auth` (sign-in, the sign-up configuration), `auth_signup` (email sign-up and password reset), `auth_google` (Google sign-in), `auth_email` (confirming and undoing an email), `admin_auth` (Admin → Users → Sign-up and the test email), `mail_events` (Resend's bounce and complaint webhook), `me` (the caller's preferences, account, email, password, Google sign-in, usage and export), `models`, `documents`, `conversations`, `chat`, `admin`, `plugins`, `admin_plugins`, `billing` (plans, checkout, the direct-UPI checkout, UTR and QR codes, orders, payments, receipts, the Razorpay return and webhook), `admin_billing` (Admin → Plans and Billing, the direct-UPI confirm and reject), `public` (the public pricing and site details), `system` (readiness and Admin → System) (all under `/api`) |
| `backend/app/services/billing/` | plans and payments: `entitlements` (what a user may do), `periods`, `plans`, `checkout`, `payments` (the exactly-once apply), `upi` (direct UPI: the request, its QR codes, the UTR, the admin's confirm and reject, expiry), `razorpay` (the REST client), `webhooks`, `reconcile` (the worker's billing loop), `refunds`, `disputes`, `receipts` (PDF), `reminders`, `summary`, `csv_export`, `business` (billing settings), `paid` (whether a user has paid), `money`, `locks`, `audit` |
| `backend/app/services/auth/` | sign-up and sign-in: `signup`, `login`, `google` (OpenID Connect), `identities`, `reset`, `email_change`, `undo`, `stepup`, `passwords` (policy, blocklist in `app/data/`, breach check), `tokens`, `throttle`, `turnstile`, `emails`, `usernames`, `cookies`, `signup_settings`, `audit` |
| `backend/app/services/mail/` | email to users: `outbox` (the queue, classes and daily cap), `drain` (the worker's mail loop), `jobs`, `templates`, `transport` (SMTP), `webhook` (bounces and complaints), `sweeps` (the hourly clean-up) |
| `backend/app/ops/` | operations: `checks` (every health check), `monitor` (the monitor service), `alerts` + `channels` (alert states, webhook, ntfy, email), `heartbeat` (the worker's), `backup_status`, `reconcile` (after a restore), `settings` |
| `backend/app/services/` | `llm` (LiteLLM), `rag`, `parsing`, `pdf_images` + `pdf_render_child` (PDF image measuring and rendering), `vision` (vision model reading), `document_tool` (the chat's `create_document` tool) + `filegen/` (PDF, DOCX, XLSX and MD rendering and image validation, run by `filegen/child.py`), `image_tool` (the chat's `create_image` tool and the image count per period), `account` (preferences, sign-in history, the user's usage report), `export` (the data export ZIP), `chunking`, `ingestion`, `vectorstore`, `usage`, `storage` |
| `backend/app/plugins/` | plugin framework: `base` (the `Plugin` interface), `registry`, `oauth` + `connections` + `crypto` (OAuth, token refresh, encryption), `intent` (the request check), `answer`, `importing`, `audit`, `tasks`, `http`; `gmail/` is the Gmail plugin (`plugin`, `client`, `mime`, `tools`, `sends`, `importer`, `render`, `router`) |
| `backend/app/fonts/` | Noto Sans fonts for generated PDFs (SIL Open Font License, `OFL.txt`) |
| `backend/app/worker.py` | ingestion worker (`python -m app.worker`) |
| `backend/tests/` | in-process API tests, no Docker needed ([README](backend/tests/README.md)) |
| `frontend/src/pages/` | `LoginPage` (+ `login.css`), `SignupPage`, `VerifyEmailPage`, `GoogleSignupPage`, `ForgotPasswordPage`, `ResetPasswordPage`, `ConfirmEmailPage`, `UndoEmailPage` (+ `auth.css`), `ChatPage`, `AdminPage`, `PlansPage`, `CheckoutPage`, `BillingReturnPage` (+ `plans.css`), `public/` (the pricing and policy pages) |
| `frontend/src/components/` | `auth/` (the sign-up and sign-in parts: Turnstile, Google button, password rules, terms), `chat/` (top bar, model, Plugins, avatar and "+" menus, thread, composer; `chat/plugins/`: Gmail cards), `documents/` (right-hand panel), `settings/` (the Settings dialog and its tabs), `connections/` (plugin cards, Gmail browser), `billing/` (plan cards, compare table, banners, locked items, the plan-ended panel, the direct-UPI payment page), `admin/`, `ui/` (shared primitives: Modal, Menu, Meter, Switch, SegmentedControl, PasswordInput, icons, …) |
| `frontend/public/legal/` | the public policy pages' text as Markdown (drafts to replace before going live, section 9) |
| `frontend/src/styles/`, `src/assets/fonts/` | the design system: `global.css` (tokens for light, dark, five accents and three chat font sizes, plus shared primitives), `fonts.css` and the bundled fonts with their licences |
| `frontend/public/theme-boot.js`, `src/theme/appearance.ts` | the theme engine: applies the cached appearance before the first paint, then follows the account's preferences and the OS (System) |
| `frontend/nginx.conf`, `upload-limit.envsh` | nginx template (security headers, body limits, upload auth check) |
| `frontend/src/**/*.test.ts(x)`, `src/test/` | Vitest tests next to the code, and their shared helpers ([README](frontend/src/test/README.md)) |
| `e2e/` | Playwright browser tests against the running stack ([README](e2e/README.md)) |
| `integration/` | full-stack tests on an isolated Compose project with fake model, Google, Razorpay, SMTP and Turnstile servers ([README](integration/README.md)) |
| `.github/workflows/ci.yml` | CI: every suite on GitHub Actions (section 7) |
| `scripts/` | `backup.sh`, `restore.sh`, the backup service's steps (`backup/`) and the optional host watchdog (`watchdog/`) ([README](scripts/README.md)) |

## 1. Prerequisites

- **Docker Desktop** (or another Docker engine with Compose v2+). Tested on Docker Desktop for
  macOS on Apple Silicon. Every image is multi-arch, so Rosetta isn't needed.
  If `docker` isn't found on macOS, the CLI is probably in `~/.docker/bin`:
  ```bash
  export PATH="$HOME/.docker/bin:$PATH"     # add it to ~/.zshrc to make it permanent
  ```
- **Provider keys:** an OpenAI key (the default embedding model is OpenAI's) and/or an Anthropic key.
- For development and tests only: Python 3.13 (or [uv](https://docs.astral.sh/uv/)), Node 22+.

## 2. Configure

```bash
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"    # paste the output as SECRET_KEY
```

Edit `.env`:

| Setting | What to put |
|---|---|
| `SECRET_KEY` | a long random string (at least 32 characters), e.g. from the command above |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | your admin login (username 3 to 64 letters, digits, `_`, `.` or `-`; password at least 8 characters, and at least 15 before sign-up opens: a shorter or common one, or one with the app's name or the username, logs a warning at start-up) |
| `POSTGRES_PASSWORD` | any strong password |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | your provider keys |

Notes:

- Compose expands `$` in `.env`. Wrap a value that contains `$` in single quotes
  (`ADMIN_PASSWORD='my$ecret'`); double quotes don't help.
- `POSTGRES_*` are applied only when the database volume is first created. To change them later,
  change them inside Postgres too, or reset all data with `docker compose down -v`.
- Every other setting (the upload cap, retrieval tuning, login throttle, parsing limits, …) is
  listed with its default in `.env.example`, in the "Advanced" section.
- **Usage limits come from plans** (Admin → Plans, with per-user overrides in Admin → Users; section 9). The old
  settings `DEFAULT_MONTHLY_TOKEN_LIMIT`, `DEFAULT_RATE_LIMIT_PER_MIN`, `DEFAULT_MAX_UPLOAD_MB`,
  `DEFAULT_MAX_DOCUMENTS`, `DEFAULT_MAX_STORAGE_MB`, `DEFAULT_MONTHLY_IMAGE_LIMIT` and `VISION_MONTHLY_TOKEN_LIMIT` are
  gone: a value still in `.env` is ignored, with one warning per setting at start-up. `MAX_UPLOAD_MB_HARD` (100) stays
  and caps every plan and override.
- **Payments** (`RAZORPAY_*`, `BILLING_*` and `CHECKOUT_UNPAID_PER_HOUR`, the "Payments" block of `.env.example`) are
  optional. Without Razorpay keys users see "Paid plans open soon. Check back in a few days." (testers and the admin:
  "Online payment isn't set up yet.") and get access only from a plan the admin grants or a bank transfer the admin
  records, or by direct UPI once the admin turns it on. Direct UPI has no `.env` setting: its switch, UPI ID and payee
  name are saved in Admin → Billing → Business & payments. Section 9 has the setup and the settings.
- **Sign-up and email** (the "Accounts and sign-up" and "Email" blocks of `.env.example`; table below) are optional.
  Without them sign-up stays closed, sign-in by username works as before, and no email is sent (receipts and plan
  reminders included). Section 10 has the setup.
- Compose passes the whole `.env` to the backend and worker. The frontend (nginx) gets only
  `MAX_UPLOAD_MB_HARD`.
- Anthropic has no embeddings API, so embedding needs the OpenAI key unless you switch to a local
  embedding model (see "Local models with Ollama").
- Models are seeded on the **first** start only. A seeded model is enabled only if its provider
  key was set at that moment; if you add a key later, enable the models in Admin → Models. The
  exceptions are the vision and image models. An existing database gets "Claude Sonnet 5.5 (vision)" (or "GPT-5.4
  mini (vision)" when only the OpenAI key is set) once, on its next start, enabled and active if the key is set,
  so from then on PDF page images go to that provider without any admin action. In the same way it gets the image
  model "GPT Image 2" (`openai/gpt-image-2`) once, enabled and active if `OPENAI_API_KEY` is set. A vision or image
  model you delete isn't added again. The three plans are seeded once too, when there are none (section 9).
- **Vision model for PDFs** (`VISION_*` in `.env.example`): at most `VISION_MAX_PAGES` (50, at most 200)
  pages per document are read with the vision model, full-page scans first (a PDF with more of them fails
  with a message; other pages over the cap keep only their text layer), with `VISION_CONCURRENCY` (3) calls
  at a time per document and `VISION_MAX_IN_FLIGHT` (4) in the worker, and no new call after
  `VISION_TIMEOUT_SECONDS` (1800). Each user's plan sets their vision tokens per period (section 9; the admin is
  exempt). Pages are rendered with a long edge of `VISION_MAX_IMAGE_PX`
  (1568, at most 2576) pixels, and `VISION_MAX_OUTPUT_TOKENS` (8192) caps each page's output when the model's
  "Max output tokens" is blank. `VISION_MIN_TEXT_CHARS` (40) and `VISION_MIN_IMAGE_RATIO` (0.1) decide which
  pages count as scanned and which images are large enough to describe.
- **Files from chat** (`CHAT_FILE_TOOLS` and `GENERATED_FILE*` in `.env.example`): `CHAT_FILE_TOOLS=false`
  turns the feature off. One answer may create `GENERATED_FILES_PER_ANSWER` (3) files, each with at most
  `GENERATED_FILE_MAX_CHARS` (100,000) characters of content; model calls that may write a file request
  `GENERATED_FILE_MAX_OUTPUT_TOKENS` (48,000) output tokens unless the model has its own "Max output tokens".
  Each file gets `GENERATED_FILE_TIMEOUT_SECONDS` (60) to render, waiting for a render slot included, and
  `GENERATED_FILE_CONCURRENCY` (2) files render at once per backend process.
  - Offering the tool adds about 1,550 input tokens to every model call of a capable chat model, whether or not
    a file is requested (measured: about $0.006 per Claude Opus 5.5 call).
  - Generated files count toward the user's document, storage and per-file limits (the admin is exempt). A user
    with no room isn't offered the tool, and the assistant says so. Neither is a user whose plan doesn't include files
    from chat (Starter, as seeded).
- **Images from chat** (`CHAT_IMAGE_TOOLS`, `GENERATED_IMAGES_PER_ANSWER` and `IMAGE_*` in `.env.example`):
  `CHAT_IMAGE_TOOLS=false` turns image generation off and keeps existing images (disabling the active image model in
  the admin page does the same).
  - One answer may generate `GENERATED_IMAGES_PER_ANSWER` (3) images.
  - The images a user may generate per period come from their plan (as seeded, only Professional includes images: 60
    per period), with an optional per-user override.
  - `IMAGE_MAX_QUALITY` (medium) caps the quality, and with it the cost.
  - `IMAGE_OUTPUT_FORMAT` (png) is the format asked of the image model and stored. A PNG measured about 0.7 MB for a
    square low-quality image and 2.7 MB for a landscape medium one; `jpeg` and `webp` are much smaller.
  - An image may take `IMAGE_TIMEOUT_SECONDS` (180), waiting for a slot included, and `IMAGE_CONCURRENCY` (3) images
    are generated at once per backend process.
  - Measured with GPT Image 2: about $0.006 for a square low-quality image and $0.042 for a landscape medium one.
    Offering the image tool adds about 1,040 input tokens to every model call of a capable chat model, whether or not
    an image is requested.
- **Gmail** (the Plugins block in `.env.example`; step by step in [`docs/gmail-setup.md`](docs/gmail-setup.md)): Gmail
  stays "not configured" until `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` (a Google "Web application" OAuth
  client) and `TOKEN_ENCRYPTION_KEY` are set, and stays off until the admin turns it on in Admin → Plugins. Only users
  whose plan includes Gmail (Professional, as seeded) can use it.
  - `PUBLIC_BASE_URLS` (`http://localhost:8080`) lists the addresses users connect from. Each one also needs
    `<address>/api/plugins/gmail/oauth/callback` registered in Google.
  - `GOOGLE_OAUTH_TESTING_MODE` (true) matches the Google app's "Testing" status, where Google ends connections after
    7 days.
  - `GMAIL_*` are the limits: per answer, 5 Gmail tool calls and 40,000 characters of email; per user, 20 sends per UTC
    day and 30 minutes to click Send; per email, 15 MB of attachments; per import, 20 items.
  - A connected user with "Use in chat" on adds about 1,900 input tokens to every model call of a tool-capable chat
    model (measured: 4,882 instead of 3,010 for the same Claude Haiku 5.5 "pong").
- **Data export** (the Account block in `.env.example`): one "Export my data" ZIP holds at most `EXPORT_MAX_MB` (100)
  of uncompressed text and is built within `EXPORT_TIMEOUT_SECONDS` (80, at most 90, because nothing is sent before
  the build ends and the Cloudflare quick tunnel drops a response that stays silent for about 100 s). Older
  conversations beyond either bound are left out, and the export says how many.
- `WORKER_MEM_LIMIT` (default `4g`) is the worker container's memory limit (Compose `mem_limit` and
  `memswap_limit`); PDF pages are rendered in child processes limited to 1 GiB each.
- **Backups and alerts** (`BACKUP_*`, `ALERT_*`, `MONITOR_*` in `.env.example`): section 8 has the table. Backups run
  with the defaults; alerts need one channel set (a webhook, ntfy or email), or they are only logged and shown in
  Admin → System. Keep a copy of `.env` itself in a password manager: it isn't in any backup.

**Sign-up and email settings** (a value outside its range stops the backend at start-up; apply a change with `docker
compose up -d backend worker monitor`, then `docker compose up -d --force-recreate frontend`):

| Setting (default) | What it does |
|---|---|
| `MAIL_SMTP_HOST` (empty) | with `MAIL_FROM`: the mailer is configured. Resend: `smtp.resend.com` |
| `MAIL_SMTP_PORT` (465; 1 to 65535), `MAIL_SMTP_SECURITY` (`ssl`; `starttls`, `none`) | `none` (no TLS) only for a local relay; refused while `PUBLIC_BASE_URLS` holds a public https origin |
| `MAIL_SMTP_USERNAME`, `MAIL_SMTP_PASSWORD` (empty) | Resend: `resend` and a sending-only API key. Never logged or shown |
| `MAIL_FROM` (empty) | `Name <address>` or an address: the From of every email to users, e.g. `AskNuvexusDoc <no-reply@asknuvexusdoc.com>` |
| `MAIL_REPLY_TO` (empty) | an address; every mail then also says "Questions? Reply to this email." |
| `MAIL_DAILY_LIMIT` (80; 1 to 100,000) | mails per UTC day, below the provider's cap, shared by class (section 10) |
| `MAIL_IDEMPOTENCY_HEADER` (`Resend-Idempotency-Key`; a header name or empty) | a unique key on every message, so the provider drops a retried duplicate; empty for Brevo |
| `MAIL_WEBHOOK_SECRET` (empty) | `whsec_…`, the signing secret of Resend's bounce and complaint webhook; empty: `/api/mail/resend-events` answers 404 |
| `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY` (empty) | both or neither: Turnstile configured. Cloudflare's test keys (`1x…`) are refused with a public https origin |
| `GOOGLE_SIGNIN_CLIENT_ID`, `GOOGLE_SIGNIN_CLIENT_SECRET` (empty) | both or neither: "Continue with Google" configured. A client of its own Google project, not Gmail's `GOOGLE_OAUTH_*` |
| `PASSWORD_MIN_LENGTH` (8; 8 to 64) | the shortest new password (at most 256 characters; sign-in accepts up to 1,024) |
| `PASSWORD_BREACH_CHECK` (true) | Have I Been Pwned's range check of new passwords; an outage lets the password through |
| `TERMS_VERSION` (`2026-11-01`; 1 to 32 of `A-Za-z0-9._-`) | recorded with each sign-up's acceptance of the terms; change it with the terms |
| `LOGIN_CHALLENGE_AFTER` (3; 1 to `LOGIN_MAX_ATTEMPTS`), `LOGIN_CHALLENGE_AFTER_PER_IP` (10; 1 to `LOGIN_MAX_ATTEMPTS_PER_IP`) | with Turnstile, failed sign-ins for one account from one network, or of one network, before the robot check comes first |
| `LOGIN_SITE_CHALLENGE_AFTER` (50; 10 to 10,000) | failed sign-ins across the site in 5 minutes that turn the robot check on for everyone |
| `ADMIN_SIGNIN_FROM` (`any`; `local`) | `local`: the admin from `.env` signs in only from this machine (`http://localhost:8080`) |
| `TURNSTILE_SITEVERIFY_URL`, `GOOGLE_SIGNIN_AUTHORIZE_URL`, `GOOGLE_SIGNIN_TOKEN_URL`, `GOOGLE_SIGNIN_JWKS_URL`, `PASSWORD_BREACH_CHECK_URL` (the real services) | other values only for fakes in tests; refused with a public https origin |

`BILLING_EMAIL_FROM` is removed: receipts and plan reminders use `MAIL_FROM` and the `MAIL_SMTP_*` settings, and a value
still in `.env` is ignored with a warning. `COOKIE_SECURE` (true on the public site) now also gives the cookies their
`__Host-` and `__Secure-` prefixes, and `NGINX_REAL_IP_FROM` is required before sign-up opens (section 10).

## 3. Run

```bash
docker compose up -d --build
docker compose ps                  # wait until every service reports "healthy"
```

Open **http://localhost:8080** (or your `APP_PORT`) and sign in with the admin credentials from `.env`.

Useful commands:

```bash
docker compose logs -f backend     # API logs
docker compose logs -f worker      # document processing logs
docker compose restart backend worker
docker compose down                # stop (data is kept in Docker volumes)
docker compose down -v             # stop AND delete all data (backups in ./backups stay)
```

- After editing `.env`, run `docker compose up -d` (it recreates the containers whose settings changed).
- Before deploying new code, back up (`scripts/backup.sh`; keep the `BACKUP_NAME` it prints): the backend migrates the
  database while it starts, and that backup is the way back (`backend/migrations/README.md`, "On a deploy").
- After changing code, run `APP_VERSION=$(git describe --always --dirty) docker compose up -d --build` (the version
  shows in Admin → System and in backup manifests), then `docker compose up -d --force-recreate frontend`: nginx finds
  a recreated backend only when it restarts.
- Stopping can take a while by design: the backend gives open answers up to about 30 s to save
  their partial text, and the worker gives running jobs up to 60 s to finish.

## 4. Using it

**Plans:** every user except the admin needs a current plan. After sign-in, a user without one lands on the plan page
(`/plans`) to choose and pay; section 9 has what users see and what the admin does.

**Chat page:** conversations on the left, the streaming chat in the middle, and documents on the
right (under 1100 px the conversation list becomes a drawer; under 800 px the document panel
becomes a slide-over). Upload PDF, Word (`.docx`), PowerPoint (`.pptx`), Excel (`.xlsx`), HTML,
Markdown, text, CSV or JSON files by dropping them on the panel or clicking **browse**. Each
file is parsed, chunked and embedded in the background. Answers cite the passages they used as
`[1]`, `[2]`, …, and you can expand the sources under every answer. Use the checkboxes in the
document panel to limit which documents a question searches; the chip in the composer shows the current choice ("All
documents", "3 documents") and opens the panel. **Stop** keeps the partial answer.

- **Top bar** (over the chat): the **model menu** (new chats start with your default model from Settings; a pick applies
  to the current chat until **New chat**, and the menu marks the models that can create files or images), the
  conversation title, **Plugins** (every plugin with its status: Connected, Not connected, Reconnect needed, Turned off
  by the admin or Not set up; each row opens the plugin's card in Settings), **Documents** (opens the panel on narrow
  screens; on wider ones it hides or shows the right-hand column, remembered per browser) and your **avatar menu**:
  your plan ("Starter · until 31 Oct"), this period's chat allowance and images against your limits, **View usage**,
  **Settings**, **Plan & billing**, a System / Light / Dark switch, **Admin** (admins only) and **Log out**.
- **"+" menu** in the composer: **Attach files…** uploads to your private documents; chips above the input show the
  upload and indexing, and Send waits until the files are indexed (remove a chip to send without it). **Create an
  image** and **Create a document** (PDF, Word, Excel, Markdown), and once Gmail is connected **Search my email** and
  **Draft an email**, put a starter such as "Create a PDF document: " in front of your message; nothing is sent until
  you send it. **Import from Gmail…** opens the Gmail browser (before you connect, **Connect Gmail…** opens its
  card). Features the server doesn't offer are left out; features your plan doesn't include show a lock and "Included
  in <plan>" and open the plan page; items you can't use right now are disabled with the reason.
- **Keys:** Enter sends and Shift+Enter adds a line. With "Enter to send" off in Settings, Ctrl+Enter (⌘+Enter on a
  Mac) sends and Enter adds a line.

**Settings** (avatar menu → **Settings**; the URL is `/?settings=<tab>`, and `/settings/<tab>` redirects there): a
dialog, full screen on phones. Every change applies and is saved to your account at once, so it follows you to other
browsers.

- **General:** theme (System, Light, Dark), accent colour (Clay, Ocean, Sage, Plum, Slate), chat font size (Small,
  Default, Large), the default chat model, and Enter to send.
- **Account:** your email (or username), role, when the account was created, **Add email…** or **Change email…**,
  **Change password…** (or **Set a password** for an account made with Google), the Google sign-in (**Connect
  Google…**, **Disconnect…**), the last 20 sign-ins (approximate address, browser, OS and device, with password or with
  Google; "This device" marks yours) and **Log out of all other sessions**, which signs out every other browser and API
  token and keeps this one. The admin, whose password lives in `.env`, has no email, password change or Google sign-in
  here (section 10).
- **Plan & billing:** your plan and its dates, what comes next, receipt details, the switches **Email me a receipt for
  each payment** and **Email me before my plan ends** (to your confirmed email), and your payments with their receipts
  (section 9).
- **Usage:** this period's chat allowance (counted at each model's rate), images and vision tokens against your plan's
  limits, the estimated cost, documents and storage, the requests-per-minute limit, when the period ends (an
  open-ended complimentary plan counts per UTC calendar month), a daily chart with a table, and totals per model.
- **Plugins & data:** the plugin cards (see Gmail below), **Export my data** (a ZIP with your account, the details of
  your documents, your conversations as Markdown and JSON, newest first within the limits in section 2, and your billing
  records; document files aren't included, use Download in the panel) and **Delete all chats** (type `DELETE` to
  confirm; your documents, the files and images created in chat and your usage history stay). Without a plan, "Your
  data" lists your documents (Download, Delete) and your chats (Delete).

**Files from chat:** ask for a file in your own words, for example "make a PDF report of the Q3 figures from
my documents", "put these numbers in a spreadsheet", "draft a Word letter" or "export this as markdown". The
assistant creates PDF, Word (`.docx`), Excel (`.xlsx`) and Markdown (`.md`) files (on a plan that includes files from
chat, with chat models that support function calling; the hint under the composer says when the selected model can). A
card under the answer shows the file being written, then its name, type, size and indexing status, with a **Download**
button.
Every file is saved to your private documents ("Created in chat" in the document panel), counts toward your
document and storage limits, and is indexed, so later chats can search it; passages from such files are
marked as earlier answers rather than sources. When the content uses your documents, the file ends with a
Sources section. Every document in the panel, uploads included, has a Download button.

- **What each format holds:**
  - PDF and Word: headings, paragraphs, bold and italic, lists, tables, code blocks and links, plus images generated
    earlier in the same conversation (see below). PDFs also get page numbers.
  - Excel: one or more sheets with a bold, frozen header row and an auto-filter. Numbers and dates are stored as
    real values, and text that looks like a formula stays text.
  - Markdown: the same content as a `.md` file that loads nothing (images become text, HTML is escaped).
- **Limits (defaults):** up to 3 files per answer, at most 100,000 characters each.
- **When it fails:** a file that fails (for example, over your document limit) shows a card with the reason, and
  the assistant explains it.
- **Stop:** pressing Stop while a file is being written cancels it; if it was already being saved, it is still in
  your documents.

**Images from chat:** ask for an image in your own words, for example "generate an image of a fox in the snow",
"draw a watercolor illustration of a lighthouse at dawn" or "make a logo concept for a bakery". This works on a plan
that includes images, with chat models that support function calling, while the admin has an image model active; the
composer hint mentions images when the selected model can. The assistant writes a full prompt for the image model and
picks square, landscape or portrait.

- **In the answer:** while the image is made (10 to 40 s in the live tests), a placeholder with the image's shape
  counts the seconds. Then the image appears under the answer with its name, type, size and Download. Click it for a
  full-size viewer that shows the description, size and quality, the prompt (under "Prompt") and Download; Escape
  closes it.
- **In your documents:** every image is also saved to your private documents, with a thumbnail and "Created in chat".
  It counts toward your document and storage limits, but a finished image is always saved, even when that takes you
  over a limit. It is indexed by its title, description and prompt, so later chats can find it; such passages are
  marked as images the assistant generated, not as facts.
- **In files:** ask for a PDF or Word file "with the logo" and the image is placed in it, scaled to the page width,
  with a caption. This works for images generated earlier in the same conversation. Excel and Markdown files get a
  text reference instead.
- **Limits (defaults):** up to 3 images per answer, and the plan's images per period (Professional: 60) that the avatar
  menu shows as "Images this period" (also in Settings → Usage). The admin may override a user's count; the admin has
  no limit. Failed attempts that the provider may have charged for count too.
- **When it fails:** a refused prompt (the provider's safety system), a busy provider or a timeout shows a card with
  the reason, and the assistant explains it. After any failure other than a refusal, that answer makes no more
  images; ask again in a new message.
- **Stop:** pressing Stop before the image model was called cancels the image. Once it is being generated, only the
  text stops: the image is still finished, saved to your documents and counted, and it appears on the answer after
  a reload.

**Gmail** (once the admin has set it up and turned it on, on a plan that includes Gmail; see
[`docs/gmail-setup.md`](docs/gmail-setup.md)): open
**Plugins** in the top bar and pick Gmail (or go to Settings → **Plugins & data**), click **Connect Gmail** and sign
in at Google. Each user connects their own account; nobody else can see or use it. Then:

- **Ask about your email** in chat ("what did Alice say about the invoice?"). The assistant searches and reads your
  mail with Gmail's search syntax and cites emails as `[n]` sources with an **Open in Gmail** link. This works with
  chat models that support function calling while **Use in chat** is on in Gmail's card (Settings → Plugins & data).
- **Import** threads and their supported attachments into your private documents: **Import from Gmail** in the
  document panel or the "+" menu (search, open a thread, tick the conversation and attachments, Import), or ask in
  chat. Imports are marked "Imported from Gmail", count toward your limits, and an unchanged thread imported again
  links the same document.
- **Drafts:** "draft a reply to Bob saying we'll pay on Friday" creates a draft in your Gmail (threaded when it is a
  reply) and shows a card with **Open in Gmail**. Nothing is sent.
- **Sending:** the assistant can only *propose* an email. The card shows the exact recipients, subject, body and
  attachments with **Send**, **Edit** and **Cancel**. Only your click sends it, within 30 minutes and up to 20 emails a
  day. The card flags recipients you never typed, attachments you never named, any Bcc, and which email it replies to.
- The assistant drafts, proposes or imports only when your own message asks for it, never because an email says so.
  In a conversation that has touched email, a bare "yes" doesn't count; say what you want ("yes, draft the reply to
  Alice").
- **Disconnect** on Gmail's card removes the app's access (also at Google, unless another app user connected the same
  Google account). While the Google app is in testing mode, Google ends connections after 7 days; the Plugins menu then
  shows "Reconnect needed" and the card offers **Reconnect**.

**Admin page** (`/admin`, admin only; **Admin** in the avatar menu, and its header has Settings and Sign out). An
overview at the top shows users, documents, failed and stale documents, the ingestion queue and the active embedding,
vision and image models ("Off" when no vision or image model is active), and offers a re-index when documents are
stale or failed. The tabs are Users, Plans, Billing, Models, Usage, Documents, Plugins and System (section 8).

- **Users:** the **Sign-up** card (open or close sign-up and its methods, the day's email, the checks and a test
  email; section 10); create users (optionally with a complimentary plan), reset passwords, deactivate, delete. The
  list has a search box, each user's email (or "Pending: …"), how they signed up, a Google mark and a "Signed up"
  column with a "Last 7 days" filter, and shows each user's plan ("Standard · until 25 Nov", "Complimentary
  Professional", "No plan") with filters (All, Active, Expiring, Lapsed, Complimentary, No plan), and this period's chat
  allowance and images. A user's form has a **Plan** section (grants, plan changes, revokes, payments, billing events;
  section 9) and the switch **Can make test payments**. Limits come from the user's plan; each can be overridden for one
  user (blank = the plan's value, `0` = unlimited, except for the max file size), and "Above plan" flags an override
  over the plan's:
  - chat allowance per period (standard tokens; embedding and vision tokens are shown but don't count); `0` asks to
    confirm, since the user then has no cost ceiling
  - chat requests per minute
  - which chat models they may use (this only narrows the plan's models)
  - max file size, max number of documents, max total storage
  - images per period and vision tokens per period
- **Plans** and **Billing:** the plan editor, payments, refunds, orders, bank transfers and the business details
  (section 9).
- **Models:** four sections, Chat, Embedding, Vision and Image. Add, edit, enable/disable, choose the default
  chat model and the active embedding, vision and image models, and **Test** the connection (for a vision model,
  it sends a small generated image and checks the model reads it; for an image model, it generates one small
  low-quality image, about $0.006, shows its thumbnail and records it as the admin's image usage).
  - The **active vision model** reads PDF pages that have no text layer (scans: it transcribes them) and
    describes images such as charts, diagrams and photos on pages with text. The page images are sent to
    the model's provider (a cloud provider, unless you add a local model, e.g. through Ollama), and every
    page read costs tokens. Disabling the active vision model (or deleting it) turns this off; scanned PDFs
    then fail with a message saying so. Charts drawn as vector graphics aren't detected, only raster images.
  - Pages read once are cached per document, so Retry, reprocess and re-indexing don't pay for them again
    (unless the vision model or its settings changed).
  - Changing, disabling or deleting the vision model while a PDF is being read puts that PDF back in the queue,
    so it is read again with the model that is active then. Deleting or reprocessing a document stops its
    reading run.
  - The **active image model** generates the images users ask for in chat. Prompts go to its provider (OpenAI or
    Azure), whichever provider the chat model is from. Disabling or deleting it turns image generation off; images
    already made stay. If the model returns unusable data (no image data, or not a PNG, JPEG or WebP image), image
    generation pauses until the model is edited or its **Test** succeeds.
- **Usage:** chat, embedding and vision tokens, generated images and the estimated cost per user, per model and
  per day, for any calendar month. Only chat answers count toward a user's chat allowance (at each model's rate);
  vision tokens and images have their own allowances per period, from the plan.
- **Documents:** every document (uploads, and files and images created in chat), with re-index and delete actions,
  and **Re-index all documents**. Users' generated images show only their type tag here; their prompts and the
  images themselves stay private to their owner.
  Documents read with the vision model show how many pages it read ("pages read by vision"). PDFs that
  failed as scanned before the vision model was enabled can be retried (Retry in the document panel, or
  "Retry failed" in the admin overview).
- **Plugins:** each plugin with its configuration status (the `.env` settings still missing), an **Enabled** switch,
  capability switches (Gmail: Search and read, Drafts, Send), the scopes it requests and the redirect URIs to register
  at Google (with Copy), connection counts (connected, reconnect needed, still on an older encryption key), this month's
  activity per action and per user (counts only: no subjects, addresses or account emails), and **Disconnect all
  users**. **Request check → Test** checks that a chat model can run the request check (one short model call).

The admin can upload to the **shared knowledge base** (toggle in the document panel). Shared
documents are searched in every user's chats. Users' own uploads stay private to them. The
admin bypasses all usage limits and needs no plan.

## 5. Switching or adding models (LiteLLM)

In **Admin → Models**, click **Add chat model**, **Add embedding model**, **Add vision model** or **Add image
model** and enter a LiteLLM model string:

| Provider | Example | Key in `.env` |
|---|---|---|
| OpenAI | `openai/gpt-5.4-mini` | `OPENAI_API_KEY` |
| Anthropic | `anthropic/claude-sonnet-5-5` | `ANTHROPIC_API_KEY` |
| Google Gemini | `gemini/gemini-2.5-flash` | `GEMINI_API_KEY` |
| Groq | `groq/llama-3.3-70b-versatile` | `GROQ_API_KEY` |
| Ollama (local) | `ollama/llama3.1` + API base `http://host.docker.internal:11434` | none |

Add the key to `.env`, run `docker compose up -d`, add the model, then click **Test**.

- **API key env var** (optional) names the variable holding the key; it must end in `_API_KEY`.
  If it is left blank, LiteLLM uses the provider's default variable from the table, except for a
  model with an **API base**, which gets no key at all (your OpenAI or Anthropic key is never sent
  to a custom host). If the named variable isn't set, calls fail with a message saying so.
- **Max output tokens:** blank means `DEFAULT_MAX_OUTPUT_TOKENS` (16000), lowered to the model's
  own limit when LiteLLM's model map knows it. For a vision model it is per page, thinking included, and
  blank means `VISION_MAX_OUTPUT_TOKENS` (8192).
- **Temperature:** leave it blank for Claude 5.5 models, which reject a custom temperature.
- **Prices** come from LiteLLM's price list unless you enter your own per-1M-token prices.

**Vision models** must accept images, e.g. `anthropic/claude-sonnet-5-5` (seeded), `openai/gpt-5.4-mini`,
`gemini/gemini-2.5-flash`, or locally `ollama_chat/llama3.2-vision` (only the Anthropic one was tried here).
**Test** sends a small generated image showing "TEST 4712" and passes only if the reply contains 4712. Then
click **Make active** on an enabled model. At most one vision model is active, and it reads PDFs from the next
run on (new uploads, Retry, reprocess). Vision calls ask for low reasoning effort, and vision models are never
offered for chat.

**Image models** must be OpenAI GPT image models (`openai/gpt-image-2`, seeded; also `openai/<name>` with an API base
for an OpenAI-compatible server) or Azure OpenAI deployments of them (`azure/<deployment>`). The server refuses other
providers and dall-e models with a message (422): LiteLLM runs other providers' image calls as blocking requests that
Stop and the time limit can't cancel, and dall-e takes other sizes and qualities. Prices are per 1M prompt text tokens
(input) and image tokens (output); blank uses LiteLLM's price list. Max output tokens and temperature don't apply.
OpenAI may require a verified organization for its image models; the Test then says so. At most one image model is
active, and image models are never offered for chat.

**Changing the embedding model:** vectors from different embedding models can't be mixed. When
you make another embedding model active, documents embedded with the old one become stale; the
admin overview shows how many and offers a re-index. Re-indexing uses embedding tokens, plus vision
tokens for PDF pages not yet read with the active vision model.

### Local models with Ollama

Install [Ollama](https://ollama.com/) on the host (it isn't installed on the current machine).
The containers reach it at `http://host.docker.internal:11434` (Docker Desktop provides that
name). For example, after `ollama pull nomic-embed-text`, add an embedding model
`ollama/nomic-embed-text` with that API base, **Test** it, and make it the default to embed
without any cloud key. This path hasn't been tested on this machine.

## 6. Development

### Tests and checks

The five test suites, the lint and type checks and CI are in [section 7](#7-testing).

### Running the app outside Docker

```bash
# From the repo root. Stop the app containers first, so only one worker consumes the queue.
docker compose stop backend worker frontend
# Infrastructure only, with ports published on 127.0.0.1 (same volumes, so same data, as the full stack)
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d postgres redis qdrant

# Backend: the app reads only environment variables, not .env, so load .env into the shell.
cd backend
set -a; source ../.env; set +a
export POSTGRES_HOST=localhost REDIS_URL=redis://localhost:6379/0 QDRANT_URL=http://localhost:6333 \
       UPLOAD_DIR="$HOME/rag-app-uploads" LITELLM_LOCAL_MODEL_COST_MAP=True
.venv/bin/uvicorn app.main:app --reload --port 8000 --no-access-log

# Worker: a second terminal in backend/, after the same `set -a …` and `export …` lines
.venv/bin/python -m app.worker

# Frontend: a third terminal
cd frontend
npm ci
npm run dev                             # http://localhost:5173, proxies /api to :8000
```

Back to the full stack: stop the local processes, then `docker compose up -d` from the repo root.

- `UPLOAD_DIR` is separate from the Docker `uploads` volume, so files uploaded through the Docker
  stack can't be reprocessed by a local worker. Keep it outside the repo.
- If you `source .env` in a shell, use single quotes for values with `$`; the `$$` form only works for Compose.
- Without nginx, the backend has no 1 MB body limit and reads upload bodies before checking the
  session. Don't expose port 8000.
- To connect Gmail from the Vite dev server, add `http://localhost:5173` to `PUBLIC_BASE_URLS` and register
  `http://localhost:5173/api/plugins/gmail/oauth/callback` in Google (not tried yet).
- FastAPI's interactive docs (`/api/docs`) load Swagger UI from a CDN, which nginx's CSP blocks, so
  they render blank on port 8080; open them on the backend directly (`http://localhost:8000/api/docs`).
  `/api/openapi.json` works on both.

### Dependencies

- **Python:** edit `backend/requirements.in` or `requirements-dev.in`, never the `.txt` files: those
  are hashed locks generated with `uv pip compile` (commands at the top of `requirements.in`). The
  Docker build installs them with `pip install --require-hashes --no-deps` and runs `pip check`.
- **npm:** edit `frontend/package.json`, then regenerate `package-lock.json` with a `--before`
  cutoff (see `docs/briefs/frontend-verification.md`) and run `npm audit`.
- **Images:** base images are pinned by tag and digest in the Dockerfiles and `docker-compose.yml`.
- Every dependency is pinned to a version released at least two weeks before adoption. Never use
  LiteLLM 1.82.7 or 1.82.8 (compromised PyPI releases).

## 7. Testing

Five suites. **None of them calls a real model provider, Google, Razorpay, an email provider, Cloudflare or Have I
Been Pwned** in its default run, so they cost nothing:

| Suite | Where | What it covers | Needs | Size |
|---|---|---|---|---|
| Backend | `backend/tests/` ([README](backend/tests/README.md)) | the whole API in-process: SQLite, fakeredis, in-memory Qdrant, LiteLLM mocked, a fake Google (Gmail and OpenID Connect sign-in), a fake Razorpay, a fake SMTP sink, a fake Turnstile and a fake breach check, the real PDF and file render children; route guards and deletion checks that cover new routes and tables without being edited; the migrations, the monitor, alerts and channels, readiness and Admin → System, and the restore's reconcile step; plans, entitlements on every route, checkout, payments, refunds, disputes, receipts and reminders; direct UPI (the request and its QR codes, the UTR, the admin's confirm and reject, the caps, migration 0005); people's names (the rules, sign-up and Google with a name, `PUT /me/name`, the admin's form, migration 0006); sign-up, sign-in by email, Google sign-in and linking, password reset, email changes and undo, the password policy, throttles, enumeration (the same response for every address), the mail outbox, templates, bounces and the `mail` check | Python 3.13 | 4,004 tests plus the 65 `postgres`-marked ones (skipped without Postgres); coverage 95.85 % (statements and branches) |
| Backend on Postgres | the `postgres`-marked tests in `backend/tests/` (with `TEST_DATABASE=postgres`, all of them) | the migrations (adopting a database made before them, repairs and refusals, the advisory lock across two processes), row locks under parallel uploads, UTC sessions, the image reservation race, the account tests, payments applied and checkouts started at once, receipt numbering, the plans, sign-up, direct-UPI and names migrations, sign-up races (two completions, a completion against a Google finish, two accounts confirming one address), two mail loops never sending one mail twice, two confirms of one UPI payment and two orders sending one UTR at once | a throwaway Postgres 17 (e.g. in Docker) | 65 tests |
| Frontend | `frontend/src/**/*.test.ts(x)` ([README](frontend/src/test/README.md)) | components, hooks, stores and the API client (the SSE parser included) with Vitest, Testing Library and jsdom; `fetch` is stubbed | Node 22+ | 1,520 tests in 102 files; coverage 94.9 % of statements, 90.6 % of branches |
| End-to-end | `e2e/` ([README](e2e/README.md)) | a real Chromium (desktop and a phone) against the running stack: sign-in, sign-up, verification, Google's return, forgot and reset password, the email pages (their endpoints mocked, Turnstile stubbed), top bar, chat streaming and Stop, the composer, documents, Settings, the admin page, plans, checkout and the return page (the payment side intercepted), direct UPI (the payment page, the UTR, "Open UPI app" on a phone, the admin's confirm and reject dialogs; intercepted), phone layout, accessibility | the Docker stack up | 80 tests in 13 files (1 `@live`, skipped) |
| Integration | `integration/` ([README](integration/README.md)) | an isolated copy of the whole stack (nginx, backend, worker, Postgres, Redis, Qdrant, the monitor and the backup service) with a fake OpenAI-compatible model server, the fake Google, a fake alert receiver, a fake SMTP server (`fake_mail`) and a fake Turnstile with the breach check (`fake_turnstile`): ingestion, chat, access, files and images from chat, limits, sessions, the worker, re-indexing, user deletion, export, Gmail, vision, backups and the restore drill, monitoring and alerts, the host watchdog, payments against a fake Razorpay, sign-up, Google sign-in and password reset (`test_18_signup`), the mailer: retries, refusals, the daily cap, receipts, bounces (`test_19_mail`), the web server logs (`test_20_web_logs`), direct UPI from checkout through the UTR, the emails, confirm, reject and a recorded refund (`test_21_upi_direct`) | Docker | 208 tests in 22 files |

```bash
# Backend (from the repo root)
cd backend
uv venv --python 3.13 .venv            # once (uv downloads Python 3.13 if needed)
uv pip sync requirements-dev.txt       # again whenever the locks change
#   or, with python3.13 on PATH: python3.13 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q                              # 8 to 15 minutes (62 skipped without TEST_POSTGRES_URL)
.venv/bin/python -m pytest -q --cov --cov-fail-under=94    # with coverage (.coveragerc); fails under 94 %
uvx ruff@0.13.0 check --target-version py313 --line-length 120 --select E,F,W,I,B --ignore B008 app tests migrations
uvx mypy@1.18.2 --python-executable .venv/bin/python --ignore-missing-imports app migrations

# Backend on Postgres: start a throwaway container first (backend/tests/README.md, "Postgres")
TEST_POSTGRES_URL=postgresql+asyncpg://rag:<password>@127.0.0.1:<port>/scratch .venv/bin/python -m pytest -q -m postgres

# Frontend
cd ../frontend
npm ci                                  # installs exactly package-lock.json, as the Docker build does
npx tsc --noEmit                        # the app and the tests
npm test                                # Vitest, the whole suite once (seconds)
npm run test:coverage                   # fails below the thresholds in vite.config.ts
npx vite build --outDir "$(mktemp -d)/dist"   # or `npm run build` (writes frontend/dist, git-ignored)

# End-to-end, against the running stack (docker compose up -d), http://localhost:8080 by default
cd ../e2e
npm ci && npx playwright install chromium
npm test                                # desktop and phone projects, under a minute
npm run typecheck

# Integration, from the repo root: builds, starts, tests and removes its own stack
cd ..
integration/run.sh                      # about 4 minutes of tests once the images are built (the restore drill included)
```

- **No real calls.** The backend tests mock LiteLLM and serve a fake Google, a fake Razorpay, a fake SMTP sink, a fake
  Turnstile and a fake breach check in-process. The e2e suite answers every chat with canned SSE events, intercepts the
  plan catalog, the checkout and the order, stubs Turnstile's script and mocks the sign-up, reset and email endpoints
  that would verify it or send mail, and blocks requests to other hosts, uploads, plugin connects, the admin's model
  Test and re-indexing; its one real call, the `@live` test, runs only with `E2E_LIVE=1`. The integration stack has
  fake provider keys and Razorpay test keys, points every model at its fake server, Google (Gmail and sign-in) at the
  fake Google, `RAZORPAY_API_BASE` at the fake Razorpay, `MAIL_SMTP_*` at the fake SMTP server
  (`MAIL_SMTP_SECURITY=none`) and `TURNSTILE_SITEVERIFY_URL` and `PASSWORD_BREACH_CHECK_URL` at the fake Turnstile,
  and its backend and worker sit on an internal network with no route to the internet.
- **Docker.** The backend and frontend suites need none. The Postgres tests need a throwaway database: its name must
  contain `test` or `scratch`, and never point `TEST_POSTGRES_URL` at the app's `rag` database. The e2e suite needs
  the stack running. The integration suite runs its own Compose project, `rag-it` (own volumes, a generated throwaway
  env file; it never reads `.env` and never touches `rag-app`), on 127.0.0.1 ports 18090 to 18095 (`IT_APP_PORT`,
  `IT_FAKE_LLM_PORT`, `IT_FAKE_GOOGLE_PORT`, `IT_FAKE_RAZORPAY_PORT`, `IT_FAKE_MAIL_PORT`,
  `IT_FAKE_TURNSTILE_PORT`), and checks afterwards that nothing of it is left.
- **The e2e suite on a stack with real data.** It signs in as the env admin (`E2E_ADMIN_USERNAME` and
  `E2E_ADMIN_PASSWORD`, else `ADMIN_USERNAME` and `ADMIN_PASSWORD` from `.env`) only to create and delete its own
  `e2e-<run id>-<random>` users and to grant or end their plans; the admin's browser context blocks every other write,
  so models, plans, plugins, the shared knowledge base and other users never change. Each run adds one sign-in to the
  admin's history. The grants write billing events for the test users, which go with them when they are deleted
  because they never place an order.
- The frontend tests pin `TZ=UTC` and the `en_US` locale (`vite.config.ts`), so dates and numbers format the same
  everywhere.

### CI

`.github/workflows/ci.yml` runs every suite on GitHub Actions (`ubuntu-latest`) on pushes to `main`, pull requests and
by hand, with a read-only token, actions pinned to commit SHAs, a timeout per job and the Python (3.13.15) and Node
(22.23.3) of the Docker images. It sees only committed files.

| Job | Runs |
|---|---|
| `backend` | the hashed dev requirements (`pip install --require-hashes`, `pip check`), ruff, mypy, pytest with coverage (fails under 94 %) |
| `backend-postgres` | the 62 `postgres`-marked tests against a Postgres 17.11 service container |
| `frontend` | `npm ci`, `tsc`, Vitest with the coverage thresholds, `vite build` |
| `e2e` | a throwaway `.env` (random secrets, fake provider keys), the stack as Compose project `rag-e2e`, the e2e type check and Playwright; the report and the stack logs are uploaded on failure, and the stack is removed with its volumes |
| `integration` | ruff on `integration/`, then `integration/run.sh --rmi`; the stack logs are uploaded on failure |

## 8. Operations

**Public address.** The app is served at https://asknuvexusdoc.com through a Cloudflare named tunnel on the host
(`cloudflared tunnel run` from `~/.cloudflared/config.yml`, started at login by a LaunchAgent), which forwards to
nginx on `127.0.0.1:8080`. With HTTPS in front, `.env` sets `COOKIE_SECURE=true`, lists the https origins in
`PUBLIC_BASE_URLS`, and sets `NGINX_REAL_IP_FROM` so nginx trusts Cloudflare's `CF-Connecting-IP` only for connections
from Docker's network (`frontend/real-ip.envsh`). Sign in at the https address: some browsers drop Secure cookies on
`http://localhost`. With `COOKIE_SECURE=true` the session cookie is `__Host-rag_session` (Secure, Path `/`, no Domain,
so a plain-http page or a subdomain can't plant one) and the sign-up and Google cookies carry the `__Secure-` prefix;
the name changed with the sign-up deploy, so everyone signed in again once. nginx sends `Strict-Transport-Security:
max-age=31536000` on every response (browsers ignore it over plain http, so `localhost:8080` is unaffected).
Cloudflare's **Always Use HTTPS** and a `www` → apex redirect are still to be turned on (section 10).

Three parts keep the data safe and say when something breaks: migrations at start-up, daily verified backups with a
tested restore, and a monitor that sends alerts. The details are in [`scripts/README.md`](scripts/README.md) (backups
and restore), [`backend/migrations/README.md`](backend/migrations/README.md) and
[`scripts/watchdog/README.md`](scripts/watchdog/README.md); the contract is `SPEC.md` section 10.

### Backups

- **What a backup holds.** A folder `BACKUP_DIR/<UTC time>-<label>` (default `./backups`, git-ignored; the folder is
  mode 700 and its files 600) with the database (`pg_dump -Fc`), every uploaded or generated file (`uploads.tar.gz`,
  with each file's checksum) and Qdrant snapshots of the vector collections, so a restore needs no paid re-embedding.
  `manifest.json` records the time, the app version (`APP_VERSION`), the Alembic revision, the rows per table, the
  files, the points per collection and every file's checksum.
- **When.** The `backup` service backs up daily at `BACKUP_SCHEDULE` (UTC; the default `02:30` is 08:00 in India). It
  also backs up `BACKUP_START_DELAY_SECONDS` (120) after it starts when no backup began since the last scheduled time
  (there is none yet, or the stack was down then); the monitor doesn't alert about a missing or old backup while that
  one is due. `scripts/backup.sh` backs up now.
- **Verified before it counts.** A backup is written to `.tmp-<name>` and checked: every checksum, the dump's table of
  contents (`pg_restore --list`) and its rows counted again, every archived file extracted and compared with its
  checksum, and each snapshot against the checksum Qdrant reported. Only then is it renamed. A failed backup is
  deleted, recorded and alerted.
- **Retention.** Every backup younger than 48 hours; manual and pre-restore backups for `BACKUP_KEEP_DAILY` days; of
  the scheduled and start-up backups, the newest of each of the last `BACKUP_KEEP_DAILY` (7) days and
  `BACKUP_KEEP_WEEKLY` (4) weeks (Monday to Sunday, UTC) that have one; and always the newest backup.
- **Not in any backup: `.env`** (`SECRET_KEY`, `TOKEN_ENCRYPTION_KEY`, the provider keys, the passwords). Keep a copy
  in a password manager. Without the same `TOKEN_ENCRYPTION_KEY` every user must reconnect Gmail after a restore; a
  different `SECRET_KEY` signs everyone out. Redis isn't backed up either: it holds only short-lived state, and the
  worker re-queues unfinished documents from Postgres.
- **Off-site copies.** A backup on the app's disk doesn't survive losing that disk. Copy `./backups` somewhere else
  after the daily backup, encrypted: rclone with a `crypt` remote (`rclone copy --max-age 48h ./backups
  b2-crypt:rag-backups`), an encrypted external drive (`rsync -a --delete ./backups/ /Volumes/Backup/rag-backups/`),
  or restic or borg. `scripts/README.md` has the details. Backups hold personal data: never send one anywhere
  unencrypted.

```bash
scripts/backup.sh                                  # back up now; the last line is BACKUP_NAME=...
docker compose run --rm restore list               # the backups and their sizes
docker compose run --rm restore verify NAME        # verify one without restoring it
docker compose run --rm restore show NAME          # its manifest
docker compose logs backup                         # what the scheduled backups did
```

### Restore

```bash
scripts/restore.sh NAME            # verifies the backup and says what --yes would do; changes nothing
scripts/restore.sh NAME --yes      # restores ("latest" works as a name too); destructive
```

1. It verifies the backup and refuses one that fails. It compares the backup's Alembic revision with this release's
   migrations: it refuses a revision they don't know (a newer release made the backup), and without `--migrate` one
   that starting this release would migrate forward. Nothing is stopped before these checks.
2. It stops the app and takes a `pre-restore` safety backup of the current data (`--no-safety-backup` skips it).
   Restoring the safety backup undoes the restore.
3. It prepares every part next to the current one and checks it: the database in `<db>_restoring` (row counts), the
   files in a staging folder (checksums), each collection as `<name>-restoring` (point counts), and keeps a snapshot of
   every current collection inside Qdrant. If any check fails, nothing is changed and the app starts again.
4. It swaps in Qdrant, then the files, last the database (in one transaction), and clears Redis's ingestion queue. If
   a step of the swap fails, it is undone (the files and collections from before come back), and the app starts again.
5. It migrates the database to this release's schema (with `--migrate`), then reconciles every document with its file
   and vectors (`python -m app.ops.reconcile`): a document whose file the backup lacks is marked failed, one whose
   vectors don't match is re-indexed, and vectors of deleted or failed documents and files no document has are removed.
6. It starts the app and waits until `GET /api/health/ready` answers.

With `--no-start` it stops after step 4 and leaves the app stopped, with a `.maintenance` file that keeps the watchdog
away until you delete it. The restore drill in the integration suite (`integration/tests/test_14_backup_restore.py`)
runs on every CI run: it backs up a stack with data, destroys it with `down -v`, starts an empty one, restores, and
checks logins, document bytes, search results without re-embedding, conversations and the Gmail token. It also
restores over a stack that changed since the backup, restores a backup taken while documents changed, restores with
`--migrate`, and injects failures (damaged backups, a dump that fails to restore, a snapshot Qdrant refuses, a swap
failing at each step), after which the stack must hold exactly its data from before (`scripts/README.md`).

- **A new machine:** clone the repo, put the saved `.env` back, copy the backup folder into `./backups`, run
  `docker compose up -d --build`, then `scripts/restore.sh latest --yes --no-safety-backup` (add `--migrate` if it
  says this release would migrate the backup's schema).
- **Rolling back a deploy:** check out and build the previous release first (`git checkout <release> && docker compose
  build`), then restore the backup taken before the deploy with that release's `scripts/restore.sh NAME --yes`.
  Releases up to `c1da1d0` predate migrations: see `backend/migrations/README.md`, "Rolling back".

### Migrations

- The backend and the worker run `init_db` when they start: Alembic's `upgrade head` in one transaction, under a
  Postgres advisory lock so that only one of them migrates. You never run a migration by hand on a deploy.
- A database made before migrations (tables but no `alembic_version`) is compared with the baseline (revision 0001):
  it is stamped and aligned by revision 0002, or, for a difference that can't be repaired automatically, left
  unchanged while the backend refuses to start and logs one error listing every difference. The live database was
  adopted this way on 2026-10-25, with no difference.
- **A deploy:** `touch .maintenance` (the watchdog leaves the stack alone meanwhile), `scripts/backup.sh` (keep the
  `BACKUP_NAME`: that backup is the way back), then `APP_VERSION=$(git describe --always --dirty) docker compose up -d
  --build` and `docker compose up -d --force-recreate frontend`, and `rm .maintenance` once the app answers. `docker
  compose logs backend` shows `Database schema migrated from ... to revision ...` or `Database schema up to date
  (revision ...)`; `docker compose exec backend alembic current` shows the revision.
- **Adding a migration:** change `backend/app/models.py`, generate a revision against a throwaway Postgres at the
  current head (`alembic revision --autogenerate --rev-id 0003 -m "..."`), review it by hand (renames, values for new
  `NOT NULL` columns, named constraints, no import of `app.models`), and test it with `tests/test_migrations.py`, whose
  drift guard fails while the models and the migrations differ. Every schema change needs a revision, a new table
  included. Never edit a migration that has run on real data. The steps are in `backend/migrations/README.md`,
  "Writing a migration".

### Monitoring and alerts

The `monitor` service runs these checks every `MONITOR_INTERVAL_SECONDS` (60):

| Check | A problem when |
|---|---|
| `postgres`, `redis`, `qdrant` | it doesn't answer (critical) |
| `worker` | its heartbeat is missing or too old (critical) |
| `queue` | pending documents waited over `ALERT_QUEUE_STUCK_MINUTES` (30) while none finished (warning) |
| `backup` | the last backup failed (critical), or there is no backup yet, the last one is older than `BACKUP_MAX_AGE_HOURS` (26), or the scheduler went silent (warning); not while a backup runs or a start-up backup is due |
| `disk_uploads`, `disk_backups` | less than `ALERT_DISK_MIN_FREE_PERCENT` (10 %) free (warning; under half of it critical) |
| `proxy` | the app doesn't answer through nginx (`MONITOR_APP_URL`) (critical) |
| `billing` | with Razorpay keys only: payments to review, a dispute to answer within 3 days, stalled payment checks, rejected keys, a refund stuck over 7 days or failed, 20 or more bad webhook or return signatures in an hour, a webhook event failed 3 times, captured payments no webhook reported, live keys without `RAZORPAY_WEBHOOK_SECRET`, test keys on a public `https` address, unpaid users at their payment-link cap (`CHECKOUT_UNPAID_PER_HOUR`) in the last hour (warning) |
| `mail` ("Email") | an email failed in the last 24 hours, a due one waits over 10 minutes, sending paused after a refused SMTP login, 80 % of `MAIL_DAILY_LIMIT` sent today, sign-up email paused by bounces, 3 or more bounces today, a spam complaint in the last 7 days, 10 or more Turnstile verifications Cloudflare couldn't answer in the last hour, the site-wide sign-in robot check turned on in the last hour, or sign-up open without the mailer, Turnstile or the bounce webhook (warning); without a mailer it reads "Email not set up" (`ok` while no email waits and sign-up is closed) |

An alert goes out when a check has shown a problem `ALERT_CONFIRM_CHECKS` (2) times in a row, so a blip sends
nothing; again every `ALERT_REPEAT_HOURS` (6) while it lasts; and when it recovers. Alerts never hold secrets,
document names, email contents or usernames. The channels can be combined; with none, alerts are only logged
(`docker compose logs monitor`) and shown in Admin → System:

| Channel | Settings in `.env` | How |
|---|---|---|
| Webhook | `ALERT_WEBHOOK_URL` | receives `POST {"text": ...}`: a Slack or Google Chat incoming webhook, a Discord webhook with `/slack` at the end, or a Microsoft Teams incoming webhook. The URL is a secret |
| ntfy (push to a phone) | `ALERT_NTFY_URL`, `ALERT_NTFY_TOKEN` | install the ntfy app, pick a long random topic and subscribe to it; set `ALERT_NTFY_URL=https://ntfy.sh/<topic>` (or your own ntfy server), plus a token if the topic is protected |
| Email | `ALERT_SMTP_HOST`, `ALERT_SMTP_PORT` (587), `ALERT_SMTP_SECURITY` (`starttls`; `ssl` for port 465; `none` for a local relay), `ALERT_SMTP_USERNAME`, `ALERT_SMTP_PASSWORD`, `ALERT_SMTP_FROM`, `ALERT_SMTP_TO` (comma-separated) | needs the host, the sender and a recipient; for Gmail use an app password |

After setting a channel, run `docker compose up -d monitor`, then press **Send a test alert** in Admin → System.

- **Admin → System** shows the overall status and every check with its detail, the app version, the worker, the
  queue, failed documents of the last 24 hours, the database size, the disks, Gmail connections that need a
  reconnect, the backups (the last one, the schedule, how many are kept, the last failure), and the alert channels with
  the recent alerts.
- **`GET /api/health/ready`** (no sign-in) answers `200 {"status": "ready"}` while Postgres, Redis and Qdrant answer
  and the worker's heartbeat is fresh, else `503 {"status": "unready"}`. Point an external uptime check at it once the
  app has a public address: it also notices the whole machine going away.
- **The watchdog** (optional, not installed): the monitor runs inside Docker, so it can't report Docker itself
  stopping. `scripts/watchdog/watchdog.sh` runs on the host every 5 minutes; when `http://127.0.0.1:8080/api/health`
  doesn't answer twice, 30 s apart, it starts Docker and the stack's containers as they are (`docker compose start`,
  never a build) and alerts through the same channels. It leaves the stack alone while `.maintenance` exists (a deploy,
  a running restore, an app left stopped on purpose). Install it as a macOS LaunchAgent or a Linux systemd timer
  (`scripts/watchdog/README.md`), then check the channels with `scripts/watchdog/watchdog.sh --test`.
- **The public address in the watchdog** (optional, off by default): the tunnel can stall while the app runs (on
  2026-11-02 cloudflared stopped reconnecting and the site showed Cloudflare's 530 until the tunnel was restarted by
  hand). With `RAG_PUBLIC_URL=https://asknuvexusdoc.com/api/health` and
  `RAG_TUNNEL_LAUNCHD_LABEL=com.asknuvexusdoc.tunnel` (or `RAG_TUNNEL_SYSTEMD_UNIT` on Linux) in its LaunchAgent or
  unit, the watchdog also asks the public address whenever the app answers locally. When that fails twice, 30 s apart
  (no answer, or a status outside 2xx and 3xx), it restarts the tunnel (`launchctl kickstart -k`, or `systemctl
  restart`), never Docker or the stack, and alerts once when the address went down (saying whether the restart
  worked), every `ALERT_REPEAT_HOURS` while it stays down and once when it answers again; `.maintenance` keeps it away
  too. The install commands for this Mac are in `scripts/watchdog/README.md`.

### Web server logs

nginx writes one line per request (the visitor's IP address, the time, the request, the status, the referrer and the
browser) to a file per UTC day, `access-YYYY-MM-DD.log`, on the `weblogs` volume (`/var/log/nginx/access` in the
`frontend` container), not to `docker compose logs`. Plugin, Razorpay and Resend requests are logged by path only and
the OAuth callbacks (Gmail, Google sign-in) not at all (`frontend/nginx.conf`). The backend keeps no access log of its
own (uvicorn `--no-access-log`).

- **Deleted after 30 days.** The `backup` service deletes every file last written more than 30 days ago when it starts
  and at every `BACKUP_SCHEDULE` time (`prune_weblogs` in `scripts/backup/lib.sh`). The privacy policy promises 30 days,
  so it is a constant there, not a setting. The logs are never in a backup.
- **nginx's error log** (it can hold an IP address) stays on stderr, in `docker compose logs frontend`, which Docker's
  log driver trims by size.

```bash
docker compose exec frontend tail -f /var/log/nginx/access/access-$(date -u +%F).log   # today's, live
docker compose exec frontend ls -l /var/log/nginx/access                               # the days kept
docker compose logs backup | grep 'web server log'                                     # what was deleted
```

### Operations settings

Every one is optional; the defaults are in `.env.example`. After a change, run `docker compose up -d`.

| Setting (default) | What it does |
|---|---|
| `BACKUP_DIR` (`./backups`) | where backups go on this machine; keep it outside `./backend` and `./frontend` (the build contexts) and out of git |
| `BACKUP_SCHEDULE` (`02:30`) | daily backup times in UTC, `HH:MM`, comma-separated for several |
| `BACKUP_START_DELAY_SECONDS` (120) | the delay of the start-up backup |
| `BACKUP_KEEP_DAILY` (7), `BACKUP_KEEP_WEEKLY` (4) | retention (above) |
| `BACKUP_UID`, `BACKUP_GID` (unset) | Linux only: the owner of the backup files, so an off-site copy can read them without root |
| `APP_VERSION` (empty) | shown in Admin → System and written to backup manifests; set by the deploy command |
| `MONITOR_INTERVAL_SECONDS` (60) | how often the monitor checks |
| `MONITOR_APP_URL` (`http://frontend/api/health`) | the app through nginx, from inside Docker; empty: not checked |
| `ALERT_CONFIRM_CHECKS` (2), `ALERT_REPEAT_HOURS` (6, 0: never) | results in a row before an alert, and the reminder interval |
| `ALERT_NAME` (`AskNuvexusDoc`) | the start of every alert's title |
| `BACKUP_MAX_AGE_HOURS` (26, 0: no backup checks) | the oldest the last good backup may be |
| `ALERT_QUEUE_STUCK_MINUTES` (30), `ALERT_DISK_MIN_FREE_PERCENT` (10) | the queue and disk thresholds |
| `WORKER_HEARTBEAT_SECONDS` (15), `WORKER_HEARTBEAT_MAX_AGE_SECONDS` (60) | the worker's heartbeat, and the age that makes the app "unready" |
| `ALERT_WEBHOOK_URL`, `ALERT_NTFY_URL`, `ALERT_NTFY_TOKEN`, `ALERT_SMTP_*` (empty) | the alert channels (above) |

## 9. Plans and billing

Every user except the admin needs a current plan to use the app. A plan is bought as a prepaid 30-day period through
Razorpay (UPI, cards, netbanking, wallets) or by **direct UPI** to the owner's own UPI ID (confirmed by the admin),
granted free by the admin, or paid by a bank transfer the admin records. Nothing renews by itself. The design is in
[`docs/briefs/plans-billing.md`](docs/briefs/plans-billing.md) (looks: `plans-billing-visual.md`; Razorpay facts:
`payments-razorpay-research.md`) and, for direct UPI, [`docs/briefs/upi-direct.md`](docs/briefs/upi-direct.md); the
contract is `SPEC.md`.

### What users see

- **After sign-in** a user without a plan (none yet, or ended) lands on the plan page, `/plans`: the plan cards with
  their features and limits, a compare table, and the options they have now (choose, renew, upgrade, switch at
  renewal). Without a plan only the plan page, checkout, Settings and the public pages open; chats and documents are
  kept.
- **Checkout** (`/plans/checkout/<plan>`) shows the dates, the amount, what a switch to a smaller plan removes, and
  **Pay**. Pay opens Razorpay's own page in the same tab, which offers UPI, cards, netbanking and wallets. No Razorpay
  script runs on the app's pages. Paying online needs a confirmed email ("Add and confirm your email in Settings →
  Account first. Your receipt goes there."). A user who has never paid gets 3 payment links an hour and 6 a day, and
  all such users together `CHECKOUT_UNPAID_PER_HOUR` (30) an hour ("Checkout is busy. Try again in a few minutes.").
  With direct UPI on, **Pay with UPI** comes first and **Pay online** second (each only when available); paying by UPI
  needs no email (below, "Direct UPI").
- **The return page** (`/billing/return`) checks with the server until the payment is confirmed, then shows the plan,
  its dates and the receipt number, with **Start chatting** and **Download receipt**. A failed or abandoned payment
  says the plan hasn't changed; a payment the app has to check says "Please don't pay again." A direct-UPI order has
  its own page at the same address (the QR code, the UTR field, then "We're checking your payment").
- **Settings → Plan & billing:** the current plan with its dates, the next period, receipt details (a name), the
  switches for receipt and reminder emails (on by default; they go to the account's confirmed email), and the payment
  history with a PDF receipt per payment (`RA/26-27/000001`, numbered per Indian financial year; test payments
  `TEST/…`).
- **Locked features:** a feature the plan lacks stays visible in the "+" menu, the model menu and the Plugins menu
  with a lock and "Included in <plan>", and leads to the plan page. The server refuses it anyway (402). When the chat
  allowance runs out (429), the user can upgrade, or start a new period of the same plan at once after using 90 % of
  an allowance.
- **Reminders:** a banner from `BILLING_REMINDER_DAYS` (5) days before the paid time ends, and emails 5 days before,
  the day before and when it ends, only when the mailer is set up (section 10), the account has a confirmed email and
  its reminder switch is on. None when another period or an open-ended complimentary plan follows.
- **When a plan ends:** the next request answers 402. An open chat shows a "plan ended" panel with **Renew** and keeps
  the unsent message; the next page load goes to the plan page. Nothing is deleted: Settings → Plugins & data → "Your
  data" lists the user's documents (Download, Delete) and chats (Delete), and the data export still works. A Gmail
  connection is kept but unused, and works again after an upgrade.
- **Public pages** (no sign-in): `/pricing` (the plan cards, from the plans), `/terms`, `/privacy`, `/refunds`,
  `/contact`, `/about` and `/shipping`. Razorpay's website review reads them.

### The plans

Seeded on the first start with this feature (the values below), then edited in Admin → Plans. `0` means unlimited.
Allowances count per period, from the start of the user's current period; an open-ended complimentary plan counts per
UTC calendar month.

| | Starter | Standard | Professional |
|---|---|---|---|
| Price per 30 days | ₹500 | ₹2,000 (recommended) | ₹5,000 |
| Files from chat / images from chat / Gmail | no / no / no | yes / no / no | yes / yes / yes |
| Chat models | Claude Haiku 5.5, GPT-5.4 mini | those and Claude Sonnet 5.5 | every enabled chat model |
| New chats start with | Claude Haiku 5.5 | Claude Sonnet 5.5 | the default model (Claude Opus 5.5) |
| Chat allowance (standard tokens) | 500,000: about 1,600 Haiku answers | 2,000,000: about 260 Sonnet answers | 5,000,000: about 290 Opus answers |
| Requests per minute | 10 | 20 | 30 |
| Max file size, documents, storage | 10 MB, 50, 200 MB | 25 MB, 200, 1,000 MB | 50 MB, 500, 3,000 MB |
| Images per period | – | – | 60 |
| Vision tokens per period (scanned PDF pages) | 150,000 (about 50 pages) | 500,000 (about 170) | 1,000,000 (about 340) |

- **The chat allowance counts each answer's cost** in standard tokens: one standard token is $3.20 per million (Claude
  Sonnet 5.5's blended price), so an answer counts `ceil(cost in USD ÷ 0.0000032)`, or its plain tokens when the model
  has no price. A Claude Haiku 5.5 answer counts about 1/20 of its tokens, a Claude Opus 5.5 one about twice; the model
  menu shows each model's rate ("×0.05", "×2"). The answer counts assume 6,000 tokens per answer, plus the file and
  image tools' prompts when the plan has them.
- Model lists are matched by LiteLLM model string when the plans are seeded; a model that doesn't exist is left out.
  A user's own allowed-models list only narrows the plan's.
- **Periods:** a paid period starts when the payment is confirmed (or, for a renewal, when the paid time ends) and ends
  at the first midnight IST at least 30 days later ("valid through 25 Nov 2026" is the whole of that day). Renewing, or
  switching to another plan, adds one period after the paid time (at most one paid ahead); a switch to a smaller plan
  refunds nothing. **Upgrade now** starts a
  30-day period of the higher plan at once, for its price minus a credit for the current period: what that period was
  paid (less refunds) × the smaller of its time left and its allowance left, in whole rupees (no credit for a
  complimentary period); at least ₹1 is charged.
- **Editing a plan** changes new periods only, unless the admin also applies it to the current and scheduled ones
  (lowering what users paid for asks again). A price change never touches a paid period. A plan that was ever used
  can only be retired, not deleted.
- **The rollout:** on the first start with plans, every active non-admin user got an open-ended complimentary
  Professional plan, and every per-user limit was cleared to the plan's value (the old values are kept in the billing
  events).

### What the admin does

- **Admin → Plans:** the plans with their users (now, next, free), key limits and the worst-case AI cost per period
  against what is kept after Razorpay's fee (2 % plus GST on it); **New plan**, **Edit** (features, limits, models, the
  recommended plan, active or retired) and **Delete** (unused plans only).
- **Admin → Billing:** **Overview** (a month in IST: gross, Razorpay fees, refunds, disputes lost, net; **Needs
  attention**; users by plan; expiring in 7 days; lapsed in the last 30 days; **Export CSV** of the month's payments,
  refunds and lost disputes), **UPI** (the direct-UPI payments to check, with **Confirm…** and **Reject…**, and the
  recent ones; below, "Direct UPI"), **Payments** (each payment with **Refund** and **Download receipt**), **Orders**
  (checkouts by status, each with its channel; **Check now**; a payment in review gets **Mark resolved** or **Grant
  instead**), and **Business & payments** (the business name, address, contact email and phone and the receipt prefix,
  shown on receipts and the public pages; the UPI limit; the payments setup with the webhook URL and the 14 events to
  subscribe; **Check pending payments now**; the direct-UPI settings and their ₹1 test QR; the going-live checklist).
- **Admin → Users:** in a user's **Plan** section, **Grant a complimentary plan…** (1 to 90 days, or no end date),
  **Change plan…** (no charge), **Revoke…** a period, **End plan** for an open-ended one, and **View payments**; the
  switch **Can make test payments** marks testers. Every grant, plan change, revoke, limit override and tester flag is
  recorded in the billing events (grants, changes and revokes with the admin's reason).
- **Refunds** start in Admin → Billing → Payments (Razorpay's refund API). A full refund ends the period it paid for; a
  partial one does only with **End access** ticked. Refund from the app rather than Razorpay's Dashboard: a Dashboard
  refund reaches the app only through a webhook. A direct-UPI payment or a bank transfer is paid back from your bank
  app and only recorded here.
- **Disputes** (chargebacks) arrive by webhook and show under Needs attention with the date to respond by, in
  Razorpay's Dashboard. A lost dispute ends access like a full refund.
- **Bank transfers** (NEFT, RTGS, IMPS to your account): **Record bank transfer** (Overview or Payments) gives the user
  a period and a receipt (at least ₹1; a note is required when the amount is below the plan's price). A bank reference
  that carries the UTR of a direct-UPI payment that waits, was confirmed or is under review is refused (the same money
  can't count twice).
- **Deleting a user** keeps their billing records (orders, payments, refunds, disputes, receipts and their events) with
  the username, for the accounts. A user who never had an order leaves no billing events behind. Their periods and
  billing profile go with them, and their open payment links and UPI requests are cancelled. A UTR still waiting for
  your check stays in Admin → Billing → UPI (the delete dialog warns about it): it can't be confirmed any more, so pay
  it back by UPI and reject it.

### Direct UPI

Customers pay your own UPI ID from any UPI app, then type the payment's 12-digit **UTR** (the UPI reference number);
you find that credit in your bank or UPI app and confirm it in Admin, and only then the plan starts, through the same
exactly-once apply as every other payment. No payment company is involved, and it works with or without Razorpay. The
design is [`docs/briefs/upi-direct.md`](docs/briefs/upi-direct.md).

**What customers see.**

- At checkout, **Pay ₹… with UPI** first ("From any UPI app, straight to <business name>. Your plan starts once we've
  checked the payment, usually within a few hours.") and, when online payment is available too, **Pay online**. Paying
  by UPI needs no confirmed email.
- The payment page (`/billing/return?order=…`, open for 24 hours): on a computer a QR code to scan with any UPI app; on
  a phone **Open UPI app** (a `upi://pay` link with your UPI ID, payee name, the exact amount and the reference) with
  the QR code behind **Show QR code** and **Save QR image** (to scan from the UPI app's gallery). Always the UPI ID,
  the amount and the note (the order's reference, `AN-XXXX-XXXX`) with Copy buttons, and the UTR field with **I've paid
  · Send UTR**. A request can be cancelled; one made for the same plan and amount with an hour left is shown again
  instead of a new one.
- After the UTR: "We're checking your payment" (the page checks every 30 s for half an hour), a banner in the chat and
  a notice on `/plans` and in Settings. No new checkout of either kind starts while it waits, so nobody pays twice.
- Confirmed: the plan starts (a renewal after the paid time; an upgrade at once, in place of the current period) and
  the receipt reads "UPI (direct)" with the UTR. Rejected: the reason you chose and your note, on the payment page and
  for 7 days in a chat banner, on `/plans` and in Settings, with **Start a new payment**.

**Which UPI ID to use.** The UPI ID of the bank account the money should reach (research in the brief, 2.2 to 2.5):

- **A business ID** (a business current account's UPI ID, or the merchant ID of Google Pay for Business, PhonePe
  Business, Paytm for Business or BharatPe) shows a business name and notifies you of each payment. The small-merchant
  (P2PM) IDs of the business apps are limited by NPCI to **₹10,000 per payment, ₹25,000 per 24 hours and ₹1,00,000 per
  30 days received**: 50 Standard payments a month.
- **A personal savings account's ID** works too, but banks may ask about many business payments into it. And a
  `upi://` link opened from a phone's browser can fail in some apps ("exceeds limit", a security warning, or nothing),
  by developers' reports more often for personal IDs (Google Pay and PhonePe are named; unverified). Scanning the QR
  code and paying the copied UPI ID work in every app, so the page always shows both; your ₹1 tests settle it.
- **iPhones have no app chooser:** **Open UPI app** opens whichever one UPI app iOS picks, so iPhone users are told to
  copy the UPI ID or save the QR image if it isn't theirs.
- Since 30 June 2025 UPI apps show the account's name from the bank's records, not the payee name of the link: enter
  the payee name as your bank shows it. A bank may also cap a payer's new account, device or reset PIN at ₹5,000 for
  24 hours (Professional costs exactly that).
- Ask your bank and your accountant which ID suits a business (brief 14.1).

**Turning it on** (Admin → Billing → Business & payments, the **UPI payments** card; nothing goes in `.env`):

1. Enter the **UPI ID** (lowercased; letters, digits, `.`, `_` and `-`, then `@` and the bank's handle) and the
   **Payee name** (up to 50 of letters, digits, spaces and `. , & ' ( ) / -`), leave **Accept UPI payments** off, and
   **Save**.
2. **Test with the ₹1 QR** that then appears: scan it from another phone with Google Pay, PhonePe and Paytm and pay ₹1.
   Check the name each app shows, that ₹1 reaches your account, and where your bank or UPI app shows the UTR and the
   note (`AN-TEST`).
3. For email alerts, set the **contact email** in Business details and the mailer (section 10); the card says "Email
   alerts: on, to …" once both are set.
4. Switch **Accept UPI payments** on and **Save** (it is refused without the UPI ID and payee name). The going-live
   checklist then lists "Test direct UPI with ₹1 from Google Pay, PhonePe and Paytm".
5. Then a real test with a `smoke-*` user from a phone: buy Starter by UPI, send the UTR, confirm it, check the receipt,
   pay it back by UPI and record the refund (access ends).

Changing the UPI ID later moves future money only: requests already made keep the ID they were made with for up to 24
hours, and the list shows that ID under the reference when it differs. Every change is in the billing events.

**Every day.** You hear of a UTR by Needs attention ("2 UPI payments to check"), the **UPI · 2** tab, the email "UPI
payment to check: ₹2,000 (AN-…)", and the monitor's `billing` check, which warns when one waits over 24 hours. In
Admin → Billing → **UPI**, oldest first:

1. Open your bank or UPI app and find a credit of **exactly the amount** with **that UTR**. If your app shows the note,
   it should be the order's reference. A customer could type someone else's UTR seen on a screenshot: the amount and
   the note are your check.
2. Found it: **Confirm…**, tick "I found a credit of exactly ₹… with UTR …" and **Confirm and start the plan**. The
   payment is recorded (method UPI, by you, paid at the time the UTR came) and applied once; a double click or a second
   admin changes nothing, and a stale dialog with another UTR is refused. An upgrade whose credit fell meanwhile, or
   whose current period changed, goes to **review** (Orders): refund it or grant the plan instead.
3. Not found, or not right: **Reject…** with a reason the customer reads: "No payment with this UTR arrived", "The
   amount is different" (send what they paid back by UPI), "This UTR belongs to another payment" or "Something else"
   (a note is then required). The note (up to 200 characters) is shown to the customer: no bank details in it. A
   rejected UTR is free again, so if you rejected a payment that did arrive, the customer starts a new payment and
   sends the same UTR.

**Refunds by UPI.** Pay the money back by UPI from your bank app to the payer of that UTR first, then Admin → Billing →
Payments → **Refund…** on the "UPI (direct)" payment → **Record refund of ₹…** (the reason can hold your refund's UTR).
Nothing moves through the app; access follows the usual refund rules (a full refund ends the period; a partial one only
with **End access**).

**Limits and abuse guards.**

- One open UPI request per user; a request without a UTR expires after 24 hours; one UTR per request.
- A UTR can be on only one payment that waits, was confirmed or is under review, and never on a bank transfer you
  recorded (both ways): the same money can't count twice. The amount is fixed when the request is made.
- Per user: 5 requests per day (IST; 3 for an account that has never paid) and 10 UTR submissions per day. Three
  rejections in 30 days pause direct UPI for that user (they can still pay online or write to you).
- Accounts that have never paid: together at most 20 UTRs waiting for your check, after which their new requests are
  refused until you have checked some ("Many UPI payments are waiting to be checked.") and the `billing` check warns
  for an hour; from one network at most 10 requests an hour and 30 a day. A customer who has paid can always send
  their UTR. nginx rate-limits the UPI checkout like the online one.
- The app stores the UTR and the reference, never the payer's name or UPI ID; logs carry no UTR, UPI ID or payee name.

**Emails** (only with the mailer set up, section 10; without it, Needs attention, the UPI tab and the monitor still
show everything, and customers see it on the payment page and in Settings):

- To you, at the business contact email: "UPI payment to check" for each UTR sent, with the amount, plan, username,
  reference, UTR and a link to Admin → Billing → UPI. It isn't sent when you decided before the mailer got to it.
- To the customer, at their confirmed email with receipt emails on: "We couldn't confirm your UPI payment" when you
  reject one, and the receipt when you confirm one (as for every payment).

**"I paid, but my UTR didn't go through."** Nothing to confirm yet: there is no UTR in the list.

- Ask for the 12-digit UTR from the payment's details in their UPI app ("UTR", "UPI Ref No." or "UPI transaction ID";
  not the app's own transaction ID with letters), and find the credit yourself first.
- While their request is open (24 hours), they enter it there: the chat banner's **Continue**, `/plans` or Settings →
  Plan & billing lead back to it. After that, the request has expired: they start a new payment for the same plan and
  send the same UTR on it (the app tells them so). If the amount differs, reject it with "The amount is different" and
  pay it back.
- If they can't, record it yourself as a bank transfer (**Record bank transfer**, the UTR as the bank reference): the
  receipt then reads bank transfer.
- If their app says the payment failed or is pending, confirm nothing: a failed payment's money comes back to them by
  itself, usually within a day.

### Setting up Razorpay

1. **Test mode.** Put the test keys from Razorpay's Dashboard (Account & Settings → API Keys) in `.env` as
   `RAZORPAY_KEY_ID` (`rzp_test_…`) and `RAZORPAY_KEY_SECRET`, then run `docker compose up -d backend worker` and
   `docker compose up -d --force-recreate frontend`. Mark a tester (Admin → Users → **Can make test payments**): with
   test keys only testers can pay, and they see Razorpay's test card (4100 2800 0000 1007, any future expiry and any
   CVV). Test periods last 1 day and stop counting once live keys run. Test mode allows about 30 payment links per
   business (Admin → Billing shows how many were used), so test sparingly.
2. **Until webhooks are set up**, the return page and the worker confirm payments by polling Razorpay: every
   `BILLING_RECONCILE_SECONDS` (30) while a link is open, then less often for `BILLING_LATE_CAPTURE_DAYS` (5) days, so
   a late payment is still applied. If the machine sleeps, payments are confirmed when it wakes. List every address
   users open the app at in `PUBLIC_BASE_URLS`: Razorpay sends the browser back to the one the checkout started from
   (or to the first).
3. **Webhooks**, once the app has its public HTTPS domain: in Razorpay's Dashboard → Webhooks, add
   `https://<domain>/api/billing/razorpay/webhook` (Admin → Billing → Business & payments shows the URL, from the first
   `PUBLIC_BASE_URLS` entry, and the 14 events), choose a secret there, and put the same value in `.env` as
   `RAZORPAY_WEBHOOK_SECRET`. Never register a webhook on the quick tunnel: its URL changes on every restart.
4. **Live.** After KYC and Razorpay's website review (fill in the policy pages and the business details first), put the
   live keys (`rzp_live_…`) in `.env`; the webhook is required before live keys. Work through the going-live checklist
   in Admin → Billing → Business & payments: keep Customer Fee Bearer and Offers off in Razorpay's Dashboard (amounts
   must match exactly), and set the UPI limit per payment that Razorpay confirms for the account (₹2,000 until then;
   above it the checkout suggests netbanking or a card).

**Policy pages.** `frontend/public/legal/*.md` (`terms`, `privacy`, `refunds`, `shipping`, `contact`, `about`, and the
pricing page's intro) are drafts with bracketed prompts: each starts with `<!-- draft -->` and shows a "Draft" band.
Replace each with your own text (the first `# ` heading is the page title; remove the marker line), then rebuild the
frontend (`docker compose up -d --build frontend`). The going-live checklist flags pages still in draft.

**GST.** Not implemented: the app is built for a seller without a GSTIN. The price is the whole charge, receipts are
titled "Receipt" with no tax line, and there is no GST setting. Registering for GST needs tax invoices, a separate
feature to build before charging GST.

**Settings** (the "Payments" block of `.env.example`; a value outside its range stops the backend at start-up; apply a
change with `docker compose up -d backend worker`, then recreate the frontend):

| Setting (default) | What it does |
|---|---|
| `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` (empty) | both set: online payment is on; `rzp_test_…` is test mode, `rzp_live_…` live, any other prefix leaves payments off and logs an error at start-up. The secret is never logged or shown |
| `RAZORPAY_WEBHOOK_SECRET` (empty) | webhooks are accepted only when it is set; required before live keys |
| `RAZORPAY_WEBHOOK_SECRET_PREVIOUS` (empty) | also accepted, for the day after a rotation; remove it then |
| `RAZORPAY_API_BASE` (`https://api.razorpay.com`) | another origin only for a fake in tests; with a live key the backend refuses to start on any other value |
| `BILLING_CHECKOUT_MINUTES` (20; 16 to 120) | a payment link's lifetime |
| `BILLING_LATE_CAPTURE_DAYS` (5; 1 to 10) | how long an unpaid checkout is still watched for a late payment |
| `BILLING_REMINDER_DAYS` (5; 1 to 14) | the "ends soon" banner and the first reminder email |
| `BILLING_RECONCILE_SECONDS` (30; 2 to 300) | the tick of the worker's billing loop (open checkouts, refunds, webhook events) |
| `BILLING_USD_INR` (88; 1 to 1000) | rupees per dollar, for the cost estimates in Admin → Plans only |
| `CHECKOUT_UNPAID_PER_HOUR` (30; 1 to 1000) | payment links per hour for all users who have never paid, together; beyond it checkout answers "Checkout is busy" |

Receipts (PDF attached) and plan reminder emails go through the mailer to the account's confirmed email: they are on
once `MAIL_SMTP_HOST` and `MAIL_FROM` (with the other `MAIL_SMTP_*` settings, section 2) are set. `BILLING_EMAIL_FROM`
is removed (ignored with a warning when still set). Direct UPI adds no setting here: it is configured in Admin
("Direct UPI" above), and its limits are fixed in `services/billing/upi.py` and `services/auth/throttle.py`
(`upi_unpaid`).

Removed, now per plan: `DEFAULT_MONTHLY_TOKEN_LIMIT`, `DEFAULT_RATE_LIMIT_PER_MIN`, `DEFAULT_MAX_UPLOAD_MB`,
`DEFAULT_MAX_DOCUMENTS`, `DEFAULT_MAX_STORAGE_MB`, `DEFAULT_MONTHLY_IMAGE_LIMIT` and `VISION_MONTHLY_TOKEN_LIMIT`
(ignored with a warning when still set).

## 10. Sign-up and email

Visitors can create their own account, by email and password or with Google, once the owner opens sign-up. One mailer
sends every email to users. The design is in [`docs/briefs/signup-auth.md`](docs/briefs/signup-auth.md) (looks:
`signup-auth-visual.md`; facts about Google, Cloudflare, Resend and Have I Been Pwned: `signup-research.md`); the
contract is `SPEC.md`. **Sign-up is closed** until the owner opens it.

### What visitors see

- **Sign-in** (`/login`): **Continue with Google**, then one **Email or username** field (an email works once it is
  confirmed), the password and **Forgot password?**. While sign-up is open, "New to AskNuvexusDoc? **Create an
  account**" sits under the lede, the public top bar has **Sign up**, and `/pricing` reads "Create an account to choose
  a plan". After repeated failed sign-ins from a network a robot check (Turnstile) comes first, never from this
  machine.
- **Create an account** (`/signup`): **Continue with Google**, or **Your name**, an email and a password (the rules
  show under the field), the unticked terms box and the robot check. Then "Check your inbox": the emailed link (24 hours,
  `/verify-email`) finishes the account in one click in the same browser, or with the chosen password on another
  device. Nothing exists until then. **Resend** waits 60 s between mails. Every address gets the same answer; one that
  already has an account gets a "you already have an account" email instead.
- **Continue with Google:** an account linked to that Google account is signed in. A new one confirms on
  `/signup/google` ("Create your account": **Your name** and the terms ticked; from the sign-in page also **I already
  have an account**).
  A verified `@gmail.com` address equal to the confirmed email of an account without a Google sign-in is linked to
  it; any other existing address (Workspace ones included) asks for the password and **Connect Google…** in Settings.
  An address Google hasn't verified gets our confirmation email.
- **Names** (added 2026-11-02): the name given at sign-up is the account's display name (any script, up to 80
  characters; no `<`, `>` or invisible characters). The chat greets with its first word ("How can I help, Bikash?"),
  and the avatar, the account menu, Settings and Admin → Users show it with the email under it. People change it in
  Settings → Account → **Name**; the admin sets or clears it in a user's form (optional for admin-created users).
  Accounts made before have no name until someone sets one: they show the username, or for a generated
  `user-xxxxxxxx` handle the email's local part. Receipts name the billing profile's name, else the display name,
  else the username.
- **No plan until they pay:** a new account has no plan and lands on `/plans` (or the page it was heading for), with
  only the plan page, checkout, Settings and the public pages open (section 9). Checkout needs a confirmed email. While
  Razorpay isn't live, users other than testers read "Paid plans open soon. Check back in a few days." There is no
  welcome email.
- **Forgot password** (`/forgot-password`): an email address (not a username) and the robot check. The link (30
  minutes, `/reset-password`) sets a new password, ends every session (it doesn't sign in) and offers to disconnect a
  linked Google sign-in. An address with only an unfinished sign-up gets a fresh verification link instead. Accounts
  without an email ask the admin.
- **Settings → Account:** **Add email…** or **Change email…** (the password first, or for an account without one a
  fresh Google sign-in; the link to the new address works only for the account that asked, and the old address gets a
  notice with a 7-day undo link, `/undo-email`, that puts it back, removes the password and ends every session),
  **Change password…** or **Set a password** (after a Google sign-in), **Connect Google…** (with the password) and
  **Disconnect…** (only while a password and a confirmed email remain).
- **Sign-up is closed until the owner opens it** in Admin → Users → **Sign-up** → **Edit…** (**Open sign-up**, and the
  methods **Email and password** and **Continue with Google**). Email sign-up also needs the mailer and Turnstile,
  Google sign-up its client. While it is closed the sign-in page says accounts are created by the administrator, and
  existing accounts keep working. Admin-created users are as before (username and password, under the password
  policy) and add their email themselves. The admin from `.env` has no email, Google sign-in or reset.

### Opening sign-up

The owner's steps, in this order; `docs/briefs/signup-auth.md` section 17 has every click.

1. **Policy pages:** the real privacy policy, terms and about page (`frontend/public/legal/`; the privacy policy names
   what is stored, for how long and the processors, below), the draft marker removed, the frontend rebuilt, and
   `TERMS_VERSION` set to the terms' date.
2. **Contact email** in Admin → Billing → Business & payments (emails and error pages show it).
3. **Razorpay live:** KYC, the live keys and the webhook secret in `.env`, one real payment (section 9; checkout needs a
   confirmed email, so the payment waits for the mailer, steps 5, 9 and 10). Until then the Sign-up card warns and new
   accounts read "Paid plans open soon".
4. **Cloudflare, HTTPS:** **Always Use HTTPS** on; a Redirect Rule `www.asknuvexusdoc.com` → `https://asknuvexusdoc.com`
   (301, query kept), then `https://www.asknuvexusdoc.com` out of `PUBLIC_BASE_URLS`; Rocket Loader, Email Address
   Obfuscation, Zaraz and the automatic Web Analytics snippet stay off (each would inject a script). HSTS comes from
   nginx. `NGINX_REAL_IP_FROM=172.16.0.0/12` is already set; the Sign-up card checks it.
5. **Resend:** an account, the domain `asknuvexusdoc.com` in region Tokyo (ap-northeast-1), and in Cloudflare DNS (each
   **DNS only**) exactly what Resend's Records tab shows, then **Verify**; open and click tracking off:

   | Type | Name | Value | Priority |
   |---|---|---|---|
   | MX | `send` | `feedback-smtp.ap-northeast-1.amazonses.com` | 10 |
   | TXT | `send` | `v=spf1 include:amazonses.com ~all` | |
   | TXT | `resend._domainkey` | the `p=…` key Resend shows | |
   | TXT | `_dmarc` | `v=DMARC1; p=none; rua=mailto:dmarc@asknuvexusdoc.com;` | |

   An API key with **Sending access** for the domain is `MAIL_SMTP_PASSWORD`. A webhook to
   `https://asknuvexusdoc.com/api/mail/resend-events` with **email.bounced** and **email.complained**: its signing
   secret (`whsec_…`) is `MAIL_WEBHOOK_SECRET`.
6. **Cloudflare Email Routing** for the apex: your own mailbox as a verified destination, and `support@` and `dmarc@`
   routed to it.
7. **Turnstile:** a widget "AskNuvexusDoc", hostname `asknuvexusdoc.com` only (no `www`, no `localhost`), mode
   **Managed**, pre-clearance **No**; its keys are `TURNSTILE_SITE_KEY` and `TURNSTILE_SECRET_KEY`.
8. **Google sign-in, in a separate Google Cloud project** "AskNuvexusDoc" (the Gmail plugin's project stays as it is,
   in Testing): the domain verified in Search Console (a TXT record on `@`), Google Auth Platform with Audience
   **External**, Branding (home page `/about`, privacy policy, terms, authorized domain `asknuvexusdoc.com`), only the
   scopes `openid` and `…/auth/userinfo.email`, a **Web application** client with no JavaScript origins and the one
   redirect URI `https://asknuvexusdoc.com/api/auth/google/callback` (its ID and secret are `GOOGLE_SIGNIN_CLIENT_ID`
   and `GOOGLE_SIGNIN_CLIENT_SECRET`), **Publish app**, then **Verify branding** once the policy pages are live.
9. **`.env`** (keep the existing lines; never commit it):

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
   TERMS_VERSION=<the terms' date>
   ```

   Optional: `PASSWORD_MIN_LENGTH`, `PASSWORD_BREACH_CHECK`, `ADMIN_SIGNIN_FROM=local` (if you administer only from
   this Mac), `MAIL_DAILY_LIMIT`. Remove `BILLING_EMAIL_FROM` if it is there, and make `ADMIN_PASSWORD` at least 15
   characters and not a common one.
10. **Restart:** `docker compose up -d backend worker monitor` (the monitor's `mail` check reads the same settings),
    then `docker compose up -d --force-recreate frontend`.
11. **Checks:** Admin → Users → **Sign-up** shows Email, Bounce reports, Turnstile and Google configured and no
    warning (visitor addresses, payments not live, the bounce webhook, a weak admin password, draft policy pages).
    **Edit…** → **Send a test email…** to your address: it arrives, not in spam, and Gmail's "Show original" says SPF,
    DKIM and DMARC "PASS".
12. **Your own email:** as `bikash`, Settings → Account → **Add email…**, and open the confirmation link while signed in
    as `bikash` (receipts, reminders and checkout need it; an address saved earlier for reminders wasn't carried over).
13. **Open sign-up** (**Edit…** → **Open sign-up**, both methods; a warning that still stands needs **Open anyway**),
    then at `https://asknuvexusdoc.com` sign up once with a throwaway address and once with a Google account, and delete
    both users afterwards.

Turnstile can't run at `http://localhost:8080` of the live box (the widget lists only the public host), and Google
sign-in works only at the public address: sign up there. Before any announcement or paid promotion, move to Resend Pro
(below).

### Email

- **Provider:** Resend's free tier (3,000 a month, 100 a UTC day) over SMTP, `smtp.resend.com` port 465 with implicit
  TLS, from `no-reply@asknuvexusdoc.com` with replies to `support@`. Every email to users goes through it:
  verification, "you already have an account", password reset, email confirmation, security notices (password changed
  or added, email changed or undone, Google sign-in added or removed), receipts with the PDF attached, and plan
  reminders. Plain text plus one HTML frame, with no images, tracking pixels or click tracking. Alerts keep their own
  `ALERT_*` channels (section 8), so a paused Resend account is still reported; ntfy or a webhook suits them better than
  the same Resend account.
- **The outbox and the worker:** a request only adds a row to the `email_outbox` table, which holds no token, link or
  secret. The worker's mail loop sends one mail at a time and mints the link's token only then. A temporary failure is
  retried after 1 minute, 5 minutes, 30 minutes, 2 hours and 6 hours; a refused SMTP login pauses all sending for 15
  minutes. Each message carries an idempotency key (`MAIL_IDEMPOTENCY_HEADER`), so a retried duplicate is dropped. A
  finished job keeps only a hash of its address, and the row is deleted 48 hours later. While the Mac sleeps, mails wait
  in the outbox and go out when it wakes.
- **The daily cap:** `MAIL_DAILY_LIMIT` (80, below Resend's 100) mails per UTC day, shared by who can cause the mail:
  visitors and accounts that never paid (sign-up mails, their confirmations and notices) up to 40 %, password resets up
  to 30 %, and everything but billing together up to 80 %, so receipts and plan reminders always keep at least 20 %.
  Past its share, email sign-up and forgot password answer "We can't send emails right now." (sign-up offers Google)
  until 00:00 UTC, 05:30 IST. The Sign-up card shows "About N sign-up emails left today"; the monitor warns at 80 %.
- **Bounces and complaints:** Resend's signed webhook and SMTP refusals (550 to 553) put the address on a suppression
  list (a bounce for 30 days, a complaint until the address is confirmed again). The worker doesn't mail a suppressed
  address, except one verification or confirmation a day that someone just asked for. 3 bounces that are at least 2 %
  of the day's mail pause sign-up mail until 00:00 UTC (Google sign-up keeps working) and warn. Without the webhook only
  SMTP refusals count, which miss most bounces, and the Sign-up card warns while sign-up is open.
- **What users can switch off:** receipt emails and plan reminder emails (Settings → Plan & billing). Verification,
  reset and security emails have no opt-out.
- **If Resend pauses the account** (the runbook, brief 17.2): switch to Brevo. Add its domain records from its
  dashboard, turn "Block unknown IP addresses" off, set `MAIL_SMTP_HOST=smtp-relay.brevo.com`, `MAIL_SMTP_PORT=587`,
  `MAIL_SMTP_SECURITY=starttls`, its SMTP login and key and an empty `MAIL_IDEMPOTENCY_HEADER=`, and run `docker compose
  up -d backend worker monitor`. Bounces are then known only from SMTP refusals, and Brevo's free mail carries its
  branding.
- **Before any announcement or paid promotion, move to Resend Pro** ($20 a month, no daily cap) and raise
  `MAIL_DAILY_LIMIT`. After two weeks of clean DMARC reports at `dmarc@`, change `_dmarc` to `p=quarantine`, later to
  `p=reject`.

### Security and privacy

- **No account enumeration:** sign-up, resend and forgot password do the same work for every address, never read the
  users table and answer the same 202; the worker decides afterwards what to send. Sign-in has one message for every
  failure ("Incorrect email, username or password.").
- **Emailed links:** 32 random bytes, minted when the mail is sent, stored only as a SHA-256 hash together with the hash
  of the address they went to, carried in the link's fragment (neither nginx's log nor a Referer sees it) and spent
  once by a click on our page. Lifetimes: sign-up 24 hours, email confirmation 24 hours, password reset 30 minutes,
  undo of an email change 7 days. Changing the email revokes the account's reset and confirmation links, and a new
  password revokes its reset links. The link pages send `Referrer-Policy: no-referrer`.
- **Turnstile only on the signed-out pages** `/login`, `/signup` and `/forgot-password`: nginx serves exactly those with
  their own CSP, which adds `https://challenges.cloudflare.com` to `script-src` and `frame-src`; every other page, the
  signed-in app included, keeps the strict CSP. The widget loads only after the server said nobody is signed in in that
  tab, and leaving those pages for the app is a full page load. Sign-up, resend and forgot password always need the
  check (and refuse while Cloudflare can't answer); sign-in only after repeated failures, and falls back to the hard
  limits during an outage. Test keys and fake endpoints stop the backend while `PUBLIC_BASE_URLS` holds the public
  address.
- **Throttles**, per network (an IPv4 address or an IPv6 /64, plus the /24 or /48 at four times the limit; a 429 with
  `Retry-After`): sign-up 20 an hour and 50 a day, resend and forgot password 10 an hour each, the emailed-link pages 30
  an hour, Google sign-in starts 20 a minute. Per address and kind of mail (silent; Gmail dots and `+tags` folded): 1 a
  minute, 3 an hour, 6 a day. Sign-in: 10 failures per account and 100 per network (400 per /24 or /48) in 15 minutes.
  nginx adds a coarse 30 requests a minute per address on the public auth endpoints. All of them need
  `NGINX_REAL_IP_FROM`, or every visitor shares one address.
- **Passwords:** at least `PASSWORD_MIN_LENGTH` characters, any characters, no composition rules; refused when common
  (a bundled list), when their letters are just the app's name, the username or the email, or when Have I Been Pwned's
  range check finds them (only the first 5 characters of the SHA-1 leave, padded; an outage lets the password through).
  The admin's forms apply the same rule.
- **Google:** a project of its own, the server-side code flow with PKCE, state, nonce and a browser-binding cookie, and
  the ID token's signature checked against Google's keys. Only the Google account's id, the email, whether it is
  verified and the Workspace domain are read; no Google token is kept and no Google script runs on our pages. Google
  sign-in never grants Gmail access.
- **Logs:** mail is logged by job and kind, never by address, token, link or SMTP credentials; addresses elsewhere in
  logs and account events are masked (`j***@example.com`). nginx never logs the Google callback and logs the bounce
  webhook by path only.
- **Kept:** a pending sign-up 7 days at most; an unconfirmed address 24 hours after its last mail; link hashes a day
  after use or expiry; an outbox row's address until the job finishes (then only its hash), the row 48 hours more;
  suppressions as above; account changes (`auth_events`, in the user's export) with approximate IPs for 90 days and the
  rows for 365 days or until the account is deleted; usernames of deleted accounts, which are never reissued; sign-ins
  as before (the newest 20, now with the method). Never stored: Google's name, picture or tokens, Turnstile tokens, or
  links. The privacy policy names the processors: Resend (every email to users), Cloudflare (Turnstile and the tunnel),
  Google (sign-in), Have I Been Pwned (the hash prefix) and Razorpay.

## 11. Security notes

- The UI is bound to `127.0.0.1` only. Before exposing it on a network, put it behind HTTPS and
  set `COOKIE_SECURE=true` (see `docs/HANDOFF.md`, next steps).
- Sessions are httpOnly, SameSite=Strict cookies holding a signed JWT (`__Host-rag_session` behind HTTPS; a `Bearer`
  header also works, for scripts). Changing a password, a password reset, undoing an email change or deactivating a
  user ends their sessions immediately; finishing a sign-up or signing in with Google starts a new one. **Log out of all
  other sessions** (Settings → Account) ends every other session and API token without asking for the password, so
  the session that asks keeps its original expiry and is never extended.
- Each successful sign-in is recorded for that user only: an approximate address (IPv4 with the last part hidden, IPv6
  as its /48 network) and the browser, OS and device parsed from the User-Agent, which isn't stored. The newest 20
  are kept; they appear only in the user's Settings and export (not in the admin page) and are deleted with the user.
- The data export holds only the caller's own data (the admin's too) and never passwords, tokens or keys. It is a POST,
  so nginx's cross-site check applies, and one export per user runs at a time.
- nginx refuses state-changing requests that the browser marks as same-site or cross-site
  (`Sec-Fetch-Site`), because SameSite cookies ignore ports and other `localhost` pages count as
  same-site. It also sets a strict CSP, HSTS and other security headers, caps request bodies at 1 MB,
  and checks the session before accepting an upload body. The CSP allows scripts and fonts only from the app itself
  (only `/login`, `/signup` and `/forgot-password` also allow Cloudflare's Turnstile, section 10), which is why the
  theme is applied before the first paint by a static file (`frontend/public/theme-boot.js`) rather than an inline
  script, and why the fonts are bundled.
- Failed sign-ins are throttled per account (10 per 15 minutes, whether the email or the username is typed) and per
  network (100; 400 per IPv4 /24 or IPv6 /48), with the visitor's address from the tunnel (`NGINX_REAL_IP_FROM`). On
  `http://localhost:8080` every client has the same address, so there the per-network limit acts as one global limit.
- The admin account is defined only by `.env` and has no email, Google sign-in or password reset. Changing
  `ADMIN_USERNAME` demotes and deactivates the old admin account on the next start; the backend refuses to start when
  it names an account that signed up itself or has an email or Google sign-in. Changing `ADMIN_PASSWORD` signs out the
  admin's existing sessions.
- Retrieved passages are treated as untrusted data in the prompt, so instructions inside documents
  are not followed. As with any RAG system, this lowers the risk but doesn't eliminate it. The vision
  prompt does the same for text inside page images.
- PDF pages for the vision model are rendered in a separate process with no API keys in its environment,
  a 1 GiB memory limit (enforced on Linux, so in Docker) and 30 s per page. Page images of users' private
  PDFs go to the active vision model's provider.
- Files the assistant creates are rendered in a child process with an empty environment and memory, CPU
  and file-size limits, killed after `GENERATED_FILE_TIMEOUT_SECONDS`. They land only in the requesting
  user's private documents and contain no active content:
  - PDFs have no JavaScript, actions or forms.
  - DOCX files have no macros.
  - XLSX files have no formulas. Text that starts with `=`, `+`, `-`, `@`, a tab or a carriage return is stored as
    quote-prefixed text.
  - Markdown files are rewritten so they load nothing: images become text and HTML is escaped.

  Links show their host. The prompt allows the tool only when the user's own message asks for a file, never
  because a document does.
- Images the assistant generates follow the same rules: the tool is used only when the user's own message asks for
  an image, and each image lands only in that user's private documents. The provider's bytes are decoded only in
  that child process (PNG, JPEG or WebP only, size-checked before decoding), re-encoded without their metadata and
  marked as AI-generated (XMP, IPTC `trainedAlgorithmicMedia`). Only image data is accepted, so no URL from a model
  response is ever fetched. Images embedded in files reach the renderers only as copies the server made, never as a
  path or URL from the model. `GET /api/documents/{id}/image` serves them inline with the listing's visibility,
  `nosniff`, a sandbox CSP and `Cache-Control: private, no-cache`. Prompts are shown only to the image's owner (not
  in Admin → Documents, not in logs), but they go to the image model's provider (OpenAI) even when the chat model is
  from another provider.
- Downloads (`GET /api/documents/{id}/download`) follow the listing's visibility rules (anything else is a 404)
  and are sent as attachments with `nosniff`, `no-store` and a sandbox CSP. PDF, Office and generated image files
  get their own content type; everything else, Markdown and HTML included, is `application/octet-stream`.
- **Plugins (Gmail):**
  - OAuth uses the authorization-code flow with state, PKCE (S256), and a one-time server-side state bound to the
    browser by an `HttpOnly` cookie. The session cookie stays `SameSite=Strict`.
  - Redirect URIs are built only from `PUBLIC_BASE_URLS` entries matching the request's `Origin`, never from `Host`.
  - Refresh and access tokens are encrypted with `TOKEN_ENCRYPTION_KEY` (Fernet), bound to their user and plugin, and
    never logged. The key is separate from `SECRET_KEY`.
  - A user only ever sees and uses their own connection. Other users' resources answer 404, and plugin problems answer
    409 or 403, never 401.
  - Email text reaches the model as untrusted data in `<email>` blocks. That is only defense in depth; the guards are:
    - drafts, send proposals and imports (and files or images in an answer that read email) need a separate request
      check that sees only the user's own messages;
    - every Gmail action re-checks that the plugin, the capability and the connection are still on;
    - sending needs the user's click on the exact previewed content (hash-checked, once, before it expires, within a
      daily cap);
    - the card flags recipients the user never typed, unnamed attachments, Bcc and the email being replied to.
  - Access logs keep plugin paths without their query strings, the OAuth callback isn't logged by nginx, and the Gmail
    browser's search goes in a POST body. Logs never hold tokens, codes, search terms, subjects, addresses or email
    text.
  - The admin sees counts, never mail content or account emails. Disconnecting revokes the grant at Google unless
    another app user shares the same Google account.
- **Plans and payments:**
  - Access is decided by the backend on every route (`billing.entitlements`, `deps.get_entitled_user`); the UI only
    mirrors it. nginx asks the backend before accepting an upload body from a user without a plan.
  - No Razorpay script runs on the app's pages, so the CSP is unchanged: the browser goes to Razorpay's page and back.
  - A plan starts only on a captured payment fetched from Razorpay's API, after the return's signature check, a
    webhook's HMAC over the raw body, or the worker's polling, and only when its amount, currency, order and mode match
    the stored order. Each Razorpay payment is applied once. A webhook's body never changes anything by itself; events
    are deduplicated by id and by body.
  - A direct-UPI plan starts only on the admin's confirm, which must echo the order's UTR, and applies once under the
    user's and the order's row locks. A UTR is unique among waiting, confirmed and reviewed orders (a partial unique
    index) and checked against recorded bank transfers both ways. The payment request and its QR codes are built only
    from the validated settings and the stored order; the QR images are served with `sandbox` and `no-store`.
  - Amounts are computed on the server only, in integer paise. Every per-user money or access change takes the user's
    row lock first, and no Razorpay call runs inside a database transaction.
  - The key secret and webhook secrets are never logged or shown (Admin → Billing shows a key hint). The return path is
    logged without its query, and nginx rate-limits the return and webhook paths.
  - Razorpay gets the amount and the user's numeric id, no name, email or phone. The app stores the payment's method,
    never the payer's email, phone, UPI id, card or bank.
- Secrets are never logged; provider errors are turned into user-facing messages (`llm.friendly_error`).

## 12. Known limitations

- Scanned PDF pages are read by the vision model, which costs tokens: with Claude Sonnet 5.5 about $0.007 for
  a simple page (measured) and up to about $0.03 for a dense one, up to `VISION_MAX_PAGES` pages per document
  and the plan's vision tokens per user and period. The allowance is checked when a document starts
  processing, so documents already in progress can take a user past it. There's no local OCR engine.
- Charts drawn as vector graphics (most Office, LaTeX and matplotlib exports) aren't detected; only raster
  images are. Pages whose content is over 2 MiB aren't checked for images.
- Only PDFs go to the vision model: images in DOCX and PPTX files aren't read yet. Password-protected PDFs
  aren't supported.
- Generated PDFs support Latin, Greek, Cyrillic and ten Indian scripts (Noto fonts are bundled). A few
  characters of another script (Chinese, Arabic, Hebrew, Thai, …) appear as □ with a note; more than 2 % of
  the letters and the PDF is refused with a hint to create a docx or md file, which keep every script. Emoji
  are left out of PDFs.
- Generated files have no charts, and only PDF and Word files can hold images (those generated in the same
  conversation). An earlier file can't be converted to another format: "make it a Word document too" writes the
  content again from the documents, which costs another file's tokens.
  Citations of a generated PDF have no page number, because it is indexed from its Markdown source.
- One answer that writes files or images makes up to `GENERATED_FILES_PER_ANSWER` + `GENERATED_IMAGES_PER_ANSWER` +
  2 model calls, each re-sending the context; with a limited chat allowance, no further call starts once the answer has
  used what was left, but up to two answers running at once can still go past the allowance.
- The file tool costs tokens even when no file is asked for: about 1,550 input tokens per model call with a
  capable model. Its calls may also use up to 48,000 output tokens instead of 16,000. Set `CHAT_FILE_TOOLS=false`
  if you don't need files.
- Images: the image tool adds about 1,040 input tokens per model call with a capable model, image or not
  (`CHAT_IMAGE_TOOLS=false` removes it). Only OpenAI and Azure OpenAI GPT image models work; image prompts go to
  OpenAI even when the chat runs on Anthropic. Three sizes only (1024×1024, 1536×1024, 1024×1536), no transparent
  backgrounds, no editing or variations of an image, and no image uploads. Images in one answer are made one after
  another. Re-encoding drops the provider's C2PA provenance data; the XMP marker is what says the image is
  AI-generated. PNG (the default) takes about 0.7 to 3 MB per image.
- The admin can't download users' private documents or view their images; downloads follow the same visibility as
  the document list.
- **Gmail has been tested only against a fake Google server**, never against real Google; `docs/gmail-setup.md`
  (section 13) lists what to check on the first real run.
  - Plain-text emails only (no HTML composition).
  - No labels, archiving, deleting or marking read.
  - Imported threads aren't refreshed; a changed thread imported again becomes a second document.
  - "Open in Gmail" links use `/mail/u/0/`, the browser's first signed-in Google account.
  - In Google's testing mode, connections end after 7 days.
  - The request check is a model judgement on the action type, not its arguments; the flags on the cards and the Send
    click are what remains.
  - The plugin locks are per process, so run one backend process.
- Deleting a user deletes their usage history too (chat, embedding, vision and images), so their costs leave the
  usage report. Their billing records (orders, payments, refunds, disputes, receipts and their events) stay, with the
  username, for the accounts; a user who never had an order leaves no billing events behind.
- **Plans and billing:**
  - Razorpay hasn't been used with real keys yet, not even in test mode: every suite uses a fake. The checks to run
    in test mode are in `docs/briefs/plans-billing.md` (14.2).
  - Webhooks need the public HTTPS domain. Until then payments are confirmed by the return page and the worker's
    polling, and refunds made in Razorpay's Dashboard and disputes don't reach the app.
  - The policy pages are drafts, and they are rendered in the browser: a website check that doesn't run JavaScript
    sees an empty page.
  - No GST and no tax invoices; no automatic renewal (each period is paid again); no coupons or annual plans. A new
    Razorpay account may cap UPI at ₹2,000 per payment, and test mode allows about 30 payment links.
  - Direct UPI is deployed but switched off until the owner sets it up, and no real UPI payment has gone through it: the
    ₹1 tests (section 9, "Direct UPI") settle what the apps do with the link and the note. The confirm dialog can't see
    the bank: the admin finds the credit by hand. "Open UPI app" can fail in some apps for a personal UPI ID (the QR
    code and the copied ID still work).
- **Sign-up and email:**
  - Built and tested against fakes only: no real Resend, Turnstile or Google sign-in keys have been used yet, so the
    checks of `docs/briefs/signup-auth.md` 18.2 (Turnstile under the CSP, Google's consent screens and step-up,
    Resend's idempotency header and webhook payload, in-app browsers, the headers through the tunnel) wait for them.
  - Resend's free tier is 100 mails a day: a bot wave or a busy day uses the sign-up share (32 of 80), and email
    sign-up then answers "We can't send emails right now" until 00:00 UTC, while Google sign-up, resets and receipts go
    on. Bounces of 4 % pause the whole Resend account, receipts included (the runbook in section 10 switches to Brevo).
  - Everything runs on the owner's Mac: while it sleeps, mails wait and go out on waking, and links in mails sent before
    a long sleep may have expired.
  - Some privacy blockers stop Turnstile; those visitors can still sign up with Google, and sign-in never depends on
    it. A new Google project may rate-limit new sign-ins (an increase can be requested).
  - A stranger can use up one address's sign-up mails for the day (not its resets or confirmations); its owner can
    still sign up with Google.
  - No self-service account deletion (users ask through Contact), no bulk actions on new accounts, and no admin action
    to lift a suppressed address or Settings notice that an address bounces yet.
- Settings → Usage shows only the current period (the admin's Usage tab has history). The export has no document files
  (each document has Download). There is no session list with per-session sign-out, only "Log out of all other
  sessions", and users can't delete their own account. Accents are the five presets. Code blocks have no syntax
  colours. The bundled fonts cover Latin only; other scripts use the system fonts.
- Sign-ins at `http://localhost:8080` read "Via a local network or proxy" in the history; through the tunnel nginx
  takes the visitor's address from `CF-Connecting-IP` (`NGINX_REAL_IP_FROM`).
- Legacy `.doc`, `.ppt` and `.xls` files must be saved in the modern format first.
- The backend migrates the database while it starts (Alembic, `backend/migrations/README.md`), and migrations only go
  forward. To roll a deploy back, check out and build the previous release, then restore the backup taken before the
  deploy with `scripts/restore.sh`, which refuses a backup whose schema revision that release doesn't know or would
  migrate. Releases up to `c1da1d0` predate migrations: they ignore the schema's revision and start on any schema
  without a warning, so go back to one only with a backup at revision 0002 or from before migrations
  (`backend/migrations/README.md`, "Rolling back"). A release before plans needs the backup taken before migration
  0003, and one before sign-up the backup taken before 0004 (`20261101T031731Z-manual` on the live stack). Rolling the
  code back to a release before images needs the SQL in
  `docs/HANDOFF.md` ("Images from chat") first, and back to a release before plugins the SQL in
  `docs/briefs/plugins-gmail.md` (section 3.3).
- Run exactly one worker container. Scale with `WORKER_CONCURRENCY` instead.
- Backups stay on the app's machine until you copy them off it (section 8, "Off-site copies"), and they never include
  `.env`. Alerts are only logged and shown in Admin → System until an alert channel is set. The monitor runs inside
  Docker, so nothing reports Docker or the machine stopping unless the host watchdog is installed or an external
  uptime check watches `/api/health/ready`.

The full list, with the open risks, is in `docs/HANDOFF.md`.
