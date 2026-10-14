# RAG Assistant: notes for coding agents

Self-hosted, single-tenant RAG chat app (locally hosted for now): users upload documents
and chat with them through a streaming UI; an admin manages users, models and usage limits.

**Start here:** [`docs/HANDOFF.md`](docs/HANDOFF.md) lists what is done, what is missing,
and the ordered next steps. [`SPEC.md`](SPEC.md) is the binding contract (API, data model,
SSE events, layout). [`docs/briefs/`](docs/briefs/) has detailed requirements per area,
plus the test, verification and review plans.

## What the user asked for (original requirements)

- Containerized with Docker Compose.
- Backend FastAPI + Postgres + Redis; frontend React with a Vite build; vector DB Qdrant.
- LiteLLM for chat-model and embedding-model abstraction, so providers can be switched easily.
- Simple username/password login. Users are created **only** from the admin page.
- Dedicated admin page: manage users, manage models (enable/disable), per-user usage
  controls. Admin credentials come from `.env`.
- Single tenant (no multitenancy).
- Streaming chat UI (not static responses).
- Multiple document formats: PDF, Word, HTML, etc.
- Layout: document upload section on the **right**; chat covers the left and middle.
- The user supplies OpenAI and Anthropic API keys.

Decisions the user made when asked:
- Documents are **private per user, plus a shared knowledge base** the admin uploads to.
  Retrieval searches both.
- Usage controls, all four: **monthly token budget, requests per minute, allowed models per
  user, upload limits** (max file size, max documents, max total storage).

## Key design decisions (keep unless there's a strong reason)

- Session = signed JWT in an httpOnly, SameSite=Strict cookie (Bearer header also accepted);
  `users.token_version` invalidates sessions on password change or deactivation.
- The one admin is defined by `ADMIN_USERNAME`/`ADMIN_PASSWORD` and synced on every start.
  The admin bypasses usage limits.
- Ingestion runs in a separate `worker` container (same image) fed by a Redis list
  (`RPUSH`/`BLPOP` on `rag:ingest_queue`); no Celery.
- One Qdrant collection per embedding model; switching the active embedding model marks
  documents stale and offers re-indexing. Every search carries the access filter.
- The monthly budget counts chat tokens only. Embedding tokens are tracked but don't count.
- Tables are created with `create_all` (no Alembic yet). Add migrations before changing the
  schema of a database with real data.
- Default chat model: `anthropic/claude-opus-5-5`. Claude 5.5 models reject a custom
  `temperature`, so leave it null. Seeded OpenAI models (`openai/gpt-5.5`,
  `openai/gpt-5.4-mini`) came from LiteLLM's model map; confirm them with the admin **Test** button.
- Dependencies are pinned to exact versions released at least two weeks before adoption.
  Never use LiteLLM 1.82.7 or 1.82.8 (compromised PyPI releases in 2026).

## Commands

```bash
# Backend tests (no Docker needed; see backend/tests/README.md once written)
cd backend && python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt && pytest

# Frontend
cd frontend && npm ci && npx tsc --noEmit && npx vite build
npm run dev                       # http://localhost:5173, proxies /api to :8000

# Whole stack
cp .env.example .env              # fill SECRET_KEY, ADMIN_PASSWORD, POSTGRES_PASSWORD, keys
docker compose up -d --build      # http://localhost:8080
```

## Conventions

- Contract changes go into `SPEC.md`, `frontend/src/api/types.ts` and the backend schemas
  together.
- Backend: async SQLAlchemy 2.0, portable queries (tests run on SQLite, prod on Postgres),
  pydantic v2 validation on every input, 404 for other users' resources, never log secrets.
- Frontend: plain CSS with per-area class prefixes (`chat-`, `docs-`, `admin-`) and the
  CSS variables from `src/styles/global.css` (light + dark); no `alert`/`confirm`/`prompt`.
- Match the existing code style: typed, concise, no comment noise or dead code.
- The user is happy for substantive work to be done with parallel agents or workflows, as
  long as they work against `SPEC.md` and the result gets verified (tests and build).
