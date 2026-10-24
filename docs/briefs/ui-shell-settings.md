# Brief: Top bar, Settings, and the warm editorial redesign

> Requirements for the ChatGPT-style top bar (model menu, Plugins menu, Documents toggle, avatar menu), the composer's
> "+" menu, a Settings dialog (General, Account, Usage, Plugins & data) backed by account-level preferences, a sign-in
> history, "log out of other sessions", a usage page, a data export, "delete all my chats", a plugin catalog that shows
> every plugin with its real status, and a visual redesign of every screen. `SPEC.md` is the binding contract (paths,
> JSON fields, data model); if this brief and `SPEC.md` disagree, `SPEC.md` wins, so fix the brief. The contract
> changes are **already applied** in `SPEC.md`, `frontend/src/api/types.ts`, `backend/app/schemas.py`
> (`Preferences`, `Me.preferences`), `backend/app/schemas_account.py` (new), `backend/app/schemas_plugins.py`
> (`PluginInfo.configured`) and `backend/app/routers/models.py` (`ChatModel.supports_tools`), shapes only: the new
> fields carry schema defaults until the backend builder fills them (section 15.2 lists the 10 existing tests that
> expect the old shapes). General rules: [`README.md`](README.md). **Revision 2** (2026-10-18): the review's
> responses are in section 0.2.
>
> This brief is the **functional** design: behaviour, states, data, contract and the work split. How things look
> (palette values, typefaces, spacing, shadows, the exact composition of each screen) comes from
> [`ui-visual-design.md`](ui-visual-design.md), called **the visual spec** below. Where the visual spec and this brief
> disagree on behaviour, accessibility or a contract, this brief wins; on looks, the visual spec wins.

The user asked for:

1. A **top bar like ChatGPT's** over the chat column: the model picker on the left; on the right a **Plugins** menu
   (every registered plugin with its real status, even when it isn't set up), the **Documents** panel toggle, and a
   **profile avatar** opening a menu (username and role, Settings, Admin for admins, Log out). The composer gets a
   **"+" menu**: attach files, create an image, create a document, Gmail shortcuts.
2. A **Settings** section: theme (Light / Dark / System), accent colour, chat font size, default chat model,
   Enter-to-send; account details (sign-ins, password, sign out other sessions); usage against the limits; plugins and
   data controls (export, delete all chats). Preferences are saved **in the account**.
3. A more attractive UI: the **"warm & editorial" (Claude.ai-like)** style, across every existing screen.

## 0. Decisions

| # | Decision | Why |
|---|---|---|
| U1 | **Preferences live in a new table, `user_preferences`** (`user_id` primary key, a JSON `data` object, `updated_at`), never in `users` columns. `Me` carries them (`Me.preferences`), so the boot request (`GET /auth/me`) and the login response bring them; `GET/PATCH /me/preferences` read and change them. Values: `theme` (`system`\|`light`\|`dark`, default `system`), `accent` (`clay`\|`ocean`\|`sage`\|`plum`\|`slate`, default `clay`), `chat_font_size` (`small`\|`default`\|`large`), `enter_to_send` (default true), `default_model_id` (null = the server's default). Read leniently (a bad stored value is its default), written strictly (422) | No Alembic (CLAUDE.md, every earlier feature). JSON keeps a later preference a code-only change. One request at boot instead of two |
| U2 | **The theme is applied before the first paint by a tiny classic script**, `frontend/public/theme-boot.js`, loaded blocking in `<head>` (`<script src="/theme-boot.js"></script>`, not a module). It reads the cached preferences from `localStorage["rag.preferences"]` and sets `data-theme` (the *resolved* `light`/`dark`), `data-theme-preference`, `data-accent` and `data-chat-font` on `<html>`. The main bundle then owns the engine (section 6): it re-applies, follows `prefers-color-scheme` live while the preference is System, and reconciles with the server's values when `Me` arrives | The CSP is `script-src 'self'` (no inline script, no hash maintenance in `nginx.conf`); a same-origin classic script is allowed and runs before the body renders. A module script runs after parsing, which can paint one frame in the wrong theme |
| U3 | **CSS never reads `prefers-color-scheme`.** `global.css` defines the light palette on `:root` and the dark one on `:root[data-theme="dark"]`; accents are `:root[data-accent="…"]` (with a dark variant each); the chat font size is `:root[data-chat-font="…"]`. The engine is the only resolver of System | One place decides the theme, so Light on a dark OS (and the reverse) can't half-apply. Today only `global.css` has a dark media block, and every feature CSS uses the tokens, so this is a contained change |
| U4 | **Settings is a dialog over the current page, addressed by a query parameter**: `?settings=general|account|usage|plugins` on any signed-in route (`/`, `/c/:id`, `/admin`). `/settings` and `/settings/:tab` redirect to `/?settings=<tab>`. Opening pushes a history entry (Back closes it), switching tabs replaces it. Phone widths (under 800 px) get a full-screen sheet | Deep-linkable and shareable, and it keeps the conversation underneath. A `/settings` page would lose the chat context |
| U5 | **The OAuth return opens Settings → Plugins & data.** The callback still redirects to `/?plugin=<id>&oauth=<result>` (unchanged contract); the client turns that into `?settings=plugins` with the result as a banner. **Connections moves into Settings** (the account menu and its dialog go away) | The redirect URIs registered at Google don't change; one place for plugin state |
| U6 | **`GET /plugins` lists every registered plugin** with a new `configured` flag. The client derives one status, in order: not configured → **Not set up**; not `enabled` → **Turned off by the admin**; connection `reconnect_needed` → **Reconnect needed**; connection → **Connected**; else **Not connected** (offer Connect). Admins get "Set up in Admin → Plugins" (`/admin#plugins`) on Not set up. In the first two states a user who still has a connection can open the card and **Disconnect** it (the server allows that today). Nothing else changes in chat: tools are offered only when the plugin is enabled and configured and the user is connected with "Use in chat" on | The user's rule: plugins must be visible even when not configured. A derived status needs one new field, not a second source of truth. Removing a connection must never depend on the admin's settings |
| U7 | **The model in the top bar is per chat; the account default is what new chats start with.** On load and on **New chat** the picker shows `default_model_id` when it is one of the user's models, else the server default. Picking a model applies to the current chat (and the following ones in this tab until New chat); it doesn't change the default. The picker marks the default and links to Settings → General. The old per-device `localStorage["rag.modelId"]` memory is removed | Two clear meanings instead of two competing memories. A user who usually wants Haiku but sometimes Opus can say so |
| U8 | **"+" menu items insert a starter prefix** into the composer (never send): "Create an image: ", "Create a PDF document: ", "Create a Word document: ", "Create an Excel spreadsheet: ", "Create a Markdown file: ", "Search my Gmail: ", "Draft an email: ". The prefix goes before the current draft (not duplicated; it replaces another starter), the caret goes to the end. Attach files and Import from Gmail open UI instead | Prefixes read well with or without a draft and match what the tools and the request check expect from the user's own words |
| U9 | **Items are hidden when the feature doesn't exist for the user, disabled with the reason when it is temporarily or personally unavailable.** Hidden: images when no model can make them, files when no model can make them, Gmail when it isn't set up or is turned off. Disabled (focusable, `aria-disabled`, reason as visible secondary text): the selected model can't, the monthly image limit is used up, the document quota is full, Gmail is off in chat, the model can't use tools. Actionable: "Connect Gmail…", "Reconnect Gmail…" open the plugin's card in Settings | Users shouldn't be shown dead features, but should learn why something they can normally do isn't available now |
| U10 | **Attach files uploads to My documents** (private), through the existing upload queue; chips above the input show each file's progress, then its indexing status. **Send waits while an attached file is uploading or indexing** (reason in the hint; removing the chip sends without it, the document stays). When the message is sent, attached documents are added to an explicit selection (`document_ids`); with "all documents" (null) they are already included | Asking "summarize this" before the file is indexed gets an answer that ignores it, which reads as a bug. The chat request's contract doesn't change |
| U11 | **Sign-in history**: a new table `login_events` (user, time, approximate IP, browser, OS, device type), written on each successful `POST /auth/login`, at most **20 per user** (older rows deleted in the same transaction), deleted with the user, shown only to the user (Settings → Account) and in their export. The raw User-Agent is **not stored**, only what is parsed from it; the IP is stored **approximately** (IPv4 last part hidden: `203.0.113.x`; IPv6 as its /48 network). `LoginEvent.ip_private` (derived on read, no column) says the address isn't a public one, and the UI then shows "Via a local network or proxy" instead of a bare `192.168.65.x` | Enough to answer "was that me?", with the least personal data. Behind Docker and the quick tunnel every sign-in comes from Docker's gateway, and a bare private address reads as "someone on my network". The admin doesn't get a view of other users' sign-ins (not asked for) |
| U12 | **A session id in the JWT** (`sid` = the `login_events` id of the sign-in). Re-issued cookies (change password, log out other sessions) keep it. The history marks the event of the calling session `current` ("This device"). Tokens from before this feature have no `sid` (nothing marked) | Without it the history can't say which entry is this browser |
| U13 | **"Log out of all other sessions"** = `POST /auth/logout-others`: bump `token_version` under the same row lock as change-password and re-issue the caller's cookie with the session's original expiry (no password is asked, so a stolen token can't be renewed through it). Every other cookie and Bearer token stops working; tabs of the same browser share the cookie and stay signed in. Plugin connections are unaffected | `token_version` already ends every session; this exposes it without a password change |
| U14 | **`GET /me/usage`** returns the current UTC month: chat tokens, images and vision tokens each with their limit, embedding tokens, chat answers, the requests-per-minute limit, estimated cost per kind, documents and storage with their limits, a **daily series** (every UTC day from the 1st through today, zeros included), per-model totals, and `resets_at` (the 1st of next month, 00:00 UTC). The chart is CSS/SVG, no library | The user's list. Filling zero days on the server keeps the chart trivial |
| U15 | **`POST /me/export` returns a ZIP** with `README.txt`, `account.json`, `documents.json` (metadata of documents the user **owns**, no file bytes), and per conversation a Markdown transcript and a JSON file (plugin cards with their current state), plus `conversations/index.json`. Bounded: `EXPORT_MAX_MB` (100) of uncompressed content and `EXPORT_TIMEOUT_SECONDS` (**80**, at most 90); conversations go newest first and the export says how many were left out when a bound is hit. One export per user at a time (409), two building in the process (503); both are freed in `finally`, and a client that disconnects ends the build. Built in a spooled temp file, then streamed with `Content-Length`. Only the caller's own data, the admin included | A ZIP holds both the readable and the machine form. File bytes would make the export unbounded; every document already has Download. Nothing is sent until the build ends, and the Cloudflare tunnel drops a response that is silent for about 100 s. A POST gets nginx's cross-site check (a same-site page on another localhost port could otherwise start heavy exports) |
| U16 | **`DELETE /conversations`** deletes every conversation of the caller with exactly the semantics of `DELETE /conversations/{id}` (plugin hooks first: Gmail cancels and scrubs pending sends; messages, cards and file links go; documents, generated files and images stay in My documents; usage history stays), **one commit per conversation**, and returns `{deleted}` (the committed ones). The client asks for a typed confirmation (`DELETE`) inline | The user's rule ("documents are kept, as today's conversation deletion semantics"). The single delete rolls back when a plugin hook fails; inside a batch that rollback would undo the other conversations' deletes |
| U17 | **The Documents toggle also works on desktop**: at 800 px and wider it collapses or expands the right-hand column (remembered per device in `localStorage["rag.documentsPanel"]`), under 800 px it opens the slide-over as today. A collapsed panel **stays mounted** (`hidden`) and keeps polling. The layout contract stays: sidebar \| chat \| documents on the right, breakpoints 1100/800, the page never scrolls | A toggle that does nothing on desktop would be confusing; collapsing gives the chat room. Attachment chips read the panel's published list |
| U18 | **The sidebar footer goes.** The avatar menu (top right) has the username, the role, this month's tokens and images as meters (same rules as today's usage meter) with "View usage" (Settings → Usage), Settings, a quick Theme switch, Admin (admins) and Log out. Change password moves to Settings → Account | One account entry point, where ChatGPT and Claude.ai put it |
| U19 | **`ChatModel.supports_tools`** (new): the model gets tools. The "+" menu's Gmail shortcuts use it ("This model can't use Gmail") | `can_create_files` is false when `CHAT_FILE_TOOLS` is off even for a tool-capable model, so it can't stand in |
| U20 | **Only curated accents**, five presets that each pass WCAG AA in both themes (section 11.2). No free colour picker | A picked colour can't be guaranteed readable in both themes |
| U21 | **Fonts are bundled** (woff2, SIL OFL 1.1), downloaded from the family's official upstream repository at a pinned commit or release tag, checked by sha256, with the licence next to them, as `backend/app/fonts` was done. No CDN (CSP `font-src 'self' data:`), no new npm package | The CSP, the no-new-packages rule and supply-chain hygiene |
| U22 | **Modals don't stack.** Anything Settings opens is inline (confirmations, the password form); "Browse Gmail" closes Settings first and asks the chat page to open the Gmail browser (`requestPluginBrowser`) **only once the Settings dialog has unmounted** (10.5) | `Modal`'s focus traps are document-level capture listeners; two open dialogs fight over focus, and the Gmail browser would record a button that is about to disappear as its opener |
| U23 | **Optimistic preferences with revert.** A change applies at once (and to the cache), then `PATCH` runs; one request in flight per tab, later changes merged into the next. A failure reverts to the last value the server confirmed and shows the error next to the control | "Applied instantly, no reload", without lying about what was saved |
| U24 | **Backend rollback is safe without SQL**: two new tables and a JWT claim; older code ignores both | Unlike images and plugins, nothing new lands in existing columns |
| U25 | **A 401 is verified before the app signs out.** The unauthorized handler asks `GET /auth/me` once (concurrent 401s share that one check): 401 → signed out; 200 → stay signed in with the fresh `Me`; anything else → stay (the failed request reports its own error) | After "Log out others" or a password change, requests this browser sent with the old cookie before the new one arrived get 401s (the documents poll, late usage refreshes, other tabs). Without the check one click signs the user out of the tab that asked |
| U26 | **Settings survives the chat page's own URL changes.** ChatPage replaces its URL (a new chat gets `/c/<id>`; a missing or deleted chat goes to `/`) through `useReplacePath` (FOUNDATION), which keeps `?settings` and the navigation state but drops `pushed` when the path changes, so closing then replaces instead of going back to an entry with the old path | Opening Settings while the first answer of a new chat streams (to change the theme) is natural; a plain `replace` closed the dialog and broke Back |
| U27 | **The top bar compacts by the chat column's width, not the viewport** (`container-type: inline-size` on `.chat-main`, container queries): labels and title go by the space the bar actually has; the model button is the one item that shrinks (ellipsis) | The column is narrowest just above each breakpoint (460 px at 800, 480 px at 1100), where viewport rules overflowed |
| U28 | **Menus render in a portal on `<body>`** (panel and, on phones, the sheet's scrim), positioned from the trigger; **item actions run synchronously in the activating click or keydown handler**, after focus returns to the trigger | No ancestor's stacking context, transform or `backdrop-filter` can trap a sheet or its scrim. Safari opens a file picker (`input.click()`) only inside the user gesture |
| U29 | **Rich menus keep `role="menu"`**; content that isn't an item is folded into names and descriptions: the avatar menu is labelled "Account: <username>, <role>", the usage meters' text describes "View usage", the theme row and the "+" menu's format chips are labelled `group`s, the Plugins menu's subtitle describes the menu | In the menu pattern only items, groups and separators are reachable; one keyboard model for the three top-bar menus |
| U30 | **Per-user client state is cleared on every user change** (login, logout, a confirmed 401): the plugin store and any queued plugin-browser request (which also expires after 5 s) | With Settings openable on `/admin`, the next user on a shared browser could briefly see the previous user's Gmail address, or get their queued Gmail browser |
| U31 | **Fonts swap without layout shift**: metric-matched local fallback faces ("RAG Serif Fallback" on Georgia, "RAG Sans Fallback" on Arial, with `size-adjust` and the ascent, descent and line-gap overrides) come second in the font stacks (visual spec 4.1) | Answer prose, bubbles, sidebar rows and display titles re-wrap when the web fonts arrive; a long thread opened at the bottom would jump |
| U32 | **Preference sync never flickers**: storage events and `Me` reconciliation update only fields without a pending change in this tab, and applying unchanged values is a no-op (no attribute change, no switching flag, no cache write) | A stale tab or a `refresh()` after each answer otherwise re-applies and re-caches, toggling transitions for nothing |

### 0.1 Rejected alternatives

- **A `/settings` page route.** Loses the conversation underneath; the query parameter gives deep links anyway (U4).
- **An inline `<script>` with a CSP hash, or `'unsafe-inline'`.** Fragile (every edit changes the hash in `nginx.conf`)
  or weakens the CSP; the public script does the same job (U2).
- **Applying the cached theme from the main bundle only.** The module script is deferred; a frame in the wrong theme is
  possible on slow devices (U2).
- **Typed preference columns.** Each new preference would need `ALTER TABLE` without Alembic (U1).
- **Storing the full IP and the raw User-Agent.** More personal data than "was that me?" needs (U11).
- **An "active sessions" list with per-session revoke.** Sessions are stateless JWTs; per-session revoke needs a session
  table checked on every request. `token_version` covers "sign everything else out" (U13).
- **Per-conversation model memory.** Messages store `model_name`, but restoring it on open adds surprises (a disabled
  model, a restricted user); out of scope (U7).
- **The top-bar pick becomes the default.** Makes the Settings default a mere "last used" (U7).
- **Export with document files.** Unbounded size and time; Download exists per document (U15).
- **A user-side "delete my account".** Users are created and deleted only by the admin (CLAUDE.md).

### 0.2 Review of revision 1: responses

The review raised 25 points (12 medium, 13 low). All 25 are addressed. Where a point offered alternatives the brief
chose one, and one fix (point 21) is declined in part; 0.3 records the options not taken and why.

| # | Point (severity) | Response | Where |
|---|---|---|---|
| 1 | The top bar overflows at 800, 820 and 1100 to 1125 px (medium) | Accepted: container queries on the chat column (U27). Checked on a copy of the reference mock at 14 widths from 390 to 1440 px, with the documents panel shown and collapsed and with a 40-character model name: no element leaves the bar. The original mock overflows at 5 of those widths | 8.1, visual 6.10, 15.4 #12 |
| 2 | Delete all: a failing hook rolls back the batch (medium) | Accepted: one commit per conversation, counted after its commit (U16) | 4.7, 15.1 |
| 3 | A not-set-up plugin can't be disconnected (medium) | Accepted: "Not set up, connected" opens the card, which offers Disconnect (U6) | 8.3, 10.5, SPEC, 15.2 |
| 4 | "Log out others" can sign out the tab that asked (medium) | Accepted: a 401 is verified with `GET /auth/me` first (U25) | 7, 12, 15.4 #6 |
| 5 | The export's time bound exceeds the tunnel's idle limit (medium) | Accepted: 80 s by default, at most 90; lock and semaphore freed in `finally`; a disconnect ends the build (U15) | 2, 4.6, SPEC 3 |
| 6 | ChatPage's URL replaces close Settings (medium) | Accepted: `useReplacePath` (U26) | 5, 12, 17.3, 15.4 #5 |
| 7 | The frozen Modal API can't express Settings (medium) | Accepted: `initialFocus`, `hideHeader` with `labelledBy`, `bare`, and a trap that skips `tabindex="-1"` | 10.1, 12 |
| 8 | Non-item content inside `role="menu"` (medium) | Accepted with option (b), keeping the menu role (U29) | 8.3, 8.5, 9.1, 12 |
| 9 | Fonts shift the layout when they load (medium) | Accepted: metric-matched fallback faces (U31) | 11.3, visual 4.1, appendices B and C |
| 10 | Chart labels scale with the SVG; the chart foot overflows (medium) | Accepted: a viewBox from the measured width, wrapping foot, sized icons | 10.4, visual 6.23, 15.4 #12 |
| 11 | Contrast: success on the hover fill, the dark switch thumb (medium) | Accepted: light `--success` `#39703f`, a dark-ink thumb when on in dark mode; the audit now checks 330 pairs, 0 under the minimum | 11.2, visual 3.5, 3.6, 6.3, 11 |
| 12 | Ownership and interface gaps (medium) | Accepted, all five: `pages/login.css` (R), `FullPageState` (F, used by T), the `format.ts` additions, the re-exports in `ConnectionsModal.tsx`, `PasswordInput` (F) | 12, 17.2 |
| 13 | Export cards are exported as stored (low) | Accepted: hydrated as `GET /conversations/{id}` shows them, plus one transcript line per card | 4.6, 15.1 |
| 14 | A GET export can be triggered by same-site pages (low) | Accepted: `POST /me/export` | 4.6, 12, 14, SPEC |
| 15 | Plugin state leaks to the next user (low) | Accepted (U30) | 7, 12 |
| 16 | The theme boot leaves the metas and costs a round trip (low) | Accepted: the boot script sets the `color-scheme` and `theme-color` metas; nginx caches `/theme-boot.js` for 5 minutes | 6.1, 17.2 |
| 17 | The file picker outside the gesture; `saveBlob` revokes too early (low) | Accepted: synchronous menu actions (U28); revoke after 60 s | 9.3, 12 |
| 18 | Menus aren't portalled (low) | Accepted (U28) | 12, visual 5 and 6.9 |
| 19 | A collapsed documents panel must stay mounted (low) | Accepted (U17), plus a reload request for a chip without a snapshot after 10 s | 8.4, 9.3 |
| 20 | "Browse Gmail" races the Settings close (low) | Accepted: the request waits for the unmount; the focus fallback is the "+" button (U22) | 10.5 |
| 21 | The dark accents are close to danger (low) | Partly: meters at 90% and 100% get an icon and words; the hue shift is declined (0.3) | 8.5, visual 6.6 |
| 22 | Inconsistencies: short model names, font size copy, `field-sizing`, the header link, target sizes (low) | Accepted: no short names (ellipsis); the chat font size also sizes the user's bubble; JS autosize stays the baseline; the header links the visual spec; the target note is corrected | 8.2, 10.2, visual 4.3, 6.10, 6.14, 10 |
| 23 | Settings accessibility details (low) | Accepted: the live readout only for keyboard moves, the chart as a labelled `group`, "Saved" visual only, skeletons at once | 10.1, 10.2, 10.4, 13 |
| 24 | Every sign-in shows the gateway IP (low) | Accepted: `LoginEvent.ip_private` and "Via a local network or proxy"; open point 1 stays | 4.2, 4.4, 10.3, SPEC |
| 25 | Preference sync can flicker between tabs (low) | Accepted (U32) | 6.2, 6.3, 7 |

### 0.3 Review suggestions not taken, and why

- **Shifting dark `--danger` toward a cooler rose and requiring an accent-vs-danger ΔE of 30 or more** (point 21).
  Red sits between Clay (orange) and Plum (magenta): moving it away from one accent moves it toward the other, and it
  still has to read as red. The lowest distances (ΔE 23 to 27, Clay in both themes and dark Plum) stay. What carries
  the meaning is words and icons (WCAG 1.4.1): destructive buttons name their action ("Delete 48 chats"), and
  meters at 90% and 100% now say "Almost used up" and "Limit reached" with an icon. The audit prints ΔE for
  information only.
- **`font-display: optional` for the sans** (point 9, the alternative). On a cold load through the tunnel it would keep
  the fallback face for the whole first visit; the metric-matched fallback faces give the same stability with the
  intended face.
- **Streaming the ZIP while it is built** (point 5, implied by "nothing is sent before the build"). It would lose
  `Content-Length` and the `X-Export-*` counts, and an error midway would leave a truncated file that the browser saves
  as complete. 80 s is ample for text: deflating 100 MB of transcripts takes seconds.
- **Releasing the per-user export lock before the download** (point 5). A client could then hold many spooled files
  at once. The lock stays over the download. A stalled reader holds it only until nginx's client `send_timeout`
  (60 s) closes the connection, which cancels the response and frees the lock.
- **Pausing the documents poll around "Log out others", or ignoring 401s of requests started before it** (point 4,
  optional). The session check of U25 covers every source of a stale 401 (uploads, polls, other tabs) without
  tracking when each request started.
- **A non-modal dialog for the avatar popover** (point 8, option a). It would give the three top-bar menus two
  keyboard models; option (b) keeps one and still announces the username, role and usage.
- **Storing an "is private" flag in `login_events`** (point 24). It is derivable from the stored approximation (every
  private, loopback, link-local and shared range is a prefix of /24 or shorter for IPv4 and /48 or shorter for IPv6,
  except `::1`, which also reads as not public), so no column is added.
- **A short model name on phones** (point 22, the heuristic). Display names are the admin's; the button ellipsizes,
  and the full name stays in its accessible name.
- **A SAVEPOINT per plugin hook in "Delete all"** (point 2, the alternative). SAVEPOINTs need extra event wiring with
  the SQLite driver the tests use; one commit per conversation gives the same isolation on both engines.

## 1. Flows at a glance

```
boot:     theme-boot.js (cache → <html data-theme …>) → main.tsx engine → GET /auth/me → Me.preferences
          → apply + cache (reconcile) → routes
settings: avatar → Settings  ⇒  push ?settings=general  ⇒  SettingsHost renders the dialog over the page
          change theme → apply + cache now → PATCH /me/preferences (queued) → confirm | revert + error
oauth:    Google → /api/plugins/gmail/oauth/callback → 303 /?plugin=gmail&oauth=connected
          ⇒ SettingsHost: replace with ?settings=plugins, banner "Gmail is connected as …"
plugins:  top bar Plugins ▾ → per plugin: status line → opens its card in Settings → Plugins & data
          (admin + Not set up → /admin#plugins) · "Manage plugins…"
composer: + ▾ → Attach files (picker → upload queue → chips → Send waits until indexed)
              | Create image / PDF / Word / Excel / Markdown (prefix inserted) | Gmail: search, draft, import…
account:  Settings → Account → GET /me/account (sign-ins, created) · change password · POST /auth/logout-others
usage:    Settings → Usage → GET /me/usage (limits, daily series, by model, resets_at)
data:     Settings → Plugins & data → POST /me/export (ZIP download) · DELETE /conversations (typed confirmation)
```

## 2. Settings (`config.py`, `.env.example`)

| Setting | Default | Range | What it does |
|---|---|---|---|
| `EXPORT_MAX_MB` | 100 | 1 to 1000 | uncompressed content of one data export; conversations beyond it (oldest first) are left out and the export says how many |
| `EXPORT_TIMEOUT_SECONDS` | 80 | 10 to 90 | time from the request's start (the wait for a build slot included) to the end of the build; conversations not reached are left out the same way. Nothing is sent before the build ends, and the Cloudflare quick tunnel drops a response that sends nothing for about 100 s (HANDOFF, "Keepalive"), hence the cap of 90 |

`.env.example` gets both, commented out, in a new "Account" block with one line each (the timeout's line mentions the
tunnel). Values outside the ranges stop the backend at startup, like the other ranged settings. The sign-in history
keeps 20 rows per user (a constant, `LOGIN_HISTORY_LIMIT` in the backend module, not a setting).

## 3. Data model (`models.py`; B declares them)

New tables only, created by `create_all`; no column on an existing table, so no Alembic and no rollback SQL (U24).

```python
class UserPreference(Base):
    """A user's own UI settings (docs/briefs/ui-shell-settings.md, U1). No row: every default."""
    __tablename__ = "user_preferences"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)  # Preferences fields; unknown keys are kept
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class LoginEvent(Base):
    """One successful sign-in (U11). The newest LOGIN_HISTORY_LIMIT per user are kept."""
    __tablename__ = "login_events"
    __table_args__ = (Index("ix_login_events_user_created", "user_id", "created_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)       # approximate (4.2)
    browser: Mapped[str | None] = mapped_column(String(64), nullable=True)
    os: Mapped[str | None] = mapped_column(String(32), nullable=True)
    device: Mapped[str] = mapped_column(String(16), default="unknown")      # desktop | mobile | tablet | unknown
```

`users.last_login_at` stays the source of "last sign-in" for users who signed in before `login_events` existed.

## 4. Backend (builder B)

A new router `app/routers/me.py` (`router = APIRouter(prefix="/me", tags=["me"])`, mounted in `main.py` with the
others), a new service `app/services/account.py` (User-Agent parsing, IP approximation, preferences read/write, the
usage report), `app/services/export.py` (the ZIP), plus changes to `routers/auth.py`, `security.py`, `deps.py`,
`routers/conversations.py`, `routers/plugins.py`, `routers/models.py`, `routers/admin.py`, `config.py`, a few plugin
strings, and tests. Shapes are in `schemas.py` and `schemas_account.py` (already written; report contract bugs instead
of editing them).

### 4.1 Preferences

- **Read** (`account.read_preferences(db, user) -> Preferences`): the row's `data`, each field taken when it is a
  valid value and its default otherwise (a corrupted row never fails `/auth/me`). `default_model_id` is returned only
  when it names a chat model that is enabled and that the user may use now (`kind == "chat"`, `enabled`, and
  `restrict_models` false or the id in `user.allowed_models`, which is already loaded); otherwise null. The stored
  value stays, so a model that comes back (re-enabled, allowed again) is the default again. One primary-key select,
  plus one for the model only when an id is stored: `/auth/me` also serves nginx's upload `auth_request`.
- **`Me.preferences`** in `build_me` (login, `GET /auth/me`).
- **`GET /me/preferences`** → `Preferences`.
- **`PATCH /me/preferences`** `PreferencesUpdate` → `Preferences` (the full result):
  - omitted fields stay; `default_model_id: null` clears it; null elsewhere, unknown fields and bad values → 422
    (pydantic, already in the schema);
  - `default_model_id` must pass the read rule above, else 422 `{"detail": "That model isn't available to you."}`;
  - the row is read `FOR UPDATE` (Postgres) and the patch merged into `data` (`{**stored, **patch}`: unknown keys a
    newer version wrote are kept); no row → insert; a racing first insert (IntegrityError) rolls back and retries once;
  - the admin has preferences like anyone.
- No rate limit (each call is one small row write); 401 without a session.

### 4.2 Sign-in history and the session id

- **On a successful `POST /auth/login`**, in the transaction that sets `last_login_at`: insert a `LoginEvent` from
  `request.client.host` (uvicorn runs with `--proxy-headers`, so this is nginx's `$remote_addr`) and the
  `User-Agent` header, then delete the user's rows beyond the newest 20
  (`DELETE … WHERE user_id = :u AND id NOT IN (SELECT id … ORDER BY created_at DESC, id DESC LIMIT 20)`; portable). The
  cookie's token gets `sid = event.id`. Failed or refused logins record nothing (an attacker mustn't be able to write
  into a victim's history).
- **`account.approximate_ip(host: str | None) -> str | None`** (`ipaddress`): IPv4 → `a.b.c.x`; an IPv4-mapped IPv6
  address → its IPv4 form, masked the same way; IPv6 → `str(ip_network(f"{ip}/48", strict=False))` (e.g.
  `2001:db8:85a3::/48`); anything unparsable (`"unknown"`, `"testclient"`) → null. Private and loopback addresses are
  masked the same way (Docker's gateway shows as `192.168.65.x`; behind the quick tunnel and Docker's port publishing
  every client has that address until `set_real_ip_from` is configured, HANDOFF next step 5, and the UI says
  "approximate, as seen by the server").
- **`account.ip_is_private(stored: str | None) -> bool`**, for `LoginEvent.ip_private` (computed on read; no column):
  `None` → false; `a.b.c.x` → the address `a.b.c.0`; an IPv6 network → its network address; then
  `not ipaddress.ip_address(...).is_global`. Every non-public range (private, loopback, link-local, shared
  `100.64.0.0/10`, and the documentation ranges, which Python's IANA tables also treat as not global) is a prefix of
  /24 or shorter (IPv4) or /48 or shorter (IPv6), except `::1`, whose /48 (`::/48`) is not global either, so the
  masked value decides it. Unparsable stored values → false. Tests therefore use a global address (for example
  `81.2.69.x`) as their public example, not `203.0.113.x`.
- **`account.parse_user_agent(ua: str | None) -> tuple[browser, os, device]`**: input cut to 512 characters; output
  printable ASCII, at most 64 characters each. Browser, first match wins: `Edg/` → Edge; `OPR/` or `Opera` → Opera;
  `SamsungBrowser/` → Samsung Internet; `Firefox/` or `FxiOS/` → Firefox; `HeadlessChrome/` → Headless Chrome;
  `CriOS/` or `Chrome/` → Chrome; `Version/…Safari/` → Safari; `curl/` → curl; `python-httpx`, `python-requests`,
  `aiohttp` → Python; else null. Each with its major version when present ("Chrome 141"). OS: `Windows NT` →
  Windows; `iPhone` → iOS; `iPad` → iPadOS; `Mac OS X`/`Macintosh` → macOS; `Android` → Android; `CrOS` → ChromeOS;
  `Linux` → Linux; else null. Device: iPad, or Android without `Mobile` → tablet; iPhone, `Mobile` → mobile; Windows,
  macOS, Linux, ChromeOS → desktop; else unknown. (iPadOS Safari reports itself as a Mac; it shows as macOS desktop.
  Accepted.)
- **The `sid` claim** (`security.create_access_token(user_id, token_version, session_id: int | None, expires_at:
  datetime | None)`): `deps.get_current_user` keeps the decoded `sid` on `request.state.session_id` (absent: None) and
  the token's `exp` on `request.state.session_expires_at` (`deps.current_session_expiry`);
  `deps.set_session_cookie(response, user, session_id, expires_at)` writes them (`expires_at` None = a fresh lifetime;
  given, the token keeps it, never later than a fresh one, and `Max-Age` is the time left). `POST /auth/change-password`
  and `POST /auth/logout-others` re-issue with the caller's `sid`; logout-others also keeps the caller's expiry
  (change-password checks the password, so it issues a fresh lifetime like a login). `decode_access_token` keeps
  requiring only `sub` and `exp`, so tokens without `sid` stay valid.

### 4.3 Log out of other sessions

`POST /auth/logout-others` (no body) → 204. Same steps as change-password without the password: lock and re-read the
user row (`with_for_update(key_share=True)`, `populate_existing`), 401 when the session ended meanwhile (inactive, or
`token_version` changed), `token_version += 1`, commit, re-issue the cookie with the same `sid` and the same expiry
(the session is not extended: without a password, renewing would let a stolen token live forever). Allowed for the env
admin (their password is in `.env`, but their sessions are theirs). It doesn't count toward the login throttle. A
caller authenticated by a Bearer header gets the new cookie and its own Bearer token stops working too (documented in
SPEC). Plugin connections are untouched.

### 4.4 Account: `GET /me/account` → `AccountInfo`

`password_managed_by_env` = `user.is_admin` (the only admin is the env admin; change-password answers 400 for it).
`conversation_count` = `COUNT(*)` of the user's conversations. `recent_logins`: the user's `login_events`, newest
first, at most 20, `current` = `event.id == request.state.session_id`, `ip_private` from 4.2. Never anyone else's;
the admin API doesn't change.

### 4.5 My usage: `GET /me/usage` → `MyUsage`

For the current UTC calendar month (`usage.month_bounds(None)`), the caller's `usage_events` only:

- `chat_tokens.used` = sum of `total_tokens` of `chat` events (what `check_budget` counts); `limit` = 0 for the admin,
  else `monthly_token_limit`.
- `images.used` = count of `image` events (as `images_this_month`); `limit` = `monthly_image_limit(db, user)`.
- `vision_tokens.used` = sum of `vision` events; `limit` = 0 for the admin, else `VISION_MONTHLY_TOKEN_LIMIT`.
- `embedding_tokens` = sum of `embedding` events. `requests` = count of `chat` events.
- `cost_usd` per kind and `total`, rounded to 6 decimals like `usage_summary`.
- `rate_limit_per_min` and `documents` from `user_limits(user)` and `storage.owned_totals` (exactly what
  `GET /documents/config` reports, the admin's limits as 0).
- `daily`: grouped by `func.date(UsageEvent.created_at)` (UTC on both engines, HANDOFF "Database"), then one entry for
  every date from the 1st through today (UTC), zeros included, in date order.
- `by_model`: grouped by `model_name` and `kind`; `model_name` replaced by the display name of an `llm_models` row
  whose `litellm_model` matches (the first, by id) when one exists; sorted by cost, then tokens, descending. `tokens`
  is prompt + completion; `requests` is the event count.
- `period_start`, `resets_at`: the month's bounds. December rolls over to January.
- The month's start and "today" come from one injectable clock (`usage.month_start(now)` already takes `now`), so
  tests can pin the date.

### 4.6 Export: `POST /me/export`

No body (FastAPI declares none; the client sends an empty JSON object like every `api.post`). A POST, so nginx's
cross-site check (`$xsite_write`) refuses it from same-site pages on other localhost ports.

- **Authorization**: the caller's own rows only: `conversations.user_id`, `documents.owner_id`,
  `plugin_connections.user_id`, `login_events.user_id`, their preferences. Shared documents of others, other users'
  anything and server settings never appear, for the admin too.
- **Concurrency**: one export per user at a time (a per-user `asyncio.Lock`, held from the start of the build to the
  end of the download; a second request → 409 "An export is already being prepared. Wait for it to finish."); at
  most 2 exports **building** in the process (`asyncio.Semaphore(2)`, held only while building, waiting up to 5 s,
  then 503 "Exports are busy right now. Try again in a minute."). One backend process (HANDOFF), like the plugin
  locks. Both are released in `finally` blocks on every path: success, an error, `CancelledError`, and a client that
  went away. The builder checks `await request.is_disconnected()` before each page of conversations and before the
  response starts, and stops (closing the temp file) when it is true. After the build, the response owns the lock and
  the temp file: a small `StreamingResponse` subclass whose `__call__` wraps `super().__call__` in `try/finally`, so
  both are released even when the body generator never starts (a disconnect right before streaming; an unstarted
  generator's own `finally` never runs) or is cancelled midway. A stalled reader is cut by nginx's client
  `send_timeout` (60 s), which cancels the response the same way.
- **Build** into `tempfile.SpooledTemporaryFile(max_size=16 MiB)` with `zipfile.ZipFile(..., ZIP_DEFLATED,
  compresslevel=6)`; entry writes run in `asyncio.to_thread`. Conversations are read newest first
  (`updated_at DESC, id DESC`) in pages of 50, with their messages, files (`message_files`) and plugin cards
  **with their current state**: `registry.hydrate_cards(db, user, items)` (4.8), the same code `GET /conversations/{id}`
  uses, so a proposed email that was sent since reads `sent`; when a plugin isn't registered any more or its
  hydration raises (logged without content), the stored `data` is exported instead. Before writing a conversation the
  builder checks both bounds: the content written so far plus this conversation over `EXPORT_MAX_MB`, or
  `EXPORT_TIMEOUT_SECONDS` passed since the request started (one injectable monotonic clock) → stop; the rest is
  "omitted" (`truncated: "size"` or `"time"`). The account and documents files are always written (they are small:
  20 sign-ins and at most the user's document quota of rows), so the first byte leaves a few seconds after the
  deadline at most.
- **Layout** (all names ASCII; `<slug>` = the title lowercased, ASCII letters, digits and dashes, at most 40
  characters, `conversation` when empty; `<id>` zero-padded to 6 digits):

  ```
  README.txt                         what is inside, when, how many conversations, what was left out and why, and
                                     that document files are downloaded from the documents panel
  account.json                       {format: "rag-assistant-export", version: 1, exported_at, username, role,
                                      created_at, last_login_at, preferences, limits (UserLimits),
                                      usage_this_month (UsageSummary), plugin_connections: [{plugin_id, plugin_name,
                                      account_email, status, chat_enabled, connected_at}], recent_logins (LoginEvent
                                      without "current"), conversations: {included, omitted, truncated: null|"size"|"time"}}
  documents.json                     [{id, filename, scope, status, error, size_bytes, content_type, num_pages,
                                       num_chunks, created_at, updated_at, generated, imported_from,
                                       image: {width, height, alt_text, quality, prompt, revised_prompt, model_name}|null}]
  conversations/index.json           [{id, title, created_at, updated_at, messages: <count>, markdown, json}]
  conversations/<id>-<slug>.md       the transcript (below)
  conversations/<id>-<slug>.json     {id, title, created_at, updated_at, messages: [{id, role, content, created_at,
                                      model_name, prompt_tokens, completion_tokens, error, sources (as stored),
                                      files: [{document_id, filename, format, size_bytes}], plugin_cards (PluginCard,
                                      hydrated as above; card_id "p<n>")}]}
  ```

  Transcript: `# <title>`, a line with the created/updated times (UTC, ISO) and the id, then per message
  `## You · <time>` or `## Assistant (<model_name>) · <time>`, the content as written, then for answers
  "Sources:" (passages: `n. filename, page p`; emails: `n. Email: "subject" from sender (date)`), "Files:" (name,
  format, size), "Plugins:" (one line per card, below) and `*Error: …*` when set. Titles have newlines collapsed.
  Card lines (`export._card_line(card)`, by `type`; the union is in `schemas_plugins`; addresses as `address`, never
  display names alone; times UTC ISO):
  - `gmail_send` with a `pending` send: `Email <pending.status>: to <to, cc> · "<subject>"` plus
    ` · sent <sent_at>` when sent (`pending`, `sending`, `sent`, `failed`, `cancelled`, `expired`, `replaced`; a
    stale `sending` reads `failed`, as the card shows it); without one: `Email proposal <card.status>` plus
    `: <error>` when set;
  - `gmail_draft`: `Gmail draft <status>: to <to> · "<subject>"`;
  - `gmail_import`: `Imported from Gmail: <n> items (<imported> imported, <already> already there, <failed> failed)`;
  - `gmail_activity`: `Gmail <action>` plus ` for "<query>"` and ` · <count> found` when set;
  - any other type: `<plugin_id> card (<type>)`.
- **Never in the archive**: password hashes, `token_version`, OAuth tokens (encrypted or not), the client secret,
  `SECRET_KEY`, anything of another user. A test scans the archive bytes for them (15.1).
- **Response** after the build: `200` `application/zip` (the `StreamingResponse` subclass above), streamed from the
  temp file in 64 KiB chunks (reads in `asyncio.to_thread`) with `Content-Length`; `Content-Disposition: attachment; filename="rag-assistant-export-<username>-<YYYY-MM-DD>.zip"`
  (usernames are `[a-z0-9_.-]`); `Cache-Control: no-store`; `X-Content-Type-Options: nosniff`;
  `X-Export-Conversations: <included>`; `X-Export-Omitted: <omitted>`. Nothing of the content is logged.
- nginx needs no change for it (`location /api/` doesn't buffer; the cross-site map already covers `POST`).

### 4.7 Delete all chats: `DELETE /conversations` → `ConversationsDeleted`

Move the body of `delete_conversation` into one helper (`delete_conversation_completely(db, user, conversation)`:
plugin hooks with their rollback-and-continue, the explicit deletes, the row, **and the commit**) used by both routes.
Because each call ends in its own commit, a hook's `db.rollback()` can only undo the current conversation's
uncommitted changes, never another conversation's deletion. The new route reads the caller's conversation ids in id
order (one select), then for each id loads the row (skipping one that is already gone) and calls the helper, adding 1
to the count after its commit. A conversation whose own commit fails (a database error) stops the loop with the error
(500); the conversations before it stay deleted, and a retry deletes the rest. One commit per conversation is cheap
for a few hundred conversations. Idempotent: a second call returns `{deleted: 0}`. A conversation created while it
runs may survive (it didn't exist when the call started); an answer streaming into a deleted conversation is already
handled (`chat.py`, "deleted while the answer streamed"). Documents, generated files and images, their `generated_*`
rows (`generated_images.conversation_id` becomes null by its foreign key), imported documents and usage events stay.
FastAPI matches `DELETE /conversations` and `DELETE /conversations/{id}` separately; nginx's cross-site check covers
`DELETE`.

### 4.8 Plugin catalog and wording

- `registry.hydrate_cards(db, user, items) -> dict[int, PluginCard]` (new): the per-plugin loop now inside
  `plugin_cards` in `routers/conversations.py` (group by plugin, each registered plugin's `hydrate_cards`, log and
  skip a plugin that raises, map item id → card by `card_id` "p<item id>") moves here unchanged; `plugin_cards`
  keeps its signature and behaviour and calls it. The export (4.6) calls it with its own items, and exports the stored
  data for an item without an entry.
- `GET /plugins`: every plugin of `registry.all_plugins()`, in order, with `configured = not registry.configured(plugin)`
  and everything else as today (`enabled` is still the admin switch **and** configured; the connection is still only
  the caller's). Delete the `if not access.enabled and row is None: continue` filter. A test registry without plugins
  still lists nothing.
- No other plugin behaviour changes: connect still answers 409 when not configured and 403 when off;
  `registry.plan_chat` still skips plugins that aren't enabled (no note for them, so users of an unconfigured app pay no
  prompt tokens).
- User-facing text that points at the old account menu changes to Settings (the tests reference the constants, not the
  wording):
  - `plugins/gmail/tools.py` `NOTE_CONNECT`: "… tell them they can connect it from the Plugins menu at the top of the
    chat, or in Settings → Plugins & data."; `_NOTE_RECONNECT`: "… tell them to reconnect Gmail in Settings → Plugins &
    data; never claim to have read or sent email."
  - `plugins/registry.py` (capability not granted): "… Grant it in Settings → Plugins & data."
  - `plugins/connections.py` `_GONE`: "This connection was removed. Connect again in Settings → Plugins & data."
  - `plugins/gmail/sends.py` (two places): "… Reconnect it in Settings → Plugins & data."

### 4.9 Models

`routers/models.py`: `supports_tools=tools` (the value `llm.supports_tools(model)` already computed in the loop).

### 4.10 User deletion and rollback

`admin.delete_user` adds explicit deletes of `UserPreference` and `LoginEvent` rows next to the others. Rolling the
code back leaves two unused tables and a `sid` claim older code ignores (U24): no SQL.

## 5. Frontend structure and the URL contract

New and changed modules (owners in section 17):

```
frontend/index.html                    color-scheme and theme-color metas, then <script src="/theme-boot.js"></script>
frontend/public/theme-boot.js          classic script, ~30 lines (section 6.1)
frontend/nginx.conf                    one new location: /theme-boot.js cached for 5 minutes (6.1)
src/main.tsx                           startAppearanceEngine() before createRoot(); fonts.css import
src/theme/appearance.ts                the engine (section 6)
src/styles/global.css                  tokens (light, dark, accents, chat font), primitives (old and new)
src/styles/fonts.css, src/assets/fonts/  @font-face, woff2 files, licences
src/auth/AuthContext.tsx               preferences + updatePreferences (section 7)
src/api/client.ts                      helpers for every new endpoint (section 12)
src/lib/settingsLink.ts                SettingsTab, useOpenSettings, useReplacePath, settings URL helpers (10.1)
src/lib/appEvents.ts                   conversations deleted; the signed-in user changed
src/lib/format.ts                      new date, month and reset formatters (section 12)
src/components/ui/                     Modal (sheet variant, own header, bare body, initial focus), Menu (portalled),
                                       Avatar, SegmentedControl, Switch, Meter, PasswordInput, FullPageState, icons
src/pages/login.css                    the login page (prefix login-)
src/components/settings/               SettingsHost, SettingsModal, the four tabs, settings.css (prefix settings-)
src/components/chat/                   TopBar, ModelMenu, PluginsMenu, AvatarMenu, PlusMenu, AttachmentChips, …
src/components/connections/            ConnectionsPanel (extracted from ConnectionsModal), pluginStore additions
```

**Routes** (`App.tsx`): `/login`, `/`, `/c/:conversationId`, `/admin` as today; `/settings` → `<Navigate
to="/?settings=general" replace>`, `/settings/:tab` → `/?settings=<tab>` (unknown tab: `general`). `SettingsHost` is
rendered once inside `AuthProvider` next to `<Routes>`, and renders the dialog when a user is signed in and the
location has `settings=<tab>`.

**Query parameters** (any signed-in route):

| Parameter | Meaning |
|---|---|
| `settings=general\|account\|usage\|plugins` | the dialog is open on that tab (`data` is accepted as `plugins`; anything else: `general`) |
| `plugin=<id>&oauth=<result>` (from the backend's callback, on `/`) | consumed once by `SettingsHost`: replaced (`replace`) with `?settings=plugins`, with the result passed as navigation state, so a reload doesn't repeat the banner |

**Navigation state** (`location.state.settings`, set by `useOpenSettings`): `{ pushed?: true, focusPluginId?,
closedPluginId?, oauthReturn? }`. `pushed` says the dialog was opened by a push (closing goes `navigate(-1)`); without
it (a deep link, a reload, the OAuth return, or a path that changed underneath) closing replaces the URL without the
parameter. Banners and focus targets travel in state, never in the URL.

**The chat page's own URL changes keep Settings open (U26).** Every `navigate(…, {replace: true})` in ChatPage
(the new conversation's `/c/<id>`, `showMissing`, `onDeleted`, the invalid-route redirect, the reaction to
`onConversationsDeleted`) goes through `useReplacePath()(path)`: it keeps `settings=<tab>` (and no other parameter)
and `location.state.settings`, reading the location at call time (a ref, because the stream's callbacks outlive
renders), and removes `pushed` when `path` differs from the current path. History then has one extra entry below the
conversation (the `/` it started from), which Back reaches after Settings is closed; accepted.

## 6. Theme engine (`src/theme/appearance.ts`, FOUNDATION)

### 6.1 Before the first paint: `public/theme-boot.js`

`index.html`'s `<head>` starts with the charset and viewport metas, then `<meta name="color-scheme" content="light">`
(was `light dark`) and `<meta name="theme-color" content="#faf8f3">`, then the script, so it can find and set both
metas before anything paints:

```js
// Applies the cached appearance before the first paint (index.html loads it blocking, after the color-scheme and
// theme-color metas). Keep the allowed values in sync with src/theme/appearance.ts and SPEC.md section 6
// (Preferences), and the two colours with --bg in src/styles/global.css.
(function () {
  var root = document.documentElement, p = null;
  try { p = JSON.parse(localStorage.getItem("rag.preferences") || "null"); } catch (e) { p = null; }
  function pick(value, allowed, fallback) { return allowed.indexOf(value) >= 0 ? value : fallback; }
  function meta(name, content) {
    var el = document.querySelector('meta[name="' + name + '"]');
    if (el) el.setAttribute("content", content);
  }
  p = p && typeof p === "object" ? p : {};
  var theme = pick(p.theme, ["system", "light", "dark"], "system");
  var dark = theme === "dark" ||
    (theme === "system" && !!window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
  root.setAttribute("data-theme", dark ? "dark" : "light");
  root.setAttribute("data-theme-preference", theme);
  root.setAttribute("data-accent", pick(p.accent, ["clay", "ocean", "sage", "plum", "slate"], "clay"));
  root.setAttribute("data-chat-font", pick(p.chat_font_size, ["small", "default", "large"], "default"));
  meta("color-scheme", dark ? "dark" : "light");
  meta("theme-color", dark ? "#262421" : "#faf8f3");
})();
```

Allow-lists with `indexOf`, never object lookups (`"constructor"` would pass one). Vite copies `public/` as is and
doesn't warn about a public classic script.

**Caching.** nginx serves `/` with `no-cache`, so a blocking script there would cost a revalidation round trip on every
load (100 to 300 ms through the tunnel) before the first paint. `nginx.conf` gets one location for it (F):

```nginx
location = /theme-boot.js {
    try_files $uri =404;
    add_header Cache-Control "public, max-age=300" always;
    add_header X-Content-Type-Options "nosniff" always;
}
```

After a deploy that changes it, a browser may run the previous version for up to 5 minutes; harmless, because the
bundle re-applies the appearance as soon as it runs.

### 6.2 The engine

```ts
export const PREFERENCES_KEY = "rag.preferences";
export const DEFAULT_PREFERENCES: Preferences;   // system, clay, default, true, null
export const ACCENTS: readonly { id: AccentColor; label: string }[];  // Clay, Ocean, Sage, Plum, Slate (in this order)
export const CHAT_FONT_SIZES: readonly { id: ChatFontSize; label: string }[];  // Small, Default, Large
export function readCachedPreferences(): Preferences;       // validated field by field; defaults when absent/unreadable
/** Writes only when the value differs from what is stored; try/catch: private mode and quotas are fine. */
export function cachePreferences(preferences: Preferences): void;
/** Sets the four attributes and both metas; a no-op when the resolved theme, preference, accent and font are unchanged. */
export function applyAppearance(preferences: Preferences): void;
export function resolvedTheme(): "light" | "dark";
/** Starts the System listener and the storage listener; `onExternalChange` gets another tab's cached preferences. */
export function startAppearanceEngine(onExternalChange: (preferences: Preferences) => void): void;
```

- **Apply** sets `data-theme` to the resolved theme, `data-theme-preference`, `data-accent`, `data-chat-font`, the
  `color-scheme` meta (`light` or `dark`) and the `content` of `<meta name="theme-color">` to the resolved `--bg`. The
  engine remembers what it applied last: when nothing would change it returns at once (no attribute writes, no
  switching flag). A real switch adds `data-theme-switching` to `<html>` for two animation frames; `global.css` turns
  transitions off under it, so the swap doesn't animate every border.
- **System, live**: while the preference is `system`, a `matchMedia("(prefers-color-scheme: dark)")` `change` listener
  re-applies the last preferences. Light and Dark ignore the OS.
- **Other tabs**: a `storage` event on `rag.preferences` doesn't apply anything itself; it calls `onExternalChange`
  with the validated value, and AuthContext merges it (section 7: fields with a pending change in this tab keep their
  pending value) and applies the result.
- **Reduced motion**: the engine doesn't animate anything; CSS keeps the global `prefers-reduced-motion` rule.
- **Signed out** (login page, boot): the cached values (or the defaults) apply. An explicit logout keeps the cache: the
  next person on this browser sees the same theme until they sign in (harmless, and the login page doesn't flash).

### 6.3 Reconciliation

When `Me` arrives (boot, login, every `refresh()`, a confirmed session check), AuthContext merges `me.preferences`
into what it holds: fields without a pending change take the server's value, fields with one keep the pending value.
Then it applies and caches the result, both of which do nothing when the values didn't change (6.2). So a theme changed
on another device shows on the next load or refresh, once, without a reload, and a `refresh()` after each answer
costs nothing.

## 7. Preferences in the client (`AuthContext`, FOUNDATION)

```ts
type AuthContextValue = {
  // … existing fields …
  /** The signed-in user's preferences (optimistic while a save is pending); the cached ones while signed out. */
  preferences: Preferences;
  /**
   * Applies at once (state, <html>, cache), then PATCH /me/preferences. One request in flight per tab: changes made
   * meanwhile are merged into the next request. Resolves with the server's result; rejects with ApiError after
   * reverting to the last confirmed preferences (re-applied and cached).
   */
  updatePreferences: (update: PreferencesUpdate) => Promise<Preferences>;
};
```

- Confirmed state = the last `Me.preferences` or PATCH response. Pending = the fields of the request in flight plus the
  queued changes. Optimistic state = confirmed with the pending fields on top.
- A `Me` (from `refresh()`, boot or a session check) and another tab's cached value (`onExternalChange`, 6.2) update
  only the fields that aren't pending; the rest of `Me` updates as usual (U32).
- A failed save reverts the fields of that request to their confirmed values, except fields changed again since
  (those go in the next request). A 401 during a save follows the session check below. A 422 (a default model withdrawn
  meanwhile) reverts with the server's message.
- `logout`, a confirmed sign-out and a new login drop pending patches.

**The session check (U25).** `setUnauthorizedHandler` gets a handler that verifies before signing out:

- the first 401 starts one check, `getSessionUser()` (section 12: `GET /auth/me` that doesn't notify); further 401s
  while it runs join it;
- it resolves `null` (a 401) → signed out, exactly as today's handler; a `Me` → stay signed in, with that `Me` (and
  6.3's merge); an error (network, 5xx) → stay signed in, because the request that got the 401 shows its own error and
  the next request checks again;
- `request()` no longer notifies for `/auth/me` or `/auth/login`. `refresh()` (also used by the boot) checks through
  `getSessionUser()` itself: it waits for a cookie re-issue in flight, and in a signed-in tab it re-checks a 401 once
  before signing out, so a late usage refresh can't sign out the tab that asked;
- an upload that gets a 401 fails with its own message ("Your session changed during the upload. Upload the file
  again."); the rest of the queue goes on. The queue is cleared only on a confirmed sign-out (U30).

**The user changed (U30).** An effect on `user?.id` (including to and from null) calls `announceUserChanged(id)`
(`lib/appEvents.ts`). `pluginStore` subscribes at module load: it clears the list (`clearPlugins`, aborting a load in
flight) and drops a queued plugin-browser request. `uploadManager` subscribes the same way: signed out → it cancels
and forgets every upload; another user → it drops the previous user's. Other per-user module stores may do the same.

## 8. The top bar (SHELL)

### 8.1 Layout

The bar sits over the chat column (`chat-main`), one row: [Conversations, while the sidebar is a drawer, under 1100 px
viewport] · Model menu · conversation title · spacer · Plugins · Documents toggle · Avatar.

**It compacts by the chat column's own width (U27)**, because the column is narrowest just above each breakpoint (460 px
at 800, 480 px at 1100 with the documents panel open), not below it. `.chat-main` is a size container
(`container-type: inline-size; container-name: chat`) and the bar's rules are container queries:

| Chat column width | Plugins and Documents | Title |
|---|---|---|
| 720 px and wider | icon and label (Plugins also its chevron) | visible, between the model and the actions (`flex: 1`, ellipsis) |
| 560 to 719 px | icon and label | `sr-only` |
| under 560 px | icon only, 40 × 40 | `sr-only` |

- **The model button is the only item that shrinks**: it and any wrapper around it are `flex: 0 1 auto; min-width: 0`,
  its label ellipsizes, and its accessible name keeps the full model name ("Model: Claude Opus 5.5"). The actions are
  `flex: none`. So no width overflows, whatever the model's display name.
- The viewport breakpoints still decide what they decided before: the Conversations button under 1100 px, the bar's
  height (56 px under 800), and the coarse-pointer sizes.
- Checked on a copy of the reference mock with these rules: no element leaves the bar at 390, 430, 700, 799, 800, 820,
  860, 1000, 1099, 1100, 1110, 1125, 1200 and 1440 px, documents panel shown or collapsed, with a 40-character model
  name too. (Typical widths: 1440 → 820 px column, labels and title; 1100 → 480 px, icons; 1000 → 660 px, labels; 820
  → 480 px, icons; 390 → 390 px, icons.)

The conversation title stays the page's `<h1>`, `sr-only` where the table says so. `document.title` becomes
`<title> · RAG Assistant` on a conversation and "RAG Assistant" on a new chat (restore on unmount).

### 8.2 Model menu

A menu button showing the selected model's display name (as the admin wrote it; never shortened, only ellipsized,
8.1) and a chevron (`aria-haspopup="menu"`, accessible name "Model: <name>"). The menu: models grouped by provider
(group labels when there is more than one provider), each a `menuitemradio` (`aria-checked` on the selected one) with
its name, small tags "Files" (`can_create_files`) and "Images" (`can_create_images`), and a "Default" badge on the
default (section U7). Last, after a separator: "Set the default in Settings…" → Settings → General. States: loading ("Loading models…", disabled), load error ("Default model",
disabled, the error as its description; today's behaviour), no models ("No models available", disabled).

Selection rules (`useModels`): on load and on New chat, `default_model_id` if listed, else the `is_default` model, else
the first; after a reload of the list (a rejected send) a choice that is still listed is kept, else the same rule.
When `default_model_id` changes in Settings while the current thread has no messages, the picker follows it.
`localStorage["rag.modelId"]` is no longer read or written; remove the key once on load.

### 8.3 Plugins menu

A menu button "Plugins" (plug icon; label hidden when the chat column is under 560 px, 8.1; accessible name kept). When any plugin needs the
user's attention (status Reconnect needed), the button shows a small warning dot and its accessible name becomes
"Plugins, 1 needs attention".

Menu contents, from `usePlugins()` (the store already loads and refreshes on focus):

- One item per plugin: its icon, name and a status line. Status from `pluginStatus(plugin)` (section 12) and what
  activating the item does:

  | Status | Status line | Activation |
  |---|---|---|
  | Connected | "Connected · <account_email>" (and "· off in chat" when `chat_enabled` is false) | Settings → Plugins & data, focused on its card |
  | Reconnect needed | "Reconnect needed" (warning tone) | same |
  | Not connected | "Not connected" | same (the card has Connect and, in testing mode, the hint) |
  | Turned off by the admin, connected | "Turned off by the admin" | same (the card offers Disconnect) |
  | Turned off by the admin, not connected | "Turned off by the admin" | none: `aria-disabled`, description "The admin turned <name> off." |
  | Not set up, connected (anyone) | "Not set up · <account_email> still connected" | Settings → Plugins & data, focused on its card (the card offers Disconnect, and for admins the setup link) |
  | Not set up, not connected, user | "Not set up yet" | none: `aria-disabled`, description "The admin hasn't set up <name> yet." |
  | Not set up, not connected, admin | "Not set up · Set up in Admin → Plugins" | a link to `/admin#plugins` |

- A separator, then "Manage plugins…" → Settings → Plugins & data.
- Loading: one non-interactive line "Loading plugins…"; an error: the message and a "Try again" item
  (`reloadPlugins`). An empty catalog (only in tests): "No plugins are available."
- Semantics (U29): the menu is labelled "Plugins" and described (`aria-describedby`) by its subtitle ("Apps the
  assistant can use for you in chat."); the visual head is `aria-hidden`. Each item's accessible name is the plugin's
  name, its description the status line (and the reason when disabled). The loading and empty lines are
  `role="none"` text that the menu's description also carries ("Loading plugins…").

The menu never starts OAuth itself: the card in Settings carries the testing-mode hint, errors and capability grants.

### 8.4 Documents toggle

A button with the documents icon and "Documents" (icon only when the chat column is under 560 px, 8.1), `aria-controls` the panel and
`aria-expanded` its visibility. At 800 px and wider it collapses or expands the right-hand column: the grid's third
track goes to 0, the panel gets `hidden` (out of the tab order and the accessibility tree), and the choice is stored in
`localStorage["rag.documentsPanel"]` (`"hidden"` or absent; try/catch). Under 800 px it opens the slide-over as today
(the stored choice doesn't apply there). The composer's scope chip keeps opening the slide-over under 800 px; at 800
px and wider, while the panel is collapsed, it expands it.

**The collapsed panel stays mounted** (only `hidden`): `DocumentPanel` keeps polling and publishing its list
(`publishDocuments`), which the attachment chips (9.3) and the file cards read through `useDocumentSnapshot`. Never
unmount it to collapse it.

### 8.5 Avatar menu

A round button with the user's initials (`Avatar`, section 12), accessible name "Account: <username>". The menu:

1. A header (not a menu item): the avatar, the username, the role ("Administrator" for `is_admin`, else "Member").
2. This month's usage, read-only: "Tokens this month" and, by today's rules, "Images this month", as `Meter`s against
   the limits ("Unlimited" when 0; the admin has none), then a "View usage" item → Settings → Usage. The meters update
   with `refresh()` after each answer, as the sidebar meter does today. At 90% and 100% a meter adds an icon and
   "Almost used up" or "Limit reached" (the `Meter` primitive does it), so the state isn't colour alone.
3. "Settings" → Settings → General.
4. "Theme": three `menuitemradio` items System / Light / Dark (icons), calling `updatePreferences({theme})`; the menu
   stays open after choosing one so the change is visible.
5. "Admin" (admins only) → `/admin`.
6. "Log out": today's behaviour (on failure the notice "Couldn't sign out…" shows in the chat).

When tokens or images are at 90% or more of a non-zero limit, the avatar shows a small dot and its accessible name adds
"usage almost at the limit".

**Semantics (U29).** The panel keeps `role="menu"` (one keyboard model for the three top-bar menus), and what isn't an
item is folded into names and descriptions:

- the menu's `aria-label` is "Account: <username>, <role>"; the visual header is `aria-hidden`;
- the usage block is `aria-hidden` inside the menu (its meters are visual there), and "View usage" carries
  `aria-describedby` pointing at a hidden text with the same numbers ("Tokens this month: 412,380 of 1,000,000.
  Images this month: 12 of 30. Almost used up." or "Unlimited");
- the theme row is `role="group"` labelled "Theme" with its three `menuitemradio` items; Up and Down move through every
  item in DOM order (the three included), Left and Right move within the row without wrapping.

### 8.6 Sidebar

Brand, New chat and the conversation list stay; the footer (usage meter and account menu) is removed. "No
conversations yet" and the loading and error states stay (restyled). The drawer behaviour under 1100 px is unchanged.

## 9. Composer and the "+" menu (SHELL)

### 9.1 Items and availability

A round "+" button at the start of the composer bar (`aria-haspopup="menu"`, name "Add files and more"), opening a menu
above it. It loads `GET /documents/config` each time it opens (aborted on close; items that need it show as disabled
"Checking your storage…" until it arrives; a failed load leaves them enabled and the server decides). The selected
model is `selectedModel`; "any model" means any model in the user's list.

| Item (group) | Hidden when | Disabled (reason shown) when | Does |
|---|---|---|---|
| **Attach files…** | never | composer disabled (no models, loading, error); document count at `max_documents` ("You've reached your limit of N documents.") or storage full ("Your storage is full.") | opens a file picker (`multiple`, `accept` = `supported_extensions`); 9.3 |
| **Create an image** (Create) | no model has `can_create_images` | the selected model can't ("<model> can't create images. Pick a model marked Images."); images used up (`usage.images >= limit > 0`, non-admin: "You've used this month's N images. The count starts again on <resets date>."); no document slot or less than 8 MiB of storage left ("Your documents are full.") | prefix "Create an image: " |
| **PDF document**, **Word document**, **Excel spreadsheet**, **Markdown file** (Create) | no model has `can_create_files` | the selected model can't ("<model> can't create files…"); no document slot or less than 256 KiB of storage left | prefixes "Create a PDF document: ", "Create a Word document: ", "Create an Excel spreadsheet: ", "Create a Markdown file: " |
| **Search my email** (Gmail) | Gmail not set up or turned off (status Not set up / Turned off) or not connected / reconnect needed (then the single item below shows instead) or `read` not enabled by the admin | `chat_enabled` off ("Gmail is off in chat. Turn on “Use in chat” in Settings → Plugins & data."); `read` not granted ("Grant Gmail read access in Settings → Plugins & data."); the selected model lacks `supports_tools` ("<model> can't use Gmail. Pick another model.") | prefix "Search my Gmail: " |
| **Draft an email** (Gmail) | as above, or `draft` not enabled by the admin | as above, with `draft` | prefix "Draft an email: " |
| **Import from Gmail…** (Gmail) | as above, or the plugin doesn't support import | `read` not granted | opens the Gmail browser (`GmailImportModal`, as the panel's button does) |
| **Connect Gmail…** / **Reconnect Gmail…** (Gmail) | Gmail connected, not set up or turned off | — | Settings → Plugins & data, focused on Gmail's card |

Disabled items stay focusable (`aria-disabled="true"`, the reason as visible secondary text referenced by
`aria-describedby`); activating them does nothing. A group whose items are all hidden is hidden with its label. While
an answer streams, the menu still works (the textarea stays editable today).

Semantics (U29): "Create" and "Gmail" are `role="group"` with `aria-labelledby` their visual labels. The visual
"Create a document" row isn't an item: it is the label of a nested horizontal group (`role="group"`,
`aria-labelledby` that row's text) holding the four format chips, each a `menuitem` named "PDF document", "Word
document", "Excel spreadsheet", "Markdown file"; a reason that disables all four describes each of them. Up and Down
move through every item in DOM order (the chips one by one); Left and Right move along the chip row without wrapping.

### 9.2 Starters

`ComposerHandle.insertStarter(prefix)` (`withStarter` in `components/chat/starters.ts`): when the trimmed draft
already starts with the prefix (case-insensitive), it only focuses, caret at the end; when it starts with another "+"
menu starter, that starter is replaced (the text after it is kept); otherwise the draft becomes
`prefix + draft.trimStart()`. Focus the textarea, caret at the end, resize. Nothing is sent. The empty state's example
prompts stay as today (restyled); the image example is left out once the month's images are used up, as the "+" menu's
item is then disabled.

### 9.3 Attach files

- **The picker opens inside the gesture.** The "+" menu renders a hidden `<input type="file" multiple>` outside the
  menu panel (it survives the menu closing), and the item's action calls `input.click()` synchronously; the `Menu`
  runs actions synchronously in the activating click or keydown handler (U28, section 12), because Safari ignores
  `click()` on a file input outside a user gesture. Never defer it (no effect, timer or state round trip).
- The picked files go to `uploadManager.enqueue({ownerId, scope: "private", rules: uploadRules(config), files,
  folders: []})`, which returns the queue item ids (section 12). The composer keeps an attachment per id: name, size,
  then the created document's id from `onSettled`.
- Chips above the textarea, one per attachment: file-type tag, name (truncated), and the state: "Uploading 40%"
  (queue progress), "Indexing…" (document `pending`/`processing`, from `useDocumentSnapshot`), "Ready", "Failed: <reason>"
  (upload or indexing error). A chip whose snapshot is still `undefined` 10 s after its upload settled calls
  `requestDocumentsReload()`, and again every 10 s while it stays so (the hook itself asks only once). Each chip has a remove button: while uploading it cancels the upload
  (`uploadManager.remove`); afterwards it only takes the file off this message ("Remove report.pdf from this message;
  it stays in your documents").
- **Send waits** while any chip is uploading or indexing: the send button is disabled and the hint says "Waiting for
  report.pdf to finish indexing…" (or "…uploading…"). Failed chips don't block.
- On send: with an explicit selection (`number[]`, including `[]`), the ready attachments' ids are added to it (the
  panel shows them ticked from then on); with null they're already included. The chips clear when the send is
  accepted; a rejected send restores the draft **and** its chips.
- Uploads also appear in the documents panel's queue (the queue is shared); that's fine.
- Optional: dropping files on the composer does the same (reuse `documents/useFileDrop`; a drop zone that calls
  `preventDefault` is already exempt from `blockStrayFileDrops`).

### 9.4 Enter to send

`preferences.enter_to_send` true (default): Enter sends, Shift+Enter adds a line (today). False: Enter adds a line,
Ctrl+Enter (Cmd+Enter on macOS and iOS, detected from `navigator.userAgent`) sends. Ctrl/Cmd+Enter sends in both modes.
The IME guards stay. The hint reads "Enter to send · Shift+Enter for a new line" or "⌘ Enter to send · Enter for a new
line" (Ctrl on other systems), plus today's files/images sentence.

## 10. Settings (SETTINGS)

### 10.1 The dialog

- `SettingsHost` (mounted once, section 5) reads `?settings`, renders `SettingsModal` for a signed-in user, handles the
  OAuth return, and subscribes to `onConnectionsRequest` (`requestConnections(closedPluginId)` from the Gmail browser
  or the panel now opens Settings → Plugins & data with that banner).
- `SettingsModal` uses `Modal` with `variant="sheet"` (full screen under 800 px), `title="Settings"`, `hideHeader`
  with `labelledBy` = the id of its own "Settings" title in the nav column, `bare` (no padded, scrolling body: each
  panel scrolls on its own), `className="settings-dialog"` for its size, and `initialFocus` = the selected tab's
  button (section 12). It renders its own close button in the panel header (`aria-label="Close settings"`). It has a
  tablist of four tabs: **General**, **Account**, **Usage**, **Plugins & data** (`SETTINGS_TABS`). At 800 px and wider the tabs
  are a vertical list on the left (`aria-orientation="vertical"`); below, a horizontally scrollable strip under the
  title. Arrow keys (Up/Down and Left/Right both work), Home and End move between tabs and select them; the selected
  tab is the only one in the tab order; the panel is `role="tabpanel"` labelled by its tab. Switching tabs replaces the
  URL. Each panel has an `<h2>` (the tab name) and section `<h3>`s.
- Escape and the close button close it (section 5: back when pushed, else replace). Focus returns to the element that
  opened it (Modal). On open, focus goes to the selected tab (`initialFocus`); with `focusPluginId`, the Plugins & data
  panel moves focus to that card's heading once the cards have loaded (after Modal's own focus).
- Tabs activate automatically (arrow keys select). Each panel renders its skeleton in the same render it is selected
  and starts its load then (aborted when another tab is selected; loaded again when shown again), with an inline error
  and "Try again" on failure. Nothing in Settings opens another modal (U22).
- `useOpenSettings()` and `SETTINGS_TABS` come from `lib/settingsLink.ts` (FOUNDATION), so SHELL and RESTYLE open it
  without depending on SETTINGS.

### 10.2 General

| Control | Kind | Behaviour |
|---|---|---|
| Theme | `SegmentedControl` (radio group): System, Light, Dark, with icons | `updatePreferences({theme})`; System follows the OS live; the hint under it says which one System currently resolves to ("Currently dark, from your system") |
| Accent colour | radio group of swatches with names (`ACCENTS`) | `updatePreferences({accent})`; each swatch shows the accent in the current theme; the selected one has a check mark (not colour alone) |
| Chat font size | `SegmentedControl`: Small, Default, Large | `updatePreferences({chat_font_size})`; the hint "Answers and your messages"; a sample line under it in the chat typography. It sizes the answers (`--chat-font-size`) and the user's bubbles (`--chat-bubble-size`, visual spec 4.3) |
| Default chat model | `select`: "Server default (<is_default model's name>)" (value null), then the user's models (`GET /models`, its own fetch) | `updatePreferences({default_model_id})`; the hint: "New chats start with this model. The model menu at the top changes it for one chat." |
| Enter to send | `Switch` | `updatePreferences({enter_to_send})`; the hint describes the other mode (9.4) |

A failed save shows "Couldn't save: <message>" under that control (`role="alert"`) after the revert; the next change
clears it. There is no Save button. The "✓ Saved" mark in the panel header is visual only (`aria-hidden`): each control
already announces its new state, and a theme change isn't announced (13).

### 10.3 Account

- **Profile**: the avatar, username, role badge, "Member since <created_at date>".
- **Sign-in activity** (`GET /me/account`):
  - "This session": the `current` event's browser, OS, device and time ("Chrome 141 on macOS · signed in 2 hours
    ago"), or nothing when the session predates the history.
  - "Recent sign-ins": up to 5 newest, then "Show all (N)" expanding to all 20; each row: device icon, browser on OS,
    the place line, the time (relative, with the exact time in a tooltip and the accessible name), and "This device"
    on the current one. The place line is the approximate IP ("81.2.69.x"), or, when `ip_private`, "Via a local network
    or proxy" with the masked address in its `title` and accessible description, or "Unknown address" when `ip` is
    null. The section note: "Addresses are approximate and are as seen by this server. Behind a proxy or tunnel,
    sign-ins show as coming from a local network." (Configuring nginx's `set_real_ip_from` for the outer proxy, HANDOFF
    next step 5, makes them real client addresses.)
  - No events: "Sign-ins are recorded from now on." plus "Last sign-in: <last_login_at>" when it is set.
- **Password**: the change-password form inline (current, new, confirm, each a `PasswordInput` with its show/hide
  toggle; at least 8 characters; mismatch and length messages as today; `POST /auth/change-password`), then "Your password was changed. Your other sessions were signed
  out." For `password_managed_by_env`: no form, the text "The admin password is set with ADMIN_PASSWORD in the server's
  .env file. Change it there and restart the backend."
- **Sessions**: "Log out of all other sessions" → inline confirmation "Sign out every other browser, device and API
  token? You stay signed in here." with "Log out others" / "Cancel" → `POST /auth/logout-others` → "Other sessions were
  signed out." Errors inline. A "Log out" button for this session (same as the avatar menu's).

### 10.4 Usage

From `GET /me/usage`, with a "Refresh" button:

- The period: "<Month YYYY> (UTC)" and "Limits start again on <resets_at, local date and time> (<n> days)".
- Meters for chat tokens, images and vision tokens against their limits ("Unlimited" for 0; the admin's say
  "Unlimited (admin)"), each with used/limit numbers; a 90% warning tone and a 100% danger tone (same as the meter).
- Facts: chat answers this month, embedding tokens ("not limited"), requests per minute ("20 per minute" or
  "Unlimited"), documents "<count> of <max>", storage "<used> of <max>", largest upload "<max_upload_mb> MB".
- Estimated cost: the total, with chat, embedding, vision and image parts, and the note "An estimate from the models'
  prices; the provider's bill may differ."
- **Daily chart** (`DailyUsageChart`, inline SVG): one bar per day of the month (days after today shown as empty
  slots), the metric picked by a `SegmentedControl` (Chat tokens, Images, Cost). A y axis with 3 or 4 rounded
  gridlines and compact labels, day numbers on the x axis every few days, bars in `--accent`, today marked.
  - **Text never scales with the chart.** The SVG's `viewBox` is `0 0 <width> <height>` from the plot's measured CSS
    size (a `ResizeObserver`; 1 unit = 1 CSS px; the first render waits for the first measurement, with the skeleton
    meanwhile), so the 12 px axis, peak and "Today" labels stay 12 px at every width; bar slots divide the measured
    width. Under 420 px of plot, x labels show the 1st, today and the last day only.
  - **Readout, not tooltip.** A fixed readout line above the plot shows the hovered or keyboard-selected day ("Tue 14
    October · 44,210 chat tokens · 2 images · $0.51"; default: today). It is `aria-hidden` and updates on pointer moves
    and keys. A separate `sr-only` `aria-live="polite"` region gets the same text **only on keyboard moves**, so a
    pointer crossing 31 bars announces nothing.
  - **Semantics.** The plot is a focusable `role="group"` with `aria-roledescription="chart"` and the summary as its
    `aria-label` ("Chat tokens per day, October 2026: 85k in total, most on 14 October with 12.4k"); Left and Right
    move the selected day (one tab stop, not 31). Screen-reader users get the "Show as table" disclosure, a real
    `<table>` of every series per day, as the reading path.
  - **The foot wraps**: the request-rate line, the reset line and "Show as table" are a `flex-wrap: wrap` row with a row
    gap; its icons are sized (15 px), so nothing overflows the card at 390, 800 or 860 px.
  - No animation under `prefers-reduced-motion`. All zeros: "No usage yet this month."
- **By model**: a table (name, kind, tokens, requests, cost), or "Nothing used yet."

### 10.5 Plugins & data

- **Plugins**: `ConnectionsPanel` (section 12) with `oauthReturn`, `closedPluginId` and `focusPluginId` from the
  navigation state, and `onBrowse`. The panel shows every plugin card, including Not set up ("<name> isn't set up yet.
  The admin needs to add its settings to the server.", and for admins a "Set up in Admin → Plugins" link to
  `/admin#plugins`). **Not set up or turned off, with a connection**: the card also shows the account ("<email> is
  still connected") and **Disconnect** (the usual inline confirmation; `DELETE /plugins/{id}/connection` works in both
  states). Connect, Reconnect, Browse and capability grants aren't offered there (the server refuses them: 409 not
  configured, 403 off).
- **Browse Gmail, without two dialogs (U22).** `onBrowse(plugin)` doesn't open anything: `SettingsHost` stores the
  plugin id as a pending browse and closes Settings (on `/admin`, by navigating to `/` with the pending browse kept).
  When the location no longer has `settings` and the dialog has unmounted (an effect in `SettingsHost`, which stays
  mounted), it calls `requestPluginBrowser(id)` once and clears the pending browse. By then the Settings `Modal` has
  returned focus to its opener, which `GmailImportModal` records as its own opener. When the Gmail browser closes and
  that element is gone (focus on `<body>`), ChatPage focuses the composer's "+" button.
- **Export my data**: the text "A ZIP with your conversations (Markdown and JSON), the list of your documents, your
  settings and your sign-in history. Document files aren't included: download them from the documents panel." and an
  "Export" button → `downloadMyData()` (`POST /me/export`; spinner "Preparing your export…", the button disabled) →
  `saveBlob` → "Saved <filename> (<size>)." When `omitted > 0`: a warning "The export holds your newest <n>
  conversations; <m> older ones didn't fit." Errors inline (409, 503 and network errors with their messages).
- **Delete all chats**: "Deletes every conversation (<conversation_count> now, from `GET /me/account`, loaded with the
  tab) and its messages. Your documents stay, including files and images created in chat. Drafts and sent emails stay
  in Gmail." → "Delete all chats…" → inline confirmation with a text field "Type DELETE to confirm" (trimmed,
  case-insensitive) and a danger button enabled only on a match → `deleteAllConversations()` → "Deleted <n>
  conversations." and `announceConversationsDeleted()`. After it the dialog stays open, and closing it replaces the
  URL instead of going back (the conversation underneath may be gone).

## 11. Visual redesign: what the visual spec must respect

### 11.1 Scope

Every existing screen, restyled consistently, with all functionality kept: the chat (top bar, thread, messages with
Markdown, code blocks, tables, citations and sources, file, image and Gmail cards, the composer, the empty state,
banners and notices), the sidebar and its drawer, the documents panel and slide-over (list, upload queue, dropzone,
quota, the image viewer), the Gmail browser and import dialog, the admin page (header, overview tiles, all five tabs,
the user and model dialogs, confirmations), the login page, the full-page states (spinner, "Can't reach the server",
the error boundary), and every modal. The layout contract stays (section U17).

### 11.2 Tokens

`global.css` keeps every existing token name (feature CSS uses them) with new values, and adds at least:
`--font-serif` (display and headings), `--chat-font-size` and `--chat-line-height` (set per `data-chat-font`),
`--overlay` (modal and drawer backdrops; replaces the hard-coded `rgba` in `chat.css`), and motion tokens (durations
and an easing). The visual spec may add more; feature CSS uses only tokens defined in `global.css`.

**Contrast (WCAG 2.2 AA), for both themes and every accent**, checked by FOUNDATION with a scratch script that reads the
token values (report the table):

- `--text`, `--text-2` and `--text-3` (wherever text uses them) on `--bg`, `--surface`, `--surface-2`, `--surface-3`:
  at least 4.5:1;
- `--accent` as text and links on `--bg` and `--surface`: 4.5:1; `--accent-text` on `--accent` and on
  `--accent-hover`: 4.5:1; `--accent` text on `--accent-soft`: 4.5:1;
- each badge and status colour on its soft background: 4.5:1; danger button text on `--danger`: 4.5:1;
- status colours (`--success`, `--warning`, `--danger`, `--info`) as text on `--bg`, `--surface`, `--sidebar`,
  `--surface-2` and `--surface-3` (hover and pressed fills: menu items, rows): 4.5:1;
- `--accent-ink` on every ground including `--surface-3`: 4.5:1;
- focus ring, control borders (`--border-control`, visual spec 3.2) and the switch track against their backgrounds:
  3:1; the switch thumb against the track it sits on, off and on, in both themes and every accent: 3:1 (the thumb's
  position is the state cue, WCAG 1.4.11);
- informational: the CIE76 ΔE between each accent fill and `--danger` (printed, not a gate; 0.3).

The visual spec's `contrast_audit.py` (revision 2) checks 330 pairs, 0 under the minimum.

### 11.3 Behavioural constraints on the look

- Motion is calm and short; every animation and transition is off under `prefers-reduced-motion: reduce` (the global
  rule stays) and during a theme switch (6.2).
- No layout shift when fonts load (U31): `font-display: swap` with metric-matched local fallback faces second in each
  stack (visual spec 4.1 and appendix B), and the top bar and composer heights don't depend on the web font. A thread
  stuck to the bottom stays at the bottom when its height changes (font swap included).
- Fonts: at most 4 woff2 files and about 400 KB together; Latin coverage at least; other scripts fall back to the
  system fonts (users write Hindi and Nepali today).
- Every interactive element keeps a visible focus indicator; targets at least 24×24 px (WCAG 2.5.8; citation chips are
  inline in text, the criterion's inline exception; checkboxes get a 24 px hit area through their label), 40 px for
  the top-bar and composer buttons on touch.
- Plain CSS with the area prefixes: `chat-` (SHELL), `docs-`, `admin-`, `conn-`, `login-` (RESTYLE), `settings-`
  (SETTINGS), `ui-` (new FOUNDATION primitives in `global.css`; the existing unprefixed primitives keep their names).
- Down to 390 px with no horizontal page scrolling; tables scroll inside `.table-wrap`.

## 12. Shared modules (FOUNDATION's interfaces for stage 2)

Frozen once stage 1 ends: stage-2 builders may use these but not change their signatures.

```ts
// src/api/client.ts (additions)
export function getPreferences(signal?: AbortSignal): Promise<Preferences>;          // GET /me/preferences
export function patchPreferences(update: PreferencesUpdate): Promise<Preferences>;   // PATCH /me/preferences
export function getAccount(signal?: AbortSignal): Promise<AccountInfo>;              // GET /me/account
export function getMyUsage(signal?: AbortSignal): Promise<MyUsage>;                  // GET /me/usage
export function changePassword(currentPassword: string, newPassword: string): Promise<void>;
export function logoutOtherSessions(): Promise<void>;                                // POST /auth/logout-others
export function deleteAllConversations(): Promise<ConversationsDeleted>;             // DELETE /conversations
/** POST /me/export with fetch (empty JSON body); ApiError (with the server's detail) on non-2xx. The filename comes from
 *  Content-Disposition (fallback "rag-assistant-export.zip"), the counts from X-Export-Conversations / -Omitted. */
export function downloadMyData(signal?: AbortSignal): Promise<{ blob: Blob; filename: string; conversations: number; omitted: number }>;
/** Object URL + a temporary <a download> click; the URL is revoked 60 s later (an immediate revoke can abort a large
 *  download in Safari and Firefox). */
export function saveBlob(blob: Blob, filename: string): void;
/** GET /auth/me for the session check (U25): the Me, or null on 401, without notifying the unauthorized handler;
 *  rejects with ApiError on any other failure. */
export function getSessionUser(signal?: AbortSignal): Promise<Me | null>;
// request(): a 401 notifies the unauthorized handler except for /auth/login and /auth/me (their callers handle it).

// src/lib/settingsLink.ts
export type SettingsTab = "general" | "account" | "usage" | "plugins";
export const SETTINGS_TABS: readonly { id: SettingsTab; label: string }[];   // General, Account, Usage, Plugins & data
export function parseSettingsTab(value: string | null): SettingsTab | null;  // null when absent; "data" → plugins; unknown → general
export type SettingsNavigation = { pushed?: true; focusPluginId?: string; closedPluginId?: string; oauthReturn?: OAuthReturn };
export function useOpenSettings(): (tab?: SettingsTab, options?: Omit<SettingsNavigation, "pushed">) => void;  // push, keeps path and hash
/** ChatPage's replace (U26, section 5): `(path) => void` keeping ?settings and its state, dropping `pushed` when the
 *  path changes; reads the location at call time. */
export function useReplacePath(): (path: string) => void;

// src/lib/appEvents.ts
export function announceConversationsDeleted(): void;
export function onConversationsDeleted(listener: () => void): () => void;
/** Called by AuthContext when the signed-in user's id changes (null = signed out). U30. */
export function announceUserChanged(userId: number | null): void;
export function onUserChanged(listener: (userId: number | null) => void): () => void;

// src/lib/format.ts (additions; the existing exports stay, formatCompact gives "44.2K", formatRelativeTime "2 hours ago")
export function formatDate(iso: string | null | undefined): string;        // local date: "18 Oct 2026"; "—" when absent
export function formatDayMonth(iso: string): string;                       // local: "1 Nov"
export function formatResetMoment(iso: string): string;                    // local: "Sat 1 November, 01:00"
export function formatUtcDay(date: string, style: "long" | "short"): string; // a "YYYY-MM-DD" UTC day: "Tue 14 October" / "14"
export function formatMonthName(month: string): string;                    // "YYYY-MM" → "October 2026"
export function daysUntil(iso: string, now?: number): number;              // whole days, rounded up, never negative
export function nextUsageReset(month: string): string;                     // "YYYY-MM" → ISO of the next month's 1st, 00:00 UTC

// src/theme/appearance.ts — section 6.2. src/auth/AuthContext.tsx — section 7.

// src/components/ui/
//   Modal: the existing props (open, title, onClose, children, footer?, width?) plus
//     variant?: "dialog" | "sheet"      sheet: full screen under 800 px
//     className?: string                added to the dialog element (sizing by the caller's own prefix)
//     describedBy?: string              aria-describedby of the dialog
//     initialFocus?: RefObject<HTMLElement | null>   focused on open instead of the first form control
//     hideHeader?: boolean              no built-in header (title + close); the caller renders its own close control
//     labelledBy?: string               aria-labelledby (an id inside children); without it the dialog gets aria-label={title}
//     bare?: boolean                    children go straight into the dialog: no padded, scrolling .modal-body
//   The Tab trap and the open focus skip [tabindex="-1"] elements (roving tabs and menus), and open focus runs after
//   the children's effects (so initialFocus wins; a child that moves focus later, after a load, still can).
//   Menu: an accessible menu button (aria-haspopup, roving focus with arrows/Home/End, Escape returns focus, Tab and
//     outside pointerdown close, disabled items focusable with aria-disabled and a description, groups with labels,
//     separators, menuitemradio, link items (react-router), placement bottom-start | bottom-end | top-start, kept
//     inside the viewport, scrolls when tall), plus:
//     - the panel (and under 800 px the bottom sheet with its scrim) renders in a portal on document.body, positioned
//       from the trigger's rect (fixed; updated on resize and scroll; flips when there is no room), aria-controls kept;
//     - an item's action runs synchronously in the activating click or keydown handler, right after focus returns to
//       the trigger (a file input's click() must stay inside the gesture);
//     - groups may be orientation="horizontal": Left/Right move within the group without wrapping, Up/Down move
//       through every item in DOM order;
//     - the menu takes an accessible label and an optional describedBy (U29); non-item content is aria-hidden.
//   Avatar ({ name, size }): initials (first letters of up to two parts split on . _ - or space; else the first
//     letter), uppercase, aria-hidden
//   SegmentedControl ({ label, value, options: {value, label, icon?}[], onChange, disabled? }): role="radiogroup",
//     arrow keys
//   Switch ({ checked, onChange, label, description?, disabled? }) on the existing .switch styles
//   Meter ({ label, used, limit, format, noun }): role="meter" with aria-valuetext; warning at 90% ("Almost used up"
//     with an icon), danger at 100% ("Limit reached" with an icon), in the value row and the valuetext; "Unlimited"
//     when limit is 0 (no bar)
//   PasswordInput (input props + { label? }): .ui-password wrapper, the eye toggle (aria-label "Show password" /
//     "Hide password", aria-pressed), switches type and keeps focus and caret; used by login, Change password and the
//     admin user form
//   FullPageState: FullPageSpinner() and FullPageMessage({ title, children, action? }) (the brand mark, a serif title,
//     text, a primary action); ErrorBoundary uses it, and App.tsx (T) for the boot spinner and "Can't reach the server"
//   icons.tsx: the shared icon set the new shell and settings need (stroke icons, currentColor, aria-hidden)

// src/components/connections/pluginStore.ts (additions)
export type PluginStatus = "not_configured" | "disabled" | "reconnect_needed" | "connected" | "available";
export function pluginStatus(plugin: PluginInfo): PluginStatus;          // the order of U6
export const PLUGIN_STATUS_LABEL: Record<PluginStatus, string>;          // Not set up, Turned off by the admin, Reconnect needed, Connected, Not connected
/** Asks the chat page to open the plugin's browser (Gmail import). Kept until a listener subscribes (once), for at
 *  most 5 s; dropped on a user change (U30). */
export function requestPluginBrowser(pluginId: string): void;
export function onPluginBrowserRequest(listener: (pluginId: string) => void): () => void;
// unusableReason(): "<name> isn't set up yet." first when !configured
// The store clears itself on onUserChanged (U30), besides ChatPage's unmount.

// src/components/connections/ConnectionsPanel.tsx (extracted from ConnectionsModal.tsx)
export type OAuthReturn = { pluginId: string; result: PluginOAuthResult };
export function parseOAuthReturn(search: string): OAuthReturn | null;
export type ConnectionsPanelProps = { oauthReturn: OAuthReturn | null; closedPluginId?: string | null;
  focusPluginId?: string | null; onBrowse: (plugin: PluginInfo) => void };
export default function ConnectionsPanel(props: ConnectionsPanelProps): JSX.Element;  // no Modal; cards use h3
// ConnectionsModal.tsx stays as a thin Modal wrapper with its old props, and re-exports OAuthReturn and
// parseOAuthReturn from ConnectionsPanel (ChatPage imports them from it), until SHELL deletes it.

// src/components/documents/uploadManager.ts (backward-compatible change)
//   enqueue(request): number[]  — the new items' ids, folders first then files, in order
//   onSettled(listener: (created: DocumentItem | null, itemId: number) => void)
```

Also frozen for stage 2 (used across areas today): `components/chat/icons.tsx` exports `AlertIcon`, `CheckIcon`,
`CloseIcon`, `MailIcon`, `DocumentsIcon`, `ExternalLinkIcon`, `PaperclipIcon`, `SearchIcon`;
`components/chat/plugins/email.ts` exports `formatEmailDate`, `importStatusView`, `plural`, `safeHttpsUrl`; from
`components/documents/`: `DocumentPanel` and its props, `documentEvents.ts`, `fileTypes.ts` (`fileTypeTag`,
`uploadRules`, `validateUpload`), `FileTag`, `ImageViewer`, `useFileDrop`, `uploadManager`/`useUploads`;
`components/connections/GmailImportModal` and its props (`{plugin, onClose}`); `components/ui/*`.

## 13. Accessibility

- Menus: the WAI-ARIA menu button pattern (12, Menu); every item has an accessible name; disabled items explain why;
  content that isn't an item is folded into the menu's label and the items' descriptions (U29: "Account: <username>,
  <role>", the usage text on "View usage", the Plugins subtitle), and grouped items (the theme row, the format chips)
  sit in labelled `group`s.
- Settings: a modal dialog with a labelled tablist (10.1); every control labelled; hints linked with
  `aria-describedby`; save errors `role="alert"`; the export and delete progress `role="status"`.
- Segmented controls and swatches are radio groups; selection is never shown by colour alone.
- The chart is a labelled `group` with `aria-roledescription="chart"`, a text summary and a table (10.4); its live
  readout speaks only for keyboard moves; meters have `aria-valuetext`, and their 90% and 100% states have words and
  an icon, not colour alone.
- Focus: opening a menu focuses its first enabled item (or the checked radio); items disabled only while the menu
  loads (the "+" menu's "Checking your storage…") take focus back when they become enabled, until the user moves;
  closing returns focus to the trigger; after a destructive action focus goes to the result message.
- Live regions are polite except errors; a theme change isn't announced, and neither is "✓ Saved" (visual only).
- Settings tabs activate automatically; the selected panel shows its skeleton at once (10.1).
- Keyboard only: every flow in section 15.4 works without a pointer; screen-reader names checked in the browser pass
  (accessibility snapshot).

## 14. Security and privacy

- Every new endpoint requires a session (401) and touches only the caller's rows; there is no id in their paths.
- Preferences are validated on write; `default_model_id` can't name a model the user may not use (422) and is
  re-checked on read.
- Sign-in history: successful sign-ins only; approximate IP; parsed User-Agent fields only; 20 per user; deleted with
  the user; visible only to the user and in their own export; never logged; not in the admin API.
- Logout others: a row lock against change-password and admin resets; 401 for a session that ended meanwhile.
- Export: own data only; no secrets (15.1 scans for them); bounded size, time and concurrency; `no-store`; nothing
  logged; a `POST`, so the `SameSite=Strict` cookie and nginx's cross-site check (`Sec-Fetch-Site` for state-changing
  methods) both apply: a page on another localhost port can't start exports.
- Delete all: the same per-conversation semantics and plugin hooks as today; nginx's cross-site check applies.
- The plugin catalog reveals to users only that a plugin exists and whether it is set up; the missing settings stay in
  the admin API.
- Frontend: no `dangerouslySetInnerHTML`; email text stays text; the export download uses a same-origin blob URL;
  `theme-boot.js` reads only `localStorage` and validates against allow-lists.
- Shared browsers: per-user client state (the plugin store, a queued plugin-browser request) is cleared on every user
  change (U30); the appearance cache is deliberately kept (6.2).
- Session checks: a 401 signs the user out only once `GET /auth/me` confirms it (U25), so a stale request can't sign
  out a tab that holds a fresh cookie, and an expired session still signs out at the next request.

## 15. Test plan

Backend tests run offline as before (SQLite, FakeRedis, in-memory Qdrant, FakeLLM). New files (B):

### 15.1 New backend tests

- **`test_preferences.py`**: defaults in the login response, `GET /auth/me` and `GET /me/preferences` for a user
  without a row; each field patched and returned; a partial patch keeps the rest; `default_model_id: null` clears;
  explicit null for `theme`, an unknown field, a bad enum value → 422; `default_model_id` for a disabled, embedding,
  vision or image model, a model the restricted user isn't allowed, a missing id, `0` and `MAX_INT + 1` → 422; a
  stored default later disabled (or the user restricted) reads as null, and reads back after re-enabling; a corrupted
  row (bad values, unknown keys) reads as defaults, and a later PATCH keeps the unknown keys; users don't see or change
  each other's; 401 without a session; two concurrent first PATCHes both land (no 500); deleting the user deletes the
  row; the admin can save preferences.
- **`test_login_history.py`**: a login records one event (time; masked IPv4, IPv6 → /48, IPv4-mapped IPv6, the test
  client's host → null); `ip_private` true for `192.168.65.x`, `10.x`, `127.0.0.x`, `100.64.x`, `fe80::/48`, `::/48`
  and the documentation ranges, false for a global address (`81.2.69.x`, `2a00:1450:4001::/48`) and for null; a table of about 15 User-Agents (Chrome/macOS, Safari/iPhone, Safari/iPad, Firefox/Linux,
  Edge/Windows, Samsung Internet, Opera, Android phone and tablet, ChromeOS, curl, python-httpx, HeadlessChrome,
  empty, garbage, a 10 KB string); the raw User-Agent is in no column; wrong passwords, inactive users and throttled
  attempts record nothing; 25 logins leave the newest 20; `current` only on the calling session's event (two
  clients); a token without `sid` marks none; change-password keeps `current`; `GET /me/account` fields
  (`password_managed_by_env` true for the admin only, `conversation_count`, `created_at`, `last_login_at`); another
  user's events never appear; `GET /admin/users` is unchanged; deleting the user deletes the events.
- **`test_logout_others.py`**: sessions A and B plus a Bearer token: `POST /auth/logout-others` from A → 204, A still
  works with the new cookie (and keeps its `sid` and `exp`; a token with 60 s left stays at 60 s, also when chained),
  B and the Bearer token → 401; `token_version` + 1; the env admin
  can; 401 unauthenticated; a session ended meanwhile (admin reset) → 401.
- **`test_my_usage.py`** (clock pinned): an empty month (zeros, `daily` from the 1st through today); sums and costs per
  kind; images counted; `requests`; the limits (budget, image limit from row/default, vision setting; all 0 for the
  admin); documents and storage of owned documents only (another user's shared document doesn't count); `by_model`
  display names and the fallback for a deleted model; last month's and other users' events excluded; a +05:45 event
  near midnight lands on its UTC day; `resets_at` in December is the next 1 January; 401.
- **`test_export.py`** (`POST /me/export`; `GET` → 405): the ZIP's entries and that each parses; messages, sources
  (passage and email), files and cards in the JSON and the Markdown; only the caller's conversations and owned documents (bob's absent, another user's
  shared document absent; the admin's export holds only the admin's); no secrets anywhere in the archive bytes
  (`password_hash` values, `SECRET_KEY`, `TOKEN_ENCRYPTION_KEY`, the fake client secret, encrypted token strings);
  headers (type, disposition, `no-store`, `nosniff`, the two counts); truncation by size (a tiny `EXPORT_MAX_MB`
  through settings) keeps the newest and reports `omitted` and `truncated: "size"` in `account.json` and `README.txt`;
  truncation by time (a pinned clock past the deadline); a second export of the same user while one runs → 409; 503
  when two are building; **the lock and the slot are free again** after a finished export, after a build that raised,
  and after a client that disconnected during the build (`request.is_disconnected` patched to true: the build stops
  and the next export of that user is 200, not 409); plugin cards are **hydrated**: a proposed email sent since
  exports with `status: "sent"` and its transcript line says "Email sent", and a card of an unregistered plugin exports
  its stored data; `EXPORT_TIMEOUT_SECONDS` above 90 stops the settings from loading; a conversation whose model and
  documents were deleted still exports; non-ASCII titles give ASCII names; an account with nothing exports a valid
  ZIP; 401.
- **Delete all** (in `test_conversations.py`): every conversation and message of the caller goes and the count is
  returned; bob's stay; generated files and images stay (documents and `generated_*` rows; `conversation_id` null);
  `message_files` and `message_plugin_items` go; with the fake plugin (and the Gmail fixture) pending sends are
  cancelled and scrubbed, as the single delete's tests check; usage events stay; a second call → 0; 401; a conversation
  deleted while an answer streams saves nothing and raises nothing; **a fake plugin whose hook raises on one of 60
  conversations**: all 60 are deleted, `deleted` is 60, and the other 59 conversations' plugin clean-ups are kept.
- **Catalog** (`test_plugins_connections.py` or a new `test_plugin_catalog.py`): with the fake plugin unconfigured →
  listed with `configured: false`, `enabled: false`, no connection; configured and off → `configured: true`,
  `enabled: false`; on → as before; connected and turned off → listed with the connection; another user's connection
  never shown; in every state but "enabled, connected, Use in chat on" the chat offers no plugin tools, and the connect
  note appears only for enabled and not connected; connect still 409 (not configured) and 403 (off); a connected user
  of a plugin that became not configured still gets it listed with the connection, and `DELETE …/connection` → 204;
  the changed messages contain "Settings".
- **Cards** (`test_conversations.py`): `GET /conversations/{id}` returns the same cards as before the move of the
  hydration loop into `registry.hydrate_cards` (the existing plugin card tests cover it; run them).
- **Models** (`test_models_api.py`): `supports_tools` true for tool-capable models, false for one LiteLLM doesn't
  know and after a model rejected tools in a chat.

### 15.2 Existing tests to update (B)

With the contract applied and no logic yet, the suite is **1903 passed, 10 failed, 1 skipped** (2026-10-18, 291 s;
ruff and mypy clean). Each failure is an exact-shape assertion that doesn't expect a new key:

- `Me.preferences`: `test_auth.py::test_login_returns_me_sets_the_session_cookie_and_records_the_login`,
  `::test_me_reports_the_users_limits_and_this_months_usage`, `::test_me_reports_the_admin_as_unlimited`;
- `ChatModel.supports_tools` (still the schema default `false` until 4.9):
  `test_models_api.py::test_unrestricted_user_gets_every_enabled_chat_model_default_first`,
  `::test_restricted_user_gets_only_allowed_enabled_models`, the four cases of
  `::test_provider_is_the_prefix_of_the_litellm_model`, and
  `test_admin.py::test_making_a_model_the_default_clears_the_previous_default`.

The catalog rule (4.8) will then break three assertions that expect a plugin to be hidden:
`test_plugins_connections.py::test_plugin_list_rules` (lines 347 and 358: turned off, and not configured) and
`test_plugins_gmail_independent.py::test_disconnect_after_the_key_was_removed` (line 517: not configured after the
key was removed). Update them to assert the plugin is listed with `enabled: false` and the right `configured` and
`connection`; in the last one, after the `DELETE` the plugin is still listed (`configured: false`, `connection: null`)
instead of the list being empty. `PluginInfo.configured` has the schema default `true` until 4.8 sets it. Nothing else
should change. Revision 2 added `LoginEvent.ip_private` (no default; nothing builds a `LoginEvent` yet), and the suite
is unchanged by it: 1903 passed, the same 10 failed, 1 skipped (2026-10-18, 272 s; ruff and mypy clean).

### 15.3 Frontend checks (every builder, at the end of each stage)

`npx tsc --noEmit`; `npx vite build --outDir "$(mktemp -d)/dist"` (never into the repo); `package.json` and
`package-lock.json` unchanged; each CSS file only uses its own prefix (a grep over selectors); no
`prefers-color-scheme` in any CSS file; FOUNDATION's contrast table (11.2) for 5 accents × 2 themes (the revision 2
audit: 330 pairs, 0 failures); the font files' sha256 match the recorded ones, and `fonts.css` has the fallback faces
of appendix B with the overrides printed by appendix C.

### 15.4 Browser pass (integrator; Playwright + Chromium from the scratch `browser` directory)

Users `smoke-ui` and `smoke-ui-other` (admin-created, deleted at the end); never sign in as `bikash`, and take a
read-only snapshot of its counts through the admin API before and after (as the earlier runs did). No model calls are
needed; at most one short Claude Haiku 5.5 answer for the attachment flow, with the user's approval.

1. **No flash**: with the cache set to dark and the OS light, delay the main bundle (`page.route`) and check that
   `<html data-theme="dark">` and the dark background are there before it runs; the same for light on a dark OS.
2. **System live**: preference System, `page.emulateMedia({colorScheme})` flips the theme without a reload; Light
   and Dark ignore it.
3. **General**: theme, accent and font size apply at once, survive a reload, and show in a second browser context
   after sign-in (server-side); a failed save (route the PATCH to a 500) reverts and shows the error; Enter-to-send
   off: Enter adds a line, Ctrl/Cmd+Enter sends.
4. **Default model**: set in Settings → a new chat starts with it; the top-bar pick changes only this chat; New chat
   resets; a default the admin disables falls back to the server default.
5. **Settings navigation**: `/settings/usage` → `/?settings=usage`; `?settings=account` over `/c/<id>` keeps the
   conversation; Back closes; tab keys; Escape; focus returns to the avatar; the OAuth return URL opens Plugins & data
   with the banner (use `/?plugin=gmail&oauth=denied`). **Settings over a new chat's first answer**: on `/`, send a
   message (route the chat stream to a slow fake SSE response with `page.route`, so no model is called) and open
   Settings before it ends; when the URL becomes `/c/<id>` the dialog stays open on the same tab, and closing it leaves
   `/c/<id>` with the answer still there.
6. **Account**: the sign-in list with "This device" (and "Via a local network or proxy" for the Docker gateway);
   change password for `smoke-ui`; "Log out of all other sessions" signs out the second context (its next request →
   login page) and keeps this one. **Stale 401s**: start an upload that keeps the documents panel polling (or hold a
   `GET /documents` with `page.route` until the logout-others response has arrived, then let it through with the old
   cookie's 401), click "Log out others": this tab stays signed in.
7. **Usage**: the numbers match `GET /me/usage`; the chart and its table; the meters in the avatar menu.
8. **Plugins menu** with the repo's `.env` (Gmail not configured): `smoke-ui` sees "Not set up yet" (disabled);
   the admin sees the link to `/admin#plugins`; "Manage plugins…" opens Plugins & data with the Gmail card. Optional,
   with the fake Google setup of the plugin smoke: Connect, Connected, Reconnect needed, the "+" menu's Gmail group,
   and "Not set up, still connected" (remove the fake client's settings and restart the backend): the card offers
   Disconnect and it works. **Shared browser**: as the admin open Settings → Plugins & data on `/admin`, sign out there,
   sign in as `smoke-ui`: the Plugins menu and the "+" menu never show the admin's state.
9. **"+" menu**: Attach a small `.txt` → chip progress → "Indexing…" → "Ready", Send waits meanwhile, the selection
   includes it; image and document items insert their prefixes; hidden and disabled items with reasons (a model
   without tools via a custom model the admin adds for the run, then deletes).
10. **Export**: the ZIP downloads, unzips, holds `smoke-ui`'s conversation and not `smoke-ui-other`'s.
11. **Delete all chats**: the typed confirmation; the sidebar empties; the route returns to `/`; documents stay.
12. **Looks**: screenshots at 1440, 1000, 700 and 390 px, light and dark, for the chat (empty, with an answer with
    sources and a file card), the sidebar drawer, the documents panel and slide-over (and collapsed on desktop), every
    Settings tab, every admin tab, the login page; Settings → Usage also at 820 and 390 px; no page errors, no CSP
    violations, no horizontal or page-level scrolling; `prefers-reduced-motion` turns transitions off.
    **Element overflow, not just page overflow**: at 390, 700, 800, 820, 1000, 1100, 1110 and 1440 px, with the
    documents panel shown and collapsed and the longest model name in the list, every child of `.chat-topbar` ends
    inside the bar (`getBoundingClientRect`) and the bar doesn't scroll; in Settings → Usage the chart's axis labels
    compute to at least 12 px and the chart foot stays inside its card.
    **Fonts**: on a cold cache (a new context) open a long conversation scrolled to the bottom and wait for
    `document.fonts.ready`: the thread is still at the bottom (within 2 px). Then route the woff2 files to a 3 s delay
    and compare the thread's `scrollHeight` before and after the swap: within 1% (the fallback faces are doing their
    job).
    **Theme boot**: with the cache set to dark on a light OS, the `color-scheme` meta reads `dark` before the bundle
    runs; `/theme-boot.js` comes with `Cache-Control: public, max-age=300`.
13. **Keyboard and names**: the avatar, Plugins, model and "+" menus and the Settings tabs by keyboard only; the
    accessibility snapshot names the menu buttons, items (with their disabled reasons), tabs and meters; the avatar
    menu reads "Account: smoke-ui, Member" and "View usage" is described by the usage numbers; Left/Right move along
    the theme row and the format chips. "Attach files…" chosen by keyboard (Enter) opens the picker (Playwright's
    `filechooser` event), which proves the click stayed inside the gesture in Chromium; WebKit isn't installed, so
    code review checks that the action calls `input.click()` synchronously.
14. **Browse Gmail from Settings** (with the fake Google setup, optional): only one dialog is open at any moment (one
    `[role=dialog][aria-modal=true]`), and when the Gmail browser closes focus lands on the element that opened
    Settings, or on the "+" button.

## 16. Out of scope and open points

Out of scope: collapsing the sidebar on desktop; keyboard shortcuts; per-conversation model memory; custom accent
colours; avatar images; languages other than English; notifications; a user-side account deletion; a session list with
per-session revoke; an admin view of sign-ins; importing data; exporting document files; previous months in Settings →
Usage.

Open points for the user:

1. **Approximate IPs** in the sign-in history (`81.2.69.x`), or the full address? Behind the tunnel and Docker's port
   publishing every sign-in comes from Docker's gateway either way, shown as "Via a local network or proxy", until
   nginx's `set_real_ip_from` is set up for the outer proxy (HANDOFF next step 5).
2. **Send waits for attached files** to finish indexing (a large scanned PDF read by the vision model can take minutes;
   removing the chip sends without it).
3. **Users see estimated costs** ($) of their own usage in Settings → Usage. Hide them for non-admins?
4. **The top-bar model pick is per chat** and New chat returns to the default (U7), instead of the last pick sticking.
5. **Unconfigured plugins are listed for every user** as "Not set up yet" (the user's request), which tells users the
   app has a Gmail plugin the admin hasn't set up.
6. **The export leaves out document files** and stops at 100 MB of text or 80 seconds of building (newest
   conversations first; the tunnel allows at most about 100 s of silence).
7. **Delete all chats keeps documents**, generated files and images, and the usage history (as single deletes do).
8. **The documents panel can be collapsed on desktop**, remembered per browser.
9. The five accent presets (Clay, Ocean, Sage, Plum, Slate) and the fonts the visual spec picks (bundle size), and
   the visual spec's own open points (its section 0.4: the renamed font families, the new Ocean and Slate colours,
   "Indexing…" replacing "Processing").

## 17. Work split and interfaces

The contract (`SPEC.md`, `types.ts`, `schemas.py`, `schemas_account.py`, `schemas_plugins.py`, `ChatModel` in
`routers/models.py`) is written. A builder who finds a contract bug reports it instead of editing it. Builders never
build into the repo, never change git state, and keep test users named `smoke-*`.

### 17.1 Stages

```
stage 1:  BACKEND (B)  ∥  FOUNDATION (F)            disjoint files; B's API is what F's client calls
stage 2:  SHELL (S)  ∥  SETTINGS (T)  ∥  RESTYLE (R) on F's interfaces (section 12), against B's backend
then:     integrator: README, HANDOFF, briefs index, Docker rebuild, the browser pass (15.4)
```

B may also run before F or alongside stage 2; only the stage-2 browser checks need it.

### 17.2 Ownership (each file has exactly one owner per stage)

| Builder | Owns (creates, changes or deletes) |
|---|---|
| **B** backend | `backend/app/models.py` (two tables), `routers/me.py` (new), `routers/{auth,conversations,plugins,models,admin}.py` (logic; the `ChatModel` class is contract), `services/account.py` and `services/export.py` (new), `services/usage.py` (helpers only), `security.py`, `deps.py`, `config.py`, `main.py` (router wiring), `plugins/registry.py` (the string of 4.8 and the moved `hydrate_cards`), `plugins/connections.py` and `plugins/gmail/{tools,sends}.py` (the strings of 4.8 only), `.env.example`; tests: the new files of 15.1, the updates of 15.2, `conftest.py` helpers if needed |
| **F** foundation (stage 1) | `frontend/index.html`; `frontend/public/theme-boot.js` (new); `frontend/nginx.conf` (only the new `location = /theme-boot.js`, 6.1); `src/main.tsx`; `src/styles/global.css`; `src/styles/fonts.css` and `src/assets/fonts/**` (new; licences included, sources and sha256 in a comment at the top of `fonts.css`); `src/theme/appearance.ts` (new); `src/auth/AuthContext.tsx`; `src/api/client.ts`; `src/lib/format.ts`, `src/lib/appEvents.ts` (new), `src/lib/settingsLink.ts` (new); `src/components/ui/**` (`Modal`, `Spinner`, `ErrorBoundary`, new `Menu`, `Avatar`, `SegmentedControl`, `Switch`, `Meter`, `PasswordInput`, `FullPageState`, `icons`). No `login-*` rules: those are R's (`pages/login.css`). **Interface scaffolding, stage 1 only, behaviour-preserving except as stated in 12:** `src/components/connections/ConnectionsPanel.tsx` (new, extracted, with the catalog statuses), `src/components/connections/ConnectionsModal.tsx` (reduced to a thin wrapper that re-exports `OAuthReturn` and `parseOAuthReturn`), `src/components/connections/pluginStore.ts` (statuses, the browser request, the user-change reset), `src/components/documents/uploadManager.ts` |
| **S** shell (stage 2) | `src/pages/ChatPage.tsx`; `src/components/chat/**` (every file, `chat.css`, `icons.tsx`, `plugins/**` and `plugins.css`; new `TopBar`, `ModelMenu`, `PluginsMenu`, `AvatarMenu`, `PlusMenu`, `AttachmentChips`, `useAttachments`; deletes `ChatHeader`, `ModelSelect`, `UserMenu`, `UsageMeter`, `ChangePasswordModal` once replaced); `src/components/connections/ConnectionsModal.tsx` (deletes it after removing its use) |
| **T** settings (stage 2) | `src/App.tsx` (routes, `SettingsHost` mount, and its full-page states `FullPageSpinner` and "Can't reach the server" rebuilt on F's `FullPageState`); `src/components/settings/**` (new: `SettingsHost`, `SettingsModal`, `GeneralTab`, `AccountTab`, `PasswordForm`, `UsageTab`, `DailyUsageChart`, `PluginsDataTab`, `settings.css`) |
| **R** restyle (stage 2) | `src/pages/LoginPage.tsx` and `src/pages/login.css` (new, prefix `login-`, imported by `LoginPage.tsx`), `src/pages/AdminPage.tsx`; `src/components/admin/**`; `src/components/documents/**`; `src/components/connections/**` except `ConnectionsModal.tsx` |
| **Integrator** | `README.md`, `docs/HANDOFF.md`, `docs/briefs/README.md`; the Docker rebuild (`docker compose up -d --build`, then recreate the frontend after the backend), the browser pass (15.4), cleanup |

Nobody owns `src/api/types.ts` (contract), `src/lib/blockStrayFileDrops.ts` and `src/auth/loginRedirect.ts` (no
change needed) in stage 2.

### 17.3 What each stage-2 builder relies on, and who changes what

- **S** uses `useAuth().preferences`/`updatePreferences`, `useOpenSettings`, `useReplacePath` (every `replace`
  navigation in ChatPage, section 5), `onConversationsDeleted`, the `ui` primitives, `pluginStatus`/`PLUGIN_STATUS_LABEL`/`onPluginBrowserRequest` (opens `GmailImportModal` from the "+"
  menu and from Settings' Browse), `uploadManager` and `useDocumentSnapshot`; the avatar menu's meters use `Me.usage`
  (no `GET /me/usage` call). S **removes** ChatPage's OAuth-return handling and its `onConnectionsRequest` subscription
  (T's `SettingsHost` takes both over) and the transitional Connections modal. On `onConversationsDeleted`: the
  conversation list empties (and remembers the ids as deleted, so a late `touch` can't bring one back), a stream in
  progress is stopped, and a `/c/:id` route goes to `/` through `useReplacePath` (so an open Settings stays open).
  ChatPage keeps `DocumentPanel` mounted while collapsed (8.4), and focuses the "+" button when the Gmail browser
  closes with nowhere to return focus (10.5).
- **T** uses `ConnectionsPanel`, `requestConnections`'s listener (`onConnectionsRequest`), `requestPluginBrowser`
  (after the dialog unmounted, 10.5), every client helper of section 12, the `format.ts` additions,
  `ACCENTS`/`CHAT_FONT_SIZES`, the `ui` primitives (`Modal`'s `hideHeader`, `labelledBy`, `bare`, `initialFocus`;
  `PasswordInput` in `PasswordForm`; `FullPageState` in `App.tsx`), `announceConversationsDeleted`.
- **R** restyles its areas to the visual spec. The login page's rules move into `pages/login.css` (the visual spec's
  6.28); `LoginPage` and the admin user form use `PasswordInput`. In `AdminHeader` it adds a Settings button
  (`useOpenSettings`) and keeps Back to chat and Sign out. In `ConnectionsPanel` it keeps the "Not set up, connected"
  Disconnect (10.5). In `DocumentPanel` it may switch "Import from Gmail" to `requestPluginBrowser` or keep its
  own `GmailImportModal` (either way the props above stay). It keeps the props and exports listed in section 12.
- **F** finishes before stage 2 with tsc and the build clean, the app working as before apart from the new look of the
  primitives, the theme engine live (preferences applied from `Me` once B is in; until then from defaults), and a short
  note in its report of any interface it had to shape differently (then this brief is updated before stage 2).

### 17.4 Backend ↔ frontend

The HTTP contract in `SPEC.md` and `types.ts`: `Me.preferences`, `GET/PATCH /me/preferences`, `GET /me/account`,
`GET /me/usage`, `POST /me/export` (headers included), `POST /auth/logout-others`, `DELETE /conversations`,
`PluginInfo.configured` and the catalog rule, `LoginEvent.ip_private`, `ChatModel.supports_tools`. Stage-2 builders can work against a mocked
backend from these alone.
