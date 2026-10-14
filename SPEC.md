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
  `litellm.aembedding`). Provider keys come from env (`OPENAI_API_KEY`,
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
  is always ≥ 1 and capped by `MAX_UPLOAD_MB_HARD`. The admin bypasses all
  usage checks.
- **Monthly budget** counts **chat (LLM) tokens only** (prompt + completion)
  in the current UTC calendar month. Embedding and vision tokens are recorded
  and shown but don't count toward the budget. Vision tokens have a separate
  monthly allowance per user, `VISION_MONTHLY_TOKEN_LIMIT` (section 7; the
  admin is exempt).

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
        llm.py              FOUNDATION  LiteLLM wrappers: stream_chat, embed_texts, read_image, test_model, compute_cost
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
      routers/
        __init__.py
        auth.py             AUTH-ADMIN  /api/auth/*
        models.py           AUTH-ADMIN  /api/models
        admin.py            AUTH-ADMIN  /api/admin/*
        documents.py        INGESTION   /api/documents/*
        conversations.py    CHAT        /api/conversations/*
        chat.py             CHAT        /api/chat/stream
    tests/                  (integration tests, see section 9)
  frontend/
    Dockerfile  .dockerignore  package.json  package-lock.json  vite.config.ts
    tsconfig.json  index.html
    nginx.conf  upload-limit.envsh            nginx template; its upload body limit follows MAX_UPLOAD_MB_HARD
    src/
      main.tsx  App.tsx                        FOUNDATION
      api/client.ts  api/types.ts              FOUNDATION
      auth/AuthContext.tsx                     FOUNDATION
      styles/global.css                        FOUNDATION (design tokens + primitives)
      components/ui/Modal.tsx, Spinner.tsx     FOUNDATION
      pages/LoginPage.tsx                      FOUNDATION
      pages/ChatPage.tsx + components/chat/*   FE-CHAT
      components/documents/*                   FE-DOCS
      pages/AdminPage.tsx + components/admin/* FE-ADMIN
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
  "anthropic/claude-sonnet-5-5", "ollama/llama3.1"), kind ("chat"|"embedding"|"vision"),
  enabled, is_default, api_base, api_key_env, max_output_tokens, temperature,
  input_cost_per_mtok, output_cost_per_mtok, embedding_dim, created_at,
  updated_at`. At most one `is_default` per kind. The default embedding model
  is the **active** one: all indexing and retrieval use it. The default vision
  model, when enabled, is the **active** vision model: ingestion uses it to read
  PDF pages (section 7); without one, PDFs are read from their text layer only.
  Vision models are never offered to users as chat models. `api_key_env` is
  the *name* of an env var holding the key (must match `^[A-Z][A-Z0-9_]*_API_KEY$`).
  Costs are USD per 1M tokens; when null, LiteLLM's cost map is used.
- **documents**: `id, owner_id → users (CASCADE), scope ("private"|"shared"),
  filename, stored_name (uuid + ext, in UPLOAD_DIR), content_type, size_bytes,
  sha256, status ("pending"|"processing"|"ready"|"failed"), error, num_chunks,
  num_pages, embedding_model_id → llm_models (SET NULL), created_at, updated_at`.
- **conversations**: `id, user_id → users (CASCADE), title, created_at, updated_at`.
- **messages**: `id, conversation_id → conversations (CASCADE), role
  ("user"|"assistant"), content, sources (JSON list, see Source), model_id,
  model_name, prompt_tokens, completion_tokens, error, created_at`.
- **usage_events**: `id, user_id → users (CASCADE), model_id (SET NULL),
  model_name, kind ("chat"|"embedding"|"vision"), prompt_tokens, completion_tokens,
  total_tokens, cost_usd, created_at`. Ingestion records at most one `embedding`
  and one `vision` event per document run, for the document's owner.
- **vision_pages**: `document_id → documents (CASCADE), page, mode
  ("transcribe"|"describe")` (together the primary key), `fingerprint, text,
  created_at`. The vision model's cleaned output for one page of one document
  (`text` "" = nothing to add). Later runs of the same document reuse a row
  while its `fingerprint` (a hash of the vision model's `litellm_model` and
  `api_base`, the prompt version, `VISION_MAX_IMAGE_PX` and the page
  `max_tokens`) matches; a successful run deletes the rows it didn't use.
  Never shared between documents, not even identical files.
- **app_state**: `key (primary key), value, updated_at`. Server flags, e.g.
  `vision_seeded` (section 6, Admin).
- New tables are created by `create_all` on an existing database; no column is
  added to an existing table, so no migration is needed.

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
  max_upload_mb: number; max_documents: number; max_storage_mb: number }
type UsageSummary = { month: string /* "YYYY-MM" */; chat_tokens: number;
  embedding_tokens: number; vision_tokens: number;
  cost_usd: number /* every kind */; monthly_token_limit: number /* counts chat_tokens only */ }
type Me = { id: number; username: string; is_admin: boolean;
  limits: UserLimits; usage: UsageSummary }
type ChatModel = { id: number; display_name: string; provider: string; is_default: boolean }
type DocumentItem = { id: number; filename: string; scope: "private"|"shared";
  status: "pending"|"processing"|"ready"|"failed"; error: string|null;
  size_bytes: number; content_type: string; num_chunks: number;
  num_pages: number|null; owner_id: number; owner_username: string;
  can_delete: boolean; is_stale: boolean;
  vision_pages: number /* pages read with the vision model (vision_pages rows); 0 unless status is "ready" */;
  created_at: string; updated_at: string }
type DocumentConfig = { supported_extensions: string[] /* e.g. [".pdf", ".docx"] */;
  max_upload_mb: number; max_documents: number; max_storage_mb: number;
  document_count: number; storage_bytes: number; can_upload_shared: boolean }
type Source = { index: number /* 1-based citation number */; document_id: number;
  filename: string; page: number|null; chunk_index: number; score: number;
  snippet: string /* ≤ 500 chars of the chunk */ }
type ConversationSummary = { id: number; title: string; created_at: string; updated_at: string }
type ChatMessage = { id: number; role: "user"|"assistant"; content: string;
  sources: Source[]; model_name: string|null; prompt_tokens: number;
  completion_tokens: number; error: string|null; created_at: string }
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
  as bad credentials ("Invalid username or password"). Updates `last_login_at`.
- `POST /auth/logout` → 204; clears the cookie.
- `GET /auth/me` → `Me`.
- `POST /auth/change-password` `{current_password, new_password}` (new ≥ 8
  chars) → 204; bumps `token_version` and re-issues the cookie. The env admin
  gets 400 ("The admin password is managed in .env"); a wrong current password
  gets 400 and counts toward the login throttle for that username (429).

### Models — `routers/models.py`
- `GET /models` → `ChatModel[]`: enabled chat models (never embedding or vision models) this user may use,
  default first, then by display_name. `provider` = prefix before `/` of
  `litellm_model` (or `"openai"` if no prefix).

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

### Conversations — `routers/conversations.py`
- `GET /conversations` → `ConversationSummary[]` (caller's, `updated_at` desc, max 200).
- `GET /conversations/{id}` → `ConversationDetail` (messages oldest first).
- `PATCH /conversations/{id}` `{title}` (1–200 chars) → `ConversationSummary`.
- `DELETE /conversations/{id}` → 204.

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
| `sources` | `{sources: Source[]}` (always sent, possibly empty) |
| `warning` | `{message}` (0..n, e.g. "Document search unavailable: …") |
| `delta` | `{content: string}` (many) |
| `done` | `{assistant_message_id, usage: {prompt_tokens, completion_tokens, total_tokens, cost_usd}}` |
| `error` | `{message, assistant_message_id: number|null}` (terminal, instead of `done`) |

Server behaviour: new conversation title = first 60 chars of the message
(whitespace collapsed). The user message is saved before streaming; the
assistant message is saved at the end, **including partial content if the
client disconnects or the provider errors** (then `error` is set). Usage is
recorded as a `chat` usage event (provider-reported tokens, else estimated
with `litellm.token_counter`), committed before the message so a message that
can't be stored is still billed; NUL characters are dropped from the answer. Retrieval failures don't fail the chat: they
emit `warning` and continue without context. History: last `HISTORY_MESSAGES`
messages of the conversation (excluding the new one). Retrieval query = the
new message, prefixed by the previous user message when one exists.

### Admin — `routers/admin.py` (all require admin; non-admin → 403)
```ts
type AdminUser = { id: number; username: string; is_admin: boolean; is_active: boolean;
  monthly_token_limit: number; rate_limit_per_min: number; max_upload_mb: number;
  max_documents: number; max_storage_mb: number; restrict_models: boolean;
  allowed_model_ids: number[]; created_at: string; last_login_at: string|null;
  usage: { chat_tokens: number; embedding_tokens: number; vision_tokens: number; cost_usd: number }; // current month
  document_count: number; storage_bytes: number }
type AdminUserCreate = { username: string /* 3-64, ^[a-zA-Z0-9_.-]+$, lowercased */;
  password: string /* >= 8 */; is_active?: boolean; monthly_token_limit?: number;
  rate_limit_per_min?: number; max_upload_mb?: number; max_documents?: number;
  max_storage_mb?: number; restrict_models?: boolean; allowed_model_ids?: number[] }
type AdminUserUpdate = Partial<Omit<AdminUserCreate, "username">>  // password = reset
type ModelKind = "chat"|"embedding"|"vision"
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
type ModelTestResult = { ok: boolean; message: string; latency_ms: number; embedding_dim: number|null }
type AdminConfig = { defaults: UserLimits; max_upload_mb_hard: number; admin_username: string }
type AdminStats = { users: number; documents: number; stale_documents: number;
  failed_documents: number; active_embedding_model: {id: number; display_name: string}|null;
  active_vision_model: {id: number; display_name: string}|null; queue_length: number }
type UsageReport = { month: string;
  totals: { chat_tokens: number; embedding_tokens: number; vision_tokens: number; cost_usd: number; requests: number };
  by_user: { user_id: number; username: string; chat_tokens: number; embedding_tokens: number; vision_tokens: number; cost_usd: number; requests: number }[];
  by_model: { model_name: string; kind: ModelKind; prompt_tokens: number; completion_tokens: number; cost_usd: number; requests: number }[];
  daily: { date: string /* YYYY-MM-DD */; chat_tokens: number; embedding_tokens: number; vision_tokens: number; cost_usd: number }[] }
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
  (422 otherwise).
- `GET /admin/models` → `AdminModel[]` · `POST /admin/models` → 201 ·
  `PATCH /admin/models/{id}` · `DELETE /admin/models/{id}` → 204 ·
  `POST /admin/models/{id}/test` → `ModelTestResult`. Setting `is_default`
  clears it on the other models of the same kind; making a disabled model the
  default → 400. Vision models follow the chat-model rules: disabling,
  un-defaulting or deleting the active vision model is allowed and turns PDF
  page reading off. Deleting the active embedding model, disabling it
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
  vision system prompt (`ok` only if the reply contains 4712). Bootstrap adds a
  default vision model once, recorded as `app_state` key `vision_seeded` (an
  existing database gets it on its next start): "Claude Sonnet 5.5 (vision)"
  `anthropic/claude-sonnet-5-5`, enabled if `ANTHROPIC_API_KEY` is set, or
  "GPT-5.4 mini (vision)" `openai/gpt-5.4-mini` if only `OPENAI_API_KEY` is
  set. A vision model the admin deletes is not added again.
- `POST /admin/reindex` `{only_stale?: boolean /* default true */}` →
  `{queued: number}`: sets matching documents (`only_stale`: stale and failed
  ones; otherwise all) to "pending" and enqueues them. 400 when there is no
  active embedding model.
- `GET /admin/usage?month=YYYY-MM` (default current month) → `UsageReport`.
  `requests` counts chat usage events only; `cost_usd` covers every kind.
- `GET /admin/documents` → `DocumentItem[]` (all documents, every owner).
  `DELETE /admin/documents/{id}` → 204 (any document).

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
   `num_pages` and `embedding_model_id`. Any failure → `failed` with a
   human-readable `error` (≤ 1000 chars). If the embedding model was edited
   (`litellm_model`/`api_base`) or deleted during the run, its vectors are
   dropped and the document goes back to `pending` and is re-enqueued for the
   model active now (Postgres row locks keep the run from recreating a
   dropped collection).
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
   with a logged warning. A document in this phase doesn't hold a
   `WORKER_CONCURRENCY` slot (at most `WORKER_CONCURRENCY` documents are in it
   at once).
   *Cost*: vision tokens are one `vision` usage event per run for the owner,
   summed over the calls that completed, also when the run fails or stops; they
   don't count toward the monthly chat budget. If the owner already used
   `VISION_MONTHLY_TOKEN_LIMIT` this month when the run starts, only cached
   pages are used: a scanned page that isn't cached fails the document, and
   descriptions are skipped. Pages read by the vision model are chunked,
   embedded and cited by page like any other.
5. Supported: `.pdf .docx .pptx .xlsx .html .htm .md .markdown .txt .csv .json`.
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

- Routes: `/login` (public), `/` chat (auth), `/admin` (admin only; others
  redirected to `/`). `AuthContext` loads `/api/auth/me` on boot.
- **Chat page layout** (desktop): CSS grid
  `[conversation sidebar 260px] [chat 1fr] [documents panel 360px]`. The chat
  covers the left and middle; the **document panel is on the right**. Under
  1100px the sidebar collapses behind a toggle; under 800px the document panel
  becomes a slide-over opened from the chat header.
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
- Streaming uses `postSSE` from `src/api/client.ts` (fetch + ReadableStream,
  `AbortController` for the Stop button). Assistant messages render Markdown
  (`react-markdown` + `remark-gfm`, raw HTML disabled) with `[n]` citations
  linked to the sources list.
- Styling: plain CSS. `src/styles/global.css` defines tokens and primitives
  (`.btn`, `.btn-primary`, `.btn-ghost`, `.btn-danger`, `.btn-sm`, `.input`,
  `.select`, `.textarea`, `.card`, `.badge` + `.badge-{neutral,info,success,warning,danger}`,
  `.table`, `.switch`, `.field`, `.field-label`, `.field-hint`, `.muted`,
  `.error-text`, `.stack`, `.row`, `.spacer`). Each feature group adds its own
  CSS file next to its components (`chat.css`, `documents.css`, `admin.css`)
  using only its own class prefix (`chat-`, `docs-`, `admin-`). Light and dark
  themes via `prefers-color-scheme`.
- No `alert`/`confirm`/`prompt`: use inline confirmation or `<Modal>`.

## 9. Testing

`backend/tests/` holds integration tests runnable without Docker:
SQLite (`aiosqlite`), `fakeredis`, Qdrant `:memory:`, and LiteLLM mocked
(vision calls included; PDFs with images and scanned pages are built in code
with Pillow; the PDFium child process runs for real, and fake child scripts
simulate crashes and timeouts).
Models therefore stick to portable column types (`JSON`, not `JSONB`).
Run with `pytest` from `backend/` (see `backend/tests/README.md`).
