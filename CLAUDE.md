# AskNuvexusDoc: notes for coding agents

AskNuvexusDoc (asknuvexusdoc.com) is a self-hosted, single-tenant RAG chat app (locally hosted for now): users upload
documents and chat with them through a streaming UI; an admin manages users, models and usage limits. It was called
"RAG Assistant" until 2026-10-28; technical identifiers (the `rag-app` folder, Compose, container and volume names,
`rag_*`/`rag:`/`rag.` keys, environment variables) keep their old names on purpose.

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
  user, upload limits** (max file size, max documents, max total storage). Since plans (2026-10-27) these
  come from the user's plan, per period, with optional per-user overrides set by the admin (the allowed-models
  list only narrows the plan's models).

## Key design decisions (keep unless there's a strong reason)

- Session = signed JWT in an httpOnly, SameSite=Strict cookie (Bearer header also accepted);
  `users.token_version` invalidates sessions on password change or deactivation.
- The one admin is defined by `ADMIN_USERNAME`/`ADMIN_PASSWORD` and synced on every start.
  The admin bypasses usage limits.
- Ingestion runs in a separate `worker` container (same image) fed by a Redis list
  (`RPUSH`/`BLPOP` on `rag:ingest_queue`); no Celery.
- One Qdrant collection per embedding model; switching the active embedding model marks
  documents stale and offers re-indexing. Every search carries the access filter.
- The chat allowance (`monthly_token_limit`, per plan period) counts chat answers only, at each model's price in
  standard tokens ($3.20 per million). Embedding tokens are tracked but don't count.
- Plans (`docs/briefs/plans-billing.md`): access, features and limits come from the user's plan period (or
  open-ended complimentary plan) through `billing.entitlements.load`, never from `users` columns or settings
  directly; every route that needs a plan uses `deps.get_entitled_user` (402 with a `code`); the admin is exempt.
  Payments go through Razorpay Payment Links (no Razorpay script on our origin). A plan starts only on a payment
  fetched from Razorpay's API (after the return's signature check, a webhook's HMAC or the reconciler's poll) whose
  amount, currency, order and mode match the stored order; `payments.apply_payment` applies each payment exactly once,
  under the user's row lock.
- Schema changes go through Alembic (`backend/migrations/`, README there): `init_db` runs `upgrade head` at
  start-up in one transaction under the advisory lock. Every change, a new table included, needs a reviewed
  revision; migrations only go forward, and a rollback means restoring the backup taken before the deploy.
- Default chat model: `anthropic/claude-opus-5-5`. Claude 5.5 models reject a custom
  `temperature`, so leave it null. Seeded OpenAI models (`openai/gpt-5.5`,
  `openai/gpt-5.4-mini`) came from LiteLLM's model map; confirm them with the admin **Test** button.
- Dependencies are pinned to exact versions released at least two weeks before adoption.
  Never use LiteLLM 1.82.7 or 1.82.8 (compromised PyPI releases in 2026).

## Commands

```bash
# Backend tests (no Docker needed; see backend/tests/README.md)
cd backend && python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt && pytest
pytest --cov --cov-fail-under=94  # coverage gate, as in CI

# Frontend
cd frontend && npm ci && npx tsc --noEmit && npx vite build
npm test                          # Vitest unit/component tests (TZ and locale pinned)
npm run test:coverage             # fails below the thresholds in vite.config.ts
npm run dev                       # http://localhost:5173, proxies /api to :8000

# Browser end-to-end (against the running stack; canned answers, no model calls)
cd e2e && npm ci && npx playwright install chromium && npx playwright test

# Full-stack integration (isolated rag-it stack with fake model and Google servers)
integration/run.sh                # ports 18090-18092; never touches rag-app or .env

# Whole stack
cp .env.example .env              # fill SECRET_KEY, ADMIN_PASSWORD, POSTGRES_PASSWORD, keys
docker compose up -d --build      # http://localhost:8080

# Deploy (the backend migrates the database while it starts)
scripts/backup.sh                 # first; keep the BACKUP_NAME it prints (the way back)
APP_VERSION=$(git describe --always --dirty) docker compose up -d --build
docker compose up -d --force-recreate frontend   # nginx finds the new backend only after a restart
docker compose exec backend alembic current      # the schema's revision

# Migrations (backend/migrations/README.md): a new revision, against a throwaway Postgres only
cd backend && .venv/bin/alembic revision --autogenerate --rev-id 0003 -m "..."
.venv/bin/python -m pytest -q tests/test_migrations.py

# Backups and restore (scripts/README.md)
docker compose run --rm restore list           # verify NAME / show NAME
scripts/restore.sh NAME                        # dry run; --yes restores (destructive)
integration/run.sh                             # includes the restore drill and the alert test
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
