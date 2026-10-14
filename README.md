# RAG Assistant

A self-hosted, single-tenant Retrieval-Augmented-Generation app: upload documents,
chat with them, and manage users, models and usage from an admin page.

- **Backend:** FastAPI · Postgres · Redis · Qdrant · a separate ingestion worker
- **Frontend:** React + Vite, served by nginx
- **Models:** every model call goes through [LiteLLM](https://docs.litellm.ai/), so
  switching between OpenAI, Anthropic, Gemini, Groq, Ollama, and others is just
  adding a model in the admin page
- **Everything runs in Docker Compose.** Only the web UI is published, on `127.0.0.1`.

```
 browser ──> nginx (frontend) ──/api──> FastAPI ──> Postgres  (users, chats, usage)
                                           │  └──> Redis     (rate limits, job queue)
                                           │  └──> Qdrant    (vectors)
                                           └──> LiteLLM ──> OpenAI / Anthropic / …
              worker (same image) <── Redis queue: parse → chunk → embed → Qdrant
```

## 1. Prerequisites (Windows)

Docker Desktop on Windows needs WSL 2.

1. Open **PowerShell as Administrator** and run:
   ```powershell
   wsl --install
   ```
   This installs the WSL platform. Your Ubuntu app is already installed. **Restart Windows** when prompted.
2. Check that it works: `wsl -l -v` should list Ubuntu with **VERSION 2**.
3. Install **Docker Desktop** (https://www.docker.com/products/docker-desktop/). Keep
   "Use WSL 2 based engine" ticked. Start Docker Desktop and wait until it says *Engine running*.
4. Check it from PowerShell:
   ```powershell
   docker version
   docker compose version
   ```

## 2. Configure

```powershell
cd rag-app
copy .env.example .env
```

Edit `.env`:

| Setting | What to put |
|---|---|
| `SECRET_KEY` | a long random string: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | your admin login (password ≥ 8 chars) |
| `POSTGRES_PASSWORD` | any strong password |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | your provider keys |

> Anthropic has no embeddings API, so the default **embedding** model is OpenAI's
> `text-embedding-3-small`, which needs the OpenAI key. To embed locally instead, see
> "Local models with Ollama" below.

## 3. Run

```powershell
docker compose up -d --build
```

Open **http://localhost:8080** and sign in with the admin credentials from `.env`.

Useful commands:

```powershell
docker compose ps                 # status
docker compose logs -f backend    # API logs
docker compose logs -f worker     # document processing logs
docker compose down               # stop (data is kept in Docker volumes)
docker compose down -v            # stop AND delete all data
```

After editing `.env`, apply it with `docker compose up -d` (recreates the changed containers).

## 4. Using it

**Chat page:** conversations on the left, streaming chat in the middle, documents on the
right. Upload PDF, Word (.docx), PowerPoint (.pptx), Excel (.xlsx), HTML, Markdown,
text, CSV or JSON files. Each file is parsed, chunked and embedded in the background.
Answers cite the passages they used as `[1]`, `[2]`, …, and you can expand the sources
under every answer. Use the checkboxes to limit which documents a question searches.

**Admin page** (`/admin`, admin only):

- **Users:** create users (the only way to get an account), reset passwords,
  deactivate, delete. Per-user limits:
  - monthly token budget (chat tokens; `0` = unlimited)
  - requests per minute
  - which models they may use
  - max file size, max number of documents, max total storage
- **Models:** add, edit, enable/disable, choose the default chat model and the
  active embedding model, and **Test** the connection.
- **Usage:** tokens and estimated cost per user, per model and per day, for any month.
- **Documents:** every uploaded document, with re-index and delete actions.

Admins can upload documents to the **shared knowledge base** (toggle in the
document panel). Shared documents are searched in every user's chats. Users'
own uploads stay private to them.

## 5. Switching or adding models (LiteLLM)

In **Admin → Models → Add model**, enter a LiteLLM model string:

| Provider | Example | Key in `.env` |
|---|---|---|
| OpenAI | `openai/gpt-5.4-mini` | `OPENAI_API_KEY` |
| Anthropic | `anthropic/claude-sonnet-5-5` | `ANTHROPIC_API_KEY` |
| Google Gemini | `gemini/gemini-2.5-flash` | `GEMINI_API_KEY` |
| Groq | `groq/llama-3.3-70b-versatile` | `GROQ_API_KEY` |
| Ollama (local) | `ollama/llama3.1` + API base `http://host.docker.internal:11434` | none |

Add the key to `.env`, run `docker compose up -d`, add the model, then click **Test**.
Prices are read from LiteLLM's price list unless you enter your own per-1M-token prices.

**Changing the embedding model:** vectors from different embedding models can't be
mixed. When you make another embedding model active, the admin page shows how many
documents need re-indexing and offers a **Re-index now** button.

### Local models with Ollama

Ollama is installed on this machine. To use it from the containers, use API base
`http://host.docker.internal:11434`. For example, after running
`ollama pull nomic-embed-text`, add an embedding model `ollama/nomic-embed-text` and
make it active to embed without any cloud key.

## 6. Development without Docker for the app

```powershell
# infrastructure only, with ports published on localhost
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d postgres redis qdrant

# backend (Python 3.12+)
cd backend
python -m venv .venv; .venv\Scripts\activate
pip install -r requirements-dev.txt
$env:POSTGRES_HOST="localhost"; $env:REDIS_URL="redis://localhost:6379/0"; $env:QDRANT_URL="http://localhost:6333"; $env:UPLOAD_DIR="./data/uploads"
# also set SECRET_KEY, ADMIN_USERNAME, ADMIN_PASSWORD, POSTGRES_PASSWORD, provider keys
uvicorn app.main:app --reload --port 8000
python -m app.worker          # in a second terminal

# frontend
cd frontend
npm install
npm run dev                   # http://localhost:5173, proxies /api to :8000
```

Tests (no Docker needed: SQLite, fake Redis, in-memory Qdrant, mocked LLMs): see
[backend/tests/README.md](backend/tests/README.md).

## 7. Security notes

- The UI is bound to `127.0.0.1` only. Before exposing it on a network, put it behind
  HTTPS and set `COOKIE_SECURE=true`.
- Sessions are httpOnly, SameSite=Strict cookies holding a signed JWT. Changing a
  password or deactivating a user ends their sessions immediately.
- Logins are throttled per username and per IP.
- The admin account is defined only by `.env`. Changing `ADMIN_USERNAME` disables the
  old admin account on the next start.
- Retrieved passages are treated as untrusted data in the prompt, so instructions
  inside documents are not followed. As with any RAG system, this lowers the risk
  but doesn't eliminate it.

## 8. Known limitations

- No OCR: scanned PDFs without a text layer can't be indexed.
- Legacy `.doc`, `.ppt` and `.xls` files must be saved in the modern format first.
- Database tables are created automatically on start. There are no migrations yet
  (add Alembic before changing the schema of a database with real data).
