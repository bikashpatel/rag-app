# RAG Assistant

A self-hosted, single-tenant Retrieval-Augmented-Generation app: upload documents,
chat with them, and manage users, models and usage from an admin page.

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
                               │           └──> LiteLLM ─> OpenAI / Anthropic / Ollama / …
                               │           └──> file render child (create_document: PDF/DOCX/XLSX/MD)
                               └── uploads volume (shared)
              worker (same image) <── Redis list rag:ingest_queue
                 parse ─> [PDF pages: render (child process) ─> vision model (LiteLLM)] ─> chunk ─> embed (LiteLLM) ─> Qdrant
```

- **Upload:** nginx checks the session, then streams the file to the backend, which validates it,
  stores it in the `uploads` volume, creates the document row (`pending`) and pushes its id onto
  the Redis queue. The worker parses it (in a thread), chunks it, embeds it with the active
  embedding model, writes the vectors to Qdrant and marks the document `ready` (or `failed` with
  a readable error). For PDFs, pages without a text layer (scans) and pages with large images are
  rendered to JPEG in a memory- and time-limited child process and read by the active vision model,
  which transcribes scans and describes charts, diagrams and photos; that text is indexed and cited by
  page like the rest.
- **Chat:** `POST /api/chat/stream` checks the rate limit, budget and model access, saves the
  user message, retrieves passages from the user's private documents plus the shared knowledge
  base (every Qdrant search carries this access filter), and streams the answer as Server-Sent
  Events (`meta`, `sources`, `warning`, `delta`, `file`, `done` or `error`, plus `: keepalive` comments
  during silences). The answer and its token usage are saved at the end, including partial answers after
  Stop or an error.
- **Files from chat:** chat models with function calling get a `create_document` tool. When the user
  asks for a PDF, Word, Excel or Markdown file, the backend validates the tool call, renders the file in a
  time- and memory-limited child process (fpdf2 with bundled Noto fonts for PDFs, python-docx, openpyxl),
  stores it as one of the user's private documents (quotas apply), queues it for indexing like an upload,
  and calls the model again with the result. The answer shows a file card with a Download button.
- **Admin:** one admin account, defined in `.env`. Users exist only if the admin creates them.

Code layout:

| Path | What |
|---|---|
| `backend/app/main.py`, `config.py`, `db.py`, `models.py` | app factory, settings, async SQLAlchemy, ORM |
| `backend/app/routers/` | `auth`, `models`, `documents`, `conversations`, `chat`, `admin` (all under `/api`) |
| `backend/app/services/` | `llm` (LiteLLM), `rag`, `parsing`, `pdf_images` + `pdf_render_child` (PDF image measuring and rendering), `vision` (vision model reading), `document_tool` (the chat's `create_document` tool) + `filegen/` (PDF, DOCX, XLSX and MD rendering, run by `filegen/child.py`), `chunking`, `ingestion`, `vectorstore`, `usage`, `storage` |
| `backend/app/fonts/` | Noto Sans fonts for generated PDFs (SIL Open Font License, `OFL.txt`) |
| `backend/app/worker.py` | ingestion worker (`python -m app.worker`) |
| `backend/tests/` | integration tests, no Docker needed ([README](backend/tests/README.md)) |
| `frontend/src/pages/` | `LoginPage`, `ChatPage`, `AdminPage` |
| `frontend/src/components/` | `chat/`, `documents/` (right-hand panel), `admin/`, `ui/` |
| `frontend/nginx.conf`, `upload-limit.envsh` | nginx template (security headers, body limits, upload auth check) |

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
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | your admin login (username 3 to 64 letters, digits, `_`, `.` or `-`; password at least 8 characters) |
| `POSTGRES_PASSWORD` | any strong password |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | your provider keys |

Notes:

- Compose expands `$` in `.env`. Wrap a value that contains `$` in single quotes
  (`ADMIN_PASSWORD='my$ecret'`); double quotes don't help.
- `POSTGRES_*` are applied only when the database volume is first created. To change them later,
  change them inside Postgres too, or reset all data with `docker compose down -v`.
- Every other setting (upload limits, retrieval tuning, login throttle, parsing limits, …) is
  listed with its default in `.env.example`, in the "Advanced" section.
- Compose passes the whole `.env` to the backend and worker. The frontend (nginx) gets only
  `MAX_UPLOAD_MB_HARD`.
- Anthropic has no embeddings API, so embedding needs the OpenAI key unless you switch to a local
  embedding model (see "Local models with Ollama").
- Models are seeded on the **first** start only. A seeded model is enabled only if its provider
  key was set at that moment; if you add a key later, enable the models in Admin → Models. The one
  exception is the vision model: an existing database gets "Claude Sonnet 5.5 (vision)" (or "GPT-5.4 mini
  (vision)" when only the OpenAI key is set) once, on its next start, enabled and active if the key is set,
  so from then on PDF page images go to that provider without any admin action. A vision model you delete
  isn't added again.
- **Vision model for PDFs** (`VISION_*` in `.env.example`): at most `VISION_MAX_PAGES` (50, at most 200)
  pages per document are read with the vision model, full-page scans first (a PDF with more of them fails
  with a message; other pages over the cap keep only their text layer), with `VISION_CONCURRENCY` (3) calls
  at a time per document and `VISION_MAX_IN_FLIGHT` (4) in the worker, and no new call after
  `VISION_TIMEOUT_SECONDS` (1800). Each user may use `VISION_MONTHLY_TOKEN_LIMIT` (2,000,000) vision tokens a
  month (0 = unlimited; the admin is exempt). Pages are rendered with a long edge of `VISION_MAX_IMAGE_PX`
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
    with no room isn't offered the tool, and the assistant says so.
- `WORKER_MEM_LIMIT` (default `4g`) is the worker container's memory limit (Compose `mem_limit` and
  `memswap_limit`); PDF pages are rendered in child processes limited to 1 GiB each.

## 3. Run

```bash
docker compose up -d --build
docker compose ps                  # wait until the services report "healthy" (the worker has no healthcheck)
```

Open **http://localhost:8080** (or your `APP_PORT`) and sign in with the admin credentials from `.env`.

Useful commands:

```bash
docker compose logs -f backend     # API logs
docker compose logs -f worker      # document processing logs
docker compose restart backend worker
docker compose down                # stop (data is kept in Docker volumes)
docker compose down -v             # stop AND delete all data
```

- After editing `.env`, run `docker compose up -d` (it recreates the containers whose settings changed).
- After changing code, run `docker compose up -d --build`. If a rebuilt service still serves the
  old version, run `docker compose up -d --force-recreate <service>` (seen once with `frontend`).
- Stopping can take a while by design: the backend gives open answers up to about 30 s to save
  their partial text, and the worker gives running jobs up to 60 s to finish.

## 4. Using it

**Chat page:** conversations on the left, the streaming chat in the middle, and documents on the
right (under 1100 px the conversation list becomes a drawer; under 800 px the document panel
becomes a slide-over). Upload PDF, Word (`.docx`), PowerPoint (`.pptx`), Excel (`.xlsx`), HTML,
Markdown, text, CSV or JSON files by dropping them on the panel or clicking **browse**. Each
file is parsed, chunked and embedded in the background. Answers cite the passages they used as
`[1]`, `[2]`, …, and you can expand the sources under every answer. Use the checkboxes in the
document panel to limit which documents a question searches. **Stop** keeps the partial answer.
Users change their own password from the account menu (the admin's password lives in `.env`).

**Files from chat:** ask for a file in your own words, for example "make a PDF report of the Q3 figures from
my documents", "put these numbers in a spreadsheet", "draft a Word letter" or "export this as markdown". The
assistant creates PDF, Word (`.docx`), Excel (`.xlsx`) and Markdown (`.md`) files (with chat models that
support function calling; on desktop, the composer hint says when the selected model can). A card under the answer
shows the file being written, then its name, type, size and indexing status, with a **Download** button.
Every file is saved to your private documents ("Created in chat" in the document panel), counts toward your
document and storage limits, and is indexed, so later chats can search it; passages from such files are
marked as earlier answers rather than sources. When the content uses your documents, the file ends with a
Sources section. Every document in the panel, uploads included, has a Download button.

- **What each format holds:**
  - PDF and Word: headings, paragraphs, bold and italic, lists, tables, code blocks and links. PDFs also get page
    numbers.
  - Excel: one or more sheets with a bold, frozen header row and an auto-filter. Numbers and dates are stored as
    real values, and text that looks like a formula stays text.
  - Markdown: the same content as a `.md` file that loads nothing (images become text, HTML is escaped).
- **Limits (defaults):** up to 3 files per answer, at most 100,000 characters each.
- **When it fails:** a file that fails (for example, over your document limit) shows a card with the reason, and
  the assistant explains it.
- **Stop:** pressing Stop while a file is being written cancels it; if it was already being saved, it is still in
  your documents.

**Admin page** (`/admin`, admin only). An overview at the top shows users, documents, failed
and stale documents, the ingestion queue and the active embedding and vision models ("Off" when no
vision model is active), and offers a re-index when documents are stale or failed.

- **Users:** create users (the only way to get an account), reset passwords, deactivate, delete.
  Per-user limits (`0` = unlimited, except for the max file size):
  - monthly token budget (chat tokens only; embedding and vision tokens are shown but don't count)
  - chat requests per minute
  - which chat models they may use
  - max file size, max number of documents, max total storage
- **Models:** three sections, Chat, Embedding and Vision. Add, edit, enable/disable, choose the default
  chat model, the active embedding model and the active vision model, and **Test** the connection (for a
  vision model, it sends a small generated image and checks the model reads it).
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
- **Usage:** chat, embedding and vision tokens and the estimated cost per user, per model and per day, for
  any month. Only chat tokens count toward user budgets; vision tokens have their own monthly allowance
  (`VISION_MONTHLY_TOKEN_LIMIT`).
- **Documents:** every document (uploads and files created in chat), with re-index and delete actions, and
  **Re-index all documents**.
  Documents read with the vision model show how many pages it read ("pages read by vision"). PDFs that
  failed as scanned before the vision model was enabled can be retried (Retry in the document panel, or
  "Retry failed" in the admin overview).

The admin can upload to the **shared knowledge base** (toggle in the document panel). Shared
documents are searched in every user's chats. Users' own uploads stay private to them. The
admin bypasses all usage limits.

## 5. Switching or adding models (LiteLLM)

In **Admin → Models**, click **Add chat model**, **Add embedding model** or **Add vision model** and enter
a LiteLLM model string:

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

```bash
# Backend tests: SQLite, fake Redis, in-memory Qdrant, mocked LiteLLM; no Docker or network needed
cd backend
uv venv --python 3.13 .venv            # once (uv downloads Python 3.13 if needed)
uv pip sync requirements-dev.txt       # again whenever the locks change
#   or, with python3.13 on PATH: python3.13 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q          # about 1,170 tests, about 2.5 minutes

# Lint and type-check (tools not in the requirements; run through uvx)
uvx ruff@0.13.0 check --target-version py313 --line-length 120 --select E,F,W,I,B --ignore B008 app tests
uvx mypy@1.18.2 --python-executable .venv/bin/python --ignore-missing-imports app

# Frontend
cd ../frontend
npm ci                                  # installs exactly package-lock.json, as the Docker build does
npx tsc --noEmit
npx vite build --outDir "$(mktemp -d)/dist"   # or `npm run build` (writes frontend/dist, git-ignored)
```

More on the tests: [backend/tests/README.md](backend/tests/README.md). The frontend has no test
runner; verify it with the manual smoke test in `docs/briefs/frontend-verification.md`.

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
.venv/bin/uvicorn app.main:app --reload --port 8000

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

## 7. Security notes

- The UI is bound to `127.0.0.1` only. Before exposing it on a network, put it behind HTTPS and
  set `COOKIE_SECURE=true` (see `docs/HANDOFF.md`, next steps).
- Sessions are httpOnly, SameSite=Strict cookies holding a signed JWT (a `Bearer` header also
  works, for scripts). Changing a password or deactivating a user ends their sessions immediately.
- nginx refuses state-changing requests that the browser marks as same-site or cross-site
  (`Sec-Fetch-Site`), because SameSite cookies ignore ports and other `localhost` pages count as
  same-site. It also sets a strict CSP and other security headers, caps request bodies at 1 MB,
  and checks the session before accepting an upload body.
- Failed logins are throttled per username (10 per 15 minutes) and per client IP (100). Behind
  Docker's port publishing every client has the same IP, so the per-IP limit acts as one global
  limit.
- The admin account is defined only by `.env`. Changing `ADMIN_USERNAME` demotes and deactivates
  the old admin account on the next start; changing `ADMIN_PASSWORD` signs out the admin's
  existing sessions.
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
- Downloads (`GET /api/documents/{id}/download`) follow the listing's visibility rules (anything else is a 404)
  and are sent as attachments with `nosniff`, `no-store` and a sandbox CSP. PDF and Office files get their own
  content type; everything else, Markdown and HTML included, is `application/octet-stream`.
- Secrets are never logged; provider errors are turned into user-facing messages (`llm.friendly_error`).

## 8. Known limitations

- Scanned PDF pages are read by the vision model, which costs tokens: with Claude Sonnet 5.5 about $0.007 for
  a simple page (measured) and up to about $0.03 for a dense one, up to `VISION_MAX_PAGES` pages per document
  and `VISION_MONTHLY_TOKEN_LIMIT` tokens per user and month. The allowance is checked when a document starts
  processing, so documents already in progress can take a user past it. There's no local OCR engine.
- Charts drawn as vector graphics (most Office, LaTeX and matplotlib exports) aren't detected; only raster
  images are. Pages whose content is over 2 MiB aren't checked for images.
- Only PDFs go to the vision model: images in DOCX and PPTX files aren't read yet. Password-protected PDFs
  aren't supported.
- Generated PDFs support Latin, Greek, Cyrillic and ten Indian scripts (Noto fonts are bundled). A few
  characters of another script (Chinese, Arabic, Hebrew, Thai, …) appear as □ with a note; more than 2 % of
  the letters and the PDF is refused with a hint to create a docx or md file, which keep every script. Emoji
  are left out of PDFs.
- Generated files have no images or charts, and an earlier file can't be converted to another format: "make
  it a Word document too" writes the content again from the documents, which costs another file's tokens.
  Citations of a generated PDF have no page number, because it is indexed from its Markdown source.
- One answer that writes files makes up to `GENERATED_FILES_PER_ANSWER` + 2 model calls, each re-sending the
  context; with a monthly budget, no further call starts once the answer has used what was left, but up to two
  answers running at once can still go past the budget.
- The file tool costs tokens even when no file is asked for: about 1,550 input tokens per model call with a
  capable model. Its calls may also use up to 48,000 output tokens instead of 16,000. Set `CHAT_FILE_TOOLS=false`
  if you don't need files.
- The admin can't download users' private documents; downloads follow the same visibility as the document list.
- Deleting a user deletes their usage history too (chat, embedding and vision), so their costs leave the
  usage report.
- Legacy `.doc`, `.ppt` and `.xls` files must be saved in the modern format first.
- Database tables are created automatically on start. There are no migrations yet: add Alembic
  before changing the schema of a database with real data.
- Run exactly one worker container. Scale with `WORKER_CONCURRENCY` instead.
- No backups are configured.

The full list, with the open risks, is in `docs/HANDOFF.md`.
