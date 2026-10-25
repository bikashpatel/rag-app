# RAG App — Build Specification (shared contract)

This file is the single source of truth for everyone building this app. Backend
and frontend are built in parallel against it, so **field names, paths, and
event names below are binding**. If you must deviate, change this file in the
same edit and say so in your report.

## 1. Overview

Single-tenant, locally hosted Retrieval-Augmented-Generation chat app.

- **Stack:** FastAPI (Python 3.13) · Postgres 17 · Redis 7.4 · Qdrant 1.19 ·
  React 19 + Vite 8 (TypeScript 5.9) · nginx · Docker Compose.
- **LLM access:** everything goes through **LiteLLM** (`litellm.acompletion`,
  `litellm.aembedding`, `litellm.aimage_generation`). Provider keys come from env (`OPENAI_API_KEY`,
  `ANTHROPIC_API_KEY`, and any other key LiteLLM understands).
- **Auth:** username + password, argon2 hashes, JWT in an **httpOnly,
  SameSite=Strict cookie** (`rag_session`). A `Authorization: Bearer <jwt>`
  header is also accepted (for scripts). Only the admin creates users.
- **Admin:** exactly one admin, credentials from `.env`
  (`ADMIN_USERNAME`/`ADMIN_PASSWORD`), synced on every backend start.
- **Documents:** private per user + a shared knowledge base (admin uploads
  with scope `shared`). Retrieval searches the user's private docs **and** all
  shared docs (optionally narrowed to a selection).
- **Usage controls (per user, set by admin):** monthly token budget, requests
  per minute, allowed models, upload limits (max file MB, max documents, max
  total storage MB). **`0` means unlimited** for `monthly_token_limit`,
  `rate_limit_per_min`, `max_documents`, `max_storage_mb`. `max_upload_mb`
  is always ≥ 1 and capped by `MAX_UPLOAD_MB_HARD`. Plus a **monthly image
  count** (`monthly_image_limit`, default `DEFAULT_MONTHLY_IMAGE_LIMIT`, `0` =
  unlimited). The admin bypasses all usage checks.
- **Monthly budget** counts **chat (LLM) tokens only** (prompt + completion)
  in the current UTC calendar month. Embedding and vision tokens are recorded
  and shown but don't count toward the budget. Vision tokens have a separate
  monthly allowance per user, `VISION_MONTHLY_TOKEN_LIMIT` (section 7; the
  admin is exempt).
- **Files from chat:** when the user asks, the assistant creates a PDF, Word
  (`.docx`), Excel (`.xlsx`) or Markdown (`.md`) file with the `create_document`
  tool (chat models with function calling only). Each file becomes one of the
  user's private documents, indexed like an upload, and the answer shows a file
  card with a Download button (section 6, Chat; brief
  `docs/briefs/document-generation.md`).
- **Images from chat:** when the user asks, the assistant generates an image
  with the `create_image` tool and the active **image model**. The image is shown
  in the answer (with a full-size viewer and Download), saved as one of the
  user's private documents (indexed by its prompt and description), and can be
  embedded in PDF and DOCX files the assistant creates in the same conversation
  (section 6, Chat; brief `docs/briefs/image-generation.md`). Image costs are
  `image` usage events and don't count toward the token budget.
- **Plugins:** connectors the admin turns on (Admin → Plugins) and each user
  connects to their **own** account (Settings → Plugins & data, OAuth 2.0);
  every user sees every plugin with its status in the top bar's Plugins menu;
  nobody can see or use another user's connection. The first plugin is
  **Gmail**: search and read email in chat (emails are cited like passages),
  import threads and attachments into the user's documents, create drafts on
  explicit request, and **propose** emails that only the user's click on Send
  sends (section 6, Plugins and Chat; brief `docs/briefs/plugins-gmail.md`).
- **Account and settings:** each user's preferences (theme Light / Dark /
  System, accent colour, chat font size, default chat model, Enter to send) are
  saved in their account and applied without a reload; Settings also shows the
  account (sign-in history, password, "log out of all other sessions"), this
  month's usage against the limits, plugins, a data export and "delete all my
  chats" (section 6, Account; brief `docs/briefs/ui-shell-settings.md`).

## 2. Repository layout and ownership

```
rag-app/
  SPEC.md  README.md  docker-compose.yml  .env.example  .gitignore
  backend/
    Dockerfile  .dockerignore  pytest.ini
    requirements.in  requirements-dev.in      hand-edited top-level pins
    requirements.txt  requirements-dev.txt    hashed locks generated from the .in files (never edit)
    app/
      __init__.py
      main.py               FOUNDATION  app factory, lifespan, router wiring, /api/health
      config.py             FOUNDATION  Settings (pydantic-settings)
      db.py                 FOUNDATION  async engine, SessionLocal, Base, get_db, init_db
      models.py             FOUNDATION  ORM models (section 4)
      security.py           FOUNDATION  argon2 + JWT helpers
      deps.py               FOUNDATION  get_current_user, get_current_admin
      redis_client.py       FOUNDATION  get_redis(), queue key names
      bootstrap.py          AUTH-ADMIN  admin sync + model seeding
      worker.py             INGESTION   ingestion worker process (python -m app.worker)
      services/
        __init__.py
        llm.py              FOUNDATION  LiteLLM wrappers: stream_chat, embed_texts, read_image, generate_image, test_model, compute_cost
        usage.py            FOUNDATION  rate limit, budget check, record_usage, usage_summary
        vectorstore.py      FOUNDATION  Qdrant wrapper
        model_registry.py   FOUNDATION  model lookup helpers
        storage.py          FOUNDATION  upload paths, delete_document_completely
        parsing.py          INGESTION   file -> text segments (+ PDF pages selected for the vision model)
        pdf_images.py       INGESTION   PDF image measurement (pypdf) and page rendering through the child below
        pdf_render_child.py INGESTION   standalone PDFium renderer, run as a memory- and time-limited child process
        vision.py           INGESTION   vision prompts, reading pages (limits, stop checks, page cache), vision Test
        chunking.py         INGESTION   segments -> chunks
        ingestion.py        INGESTION   enqueue_document, process_document
        rag.py              CHAT        retrieval + prompt building
        document_tool.py    CHAT        create_document tool: schema, validation, render limits, storing the file as a document
        image_tool.py       CHAT        create_image tool: schema, validation, the monthly limit's reservation, generating, storing the image as a document, the image model Test
        filegen/            CHAT        Markdown / sheets -> PDF (fpdf2), DOCX, XLSX, MD bytes, and image validation (images.py) (pure, no app imports; run by filegen/child.py in a time- and memory-limited child process)
        account.py          ACCOUNT     preferences read/write, User-Agent parsing, approximate IPs, the caller's usage report
        export.py           ACCOUNT     the caller's data export (ZIP)
      fonts/                CHAT        Noto Sans TTFs for generated PDFs (SIL OFL 1.1, fonts/OFL.txt)
      schemas_plugins.py    PLUGINS     plugin API shapes (the Gmail ones are in plugins/gmail/schemas.py)
      schemas_account.py    ACCOUNT     the caller's account shapes (Preferences itself is in schemas.py, with Me)
      plugins/              PLUGINS     framework: base (interfaces), registry, crypto (token encryption), http, oauth,
                                        connections (tokens, refresh, revoke), audit, intent (request check), answer
                                        (sources and cards of one answer), importing (store_import), tasks
                                        (tracked background work, drained at shutdown)
        gmail/              GMAIL       plugin, client (REST), mime, links, render, tools (chat), sends, importer, router
      routers/
        __init__.py
        auth.py             AUTH-ADMIN  /api/auth/*
        me.py               ACCOUNT     /api/me/* (preferences, account, usage, export)
        models.py           AUTH-ADMIN  /api/models
        admin.py            AUTH-ADMIN  /api/admin/*
        admin_plugins.py    PLUGINS     /api/admin/plugins/*
        plugins.py          PLUGINS     /api/plugins/* (and each plugin's own router under /api/plugins/<id>)
        documents.py        INGESTION   /api/documents/*
        conversations.py    CHAT        /api/conversations/*
        chat.py             CHAT        /api/chat/stream
    tests/                  (in-process API tests, see section 9)
  frontend/
    Dockerfile  .dockerignore  package.json  package-lock.json  vite.config.ts
    tsconfig.json  index.html                 index.html loads public/theme-boot.js first in <head>
    nginx.conf  upload-limit.envsh            nginx template; its upload body limit follows MAX_UPLOAD_MB_HARD;
                                               /theme-boot.js is cached for 5 minutes (no revalidation per load)
    public/theme-boot.js                       FOUNDATION  classic script: applies the cached theme before the first paint
    src/
      main.tsx  App.tsx                        FOUNDATION (App.tsx routes: SETTINGS)
      api/client.ts  api/types.ts              FOUNDATION
      auth/AuthContext.tsx                     FOUNDATION (session, preferences)
      theme/appearance.ts                      FOUNDATION (theme engine: section 8)
      styles/global.css, styles/fonts.css      FOUNDATION (design tokens + primitives; bundled fonts in assets/fonts/)
      lib/settingsLink.ts, lib/appEvents.ts    FOUNDATION (Settings URL helpers; app-wide events)
      components/ui/*                          FOUNDATION (Modal, Spinner, ErrorBoundary, FullPageState, Menu, Avatar, SegmentedControl,
                                               Switch, Meter, PasswordInput, icons)
      pages/LoginPage.tsx, pages/login.css     FOUNDATION (CSS prefix login-)
      pages/ChatPage.tsx + components/chat/*   FE-CHAT (top bar, model, Plugins and avatar menus, composer and its "+" menu)
      components/documents/*                   FE-DOCS
      pages/AdminPage.tsx + components/admin/* FE-ADMIN
      components/connections/*                 FE-CONN (connection cards, Gmail browser and import; CSS prefix conn-)
      components/settings/*                    FE-SETTINGS (the Settings dialog; CSS prefix settings-)
      **/*.test.ts(x), test/                   Vitest tests next to the code; shared test helpers (section 9)
  integration/              full-stack tests (section 9): run.sh, stack.sh, gen_env.py, compose.it.yml,
                            fake_llm/ (a fake OpenAI-compatible server), fake_google/ (serves the fakes in
                            backend/tests), tests/
  e2e/                      Playwright browser tests against the running stack (section 9)
  .github/workflows/ci.yml  CI: every suite (section 9)
```

Each router module exposes `router = APIRouter(prefix="/<area>", tags=[...])`;
`main.py` mounts them all under `/api`. Each owner may add private helper
modules inside their own area (e.g. `app/schemas_chat.py`, or
`components/chat/useChatStream.ts`). Pydantic request/response schemas live in
the router module that uses them (or a sibling module owned by the same group).

## 3. Configuration (env vars)

See `backend/app/config.py` for defaults. Important ones:

| Var | Meaning |
|---|---|
| `SECRET_KEY` | JWT signing key, ≥ 32 chars, required |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | admin login (password ≥ 8 chars) |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | provider keys read by LiteLLM |
| `POSTGRES_USER/PASSWORD/DB/HOST/PORT` or `DATABASE_URL` | database |
| `REDIS_URL`, `QDRANT_URL`, `QDRANT_API_KEY` | infra (`QDRANT_URL=:memory:` = in-process, tests only) |
| `UPLOAD_DIR` | where uploaded files live (shared volume between backend and worker) |
| `COOKIE_SECURE` | set `true` behind HTTPS |
| `DEFAULT_*` | defaults pre-filled when admin creates a user |
| `MAX_UPLOAD_MB_HARD` | absolute per-file cap. nginx accepts this + 10 MB only on `POST /api/documents` (after an auth check) and 1 MB on every other request |
| `RAG_TOP_K`, `CHUNK_SIZE`, `CHUNK_OVERLAP`, `HISTORY_MESSAGES` | RAG tuning |
| `LOGIN_MAX_ATTEMPTS` (10), `LOGIN_MAX_ATTEMPTS_PER_IP` (100), `LOGIN_WINDOW_SECONDS` (900) | login throttle |
| `VISION_MAX_PAGES` (50) | PDF pages per document read with the vision model (section 7, step 4) |
| `VISION_CONCURRENCY` (3), `VISION_MAX_IN_FLIGHT` (4) | vision calls in flight per document, and in the whole worker |
| `VISION_TIMEOUT_SECONDS` (1800) | time a document may spend reading pages with the vision model; no call starts after it |
| `VISION_MONTHLY_TOKEN_LIMIT` (2000000) | vision tokens per user per UTC calendar month, checked when a document starts (0 = unlimited; the admin is exempt) |
| `VISION_MIN_TEXT_CHARS` (40), `VISION_MIN_IMAGE_RATIO` (0.1) | a PDF page with fewer non-whitespace text-layer characters has "no text layer"; images must cover at least this share of a page to be read |
| `VISION_MAX_IMAGE_PX` (1568, at most 2576), `VISION_MAX_OUTPUT_TOKENS` (8192) | long edge of a rendered page image (a cost choice); `max_tokens` per page, thinking included, when the vision model has no `max_output_tokens` |
| `WORKER_MEM_LIMIT` (4g; Compose only) | memory limit of the worker container (`mem_limit` and `memswap_limit`) |
| `CHAT_FILE_TOOLS` (true) | offer the `create_document` tool to chat models that support function calling (section 6, Chat) |
| `GENERATED_FILES_PER_ANSWER` (3) | files one answer may create; the answer makes at most this + 2 model calls |
| `GENERATED_FILE_MAX_CHARS` (100000) | characters of a generated file's content (or of all its xlsx cell text) |
| `GENERATED_FILE_MAX_OUTPUT_TOKENS` (48000) | `max_tokens` of a chat call that offers the tool (a model's own `max_output_tokens` wins; lowered to LiteLLM's known output cap) |
| `GENERATED_FILE_TIMEOUT_SECONDS` (60) | time to render one generated file in its child process, waiting for a render slot included; the child is killed after it |
| `GENERATED_FILE_CONCURRENCY` (2) | generated files rendered at once (child processes) in one backend process |
| `CHAT_IMAGE_TOOLS` (true) | offer the `create_image` tool when a usable image model is active and the chat model supports function calling (section 6, Chat); `false` turns image generation off and keeps existing images |
| `GENERATED_IMAGES_PER_ANSWER` (3) | images one answer may generate |
| `DEFAULT_MONTHLY_IMAGE_LIMIT` (30) | images per user per UTC calendar month: the user form's default and the value every existing user gets once on the first start with images (0 = unlimited; the admin is exempt) |
| `IMAGE_MAX_QUALITY` (medium) | highest quality `create_image` may use: `low`, `medium` or `high` |
| `IMAGE_OUTPUT_FORMAT` (png) | format asked of the image model: `png`, `jpeg` or `webp`; the image is stored as returned |
| `IMAGE_TIMEOUT_SECONDS` (180) | one image call, waiting for an image slot and quick provider retries included; the call is abandoned after it |
| `IMAGE_CONCURRENCY` (3) | image calls at once in one backend process |
| `PUBLIC_BASE_URLS` (`http://localhost:8080`) | comma-separated origins the app is opened at; OAuth redirect URIs are `<origin>/api/plugins/<id>/oauth/callback` for these only, chosen by matching the request's `Origin` header (never `Host`) |
| `TOKEN_ENCRYPTION_KEY` | comma-separated Fernet keys encrypting users' OAuth tokens (the first encrypts, all decrypt; every start re-encrypts rows under the first key); independent of `SECRET_KEY`; empty or invalid: OAuth plugins are not configured. Remove an old key only when Admin → Plugins shows 0 connections on an older key |
| `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` | Gmail's Google "Web application" OAuth client; either empty: Gmail is not configured |
| `GOOGLE_OAUTH_TESTING_MODE` (true) | the Google app is in "Testing": only its test users can connect, and Google ends refresh tokens after 7 days (connections older than that read as reconnect needed; shown to users) |
| `GOOGLE_OAUTH_AUTHORIZE_URL`, `GOOGLE_OAUTH_TOKEN_URL`, `GOOGLE_OAUTH_REVOKE_URL`, `GMAIL_API_BASE_URL` | Google's endpoints by default; other values only for a fake Google (tests, smoke runs), shown as "test endpoints" to the admin |
| `GMAIL_TOOL_CALLS_PER_ANSWER` (5), `GMAIL_MAX_CHARS_PER_ANSWER` (40000) | Gmail tool calls, and characters of email text returned to the model, per chat answer |
| `GMAIL_DAILY_SEND_LIMIT` (20) | emails a user (the admin too) may send per UTC day |
| `GMAIL_SEND_CONFIRM_MINUTES` (30) | how long a proposed email can be sent |
| `GMAIL_MAX_ATTACHMENT_MB` (15, at most 18) | total attachment size of one draft or email (the built message must also stay under Gmail's 25 MB) |
| `GMAIL_IMPORT_MAX_ITEMS` (20) | threads plus attachments per import, from the Gmail browser or from chat |
| `EXPORT_MAX_MB` (100, 1 to 1000) | uncompressed content of one data export (`POST /me/export`); older conversations beyond it are left out, and the export says how many |
| `EXPORT_TIMEOUT_SECONDS` (80, 10 to 90) | time from the request to the end of the build of one data export (nothing is sent before the build ends); conversations not reached are left out the same way. At most 90: the Cloudflare tunnel drops a response that sends nothing for about 100 s |

## 4. Data model (SQLAlchemy, `app/models.py`)

All timestamps are timezone-aware UTC. Integer primary keys.

- **users**: `id, username (unique, stored lowercase), password_hash,
  is_admin, is_active, token_version, monthly_token_limit, rate_limit_per_min,
  max_upload_mb, max_documents, max_storage_mb, restrict_models,
  created_at, updated_at, last_login_at`. Relationship `allowed_models`
  (M2M via `user_allowed_models`). If `restrict_models` is false the user may
  use every enabled chat model; if true only `allowed_models`.
  Bumping `token_version` invalidates all of the user's sessions.
- **llm_models**: `id, display_name, litellm_model (e.g. "openai/gpt-5.4-mini",
  "anthropic/claude-sonnet-5-5", "ollama/llama3.1"), kind ("chat"|"embedding"|"vision"|"image"),
  enabled, is_default, api_base, api_key_env, max_output_tokens, temperature,
  input_cost_per_mtok, output_cost_per_mtok, embedding_dim, created_at,
  updated_at`. At most one `is_default` per kind. The default embedding model
  is the **active** one: all indexing and retrieval use it. The default vision
  model, when enabled, is the **active** vision model: ingestion uses it to read
  PDF pages (section 7); without one, PDFs are read from their text layer only.
  The default image model, when enabled, is the **active** image model: chat
  generates images with it; without one, `create_image` isn't offered.
  Vision and image models are never offered to users as chat models. `api_key_env` is
  the *name* of an env var holding the key (must match `^[A-Z][A-Z0-9_]*_API_KEY$`).
  Costs are USD per 1M tokens (image models: input = prompt text tokens,
  output = image tokens); when null, LiteLLM's cost map is used.
- **documents**: `id, owner_id → users (CASCADE), scope ("private"|"shared"),
  filename, stored_name (uuid + ext, in UPLOAD_DIR), content_type, size_bytes,
  sha256, status ("pending"|"processing"|"ready"|"failed"), error, num_chunks,
  num_pages, embedding_model_id → llm_models (SET NULL), created_at, updated_at`.
  A PDF the assistant created has its Markdown source stored next to it as
  `<stored_name>.source.md` (indexed instead of the PDF's text, section 7;
  removed with the document). An image the assistant generated (`image/png`,
  `image/jpeg` or `image/webp`; `.png`, `.jpg` or `.webp`) has three files next
  to it: `<stored_name>.source.md` (title, description, prompt; indexed),
  `<stored_name>.preview.webp` (long edge 1024) and `<stored_name>.thumb.webp`
  (long edge 256); sidecars don't count toward storage.
- **conversations**: `id, user_id → users (CASCADE), title, created_at, updated_at`.
- **messages**: `id, conversation_id → conversations (CASCADE), role
  ("user"|"assistant"), content, sources (JSON list, see Source and
  EmailSource: entries without `kind` are document passages), model_id,
  model_name, prompt_tokens, completion_tokens, error, created_at`.
- **usage_events**: `id, user_id → users (CASCADE), model_id (SET NULL),
  model_name, kind ("chat"|"embedding"|"vision"|"image"), prompt_tokens, completion_tokens,
  total_tokens, cost_usd, created_at`. Ingestion records at most one `embedding`
  and one `vision` event per document run, for the document's owner. Each
  generated image is one `image` event (prompt tokens = prompt text, completion
  tokens = image tokens), inserted before the image model is called (the monthly
  image count's reservation) and settled with the tokens and cost afterwards
  (estimated when the request was sent but no response came; deleted when
  nothing was billed). Each admin Test of an image model is one `image` event
  of the admin.
- **vision_pages**: `document_id → documents (CASCADE), page, mode
  ("transcribe"|"describe")` (together the primary key), `fingerprint, text,
  created_at`. The vision model's cleaned output for one page of one document
  (`text` "" = nothing to add). Later runs of the same document reuse a row
  while its `fingerprint` (a hash of the vision model's `litellm_model` and
  `api_base`, the prompt version, `VISION_MAX_IMAGE_PX` and the page
  `max_tokens`) matches; a successful run deletes the rows it didn't use.
  Never shared between documents, not even identical files.
- **app_state**: `key (primary key), value, updated_at`. Server flags, e.g.
  `vision_seeded`, `image_seeded` and `image_limits_backfilled` (section 6, Admin).
- **message_files**: `id, message_id → messages (CASCADE), document_id →
  documents (SET NULL), filename, format ("pdf"|"docx"|"xlsx"|"md"|"png"|"jpeg"|"webp"),
  size_bytes, created_at`. A file the assistant created in that answer, in
  creation order; filename, format and size are a snapshot, so the card can
  still be shown after the document is deleted (`document_id` NULL).
- **generated_documents**: `document_id` (primary key) `→ documents (CASCADE),
  created_at`. Marks a document the assistant created in a chat (inserted with
  the document); kept when the conversation is deleted. Source of
  `DocumentItem.generated` and of the label on its passages in later chats.
- **generated_images**: `document_id` (primary key) `→ documents (CASCADE),
  conversation_id → conversations (SET NULL), model_name, prompt, revised_prompt,
  alt_text, format ("png"|"jpeg"|"webp"), quality ("low"|"medium"|"high"),
  width, height, created_at`. An image the assistant generated (inserted with its
  document, which also gets a `generated_documents` row). Source of
  `DocumentItem.image` and `GeneratedFile.image`; `conversation_id` decides which
  images `create_document` may embed.
- **user_image_limits**: `user_id` (primary key) `→ users (CASCADE),
  monthly_image_limit, updated_at`. A user's monthly image count (0 =
  unlimited). Written when the admin creates a user or sends the field in an
  update; on the first start with this table every existing non-admin user
  gets a row with `DEFAULT_MONTHLY_IMAGE_LIMIT` (`app_state` key
  `image_limits_backfilled`). A user without a row gets the env default.
- **plugin_settings**: `plugin_id` (primary key), `enabled` (false), `capabilities`
  (JSON object: capability id → bool; a missing id uses the plugin's default),
  `updated_at`. The admin's switches; no row = disabled.
- **plugin_connections**: `id, user_id → users (CASCADE), plugin_id` (unique
  together), `account_subject` (the provider's account id), `account_email`,
  `scopes` (JSON list), `status` ("connected"|"reconnect_needed"),
  `status_reason` ("revoked"|"expired_testing"|"key_changed"|"scopes_missing"|null),
  `refresh_token_enc, access_token_enc` (encrypted with `TOKEN_ENCRYPTION_KEY`,
  bound to the user and plugin), `access_expires_at, key_id, chat_enabled,
  connected_at, refreshed_at, last_used_at, created_at, updated_at`. A user's own
  account at a plugin's provider; deleted on disconnect and with the user, and
  revoked at the provider then unless another connection (any user) uses the
  same provider account (revoking ends the whole grant).
- **message_plugin_items**: `id, message_id → messages (CASCADE), plugin_id,
  card_type, data` (JSON: the card as last sent), `created_at`. The cards of an
  answer (`ChatMessage.plugin_cards`), in order.
- **imported_documents**: `document_id` (primary key) `→ documents (CASCADE),
  plugin_id, kind ("email"|"attachment"), external_ref` (Gmail: the thread id, or
  `<message id>/<part id>`), `account_email, created_at`. Source of
  `DocumentItem.imported_from`.
- **plugin_audit_events**: `id, user_id → users (CASCADE), plugin_id, action,
  outcome ("ok"|"error"|"refused"), origin ("chat"|"ui"|"system"), items,
  created_at`. Metadata only: no subjects, addresses, queries or text.
- **gmail_pending_sends**: `id, user_id → users (CASCADE), conversation_id →
  conversations (SET NULL), account_email, account_subject, to_addrs, cc_addrs,
  bcc_addrs` (JSON lists of `{name, address, unmentioned}`), `subject` (text),
  `body, attachments` (JSON: document id, file name, size, sha256, content type,
  unrequested), `thread_id, in_reply_to, references_header, reply_to` (JSON: the
  email replied to as shown), `content_hash, status` ("pending"|"sending"|"sent"|
  "failed"|"cancelled"|"expired"|"replaced"), `error, origin` ("chat"|"edit"),
  `replaced_by_id → gmail_pending_sends (SET NULL), created_at, expires_at,
  attempted_at, sent_at, gmail_message_id, gmail_thread_id`. An email the
  assistant proposed: exactly what Send sends, once. Retention: the content lives
  as long as its conversation. Deleting the conversation cancels a pending row
  and scrubs every row of it (subject, body, recipients, attachments, reply
  details emptied); id, status and times stay for the daily cap. Startup scrubs
  rows left without a conversation.
- **user_preferences**: `user_id` (primary key) `→ users (CASCADE), data` (JSON
  object: the `Preferences` fields; unknown keys are kept on write), `updated_at`.
  No row: every default. Read leniently (an invalid stored value reads as its
  default), written strictly (`PATCH /me/preferences`).
- **login_events**: `id, user_id → users (CASCADE), created_at, ip` (approximate:
  IPv4 `a.b.c.x`, IPv6 its /48 network; null when unknown), `browser, os` (parsed
  from the User-Agent, which isn't stored), `device` ("desktop"|"mobile"|"tablet"|
  "unknown"); index `(user_id, created_at)`. Whether the address is public is
  derived from `ip` on read (`LoginEvent.ip_private`). One row per successful
  `POST /auth/login`; the newest 20 per user are kept (older rows are deleted when
  one is added). Shown only to the user (`GET /me/account`) and in their export.
- New tables are created by `create_all` on an existing database; no column is
  added to an existing table, so no migration is needed (the choice against
  introducing Alembic for the image limit is explained in
  `docs/briefs/image-generation.md` section 3.3, and holds for the plugin and
  account tables; rolling back past plugins needs the SQL in
  `docs/briefs/plugins-gmail.md` 3.3; rolling back past the account tables needs
  none: older code ignores them and the session id claim).

## 5. Qdrant layout

- One collection per embedding model: `f"{QDRANT_COLLECTION_PREFIX}_{model_id}"`,
  cosine distance, vector size detected on first embed.
- Point id: `uuid5(NAMESPACE, f"{doc_id}:{chunk_index}")`.
- Payload: `{doc_id:int, owner_id:int, scope:str, filename:str, chunk_index:int,
  page:int|null, text:str}`. Payload indexes on `doc_id`, `owner_id`, `scope`.
- **Every search filters on access**: `(scope=="private" AND owner_id==user)`
  OR `scope=="shared"`, plus optionally `doc_id IN selection`.

## 6. HTTP API (all JSON unless noted; all under `/api`)

Errors use FastAPI's shape: `{"detail": "message"}` (validation errors:
`{"detail": [{loc, msg, type}, ...]}`). 401 = not logged in or session
invalid, 403 = forbidden, 404 = not found **or not yours** (never leak
existence), 409 = conflict, 413 = too large, 422 = validation, 429 = rate limit
or budget exhausted (`detail` explains which; rate limit also sets `Retry-After`),
503 = the vector database is unavailable for an operation that must remove
vectors (rows and files are kept; retrying is safe), or Redis is unreachable
(`{"detail": "A required service is temporarily unavailable. Try again shortly."}`;
login, chat rate limits and admin stats need it).

Timestamps are ISO 8601 strings. Shapes use TypeScript notation; the TS source
of truth is `frontend/src/api/types.ts`.

### Shared shapes

```ts
type UserLimits = { monthly_token_limit: number; rate_limit_per_min: number;
  max_upload_mb: number; max_documents: number; max_storage_mb: number;
  monthly_image_limit: number /* images per UTC month; 0 = unlimited */ }
type UsageSummary = { month: string /* "YYYY-MM" */; chat_tokens: number;
  embedding_tokens: number; vision_tokens: number; images: number /* generated this month */;
  cost_usd: number /* every kind */; monthly_token_limit: number /* counts chat_tokens only */ }
type ThemePreference = "system"|"light"|"dark"     // "system" follows prefers-color-scheme, live
type AccentColor = "clay"|"ocean"|"sage"|"plum"|"slate"
type ChatFontSize = "small"|"default"|"large"
type Preferences = { theme: ThemePreference /* default "system" */; accent: AccentColor /* "clay" */;
  chat_font_size: ChatFontSize /* "default" */; enter_to_send: boolean /* true; false: Ctrl/Cmd+Enter sends */;
  default_model_id: number|null /* the chat model new chats start with; null: unset, or no longer one the user
    may use (then the server's default) */ }
type Me = { id: number; username: string; is_admin: boolean;
  limits: UserLimits; usage: UsageSummary; preferences: Preferences }
type ChatModel = { id: number; display_name: string; provider: string; is_default: boolean;
  can_create_files: boolean /* CHAT_FILE_TOOLS is on and LiteLLM says the model supports function calling */;
  can_create_images: boolean /* CHAT_IMAGE_TOOLS is on, a usable image model is active and the model gets tools */;
  supports_tools: boolean /* the model gets tools (function calling, not rejected since the backend started):
    plugin tools such as Gmail can be offered */ }
type ImageQuality = "low"|"medium"|"high"
type GeneratedImage = { width: number; height: number; alt_text: string;
  quality: ImageQuality /* as requested */ }  // display details only; no prompt
type GeneratedImageDetails = { prompt: string; revised_prompt: string|null;
  model_name: string /* the image model's litellm_model */; created_at: string }
type DocumentItem = { id: number; filename: string; scope: "private"|"shared";
  status: "pending"|"processing"|"ready"|"failed"; error: string|null;
  size_bytes: number; content_type: string; num_chunks: number;
  num_pages: number|null; owner_id: number; owner_username: string;
  can_delete: boolean; is_stale: boolean;
  vision_pages: number /* pages read with the vision model (vision_pages rows); 0 unless status is "ready" */;
  generated: boolean /* the assistant created this file in a chat (generated_documents row) */;
  image: GeneratedImage|null /* an image the assistant generated (generated_images row); always null in GET /admin/documents */;
  imported_from: string|null /* the plugin's name ("Gmail") when imported through a plugin (imported_documents row) */;
  created_at: string; updated_at: string }
type DocumentConfig = { supported_extensions: string[] /* e.g. [".pdf", ".docx"] */;
  max_upload_mb: number; max_documents: number; max_storage_mb: number;
  document_count: number; storage_bytes: number; can_upload_shared: boolean }
type Source = { index: number /* 1-based citation number */; document_id: number;
  filename: string; page: number|null; chunk_index: number; score: number;
  snippet: string /* ≤ 500 chars of the chunk */ }   // a document passage: no `kind`
type EmailSource = { kind: "email"; index: number /* after the document passages, same [n] sequence */;
  plugin_id: string /* "gmail" */; message_id: string; thread_id: string; subject: string;
  sender: string /* "Name <address>" or "address" */; date: string|null; snippet: string /* ≤ 300 chars */;
  url: string /* the conversation in Gmail */ }       // subject, sender and snippet are untrusted text
type AnswerSource = Source | EmailSource
type ConversationSummary = { id: number; title: string; created_at: string; updated_at: string }
type GeneratedFileFormat = "pdf"|"docx"|"xlsx"|"md"|"png"|"jpeg"|"webp"
type GeneratedFileKind = "file"|"image"
type GeneratedFile = { document_id: number|null /* null once the document was deleted */;
  filename: string; format: GeneratedFileFormat; size_bytes: number;
  status: "pending"|"processing"|"ready"|"failed"|null /* the document's status now; null when deleted */;
  image: GeneratedImage|null /* a generated image whose document still exists */ }
type ChatMessage = { id: number; role: "user"|"assistant"; content: string;
  sources: AnswerSource[] /* document passages, then the emails the answer cites */;
  model_name: string|null; prompt_tokens: number;
  completion_tokens: number; error: string|null;
  files: GeneratedFile[] /* files the assistant created in this answer, in order; [] for user messages */;
  plugin_cards: PluginCard[] /* cards plugins added to this answer, in order (card_id "p<n>"); [] for user messages */;
  created_at: string }
type ConversationDetail = ConversationSummary & { messages: ChatMessage[] }
```

`is_stale` = document is ready but was embedded with a model other than the
active embedding model (or has no embedding model). For the user's own counts
(`document_count`, `storage_bytes`) only documents they own count; for the admin
`max_documents`/`max_storage_mb` are reported as 0 (unlimited) and
`max_upload_mb` as `MAX_UPLOAD_MB_HARD`.

### Auth — `routers/auth.py`
- `POST /auth/login` `{username, password}` → `Me`; sets the cookie. Throttled
  via Redis per username (`LOGIN_MAX_ATTEMPTS` failures) and per client IP
  (`LOGIN_MAX_ATTEMPTS_PER_IP` failures; larger, because every client behind a
  reverse proxy or Docker's port publishing shares one IP) per
  `LOGIN_WINDOW_SECONDS` → 429. Inactive users get 401 with the same message
  as bad credentials ("Invalid username or password"). Updates `last_login_at`
  and records a `login_events` row (approximate IP from the client address nginx
  saw, browser, OS and device parsed from `User-Agent`; failed and refused logins
  record nothing). The session token carries `sid`, that row's id, so the history
  can mark the caller's own sign-in; tokens without `sid` stay valid.
- `POST /auth/logout` → 204; clears the cookie.
- `GET /auth/me` → `Me`.
- `POST /auth/change-password` `{current_password, new_password}` (new ≥ 8
  chars) → 204; bumps `token_version` and re-issues the cookie (same `sid`). The env admin
  gets 400 ("The admin password is managed in .env"); a wrong current password
  gets 400 and counts toward the login throttle for that username (429).
- `POST /auth/logout-others` (no body) → 204: bumps `token_version` (under the
  same row lock as change-password; 401 if the session ended meanwhile) and
  re-issues the caller's cookie with the same `sid` and the same expiry (the
  session is not extended; the cookie's `Max-Age` is the time it has left).
  Every other session ends:
  other browsers and devices, and every Bearer token, the caller's included
  (tabs of the same browser share the cookie and stay signed in). Allowed for the
  env admin; not throttled; plugin connections are untouched.

### Account — `routers/me.py` (the caller's own; every route needs a session)

```ts
type PreferencesUpdate = Partial<Preferences>  // omitted = unchanged; default_model_id null = the server's default;
                                               // null elsewhere, an unknown field or a value of another JSON type
                                               // (no coercion: "true" or 1 isn't a boolean, true or "3" isn't an id) → 422
type DeviceType = "desktop"|"mobile"|"tablet"|"unknown"
type LoginEvent = { id: number; created_at: string;
  ip: string|null /* approximate, as nginx saw the client: "203.0.113.x", or an IPv6 /48 network */;
  ip_private: boolean /* ip isn't a public address (private, loopback, link-local, shared): through this machine,
    Docker, a local network or a proxy; false when ip is null */;
  browser: string|null /* e.g. "Chrome 141", "Safari 26", "curl" */; os: string|null /* "macOS", "Windows", "iOS", … */;
  device: DeviceType; current: boolean /* the sign-in of the session making the request */ }
type AccountInfo = { id: number; username: string; is_admin: boolean;
  password_managed_by_env: boolean /* the env admin: change-password answers 400 */;
  created_at: string; last_login_at: string|null; conversation_count: number;
  recent_logins: LoginEvent[] /* newest first, at most 20 */ }
type UsageAmount = { used: number; limit: number /* 0 = unlimited; always 0 for the admin */ }
type MyUsage = { month: string /* "YYYY-MM", UTC */; period_start: string; resets_at: string /* the 1st of next month, 00:00 UTC */;
  chat_tokens: UsageAmount /* the monthly token budget */; images: UsageAmount /* the monthly image limit */;
  vision_tokens: UsageAmount /* VISION_MONTHLY_TOKEN_LIMIT */; embedding_tokens: number; requests: number /* chat answers */;
  rate_limit_per_min: number /* 0 = unlimited */;
  cost_usd: { total: number; chat: number; embedding: number; vision: number; image: number } /* estimated */;
  documents: { count: number; max_documents: number; storage_bytes: number; max_storage_mb: number; max_upload_mb: number };
  daily: { date: string /* YYYY-MM-DD, every UTC day from the 1st through today, zeros included */;
    chat_tokens: number; requests: number; images: number; vision_tokens: number; embedding_tokens: number; cost_usd: number }[];
  by_model: { model_name: string /* display name while the model exists, else the stored litellm id */;
    kind: "chat"|"embedding"|"vision"|"image"; tokens: number; requests: number; cost_usd: number }[] /* cost descending */ }
```
- `GET /me/preferences` → `Preferences` (also in `Me`). `PATCH /me/preferences`
  `PreferencesUpdate` → `Preferences` (the full result). `default_model_id` must
  be an enabled chat model the caller may use (as in `GET /models`), else 422
  "That model isn't available to you."; on read, a stored default that is no
  longer usable is reported as null (and comes back if the model does).
- `GET /me/account` → `AccountInfo`.
- `GET /me/usage` → `MyUsage` for the current UTC month: the same counts the
  limits use (`chat` tokens for the budget, `image` events for the image limit,
  `vision` tokens for the vision allowance), documents and storage as in
  `DocumentConfig`.
- `POST /me/export` (no body; a POST so nginx's cross-site check applies) →
  `200 application/zip` (a download): `README.txt`,
  `account.json` (profile, preferences, limits, this month's usage, plugin
  connections without tokens, sign-ins, and the counts of included and omitted
  conversations), `documents.json` (metadata of the documents the caller owns; no
  file bytes), `conversations/index.json`, and per conversation
  `conversations/<id>-<slug>.md` (a transcript, with one line per plugin card)
  and `.json` (messages with sources, files and plugin cards with their current
  state, as `GET /conversations/{id}` shows them); `<id>` is zero-padded to 6
  digits and `<slug>` is the title's ASCII letters and digits, lowercased, other
  runs as `-`, at most 40 characters (`conversation` when nothing is left), e.g.
  `conversations/000062-trip-plan.md`. Only the caller's own data, the
  admin included; never passwords, tokens or keys. Newest conversations first,
  within `EXPORT_MAX_MB` of content and `EXPORT_TIMEOUT_SECONDS`. Headers:
  `Content-Disposition: attachment; filename="rag-assistant-export-<username>-<YYYY-MM-DD>.zip"`,
  `Content-Length`, `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`,
  `X-Export-Conversations: <included>`, `X-Export-Omitted: <omitted>`. 409 while
  another export of the same user is being built or downloaded; 503 when the
  server is already building 2 exports. A client that disconnects ends the build
  and frees both. The layout is in `docs/briefs/ui-shell-settings.md` section 4.6.

### Models — `routers/models.py`
- `GET /models` → `ChatModel[]`: enabled chat models (never embedding, vision or image models) this user may use,
  default first, then by display_name. `provider` = prefix before `/` of
  `litellm_model` (or `"openai"` if no prefix). `supports_tools` = `llm.supports_tools(model)`.

### Documents — `routers/documents.py`
- `GET /documents` → `DocumentItem[]`: caller's private docs + all shared
  docs, newest first.
- `GET /documents/config` → `DocumentConfig`.
- `POST /documents` multipart form: `file` (one file), `scope` ("private"
  default | "shared"; shared requires admin, else 403) → 201 `DocumentItem`.
  Validates extension (415 if unsupported), size (413), empty file (422),
  document count and storage quota (409 with a clear message), duplicate (same
  owner + scope + sha256 → 409 "This file has already been uploaded"). Streams
  the upload to disk in chunks, never reads the whole file into memory.
  Sanitizes the display filename. Enqueues ingestion.
- `DELETE /documents/{id}` → 204. Allowed: owner (private docs) or admin
  (shared docs). Others → 404. Removes vectors, file and row (503 if the
  vectors can't be removed).
- `POST /documents/{id}/reprocess` → `DocumentItem` (status reset to
  "pending", re-enqueued; a document that is already pending is returned as is
  and not queued again). Same permission rule as delete. 400 when there is no
  active embedding model.
- `GET /documents/{id}/download` → the stored file, streamed, in any status.
  Visible documents only, as in `GET /documents` (the caller's private ones and
  every shared one); others 404 "Document not found"; a row whose file is gone
  404 "The file is missing from storage.". Headers: `Content-Type` from the
  extension (`.pdf`, `.docx`, `.pptx`, `.xlsx`, `.png`, `.jpg`/`.jpeg`,
  `.webp` their own types, anything else `application/octet-stream`; never the
  type the upload declared),
  `Content-Disposition: attachment; filename="<ASCII fallback>";
  filename*=UTF-8''<percent-encoded filename>`, `X-Content-Type-Options:
  nosniff`, `Cache-Control: private, no-store`, `Content-Security-Policy:
  sandbox`.
- `GET /documents/{id}/image?size=full|preview|thumb` (default `full`; other
  values 422) → a generated image for display. Visibility as for download
  (others 404 "Document not found"); a visible document that isn't a generated
  image 404 "This document isn't an image."; the stored file missing 404 "The
  file is missing from storage." `full` is the stored image, `preview` (long edge
  1024) and `thumb` (256) are WebP renditions (the stored image when a rendition
  is missing). Headers: `Content-Type` `image/png`, `image/jpeg` or `image/webp`;
  `Content-Disposition: inline; filename="<ASCII fallback>";
  filename*=UTF-8''<percent-encoded filename>`; `X-Content-Type-Options:
  nosniff`; `Cache-Control: private, no-cache`; `ETag`
  `"<first 32 hex of sha256>-<served>-<served file size>"` (`<served>` is
  `full`, `preview` or `thumb` as actually served; `If-None-Match` per RFC 9110:
  `*`, or a comma list of tags compared weakly, `W/` allowed; a match → 304
  with `ETag` and `Cache-Control`, no body);
  `Content-Security-Policy: sandbox`; `Cross-Origin-Resource-Policy:
  same-origin`.
- `GET /documents/{id}/image/details` → `GeneratedImageDetails` (the prompt,
  revised prompt, image model and time; for the image viewer). Same visibility
  and 404 rules as `/image`; `Cache-Control: private, no-store`.

### Conversations — `routers/conversations.py`
- `GET /conversations` → `ConversationSummary[]` (caller's, `updated_at` desc, max 200).
- `GET /conversations/{id}` → `ConversationDetail` (messages oldest first).
- `PATCH /conversations/{id}` `{title}` (1–200 chars) → `ConversationSummary`.
- `DELETE /conversations/{id}` → 204. Plugins clean up first (Gmail cancels and
  scrubs the conversation's pending sends); messages, their cards and file links
  go; documents (generated files and images included) and usage stay.
- `DELETE /conversations` → `{deleted: number}` (`ConversationsDeleted`): every
  conversation of the caller, each exactly as `DELETE /conversations/{id}` and
  committed on its own (a plugin's failing clean-up never undoes another
  conversation's deletion); `deleted` counts the committed ones; `{deleted: 0}`
  when there is none.

### Chat — `routers/chat.py`
`POST /chat/stream` body:
```ts
{ conversation_id: number|null; message: string /* 1..20000 chars */;
  model_id: number|null /* null = default */;
  document_ids: number[]|null /* null = all accessible docs, [] = no retrieval; max 10000 ids */ }
```
Before streaming (normal JSON errors): auth, rate limit (429), budget (429),
answers in flight (429 "Another answer is still being generated…": a user with
a monthly budget has at most 2 answers generating at once, since the budget is
charged when an answer ends), model resolution (400 no model available / 403
model not allowed), conversation ownership (404). Then responds `200 text/event-stream` with headers
`Cache-Control: no-cache` and `X-Accel-Buffering: no`. Events, in order
(`event: <name>\ndata: <json>\n\n`):

| event | data |
|---|---|
| `meta` | `{conversation_id, title, user_message_id, model: {id, display_name}}` (always first) |
| `sources` | `{sources: AnswerSource[]}` (always sent once after `meta`, possibly empty; sent **again**, with the whole list, after a plugin tool adds emails, and once more before `done`/`error` when emails the answer doesn't cite are dropped; the client replaces its list each time) |
| `warning` | `{message}` (0..n, e.g. "Document search unavailable: …") |
| `delta` | `{content: string}` (many) |
| `file` | `FileEventData` (0..n, interleaved with `delta`; see "Files" and "Images" below) |
| `plugin` | `PluginCard` (0..n, interleaved; the card's whole state, replacing the card with the same `card_id`; see "Plugins" below) |
| `done` | `{assistant_message_id, usage: {prompt_tokens, completion_tokens, total_tokens, cost_usd}}` (usage sums every billed model call of the answer) |
| `error` | `{message, assistant_message_id: number|null}` (terminal, instead of `done`) |

```ts
type FileEventData =
  | { call_id: string /* "f1", "f2", … for files; "i1", "i2", … for images; per answer */;
      kind: GeneratedFileKind; status: "writing";
      title: string|null; format: GeneratedFileFormat|null /* null for images */;
      chars: number /* characters of the file (or image prompt) written so far (approximate) */ }
      // a create_document or create_image call started (chars 0); then at most every 2 s while it is written (title
      // and format as soon as they appear); once more with the validated title (and a file's format) before the work
  | { call_id: string; kind: "image"; status: "generating"; title: string;
      width: number; height: number; quality: ImageQuality }
      // images only: the arguments are valid and the image model was called (20 to 120 s)
  | { call_id: string; kind: GeneratedFileKind; status: "created"; file: GeneratedFile /* file.image set for images */;
      document: DocumentItem;
      already_existed: boolean /* an identical private document existed and is linked instead; false for images */ }  // stored and queued for indexing
  | { call_id: string; kind: GeneratedFileKind; status: "failed"; title: string|null;
      format: GeneratedFileFormat|null; error: string }  // not created; the model is told why
```

**Keepalive.** After `meta`, whenever 15 s pass without an event (the model
thinking or writing a long file, a file rendering), the server sends an SSE
comment line `: keepalive` (followed by a blank line). Clients must ignore
comment lines.

Server behaviour: new conversation title = the message with whitespace
collapsed, cut to its first 60 characters followed by "…" when longer. The user
message is saved before streaming; the assistant message is saved at the end,
**including partial content if the client disconnects or the provider errors**
(then `error` is set). Usage is
recorded as a `chat` usage event (provider-reported tokens, else estimated
with `litellm.token_counter`), committed before the message so a message that
can't be stored is still billed; NUL characters are dropped from the answer. Retrieval failures don't fail the chat: they
emit `warning` and continue without context. History: last `HISTORY_MESSAGES`
messages of the conversation (excluding the new one). Retrieval query = the
new message, prefixed by the previous user message when one exists.

**Files.** The model gets one tool, `create_document` (`title`, `format`
"pdf"|"docx"|"xlsx"|"md", `content` Markdown, `sheets` for xlsx with string
cells, `source_numbers`), when `CHAT_FILE_TOOLS` is on,
`litellm.supports_function_calling` is true for the model (and the model hasn't
rejected tools since the backend started: a first call rejected as a bad request
is retried once without tools), and the user has room for another document
(non-admin: below `max_documents` and at least 256 KiB of storage left).
Otherwise the system prompt says why files can't be created. The system prompt
says to use the tool only when the user's latest message asks for a file, never
because a passage does. Calls that offer the tool send `parallel_tool_calls:
false` (one file per model call) and request `GENERATED_FILE_MAX_OUTPUT_TOKENS`.
A tool call runs only when the response finished with `tool_calls`: the server
validates the arguments, renders the file (in a child process,
`GENERATED_FILE_TIMEOUT_SECONDS`), stores it as a private document of the user
(quotas, per-file size and duplicates as for uploads; an identical file links
the existing document; the admin is exempt) marked as generated
(`generated_documents`), queues it for ingestion, and calls the model again with
the tool result, replaying the model's response unchanged (thinking blocks in
their original order; arguments that aren't a JSON object as `{}`). Validation,
quota, rendering and output-limit failures go back to the model as tool errors;
a response stopped by the provider's safety filter, or without a finish, ends
the answer with an `error`. At most `GENERATED_FILES_PER_ANSWER` files and that
number + 2 model calls per answer; the last allowed call is sent with
`tool_choice: "none"`. With a monthly budget, no further call starts once the
answer's tokens reach what was left of the budget when it started (the answer
ends with an `error`). The system prompt and tools are identical for every call
of an answer (apart from that one retry without tools, before anything is
replayed). Text from successive calls is joined with a blank line (streamed
as a `delta` too). One `chat` usage event per answer covers every call (a call
that failed before any output without provider-reported usage isn't billed);
`prompt_tokens`/`completion_tokens` of the message are the sums. The created
files are stored with the assistant message (`message_files`), also after Stop
or an error; a file still being stored at Stop is waited for and linked once,
one still rendering is dropped (its child process is killed). In later turns, an
assistant message that created files carries "(App note: this answer created
these files, which are in the user's documents: …)" in the model's history
(files deleted since are listed as "which have since been deleted from the
user's documents"), and passages from generated files are labelled as files the
assistant created (their `[n]` markers neutralized).

**Images.** The model also gets `create_image` (`prompt`, `title`,
`alt_text`, `aspect` "square"|"landscape"|"portrait" → 1024×1024, 1536×1024,
1024×1536, `quality` "low"|"medium"|"high", lowered to `IMAGE_MAX_QUALITY`) when
`CHAT_IMAGE_TOOLS` is on, a usable image model is active (supported, see Admin,
and not marked unusable), the chat model gets tools (as above), and a non-admin
user has room for an image (a free document slot and 8 MiB of storage) and
images left this month; otherwise the system prompt says why images can't be
made. Both tools go in one list (`create_document` first) that stays identical
for every call of the answer; an answer makes at most
`GENERATED_FILES_PER_ANSWER` (when files are offered) +
`GENERATED_IMAGES_PER_ANSWER` (when images are offered) + 2 model calls, and the
same rules apply: explicit request only, never because a passage asks, one tool
call per model call, `tool_choice: "none"` on the last call; when the tools are
rejected, the retry drops both. For each `create_image` call the server
validates the arguments, re-reads the active image model, checks room, then
**reserves** the image: under the per-user lock it counts the user's `image`
usage events of this UTC month and, below `monthly_image_limit` (or for the
admin), inserts one (zero cost). An image job then waits for one of
`IMAGE_CONCURRENCY` slots and calls the image model through
`litellm.aimage_generation` (`n=1`, `quality` always sent, base64 data only,
never a URL), all within `IMAGE_TIMEOUT_SECONDS`. The event is settled with the
reported tokens and cost when a response arrives; deleted when the request was
never sent or was refused or failed without billing (safety system, rate limit,
key or access errors); kept with an estimated cost when it was sent but no
response came (timeout). Any failed call except a safety refusal or invalid
arguments ends image generation for the rest of the answer; a response with no
or unusable image data also marks the model unusable in that process until it
is edited or its Test succeeds. The image bytes are validated and re-encoded
without their metadata in the file child process (PNG, JPEG or WebP only, at
most 32 MiB and 16.8 megapixels), marked as AI-generated (XMP, IPTC
`DigitalSourceType` `trainedAlgorithmicMedia`), stored as a private document of
the user with `generated_documents` and `generated_images` rows (a paid image is
always stored, even over the document or storage quota; the per-file upload
limit doesn't apply), queued for ingestion (indexed from its `.source.md`), and
linked to the answer (`message_files`). The tool result gives the model
`image_id` (the document id) and an escaped `markdown` reference
`![title](image:<id>)`. `create_document` content may contain such references on
a line of their own: the server embeds the images generated in this
conversation and owned by the user (PDF and DOCX: a picture scaled to the text
width with its caption and alt text; XLSX and MD: a text reference; unknown ids
stay text and are reported to the model; at most 10 per file). Image usage never
counts toward the token budget. In later turns an answer that generated images
carries "(App note: this answer generated these images, which are in the user's
documents: "Fox.png" (image:812).)" in the model's history; while
`create_document` is offered, the conversation's images whose answers are older
than the history (the newest 20, deleted ones left out) are listed with their
references in the system prompt. Passages from image documents are labelled as
images the assistant generated, not factual sources. **Stop** before the `generating` event, or while the job still waits
for a slot, sends nothing and releases the reservation; once the request may
have been sent, the job finishes on its own: the answer is saved at once as
stopped, and the image is stored, counted and linked to that message when it
arrives.

**Plugins (Gmail).** When the Gmail plugin is enabled and configured, the user's
connection is `connected` with `chat_enabled`, and the chat model gets tools,
the tool list also holds (after `create_document` and `create_image`, only for
capabilities the admin enabled and the user granted): `gmail_search` (`query` in
Gmail syntax, `max_results` 1..20, `page_token`), `gmail_read_message`
(`message_ids`, 1..5), `gmail_read_thread` (`thread_id`, `max_messages` 1..20)
and `gmail_import` (`thread_ids` 1..5, `include_attachments`) for `read`;
`gmail_create_draft` for `draft`; `gmail_propose_send` for `send` (both: `to`,
`cc`, `bcc`, `subject`, `body` plain text, `reply_to_message_id` (with `read`),
`attachment_filenames` of the user's own documents). Otherwise the system prompt
gets at most one sentence (connect from the Plugins menu or Settings → Plugins &
data, reconnect needed, this model can't use Gmail); a plugin that is turned off
or not configured adds nothing. The same loop rules apply (one tool call per model call,
identical tools and system prompt, the last call without tools); an answer makes
at most `GENERATED_FILES_PER_ANSWER` + `GENERATED_IMAGES_PER_ANSWER` +
`GMAIL_TOOL_CALLS_PER_ANSWER` (offered sets only) + 2 model calls, and returns at
most `GMAIL_MAX_CHARS_PER_ANSWER` characters of email text. Tool results present
emails as **untrusted data** (`<email cite_as="N">…</email>`, a notice, tags
defused, invisible and hidden content removed); each returned message gets the
next citation number after the document passages (an `EmailSource`, sent in a
repeated `sources` event), and the answer cites it as `[N]`. Emails the final
text doesn't cite are dropped before `done`. **Side effects need the user's own
request:** before `gmail_create_draft`, `gmail_propose_send` or `gmail_import`
runs, and before `create_document` or `create_image` runs in an answer that
read email (a Gmail tool returned email text, or a retrieved passage comes from
a document imported through a plugin: an email or an email's attachment), the
server classifies the user's own words with the chat model in a separate call
(once per answer, low reasoning effort, billed in the
answer's usage, fail-closed). It sees only the user's latest message, up to two
of their earlier messages in this conversation and, only when no earlier answer
in the model's history used email or plugin tools, the end of the previous
assistant reply; never emails, documents or tool results. A refused call gets a
tool error and a failed card. Each Gmail action re-checks, just before it runs,
that the plugin, the capability, "Use in chat" and the same connected account
are still on (turning any off takes effect at the next Gmail action, also in an
answer already streaming). `gmail_create_draft` creates the draft in Gmail.
`gmail_propose_send` **never sends**: it stores a pending send (exact
recipients, subject, body, attachments, reply headers, `content_hash`) that only
`POST /plugins/gmail/sends/{id}/send` sends. Draft and send cards show each
recipient's address apart from its display name (flagged when the user never
typed it and it isn't in the email replied to; Bcc always highlighted), the
email replied to, and attachments the user didn't name (a file the assistant
created in this conversation, earlier in the same answer included, isn't
flagged). The card shows everything that is sent: a body or subject the model
writes with invisible, format or control characters (tag characters, bidi
controls, zero-width characters, …) is refused with a tool error, and every
drafted or sent body, subject and display name (the user's edits too, cleaned
without refusing) loses them and keeps at most one blank line in a row. Threads
are read (in chat, the Gmail browser and imports) without the user's unsent
drafts, and without messages in Trash or Spam unless the whole thread is there;
a draft read on its own is marked as never sent. Cards (`plugin` events, saved in
`message_plugin_items` and returned as `ChatMessage.plugin_cards`, hydrated with
the live pending-send and document state): `gmail_activity` (search or read),
`gmail_draft`, `gmail_send`, `gmail_import`. In later turns an answer that used
Gmail carries an App note in the model's history listing the cited emails (ids,
subjects as untrusted text) and the drafts and proposals with their outcome. A
created file's Sources section may list cited emails ("Email: subject (sender,
date)"). Stop waits for a draft, import or proposal being stored; reads stop at
once.

### Plugins — `routers/plugins.py`, `plugins/gmail/router.py`

```ts
type PluginCapabilityInfo = { id: string /* Gmail: "read"|"draft"|"send" */; name: string; description: string;
  enabled: boolean /* by the admin */; granted: boolean /* by the caller's connection */ }
type PluginConnectionStatus = { account_email: string; status: "connected"|"reconnect_needed";
  status_reason: "revoked"|"expired_testing"|"key_changed"|"scopes_missing"|null; status_message: string|null;
  granted_scopes: string[]; chat_enabled: boolean; connected_at: string; last_used_at: string|null;
  refresh_expires_at: string|null /* testing mode: when Google ends it */ }
type PluginInfo = { id: string; name: string; description: string; auth_type: "oauth2";
  configured: boolean /* the settings it needs are in .env; only the admin API lists the missing ones */;
  enabled: boolean /* turned on by the admin and configured: it can be connected and used */;
  testing_mode: boolean /* only the provider app's test users can connect; connections end after 7 days */;
  supports_import: boolean; import_max_items: number|null; capabilities: PluginCapabilityInfo[];
  connection: PluginConnectionStatus|null /* the caller's own; never anyone else's */ }
type PluginConnectRequest = { capabilities?: string[] }   type PluginConnectResponse = { authorize_url: string }
type PluginConnectionUpdate = { chat_enabled: boolean }
type PluginOAuthResult = "connected"|"denied"|"expired"|"invalid"|"no_access"|"disabled"|"error"
type GmailThreadSearch = { q?: string|null; page_token?: string|null; max_results?: number }
type GmailThreadSummary = { thread_id: string; subject: string; sender: string; date: string|null; snippet: string;
  message_count: number; unread: boolean; gmail_url: string }   // sender, date, snippet: of the newest message;
  // drafts, and Trash/Spam messages of a thread not wholly there, aren't counted
type GmailThreadPage = { threads: GmailThreadSummary[]; next_page_token: string|null; result_size_estimate: number }
type GmailAttachmentInfo = { part_id: string; filename: string; mime_type: string; size_bytes: number;
  importable: boolean; reason: string|null }
type GmailMessageView = { message_id: string; thread_id: string; sender: string; to: string[]; cc: string[];
  date: string|null; subject: string; snippet: string; body_text: string /* plain text, ≤ 20000 chars */;
  body_truncated: boolean; attachments: GmailAttachmentInfo[]; unread: boolean }
type GmailThreadDetail = { thread_id: string; subject: string; gmail_url: string;
  messages: GmailMessageView[] /* oldest first, the newest 50 */; messages_truncated: boolean }
type GmailImportRequest = { threads: { thread_id: string; include_thread: boolean;
  attachments: { message_id: string; part_id: string }[] }[] }
type GmailImportItem = { kind: "thread"|"attachment"; thread_id: string; message_id: string|null;
  part_id: string|null; label: string; status: "imported"|"already_existed"|"skipped"|"failed";
  reason: string|null; document_id: number|null; document: DocumentItem|null }
type GmailImportResult = { items: GmailImportItem[] }
type EmailAddress = { address: string /* ASCII, what is sent; IDN domains in punycode */;
  name: string|null /* untrusted; dropped when it contained "@" */;
  unicode_domain: string|null /* the Unicode form of an internationalized domain: show both, with a warning */ }
type EmailRecipient = EmailAddress & { unmentioned: boolean /* never typed by the user in this conversation and not
  a participant of the email replied to (Bcc: only typed addresses count) */ }
type EmailReplyInfo = { message_id: string; subject: string; sender: EmailAddress; date: string|null }
type EmailAttachment = { document_id: number|null; filename: string; size_bytes: number;
  unrequested: boolean /* not named by the user, not created in this conversation, not picked in Edit */ }
type PendingSend = { id: number; status: "pending"|"sending"|"sent"|"failed"|"cancelled"|"expired"|"replaced"
  /* "sending" for over 5 minutes reads as "failed" ("may have been sent") */;
  account_email: string; to: EmailRecipient[]; cc: EmailRecipient[]; bcc: EmailRecipient[]; subject: string;
  body: string; attachments: EmailAttachment[]; reply_to: EmailReplyInfo|null; content_hash: string;
  created_at: string; expires_at: string; sent_at: string|null; error: string|null; gmail_url: string|null;
  replaced_by: number|null }
type PendingSendConfirm = { content_hash: string }
type PendingSendRevision = { to: string[] /* "Name <address>" or "address" */; cc: string[]; bcc: string[];
  subject: string; body: string; attachment_document_ids: number[] }
type PluginCard =
  | { card_id: string; plugin_id: "gmail"; type: "gmail_activity"; status: "running"|"done"|"failed";
      action: "search"|"read_message"|"read_thread"; query: string|null; count: number; error: string|null }
  | { card_id: string; plugin_id: "gmail"; type: "gmail_draft"; status: "writing"|"creating"|"created"|"failed";
      account_email: string|null; to: EmailRecipient[]; cc: EmailRecipient[]; bcc: EmailRecipient[];
      subject: string|null; preview: string /* first 500 chars */; attachments: EmailAttachment[];
      reply_to: EmailReplyInfo|null; gmail_url: string|null; error: string|null }
  | { card_id: string; plugin_id: "gmail"; type: "gmail_send"; status: "writing"|"proposed"|"failed";
      pending: PendingSend|null; error: string|null }
  | { card_id: string; plugin_id: "gmail"; type: "gmail_import"; status: "importing"|"done"|"failed";
      items: GmailImportItem[]; error: string|null }
  // card_id: "g1", "g2", … while streaming; "p<n>" when loaded with the conversation. Unknown types: ignore.
```

Plugin problems never answer 401 (that means "signed out"): not connected,
reconnect needed or another Google account → 409; the plugin or the capability
turned off → 403; Gmail unreachable or rate limited → 503; an unknown Gmail id →
404; another user's pending send → 404. Send and revise check the caller's own
Gmail access before the pending send (turned off → 403; not connected,
reconnect needed or send not granted → 409, e.g. "Gmail isn't connected."), so
only a caller who could send gets that 404; get and cancel answer 404 to any
other user. In Google's testing mode a connection
reads as reconnect needed (`expired_testing`) once it is 7 days old, without
waiting for a failed call.

- `GET /plugins` → `PluginInfo[]`: **every registered plugin** (the catalog), in
  registry order, whatever its state, with the caller's own connection when there
  is one. A client shows one status, in this order of precedence: not
  `configured` → "Not set up" (admins: "Set up in Admin → Plugins"); not
  `enabled` → "Turned off by the admin"; in both, Disconnect is still offered
  when the caller has a connection (`DELETE …/connection` works then);
  connection `reconnect_needed` → "Reconnect needed"; a connection →
  "Connected"; else "Not connected" (Connect). Listing a plugin changes nothing
  in chat: its tools are offered only as described under Chat, Plugins.
- `POST /plugins/{id}/connect` `PluginConnectRequest` → `PluginConnectResponse`
  (the client navigates there). The request's `Origin` must be one of
  `PUBLIC_BASE_URLS` (else 400); `redirect_uri` = that origin +
  `/api/plugins/{id}/oauth/callback`. Starts an authorization-code flow with
  `state` (kept in Redis 10 minutes, one-time) and PKCE S256, `access_type=offline`,
  `prompt=consent`, `include_granted_scopes=true`, scopes `openid email` plus the
  requested capabilities' (Gmail: `gmail.readonly` for read; `gmail.compose` for
  draft, which Google also lets send, so send adds nothing when draft is
  requested too, else `gmail.send`), and `login_hint` when reconnecting; sets the cookie
  `rag_oauth_<id>` (a nonce; `HttpOnly`, `SameSite=Lax`, path
  `/api/plugins/<id>/oauth/`, 10 minutes). 404 unknown plugin, 403 turned off,
  409 not configured, 422 unknown capability, 429 more than 10 a minute.
- `GET /plugins/{id}/oauth/callback?state&code&scope|error` (Gmail's is fixed:
  `/api/plugins/gmail/oauth/callback`; no session cookie needed, the user comes
  from the state and must still be active with the same `token_version`; the
  nonce cookie must match) → **303** to `<origin>/?plugin=<id>&oauth=<PluginOAuthResult>`.
  On success the tokens are stored encrypted; reconnecting the same account keeps
  the connection, another account replaces it (the old grant is revoked unless
  another connection uses that account).
- `PATCH /plugins/{id}/connection` `PluginConnectionUpdate` → `PluginConnectionStatus` (404 without one).
- `DELETE /plugins/{id}/connection` → 204 (also while the plugin is off): deletes
  the connection, cancels pending sends and revokes the grant at Google (best
  effort) unless another connection of this app, any user's, uses the same
  Google account (revoking would end that one too). Imported documents, drafts
  and sent mail stay.
- `POST /plugins/gmail/threads/search` `GmailThreadSearch` `{q?, page_token?,
  max_results?}` (`q` ≤ 500 chars, empty or absent: `in:inbox`; `max_results`
  1..25, default 20) → `GmailThreadPage` (read). A POST body, never a URL query:
  search terms must not reach the nginx or uvicorn access logs, which record
  plugin paths without their query string anyway.
- `GET /plugins/gmail/threads/{thread_id}` → `GmailThreadDetail` (read).
- `POST /plugins/gmail/import` `GmailImportRequest` → `GmailImportResult` (read):
  1..`GMAIL_IMPORT_MAX_ITEMS` items (threads with `include_thread` plus
  attachments; else 422), one import per user at a time, chat imports included
  (409); items not reached within 90 seconds are `skipped`. A thread becomes a
  private Markdown document (`<subject> (Gmail <date>).md`: headers and text of
  each message); attachments with a supported extension become documents of their
  own. Quotas, per-file size and duplicates as for uploads, except that identical
  bytes link the existing private document (`already_existed`). Each item reports
  its own outcome.
- `GET /plugins/gmail/sends/{id}` → `PendingSend` (owner; also while Gmail is off).
- `POST /plugins/gmail/sends/{id}/send` `PendingSendConfirm` → `PendingSend`
  (send): sends exactly the stored email once. 409 when it isn't `pending`, has
  expired (`GMAIL_SEND_CONFIRM_MINUTES`), `content_hash` differs, or the connected
  account (its provider id) changed; 429 at `GMAIL_DAILY_SEND_LIMIT` emails today (UTC, the admin
  too); 409 when an attachment was changed or deleted since (checked before the
  claim, so no daily slot is used). The claim `pending → sending` is atomic, and
  the send plus its status update finish even if the request is cancelled.
  Afterwards the status is `sent` (with `gmail_url`) or `failed` (with `error`;
  "may have been sent" when Gmail didn't answer, or when a row stayed `sending`
  over 5 minutes, e.g. after a restart).
- `POST /plugins/gmail/sends/{id}/cancel` → `PendingSend` (`pending` → `cancelled`).
- `POST /plugins/gmail/sends/{id}/revise` `PendingSendRevision` → `PendingSend`
  (send): a new proposal (new expiry; a reply keeps its thread and subject); the
  old one, if pending, becomes `replaced`. Only from `pending`, `expired`,
  `failed` (a stale `sending` included) or `cancelled` (409 otherwise).

### Admin — `routers/admin.py` (all require admin; non-admin → 403)
```ts
type AdminUser = { id: number; username: string; is_admin: boolean; is_active: boolean;
  monthly_token_limit: number; rate_limit_per_min: number; max_upload_mb: number;
  max_documents: number; max_storage_mb: number;
  monthly_image_limit: number /* effective: the user's own value, else DEFAULT_MONTHLY_IMAGE_LIMIT */;
  restrict_models: boolean;
  allowed_model_ids: number[]; created_at: string; last_login_at: string|null;
  usage: { chat_tokens: number; embedding_tokens: number; vision_tokens: number; images: number; cost_usd: number }; // current month
  document_count: number; storage_bytes: number }
type AdminUserCreate = { username: string /* 3-64, ^[a-zA-Z0-9_.-]+$, lowercased */;
  password: string /* >= 8 */; is_active?: boolean; monthly_token_limit?: number;
  rate_limit_per_min?: number; max_upload_mb?: number; max_documents?: number;
  max_storage_mb?: number; monthly_image_limit?: number /* 0 = unlimited */;
  restrict_models?: boolean; allowed_model_ids?: number[] }
type AdminUserUpdate = Partial<Omit<AdminUserCreate, "username">>  // password = reset
type ModelKind = "chat"|"embedding"|"vision"|"image"
type AdminModel = { id: number; display_name: string; litellm_model: string;
  kind: ModelKind; enabled: boolean; is_default: boolean;
  api_base: string|null; api_key_env: string|null; max_output_tokens: number|null;
  temperature: number|null; input_cost_per_mtok: number|null;
  output_cost_per_mtok: number|null; embedding_dim: number|null;
  created_at: string; updated_at: string }
type AdminModelCreate = { display_name: string; litellm_model: string; kind: ModelKind;
  enabled?: boolean; is_default?: boolean; api_base?: string|null; api_key_env?: string|null;
  max_output_tokens?: number|null; temperature?: number|null;
  input_cost_per_mtok?: number|null; output_cost_per_mtok?: number|null }
type AdminModelUpdate = Partial<Omit<AdminModelCreate, "kind">>
type ModelTestResult = { ok: boolean; message: string; latency_ms: number; embedding_dim: number|null;
  image_data_url: string|null /* image models: a data: URL of a small thumbnail of the test image */ }
type AdminConfig = { defaults: UserLimits; max_upload_mb_hard: number; admin_username: string }
type AdminStats = { users: number; documents: number; stale_documents: number;
  failed_documents: number; active_embedding_model: {id: number; display_name: string}|null;
  active_vision_model: {id: number; display_name: string}|null;
  active_image_model: {id: number; display_name: string}|null; queue_length: number }
type UsageReport = { month: string;
  totals: { chat_tokens: number; embedding_tokens: number; vision_tokens: number; images: number; cost_usd: number; requests: number };
  by_user: { user_id: number; username: string; chat_tokens: number; embedding_tokens: number; vision_tokens: number; images: number; cost_usd: number; requests: number }[];
  by_model: { model_name: string; kind: ModelKind; prompt_tokens: number; completion_tokens: number; cost_usd: number; requests: number /* image models: images */ }[];
  daily: { date: string /* YYYY-MM-DD */; chat_tokens: number; embedding_tokens: number; vision_tokens: number; images: number; cost_usd: number }[] }
```
Endpoints:
- `GET /admin/config` → `AdminConfig`
- `GET /admin/stats` → `AdminStats`
- `GET /admin/users` → `AdminUser[]` · `POST /admin/users` → 201 `AdminUser`
  (409 if username taken) · `PATCH /admin/users/{id}` → `AdminUser` ·
  `DELETE /admin/users/{id}` → 204 (deletes their documents completely:
  vectors, rows and files; 503 if the vectors can't be removed). The env
  admin account cannot be changed or deleted here (400 "The admin account is
  managed in .env"). Changing a password or deactivating bumps
  `token_version`. `allowed_model_ids` must reference existing chat models
  (422 otherwise). Creating a user writes its `user_image_limits` row with
  `monthly_image_limit` (default `DEFAULT_MONTHLY_IMAGE_LIMIT`); an update that
  sends the field writes (or creates) the row.
- `GET /admin/models` → `AdminModel[]` · `POST /admin/models` → 201 ·
  `PATCH /admin/models/{id}` · `DELETE /admin/models/{id}` → 204 ·
  `POST /admin/models/{id}/test` → `ModelTestResult`. Setting `is_default`
  clears it on the other models of the same kind; making a disabled model the
  default → 400. Vision models follow the chat-model rules: disabling,
  un-defaulting or deleting the active vision model is allowed and turns PDF
  page reading off. Image models do the same: disabling, un-defaulting or
  deleting the active image model is allowed and turns image generation off.
  Image models must be OpenAI (`openai/…`, also with an `api_base`) or Azure
  OpenAI (`azure/<deployment>`) models whose LiteLLM image config is a GPT
  image config (it takes `quality` and `output_format`); creating one, or
  changing an image model's `litellm_model` or `api_base`, otherwise → 422 with
  the reason (other providers run as blocking calls that Stop can't cancel;
  dall-e models take other sizes and qualities).
  Deleting the active embedding model, disabling it
  (`enabled: false`) or unsetting its `is_default` → 409 ("This is the active
  embedding model. Make another embedding model the default first.").
  Deleting another embedding model, or changing an embedding model's
  `litellm_model` or `api_base`, drops its Qdrant collection, marks its
  documents stale (embedding_model_id → NULL) and resets `embedding_dim`.
  Model calls send the key in the env var named by `api_key_env` (the call
  fails with a message when it is unset); without one, a model with an
  `api_base` gets a placeholder key, never the provider's default key, and
  any other model gets LiteLLM's provider default. Without
  `max_output_tokens`, chats (and the Test button) request
  `DEFAULT_MAX_OUTPUT_TOKENS`, lowered to the output cap LiteLLM's model map
  lists for the model; vision calls (and their Test) request
  `VISION_MAX_OUTPUT_TOKENS`, lowered the same way, and
  `reasoning_effort="low"` (thinking counts toward `max_tokens`). The Test
  sends a tiny real request: a one-word chat prompt, one embedding, or for a
  vision model a small generated image showing "TEST 4712" with the production
  vision system prompt (`ok` only if the reply contains 4712), or for an image
  model one 1024×1024 image at low quality, validated like a chat image and
  returned as a 256-px thumbnail in `image_data_url` (recorded as an `image`
  usage event of the admin; other Tests aren't recorded).
  Bootstrap adds a
  default vision model once, recorded as `app_state` key `vision_seeded` (an
  existing database gets it on its next start): "Claude Sonnet 5.5 (vision)"
  `anthropic/claude-sonnet-5-5`, enabled if `ANTHROPIC_API_KEY` is set, or
  "GPT-5.4 mini (vision)" `openai/gpt-5.4-mini` if only `OPENAI_API_KEY` is
  set. A vision model the admin deletes is not added again. Bootstrap also adds
  an image model once, recorded as `app_state` key `image_seeded`: "GPT Image 2"
  `openai/gpt-image-2`, the active image model, enabled if `OPENAI_API_KEY` is
  set (prices from LiteLLM's map); an image model the admin deletes is not added
  again. On that start every existing non-admin user also gets a
  `user_image_limits` row with `DEFAULT_MONTHLY_IMAGE_LIMIT`, once (`app_state`
  key `image_limits_backfilled`).
- `POST /admin/reindex` `{only_stale?: boolean /* default true */}` →
  `{queued: number}`: sets matching documents (`only_stale`: stale and failed
  ones; otherwise all) to "pending" and enqueues them. 400 when there is no
  active embedding model.
- `GET /admin/usage?month=YYYY-MM` (default current month) → `UsageReport`.
  `requests` counts chat usage events only; `images` counts `image` usage
  events; `cost_usd` covers every kind.
- `GET /admin/documents` → `DocumentItem[]` (all documents, every owner;
  `image` is always null there: prompts are the owner's private content).
  `DELETE /admin/documents/{id}` → 204 (any document).

Plugins — `routers/admin_plugins.py`:

```ts
type PluginAction = "connect"|"disconnect"|"reconnect_needed"|"search"|"read"|"import"|"draft"|"send_proposed"|"send"|"send_cancelled"
type PluginActionCount = { action: PluginAction; ok: number; failed: number; refused: number /* by the request check */ }
type AdminPlugin = { id: string; name: string; description: string; auth_type: "oauth2"; supports_import: boolean;
  configured: boolean; missing_settings: string[] /* env vars, e.g. "GOOGLE_OAUTH_CLIENT_ID", "TOKEN_ENCRYPTION_KEY" */;
  test_endpoints: boolean; enabled: boolean;
  capabilities: { id: string; name: string; description: string; scopes: string[]; enabled: boolean;
    note: string|null /* e.g. drafts: Google's draft permission also allows sending; the app still needs the click */ }[];
  requested_scopes: string[] /* exactly what a connect asks for with the current switches */;
  redirect_uris: string[] /* one per PUBLIC_BASE_URLS entry */; testing_mode: boolean;
  connections: { connected: number; reconnect_needed: number;
    old_key: number /* still encrypted with a key other than the first TOKEN_ENCRYPTION_KEY */ };
  activity: { month: string; actions: PluginActionCount[] } /* this month */ }
type AdminPluginUpdate = { enabled?: boolean; capabilities?: Record<string, boolean> }
type PluginActivityReport = { month: string; totals: PluginActionCount[];
  by_user: { user_id: number; username: string; actions: PluginActionCount[] }[] }
type RequestCheckTest = { model_id: number }
type RequestCheckTestResult = { ok: boolean; message: string; latency_ms: number;
  answer: { file: boolean; image: boolean; draft: boolean; send: boolean; import: boolean }|null }
```
- `GET /admin/plugins` → `AdminPlugin[]` (counts only: account emails are never shown to the admin).
- `PATCH /admin/plugins/{id}` `AdminPluginUpdate` → `AdminPlugin`. Turning on a
  plugin that isn't configured → 409 (names the missing settings); an unknown
  capability, or every capability off → 422. Turning a plugin or capability off
  takes its tools out of the next chat turn, refuses its next action in answers
  already streaming, and takes its UI out of the next load (the client also
  reloads plugin state when the window regains focus); connections stay.
- `GET /admin/plugins/{id}/activity?month=YYYY-MM` → `PluginActivityReport`.
- `POST /admin/plugins/{id}/disconnect-all` → `{disconnected: number}` (each as
  `DELETE /plugins/{id}/connection`).
- `POST /admin/plugins/request-check/test` `RequestCheckTest` → `RequestCheckTestResult`:
  runs the request check (the model call that gates drafts, sends, imports and,
  in answers that read email, files and images) once with the given chat model
  and a fixed message asking for a draft; `ok` when it answers with valid JSON
  asking for a draft only. 404 unknown model, 422 not a chat model. No usage is
  recorded (like the model Test).
- Deleting a user also deletes their connections (revoked at the provider after
  the commit, best effort), pending sends and plugin audit events, and their
  preferences and sign-in history. The admin API never shows users' sign-in
  history or preferences.

## 7. Ingestion pipeline

1. `POST /documents` stores the file under `UPLOAD_DIR/<stored_name>`, creates
   the row (`pending`) and calls `ingestion.enqueue_document(doc_id)`
   (`RPUSH rag:ingest_queue <doc_id>`).
2. `app.worker` (separate container, same image) `BLPOP`s ids and runs
   `ingestion.process_document(doc_id)` with bounded concurrency
   (`WORKER_CONCURRENCY`). On start it re-enqueues documents left in
   `pending`/`processing`, except that a document whose run was interrupted 3
   times (e.g. the worker was killed parsing it) becomes `failed` instead
   (runs are counted in Redis, `rag:ingest_attempts:<id>`; reprocess, reindex
   and delete reset the count). While running it also queues `pending`
   documents missing from the queue (e.g. uploaded while Redis was down: the
   upload still succeeds), and treats `processing` documents none of its jobs
   is running (a run that couldn't record its result) like on start: every
   60 s, and right after Redis comes back.
3. `process_document` skips documents not in `pending`/`processing` (makes
   duplicate queue entries harmless), sets `processing`, parses in a thread
   (`asyncio.to_thread`), reads PDF pages with the vision model (step 4),
   chunks, embeds in batches of `EMBED_BATCH_SIZE` with
   the active embedding model, ensures the collection, deletes the document's
   old points (in the old and the new collection), upserts, records an
   `embedding` usage event (and a `vision` one) for the owner, then sets `ready` with `num_chunks`,
   `num_pages` and `embedding_model_id`. An embedding or Qdrant call that
   fails with a dropped connection or a timeout is tried again after 1 s and
   then 4 s (no retry starts more than 120 s after the first try); other errors
   (rejected requests, quotas, error responses) aren't retried. The retries are
   part of the run (they don't count toward the 3 interrupted runs). Any
   failure → `failed` with a human-readable `error` (≤ 1000 chars). If the
   embedding model was edited (`litellm_model`/`api_base`) or deleted during
   the run, its vectors are dropped and the document goes back to `pending` and
   is re-enqueued for the model active now (Postgres row locks keep the run from
   recreating a dropped collection).
4. **Vision (PDFs only, when a vision model is active as the run starts).**
   *Measuring* (pypdf, in the parse thread): pages that draw images (image
   XObjects, also inside forms, or inline images) get their content streams
   interpreted for image placement and text visibility; pages drawing the same
   content are one unit. Images under 1% of the page, and images drawn on 3 or
   more units (or on every unit of a multi-page PDF: letterheads, watermarks,
   slide templates) are ignored; coverage is the union of the rest. A page
   whose content exceeds 2 MiB isn't measured (text only).
   *Classes*: a page with fewer than `VISION_MIN_TEXT_CHARS` non-whitespace
   text-layer characters whose images cover at least `VISION_MIN_IMAGE_RATIO`
   is **scanned**; so is a page covered (90% or more) by images whose text is a
   small visible overlay (under 200 characters: e-signature envelope IDs, Bates
   numbers, fax headers). A page covered by images whose text is mostly
   invisible (render mode 3/7: an OCR layer) keeps its text and never reaches
   the model. Any other page with text whose images cover at least
   `VISION_MIN_IMAGE_RATIO` gets a **description** of its visual content
   appended. Scanned pages are **transcribed** (text in reading order, tables
   as Markdown, figures described in place) and that becomes their text.
   *Cap*: at most `VISION_MAX_PAGES` units per run. Scanned pages covered by
   images come first; if they alone exceed the cap the document fails before
   any model call with "This PDF has N scanned pages, but at most M pages per
   document can be read with the vision model (VISION_MAX_PAGES). Split it into
   smaller files and upload them separately." Then the other scanned pages in
   page order, then description pages (largest figures first); pages over the
   cap keep their text layer only (logged).
   *Rendering*: only the selected pages that aren't cached reach PDFium, in a
   child process (`python -I`, empty environment, 1 GiB address-space limit,
   30 s per page, killed and restarted on a crash or timeout; JPEG, long edge
   ≤ `VISION_MAX_IMAGE_PX`, at most 200 DPI; within `PARSE_TIMEOUT_SECONDS`).
   Pages too complex to render (over 300 000 operators with forms expanded) or
   with a source image over 100 megapixels are refused before rendering. A
   scanned page that can't be rendered fails the document; a description page
   is skipped.
   *Reading*: `VISION_CONCURRENCY` calls per document and `VISION_MAX_IN_FLIGHT`
   in the worker; rate limits and provider errors are retried with backoff
   (honouring `Retry-After`, at most 60 s per wait) until
   `VISION_TIMEOUT_SECONDS`. The prompt treats the image as untrusted data
   (instructions in it are ignored); output is normalized and capped like
   other extracted text. Each page's output is stored in `vision_pages` as it
   arrives, so Retry, reprocess, re-indexing and a run interrupted by a restart
   pay only for pages not read yet. Before each call the run checks that the
   document is still processing (a delete or reprocess stops it, without an
   error) and that its vision model is still the active one (otherwise the
   document goes back to `pending` and is queued again). The first failed call
   on a scanned page stops new calls and fails the document ("Page N couldn't
   be read with the vision model: <reason>"); a failed description is skipped
   with a logged warning, but a fatal error on one (rejected key, unknown model,
   no image received) also stops new calls, and scanned pages not read yet fail
   the document with that reason. A document in this phase doesn't hold a
   `WORKER_CONCURRENCY` slot (at most `WORKER_CONCURRENCY` documents are in it
   at once).
   *Cost*: vision tokens are one `vision` usage event per run for the owner,
   summed over the calls that completed, also when the run fails or stops; they
   don't count toward the monthly chat budget. If the owner already used
   `VISION_MONTHLY_TOKEN_LIMIT` this month when the run starts, only cached
   pages are used: a scanned page that isn't cached fails the document, and
   descriptions are skipped. Pages read by the vision model are chunked,
   embedded and cited by page like any other.
5. **Files the assistant created** are ingested like uploads. A generated PDF
   is indexed from its Markdown source (`<stored_name>.source.md`) instead of
   its text layer, because text extracted from shaped Indian-script PDFs is
   lossy; `num_pages` still comes from the PDF, no page is sent to the vision
   model, and its citations have no page number. DOCX, XLSX and MD files are
   parsed as usual. A generated image is indexed from its `.source.md` (title,
   description, prompt and revised prompt; no OCR or vision model), with no
   pages. When their passages are put into a chat prompt they are
   labelled as files the assistant created (section 6, Chat); passages of a
   generated image as an image the assistant generated (its title, description
   and prompt, not a factual source).
6. **Documents imported through a plugin** (Gmail threads as `.md`, attachments
   as themselves) are ingested like uploads. Their passages are labelled in chat
   prompts as emails imported from Gmail, or files attached to them (untrusted
   text written by others), and an answer that retrieves either counts as having
   read email (section 6, Chat, Plugins).
7. Supported uploads: `.pdf .docx .pptx .xlsx .html .htm .md .markdown .txt .csv .json`
   (images can't be uploaded; only generated images are image documents).
   Legacy `.doc`/`.ppt`/`.xls` are rejected with a hint to save as the modern
   format. Without an active vision model, PDFs without any text fail with "No
   extractable text: this PDF looks scanned or image-only. Ask the admin to
   enable a vision model to read it." Guard against zip bombs
   (refuse when total uncompressed size > `MAX_UNCOMPRESSED_MB`, default 100,
   or a `.docx`/`.pptx` XML part > `MAX_XML_PART_MB`, default 32) and cap
   extracted text at `MAX_EXTRACTED_CHARS` (text, JSON and HTML files over 4
   bytes per allowed character are refused before parsing). PDFs over
   `MAX_PDF_PAGES` (5000) are refused; pages drawing the same content stream
   and resources are read once. Parsing stops after `PARSE_TIMEOUT_SECONDS`
   (300; checked between pages, slides and rows).

## 8. Frontend

- Routes: `/login` (public), `/` chat (auth), `/c/:conversationId` (auth),
  `/admin` (admin only; others redirected to `/`), `/settings` and
  `/settings/:tab` (redirect to `/?settings=<tab>`). `AuthContext` loads
  `/api/auth/me` on boot.
- **Settings** is a dialog over the current page, open while the URL has
  `?settings=general|account|usage|plugins` (any signed-in route; `data` means
  `plugins`, anything else `general`); opening pushes a history entry, switching
  tabs replaces it, Back or Escape closes it; the chat page's own URL changes
  underneath (a new chat getting `/c/<id>`, a deleted one going to `/`) keep it
  open; a full-screen sheet under 800px.
  Tabs: General (theme, accent, chat font size, default chat model, Enter to
  send), Account (profile, sign-ins, change password unless the env admin, log
  out other sessions), Usage (`GET /me/usage`, with a CSS/SVG daily chart and a
  table), Plugins & data (connection cards, export, delete all chats with a typed
  confirmation). The OAuth return `/?plugin=<id>&oauth=<result>` opens Settings →
  Plugins & data with the result. Details: `docs/briefs/ui-shell-settings.md`.
- **Theme engine** (`src/theme/appearance.ts`): `public/theme-boot.js`, a
  classic script loaded blocking in `<head>` (the CSP allows only `'self'`
  scripts), applies the preferences cached in `localStorage["rag.preferences"]`
  before the first paint as attributes on `<html>`: `data-theme` (the resolved
  `light`/`dark`), `data-theme-preference`, `data-accent`, `data-chat-font`, and
  sets the `color-scheme` and `theme-color` metas to the resolved theme. The
  engine follows `prefers-color-scheme` live while the preference is System,
  re-applies on `storage` events from other tabs, and reconciles with
  `Me.preferences` (the server wins unless a change is pending). Changes apply at
  once and are saved with `PATCH /me/preferences`; a failed save reverts.
- **Chat page layout** (desktop): CSS grid
  `[conversation sidebar 260px] [chat 1fr] [documents panel 360px]`. The chat
  covers the left and middle; the **document panel is on the right**. Under
  1100px the sidebar collapses behind a toggle; under 800px the document panel
  becomes a slide-over. A **top bar** over the chat column holds the model menu
  (left), the Plugins menu, the Documents toggle and the avatar menu (right). The
  Documents toggle opens the slide-over under 800px and collapses or expands the
  right-hand column at 800px and wider (remembered per browser; a collapsed panel
  stays mounted and keeps polling). The top bar compacts by the chat column's own
  width (container queries), never overflowing. The avatar menu
  has the username and role, this month's token and image meters, Settings, a
  theme switch, Admin (admins) and Log out; the sidebar has no footer. The page
  never scrolls.
- **Model menu**: new chats start with `Me.preferences.default_model_id` when the
  user may use it, else the server default; a pick applies to the current chat
  until New chat.
- **Composer "+" menu**: Attach files (uploads to My documents; chips show
  progress and indexing; Send waits until attached files are indexed; attached
  documents are added to an explicit selection), Create an image, Create a PDF,
  Word, Excel or Markdown file, and Gmail (search, draft, import; or Connect /
  Reconnect) insert starter prefixes or open the right UI; unavailable items are
  hidden, or disabled with the reason. `preferences.enter_to_send` false:
  Ctrl/Cmd+Enter sends and Enter adds a line.
- `ChatPage` (FE-CHAT) renders `<DocumentPanel selection={...}
  onSelectionChange={...} />` from `src/components/documents/DocumentPanel.tsx`
  (FE-DOCS) with this exact contract:

  ```ts
  // null = all accessible documents (default; newly uploaded docs are included automatically)
  // number[] = only these document ids ([] = don't use documents)
  export type DocumentSelection = number[] | null;
  export interface DocumentPanelProps {
    selection: DocumentSelection;
    onSelectionChange: (selection: DocumentSelection) => void;
    onClose?: () => void;            // shown as a close button in slide-over mode
  }
  export default function DocumentPanel(props: DocumentPanelProps): JSX.Element
  ```
  `ChatPage` passes `selection` straight through as `document_ids`.
  `DocumentSelection` is exported from `src/api/types.ts`.
- **Generated files** (FE-DOCS exports for FE-CHAT, in
  `src/components/documents/documentEvents.ts`; a module store, so
  `DocumentPanelProps` don't change):

  ```ts
  // A document created outside the panel (a file the assistant made): the panel inserts it, reloads its list
  // (so an older in-flight list can't drop it) and refreshes its quota.
  export function announceDocument(doc: DocumentItem): void
  export function subscribeToAnnouncements(listener: (doc: DocumentItem) => void): () => void
  // Ask the panel to reload its list and quota (a stream ended while a file was still being written, or a
  // card shows a document the panel's list doesn't have yet).
  export function requestDocumentsReload(): void
  export function subscribeToReloadRequests(listener: () => void): () => void
  // The panel's latest list (null until loaded or after unmount).
  export function publishDocuments(list: DocumentItem[] | null): void
  // undefined = no list loaded yet, or the list hasn't had this id yet (the hook then asks the panel once
  // to reload); null = it was in a published list and is gone (deleted)
  export function useDocumentSnapshot(id: number | null): DocumentItem | null | undefined
  ```
  The chat calls `announceDocument` on every `file` event with status
  `created` and shows a file card (type tag, name, size, progress while
  writing, status from `useDocumentSnapshot`, Download) under the answer;
  cards are rebuilt from `ChatMessage.files` on reload. Cards still writing when
  a stream stops become "stopped" and the chat calls `requestDocumentsReload`.
  Every document row in the panel has a Download link too
  (`GET /api/documents/{id}/download`), and rows with `generated` say "Created
  in chat". The composer hint mentions files when the selected model's
  `can_create_files` is true.
- **Generated images**: `file` events with `kind: "image"` drive an image card
  under the answer (writing, then `generating` with a placeholder of the final
  aspect ratio, then the preview from `GET /api/documents/{id}/image?size=preview`
  with filename, size, indexing status and Download; failed; stopped, which for
  a card that was `generating` says the image is still saved; deleted),
  rebuilt from `ChatMessage.files` (`file.image`) on reload. Clicking the preview
  opens the viewer, exported by FE-DOCS for FE-CHAT:

  ```ts
  // src/components/documents/ImageViewer.tsx: a Modal (Escape closes, focus trapped and returned) with the full image,
  // its alt text as caption, size, format and quality, the prompt (and revised prompt) loaded from
  // GET /api/documents/{id}/image/details when it opens, Download and Close.
  export type ImageViewerProps = { documentId: number; filename: string; image: GeneratedImage; onClose: () => void };
  export default function ImageViewer(props: ImageViewerProps): JSX.Element
  ```
  Document rows with `image` show a thumbnail (`size=thumb`) that opens the
  viewer. `client.ts` exports `documentImageUrl(id, size = "full")` and
  `getImageDetails(id)`. The
  composer hint and empty state mention images when the selected model's
  `can_create_images` is true, and the avatar menu's meters (and Settings →
  Usage) show images this month against `limits.monthly_image_limit`.
- **Plugins** (FE-CONN, `src/components/connections/`): the top bar's
  **Plugins** menu lists every plugin of `GET /plugins` with its status (section
  6, Plugins) and opens its card in Settings → Plugins & data ("Set up in Admin →
  Plugins" for admins when it isn't set up; "Manage plugins…"). The cards
  (`ConnectionsPanel`, inside Settings) show per plugin its status,
  account, capabilities ("Grant access" for enabled ones not granted), "Use in
  chat", the testing-mode end date (with a "reconnect now" hint in its last
  day), Connect / Reconnect / Disconnect (inline confirmation), and "Browse
  Gmail". In testing mode the card says before connecting that only accounts
  the admin added as Google test users can connect. Plugin state reloads when the
  window regains focus (at most every 30 seconds). Connecting navigates to `authorize_url`;
  back on `/?plugin=<id>&oauth=<result>` the app opens Settings → Plugins & data
  with a message for the result and replaces the URL with `/?settings=plugins`. A Gmail browser (search,
  threads, messages as plain text, attachments) imports selected threads and
  attachments (also from an "Import from Gmail" button in the document panel
  and the composer's "+" menu; one dialog at a time: Settings closes before it opens);
  imported documents are announced like created files, and their rows say
  "Imported from <imported_from>". Chat: repeated `sources` events replace the
  list; `EmailSource` entries show subject, sender, date, snippet and "Open in
  Gmail"; `plugin` events and `ChatMessage.plugin_cards` render cards under the
  file cards: activity lines, draft cards (recipients, subject, preview, the
  email replied to, "Open in Gmail"), send cards (the whole email, the time left,
  **Send** / **Edit** / **Cancel**; Send posts the `content_hash`; then Sent with
  a link, or failed, cancelled, expired, replaced) and import cards. Draft and
  send cards show each recipient's address apart from its display name, both
  forms of an internationalized domain with a warning, "not mentioned by you"
  on `unmentioned` recipients and "not named by you" on `unrequested`
  attachments, and Bcc highlighted. Email text is always rendered as text, never HTML.
  Admin → **Plugins**: configured or the missing settings, test-endpoint warning,
  enable and capability switches (with their notes), the scopes requested,
  redirect URIs to register, testing mode, connection counts (and connections on
  an older encryption key), a request-check Test per chat model, this month's
  activity, per-user activity, Disconnect all.
- Streaming uses `postSSE` from `src/api/client.ts` (fetch + ReadableStream,
  `AbortController` for the Stop button). Assistant messages render Markdown
  (`react-markdown` + `remark-gfm`, raw HTML disabled) with `[n]` citations
  linked to the sources list.
- Styling: plain CSS. `src/styles/global.css` defines tokens and primitives
  (`.btn`, `.btn-primary`, `.btn-ghost`, `.btn-danger`, `.btn-sm`, `.input`,
  `.select`, `.textarea`, `.card`, `.badge` + `.badge-{neutral,info,success,warning,danger}`,
  `.table`, `.switch`, `.field`, `.field-label`, `.field-hint`, `.muted`,
  `.error-text`, `.stack`, `.row`, `.spacer`; new primitives use the `ui-`
  prefix). Each feature group adds its own
  CSS file next to its components (`chat.css`, `documents.css`, `admin.css`,
  `connections.css`, `settings.css`, `pages/login.css`)
  using only its own class prefix (`chat-`, `docs-`, `admin-`, `conn-`,
  `settings-`, `login-`). Light and dark themes, five accents and three chat font sizes are
  token sets selected by the `<html>` attributes of the theme engine
  (`:root[data-theme="dark"]`, `:root[data-accent="…"]`,
  `:root[data-chat-font="…"]`); no CSS reads `prefers-color-scheme`. Every text
  and control colour meets WCAG 2.2 AA in both themes with every accent. Fonts are
  bundled woff2 files (SIL OFL), never loaded from a CDN, with metric-matched
  local fallback faces so the swap doesn't shift the layout.
- No `alert`/`confirm`/`prompt`: use inline confirmation or `<Modal>`.

## 9. Testing

`backend/tests/` holds integration tests runnable without Docker:
SQLite (`aiosqlite`), `fakeredis`, Qdrant `:memory:`, and LiteLLM mocked
(vision calls included; PDFs with images and scanned pages are built in code
with Pillow; the PDFium child process runs for real, and fake child scripts
simulate crashes and timeouts; tool-calling streams, interleaved thinking
included, are scripted in the shapes LiteLLM 1.103 produces for Anthropic and
OpenAI models, and one test runs the real LiteLLM against a local fake provider
server and checks the replayed request bodies; generated files are checked by
parsing them back with pypdf, pypdfium2, python-docx and openpyxl, and the
file-rendering child runs for real, with fake children for timeouts and
crashes; image generation is mocked at `litellm.aimage_generation` with real
PNG, JPEG and WebP bytes made with Pillow, and one test runs the real LiteLLM
against a local fake OpenAI images endpoint; tests marked `postgres` run only
with `TEST_POSTGRES_URL`, against a throwaway Postgres; plugins never call
Google: a fake Google (OAuth and Gmail, a FastAPI app in `tests/`) is reached
through `httpx.ASGITransport`, client edge cases use `httpx.MockTransport`, the
plugin registry is empty unless a test opts in, the same fake runs
standalone for Docker smoke runs, and one test runs the real LiteLLM for the
request check with the HTTP layer replaced and checks the request bodies).
Models therefore stick to portable column types (`JSON`, not `JSONB`).
Run with `pytest` from `backend/` (see `backend/tests/README.md`). The
Postgres database must be a throwaway one: its name must contain `test` or
`scratch`, and a `public` schema holding tables the tests didn't create (they
mark the schema they create) is never emptied. Cross-cutting guards cover new
code without being edited: every route of the route table (hidden ones
included) answers 401 without a session unless it is public, and every route
taking a user's resource id is classified; every foreign key cascades or is a
listed `SET NULL` key, and deleting a user, a conversation or a document is
checked over every table those keys reach.

`integration/` runs the whole stack in Docker: `integration/run.sh` starts an
isolated Compose project (`rag-it`: its own volumes, an internal network, a
generated throwaway env file; the repository's `.env` is never read), runs
pytest from the host against it and tears it down. Ports on 127.0.0.1 come from
`IT_APP_PORT`, `IT_FAKE_LLM_PORT` and `IT_FAKE_GOOGLE_PORT` (defaults in
`integration/README.md`). Every enabled model points at `fake_llm/` (an
OpenAI-compatible chat, embedding, vision and image server) and Google at
`fake_google/`, which runs `backend/tests/fake_google.py`, `fake_gmail.py` and
`fake_google_server.py` (mounted read-only from `backend/tests`), so changes to
those fakes must keep both suites passing. Real providers and Google are never
reached.

`e2e/` is a Playwright suite (Chromium, desktop and phone) against the running
stack (`http://localhost:8080` by default). It creates and deletes its own
`e2e-<run id>-<random>` users through the admin API, answers chat with canned
SSE, and blocks model, Google, upload and re-index calls (and, in the admin's
browser context, every write except to its own test users); the one `@live`
test (a real model call) is skipped unless `E2E_LIVE=1`. See `e2e/README.md`.

`frontend/src/**/*.test.ts(x)` are Vitest tests (Testing Library, jsdom) with
`fetch` stubbed, run with `TZ=UTC` and the `en_US` locale; `npm run
test:coverage` fails below the thresholds in `vite.config.ts`. See
`frontend/src/test/README.md`.

`.github/workflows/ci.yml` runs every suite on pushes to `main` and pull
requests: the backend with ruff, mypy and coverage (at least 94 %), the
`postgres`-marked tests against a Postgres service, the frontend (`tsc`,
Vitest, the build), e2e against a stack with a throwaway `.env` and fake
provider keys, and `integration/run.sh`.
