# Handoff: status and next steps

Snapshot taken 2026-10-14 (IST) on macOS (Apple Silicon), after the full build, review and Docker
run. `SPEC.md` is the binding contract; `README.md` has setup, commands and the architecture.

## Status

Everything in `SPEC.md` is built, tested, reviewed and running in Docker:

- **Backend:** auth and admin (`bootstrap.py`, `routers/{auth,models,admin}.py`), ingestion
  (`services/{parsing,chunking,ingestion}.py`, `worker.py`, `routers/documents.py`), chat
  (`services/rag.py`, `routers/{conversations,chat}.py`), on top of the foundation modules.
- **Frontend:** chat page (`pages/ChatPage.tsx`, `components/chat/`), document panel
  (`components/documents/`), admin page (`pages/AdminPage.tsx`, `components/admin/`).
- **Infra:** Docker Compose stack with six services (postgres, redis, qdrant, backend, worker,
  frontend/nginx).

How the work went: the lead wrote `SPEC.md` and the foundation code. Agents then built the
feature areas in parallel from `docs/briefs/`, followed by backend integration tests, frontend
verification, an adversarial review against `docs/briefs/review-checklist.md` (46 confirmed
findings: 3 high, 14 medium, 29 low), fixes for those, Docker and Postgres checks, and a final
verification pass that fixed two more bugs (see below).

The Compose project `rag-app` was left running with its data. At the time of this snapshot the
whole build is **uncommitted** on top of the scaffold commit `e56133e`, so check `git status`
before relying on history. The agents' test scripts lived in scratch directories and are not in
the repo.

## How it was verified

| Check | Result |
|---|---|
| Backend suite (`cd backend && .venv/bin/python -m pytest -q`) | 697 passed in about 70 s (SQLite, fakeredis, in-memory Qdrant, mocked LiteLLM) |
| Backend suite against a throwaway Postgres 17 (scratch copy of `conftest.py`, run before the last 2 tests were added) | 694 passed; only the fresh-interpreter import test was left out, because the scratch layout broke its path |
| Lint and types (commands in `README.md`) | ruff 0.13.0 and mypy 1.18.2 clean on `app/` (ruff on `tests/` too) |
| Frontend `npm ci`, `npx tsc --noEmit`, `npx vite build` | clean; `npm audit` reports 0 vulnerabilities |
| `docker compose up -d --build` (Docker Desktop, arm64) | all 6 services up, 5 healthy (the worker has no healthcheck); `/api/health` returns 200 through nginx |
| API smoke test against the live stack | 35 passed, 1 skipped (the disabled embedding model): admin login, user creation, PDF and DOCX ingestion, duplicate rejected, vectors in Qdrant, SSE event order, citations from both documents, Stop mid-answer saves the partial answer and its usage, usage meter, admin usage report (daily grouping on Postgres), stats, cleanup including vectors |
| Real model calls | Admin **Test** returned OK for all 5 seeded chat models and `text-embedding-3-small` (1536 dims). Streaming usage is provider-reported, not estimated, for Anthropic and OpenAI |
| Browser check (Playwright, Chromium) | 36 steps passed 3 times: login, upload, streaming, sources, Stop, reload, layouts at 1440/1000/700/390 px, dark mode, admin at 390/800 px, modal focus trap; no page errors, CSP violations or page-level scrolling |
| Postgres behaviour | `create_all` advisory lock (6 processes at once: 0 of 18 failures, 15 of 18 without it); all timestamps `timestamptz`; usage report splits days in UTC even with a +05:45 database default; native booleans |
| Failure handling | worker SIGKILLed mid-document: re-queued on start, finished with exactly 800 chunks and no duplicate vectors. Graceful worker stop finished the in-flight job. Redis stopped for 20 s: worker backed off and recovered, and an upload made during the outage was queued when Redis returned. `docker compose restart backend` mid-answer took 15.8 s and stored the partial answer and its usage |
| Postgres row locks (scratch tests) | delete-while-finishing race: passes with the lock, leaves an orphan without it; admin reset racing change-password: correct in 8 of 8 runs |

Late fixes from the final pass: the chat stopped following new text during fast streams and after
**Jump to latest** (`MessageList.tsx`: now only an upward user scroll stops following), and a
backend restart lost streaming answers (`chat.drain_answers`, awaited in the lifespan before
Redis, Qdrant and the database close; two tests in `test_chat.py`).

## Decisions a maintainer must know

All contract changes are already in `SPEC.md` and `frontend/src/api/types.ts`.

### API contract

- **503** with `{"detail": "A required service is temporarily unavailable. Try again shortly."}`
  when Redis is unreachable (login, chat rate limit, admin stats), and 503 when vectors can't be
  removed on a delete (the row and file are kept; retrying is safe).
- **Uploads:** an empty file is a 422. Reprocessing a document that is already `pending` returns it
  unchanged and doesn't queue it again. Reprocess and reindex return 400 when there is no active
  embedding model. `only_stale` reindexing also re-queues failed documents.
- **Chat:** `document_ids` accepts at most 10 000 ids (the brief said 500); `message` is 1 to
  20 000 characters, and only the last 4 000 characters of the retrieval query are used. Users with
  a monthly budget can have at most 2 answers generating at once (`MAX_CHATS_IN_FLIGHT`; 429
  "Another answer is still being generated…" with `Retry-After: 5`), because the budget is checked
  at the start of an answer and charged at the end. Usage is committed before the assistant message,
  so an answer that can't be stored is still billed. NUL characters are dropped from answers.
- A stopped answer is stored with `error = "Stopped before completion"`. The frontend matches this
  exact string to show a muted "Stopped" note instead of an error, so change it on both sides or not at all.
- **Login throttle:** 10 failures per username and 100 per client IP within 15 minutes. A wrong
  current password on change-password counts toward the username's limit.
- **Admin rules:** the env admin account can't be changed or deleted through the API (400). The
  active embedding model can't be deleted, disabled or un-defaulted (409). A disabled model can't
  become the default (400). Changing an embedding model's `litellm_model` or `api_base` drops its
  Qdrant collection and marks its documents stale.
- **PATCH semantics.** `PATCH /admin/users/{id}`: omitted or null fields stay unchanged; a blank
  password keeps the current one, and any other password is stored exactly as typed (no trimming).
  `PATCH /admin/models/{id}`: null (or an empty string) clears an optional setting (`api_base`,
  `api_key_env`, `max_output_tokens`, `temperature`, the costs), but leaves a required field
  (`display_name`, `litellm_model`, `enabled`, `is_default`) unchanged.
- **Model keys** (`llm._base_kwargs`): `api_key_env` must match `*_API_KEY`, and `QDRANT_API_KEY` is
  reserved. If the named variable is unset, the call fails with a message, with no fallback. A model
  with an `api_base` and no `api_key_env` gets the placeholder key `unused`, so provider keys never
  reach a custom host.
- **max_tokens** (`llm.resolve_max_tokens`): the model's `max_output_tokens`, or else
  `DEFAULT_MAX_OUTPUT_TOKENS` (16000) lowered to the cap in LiteLLM's bundled model map. The admin
  **Test** sends the same value.
- **Seeding** happens only when `llm_models` is empty (the first start). A seeded model is enabled
  only if its provider key was set then. The default chat model is Claude Opus 5.5 (GPT-5.4 mini if
  only the OpenAI key is set); `text-embedding-3-large` is always seeded disabled.

### Database

- Timestamps use `models.UTCDateTime`, which is timezone-aware on SQLite too. asyncpg sessions are
  forced to `timezone=UTC` (`db.make_engine`), so `date()` in the usage report splits days in UTC
  whatever the server's `TimeZone`.
- `init_db` runs `create_all` under `pg_advisory_xact_lock`, because the backend and the worker both
  run it and can start at the same time (e.g. `docker compose restart backend worker`). There is no Alembic.
- Ids and limits are validated against Postgres `INTEGER`/`BIGINT` maxima, so they return 422, not 500.
- Postgres row locks guard several races: upload quota and duplicate checks (`FOR NO KEY UPDATE`
  on the user row, plus a per-user asyncio lock), admin reset against change-password, document
  delete against the worker finishing it (`storage.lock_documents`), embedding-model edit or delete
  against a running job, and chat-model delete against saving an answer (`FOR KEY SHARE`). SQLite
  ignores them all; see "Open risks".

### Ingestion

- **One worker process** runs `WORKER_CONCURRENCY` jobs. On start it re-queues `pending` and
  `processing` documents. Every 60 s, and right after Redis comes back, it also queues `pending`
  documents missing from the queue (older than 10 s) and retries `processing` documents that none
  of its jobs is running (older than 60 s). This assumes exactly one worker container.
- **Poison-pill guard:** interrupted runs are counted in Redis (`rag:ingest_attempts:<id>`,
  1-day TTL). After 3, the document becomes `failed` instead of being retried forever. Reprocess,
  reindex and delete reset the count.
- **Parsing limits:** `MAX_UNCOMPRESSED_MB` 100, `MAX_XML_PART_MB` 32, and `MAX_EXTRACTED_CHARS`
  5 000 000 (text, JSON and HTML files over 4 bytes per allowed character are refused before
  parsing). Also `MAX_PDF_PAGES` 5000 and `PARSE_TIMEOUT_SECONDS` 300, checked between PDF pages,
  slides and every 1000 spreadsheet rows. PDF pages that draw the same content are read once. HTML
  text comes from one iterative walk that is linear in nesting depth. AES-encrypted PDFs need
  `cryptography`, which is pinned; PDFs that require a user password are refused.
- If the embedding model is edited or deleted during a run, the run's vectors are dropped and the
  document is re-queued for the model that is active now.

### Infrastructure

- **nginx** (`frontend/nginx.conf`, a template): the body limit is 1 MB everywhere except
  `location = /api/documents`. That location allows `MAX_UPLOAD_MB_HARD` + 10 MB
  (`upload-limit.envsh` renders it at container start), checks the session with `auth_request`
  before forwarding a POST body, and streams the request. State-changing requests with
  `Sec-Fetch-Site: same-site|cross-site` get 403, because SameSite cookies ignore ports. The CSP
  allows only `'self'` scripts, which is why Swagger UI at `/api/docs` is blank behind nginx.
- **Shutdown:** the backend has `stop_grace_period: 30s` and uvicorn `--timeout-graceful-shutdown 15`,
  then `drain_answers` waits up to 10 s so cancelled streams can save. The worker has
  `stop_grace_period: 75s`, and running jobs get 60 s.
- Healthchecks exist for every service except the worker. The frontend's goes through nginx to `/api/health`.
- `LITELLM_LOCAL_MODEL_COST_MAP=True`, so the cost map isn't fetched from GitHub at start.
  LiteLLM's own logger is set to WARNING.
- **Supply chain:** Python dependencies are hashed universal locks (`uv pip compile --exclude-newer`;
  commands at the top of `backend/requirements.in`), installed with `--require-hashes --no-deps` and
  `pip check`. The npm lock was regenerated with `--before`, so no package is under 14 days old.
  Base images are pinned by tag and digest. The backend uses `python:3.13.15-slim` because 3.13.16
  was under two weeks old.

### Frontend

- Routes: `/login`, `/`, `/c/:conversationId`, and `/admin`. The admin page is a lazy chunk: it
  reloads once if the chunk is missing after a redeploy, then falls back to the app-wide `ErrorBoundary`.
- Modals are portaled, make `#root` inert and trap focus. A window-level guard
  (`lib/blockStrayFileDrops.ts`) stops a file dropped outside the panel from replacing the tab.
- A `/auth/me` failure on boot that isn't a 401 shows "Can't reach the server" with Retry. Login
  returns to the requested URL, except after an explicit logout. Logout waits for the server and
  reports failure.
- Stop before the `meta` event in a new chat undoes the turn and restores the draft. The model list
  is reloaded after a rejected send.

## Known limitations and open risks

- **Parsing isn't isolated.** It runs in the worker process via `asyncio.to_thread` (SPEC section 7),
  and the worker container has no memory limit. Format caps, the parse deadline and the 3-strike
  counter bound the damage, but one pathological page can still overrun the deadline, which is only
  checked between pages, and a file can still use a lot of memory. Docker Desktop's VM here has
  about 8 GB.
- **Postgres-only locking isn't in the suite.** The suite runs on SQLite, which ignores row locks.
  The delete race and the password-reset race were checked with scratch tests against Postgres 17,
  and the whole suite passed on Postgres once. The upload lock across several backend processes and
  the embedding-model locks were never raced there.
- **One worker only.** A second worker container would reset the other's in-flight documents (the
  start-up recovery and the sweep assume a single process).
- **The per-IP login throttle is effectively global.** Behind Docker's port publishing every client
  shares one IP, so 100 failed logins from anyone within 15 minutes block all logins until the
  window passes. nginx has no `set_real_ip_from`/`real_ip_header` for an outer proxy.
- **Without nginx** (uvicorn on :8000 in development), upload bodies are read before the session
  check, there is no 1 MB body cap, and 422 responses echo the rejected input.
- **Budget overspend is bounded, not zero:** up to 2 in-flight answers can finish past the limit.
  Embedding tokens don't count toward the budget (by design).
- No HTTPS, `COOKIE_SECURE=false`, no backups, no migrations.
- The frontend has no automated tests in the repo. The browser and smoke scripts weren't committed.
- `pytest` 8.4.2 has advisory GHSA-6w46-j5rx-g56g (dev only; fixed in 9.0.3). It was left pinned
  because a major bump may break `pytest-asyncio` 1.2.0.
- Not done (optional): rate limits on upload and reprocess, and clearing a user's login-failure
  counter when the admin resets their password.
- Minor UI: right after Stop, the sidebar's "Tokens this month" can lag until the next refresh,
  because the refresh can run before the server saves the stopped answer's usage. A document row's
  meta line can wrap with a trailing ` ·`.
- Ops quirk: one `docker compose up -d --build` built a new frontend image but kept the old
  container. `docker compose up -d --force-recreate frontend` fixed it.
- An agent's command printed the OpenAI API key into that session's tool output (never into a
  file). Rotate the key.

## Next steps

1. Review `git status` and commit the build.
2. Rotate the OpenAI API key, update `.env`, and run `docker compose up -d`.
3. Add Alembic before any schema change on a database with real data. Baseline it on the current
   models and keep the advisory lock around the migration run.
4. Before exposing the app beyond localhost: put HTTPS in front, set `COOKIE_SECURE=true`,
   configure `set_real_ip_from`/`real_ip_header` in `nginx.conf` for the outer proxy so the per-IP
   throttle sees real clients, and consider rate limits on upload and reprocess.
5. Set up backups. Postgres and the `uploads` volume hold everything that matters:
   ```bash
   docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > rag-$(date +%F).dump
   docker compose exec -T worker tar czf - -C /data/uploads . > uploads-$(date +%F).tgz
   ```
   Both commands were checked against the running stack; a restore hasn't been tested. Qdrant can
   be rebuilt from the uploads with Admin → Documents → **Re-index all documents** (this costs
   embedding tokens). Redis holds only transient state (queue, throttles, answer slots, attempt
   counters), and the worker re-queues pending documents from Postgres.
6. Give the worker a memory limit sized to Docker's VM, e.g. `mem_limit: ${WORKER_MEM_LIMIT:-3g}`
   with the same `memswap_limit`. An OOM kill then stays inside the worker, and the attempt counter
   fails the document after 3 kills.
7. Add CI: the backend suite on SQLite and on Postgres 17 (to cover the locks), tsc and the build,
   plus a committed Playwright smoke test for the UI.
8. Later: move parsing into a child process with a hard kill, and bump pytest to 9.x together with
   a compatible pytest-asyncio.

## macOS setup notes (this machine)

- Apple Silicon, macOS 26, Docker Desktop (engine 29.8, Compose v5.6). Docker Desktop's Rosetta
  emulation isn't needed and can stay disabled: every image in the stack is arm64 (multi-arch base
  images, native local builds). The `docker` CLI is in `~/.docker/bin`, which isn't on `PATH` by
  default: `export PATH="$HOME/.docker/bin:$PATH"`.
- No Homebrew. Python 3.13 is managed by uv (`~/.local/bin/uv`), and `backend/.venv` was created
  with `uv venv --python 3.13`, so it has no pip: refresh it with `uv pip sync requirements-dev.txt`.
  The system `python3` is 3.14 (python.org); use 3.13 for the backend, as the image does.
- Node 24 (npm 11) comes from the official tarball in `~/.local/bin`. npm 11 warns that the
  optional `fsevents` install script was skipped, which is harmless. The Docker build uses Node 22.23.3.
- `.env` lives in the repo root next to `docker-compose.yml` and is git-ignored, so copy
  `.env.example` and fill it in on each machine. The backend reads only environment variables:
  Compose passes `.env` in, and outside Docker you load it into the shell (see `README.md`).
- Ollama isn't installed here.
