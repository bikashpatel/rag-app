# Handoff: status and next steps

Snapshot taken 2026-10-14 (IST) on the original Windows machine, which had no Docker,
before moving to macOS. Nothing here has run in Docker yet.

## How the work was organised

The lead engineer wrote the contract (`SPEC.md`) and all **foundation** code. Six agents then
built the feature areas in parallel against the spec (one brief each in `docs/briefs/`).
The parallel run was stopped early for the move, so only some feature files exist. None of the
builder agents' final reports were collected. Treat every builder-written file as **unreviewed**.

## File status

### Done: foundation (written and reviewed by the lead; compiles, imports cleanly)

- Root: `SPEC.md`, `README.md`, `CLAUDE.md`, `docker-compose.yml`, `docker-compose.dev.yml`,
  `.env.example`, `.gitignore`
- Backend: `Dockerfile`, `.dockerignore`, `requirements.txt`, `requirements-dev.txt`,
  `app/{__init__,config,db,models,schemas,security,deps,redis_client,main}.py`,
  `app/services/{__init__,llm,usage,vectorstore,model_registry,storage}.py`,
  `app/routers/__init__.py`
- Frontend: `Dockerfile`, `nginx.conf`, `.dockerignore`, `package.json`, `package-lock.json`
  (generated with npm 11 / Node 24; includes darwin + linux-musl native bindings),
  `tsconfig.json`, `vite.config.ts`, `index.html`, `public/favicon.svg`, `src/main.tsx`,
  `src/App.tsx`, `src/vite-env.d.ts`, `src/api/{client,types}.ts`, `src/auth/AuthContext.tsx`,
  `src/lib/format.ts`, `src/styles/global.css`, `src/components/ui/{Modal,Spinner}.tsx`,
  `src/pages/LoginPage.tsx`

### Written by builders: complete per their brief, not yet tested or reviewed

- **Backend chat** ([brief](briefs/backend-chat.md)): `app/services/rag.py`,
  `app/routers/conversations.py`, `app/routers/chat.py`. They import cleanly. A quick read of
  `chat.py` matches the spec: event order, a shielded save of partial answers on stop or error,
  usage recording, and its own DB session inside the stream.

### Partially written

- **Frontend document panel** ([brief](briefs/frontend-documents.md)): helper modules exist
  and type-check: `components/documents/{DocumentList,DocumentRow,FileTag,QuotaFooter,
  ScopeToggle,SelectionBar,UploadDropzone,UploadQueue,icons}.tsx` and
  `{fileTypes,selection,uploadDocument,uploadManager,useDocuments,useFileDrop}.ts`.
  **Missing:** `DocumentPanel.tsx` (the default-exported component in `SPEC.md` §8) and
  `documents.css`. Read the helpers first and build the panel on them (or replace them).

### Not started

| Area | Files | Brief |
|---|---|---|
| Backend auth/admin | `app/bootstrap.py`, `app/routers/{auth,models,admin}.py` | [backend-auth-admin.md](briefs/backend-auth-admin.md) |
| Backend ingestion | `app/services/{parsing,chunking,ingestion}.py`, `app/worker.py`, `app/routers/documents.py` | [backend-ingestion.md](briefs/backend-ingestion.md) |
| Frontend chat | `src/pages/ChatPage.tsx`, `src/components/chat/*` (incl. `chat.css`) | [frontend-chat.md](briefs/frontend-chat.md) |
| Frontend admin | `src/pages/AdminPage.tsx`, `src/components/admin/*` (incl. `admin.css`) | [frontend-admin.md](briefs/frontend-admin.md) |
| Backend tests | `backend/tests/*` (+ `backend/tests/README.md`, already linked from `README.md`) | [backend-integration-tests.md](briefs/backend-integration-tests.md) |

## Verified so far

- `pip install -r backend/requirements-dev.txt` resolves and installs (checked on Python 3.14).
  `npm install` for the frontend works (154 packages in the lockfile).
- Every foundation module and the backend chat files import without errors. `app.main` fails
  to import only because the routers listed above don't exist yet.
- `tsc --noEmit`: the only errors are the missing `pages/ChatPage` and `pages/AdminPage`.

## Next steps (in order)

The areas in steps 1 and 3 touch disjoint files, so they can be built in parallel.

1. **Backend:** write the auth/admin and ingestion files from their briefs.
2. **Backend tests and integration:** follow
   [backend-integration-tests.md](briefs/backend-integration-tests.md). This is also where the
   builder-written chat files get exercised and fixed. Run until green.
3. **Frontend:** finish `DocumentPanel.tsx` + `documents.css`, then build the chat page and the
   admin page from their briefs.
4. **Frontend verification:** follow [frontend-verification.md](briefs/frontend-verification.md):
   typecheck, build, and an API contract cross-check against the real routers.
5. **Review:** work through [review-checklist.md](briefs/review-checklist.md) adversarially and
   fix confirmed findings.
6. **Run it:** `cp .env.example .env`, fill it in, `docker compose up -d --build`, then do the
   manual smoke test in `frontend-verification.md`. The Docker images have never been built;
   expect small fixes.
7. Update this file and `README.md` with the final state.

## Open questions and risks

- The seeded OpenAI model IDs (`gpt-5.5`, `gpt-5.4-mini`) come from LiteLLM's model map and
  haven't been called yet. Verify them with Admin → Models → **Test**.
- Anthropic token usage in streaming (`stream_options.include_usage` through LiteLLM) has only
  been checked in code. `llm.stream_chat` falls back to `litellm.token_counter` estimates if the
  provider reports nothing.
- Postgres-only behaviour isn't covered by the SQLite tests: the advisory lock in
  `db.init_db`, `func.date()` on `timestamptz` in the usage report, and timezone handling.
  Check these against the real stack.
- No Alembic migrations yet (`create_all` at startup).
- Embeddings need an OpenAI key by default (Anthropic has no embeddings API). Ollama (`ollama/…`
  with `api_base` `http://host.docker.internal:11434`) is the no-key alternative.

## macOS setup notes

- Docker Desktop for Mac. All images used are multi-arch, so Apple Silicon is fine.
- Python 3.13 for local tests (`brew install python@3.13`) and Node 22+ (`brew install node`).
- The app's `.env` lives in the repo root next to `docker-compose.yml`. It's git-ignored, so
  copy `.env.example` and fill it in on each machine.
