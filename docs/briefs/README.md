# Build briefs

Detailed, per-area requirements for finishing the app. Read [`../../SPEC.md`](../../SPEC.md)
first; it is the binding contract. Status of every file is in [`../HANDOFF.md`](../HANDOFF.md).

| Brief | Area |
|---|---|
| [backend-auth-admin.md](backend-auth-admin.md) | `bootstrap.py`, `/api/auth/*`, `/api/models`, `/api/admin/*` |
| [backend-ingestion.md](backend-ingestion.md) | parsing, chunking, ingestion, worker, `/api/documents/*` |
| [backend-chat.md](backend-chat.md) | retrieval + prompt, `/api/conversations/*`, `/api/chat/stream` |
| [frontend-chat.md](frontend-chat.md) | chat page: sidebar, streaming chat, composer |
| [frontend-documents.md](frontend-documents.md) | right-hand document panel |
| [frontend-admin.md](frontend-admin.md) | admin page |
| [backend-integration-tests.md](backend-integration-tests.md) | test plan for the backend (no Docker needed) |
| [frontend-verification.md](frontend-verification.md) | typecheck/build + API contract cross-check |
| [review-checklist.md](review-checklist.md) | security and correctness review before calling it done |
| [vision-pdf.md](vision-pdf.md) | vision model for PDFs (revision 2, built; commit `9fb8be6`): scanned pages and image descriptions (backend, admin UI, tests) |
| [document-generation.md](document-generation.md) | files from chat (revision 2, built and verified live on 2026-10-16; commit `70f8076`): the `create_document` tool loop with ordered thinking replay, keepalives and progress, PDF/DOCX/XLSX/MD rendering in a child process, storage and provenance as documents, download endpoint, file cards. Decisions D1-D24 are in its section 0; what is left is in `../HANDOFF.md` |
| [image-generation.md](image-generation.md) | images from chat (revision 2, built, reviewed and verified live on 2026-10-16; commit `7eaf587`): the `create_image` tool in the same loop, image models (kind `image`, OpenAI and Azure GPT image models only, seeding, Test), the image job (slots, deadline, Stop keeps a paid image), validation, AI marker and renditions in the child process, images as private documents, the image and details endpoints, embedding in PDF/DOCX files, the monthly image count (`user_image_limits`, backfill, race-safe reservation), usage kind `image`, image cards and viewer, rollback. Decisions I1-I26 and the review outcomes are in its section 0; the builder split in section 21; what is left is in `../HANDOFF.md` |
| [plugins-gmail.md](plugins-gmail.md) | plugins, with Gmail as the first plugin (revision 2, built, reviewed and verified in Docker against a fake Google on 2026-10-16; **uncommitted; never run against real Google**): the plugin framework (`app/plugins/`: the `Plugin` interface, registry, admin switches, OAuth 2.0 with state + PKCE, encrypted tokens and key rotation, refresh and reconnect states, the request check, imports, audit), the Gmail plugin (REST client, MIME parsing and building, six chat tools in the shared loop, email sources, import, drafts, pending sends confirmed by a click), the Connections dialog, Gmail browser, cards and the admin Plugins tab, nginx and log redaction, the fake Google. Decisions P1-P28 and the review responses are in its section 0; the builder split in section 21; the owner's setup guide is [`../gmail-setup.md`](../gmail-setup.md); what is left is in `../HANDOFF.md` |
| [ui-shell-settings.md](ui-shell-settings.md) | top bar, Settings and the warm editorial redesign (revision 2, designed and reviewed; **not built yet**): the ChatGPT-style top bar (model menu, Plugins menu with every plugin's status, Documents toggle, avatar menu), the composer's "+" menu (attach files, create an image or a document, Gmail shortcuts), the Settings dialog (General, Account, Usage, Plugins & data) with account-level preferences (`user_preferences`), the theme engine (System/Light/Dark, accents, chat font size, no flash under the CSP), sign-in history (`login_events`), log out other sessions, `GET /me/usage`, the data export, delete all chats, the plugin catalog, and the four-builder frontend split. Decisions U1-U32 and the review responses are in its section 0; the split in section 17 |
| [ui-visual-design.md](ui-visual-design.md) | the visual spec for `ui-shell-settings.md` (revision 2): "Paper & Clay", the warm editorial design chosen from three mockups; tokens for light and dark and the five accents (with the contrast audit), the bundled RAG Serif / RAG Sans fonts and their metric-matched fallbacks, layout, every component and screen, motion, mobile rules, and the mockup class map. On looks it wins; on behaviour the functional brief and `SPEC.md` do |

## Rules that apply to every area

- `SPEC.md` is the contract: API paths, JSON field names, SSE event names, data model. If you
  must change it, change `SPEC.md`, `frontend/src/api/types.ts` and the backend schemas together.
- Write production code in the style of the existing foundation files: typed, clear names,
  concise docstrings only where they add information, no comment noise, no dead code, no TODOs.
- Security: authorization on every endpoint; other users' resources return 404 (never reveal they
  exist); validate all client input; never log secrets; the Qdrant search must always carry the
  access filter (`vectorstore.access_filter`).
- Frontend: no `alert`/`confirm`/`prompt`; use inline confirmation or `<Modal>`. Plain CSS only,
  with each area's own class prefix (`chat-`, `docs-`, `admin-`) and only the CSS variables from
  `global.css`, so light and dark themes both work.

## Backend foundation (already written; build on it)

Under `backend/app/`: `config.py` (Settings), `db.py` (async engine, `SessionLocal`, `Base`,
`get_db`, `init_db`), `models.py` (ORM), `schemas.py` (shared response models: `Me`,
`UserLimits`, `UsageSummary`, `DocumentItem`, `Source`), `security.py` (argon2 + JWT),
`deps.py` (`get_current_user`, `get_current_admin`, session cookie helpers), `redis_client.py`
(`get_redis`, `INGEST_QUEUE`), `main.py` (router wiring), and `services/`: `llm.py` (LiteLLM:
`stream_chat`, `embed_texts`, `test_model`, `compute_cost`, `LLMError`, `Usage`), `usage.py`
(rate limit, budget, `record_usage`, `usage_summary`, `user_limits`, `month_bounds`),
`vectorstore.py` (Qdrant), `model_registry.py` (model lookups), `storage.py` (upload paths,
`document_item`, `delete_document_completely`). Later features added services of their own, described in their
briefs: `pdf_images.py`, `pdf_render_child.py` and `vision.py` (vision), `document_tool.py` plus the `filegen/`
package (files from chat), and `image_tool.py` plus `filegen/images.py` (images from chat). Plugins live in their own
package, `app/plugins/` (framework) with `app/plugins/gmail/` (the Gmail plugin), plus `schemas_plugins.py` and the
routers `plugins.py` and `admin_plugins.py`; `plugins-gmail.md` section 4.2 has the interface a plugin implements.

Pinned libs: FastAPI 0.142, SQLAlchemy 2.0 async (asyncpg in prod, aiosqlite in tests, so
keep queries portable, with no Postgres-only SQL), pydantic v2, redis-py asyncio, qdrant-client 1.19
(`AsyncQdrantClient`, `query_points`), litellm 1.103. All DB access is async. Relationship
loading: `User.allowed_models` and `Document.owner` are `selectin`; `Conversation.messages` is
`noload` (query messages explicitly).

## Frontend foundation (already written; build on it)

Under `frontend/`: `src/App.tsx` (routes `/login`, `/`, `/c/:conversationId`, `/admin`),
`src/api/types.ts` (all API types; import from here and don't redeclare them), `src/api/client.ts`
(`api.get/post/patch/del`, `postSSE`, `ApiError`, `errorMessageFromBody`, `notifyUnauthorized`),
`src/auth/AuthContext.tsx` (`useAuth`: `user`, `refresh`, `logout`), `src/styles/global.css`
(tokens + primitives: `.btn .btn-primary .btn-ghost .btn-danger .btn-sm .btn-icon .input .select
.textarea .field .field-label .field-hint .switch .card .badge .badge-{neutral,info,success,warning,danger}
.table .table-wrap .spinner .muted .error-text .stack .row .spacer .sr-only`), `src/components/ui/Modal.tsx`,
`src/components/ui/Spinner.tsx`, `src/lib/format.ts`, `src/pages/LoginPage.tsx`.

Stack: React 19.3, react-router-dom 7, react-markdown 10 + remark-gfm 4, TypeScript 5.9 (strict,
`noUnusedLocals/Parameters`), Vite 8, plain CSS. Don't add other npm packages without a good
reason. The UI should look polished and modern, with empty, loading and error states. It must be
accessible (labels, aria, keyboard) and responsive.
