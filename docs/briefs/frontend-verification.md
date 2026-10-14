# Brief: frontend verification

Goal: the frontend type-checks, builds, and matches the backend exactly.

## Build

```bash
cd frontend
npm install          # creates package-lock.json; commit it (the Docker build runs `npm ci`)
npx tsc --noEmit
npx vite build
```

Fix until both pass with zero errors.

## Contract cross-check

Find every `api.get/post/patch/del`, `postSSE` and XHR call in `src/` and check path, method,
request body fields and response fields against `SPEC.md` **and** the real backend
(`backend/app/routers/*.py`, `backend/app/schemas.py`). Fix whichever side is wrong; keep
`SPEC.md` and `src/api/types.ts` in sync.

## Integration review

- `ChatPage` ↔ `DocumentPanel` props and `DocumentSelection` semantics (null = all,
  array = only these, `[]` = off).
- No CSS prefix collisions or duplicate global rules; the 3-column layout collapses correctly
  (sidebar drawer under 1100px; documents slide-over under 800px); the document panel fills its
  column with internal scrolling; the page itself never scrolls.
- Streaming chat: every SSE event handled; Stop (abort) keeps partial text; navigating to
  `/c/:id` after the first message doesn't lose the in-flight stream; error banners; retry;
  citation chips; auto-scroll with "jump to latest".
- Admin: every endpoint wired; optimistic toggles roll back on error; server validation errors
  shown inline.
- Remove dead code and unused files.

## Manual smoke test (after `docker compose up -d --build`)

Log in as admin → Admin → Models → **Test** each model → create a user → log in as that user in
a private window → upload a PDF and a DOCX → wait for **Ready** → ask a question → check
streaming, citations, the sources list, and the usage meter → Stop mid-answer → reload (partial
answer kept) → admin Usage tab shows the tokens.
