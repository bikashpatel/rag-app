# Handoff: status and next steps

Snapshot taken 2026-10-14 (IST) on macOS (Apple Silicon), after the full build, review and Docker
run. Updated 2026-10-15 after the vision model for PDFs was built, reviewed, fixed and verified in Docker, and
2026-10-16 after files from chat went through the same steps.
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
  parsing, ingestion, the worker, bootstrap, usage and the admin API; tests in `test_pdf_images.py`,
  `test_vision.py` and the independent `test_vision_independent.py`. Frontend: a "Vision models" section in
  Admin → Models, vision tokens in Usage and Users, a seventh overview tile (the active vision model), and "pages
  read by vision" on documents.
- **Files from chat** (`docs/briefs/document-generation.md`, revision 2): when the user asks, the chat assistant
  creates PDF, Word, Excel and Markdown files with a `create_document` tool. Each file becomes one of the user's
  private documents (indexed like an upload), is linked to the answer (`message_files`) and is marked as generated
  (`generated_documents`); see "Files from chat" below.
  - Backend: `services/document_tool.py`, `services/filegen/` (renderers and the render child), `app/fonts/` (29
    Noto TTFs + `OFL.txt`), plus changes to `llm`, `rag`, `storage`, `ingestion`, `usage` and the `chat`,
    `conversations`, `documents`, `models` and `admin` routers. `GET /api/documents/{id}/download` is new.
  - Tests: `test_filegen.py`, `test_document_tool.py`, `test_llm_tools.py`, the independent
    `test_document_generation.py`, and download tests in `test_documents.py`.
  - Frontend: a file card on the answer (`chat/GeneratedFileCard.tsx`) and a module store shared by the panel and
    the cards (`documents/documentEvents.ts`). Every document row has a Download button, and generated rows say
    "Created in chat". The composer hint mentions files when the selected model can create them.

How the work went: the lead wrote `SPEC.md` and the foundation code. Agents then built the
feature areas in parallel from `docs/briefs/`, followed by backend integration tests, frontend
verification, an adversarial review against `docs/briefs/review-checklist.md` (46 confirmed
findings: 3 high, 14 medium, 29 low), fixes for those, Docker and Postgres checks, and a final
verification pass that fixed two more bugs (see below). The vision feature followed the same pattern: a
brief, an adversarial critique and a second revision of the brief, parallel backend and frontend builds, a
review (5 confirmed findings, all fixed with regression tests), then a Docker rebuild and live runs. Files from
chat followed the same steps and added an independent test pass written from the contract
(`test_document_generation.py`). Its review had 6 confirmed findings, all fixed (see "Files from chat"), and was
followed by a rebuild and live runs. A separate test pass for the vision feature (`test_vision_independent.py`, 109
tests) found one more vision bug, fixed after the vision commit (see "Vision review fixes" below).

The Compose project `rag-app` was left running with its data. The vision feature is committed (`9fb8be6`, on
top of `de561d3` and the scaffold `e56133e`). Three things are **uncommitted**: files from chat, the later vision
fix in `pdf_images.py`, and `test_vision_independent.py`. Check `git status` before relying on history. The
agents' test and smoke scripts lived in scratch directories and are not in the repo.

## How it was verified

| Check | Result |
|---|---|
| Backend suite (`cd backend && .venv/bin/python -m pytest -q`) after files from chat and its review fixes (2026-10-16) | **1169 passed** in about 147 s (SQLite, fakeredis, in-memory Qdrant, mocked LiteLLM, the real PDF and file render children). The last two of 4 runs were green in a row. Run 2 failed one timing-dependent test (`test_progress_and_keepalives_while_a_file_is_written`: a warm render child finished before the keepalive interval). The test now delays the render, and the app was unchanged. ruff 0.13.0 `check` (app and tests) and mypy 1.18.2 (app) are clean. The suite includes a test that runs the real LiteLLM 1.103.1 against a local fake Anthropic/OpenAI server and checks the replayed request bodies: thinking blocks in order, `disable_parallel_tool_use`, `{}` for invalid arguments and the OpenAI flags. Earlier counts: 856 after the vision feature (2026-10-15), 697 before it |
| Files from chat in the Linux image (a separately tagged build of `backend/`, removed afterwards; the running stack untouched) | `pip check` clean with the new locks; the render child got `RLIMIT_AS` 1 GiB, `RLIMIT_FSIZE` and `oom_score_adj` 1000; a Hindi/Nepali PDF (11 pages) and an English report (37 pages) rendered at the same time in 2.7 s |
| Files from chat, live run in Docker, 2026-10-16 (users `smoke-docgen` and `smoke-docgen-other`, deleted afterwards; scratch script, not in the repo) | Run 1 used mixed models: PDF and MD on Claude Haiku 5.5, DOCX on Claude Opus 5.5 and XLSX on GPT-5.4 mini. It passed 36 of 40 checks; the 4 failures were the script's header check, which didn't expect nginx's repeated headers (see "Files from chat"). Run 2 used only Haiku and passed 40 of 40. Both runs verified: <br>• the SSE contract of every answer: `writing` progress, one `created` per call, and `done.usage` summing every call <br>• each file became a private, owned, `generated` document, invisible to the other user, that reached `ready` with no vision pages <br>• the cards came back after a reload and followed the status <br>• the downloads: type by extension, `attachment` with an ASCII fallback plus `filename*` (a Hindi name too), the uploaded file's exact bytes, and 404 for another user and for the admin on private files <br>• the files parsed back: a Hindi PDF with Noto subset fonts, a table, the page footer and no actions, JavaScript or forms (page 1 rendered to PNG and checked by eye) <br>• the DOCX heading and list styles and its Sources section, with no macros <br>• the XLSX header, freeze pane and auto-filter, numbers stored as numbers, and `=1+1` stored as quote-prefixed text with no `<f>` element <br>• plain Markdown <br>• Qdrant vectors, with the PDF indexed from its `.source.md` (the Hindi text was exact; citations had no page) <br>• a later chat answering from the generated letter <br>• the quota cases: lowering `max_documents` mid-answer gave a tool error, the model explained it, and nothing was stored; a user at the limit wasn't offered the tool <br>• one `chat` usage event per answer, equal to the summed `done.usage` <br>• full cleanup, `.source.md` files included |
| Browser check after files from chat (Playwright, Claude Haiku 5.5) | 41 of 41 steps. The 2 new steps passed. The composer hint showed. The card went from "Creating a file…" to the title and a growing character count, then to the created card, and "In your documents" about 5.6 s after sending. The panel row showed "Created in chat" before the stream ended. The card's Download and the row's Download saved the same bytes, and Tab reaches Download. The card is still there after a reload. Dark mode and 390 px look right. There were no page errors or CSP violations. Not covered by this run: Stop during writing, the failed card, the "Deleted" card and a list poll in flight during creation. Those were checked during the frontend build, with a scratch Playwright script against a mocked backend |
| Vision tests against a throwaway Postgres 17 (scratch copy of `conftest.py`, one database per test; run before the review fixes) | 106 passed: all of `test_vision.py` plus the usage-report, users-usage, admin-documents and `/me` usage tests (daily vision aggregates, the `vision_pages` cascade, the foreign-key failure when a document is deleted while a cache row is written, the bootstrap race) |
| Backend suite against a throwaway Postgres 17 (scratch copy of `conftest.py`, run before the last 2 tests were added) | 694 passed; only the fresh-interpreter import test was left out, because the scratch layout broke its path |
| Lint and types (commands in `README.md`) | ruff 0.13.0 and mypy 1.18.2 clean on `app/` (ruff on `tests/` too), re-run after the vision review fixes and again on 2026-10-16 after files from chat |
| Frontend `npm ci`, `npx tsc --noEmit`, `npx vite build` | clean, also after the vision UI, the overview-tile fix and the files-from-chat UI (346 modules); `npm audit` reported 0 vulnerabilities (no npm package was added since) |
| `docker compose up -d --build` (Docker Desktop, arm64) | all 6 services up, 5 healthy (the worker has no healthcheck); `/api/health` returns 200 through nginx. Backend, worker and frontend rebuilt on 2026-10-15 at 16:39Z after the vision review fixes. They were rebuilt again at 21:07Z (2026-10-16 02:37 IST) after files from chat. That rebuild created the tables `message_files` and `generated_documents`, and the image has fpdf2 2.8.9, uharfbuzz 0.56.2, markdown-it-py 4.2.0 and the fonts. Both times the frontend container served the same asset names as a local build |
| API smoke test against the live stack | 35 passed, 1 skipped (the disabled embedding model): admin login, user creation, PDF and DOCX ingestion, duplicate rejected, vectors in Qdrant, SSE event order, citations from both documents, Stop mid-answer saves the partial answer and its usage, usage meter, admin usage report (daily grouping on Postgres), stats, cleanup including vectors. Re-run after the vision rebuild: 29 passed, 1 skipped (the model Tests, skipped on purpose); stats include `active_vision_model`, the report `vision_tokens`, and a text-only PDF made no vision call. Re-run after the files-from-chat rebuild (2026-10-16): 29 passed, 1 skipped. Prompt tokens per call went up by about 1,550 for the same requests, because the file tool and its prompt are sent (Haiku "pong" 352 → 1,904, Opus answer 595 → 2,147). After the client closed the connection, the saved partial answer (178 characters) was longer than what the client had received (61), because the answer runs in its own task until the cancel lands. In the browser, Stop showed 2,302 characters and saved 2,305 |
| Real model calls | Admin **Test** returned OK for all 5 seeded chat models, `text-embedding-3-small` (1536 dims) and the vision model (it read "TEST 4712"; 1.3 to 8.4 s over 3 runs, provider latency). Streaming usage is provider-reported, not estimated, for Anthropic and OpenAI. The files-from-chat runs on 2026-10-16 made about 35 chat model calls (Haiku 29, Opus 4, GPT-5.4 mini 2), 3 vision calls and about 40 small embedding calls, for under $0.10 of chat cost. The two docgen runs cost $0.048 and $0.006, and the Opus Word letter alone was $0.041 |
| Browser check (Playwright, Chromium) | 36 steps passed 3 times: login, upload, streaming, sources, Stop, reload, layouts at 1440/1000/700/390 px, dark mode, admin at 390/800 px, modal focus trap; no page errors, CSP violations or page-level scrolling. After the vision UI: 39 steps passed (Models in the order Chat, Embedding, Vision; the vision model active and not offered for chat; its UI **Test**; 390 px and dark mode; the Usage vision tile and columns). After files from chat: 41 steps (row above) |
| Postgres behaviour | `create_all` advisory lock (6 processes at once: 0 of 18 failures, 15 of 18 without it); all timestamps `timestamptz`; usage report splits days in UTC even with a +05:45 database default; native booleans |
| Failure handling | worker SIGKILLed mid-document: re-queued on start, finished with exactly 800 chunks and no duplicate vectors. Graceful worker stop finished the in-flight job. Redis stopped for 20 s: worker backed off and recovered, and an upload made during the outage was queued when Redis returned. `docker compose restart backend` mid-answer took 15.8 s and stored the partial answer and its usage |
| Postgres row locks (scratch tests) | delete-while-finishing race: passes with the lock, leaves an orphan without it; admin reset racing change-password: correct in 8 of 8 runs |
| Vision in Docker (after `docker compose up -d --build backend worker`) | `pip check` clean; the worker runs with `Memory=MemorySwap=4294967296`; on start the existing database got "Claude Sonnet 5.5 (vision)", enabled and active, and its admin **Test** returned "OK: read the test image as 'TEST 4712'" (1.8 s). The render child in the worker image rendered a Letter scan at 1212x1568 (17 KB) in 0.1 s; the reviewer's form bomb (20k operators drawn 300 times), sent to it on purpose, was refused by the measurer (not renderable) and, rendered anyway, aborted the child (SIGABRT under `RLIMIT_AS` 1 GiB) after 1.2 s while the worker kept running. The log line of that seeding (2026-10-14 17:15Z, also the time of the `vision_seeded` row) was lost when the containers were replaced, so it was replayed on a scratch clone of the database without the vision model: the new image seeded it once, enabled and active, and a second start added nothing |
| Vision live smoke run, 2026-10-15 after the rebuild (user `smoke-vision`, deleted afterwards; 25 of 25 steps) | a Pillow 2-page scan (empty text layer) and a text + raster-chart PDF (values only in the image) were `ready` with `vision_pages` 2 and 1; 3 pages cost 8,632 vision tokens ($0.0202, about $0.007 per page), as one `vision` event per document. A question about page 2's scanned text was answered from it and cited page 2; a question about the chart was answered with all 4 values from the description, citing page 1. A chat with a monthly budget 1 token below the vision tokens used was allowed (a 1-token budget gave the 429). Reprocessing the scan read both pages from the cache, with no call and no new event. The usage report, `/auth/me` and the admin user list showed the vision tokens apart from the chat tokens. An earlier run (2026-10-14) of the same kind used 8,584 tokens |
| Vision edge run, same day (10 of 10, the Test skipped) | with no active vision model a scan failed with "… Ask the admin to enable a vision model to read it."; after re-enabling it, **Retry** made it `ready`. A 51-page scan failed with the `VISION_MAX_PAGES` message before any model call. Cleanup left no `smoke-*` user, document, vector, cached page, usage row or file. Re-run on 2026-10-16 after the files-from-chat rebuild with the same result (1 vision page call); the vision model was left enabled and active |

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
- Found after the vision commit by the independent tests (`test_vision_independent.py`; uncommitted): a form
  XObject now starts with the text render mode of the `Do` that draws it (`pdf_images.PageMeasurer._walk`), as
  PDFium renders it. Before the fix, an OCR layer drawn as a form after `3 Tr` counted as visible text. That page
  image was then sent to the vision model for a needless description instead of keeping the OCR text with no
  call. The fix has no prompt change, so `PROMPT_VERSION` stays.

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
  **Test** sends the same value. Chat calls that offer the file tool use `GENERATED_FILE_MAX_OUTPUT_TOKENS`
  (48000) in the same way: the model's own value wins, and LiteLLM's cap lowers it.
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
  Each feature added only new tables, never columns: `vision_pages` and `app_state` for vision, and
  `message_files` and `generated_documents` for files from chat. A `message_files` row is deleted with its message.
  Deleting its document sets its `document_id` to null, and the card then shows "Deleted". `generated_documents`
  lives as long as the document.
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

### Files from chat

Everything in `docs/briefs/document-generation.md` (revision 2) is implemented, backend and frontend.

**How it works.** A user asks in their own words ("make a PDF report of X from my documents", "put these figures
in a spreadsheet", "draft a Word letter", "export this as markdown"). The model calls `create_document` with a
title, a format (`pdf`, `docx`, `xlsx` or `md`), Markdown content (plus string-cell sheets for xlsx) and the
passage numbers it used. The server then validates the arguments and renders the file in a child process. It
stores the file as a private document of the user (`pending`, queued for the worker like an upload) and calls
the model again with the tool result so it can finish the answer. While the answer streams, `file` SSE events
drive the card: `writing` with progress, then `created` or `failed`. After a reload the card comes from
`ChatMessage.files`, which is rebuilt from `message_files` with the document's current status. Failed attempts
aren't stored. The content is a Markdown subset: headings, paragraphs, bold and italic, lists, tables, code
blocks and links. Images, HTML and footnotes aren't supported. Rendering in short:

- **PDF**: fpdf2 with Noto Sans fonts. It has headings, paragraphs, bold and italic, nested lists, tables, code
  blocks, links with their host shown, a "page / pages" footer and the creator "RAG Assistant".
- **DOCX**: python-docx with Heading, List Bullet and List Number styles, plus tables, complex-script bold and
  italic, and right-to-left text.
- **XLSX**: openpyxl. Each sheet has a bold header row, a frozen first row, an auto-filter and fitted column widths.
  Numbers and ISO dates are stored typed, and every other cell is text. A "Notes" sheet holds the content when
  there is any.
- **MD**: the Markdown is rewritten from the parsed tree. Images become text and HTML is escaped (D21).
- When the model lists the passages it used (`source_numbers`), the file ends with a Sources section: document
  names and pages for every cited number. In xlsx this is an extra sheet.

What matters when maintaining it:

- **Thinking replay depends on LiteLLM's translation (D2).** Claude 5.5 can answer `[thinking, text, thinking,
  tool_use]`, and Anthropic binds each thinking block to everything before it. `llm.stream_chat` records the
  response in arrival order (`ChatStep.sequence`) and `llm.assistant_turn` replays it as consecutive assistant
  messages ("segments") that LiteLLM 1.103.1 merges into one Anthropic message in order. LiteLLM's top-level
  `thinking_blocks` on one message would put every block first. `test_llm_tools.py` runs the real LiteLLM
  against a local server and fails if an upgrade changes this. If Anthropic still rejects a signature, the call
  is retried once without the answer's thinking blocks (logged).
- **The loop (D4, D17):** at most `GENERATED_FILES_PER_ANSWER` files and that + 2 model calls per answer; calls
  that offer the tool send `parallel_tool_calls: false` and `GENERATED_FILE_MAX_OUTPUT_TOKENS`; the last allowed
  call is sent with `tool_choice: "none"`. Tool calls run only when the response finished with `tool_calls`;
  `length` becomes a tool error and the loop continues; `content_filter` (an Anthropic refusal) or no finish
  ends the answer with an error. Arguments that aren't a JSON object are echoed as `{}`, because LiteLLM puts
  all of unparseable arguments into the error it raises (and logs). The system prompt and tool list stay
  byte-identical for every call of an answer.
- **Tool fallback (D22):** if the first call with the tool is rejected as a bad request before any output, it is
  retried without the tool and with the same max_tokens; when that works, the model (`litellm_model` +
  `api_base`) gets no tool until the backend restarts (`llm.mark_tools_rejected`, which logs the provider's
  message), and `GET /models` says it can't create files. A rejection of the file call's larger max_tokens (the
  provider's message names the output size, or dropping the tool didn't help) or a context-window error is
  retried with the tool and the usual max_tokens instead, and a warning asks the admin to set the model's Max
  output tokens (brief 7.5, recovery 0).
- **PDF fonts (D6):** fpdf2 2.8.8 and 2.8.9 lose the font of the lines after one whose first fragment uses a
  fallback font (the text vanished). `filegen/fonts.py` picks the font of every text run itself; fallback fonts
  are only a safety net. Shaping is on only for Indian-script runs. The fonts come from
  `notofonts/notofonts.github.io` at commit `5c3913063bb189df4edc94b766584dfb4c04a187` (sha256 table in the brief).
  fpdf2 always adds an `/OpenAction` (a zoom destination); `filegen/pdf.py` removes it, so generated PDFs have
  no actions at all.
- **Indexing (D7):** a generated PDF is indexed from `<stored_name>.source.md` (written next to it, removed with
  it), because text extracted from shaped Indian scripts is lossy; it never goes to the vision model and its
  citations have no page.
- **Rendering (D14):** every file renders in `filegen/child.py` (`python -I`, empty environment, `RLIMIT_AS`
  1 GiB, CPU and file-size limits), at most `GENERATED_FILE_CONCURRENCY` per backend process, killed at
  `GENERATED_FILE_TIMEOUT_SECONDS` or on Stop. The child's stderr is discarded (it can hold content). PDF and
  DOCX tables are capped (20 columns, 500 rows, 10,000 body cells, 2,000 characters per cell).
- **Keepalive (D15):** the answer is produced by a task of its own (`chat._sse_with_keepalive`); the response
  sends `: keepalive` after 15 s without an event, because the Cloudflare tunnel drops a response that is silent
  for about 100 s and a lost connection counts as Stop. `writing` events report progress at most every 2 s.
- **Usage and budget (D18, D19):** each model call is billed on its own (a call that failed before any output
  without provider-reported usage isn't); one `chat` usage event per answer has the sums. With a budget, no
  follow-up call starts once the answer used what was left when it started, but up to `MAX_CHATS_IN_FLIGHT`
  answers running at once can each go up to that amount, so the overspend bound is now about two answers of up
  to `GENERATED_FILES_PER_ANSWER` + 2 calls each. The answer's slot is refreshed every 60 s while it streams.
- **Cost of offering the tool (D1, D16).** The tool and its prompt add about 1,550 input tokens to every call that
  offers it. The live run measured this on 2026-10-16, and it comes to about $0.006 per Claude Opus 5.5 call.
  Every answer from a tool-capable model pays it, not only file requests; `CHAT_FILE_TOOLS=false` removes it.
  Those calls also request `GENERATED_FILE_MAX_OUTPUT_TOKENS` (48,000) instead of the 16,000 default. The model
  only decides during the call whether to write a file, so every answer from a tool-capable model gets the
  higher limit. A runaway answer can therefore cost up to three times as much per call (about $0.96 instead of
  $0.32 on Opus 5.5). Prompt caching was considered and rejected (brief 0.2).
- **Who gets the tool (D1, D11, D23).** The tool goes to chat models for which
  `litellm.supports_function_calling` is true and that haven't rejected tools since the backend started. A
  non-admin user with no room isn't offered it: `max_documents` reached, or less than 256 KiB of storage left. The
  system prompt then says why, so the model tells the user. While `CHAT_FILE_TOOLS` is on, models that can't
  use the tool get a note telling them to say they can't create files. `ChatModel.can_create_files` (`GET
  /models`, which drives the composer hint) ignores quotas.
- **Storing (D8, D10).** Files count toward `max_documents`, `max_storage_mb` and the per-file `max_upload_mb`
  (the admin is exempt). They are checked under the same per-user upload lock as uploads
  (`storage.serialized_uploads`). A quota or validation failure goes back to the model as a tool error and
  nothing is stored. A file whose bytes equal one of the user's private documents links that document instead
  (`already_existed`). In practice this only happens with `.md` files. The document row and its
  `generated_documents` row are written in one commit, and the file joins the answer before `created` is sent.
  Stop waits for a store in progress and kills a render child. A card still writing becomes "stopped" and the
  panel reloads.
- **Download endpoint** (`GET /api/documents/{id}/download`). It follows the list's visibility (own private plus
  shared) and returns 404 for anything else. That includes the admin asking for a user's private document; Admin
  → Documents has no download. A missing stored file is a 404 with a logged warning. The response is typed by
  extension: pdf, docx, pptx and xlsx get their types and everything else is `application/octet-stream`. It is an
  `attachment` with an ASCII fallback name (unsafe characters as `_`, at most 150 characters) plus `filename*`,
  and carries `nosniff`, `Cache-Control: private, no-store` and `Content-Security-Policy: sandbox`. nginx's
  server-level `add_header`s add a second `nosniff` and the app's CSP to every `/api/` response. This is harmless,
  because both CSPs apply. Separate headers in `location /api/`, or `proxy_hide_header`, would tidy it.
- **Frontend (D12).** `documentEvents.ts` is a module store shared by `useDocuments` (the panel) and the cards.
  - A `created` event announces the document. The panel inserts it and reloads its list and quota, so a poll
    already in flight can't drop it.
  - A card counts as "Deleted" only when the panel listed its document and a later list lost it. When the list
    hasn't seen the document yet, the card asks the panel once to reload and meanwhile keeps the server's status
    and Download.
  - The typing dots stay below the cards until text streams. A card says "Already in your documents" for a
    linked duplicate, and "Saved, but couldn't be indexed" when indexing failed.
  - Download is a plain `<a download>` to the endpoint, which works because the session cookie is same-origin.
- **Provenance (D20):** `generated_documents` outlives the conversation; passages of generated files are labelled
  in later prompts and their `[n]` markers (also the `\[n\]` form of Markdown files) become `(ref n)`, every
  marker of a run such as `[1][3]` included.
- **History note (D13):** an earlier answer's files are listed in the model's history as in the user's documents,
  or as "since deleted" once their document is gone.
- **Characters XML can't hold:** the tool's arguments and the Sources file names lose C0 controls (vertical tab and
  form feed become line feeds), U+FFFE, U+FFFF and lone surrogates (`filegen.xml_safe`), and `filegen.render` does
  the same to every text it gets; python-docx and openpyxl used to crash on them ("server error"). Uploaded file
  names lose U+FFFE and U+FFFF too.
- **Review fixes.** The review confirmed 6 findings, and all are fixed. The 4 backend fixes have regression tests in
  `test_filegen.py` and `test_document_tool.py`. The 2 frontend fixes were checked with a scratch test of the
  store and a scratch browser harness that renders the cards and the message (not in the repo).
  - Characters XML can't hold crashed DOCX and XLSX rendering (bullet above).
  - Only half of a run of adjacent `[n]` markers was neutralized (Provenance).
  - The "tools rejected" recovery also lowered max_tokens, so a 400 caused by max_tokens turned file creation off
    for the model until a restart (Tool fallback).
  - The history note called deleted files "in the user's documents" (History note).
  - A card showed "Deleted" and hid Download when the panel's list was only stale (Frontend).
  - Nothing showed the answer was still running after a card appeared and before any text (Frontend).
- Choices made while building, beyond the brief: the table cap counts body cells (so the 20 × 500 table the
  brief measured fits); openpyxl writes a hidden `_xlnm._FilterDatabase` defined name for each auto-filter (the
  only defined names in generated workbooks); letters of scripts without a text font (Arabic, CJK, …) count as
  unsupported even where Noto Sans Math has glyphs for them; emoji outside the BMP are always left out of PDFs,
  symbols such as ✓ ✔ ★ only when no font has them; a response `[thinking, text, thinking, tool]` replays as two
  segments (`[thinking, text]`, `[thinking, tool]`), as section 4's rule gives (14.5 said three).

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
- **Vision `NO_CONTENT` after a preamble (waiting on the user's decision).** `vision.clean_output` checks for
  `NO_CONTENT` before it removes a preamble line. A reply such as "Here is the transcription:" followed by
  `NO_CONTENT` is therefore indexed as the text "NO_CONTENT" (checked on 2026-10-16). The fix changes
  `clean_output`, so `PROMPT_VERSION` must be bumped, and that re-reads and re-bills every cached page.
- **Files from chat, not yet run live** (brief 14.7). Every live answer took 4 to 10 s, so no keepalive comment
  was ever sent live; keepalives are covered by the suite only. These were never run live:
  - a long report (about 8,000 words) whose arguments stream for more than 100 s through the Cloudflare tunnel;
  - two files in one answer;
  - the preserved-thinking opt-in check, a scratch script that turns on Anthropic's block-binding enforcement;
  - two renders at once in the running backend container (it was done in a separately tagged image).
- **Files from chat, limits by design.**
  - Generated PDFs cover Latin, Greek, Cyrillic and ten Indian scripts. Other scripts under 2% of the letters
    appear as □; above that the PDF is refused with a hint to use docx or md. Emoji are left out. CJK and Arabic
    fonts would add about 20 MB to the image.
  - Generated files have no images or charts.
  - An earlier file can't be re-rendered in another format; the model writes it again from the sources, which
    costs another file's tokens.
  - Citations of generated PDFs have no page number, because they are indexed from the Markdown source.
  - The admin can't download users' private documents.
  - `[n]` markers are neutralized only in passages of generated files.
  - A client disconnect isn't an instant Stop: the answer task runs until the cancel lands, so the saved partial
    answer can be a little longer than what the client got.
- **Files from chat, optional hardening that was skipped.**
  - The admin **Test** sends the usual max_tokens, so it doesn't reveal a model that rejects 48,000.
  - `llm._model_map_entry` doesn't match `bedrock_converse` or `vertex_ai-*` map entries. Their output cap is
    unknown, so the file call's max_tokens isn't lowered for them; recovery 0 handles a rejection.
  - A lowered max_tokens isn't remembered between answers, so such a model pays one rejected call per answer.
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
- **Budget overspend is bounded, not zero:** up to 2 in-flight answers can finish past the limit. An answer
  that writes files makes up to `GENERATED_FILES_PER_ANSWER` + 2 model calls; it stops calling once it used what
  was left when it started, but answers running at the same time don't see each other's tokens.
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
  text-embedding-3-small" on its second line at 1440 and 390 px (the full name is in the tooltip). A created
  file card shows the type twice (the tag, then "MD · 503 B"), as brief 12.5 specifies. The composer hint,
  files sentence included, is hidden on touch devices, as the keyboard hint always was. There, only the
  empty state's fourth example ("Create a PDF report summarizing my documents") mentions files, and only for a
  capable model.
- Ops quirk: one `docker compose up -d --build` built a new frontend image but kept the old
  container. `docker compose up -d --force-recreate frontend` fixed it.
- An agent's command printed the OpenAI API key into that session's tool output (never into a
  file). Rotate the key.

## Next steps

Files from chat is built and was verified live in Docker on 2026-10-16. What is left of it:

- With the user's approval for the token spend, run the rest of brief section 14.7: the long report through the
  tunnel, two files in one answer, the preserved-thinking opt-in check and two renders at once in the backend
  container. Use a `smoke-*` user, deleted afterwards, never `bikash`.
- Confirm the designer's open points with the user:
  - the limits: 3 files per answer, 100,000 characters, 48,000 output tokens on calls that offer the tool, a 60 s
    render limit and 2 renders at once;
  - the input overhead of about 1,550 tokens on every call of a tool-capable model;
  - CJK and Arabic PDF fonts (about 20 MB);
  - generated PDFs cited without page numbers;
  - the rule that the tool is used only when the user's latest message asks for a file;
  - the items rejected in brief 0.2 (prompt caching, re-rendering in another format, per-model switches);
  - downloads from Admin → Documents.
- Decide the vision `NO_CONTENT` fix (known limitations), and optionally tidy nginx's duplicate headers on `/api/`.

1. Review `git status` and commit the build: files from chat, the vision follow-up fix and
   `test_vision_independent.py`.
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
