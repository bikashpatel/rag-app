# Backend tests

Integration tests that run the whole backend in-process, with no Docker and no network:

| Dependency | In tests |
|---|---|
| Postgres | SQLite through `aiosqlite`, a new database file per test (foreign keys enforced) |
| Redis | `fakeredis`, a new server per test |
| Qdrant | the in-memory client (`QDRANT_URL=:memory:`), a new one per test |
| LiteLLM | `FakeLLM` in `conftest.py` replaces `litellm.acompletion`, `litellm.aembedding` (vision calls included) and `litellm.aimage_generation` (real PNG, JPEG or WebP bytes drawn by Pillow) |
| PDFium | the real render child (`app/services/pdf_render_child.py`) runs as a subprocess; a fake child script simulates crashes and timeouts |
| File rendering | the real file render child (`app/services/filegen/child.py`) runs as a subprocess; fake children simulate timeouts, crashes and silent exits |
| Google (OAuth and Gmail) | a fake Google, never the real one: `fake_google.py` (OAuth: consent, token, refresh, revoke, grants per account) and `fake_gmail.py` (a mailbox and the Gmail REST routes) served in-process through `httpx.ASGITransport`; `fake_google_server.py` runs the same fake standalone for Docker smoke runs |
| A provider for the real LiteLLM | `test_llm_tools.py` starts a local Starlette server that speaks the Anthropic Messages and OpenAI chat-completions SSE, and checks the request bodies the real LiteLLM sends; `test_llm_images.py` does the same for OpenAI's `/v1/images/generations` (request body, refusals, and how many requests slow, stopped and failing calls make) |

## Run

```bash
cd backend
# Once. With uv (it downloads Python 3.13 if needed; its venvs have no pip):
uv venv --python 3.13 .venv && uv pip sync requirements-dev.txt
# or with a python3.13 on PATH:
python3.13 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt

.venv/bin/python -m pytest -q                    # everything: about 2,380 tests, 6 to 9 minutes
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

### Coverage

```bash
.venv/bin/python -m pytest -q --cov --cov-report=term-missing:skip-covered   # add --cov-report=html for htmlcov/
```

`.coveragerc` measures `app` with branch coverage. It sets `concurrency = thread, greenlet`: SQLAlchemy's asyncio layer
runs the ORM in greenlets, and without `greenlet` every line after a database call goes unrecorded (about 4 points of
the total). The PDF and file render children (`pdf_render_child.py`, `filegen/child.py`) are omitted: they run as
`python -I` with a scrubbed environment (test_pdf_images checks it), which coverage's subprocess support needs to pass an
environment variable through; the code they call is measured in-process. The outputs (`.coverage`, `htmlcov/`,
`coverage.xml`) are git-ignored.

### Postgres

The tests run on SQLite. Tests marked `postgres` run on a throwaway Postgres database instead, the app included, and are
skipped unless `TEST_POSTGRES_URL` is set. With `TEST_DATABASE=postgres` as well, every test runs there. Each test drops
and re-creates the database's `public` schema, so give each pytest process its own database.

**Never point `TEST_POSTGRES_URL` at the app's database.** `docker-compose.dev.yml` publishes the real one (database
`rag`, with the owner's account, documents and Gmail connection) on 127.0.0.1:5432, and the first test would drop all of
it. `conftest.py` refuses a database whose name doesn't contain `test` or `scratch`, and stops the run instead of emptying
a `public` schema that holds tables it didn't create (it marks the schema it creates with a comment).

E.g. against a scratch container (no volume, a random port and password; removed when stopped):

```bash
name=rag-test-pg-$(openssl rand -hex 4) password=$(openssl rand -hex 16)
docker run -d --rm --name "$name" -e POSTGRES_USER=rag -e POSTGRES_PASSWORD="$password" -e POSTGRES_DB=scratch \
  --tmpfs /var/lib/postgresql/data -p 127.0.0.1::5432 \
  postgres:17.11-alpine@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24  # docker-compose.yml's
until docker exec "$name" pg_isready -q -h 127.0.0.1 -U rag; do sleep 1; done
export TEST_POSTGRES_URL="postgresql+asyncpg://rag:$password@127.0.0.1:$(docker port "$name" 5432 | head -1 | cut -d: -f2)/scratch"
.venv/bin/python -m pytest -q -m postgres                      # 10 tests, seconds
TEST_DATABASE=postgres .venv/bin/python -m pytest -q           # everything on Postgres, about 17 minutes
docker stop "$name"
```

On Postgres the suite skips one test (marked `sqlite_only`), which reads the SQLite file synchronously.

## What the suite doesn't cover

SQLite ignores `SELECT ... FOR UPDATE` and the other row locks, so the races they guard run only in the
`postgres`-marked tests: `test_postgres_locks.py` (parallel uploads against the document, storage and duplicate checks
with the in-process locks taken away, so only the row lock serializes them; `db.init_db`'s advisory lock; the UTC session
time zone of `db.make_engine` under a database set to another zone), `test_account_postgres.py` and the image
reservation race in `test_image_tool.py`. Still untested as races between processes: an admin password reset racing a
password change, a document deleted while the worker finishes it, and the embedding model edited while a job writes
vectors (the tests cover the logic around these, also on Postgres with `TEST_DATABASE=postgres`, not the locking). What
was checked by hand against Postgres 17 is listed in `docs/HANDOFF.md`.

Cross-cutting checks that cover new code without being edited: `test_route_guards.py` reads the routes from the app's
route table, so routes hidden from the OpenAPI schema count too (every route but the public ones answers 401 without a
session, and a new route taking an id must be classified, and if it is a user's resource, answer another user like a
missing id; `test_admin.py` lists the admin routes from the same table); `test_deletion_completeness.py` requires every
foreign key to be `ON DELETE CASCADE` or a listed `SET NULL` key (never `SET NULL` to `users`), follows those keys from
a deleted user, conversation or document (cascaded rows gone, `SET NULL` rows kept with the key cleared, and nothing of
a deleted user's kept at all), and its guards fail until the setup gives the deleted user a row in every table and
`SET NULL` key a deletion reaches. Failures of the stores in best-effort steps are in
`test_stores.py`, and the OAuth token and revoke endpoints failing in `test_plugin_token_failures.py`.

Files from chat are tested by parsing the generated files back (pypdf, pypdfium2, python-docx, openpyxl),
in-process and through the render child; `test_document_tool.py` drives whole answers through
`POST /api/chat/stream` (and `_stream_answer` for Stop), and `test_llm_tools.py` checks the replay of thinking
blocks and tool calls against the real LiteLLM. `test_document_generation.py` is an independent pass written from
SPEC.md and the brief: every stream is checked against the SSE contract (`check_stream`) and every answer's model
calls against the replay rules (`check_replays`), files are parsed back from the download endpoint, and Stop goes
through the keepalive wrapper (`_sse_with_keepalive`). The speed guards in `test_filegen.py` (a 30,000-character mixed
document under 10 s, the largest allowed PDF table under 30 s) each took under a second here.

The vision tests call no real model and need no network: `FakeLLM` answers vision calls, and the PDFs are
built in code. `test_vision_independent.py` is an independent pass written from the vision brief
(classification, cap and priority, the allowance, the page cache, usage, retries, stop checks, render child
failures, seeding). Rendering is real (pypdfium2 in a child process), so they need the `pypdfium2` wheel from the
lock. `RLIMIT_AS` isn't enforced on macOS, so the render child's memory limit is only checked in the Linux
worker image (see `docs/HANDOFF.md`).

Images from chat: `test_image_tool.py` has the units (the tool's schema and arguments, which image models are supported,
`llm.generate_image`'s request, errors, usage and cost, the reservation of the monthly image count, the unusable
marks); `test_image_generation.py` drives whole answers through `POST /api/chat/stream` and `_sse_with_keepalive`
(events, storing, the image endpoints, embedding in PDF and DOCX files, who is offered the tool, limits, every
failure of the brief's section 17, the Stop matrix, deletes, the admin side). `test_image_generation_independent.py`
is an independent pass written from SPEC.md and the brief: every stream is checked against the SSE contract
(`check_stream`), generated PDF and DOCX files are parsed back (drawn sizes, captions, alt text), and it covers the
monthly count's default, overrides, races and month boundary, bad image bytes and metadata, serving and access, the
references `create_document` must refuse, reloads, deletes and the admin side. Images are validated by the real filegen
child. CHAT_IMAGE_TOOLS is off by default here, so the tests written before images keep their file-only prompts and
tool lists; the main file-loop scenarios of `test_document_tool.py` and `test_document_generation.py` run a second
time with it on (`tool_mode` "files+images"), as in production.

Plugins and Gmail: the plugin registry is **empty** in every test (the autouse `plugin_registry` fixture), so the tests
written before plugins keep their prompts, tools and queries. Plugin tests opt in:

- the framework tests use `fake_plugin.py`, a small plugin with a read tool and a side-effect tool: `test_plugin_crypto.py`,
  `test_plugins_oauth.py`, `test_plugins_connections.py`, `test_admin_plugins.py`, `test_chat_plugins.py`,
  `test_plugin_imports.py` and `test_llm_complete.py`. The last one also runs the real LiteLLM with its HTTP layer
  replaced, to check the request check's request bodies;
- the Gmail tests use the real `GmailPlugin` against the fake mailbox: `test_gmail_*.py`;
- `test_plugins_gmail_independent.py` is an independent, adversarial pass written from SPEC.md and the brief.

`FakeLLM` answers the request check's non-streaming calls with `intent_reply` (default: every action asked for; build
one with `intent_answer("draft", ...)`) and keeps them in `intent_calls`, not `chat_calls`.

Account and settings (docs/briefs/ui-shell-settings.md): `test_preferences.py`, `test_login_history.py` (sign-ins,
`GET /me/account`; clients from other IPs through `httpx.ASGITransport(client=(ip, port))`), `test_logout_others.py`,
`test_my_usage.py` (the report's clock is pinned with `account.current_time`), `test_export.py` (the deadline's clock is
`export.clock`; the locks and build slots are reset per test) and `test_plugin_catalog.py`; "delete all chats" is in
`test_conversations.py`. `test_ui_settings_independent.py` is an independent pass over all of them, written from SPEC.md
and the brief.

The tests never read the repository's `.env`: `conftest.py` sets `LITELLM_MODE=PRODUCTION`, because in its default DEV
mode importing LiteLLM loads the nearest `.env`, which would give the tests the owner's real settings (a test checks it).

## Writing tests

Read `conftest.py` before adding tests. It provides:

- **Fixtures:** `app` (lifespan running: tables created, admin synced, models seeded, including the
  active vision model "Claude Sonnet 5.5 (vision)"), `client` (anonymous), `admin_client`, `create_user`
  (creates a user through the admin API and returns a logged-in client), `new_client`, and these autouse
  ones: `isolated_state` (fresh state per test), `fake_llm` (the `FakeLLM` in use; request it to set the
  streamed chunks, usage, errors or delays), `vision_sleeps` (the vision reader's retry waits are
  recorded instead of slept, and its backoff delays are zero) and `ingestion_sleeps` (the waits before
  ingestion retries a dropped connection or a timeout, recorded instead of slept; the clock that ends the
  retries is `ingestion._clock`).
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
- **Images:** `FakeLLM.aimage_generation` keeps each call's kwargs in `image_calls`; it waits on `image_gate` (an
  `asyncio.Event`) or `image_delay`, raises `image_error` (an exception, or a function of the call number), returns
  `image_reply` bytes instead of the default image, only a URL with `image_url_only`, the usage `image_usage`
  ((input, output) tokens, None = not reported) and `image_revised_prompt`. The default image is `fake_image(width,
  height, fmt)` at 1/16 of the requested size (64x64, 96x64 or 64x96). The `image_tools` fixture turns
  CHAT_IMAGE_TOOLS on (the seeded GPT Image 2 is the active image model); `refusal(code)` builds a provider's safety
  refusal as LiteLLM raises it. `tool_call("create_image", prompt=..., title=..., alt_text=...)` scripts a call.
- **Scripted chat streams (files from chat):** `fake_llm.script` is a list of `ScriptedCall`s; each streaming
  call takes the first one (then FakeLLM behaves as usual). A `ScriptedCall` lists `blocks` in stream order:
  `("thinking", (text deltas, signature))`, `("redacted", data)`, `("text", str)` and `("tool", (name,
  arguments))`, where `tool_call(title=..., format=..., content=...)` builds a `create_document` call. The
  chunks have the shapes LiteLLM 1.103 produces (`shape="anthropic"` or `"openai"`): signed thinking blocks
  after their deltas, tool calls started by one chunk and continued `fragment` characters at a time, then the
  finish reason (`finish_reason`, default `"tool_calls"` with tools, else `"stop"`; `""` sends none) and the
  usage. `error` is raised after the chunks, or by the call itself with `before_output=True` (a 400). Every
  call's kwargs are kept in `chat_calls`, with a copy of its messages (the app extends one list across the
  calls of an answer). `stall_after=n` makes the plain stream wait forever before its n-th chunk. The
  `fake_llm` fixture also resets which models rejected tools (`llm._tools_rejected`).
- **File render child:** `file_child("sleep" | "crash" | "silent" | "wait", crash="MemoryError")` replaces
  `document_tool._child_command` with a script that sleeps, prints a crash result, exits without output after
  writing to stderr, or waits for a file named `go`; the returned object lists the children's PIDs.
- **Plugins:** `plugins` installs the fake Google behind the plugins' HTTP client, with the fake OAuth client, a test
  `TOKEN_ENCRYPTION_KEY` and `PUBLIC_BASE_URLS=http://testserver`. Its `connect(client, plugin_id, account=...)` runs
  the whole browser flow, and `oauth_result(response)` reads the callback's result. Install a plugin with
  `registry.use_plugins([...])`. `gmail` (in `gmail_support.py`) also installs `GmailPlugin`, enabled with every
  capability, and the default fake mailbox; `gmail.connect(client)` connects a user, and helpers read pending sends,
  audit rows and the calls the fake received.
- **Helpers:** `upload`, `process`, `process_queue`, `ingest`, `chat`, `sse_events` (it ignores SSE comment
  lines such as `: keepalive`).
- **File builders:** `pdf_bytes`, `docx_bytes`, `pptx_bytes`, `xlsx_bytes`, `html_bytes`, `md_bytes`,
  `txt_bytes`, `csv_bytes`, `json_bytes`, `sample_file(extension, text)`. For PDFs with images:
  `image_pdf_bytes((text, area), ...)` (US Letter pages with a text layer and a Pillow chart over the bottom
  `area` share; `rotate`, `in_form` and `shared_image=False`, since one image shared by 3 or more pages, or by
  every page, is ignored as a letterhead), `scanned_pdf_bytes(*texts)` (image-only pages written by Pillow),
  and `raw_pdf_bytes(*contents, objects=..., xobjects=...)` with `pdf_stream` and `jpeg_xobject` for
  strips, invisible text, overlays, inline images and crafted pages. A page needs at least 40
  non-whitespace characters of text to count as having a text layer.

Import the helpers with `from conftest import ...`. Fix failures in the app code, not by weakening a test.
