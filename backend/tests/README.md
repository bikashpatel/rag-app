# Backend tests

Integration tests that run the whole backend in-process, with no Docker and no network:

| Dependency | In tests |
|---|---|
| Postgres | SQLite through `aiosqlite`, a new database file per test (foreign keys enforced) |
| Redis | `fakeredis`, a new server per test |
| Qdrant | the in-memory client (`QDRANT_URL=:memory:`), a new one per test |
| LiteLLM | `FakeLLM` in `conftest.py` replaces `litellm.acompletion` and `litellm.aembedding` |

## Run

```bash
cd backend
# Once. With uv (it downloads Python 3.13 if needed; its venvs have no pip):
uv venv --python 3.13 .venv && uv pip sync requirements-dev.txt
# or with a python3.13 on PATH:
python3.13 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt

.venv/bin/python -m pytest -q                    # everything: about 700 tests, about 70 s
.venv/bin/python -m pytest tests/test_smoke.py   # one file
.venv/bin/python -m pytest -k chat -x            # by name, stop at the first failure
```

`requirements-dev.txt` is a hashed lock generated from `requirements-dev.in` (see the top of
`requirements.in`); after it changes, run `uv pip sync requirements-dev.txt` (or `pip install -r`)
again.

`pytest.ini` holds the settings: asyncio auto mode (plain `async def test_...` functions, no
markers) with one event loop for the whole run. You don't need to set any environment variables:
`conftest.py` sets them before the app is imported, using dummy provider keys and a temporary
directory per pytest process. Several pytest processes can run at the same time.

## What the suite doesn't cover

SQLite ignores `SELECT ... FOR UPDATE` and the other row locks, so the races they guard are
untested here: uploads from several backend processes against the quota and duplicate checks, an
admin password reset racing a password change, a document deleted while the worker finishes it,
and the embedding model edited while a job writes vectors (the tests cover the logic around these,
not the locking). The advisory lock in `db.init_db` and the UTC session time zone set in
`db.make_engine` are Postgres-only too. What was checked by hand against Postgres 17 is listed in
`docs/HANDOFF.md`.

## Writing tests

Read `conftest.py` before adding tests. It provides:

- **Fixtures:** `app` (lifespan running: tables created, admin synced, models seeded), `client`
  (anonymous), `admin_client`, `create_user` (creates a user through the admin API and returns a
  logged-in client), `new_client`, and two autouse ones: `isolated_state` (fresh state per test)
  and `fake_llm` (the `FakeLLM` in use; request it to set the streamed chunks, usage, errors or delays).
- **Helpers:** `upload`, `process`, `process_queue`, `ingest`, `chat`, `sse_events`.
- **File builders:** `pdf_bytes`, `docx_bytes`, `pptx_bytes`, `xlsx_bytes`, `html_bytes`, `md_bytes`,
  `txt_bytes`, `csv_bytes`, `json_bytes`, `sample_file(extension, text)`.

Import the helpers with `from conftest import ...`. Fix failures in the app code, not by weakening a test.
