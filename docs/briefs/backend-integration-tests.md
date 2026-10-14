# Brief: backend integration tests

Goal: prove the backend works end to end **without Docker** and fix the app code until it does.
This doubles as the backend integration pass: read every backend file first and fix integration
problems (imports, signatures, contract mismatches with `SPEC.md`, async/session misuse,
Postgres-vs-SQLite portability).

## Environment (macOS)

```bash
cd backend
python3.13 -m venv .venv   # 3.12+ works; the Docker image uses 3.13
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Everything runs in-process:

- **DB:** SQLite via aiosqlite: `DATABASE_URL=sqlite+aiosqlite:///<tmp>/test.db`
- **Redis:** `fakeredis.aioredis.FakeRedis(decode_responses=True)`, installed with
  `app.redis_client.set_redis(...)`
- **Qdrant:** in-memory: `QDRANT_URL=:memory:` (handled in `vectorstore.get_qdrant`)
- **LLMs:** mocked. Monkeypatch `litellm.acompletion` with an async function returning an
  async iterator of chunks shaped like LiteLLM streaming chunks (objects with
  `.choices[0].delta.content`, and a final chunk with `.usage.prompt_tokens/.completion_tokens`).
  Monkeypatch `litellm.aembedding` to return deterministic vectors (e.g. hash-seeded 64-dim) in an
  object with `.data=[{"embedding": [...], "index": i}, ...]` and `.usage.prompt_tokens`.
  Never call real providers in tests.

## Layout

`backend/tests/conftest.py`, several `test_*.py`, and `backend/tests/README.md` (how to run).
In `conftest.py`, set env vars **before** importing app modules: `SECRET_KEY` (≥32 chars),
`ADMIN_USERNAME=admin`, `ADMIN_PASSWORD`, `DATABASE_URL`, `QDRANT_URL=:memory:`, `UPLOAD_DIR=<tmp>`,
`OPENAI_API_KEY`/`ANTHROPIC_API_KEY` to dummy values (so seeded models are enabled). Use
`httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")` and run
the lifespan with `async with app.router.lifespan_context(app)`. Use pytest-asyncio. Build file
fixtures in code: python-docx, python-pptx, openpyxl, a small hand-written PDF with a real text
layer that pypdf can extract, and html/md/txt/csv/json strings. For ingestion, call
`app.services.ingestion.process_document(doc_id)` directly after upload (no worker process).

## Must cover

- Auth: login success/failure, throttling (429), `/auth/me`, logout, change-password (old
  sessions die), inactive user → 401, admin can't change password via API (400).
- Admin: every `/api/admin/*` route returns 403 for non-admins; user CRUD incl. env-admin
  protections and duplicate username (409); model CRUD, default switching, deleting the active
  embedding model (409), test endpoint (mocked); usage report shape; stats; reindex queues
  documents; deleting a user removes their documents (rows, files, vectors).
- `GET /api/models` respects `restrict_models` + allowed models.
- Documents: upload every supported type → `process_document` → `ready` with chunks;
  unsupported and legacy types → 415; too large → 413; duplicate → 409; document-count and
  storage quotas → 409; shared upload by non-admin → 403; delete/reprocess by other users → 404.
- **Access control (critical):** user B must never get user A's private chunks as chat sources;
  shared documents are retrievable by everyone; `document_ids` narrows retrieval; `[]` disables it.
- Chat SSE: event order `meta → sources → (warning) → delta… → done`; user + assistant messages
  and usage events persisted; other users' conversations → 404; budget exhausted → 429;
  rate limit → 429; disallowed model → 403; provider error mid-stream → `error` event and partial
  assistant message saved with `error` set.
- Worker: `recover_pending()` re-enqueues pending/processing documents.

Also check that `python -c "import app.main, app.worker"` works with the required env vars set.

Fix bugs in the **app code**, never by weakening tests. If the contract must change, update
`SPEC.md` and `frontend/src/api/types.ts` together.
