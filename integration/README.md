# Integration tests

These tests start an isolated copy of the whole Docker stack: nginx frontend, FastAPI backend, ingestion worker,
Postgres, Redis and Qdrant. Next to it run a **fake OpenAI-compatible model server** and the **fake Google** (OAuth and
Gmail) from `backend/tests`. The tests drive real cross-service flows through nginx, then tear everything down.

They complement the other suites:

| Suite | What runs | Models |
|---|---|---|
| `backend/tests` | the API in-process: SQLite, fakeredis, in-memory Qdrant | LiteLLM mocked |
| `integration/` (this) | the real containers talking to each other, plus two fakes | the real LiteLLM, calling the fake server over HTTP |
| `e2e/` | a browser against the live stack on port 8080 | whatever that stack uses |

**It costs nothing.** No real model or Google endpoint can be reached:

- The provider keys are fake (`sk-it-fake`, `sk-ant-it-fake`), and every enabled model points at the fake server.
- The backend and the worker sit on an internal Docker network with no route to the internet.

## Run it

```bash
python3.13 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements-dev.txt  # once
integration/run.sh
```

`run.sh` does the following:

1. Removes a stack an earlier run left behind, if any, and generates `integration/.run/it.env`.
2. Builds the images and runs `up -d --wait`.
3. Runs `pytest` on `integration/tests`.
4. Always runs `down -v --remove-orphans`, even when the tests fail or you press Ctrl-C, then checks that no container,
   volume or network of the project is left and lists any that are.

Its exit code is pytest's, 2 when the stack couldn't be set up, or 3 when the tests passed but the teardown left
something behind. It prints the build, start and test times (wall clock: on a laptop that sleeps, pytest's own
"in N s" is the real test time). When the stack doesn't come up or the tests fail, it saves the stack logs to
`IT_LOGS`.

| Option or variable | Effect |
|---|---|
| `--keep` | leave the stack up afterwards for debugging (remove it with `integration/stack.sh down -v --remove-orphans`) |
| `--no-build` | reuse the images of an earlier run |
| `--rmi` | also remove the `rag-it-*` images on teardown (CI) |
| `-- <pytest args>` | e.g. `integration/run.sh -- -k gmail -x` |
| `IT_APP_PORT`, `IT_FAKE_LLM_PORT`, `IT_FAKE_GOOGLE_PORT` | published ports on 127.0.0.1 (default 18090, 18091, 18092; 8080 is refused; a busy port fails fast) |
| `IT_PYTHON` | the Python that runs pytest (default `backend/.venv/bin/python`) |
| `IT_LOGS` | where the stack logs of a failed run go (default `integration/.run/stack-logs.txt`) |
| `IT_PROJECT` | Compose project name (default `rag-it`; only `rag-it` or `rag-it-<suffix>` with lowercase letters, digits and dashes is accepted) |

With a kept stack you can run single tests directly:

```bash
backend/.venv/bin/python -m pytest -c integration/pytest.ini --rootdir integration integration/tests/test_05_files_from_chat.py
```

`integration/stack.sh <compose args>` runs `docker compose` for the test stack only, for example `stack.sh ps` or
`stack.sh logs backend`.

The suite needs no dependency beyond `backend/requirements-dev.txt`: pytest, httpx, python-docx, python-pptx,
openpyxl, fpdf2, pypdf and Pillow. Test documents are generated at test time.

## Isolation guarantees

- **Its own Compose project.** Every command is `docker compose -p rag-it -f docker-compose.yml -f
  integration/compose.it.yml --env-file integration/.run/it.env …`, built in one place each for the shell
  (`stack.sh`) and Python (`tests/harness.py`). Containers, volumes and networks get the `rag-it` prefix. `run.sh`,
  `stack.sh` and `harness.py` accept only the project names `rag-it` and `rag-it-<suffix>`. Nothing here runs a
  Compose command against `rag-app`; the final test only reads `docker ps` to check that its containers are unchanged.
- **Only its own objects.** `compose.it.yml` puts the label `rag.it.stack=1` on every container, volume and network.
  Before any Compose command, `stack.sh` and `harness.py` list what the project already holds and refuse to go on
  when anything lacks the label, so a `down -v --remove-orphans` can never remove another stack's data that happens to
  use the name.
- **The repository's `.env` is never read.**
  - `compose.it.yml` replaces the base file's `env_file: .env` for the backend and the worker with
    `env_file: !override [integration/.run/it.env]`.
  - Passing `--env-file` replaces `.env` for variable interpolation as well.
  - The shell variables that would win over the env file in interpolation (`POSTGRES_PASSWORD`, `APP_PORT`, …) are
    unset for every Compose call.
  - The same mechanics were checked in a throwaway Compose project before this was written: the override dropped
    `.env`, `--env-file` stopped its interpolation, and a missing `.env` (as in CI) is fine.
- **Throwaway secrets.** `gen_env.py` writes a new `SECRET_KEY`, `ADMIN_PASSWORD`, `POSTGRES_PASSWORD` and
  `TOKEN_ENCRYPTION_KEY` on every run. The file is git-ignored and mode 600. It also holds the fake provider keys and
  the fake Google endpoints and client.
- **No egress.** The default network is `internal: true`. Only the frontend and the two fakes also join `edge`, for
  their ports on 127.0.0.1.
- **Only fake models.** At session start (`conftest.py`, fixture `models`), the admin API adds these fake models with
  `api_base` `http://fake-llm:8000/v1`, makes them the defaults, and disables every seeded model:
  - two chat models;
  - two embedding models (256 and 384 dimensions);
  - a vision model;
  - an image model.
- **Nothing restarts on its own** (`restart: "no"`), so a stack left by an interrupted run stays down after a Docker
  restart.
- **Verified by the suite itself.**
  - `test_01_stack.py` checks the project's containers, volumes and networks, their marker label, and that its network
    is internal.
  - It also checks that the backend's and the worker's own environment (their PID 1) holds every value of the generated
    file unchanged and no variable from anywhere else. That check prints only `True`/`False` and compares hashes, never
    values.
  - It checks that the frontend has no keys and that the app containers can't reach any provider or Google host.
  - `test_99_final.py` checks four more things: every request the fake received carried the app's placeholder key,
    never a provider key; no generated secret or fake key appears in any service log; the live `rag-app` containers
    are the same ones, in the same state, as when the session started; and http://localhost:8080 still answers.

## What it covers

| File | Flow |
|---|---|
| `test_01_stack.py` | all 8 services up and healthy, isolation (see above), env admin login, 403 on admin routes for users and 401 anonymous, only fake models enabled, every admin model **Test** (chat, embedding, vision, image) reaching the fake |
| `test_02_ingestion.py` | every supported extension (`.pdf .docx .pptx .xlsx .html .htm .md .markdown .txt .csv .json`). Each goes upload → file on the shared volume → queue → worker parse, chunk and embed through the fake → Qdrant points = `num_chunks` → `ready` → found in chat. Also: corrupt PDF, DOCX, PPTX and XLSX files fail with a readable error; empty, unsupported, legacy and duplicate uploads are rejected; an embedding outage fails the document and Retry recovers it; other users get 404 |
| `test_03_chat.py` | SSE through nginx: event order, deltas spread over time (no buffering), sources from the right document, `[n]` citations that map to sources, the prompt the fake received, history and retrieval query in turn 2, `document_ids` narrowing or skipping retrieval, persisted conversations and messages, a provider outage ending with `error` (saved), a client disconnect mid-answer saving the partial answer as stopped, and usage that matches the fake's reported tokens, `/auth/me`, `/me/usage`, the admin usage report, the user list and the `usage_events` rows |
| `test_04_access.py` | a user's private passage never reaches another user's sources or prompt (checked in the fake's request log), also with that document's id in `document_ids`. The admin's shared document reaches every user (who can't delete it). Deleting a document removes its Qdrant points, its stored file and its retrieval |
| `test_05_files_from_chat.py` | PDF, DOCX, XLSX and MD from chat. The fake's `create_document` call (built from the schema the backend sent) produces `file` events and a private generated document. The model is called again with the tool result, the call replayed unchanged, and usage summed over both calls. The worker indexes the file; the download is parsed with pypdf, python-docx or openpyxl; the file shows in the conversation; another user gets 404; a later chat retrieves the file, labelled as created by the assistant. No file is made when none is requested |
| `test_06_limits.py` | requests per minute (429 with `Retry-After`), the monthly token budget (429, then lifted), allowed models (403), max file size (413), max documents (409), max storage (409), the admin bypassing limits |
| `test_07_sessions.py` | a password change ends the other sessions and keeps the caller's; an admin reset ends sessions; deactivation gives 401 and blocks login; logout-others; logout; the env admin can't be changed through the API |
| `test_08_worker.py` | worker stopped: uploads stay `pending` in the Redis queue with no embedding call; worker started: they become `ready` |
| `test_09_reindex.py` | switching the active embedding model to the 384-dimension fake marks documents stale (no longer found); re-index all makes them ready in the new collection (vector size 384, old points gone) and searchable; switching back. The active embedding model can't be disabled or deleted |
| `test_10_user_deletion.py` | deleting a user removes their documents, files and sidecars (`.source.md`), vectors, conversations, messages, usage, login events and sessions. A bystander's data stays. Checked through the API, Postgres, Qdrant and the volume |
| `test_11_export.py` | the export ZIP holds the caller's account, documents and conversation (exact file set) and nothing of another user's, no password or key |
| `test_12_gmail.py` | against the fake Google: the OAuth connect flow (PKCE, nonce cookie, callback 303); a replayed callback and a foreign `Origin` refused; the Gmail browser (search, read, import a thread with its PDF and DOCX attachments, indexed, re-import links); chat search citing the email as an `EmailSource`; a draft (request check, draft at the fake, nothing sent); a send that happens only after the confirm with the right `content_hash`, exactly once (another connected user gets 404, a wrong hash 409, a second confirm 409); no revoke while another connection shares the Google account; disconnect revokes; no Gmail tools for unconnected users |
| `test_13_vision_and_images.py` | a scanned PDF fails while no vision model is active. After activating the fake vision model, Retry sends the page image to it, and the transcript is indexed, cited with page 1, and counted as vision tokens. An image from chat goes `writing` → `generating` → `created`: a 1024×1024 PNG with a WebP thumbnail, prompt details, private to its owner, counted as one image. The monthly image limit removes the tool |
| `test_99_final.py` | the run-wide checks listed under isolation |

### Not covered

- Real providers, by design.
- Browser behaviour, which `e2e/` covers.
- Load and long-running streams (the 15 s keepalive).
- The import of Gmail threads from chat (`gmail_import`): the UI import endpoint is covered.

## The fake model server (`fake_llm/server.py`)

It runs with the backend image (FastAPI and uvicorn are already there) and the script mounted read-only, so it needs
no new image or dependency. The backend and the worker reach it as `http://fake-llm:8000/v1`; tests reach it on
`127.0.0.1:$IT_FAKE_LLM_PORT`.

- `POST /v1/embeddings`: a signed, hashed bag of words, L2-normalised. `fake-embed-a` has 256 dimensions and
  `fake-embed-b` 384. A question's nearest neighbours are the passages that share its words, so tests use unique
  marker words (`harness.unique`).
- `POST /v1/chat/completions`, streaming and not, in OpenAI's wire format (usage chunk, `tool_calls` deltas,
  `finish_reason`). It answers from the `<context>` block of the system prompt and cites `[1]`.
  - When the latest user message asks for a file, an image or a Gmail search, draft or send, and the matching tool is
    offered, it calls the tool with arguments fitted to the schema in the request. A quoted string in the message
    becomes the title or subject, and an address the recipient. It answers after the tool result.
  - It also answers the plugins' request check (JSON from the same keywords), vision calls (`vision_reply`) and the
    admin Test.
- `POST /v1/images/generations`: a valid PNG of the requested size as `b64_json`, with usage.
- `GET /_log?kind=&after=` lists every request: kind, model, messages, tool names, the tool call made, the answer, the
  usage it reported, and which kind of key it got (never the key).
- `POST /_config` takes the knobs `vision_reply`, `fail_embeddings`, `fail_chat` and `chunk_delay`; tests use
  `fake.knobs(...)`, which restores them. `POST /_reset` clears the log and the knobs.

`fake_google/server.py` serves the backend tests' fake Google (mounted read-only from `backend/tests`) with one fixed
account and the default mailbox. It adds `GET /_state`: the drafts and sent emails it received, revocations and
active grants. The backend reaches it as `http://fake-google:8099`; the consent URL the browser follows is the
published port.

## CI

The `integration` job of [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs it on GitHub Actions
`ubuntu-latest` (Docker and the Compose plugin preinstalled; `!override` needs Compose 2.24.4 or newer), with no `.env`
in the checkout: a Python 3.13 venv from the hashed `backend/requirements-dev.txt`, the ruff checks below, then
`integration/run.sh --rmi` with `IT_LOGS` in the runner's temporary directory. On failure it uploads that file as the
`integration-stack-logs` artifact (7 days). The logs hold no secret: the run has only throwaway values and fake keys,
and `test_99_final.py` checks that none of them reaches a service log. The generated env file is never uploaded.

## Writing tests

- `tests/harness.py` has:
  - `compose`, `exec_in`, `python_in`, `sql`/`sql_value` (psql in the Postgres container), `redis_cli`, and
    `qdrant`/`qdrant_points`/`qdrant_vector_size` (calls made from the backend container);
  - `stored_files` and `stored_names`;
  - the API helpers `login`, `upload`, `documents`, `wait_document`, `wait_queue_idle`, `chat` (a parsed SSE stream
    with arrival times) and `assert_sse_contract`;
  - `FakeLLM`, with `mark()` then `log(kind, after=mark)` for the requests an action made;
  - `wait_until`, to poll with a deadline instead of sleeping.
- `tests/samples.py` builds a document of every format, plus a scanned PDF.
- Fixtures (`tests/conftest.py`):
  - `admin`, `models` (the fake models by role), `fake`;
  - `make_user(name, **limits)`, a new logged-in user with no limits unless given.
- Every test has a deadline: 180 s, or `@pytest.mark.deadline(seconds)`.
- Lint from the repo root with the backend's rules: `uvx ruff@0.13.0 check --target-version py313 --line-length 120
  --select E,F,W,I,B --ignore B008 integration` and `uvx ruff@0.13.0 format --line-length 120 --check integration`.
- Tests share one stack, so they create their own users and documents and leave global settings as they found them.
  `test_09` switches the embedding model back and re-indexes; `test_13` re-activates the vision model.
