# Review checklist (before calling the app done)

Run an adversarial review of the finished code along these dimensions. For each finding,
confirm it with a concrete failure scenario before fixing it.

## Security

- AuthZ on every route; IDOR on documents, conversations, messages; admin-only routes.
- Session: httpOnly + SameSite=Strict cookie, `token_version` invalidation (password change,
  deactivate), JWT verification options, expiry.
- Login throttling can't be bypassed (username case, IP from `X-Forwarded-For`: nginx sets it to
  `$remote_addr` and uvicorn trusts the proxy headers, and only nginx can reach the backend).
- Uploads: extension allow-list, size enforced while streaming, filename sanitization, stored
  under random names only, zip-bomb guard, extracted-text cap, XML parsing hardened (defusedxml).
- Retrieval: every Qdrant query uses the access filter; `document_ids` from the client can't widen access.
- Prompt injection: retrieved passages are delimited and treated as untrusted data.
- `api_key_env` can only name `*_API_KEY` variables; `api_base` is an admin-only setting.
- No secrets in logs or error messages (LiteLLM errors are mapped by `llm.friendly_error`).
- nginx: security headers, CSP compatible with the built app, `client_max_body_size` (1 MB except
  `POST /api/documents`, which checks the session before the body reaches the backend), cross-site
  writes refused (`Sec-Fetch-Site`).
- Supply chain: Python locks with hashes, npm lock, and base images pinned by digest, all at least
  two weeks old.

## Correctness and robustness

- Streaming: partial answers persisted exactly once on disconnect/error; usage recorded;
  `X-Accel-Buffering: no`; nginx `proxy_buffering off`.
- Ingestion: duplicate queue entries harmless; document deleted mid-processing leaves no vectors;
  embedding-model switch → stale detection → re-index; worker survives Redis restarts.
- Postgres specifics not covered by SQLite tests: timezone-aware datetimes, `func.date` on
  `timestamptz`, the advisory lock in `init_db`, booleans.
- LiteLLM against real providers: Anthropic streaming usage with
  `stream_options.include_usage`; Claude 5.5 models reject a custom `temperature` (leave null);
  embeddings response shape; thinking/reasoning deltas are ignored (only `delta.content` is shown).

## Requirements (from the user's original request)

Docker Compose; FastAPI + Postgres + Redis; React + Vite; Qdrant; LiteLLM for model and
embedding abstraction (easy provider switching); username/password login; admin page for users,
models (enable/disable) and per-user usage controls; users created only by the admin; admin
credentials from `.env`; single tenant; streaming chat; multiple document formats (PDF, Word,
HTML, …); document upload section on the **right**, chat covering the left and middle.
