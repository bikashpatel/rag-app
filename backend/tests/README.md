# Backend tests

Integration tests that run the whole backend in-process, with no Docker and no network:

| Dependency | In tests |
|---|---|
| Postgres | SQLite through `aiosqlite`, a new database file per test (foreign keys enforced) |
| Redis | `fakeredis`, a new server per test |
| Qdrant | the in-memory client (`QDRANT_URL=:memory:`), a new one per test |
| LiteLLM | `FakeLLM` in `conftest.py` replaces `litellm.acompletion` and `litellm.aembedding` (vision calls included) |
| PDFium | the real render child (`app/services/pdf_render_child.py`) runs as a subprocess; a fake child script simulates crashes and timeouts |

## Run

```bash
cd backend
# Once. With uv (it downloads Python 3.13 if needed; its venvs have no pip):
uv venv --python 3.13 .venv && uv pip sync requirements-dev.txt
# or with a python3.13 on PATH:
python3.13 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt

.venv/bin/python -m pytest -q                    # everything: about 860 tests, about 90 s
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

The vision tests call no real model and need no network: `FakeLLM` answers vision calls, and the PDFs are
built in code. Rendering is real (pypdfium2 in a child process), so they need the `pypdfium2` wheel from the
lock. `RLIMIT_AS` isn't enforced on macOS, so the render child's memory limit is only checked in the Linux
worker image (see `docs/HANDOFF.md`).

## Writing tests

Read `conftest.py` before adding tests. It provides:

- **Fixtures:** `app` (lifespan running: tables created, admin synced, models seeded, including the
  active vision model "Claude Sonnet 5.5 (vision)"), `client` (anonymous), `admin_client`, `create_user`
  (creates a user through the admin API and returns a logged-in client), `new_client`, and three autouse
  ones: `isolated_state` (fresh state per test), `fake_llm` (the `FakeLLM` in use; request it to set the
  streamed chunks, usage, errors or delays) and `vision_sleeps` (the vision reader's retry waits are
  recorded instead of slept, and its backoff delays are zero).
- **FakeLLM vision fields:** a non-streaming call with an image part is a vision call, kept in
  `vision_calls` (not `chat_calls`). `vision_reply` is a string or a function of `(page, mode)` (mode
  "transcribe", "describe" or "test"); a function that raises makes that call fail, e.g. with
  `litellm.RateLimitError(..., response=httpx.Response(429, headers={"retry-after": "7"}))`. Also
  `vision_usage` (default `(1500, 200)`, None = not reported), `vision_finish_reason` ("length" = truncated),
  `vision_delay` and `vision_max_in_flight` (the peak number of concurrent vision calls). The helpers
  `vision_page_and_mode(call)`, `vision_pages(fake, mode)` and `vision_part(call, "image_url" | "text")`
  read the calls.
- **Render child fixtures:** `render_child(abort=[...], hang=[...], fail=[...])` replaces the PDF render
  child with a script that delegates to the real one, except that it calls `os.abort()`, hangs or reports an
  error on the given pages (lower `pdf_images._RENDER_PAGE_SECONDS` for `hang`); the returned object lists
  the children's PIDs and the environment variable names they saw. `child_starts` counts real children
  started (e.g. to assert that a refused PDF never reaches PDFium).
- **Helpers:** `upload`, `process`, `process_queue`, `ingest`, `chat`, `sse_events`.
- **File builders:** `pdf_bytes`, `docx_bytes`, `pptx_bytes`, `xlsx_bytes`, `html_bytes`, `md_bytes`,
  `txt_bytes`, `csv_bytes`, `json_bytes`, `sample_file(extension, text)`. For PDFs with images:
  `image_pdf_bytes((text, area), ...)` (US Letter pages with a text layer and a Pillow chart over the bottom
  `area` share; `rotate`, `in_form` and `shared_image=False`, since one image shared by 3 or more pages, or by
  every page, is ignored as a letterhead), `scanned_pdf_bytes(*texts)` (image-only pages written by Pillow),
  and `raw_pdf_bytes(*contents, objects=..., xobjects=...)` with `pdf_stream` and `jpeg_xobject` for
  strips, invisible text, overlays, inline images and crafted pages. A page needs at least 40
  non-whitespace characters of text to count as having a text layer.

Import the helpers with `from conftest import ...`. Fix failures in the app code, not by weakening a test.
