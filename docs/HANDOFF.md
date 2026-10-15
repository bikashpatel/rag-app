# Handoff: status and next steps

Snapshot taken 2026-10-14 (IST) on macOS (Apple Silicon), after the full build, review and Docker
run; updated 2026-10-15 after the vision model for PDFs was built, reviewed, fixed and verified in Docker.
`SPEC.md` is the binding contract; `README.md` has setup, commands and the architecture.

## Status

Everything in `SPEC.md` is built, tested, reviewed and running in Docker:

- **Backend:** auth and admin (`bootstrap.py`, `routers/{auth,models,admin}.py`), ingestion
  (`services/{parsing,chunking,ingestion}.py`, `worker.py`, `routers/documents.py`), chat
  (`services/rag.py`, `routers/{conversations,chat}.py`), on top of the foundation modules.
- **Frontend:** chat page (`pages/ChatPage.tsx`, `components/chat/`), document panel
  (`components/documents/`), admin page (`pages/AdminPage.tsx`, `components/admin/`).
- **Infra:** Docker Compose stack with six services (postgres, redis, qdrant, backend, worker,
  frontend/nginx).
- **Vision model for PDFs** (`docs/briefs/vision-pdf.md`, revision 2): scanned PDF pages are transcribed and
  images on pages with text are described by the active vision model ("Claude Sonnet 5.5 (vision)"); see
  "Vision model for PDFs" below. Backend: `services/{pdf_images,pdf_render_child,vision}.py` plus changes to
  parsing, ingestion, the worker, bootstrap, usage and the admin API; tests in `test_pdf_images.py` and
  `test_vision.py`. Frontend: a "Vision models" section in Admin → Models, vision tokens in Usage and Users,
  a seventh overview tile (the active vision model), and "pages read by vision" on documents.

How the work went: the lead wrote `SPEC.md` and the foundation code. Agents then built the
feature areas in parallel from `docs/briefs/`, followed by backend integration tests, frontend
verification, an adversarial review against `docs/briefs/review-checklist.md` (46 confirmed
findings: 3 high, 14 medium, 29 low), fixes for those, Docker and Postgres checks, and a final
verification pass that fixed two more bugs (see below). The vision feature followed the same pattern: a
brief, an adversarial critique and a second revision of the brief, parallel backend and frontend builds, a
review (5 confirmed findings, all fixed with regression tests), then a Docker rebuild and live runs.

The Compose project `rag-app` was left running with its data. At the time of this snapshot the
whole build is **uncommitted** on top of the scaffold commit `e56133e`, so check `git status`
before relying on history. The agents' test scripts lived in scratch directories and are not in
the repo.

## How it was verified

| Check | Result |
|---|---|
| Backend suite (`cd backend && .venv/bin/python -m pytest -q`) | 856 passed in about 91 s (SQLite, fakeredis, in-memory Qdrant, mocked LiteLLM, the real PDF render child), 3 runs on 2026-10-15 after the vision review fixes; 697 before the vision feature |
| Vision tests against a throwaway Postgres 17 (scratch copy of `conftest.py`, one database per test; run before the review fixes) | 106 passed: all of `test_vision.py` plus the usage-report, users-usage, admin-documents and `/me` usage tests (daily vision aggregates, the `vision_pages` cascade, the foreign-key failure when a document is deleted while a cache row is written, the bootstrap race) |
| Backend suite against a throwaway Postgres 17 (scratch copy of `conftest.py`, run before the last 2 tests were added) | 694 passed; only the fresh-interpreter import test was left out, because the scratch layout broke its path |
| Lint and types (commands in `README.md`) | ruff 0.13.0 and mypy 1.18.2 clean on `app/` (ruff on `tests/` too), re-run after the vision review fixes |
| Frontend `npm ci`, `npx tsc --noEmit`, `npx vite build` | clean, also after the vision UI and the overview-tile fix; `npm audit` reports 0 vulnerabilities |
| `docker compose up -d --build` (Docker Desktop, arm64) | all 6 services up, 5 healthy (the worker has no healthcheck); `/api/health` returns 200 through nginx. Backend, worker and frontend rebuilt on 2026-10-15 at 16:39Z, after the vision review fixes; the frontend container serves the same asset names as a local build |
| API smoke test against the live stack | 35 passed, 1 skipped (the disabled embedding model): admin login, user creation, PDF and DOCX ingestion, duplicate rejected, vectors in Qdrant, SSE event order, citations from both documents, Stop mid-answer saves the partial answer and its usage, usage meter, admin usage report (daily grouping on Postgres), stats, cleanup including vectors. Re-run after the vision rebuild: 29 passed, 1 skipped (the model Tests, skipped on purpose); stats include `active_vision_model`, the report `vision_tokens`, and a text-only PDF made no vision call |
| Real model calls | Admin **Test** returned OK for all 5 seeded chat models, `text-embedding-3-small` (1536 dims) and the vision model (it read "TEST 4712"; 1.3 to 8.4 s over 3 runs, provider latency). Streaming usage is provider-reported, not estimated, for Anthropic and OpenAI |
| Browser check (Playwright, Chromium) | 36 steps passed 3 times: login, upload, streaming, sources, Stop, reload, layouts at 1440/1000/700/390 px, dark mode, admin at 390/800 px, modal focus trap; no page errors, CSP violations or page-level scrolling. After the vision UI: 39 steps passed (Models in the order Chat, Embedding, Vision; the vision model active and not offered for chat; its UI **Test**; 390 px and dark mode; the Usage vision tile and columns) |
| Postgres behaviour | `create_all` advisory lock (6 processes at once: 0 of 18 failures, 15 of 18 without it); all timestamps `timestamptz`; usage report splits days in UTC even with a +05:45 database default; native booleans |
| Failure handling | worker SIGKILLed mid-document: re-queued on start, finished with exactly 800 chunks and no duplicate vectors. Graceful worker stop finished the in-flight job. Redis stopped for 20 s: worker backed off and recovered, and an upload made during the outage was queued when Redis returned. `docker compose restart backend` mid-answer took 15.8 s and stored the partial answer and its usage |
| Postgres row locks (scratch tests) | delete-while-finishing race: passes with the lock, leaves an orphan without it; admin reset racing change-password: correct in 8 of 8 runs |
| Vision in Docker (after `docker compose up -d --build backend worker`) | `pip check` clean; the worker runs with `Memory=MemorySwap=4294967296`; on start the existing database got "Claude Sonnet 5.5 (vision)", enabled and active, and its admin **Test** returned "OK: read the test image as 'TEST 4712'" (1.8 s). The render child in the worker image rendered a Letter scan at 1212x1568 (17 KB) in 0.1 s; the reviewer's form bomb (20k operators drawn 300 times), sent to it on purpose, was refused by the measurer (not renderable) and, rendered anyway, aborted the child (SIGABRT under `RLIMIT_AS` 1 GiB) after 1.2 s while the worker kept running. The log line of that seeding (2026-10-14 17:15Z, also the time of the `vision_seeded` row) was lost when the containers were replaced, so it was replayed on a scratch clone of the database without the vision model: the new image seeded it once, enabled and active, and a second start added nothing |
| Vision live smoke run, 2026-10-15 after the rebuild (user `smoke-vision`, deleted afterwards; 25 of 25 steps) | a Pillow 2-page scan (empty text layer) and a text + raster-chart PDF (values only in the image) were `ready` with `vision_pages` 2 and 1; 3 pages cost 8,632 vision tokens ($0.0202, about $0.007 per page), as one `vision` event per document. A question about page 2's scanned text was answered from it and cited page 2; a question about the chart was answered with all 4 values from the description, citing page 1. A chat with a monthly budget 1 token below the vision tokens used was allowed (a 1-token budget gave the 429). Reprocessing the scan read both pages from the cache, with no call and no new event. The usage report, `/auth/me` and the admin user list showed the vision tokens apart from the chat tokens. An earlier run (2026-10-14) of the same kind used 8,584 tokens |
| Vision edge run, same day (10 of 10, the Test skipped) | with no active vision model a scan failed with "… Ask the admin to enable a vision model to read it."; after re-enabling it, **Retry** made it `ready`. A 51-page scan failed with the `VISION_MAX_PAGES` message before any model call. Cleanup left no `smoke-*` user, document, vector, cached page, usage row or file |

Late fixes from the final pass: the chat stopped following new text during fast streams and after
**Jump to latest** (`MessageList.tsx`: now only an upward user scroll stops following), and a
backend restart lost streaming answers (`chat.drain_answers`, awaited in the lifespan before
Redis, Qdrant and the database close; two tests in `test_chat.py`).

Vision review fixes (each with a regression test in `test_vision.py` or `test_pdf_images.py`):

- `llm.read_image` passes `max_retries=0`, so one vision call is one HTTP request. The OpenAI SDK
  (`openai/*` and OpenAI-compatible hosts) used to retry 429s, 5xx and timeouts twice inside the call, billing
  up to 3 requests; `vision.read_pages` owns every retry.
- A fatal error on a description page (rejected key, unknown model, no image received) stops new calls, and
  scanned pages not read yet fail with that reason. They used to fail with a false "took longer than 30
  minutes" message. A PDF with only description pages keeps its text and becomes `ready`.
- The rendered area is the crop box clipped to the media box (`pdf_images.visible_box`), and `/Rotate` counts in
  whole quarter turns (`page_rotation`), as PDFium does. Scans with such boxes or rotations used to fail as
  "couldn't be rendered".
- The PDF and output paths are made absolute before the render child starts (it runs in the output directory),
  so a relative `UPLOAD_DIR` works.
- Frontend: the overview tiles wrap to two lines (`admin.css`, `.admin-tile-text`; the full name stays in the
  tooltip), because the seventh tile cut "Claude Sonnet 5.5 (vision)" to "Claude Sonnet …".

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
  only the OpenAI key is set); `text-embedding-3-large` is always seeded disabled. The vision model is the
  exception: it is seeded once per database, also into an existing one (see "Vision model for PDFs").

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

### Vision model for PDFs

All of `docs/briefs/vision-pdf.md` (revision 2) is implemented; its section 20 lists the review points and what
was done about each. What matters when maintaining it:

- **Isolation.** PDFium (pypdfium2 5.13.0, a new pinned dependency, hashed in both locks; pillow 12.3.0 is
  pinned explicitly too) never runs in the worker process. Images are measured with pypdf in the parse
  thread (`pdf_images.PageMeasurer`: content-stream walk with CTM, forms to depth 8, 2 MiB of decoded content
  per page, 300 000 expanded operators, 10 000 `Do`, 500 placements). Only selected, uncached pages reach
  `pdf_render_child.py`, run as `python -I` with only `PATH` and `LANG` in its environment, `RLIMIT_AS`
  1 GiB, no core dumps and `oom_score_adj` 1000; 30 s per page, killed and restarted on a crash or timeout
  (at most 3 restarts per document), always killed in `finally`. `RLIMIT_AS` isn't enforced on macOS, so
  the memory limit was verified in the worker image only.
- **Classification** (`parsing._classify`, SPEC section 7): images under 1% of the page and images drawn on 3
  or more units (or every unit) are ignored; union coverage; "scanned" below `VISION_MIN_TEXT_CHARS`, or a
  page image with a small visible overlay; a page image with a mostly invisible text layer (OCR) keeps its
  text; any other page with enough image coverage is described. Vector-drawn charts aren't detected.
- **Cost controls:** `reasoning_effort="low"` and `max_tokens` 8192 per page; `VISION_MAX_PAGES` (only scans
  covered by page images, at least 90%, can fail the document, before any call; other pages beyond the cap keep
  their text layer, logged); `VISION_CONCURRENCY` per document and `VISION_MAX_IN_FLIGHT` per worker (asyncio
  semaphores per event loop); `VISION_TIMEOUT_SECONDS` per document; `VISION_MONTHLY_TOKEN_LIMIT` per user,
  checked when a run starts (admin exempt). One `vision` usage event per run, recorded on every path (also
  failures, stops, re-queues and cancellation); it doesn't count toward the chat budget.
- **Retries:** each vision call is one HTTP request (`max_retries=0`). `vision.read_pages` retries rate limits,
  5xx, "overloaded" and connection errors up to 4 times, waiting `Retry-After` (clamped to 1-60 s) or 2, 5, 15
  and 40 s, and never past the document's deadline. Timeouts aren't retried, and fatal errors (key, permission,
  unknown model, no image received) stop new calls for the document.
- **Page cache** (`vision_pages`): one row per document, page and mode, written as each page is read and keyed
  by a fingerprint of the model's `litellm_model`/`api_base`, `PROMPT_VERSION`, `VISION_MAX_IMAGE_PX` and the
  page `max_tokens`. Retry, reprocess, re-indexing and resumed runs pay only for pages not read yet; a
  successful run deletes the rows it didn't use, so `DocumentItem.vision_pages` counts the latest run's pages.
  Bump `vision.PROMPT_VERSION` whenever the prompts or `clean_output` change.
- **Stop checks:** before each uncached call the run checks (two primary-key selects) that the document is
  still `processing` and the vision model still the same active one. Delete or reprocess stops the run
  quietly; a disabled, un-defaulted, edited or deleted vision model re-queues the document. A graceful worker
  stop after the parse doesn't count toward the 3 interrupted runs.
- **The vision phase doesn't hold a worker slot** (`ingestion.set_vision_phase_listener`, `Worker._reading`);
  at most `WORKER_CONCURRENCY` documents read pages at once.
- **Seeding once:** `bootstrap._seed_vision_model` runs on every start but only acts while the `app_state` row
  `vision_seeded` is missing; it adds "Claude Sonnet 5.5 (vision)" (GPT-5.4 mini when only the OpenAI key is
  set) unless a vision model exists. A vision model the admin deletes isn't added again. A start racing
  another one rolls back on the primary key and logs.
- **LiteLLM and the effort setting.** LiteLLM 1.103.1 has no model-map entry for `claude-sonnet-5-5`; with
  `drop_params=True` it then drops `output_config.effort` and Sonnet 5.5 would think at its default (high)
  effort. `llm._allow_effort` registers `supports_output_config` for adaptive-thinking Claude models missing
  from the map before a vision call, so the request carries `thinking: adaptive` plus `output_config.effort:
  "low"` (checked offline in `test_vision.py` for Anthropic, OpenAI `reasoning_effort: "low"` and Gemini
  `thinkingBudget` 1024). Side effect: such a model's map entry gets LiteLLM's baseline output cap of 64 000
  tokens, above every default here. Drop the helper once the bundled map lists the model.
- **No 409s for vision models:** disabling, un-defaulting or deleting the active vision model is allowed and
  turns page reading off. `GET /models` never lists vision models, and they can't be chat models.

Choices the agents made beyond the user's decisions, still to be confirmed with the user:

- `VISION_MONTHLY_TOKEN_LIMIT` (2,000,000 per user per month, roughly 400-600 pages) is a control the user didn't
  ask for. It bounds upload/delete churn, which the page cache can't. `0` turns it off, which matches "not
  counted against the budget" strictly.
- Only page-image scans can fail a document over `VISION_MAX_PAGES`; short-text pages with partial images (slide
  decks with photos) are read in page order up to the cap and keep their text layer beyond it.
- The seeded "Claude Sonnet 5.5 (vision)" became active on the existing database without any admin action, so
  page images of every user's PDFs with scans or large images now go to Anthropic.
- `WORKER_MEM_LIMIT` 4g (Docker Desktop's VM has about 8 GB here). Documents in the vision phase don't hold a
  worker slot, so up to 2 × `WORKER_CONCURRENCY` documents can be in flight.
- The OpenAI-only seed is GPT-5.4 mini, and the admin overview has a seventh tile for the vision model.

## Known limitations and open risks

- **Parsing is only partly isolated.** pypdf, python-docx, python-pptx, openpyxl and BeautifulSoup still parse
  in the worker process via `asyncio.to_thread` (SPEC section 7). PDFium rendering for the vision model runs
  in a memory- and time-limited child process, and the worker container now has a memory limit
  (`WORKER_MEM_LIMIT`, default 4 GB), so a runaway parse is OOM-killed inside the container and the 3-strike
  counter fails the document. One pathological page can still overrun the parse deadline, which is only
  checked between pages. Docker Desktop's VM here has about 8 GB.
- **Vision cost and coverage.** Scanned pages cost vision tokens (measured: about $0.007 per simple page with
  Sonnet 5.5; the brief estimates $0.01-0.03 for dense pages). Vector-drawn charts and pages whose content is
  over 2 MiB aren't checked for images; DOCX/PPTX images aren't read. A 5xx retried after the provider
  finished can be billed twice (accepted, rare). `RLIMIT_AS` in the render child was verified on Linux only.
  The monthly vision allowance is checked when a run starts, so a user can go over it by the runs in progress.
  Only Anthropic's Sonnet 5.5 was tried live as a vision model; the OpenAI and Gemini effort mappings were
  checked offline, and the Ollama example is untested.
- **Usage history goes with the user.** Deleting a user cascades their `usage_events` (SPEC section 4), so their
  chat, embedding and vision costs leave the usage report (after the smoke runs, this month's vision total was
  back to 0). Keep cost history elsewhere if it must survive user deletion.
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
  meta line can wrap with a trailing ` ·`. The overview's embedding tile still shortens "OpenAI
  text-embedding-3-small" on its second line at 1440 and 390 px (the full name is in the tooltip).
- Ops quirk: one `docker compose up -d --build` built a new frontend image but kept the old
  container. `docker compose up -d --force-recreate frontend` fixed it.
- An agent's command printed the OpenAI API key into that session's tool output (never into a
  file). Rotate the key.

## Next steps

1. Review `git status` and commit the build.
2. Rotate the OpenAI API key, update `.env`, and run `docker compose up -d`.
3. Confirm the vision choices listed at the end of "Vision model for PDFs" with the user (above all the
   monthly vision allowance and the automatic activation of the seeded Sonnet vision model).
4. Add Alembic before any schema change on a database with real data. Baseline it on the current
   models and keep the advisory lock around the migration run.
5. Before exposing the app beyond localhost: put HTTPS in front, set `COOKIE_SECURE=true`,
   configure `set_real_ip_from`/`real_ip_header` in `nginx.conf` for the outer proxy so the per-IP
   throttle sees real clients, and consider rate limits on upload and reprocess.
6. Set up backups. Postgres and the `uploads` volume hold everything that matters:
   ```bash
   docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > rag-$(date +%F).dump
   docker compose exec -T worker tar czf - -C /data/uploads . > uploads-$(date +%F).tgz
   ```
   Both commands were checked against the running stack; a restore hasn't been tested. Qdrant can
   be rebuilt from the uploads with Admin → Documents → **Re-index all documents** (this costs
   embedding tokens). Redis holds only transient state (queue, throttles, answer slots, attempt
   counters), and the worker re-queues pending documents from Postgres.
7. Done: the worker has `mem_limit`/`memswap_limit` `${WORKER_MEM_LIMIT:-4g}` (verified with `docker inspect`).
   An OOM kill stays inside the worker, and the attempt counter fails the document after 3 kills.
8. Add CI: the backend suite on SQLite and on Postgres 17 (to cover the locks), tsc and the build,
   plus a committed Playwright smoke test for the UI.
9. Later: move the remaining parsing (pypdf, Office formats, HTML) into a child process with a hard kill, as
   PDF rendering already is, and bump pytest to 9.x together with a compatible pytest-asyncio.
10. Later: read images in DOCX and PPTX files with the vision model (PDFs only for now), and remove
    `llm._allow_effort` once LiteLLM's bundled model map lists `claude-sonnet-5-5`.

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
- The Mac idle-sleeps on battery, which pauses long runs (the suite, Docker builds, smoke runs). Wrap
  them in `caffeinate -i`.
