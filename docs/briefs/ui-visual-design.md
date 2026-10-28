# Visual spec: Paper & Clay (warm, editorial)

> The design system for the redesign in [`ui-shell-settings.md`](ui-shell-settings.md) (the **functional brief**). That
> brief calls this document **the visual spec**: on looks (palette, type, spacing, shadows, the composition of each
> screen) this document wins; on behaviour, accessibility and contract the functional brief and `SPEC.md` win. Where
> this spec had to bend the winning mockup to fit them, section 0.2 says so. **Revision 2**, 2026-10-18: the review's
> changes are in section 0.3; the reference mockup below predates them, and where the two differ this document wins.
>
> **Reference mockup** (static HTML, the same tokens and classes as below):
> `/private/tmp/claude-501/-Users-bikash-rag-app/73efbff7-64da-4b99-a056-7cb30809179c/scratchpad/wf7/final-mock/`
> (`index.html`, `settings.html`, `login.html`, `admin.html`, `states.html`, `paper-clay.css`, `mock.js`, `fonts/`).
> Its sources and tools are in `…/wf7/final-mock-tools/` (`src/*.html`, `sprite.svg`, `build.py`, `shoot.mjs`,
> `contrast_audit.py`, `subset_fonts.py`). Scratch paths can disappear: everything a builder needs is also in this file
> (tokens in 3.9, font scripts in appendices A and C).
>
> **Revision 2 check copy** (scratch): `…/wf7/designer-rev/` holds a copy of the mockup with the revision 2 tokens and
> the container-query top bar (`mock/`), the extended `contrast_audit.py` (330 pairs), `fallback_metrics.py`,
> `topbar_check.mjs` (element overflow at 14 widths) and a few screenshots (`shots/topbar-*.png`,
> `shots/settings-general-dark-sage.png`).

## Screenshots

All at device pixel ratio 2, `…/wf7/final-mock/shots/`:

| Screen | Files |
|---|---|
| **Chat, 1440, Plugins menu open** | `index-light.png`, `index-dark.png` |
| **Chat, 390** | `index-mobile-light.png`, `index-mobile-dark.png` |
| **Settings → General** | `settings-light.png`, `settings-dark.png`, `settings-general-system-plum.png` (System preference on a dark OS, Plum, large font), `settings-mobile-light.png` |
| **Login** | `login-light.png`, `login-dark.png`, `login-mobile-light.png` |
| "+" menu | `index-light-plus.png`, `index-mobile-plus.png` (bottom sheet) |
| Avatar menu, sources expanded | `index-dark-avatar.png`, `index-light-sources.png` |
| Model menu, documents collapsed | `index-light-model.png` |
| Plugins menu on a phone | `index-mobile-plugins-dark.png` |
| New chat (empty state) | `index-empty-light.png`, `index-empty-dark-ocean.png` |
| Drawer breakpoint (1000 px) | `index-1000-light.png` |
| Settings → Account, Usage, Plugins & data | `settings-account-light.png`, `settings-usage-light.png`, `settings-usage-dark.png` (Sage), `settings-plugins-light.png`, `settings-plugins-dark.png`, `settings-mobile-usage-dark.png` |
| Admin | `admin-light.png`, `admin-dark.png`, `admin-mobile-light.png` |
| Every other state (full page) | `states-light.png`, `states-dark.png` |

`shoot.mjs` (run from `…/scratchpad/wf2/browser`) renders all 31, checks for page errors, failed requests, horizontal
overflow and page-level scrolling (none), and lists the loaded fonts. Mockup URL state: `?theme=system|light|dark`,
`&accent=clay|ocean|sage|plum|slate`, `&font=small|default|large`, `&menu=plugins|avatar|model|plus|none`,
`&view=empty`, `&docs=hidden`, `&sources=open`, `&scroll=bottom`; `settings.html?settings=general|account|usage|plugins`.

---

## 0. How this design was chosen

### 0.1 Scores

Two judges scored three directions on seven criteria (0–10 each). Sums:

| Direction | Brand fit | Beauty | Readability | Hierarchy | Contrast | Dark | Mobile | Judge 1 | Judge 2 | **Total** |
|---|---|---|---|---|---|---|---|---|---|---|
| **paper-clay** | 18 | 17.5 | 18 | 17 | 16.5 | 17.5 | 16 | 60.0 | 60.5 | **120.5** |
| soft-warm-minimal | 12 | 15 | 14.5 | 16 | 16.5 | 15 | 14 | 52.5 | 50.5 | 103.0 |
| editorial-ink | 12 | 16.5 | 15.5 | 13 | 15.5 | 14.5 | 14.5 | 52.5 | 49.0 | 101.5 |

Both judges named paper-clay the winner; it leads every criterion except contrast. **Ties:** judge 1 scored the two
runners-up level (52.5 each) and judge 2 split them (50.5 vs 49.0), so soft-warm-minimal is second overall. Paper-clay
and soft-warm-minimal tie on contrast (16.5), which is why most accessibility grafts come from soft-warm-minimal. The
runners-up also tie on brand fit (12): both missed the clay default.

### 0.2 What changed from the winning mockup

**To fit `SPEC.md` and the functional brief (these override the mockup):**

| Change | Why |
|---|---|
| Accents are **Clay, Ocean, Sage, Plum, Slate**. Harbor became Ocean (re-tuned to a teal-blue `#22697a` so it can't be mistaken for the info-blue "Indexing…" badge); Ink became Slate (a cool `#4a5866`); Ochre is dropped | `AccentColor = "clay"\|"ocean"\|"sage"\|"plum"\|"slate"` (SPEC section 6, U1, U20) |
| Light Clay deepened from `#b0512e` to `#a94d2b`, its soft fill from `#f6e5dc` to `#f7e8e0` | The functional brief requires `--accent` as text on `--accent-soft` at 4.5:1. The old pair was 4.22; it is now 4.64 |
| Attributes `data-theme`, `data-theme-preference`, `data-accent`, `data-chat-font`. Tokens `--chat-font-size` and `--chat-line-height` (not `--chat-font`), `--overlay` (not `--backdrop`). **No `prefers-color-scheme` in any CSS** | U2, U3, 11.2, 15.3 |
| The sidebar's usage card is **gone**: the token and image meters live in the avatar menu | "the sidebar has no footer" (SPEC 8, U18). This also removes the duplication both judges flagged, from the other side |
| The Settings tab is **General** (not Appearance); theme options are ordered System, Light, Dark; the hint reads "Currently dark, from your system" | 10.1, 10.2 |
| The Plugins menu has **no Connect button**: each plugin row is one menu item that opens its card in Settings. "Connect ›" is a pill inside the item, not a second control | 8.3: "the menu never starts OAuth itself"; a button inside a menu item breaks the menu pattern |
| The "+" menu uses the functional item names and groups (Attach files…, Create, Gmail). The format chips are real `menuitem`s with full accessible names ("PDF document"). The Gmail group is hidden unless Gmail is set up and turned on | 9.1, 9.2 |
| The avatar menu adds View usage, a Theme row (System / Light / Dark) and Admin. The `⌘ ,` hint is dropped | 8.5; keyboard shortcuts are out of scope (16) |
| The normal message footer has Copy only, no Retry | Retry appears only on errors in the app today |
| Danger moved from brick red to **crimson** (`#b02a3a` light, `#f2899a` dark) | Brick next to clay made the Delete button look like the primary button, especially in dark mode |
| Fonts: four files at about 1 MB became **three subset files at 323 KB**, renamed **RAG Serif** and **RAG Sans** | 11.3 (at most 4 files, about 400 KB, Latin). Subsetting is a modification, and the OFL Reserved Font Name "Source" can't name a Modified Version (4.1) |
| Class names follow the app's prefixes (`chat-cite`, `chat-send`, `docs-tag`, `ui-menu` …); section 12 maps them | CLAUDE.md, 11.3 |
| One status vocabulary: **Queued, Indexing…, Ready, Failed, Needs re-index** | Judge 2: the same file was "Indexing" in chat and "Processing" in the panel |

**Grafted from the runners-up:**

- *soft-warm-minimal:*
  - **Settings → Account:** facts rows (Username, Role, Account created, Last sign-in with time, browser on OS, device and approximate IP) above the sign-in list, plus the "If you don't recognise one…" guidance.
  - **Settings → Usage:** a peak label on the tallest bar and a footer row (requests per minute · reset). The fixed readout above the plot replaces the tooltip, so nothing covers neighbouring bars, and the broken "Up to 20 messages a minute ." sentence is gone.
  - **Plugin statuses:** "Turned off by the admin" uses a lock and neutral ink, not a red dot. "Not set up" gets a one-line reason, and the admin link stays on one line.
  - **Cards and buttons:** destructive buttons name the count ("Delete 48 chats"). The Gmail card says "Connected 12 Oct · last used today".
  - **Forms:** a show/hide toggle on every password field.
  - **Phones:** menus open as bottom sheets.
  - **States sheet:** banners, the empty documents panel, skeletons, the control inventory with a visible focus example, and the "+" menu with disabled reasons.
- *editorial-ink:*
  - **Answer tables** use booktabs rules (heavier top and bottom, hairlines between rows, no box).
  - **Empty state:** the "contents page" list, with an italic serif sub-line.
  - **Citations:** each citation chip is glued to the word before it.
  - **Top bar:** a labelled Documents toggle and a plug icon for Plugins.
  - **Phone login:** a compact hero.
  - **Phone settings:** a title and close row above the tab strip, which gets an edge fade and scroll-snap.
- *paper-clay's own defects, fixed:*
  - **Copy:** the new-chat placeholder ("Ask a question…"; "Reply…" in a conversation) and the trailing "·" in the phone file card.
  - **Contrast:** the disabled "+" items no longer drop to 2.6:1 (only the icon and title dim; the reason stays at `--text-2`).
  - **Brand mark:** it stays clay whatever the accent.
  - **Layout:** the font-size control spans the width on phones, and the account tab no longer starts under the sticky header's fade.

**Declined, with the reason:**

- **Syntax-colour tokens for code:** there is no highlighter in the stack and no new npm packages are allowed. Code
  stays monochrome on `--code-bg`.
- **Dark tokens also under `@media (prefers-color-scheme: dark)`:** SPEC and U3 forbid it, and `theme-boot.js`
  already resolves System before the first paint.
- **A "Gmail" chip in the composer:** the contract has no per-message tool switch, and Gmail's state shows in the
  Plugins menu.
- **The "Answers can be wrong" line in the composer hint:** the hint's text is fixed by 9.4. The empty state's sub-line
  carries the message.
- **A sticky "Saved" footer on phones:** "Saved" sits in the panel header on every width.

### 0.3 Revision 2: what the review changed

The functional brief's section 0.2 lists every review point and its response; these are the ones that change looks or
component rules here.

| Change | Why | Where |
|---|---|---|
| The top bar compacts by the **chat column's width** (container queries), not the viewport: labels from 560 px of column, the title from 720 px; the model button is the only item that shrinks (ellipsis), and keeps the provider word | Viewport rules overflowed at 800, 820 and 1100 to 1125 px, where the column is 460 to 505 px. Checked on the revision 2 copy at 14 widths, panel shown and collapsed, with a 40-character model name: no overflow | 6.10, 8 |
| Light `--success` is **`#39703f`** (was `#3d7543`) | The Plugins menu's "Connected" line on a hovered item (`--surface-3`) was 4.37:1; now 4.69 | 3.5, 3.9, 11 |
| New **`--switch-thumb-on`**: the paper thumb in light, the accent's own dark ink (`--accent-text`) in dark | The light thumb on a light dark-mode track was 1.8 to 2.5:1, under 3:1 for the main state cue; now 6.9 to 9.4 | 3.6, 3.9, 6.3, 7 |
| New **`--chat-bubble-size`** (15 / 16 / 17.5 px) | The chat font size setting says "Answers and your messages"; the bubble was fixed at 16 px | 3.8, 4.3 |
| **Metric-matched fallback faces** ("RAG Serif Fallback", "RAG Sans Fallback") second in the font stacks | Answer prose, bubbles and titles re-wrapped when the web fonts arrived | 4.1, appendices B and C |
| **Menus render in a portal** on `<body>`; actions run synchronously; the "Create a document" row is the label of the chip group; non-item content is folded into labels and descriptions | No ancestor can trap a sheet; Safari's file picker; the menu pattern | 5, 6.9, 6.11, 6.12, 6.15 |
| Meters at 90% and 100% add **an icon and words** ("Almost used up", "Limit reached") | The dark Clay and Plum fills are close in hue to danger; the state can't be colour alone | 6.6 |
| The usage chart's **viewBox follows its measured size**; the readout's live region speaks only for keyboard moves; the foot wraps and its icons are sized | Labels shrank to 6 to 8 px; the foot overflowed at 390, 800 and 860 px | 6.23 |
| Sign-ins behind a proxy read **"Via a local network or proxy"** | Every sign-in through Docker showed `192.168.65.x` | 6.23 |
| The "Not set up" card and menu item **offer Disconnect when a connection remains** | A user couldn't remove a connection after the admin removed the settings | 6.11, 6.24 |
| The login page's rules live in **`src/pages/login.css`** (RESTYLE); the full-page states are FOUNDATION's `FullPageState` | One owner per file and markup next to its rules | 6.26, 6.28, 12 |
| "✓ Saved" is visual only; the textarea keeps JS autosize (`field-sizing` only as an enhancement); citation chips' 19 px is the inline exception | Accessibility details, Firefox, the target-size note | 6.14, 6.23, 10 |

**Declined in revision 2:** shifting dark `--danger` toward a cooler rose to raise its distance from the accents (ΔE
23 to 27 for Clay and dark Plum). Red sits between Clay's orange and Plum's magenta, so moving it away from one moves it
toward the other, and it must stay red. The meaning is carried by words and icons instead (6.1 "destructive buttons say
what they do", 6.6). The audit prints the distances for information.

### 0.4 Open points for the user

- **The font names**: the subset files are renamed "RAG Serif" and "RAG Sans", as the OFL's Reserved Font Name clause
  requires for a modified (subset) font. They are still Source Serif 4 and Source Sans 3.
- **The new presets**: Ocean (a teal-blue, `#22697a`) and Slate (`#4a5866`) replace the mockup's Harbor and Ink.
- **"Indexing…" replaces "Processing"** in the documents panel, so one file has one status word everywhere.

---

## 1. Principles

1. **The page and the desk.** The chat column is a sheet of ivory paper (`--bg`). The sidebar, the documents panel,
   the settings rail and the login aside are the slightly darker desk around it (`--sidebar`). Only hairlines
   separate them, never heavy chrome. Raised things (composer, cards, menus, dialogs) are a lighter paper
   (`--surface`) with warm umber shadows.
2. **Two voices.** The assistant's answer is set in a serif (RAG Serif, from Source Serif 4) like a printed page.
   Everything that is interface or data (controls, the user's own words, tables, chips, meta lines) is in the sans
   (RAG Sans, from Source Sans 3). Both come from Adobe's Source family, so they pair naturally.
3. **Clay is an accent, not a theme.** It is used for the send button, citations, list markers, checkboxes,
   switches, meters, focus rings, the selected tab and links. Large areas stay paper. The accent is a token set the
   user can swap (five presets), and every preset passes AA in both themes by construction.
4. **Fixed meanings don't follow the accent.** File-type colours, status colours and the brand mark are fixed.
5. **Calm.** Short fades and small rises, nothing bouncy, and all of it off under reduced motion.

---

## 2. Theme mechanics (FOUNDATION)

- `<html>` carries `data-theme="light|dark"` (resolved), `data-theme-preference="system|light|dark"`,
  `data-accent="clay|ocean|sage|plum|slate"` and `data-chat-font="small|default|large"`, set by
  `public/theme-boot.js` before the first paint and kept by `src/theme/appearance.ts` (functional brief 6).
- `global.css`: light tokens on `:root`; dark on `:root[data-theme="dark"]` (with `color-scheme: dark`). The presets
  use `:root[data-accent="x"]` plus `:root[data-theme="dark"][data-accent="x"]`. Each selector is also written without
  `:root` (`[data-accent="x"]` and `[data-theme="dark"] [data-accent="x"]`), so a nested element can show another
  preset: the Settings swatches use this (6.23). The chat font uses `:root[data-chat-font="small|large"]`. **No CSS
  file contains `prefers-color-scheme`.**
- `<meta name="theme-color">` takes the resolved `--bg`: `#faf8f3` (light), `#262421` (dark); `<meta
  name="color-scheme">` takes the resolved theme (`light` or `dark`; `index.html` ships `light`). Both are set by
  `theme-boot.js` before the first paint and kept by the engine (functional brief 6.1, 6.2).
- `:root[data-theme-switching] *, …::before, …::after { transition: none !important; }`, so a theme swap doesn't
  animate every border.
- **Colours only come from tokens.** There are two documented exceptions: the mini theme previews in `settings.css`
  (they depict a theme, so their colours are fixed) and the `rgba()` values inside shadows.

---

## 3. Tokens

### 3.1 Grounds and surfaces (elevation)

| Level | Token | Light | Dark | Used for |
|---|---|---|---|---|
| Desk | `--sidebar` *(new)* | `#f3f0e8` | `#1e1c1a` | sidebar, documents panel, settings rail, login aside |
| Page (level 0) | `--bg` | `#faf8f3` | `#262421` | chat column, admin page, login form side, dialog footers |
| Raised (level 1) | `--surface` | `#fffdf9` | `#2f2c28` | composer, cards, rows on hover, inputs; `--shadow-xs`, or `--shadow-composer` for the composer |
| Overlay (level 2) | `--surface` + `--border` + `--shadow-lg` | | | menus, popovers |
| Modal (level 3) | `--surface` + `--shadow-lg` over `--overlay` | | | dialogs, the Settings dialog, sheets |
| Quiet fill | `--surface-2` | `#f3f0e8` | `#2a2724` | table heads, chips, segmented track, inline notes |
| Hover / pressed | `--surface-3` | `#eae5da` | `#3a3631` | menu item hover, active conversation, pressed |
| User bubble | `--bubble` *(new)* | `#efeae0` | `#37332e` | the user's message |
| Code | `--code-bg` *(new)* | `#f4f0e7` | `#1c1a18` | code blocks, inline code |

In dark mode, surfaces get **lighter** as they rise (`#1e1c1a` → `#262421` → `#2f2c28` → `#3a3631`). Shadows carry
less and borders carry more.

### 3.2 Lines and focus

| Token | Light | Dark | Role |
|---|---|---|---|
| `--border` | `#e5dfd3` | `#3a3631` | hairlines between areas, around cards and menus, between rows |
| `--border-strong` | `#d3cab9` | `#4e4842` | secondary button outlines, the dashed dropzone, hover borders, blockquote rule. **Decorative**: never the only boundary of a control |
| `--border-control` *(new)* | `#8f8476` | `#857b70` | inputs, selects, textareas, checkboxes, radios, the switch track when off: **≥ 3:1** on every ground (WCAG 1.4.11) |
| `--rule` *(new)* | `#b8ad9b` | `#605850` | the heavier top and bottom rules of answer tables and the empty-state list; table header underline |
| `--focus-ring` | `0 0 0 2px var(--bg), 0 0 0 4px var(--accent)` | same | every `:focus-visible` (accent ≥ 4.33:1 against the grounds) |

**Mapping existing uses:**

- **Inputs, selects and the switch track:** global.css used `--border-strong` for `.input`, `.select` and the
  `.switch` track. FOUNDATION switches them to `--border-control`. Feature CSS that draws its own control boundary
  (a custom checkbox, radio or switch track) does the same. Decorative marks such as `.conn-cap-dot` keep
  `--border-strong`.
- **The contrast check:** functional brief 11.2's "input borders (`--border-strong`) … 3:1" check therefore applies
  to `--border-control`.

### 3.3 Text

| Token | Light | Dark | Role |
|---|---|---|---|
| `--text` | `#2a2520` | `#f2ede4` | body, titles, values |
| `--text-2` | `#5a5249` | `#cec6b9` | secondary text, descriptions on hover fills, button text in ghost buttons |
| `--text-3` | `#6e655a` | `#a89f92` | meta, hints, placeholders, group labels. 4.55 / 4.59 on `--surface-3`: prefer `--text-2` on hover fills |

### 3.4 Accent (five presets; only these five tokens change)

| Token | Role |
|---|---|
| `--accent` | fills (primary button, send, checkbox, switch on, meter, selected tab bar), marks (list markers, focus ring) |
| `--accent-hover` | hover and pressed fill |
| `--accent-soft` | quiet tint: citation chips, "Connect" pill, dropzone icon tile, selected theme halo |
| `--accent-ink` *(new)* | **accent-coloured text**: links, citation numbers, "Created in chat", active tab icon, `em` in display headings |
| `--accent-text` | text and icons on an `--accent` fill |

| Preset | Light: accent / hover / soft / ink / on-accent | Dark: accent / hover / soft / ink / on-accent |
|---|---|---|
| **Clay** (default) | `#a94d2b` / `#93421f` / `#f7e8e0` / `#9a4423` / `#ffffff` | `#e28c69` / `#eba083` / `#44302a` / `#f2b497` / `#24140d` |
| **Ocean** | `#22697a` / `#1b5765` / `#ddedf0` / `#1d5c6b` / `#ffffff` | `#7cc0cf` / `#97cedb` / `#213a40` / `#a7d7e1` / `#0c1d21` |
| **Sage** | `#4a7352` / `#3e6146` / `#e3ede2` / `#3d6244` / `#ffffff` | `#8dbe97` / `#a2cdaa` / `#2c3a2f` / `#afd6b7` / `#14201a` |
| **Plum** | `#8b4a7b` / `#763e68` / `#f2e3ee` / `#773f69` / `#ffffff` | `#d49bc4` / `#ddafd0` / `#3f2d3a` / `#e6bcda` / `#221420` |
| **Slate** | `#4a5866` / `#3b4753` / `#e4e8ec` / `#3e4b58` / `#ffffff` | `#b3c0cc` / `#c7d1da` / `#323940` / `#cfd9e2` / `#161b20` |

In dark mode the accent is light, so text on accent fills is **dark ink** (`--accent-text`), never white. Existing
feature CSS that uses `var(--accent)` as a **text** colour switches to `--accent-ink`. `--accent` passes 4.5:1 on
`--bg`, `--surface`, `--sidebar` and `--accent-soft`, but only 4.33 on `--surface-3`.

### 3.5 Semantic colours

| Token | Light | Dark | Use |
|---|---|---|---|
| `--success` / `--success-soft` | `#39703f` / `#e4efe1` | `#86c48d` / `#1f3122` | Connected, Ready, saved checks (light deepened in revision 2 so it holds 4.5:1 on `--surface-3`, the hover fill) |
| `--warning` / `--warning-soft` | `#8f5b00` / `#f7ebd3` | `#e3b45c` / `#3a2e17` | Reconnect needed, Needs re-index, 90% meters, notes |
| `--danger` / `--danger-soft` | `#b02a3a` / `#f8e1e3` | `#f2899a` / `#43222a` | errors, Failed, destructive buttons, 100% meters |
| `--danger-hover` | `#93202e` | `#f6a3b0` | destructive button hover |
| `--danger-text` *(new)* | `#ffffff` | `#2a0c12` | text on a `--danger` fill |
| `--info` / `--info-soft` | `#2f6490` / `#e1ecf5` | `#8db9e0` / `#1f2e3c` | Indexing…, info notices |

A status is never shown by colour alone: there is always a word, plus an icon or dot (6.7).

### 3.6 Fixed colours: file types, avatar, brand, switch thumb

| Token (fg / soft) | Light | Dark | For |
|---|---|---|---|
| `--tag-red` | `#a8392c` / `#f7e1db` | `#f0a091` / `#43251f` | PDF |
| `--tag-blue` | `#2f6490` / `#e0eaf3` | `#93bde2` / `#20303e` | DOCX |
| `--tag-green` | `#3d7543` / `#e2eedf` | `#92c99a` / `#213325` | XLSX, CSV |
| `--tag-ochre` | `#8a5a06` / `#f5e8cc` | `#e2b862` / `#3b2f18` | PPTX |
| `--tag-plum` | `#81466f` / `#f1e2ec` | `#dba6cb` / `#3c2937` | images, HTML, JSON |
| `--tag-ink` | `#5a5249` / `#ebe6dc` | `#cec6b9` / `#3a3631` | MD, TXT, anything else |
| `--avatar-bg` / `--avatar-ink` | `#e9dccd` / `#5b3a24` | `#4a3a2e` / `#f4dcc8` | initials avatar (one warm colour for everyone) |
| `--brand` / `--brand-text` | `#a94d2b` / `#ffffff` | `#e28c69` / `#24140d` | the "R" mark and the login glow; always clay |
| `--switch-thumb` | `#fffdf9` | `#fffdf9` | the switch knob when off, both themes |
| `--switch-thumb-on` *(new, rev. 2)* | `#fffdf9` | `var(--accent-text)` | the knob when on: in dark mode the accent's own dark ink, ≥ 6.9:1 on the light accent track |

The old `docs-tag-accent` tone (used for images and HTML) maps to **plum**, so file colours never change with the
accent.

### 3.7 Shadows, overlay and radii

| Token | Light (umber `rgba(51,38,25,a)`) | Dark (black) |
|---|---|---|
| `--shadow-xs` | `0 1px 2px .05` | `0 1px 2px .28` |
| `--shadow-sm` | `0 1px 2px .06, 0 3px 8px -3px .08` | `0 1px 2px .30, 0 3px 8px -3px .35` |
| `--shadow` | `0 2px 4px .04, 0 12px 28px -10px .16` | `0 2px 4px .25, 0 14px 30px -10px .55` |
| `--shadow-lg` | `0 4px 10px .05, 0 28px 64px -18px .30` | `0 4px 12px .30, 0 30px 70px -18px .70` |
| `--shadow-composer` *(new)* | `0 1px 2px .04, 0 10px 32px -14px .22` | `0 1px 2px .25, 0 12px 34px -14px .65` |
| `--overlay` *(new; replaces the `rgba` in `chat.css`)* | `rgba(42,33,24,.34)` + `backdrop-filter: blur(3px) saturate(.9)` | `rgba(10,8,6,.58)` |

The radii are below. The existing names keep their roles with new values.

| Token | Value | For |
|---|---|---|
| `--radius-xs` *(new)* | 6px | citation chips, kbd, inline code, tags |
| `--radius-sm` | 8px (was 6) | buttons, small controls |
| `--radius` | 12px (was 10) | inputs, rows, notes, code blocks, menus' inner items use 10 |
| `--radius-lg` | 16px (was 14) | cards, menus, tiles, file and image cards, dropzone |
| `--radius-xl` *(new)* | 22px | composer, dialogs, Settings, bottom sheets (top corners) |
| `--radius-pill` *(new)* | 999px | badges, pills, the sources toggle, avatar, round buttons |

The user bubble is `20px 20px 6px 20px` (a tail corner at the bottom right). The theme previews are 9px; the file tag
is 5px.

### 3.8 Spacing, motion and chat type

- **Spacing** (4-pt): `--space-1` 4, `-2` 8, `-3` 12, `-4` 16, `-5` 24, `-6` 32, plus new `-7` 40 and `-8` 56.
- **Motion:**
  - Easings: `--ease-out: cubic-bezier(.22,.8,.24,1)` and `--ease-in-out: cubic-bezier(.45,0,.25,1)`.
  - Durations: `--dur-1: 120ms` (hover, press, colour), `--dur-2: 180ms` (menus, switches, segmented controls) and
    `--dur-3: 260ms` (dialogs, sheets, drawers).
- **Chat type:** `--chat-font-size` / `--chat-line-height` are 15.5px / 1.66 (small), **16.5px / 1.68 (default)**
  and 18px / 1.7 (large). The user's bubble follows with `--chat-bubble-size` *(new, rev. 2)*: 15px, **16px** and
  17.5px.

### 3.9 The token block for `global.css` (copy as is)

```css
:root {
  color-scheme: light;
  --font-serif: "RAG Serif", "RAG Serif Fallback", "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif;
  --font-sans: "RAG Sans", "RAG Sans Fallback", system-ui, -apple-system, "Segoe UI", "Helvetica Neue", Arial,
    "Noto Sans", sans-serif;
  --font-mono: ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;

  --sidebar: #f3f0e8; --bg: #faf8f3; --surface: #fffdf9; --surface-2: #f3f0e8; --surface-3: #eae5da;
  --bubble: #efeae0; --code-bg: #f4f0e7;
  --border: #e5dfd3; --border-strong: #d3cab9; --border-control: #8f8476; --rule: #b8ad9b;
  --text: #2a2520; --text-2: #5a5249; --text-3: #6e655a;
  --accent: #a94d2b; --accent-hover: #93421f; --accent-soft: #f7e8e0; --accent-ink: #9a4423; --accent-text: #ffffff;
  --brand: #a94d2b; --brand-text: #ffffff;
  --success: #39703f; --success-soft: #e4efe1; --warning: #8f5b00; --warning-soft: #f7ebd3;
  --danger: #b02a3a; --danger-hover: #93202e; --danger-text: #ffffff; --danger-soft: #f8e1e3;
  --info: #2f6490; --info-soft: #e1ecf5;
  --tag-red: #a8392c; --tag-red-soft: #f7e1db; --tag-blue: #2f6490; --tag-blue-soft: #e0eaf3;
  --tag-green: #3d7543; --tag-green-soft: #e2eedf; --tag-ochre: #8a5a06; --tag-ochre-soft: #f5e8cc;
  --tag-plum: #81466f; --tag-plum-soft: #f1e2ec; --tag-ink: #5a5249; --tag-ink-soft: #ebe6dc;
  --avatar-bg: #e9dccd; --avatar-ink: #5b3a24; --switch-thumb: #fffdf9; --switch-thumb-on: #fffdf9;
  --radius-xs: 6px; --radius-sm: 8px; --radius: 12px; --radius-lg: 16px; --radius-xl: 22px; --radius-pill: 999px;
  --shadow-xs: 0 1px 2px rgba(51, 38, 25, 0.05);
  --shadow-sm: 0 1px 2px rgba(51, 38, 25, 0.06), 0 3px 8px -3px rgba(51, 38, 25, 0.08);
  --shadow: 0 2px 4px rgba(51, 38, 25, 0.04), 0 12px 28px -10px rgba(51, 38, 25, 0.16);
  --shadow-lg: 0 4px 10px rgba(51, 38, 25, 0.05), 0 28px 64px -18px rgba(51, 38, 25, 0.3);
  --shadow-composer: 0 1px 2px rgba(51, 38, 25, 0.04), 0 10px 32px -14px rgba(51, 38, 25, 0.22);
  --overlay: rgba(42, 33, 24, 0.34);
  --focus-ring: 0 0 0 2px var(--bg), 0 0 0 4px var(--accent);
  --space-1: 4px; --space-2: 8px; --space-3: 12px; --space-4: 16px; --space-5: 24px; --space-6: 32px;
  --space-7: 40px; --space-8: 56px;
  --ease-out: cubic-bezier(0.22, 0.8, 0.24, 1); --ease-in-out: cubic-bezier(0.45, 0, 0.25, 1);
  --dur-1: 120ms; --dur-2: 180ms; --dur-3: 260ms;
  --chat-font-size: 16.5px; --chat-line-height: 1.68; --chat-bubble-size: 16px;
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --sidebar: #1e1c1a; --bg: #262421; --surface: #2f2c28; --surface-2: #2a2724; --surface-3: #3a3631;
  --bubble: #37332e; --code-bg: #1c1a18;
  --border: #3a3631; --border-strong: #4e4842; --border-control: #857b70; --rule: #605850;
  --text: #f2ede4; --text-2: #cec6b9; --text-3: #a89f92;
  --accent: #e28c69; --accent-hover: #eba083; --accent-soft: #44302a; --accent-ink: #f2b497; --accent-text: #24140d;
  --brand: #e28c69; --brand-text: #24140d;
  --success: #86c48d; --success-soft: #1f3122; --warning: #e3b45c; --warning-soft: #3a2e17;
  --danger: #f2899a; --danger-hover: #f6a3b0; --danger-text: #2a0c12; --danger-soft: #43222a;
  --info: #8db9e0; --info-soft: #1f2e3c;
  --tag-red: #f0a091; --tag-red-soft: #43251f; --tag-blue: #93bde2; --tag-blue-soft: #20303e;
  --tag-green: #92c99a; --tag-green-soft: #213325; --tag-ochre: #e2b862; --tag-ochre-soft: #3b2f18;
  --tag-plum: #dba6cb; --tag-plum-soft: #3c2937; --tag-ink: #cec6b9; --tag-ink-soft: #3a3631;
  --avatar-bg: #4a3a2e; --avatar-ink: #f4dcc8;
  --switch-thumb-on: var(--accent-text); /* resolves per accent: the accent blocks below set --accent-text on :root */
  --shadow-xs: 0 1px 2px rgba(0, 0, 0, 0.28);
  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.3), 0 3px 8px -3px rgba(0, 0, 0, 0.35);
  --shadow: 0 2px 4px rgba(0, 0, 0, 0.25), 0 14px 30px -10px rgba(0, 0, 0, 0.55);
  --shadow-lg: 0 4px 12px rgba(0, 0, 0, 0.3), 0 30px 70px -18px rgba(0, 0, 0, 0.7);
  --shadow-composer: 0 1px 2px rgba(0, 0, 0, 0.25), 0 12px 34px -14px rgba(0, 0, 0, 0.65);
  --overlay: rgba(10, 8, 6, 0.58);
}
:root[data-accent="clay"], [data-accent="clay"] {
  --accent: #a94d2b; --accent-hover: #93421f; --accent-soft: #f7e8e0; --accent-ink: #9a4423; --accent-text: #ffffff; }
:root[data-theme="dark"][data-accent="clay"], [data-theme="dark"] [data-accent="clay"] {
  --accent: #e28c69; --accent-hover: #eba083; --accent-soft: #44302a; --accent-ink: #f2b497; --accent-text: #24140d; }
:root[data-accent="ocean"], [data-accent="ocean"] {
  --accent: #22697a; --accent-hover: #1b5765; --accent-soft: #ddedf0; --accent-ink: #1d5c6b; --accent-text: #ffffff; }
:root[data-theme="dark"][data-accent="ocean"], [data-theme="dark"] [data-accent="ocean"] {
  --accent: #7cc0cf; --accent-hover: #97cedb; --accent-soft: #213a40; --accent-ink: #a7d7e1; --accent-text: #0c1d21; }
:root[data-accent="sage"], [data-accent="sage"] {
  --accent: #4a7352; --accent-hover: #3e6146; --accent-soft: #e3ede2; --accent-ink: #3d6244; --accent-text: #ffffff; }
:root[data-theme="dark"][data-accent="sage"], [data-theme="dark"] [data-accent="sage"] {
  --accent: #8dbe97; --accent-hover: #a2cdaa; --accent-soft: #2c3a2f; --accent-ink: #afd6b7; --accent-text: #14201a; }
:root[data-accent="plum"], [data-accent="plum"] {
  --accent: #8b4a7b; --accent-hover: #763e68; --accent-soft: #f2e3ee; --accent-ink: #773f69; --accent-text: #ffffff; }
:root[data-theme="dark"][data-accent="plum"], [data-theme="dark"] [data-accent="plum"] {
  --accent: #d49bc4; --accent-hover: #ddafd0; --accent-soft: #3f2d3a; --accent-ink: #e6bcda; --accent-text: #221420; }
:root[data-accent="slate"], [data-accent="slate"] {
  --accent: #4a5866; --accent-hover: #3b4753; --accent-soft: #e4e8ec; --accent-ink: #3e4b58; --accent-text: #ffffff; }
:root[data-theme="dark"][data-accent="slate"], [data-theme="dark"] [data-accent="slate"] {
  --accent: #b3c0cc; --accent-hover: #c7d1da; --accent-soft: #323940; --accent-ink: #cfd9e2; --accent-text: #161b20; }
:root[data-chat-font="small"] { --chat-font-size: 15.5px; --chat-line-height: 1.66; --chat-bubble-size: 15px; }
:root[data-chat-font="large"] { --chat-font-size: 18px; --chat-line-height: 1.7; --chat-bubble-size: 17.5px; }
```

Base rules that go with the tokens:

- `body` uses `var(--font-sans)` at **15px/1.45** (up from 14px) on `--bg`, with antialiasing.
- `::selection` is `color-mix(in srgb, var(--accent) 24%, transparent)`.
- `a` is `--accent-ink`, underlined on hover with `text-underline-offset: 3px`.
- `:focus-visible { outline: none; box-shadow: var(--focus-ring) }`.
- `kbd` is a sans 0.86em/600 key on `--surface` with a 1px `--border` (2px at the bottom), radius 5, colour `--text-2`.

---

## 4. Typography

### 4.1 Fonts (FOUNDATION: `src/assets/fonts/`, `src/styles/fonts.css`)

| File | From | Axes kept | Size | sha256 |
|---|---|---|---|---|
| `RAGSerif-Roman.woff2` | `adobe-fonts/source-serif` tag `4.005R`, commit `2823e993c53fca27c5c8749f529b56a5a7c77b6b`, `WOFF2/VAR/SourceSerif4Variable-Roman.ttf.woff2` (sha256 `940a76eda1388de39d38c8e7a79bf6ea058a387faee0a9f33c8d25c6ba05e1be`) | wght 400–700, opsz 16–60 | 147.1 KB | `826af310b09211dafbbbff66b70eae2b1fbb2d81990e03849f441828f204e9fd` |
| `RAGSerif-Italic.woff2` | same tag, `WOFF2/VAR/SourceSerif4Variable-Italic.ttf.woff2` (sha256 `9d28b5749a1ad096a295cb607c521bd1af4cd9979b6f37332daf70143149fb44`) | wght 400–700, opsz 16–60 | 118.8 KB | `135f790e2e431a371cb701d3be16e8c65a6e12398dfe34e3d70809c1e7895419` |
| `RAGSans-Upright.woff2` | `adobe-fonts/source-sans` branch `release`, commit `87b37a2daaed80fcb8e8ccb0085c4d72ddade12e`, `WOFF2/VF/SourceSans3VF-Upright.ttf.woff2` (sha256 `5f16566f7a40d39b339ad26be151fa5a1ab1f0c2574c7a2e619765584a1acbd8`) | wght 200–900 | 56.6 KB | `c3bc9c38f264451683d03cb9d0ac187f81b1db22617727db9fd140d8b44064b3` |

**Total 323 KB in 3 files** (the budget is 4 files and about 400 KB). The licences, unchanged, sit next to them:
`LICENSE-SourceSerif4.md` (sha256 `75784a295293a8992f5a8d99210566e0064a012e6dab6731305e3787f15896c7`) and
`LICENSE-SourceSans3.md` (sha256 `56af9b9c6715597e458284a474dc118a50a4150e9d547c70f7b4a33c3e6a9328`), both SIL OFL 1.1.

**How to make them:**

1. Download the three upstream files and both licences from the pinned commits; check each sha256.
2. Run appendix A's `subset_fonts.py` in a **scratch** venv (Python 3.13, `fonttools==4.66.1`, the same version and
   wheel hash as `backend/requirements.txt`, and `brotli==1.1.0`; macOS universal2 cp313 wheel sha256
   `8bf32b98b75c13ec7cf774164172683d6e7891088f6316e54425fde1efc276d5`). Nothing is added to the app's dependencies.
3. Run it as `python -I subset_fonts.py <upstream_dir> <out_dir>`. The output is deterministic: with those versions
   the three sha256 values above must come out exactly. Record sources, commits and every sha256 in a comment at the
   top of `fonts.css`.

**What the script does:**

- **Subset to Latin:** Basic Latin, Latin-1, Latin Extended-A, Romanian, the general punctuation block, €, ₹, ™,
  arrows, −, ⌘ (sans only), ✓ and the fi/fl ligatures.
- **Keep the layout features the design uses:** kern, liga, the fraction features, sups and subs, tnum, pnum, lnum,
  onum, case, zero, smcp and c2sc.
- **Limit the serif's axes:** wght to 400–700 (the design uses 400–700 serif) and opsz to 16–60 (serif text is never
  under 15.5 px).
- **Rename the families:** "Source" is an OFL **Reserved Font Name** and a subset is a Modified Version, so the name
  table becomes **RAG Serif** / **RAG Sans**. Copyright, trademark and licence records stay unchanged.

**`fonts.css`:** three `@font-face` rules for the bundled files, "RAG Serif" normal and italic (`font-weight: 400
700`) and "RAG Sans" normal (`font-weight: 200 900`), plus the five fallback faces below (appendix B). Each has `font-display: swap` and the `unicode-range` of the subset (appendix B), so
Devanagari and every other script go straight to the system fonts (users write Hindi and Nepali). There is no sans
italic file: the UI never sets sans italic, and a stray `em` in sans text gets the browser's oblique. Serif weights
outside 400–700 clamp, so **use only 400–700 for serif text**.

**No layout shift (revision 2):** fixed heights keep the top bar, composer controls, rows and buttons still, but text
blocks (answer prose, bubbles, sidebar rows, the login hero, the empty-state greeting) re-wrap when the web font
replaces a fallback with other metrics, and a long thread opened at the bottom would jump. So `fonts.css` also declares
**metric-matched fallback faces** on local system fonts, placed second in `--font-serif` and `--font-sans` (3.9):

| Face | `src` | `size-adjust` | `ascent-override` | `descent-override` | `line-gap-override` |
|---|---|---|---|---|---|
| "RAG Serif Fallback", 400 normal | `local("Georgia")` | 105.33% | 98.36% | 31.80% | 0% |
| "RAG Serif Fallback", 700 normal (used for 600 and up) | `local("Georgia Bold"), local("Georgia-Bold")` | 88.08% | 117.62% | 38.03% | 0% |
| "RAG Serif Fallback", 400 italic | `local("Georgia Italic"), local("Georgia-Italic")` | 93.94% | 110.28% | 35.66% | 0% |
| "RAG Sans Fallback", 400 normal | `local("Arial"), local("ArialMT")` | 93.68% | 106.74% | 34.80% | 0% |
| "RAG Sans Fallback", 700 normal (used for 600 and up) | `local("Arial Bold"), local("Arial-BoldMT")` | 89.51% | 111.73% | 36.42% | 0% |

- **How the numbers were made:** appendix C's `fallback_metrics.py`, run in the same scratch venv as appendix A, from
  the three bundled files and macOS's Georgia and Arial. `size-adjust` is the ratio of the English-weighted average
  advance width (the method Next.js and Capsize use); the overrides are the bundled font's `hhea` metrics divided by
  it.
- **Weights and optical size:** the serif regular is measured at wght 400 and opsz 16.5 (the answer body), the bold
  at wght 600 and opsz 22 (headings), the italic at wght 400 and opsz 18 (the empty-state sub-line); the sans at 400
  and 600.
- **Coverage:** Georgia and Arial exist on macOS, iOS and Windows. Where they don't (Linux, Android), the `local()`
  face fails and the stack goes on to the next font, unadjusted, as before.
- **Measured:** in Chromium on macOS, on the revision 2 copy of the mockup (`font_shift.mjs`), with the woff2 files
  blocked the thread's height differs from the web-font layout by 1.2% at 1440px and 0.9% at 390px on the revision 1
  stack, and by 0.0% at both widths with the fallback faces.
- **Re-run when the font files change**, and paste the new values into appendix B. FOUNDATION may refine the numbers
  from a cold-cache comparison but must keep the five faces.

### 4.2 Who uses which face

| Serif (`--font-serif`) | Sans (`--font-sans`) | Mono |
|---|---|---|
| the assistant's Markdown answer (body, headings, lists, blockquotes); display titles (empty-state greeting, login hero and card title, Settings and panel titles, Documents title, menu titles, admin page and card titles, dialog titles); the conversation title in the top bar; big figures (usage tiles, cost, admin tiles); source snippets; the font-size preview; brand name and "R" mark | everything else: UI, buttons, inputs, menus, the user's bubble, the composer, tables (in answers too), citation chips, badges, meta lines, the sidebar, the documents panel, the code block header | code blocks and inline code |

### 4.3 Type scale

| Role | Face | Size / line-height | Weight | Tracking | Notes |
|---|---|---|---|---|---|
| Login hero | serif | 54 / 1.05 (38 on phones) | 500; the `em` word 450 italic in `--accent-ink` | -0.025em | "Ask your documents *anything.*" |
| Empty-state greeting | serif | 42 / 1.1 (32 on phones) | 450 (the username italic, `--accent-ink`) | -0.024em | `text-wrap: balance`. Uses the username, never a guessed first name |
| Empty-state sub-line | serif italic | 18 / 1.5 (16.5 on phones) | 400 | | `--text-2`, max 46ch |
| Login card title "Welcome back" | serif | 36 / 1.1 (26 on phones) | 500 | -0.02em | |
| Admin page title | serif | 34 (28 on phones) | 500 | -0.02em | |
| Settings title; panel `h2` | serif | 25 and 26 (22 on phones) | 600 | -0.015em | |
| Usage, cost and admin figures | serif | 27 / 1.2 | 600 | -0.015em | `lining-nums tabular-nums`; the unit after is sans 14.5 `--text-3` |
| Account name | serif | 23 | 600 | -0.01em | |
| Dialog title, admin card title | serif | 22 and 21 | 600 | -0.012em | |
| Documents title, empty-docs title | serif | 19 | 600 | -0.01em | |
| Brand name, menu title | serif | 18 | 600 | -0.01em | the "R" mark is serif italic 20/600 |
| Top-bar conversation title | serif | 15.5 | 500 | | `--text-2`, ellipsis |
| **Answer body** | serif | `--chat-font-size` / `--chat-line-height` (16.5/1.68) | 400; `strong` 650 | | `font-optical-sizing: auto`, `lining-nums` |
| Answer `h1` / `h2` / `h3` / `h4` | serif | 1.6em / 1.4em / 1.15em / 1em, line-height 1.25 | 600 | -0.012em | margins: h2 1.3em top, 0.5em bottom |
| Source snippet, font preview | serif | 15 / 1.55; the preview at `--chat-font-size` | 400 | | |
| Composer input | sans | 16.5 / 1.5 | 400 | | ≥ 16 avoids iOS zoom |
| User bubble | sans | `--chat-bubble-size` (15, **16**, 17.5) / 1.55 | 400 | | `white-space: pre-wrap`; follows the chat font size setting |
| Model button | sans | 16.5 (16 on phones) | 600 | -0.005em | |
| Settings group title (`h3`) | sans | 16 | 650 | | |
| File card name, plugin name | sans | 15.5 | 650 | | |
| UI default: buttons, menu items, sidebar rows, docs row names, inputs | sans | 15 / 1.45 | buttons 600, rows 400 (active 600), names 600 | | |
| Answer tables | sans | 15 / 1.4 | 400; first column 600 `--text` | | `tabular-nums` |
| Small buttons, descriptions, menu descriptions, meta | sans | 14 / 13.5 | 600 / 400 | | |
| Docs meta, hints, chart axis | sans | 13 / 12 | 400 | | |
| Group labels (TODAY, MY DOCUMENTS, CREATE), table headers | sans | 12 (headers 12.5) | 650 | 0.07em (0.06em) | uppercase, `--text-3` |
| Badges | sans | 12.5 | 600 | | |
| Citation chip | sans | 12 | 700 | | 19px tall |
| File tag label | sans | 9.5 (10.5 large, 7 small) | 750 | 0.04em | |
| Code | mono | 13.5 / 1.65 (inline 0.82em) | 400 | | |

**Measure:** the thread is at most **740px** wide. With the 30px assistant mark and its 16px gap, answer prose runs
at about 690px, roughly 75–80 characters at the default size. That is the ceiling: don't widen the thread when the
documents panel is collapsed. The composer is at most 776px wide.

---

## 5. Layout

- **Grid:** `260px | minmax(0,1fr) | 360px`, `height: 100dvh`, `overflow: clip`, and the page never scrolls (SPEC).
  - With the documents panel collapsed (U17) the third track is `0` and the panel is `hidden`. Animate
    `grid-template-columns` over `--dur-3`, or not at all.
  - Under 1100px the grid is `1fr | 340px` and the sidebar becomes a fixed drawer (`min(300px, 86vw)`, `--shadow-lg`,
    slides over `--dur-3` with the `--overlay` scrim).
  - Under 800px the grid is one column and the documents panel becomes a fixed slide-over from the right
    (`min(380px, 92vw)`). The top bar's Documents toggle opens it.
- **Header rows align across the three columns:** 60px everywhere (the sidebar header, the top bar, the documents
  header), 56px on phones.
- **Gutters:** thread padding is `18px 28px 28px` (`10px 16px 24px` on phones). The composer area is `0 28px 14px`
  (`0 10px 10px` on phones), and its bottom padding is `max(…, env(safe-area-inset-bottom))`. Messages are 30px apart.
- **Fades:**
  - An 18px `--bg` gradient hangs under the top bar, so the thread fades as it scrolls under it.
  - A 24px gradient rises above the composer.
  - In Settings, a 10px `--surface` gradient sits under the sticky panel header. The body starts 10px lower, so at
    rest the fade covers nothing.
- **z-index scale:**

  | Layer | z-index |
  |---|---|
  | top bar | 20 |
  | menus | 40 |
  | drawers and slide-over | 60 |
  | bottom-sheet scrim | 65 |
  | bottom sheets | 70 |
  | modal backdrop | 100 |

  **Menus render in a portal on `<body>`** (revision 2): the panel, and under 800px the bottom sheet with its own scrim
  (`ui-menu-scrim`), are children of `<body>` at the z-indexes above, positioned from the trigger's rect. So no
  ancestor's stacking context, `transform` or `backdrop-filter` (the top bar, the composer's fade) can trap a sheet or
  put its scrim under the top bar. (The mockup still nests them inside the trigger; the app doesn't.)

---

## 6. Components

Classes: unprefixed primitives keep their names, and new primitives are `ui-*` (both in `global.css`, FOUNDATION).
Feature classes use their area prefix. "Mock" names the reference class in `paper-clay.css`; section 12 maps them.

### 6.1 Buttons (`.btn`)

- **Default (secondary):**
  - Size and shape: 38px tall, padding 0 16px, radius `--radius-sm`, gap 7px between icon and label, `white-space:
    nowrap`.
  - Colour and type: `--surface` fill, 1px `--border-strong` border, `--text`, 15px/600.
  - States: hover fills `--surface-3`; `:active` moves down 0.5px.
- **Variants:**
  - `.btn-primary`: `--accent` fill and border, `--accent-text`, `--shadow-xs`; hover `--accent-hover`.
  - `.btn-ghost`: transparent, `--text-2`; hover `--surface-3` and `--text`.
  - `.btn-danger`: `--danger` fill, `--danger-text`; hover `--danger-hover`.
- **Sizes:** `.btn-sm` 32px/14px (padding 0 12); `.btn-xs` *(new)* 28px/13.5px (radius 7); `.btn-icon` 36×36 (32 with
  `.btn-sm`); `.btn-pill` *(new)* radius pill.
- **Disabled** (`:disabled` or `[aria-disabled=true]`): opacity 0.5, `cursor: not-allowed`, no hover change.
- **Destructive buttons say what they do**, with the count: "Delete 48 chats", "Delete user", "Log out others".
- Icons are 15px (`sm`) or 18px stroke icons in `currentColor`.

### 6.2 Inputs (`.input`, `.select`, `.textarea`, `.field`)

- **Inputs:** 42px tall (46 on the login form), padding 0 14px, radius `--radius`, `--surface` fill, a 1px
  `--border-control` border, 15px text, placeholder `--text-3`.
  - Focus: `--accent` border plus `0 0 0 3px var(--accent-soft)`.
  - Invalid (`aria-invalid=true`): `--danger` border, and a `--danger-soft` halo on focus.
  - Disabled: `--surface-2` fill, `--text-3`.
- **Selects:** `appearance: none`, with a 5px chevron drawn in `--text-2` 20px from the right.
- **Textareas:** min height 96, padding 10 14, line-height 1.5, vertical resize.
- **Fields:**
  - `.field`: a column with a 7px gap.
  - `.field-label`: 14px/600 `--text-2`.
  - `.field-hint`: 13.5px `--text-3`.
  - `.error-text`: 13.5px/600 `--danger` with a 13px alert icon, linked by `aria-describedby`.
- **Password toggle** (`.ui-password` wrapping the input with `.ui-password-toggle`), on login, Change password and
  the admin user form:
  - The button is 36px, placed 3px from the field's right edge (5px on the 46px login field), radius 9.
  - Its icon is eye / eye-off in `--text-2`, with `aria-label` "Show password" / "Hide password" and `aria-pressed`.
  - It toggles the input's `type` and keeps focus and the caret.
  - The input gets 46px of right padding.

### 6.3 Checkbox, radio, switch

- **`.ui-check` and `.ui-radio`** *(new; replace `docs-check` and `admin-check` visuals)*: native inputs with
  `appearance: none`.
  - Size: 18px, with a 1.5px `--border-control` border on `--surface`; the checkbox radius is 5, the radio is a
    circle.
  - Checked checkbox: `--accent` fill with an `--accent-text` tick (2px, rotated L).
  - Checked radio: a 5px `--accent` border.
  - Disabled: opacity 0.45 on `--surface-2`.
- **`.switch`:**
  - Size: 40×24. The track is `--border-control` when off and `--accent` when on.
  - The thumb is 18px `--switch-thumb` (off) or `--switch-thumb-on` (on) with a `0 1px 3px rgba(51,38,25,.3)` shadow,
    and moves 16px over `--dur-2 --ease-out`. In dark mode the "on" thumb is the accent's dark ink, so it stands out
    from the light accent track by 6.9:1 or more (3:1 needed); the off thumb on `--border-control` is 3.6 (light) and
    4.1 (dark).
  - The input stays the hit target (`inset: 0`, opacity 0) with `role="switch"`. Focus puts `--focus-ring` on the
    track.

### 6.4 Segmented control (`.ui-segmented`, `.ui-segment`) *(new)*

- **Track:** padding 3, gap 2, radius `--radius`, `--surface-2` with a 1px `--border`.
- **Segments:** 34px tall, padding 0 14px, radius 9, 14.5px/600 `--text-2`; hover `--text`.
- **Selected:** `--surface` fill, `--text`, with `--shadow-sm` plus an inset 1px `--border-control` ring (≥ 3:1
  against the fill and the track in both themes, 11).
- **Variant and layout:** `.ui-segmented-sm` has 28px segments at 13.5px. Segments are `flex: 1 1 0`, so a full-width
  control splits evenly.
- **Contents:** each segment can carry a 15px icon or the "Aa" serif specimen (the font-size control, `--text-3`,
  turning `--accent-ink` when selected).
- **Semantics:** a radio group; the selection shows by fill and a 3:1 ring, never by colour alone.

### 6.5 Badges, tags and the status vocabulary

- **`.badge`:** 22px tall pill, padding 0 9px, 12.5px/600, gap 5.
  - Neutral: `--surface-2`, `--text-2`, with an inset 1px `--border` ring.
  - Tones `-info`, `-success`, `-warning`, `-danger` and `-accent` *(new)*: the matching soft fill and colour, no
    ring.
  - A spinner (11px) or a 13px icon may lead.
- **Document status (one vocabulary everywhere):**

  | Status | Badge | Extra |
  |---|---|---|
  | pending | neutral "Queued" | |
  | processing | info "Indexing…" with a spinner | |
  | ready | no badge in the panel; "✓ Ready" in success colour on chat cards and attachment chips | |
  | stale | | chat cards read "Needs re-index" (warning, alert icon) instead of "Ready"; admin shows only that badge |
  | failed | danger "Failed" | the error text below, plus a "Retry" link-button |
  | stale | warning "Needs re-index" | a "Re-index" link-button |

  Chat cards use the same words ("Queued", not "Queued for indexing").
- **Model tags** (`.chat-model-tag`): 19px tall, radius 5, `--surface-2` with an inset `--border` ring, 12px/600
  `--text-2` ("Files", "Images"). The "Default" mark is a `badge-accent`.
- **File tag** (`.docs-tag`, the `FileTag` component, used by chat cards too) is a sheet of paper:
  - Shape: 34×40 (large 40×48, small 22×26), radius 5, with a 10px folded corner (`clip-path` plus a
    gradient triangle).
  - Lines: two 2px "text lines" at 30% of the tone.
  - Label: the type at the bottom, 9.5px/750, in `--tag-*`, on `--tag-*-soft`.
  - Tones: PDF red, DOCX blue, XLSX/CSV green, PPTX ochre, images/HTML/JSON plum, the rest ink.
  - An image document shows its `thumb` rendition instead, 34×40 with radius 7, an inset `--border` ring and
    `cursor: zoom-in`.

### 6.6 Meter (`Meter`, `.ui-meter`) *(new)*

- **Label row:** label 13.5px `--text-2` on the left; the value on the right in `--text` 600 tabular, with "/ limit"
  in `--text-3` 400.
- **Bar:** 6px tall (4px in the documents footer and 5px in admin tables), pill radius, `--surface-3` track,
  `--accent` fill.
- **Thresholds:** `.ui-meter-warning` (`--warning`) from 90%, `.ui-meter-danger` (`--danger`) at 100%. **Not colour
  alone** (revision 2): the value row then starts with a 13px icon (alert triangle for warning, alert circle for
  danger) and the words "Almost used up" or "Limit reached" in the tone colour, 13px/650, before the numbers; the
  `aria-valuetext` ends with the same words. This also keeps a full meter distinct from a Clay or Plum accent fill
  in dark mode, whose hue is close to danger.
- **No limit:** no bar; the value reads "3.9M · Unlimited" ("Unlimited (admin)" in Settings → Usage).
- **Semantics:** `role="meter"` with `aria-valuetext` ("412,380 of 1,000,000 tokens").

### 6.7 Status line (`.ui-status`) *(new)*

A word led by a 7px dot, 13.5px/600:

| Variant | Colour | Mark |
|---|---|---|
| connected | `--success` | the dot has a 3px `--success-soft` halo |
| attention | `--warning` | the dot has a halo |
| neutral | `--text-3` | dot |
| off | `--text-2` at 500 | a 13px **lock** icon instead of a dot |
| unset | `--text-2` at 500 | a 9px **dashed circle** |

Used by the Plugins menu, the connection cards and anywhere a plugin or connection state is shown.

### 6.8 Avatar (`Avatar`, `.ui-avatar`) *(new)*

- **Shape:** a 32px circle (40 in menu heads, 56 in Settings → Account) on `--avatar-bg`.
- **Text:** `--avatar-ink` 13px/700 (15 and 20 in the larger sizes), tracking 0.02em, with an inset 1px ring of the
  ink at 14%.
- **Initials and semantics:** up to two initials (functional brief 12); `aria-hidden`.
- **The top-bar button:** a 40px circle around the 32px avatar. Hover adds a 3px `--surface-3` ring; a 90% usage
  warning adds an 8px `--warning` dot at the top right with a 2px `--bg` ring.

### 6.9 Menus and popovers (`Menu`, `.ui-menu`) *(new)*

- **Portal and actions** (revision 2): the panel renders in a portal on `<body>` (5), placed from the trigger's rect
  (fixed; updated on resize and scroll; flipped when there is no room). An item's action runs synchronously in the
  activating click or keydown handler, right after focus returns to the trigger, so a file picker opens inside the
  gesture.
- **Panel:**
  - Shape: `--surface`, 1px `--border`, radius `--radius-lg`, `--shadow-lg`, padding 6.
  - Size: minimum width 248px, maximum height `min(560px, 100dvh - 96px)`, scrolls when taller.
  - Position: 10px from its trigger (8px for the "+" menu, which opens upward).
  - Enter: fade with a 4px rise and `scale(.985)` from the trigger corner over `--dur-2 --ease-out`
    (`transform-origin` follows the placement). No exit animation.
- **Item** (`.ui-menu-item`):
  - Size: minimum 40px tall (44 on coarse pointers), padding 8px 10px, radius 10, gap 12.
  - Text: 15px/500 `--text`. The leading icon is 18px `--text-2`, or a 34px icon tile (`.ui-menu-icon`: radius 10,
    `--sidebar` fill, 1px `--border`; 40px "large" on `--surface` with `--shadow-xs` for plugins).
  - Hover, or roving focus: `--surface-3`, and the description switches from `--text-3` to `--text-2`, so it stays
    above 4.5:1 on the hover fill.
  - Keyboard focus also draws a 2px `--accent` outline inset by 2px.
- **Inside the item:**
  - Description (`.ui-menu-desc`): 13.5px/1.35, under the title.
  - Trailing (`.ui-menu-end`): `--text-3` 13.5px, holding a chevron, kbd or count.
- **Groups and separators:**
  - Groups are `role="group"` labelled by their visual label; a horizontal group (the theme row, the format chips)
    moves with Left and Right, and Up and Down go through every item in DOM order.
  - Content that isn't an item (heads, the avatar menu's usage block) is `aria-hidden`; its text reaches screen
    readers through the menu's label or an item's description (functional brief U29).
  - Group label (`.ui-menu-label`): 12px/650 caps, 0.07em, `--text-3`, padding 10 10 4.
  - Separator (`.ui-menu-sep`): 1px `--border`, margin 6 4.
  - Head (`.ui-menu-head`): a serif 18/600 title and a 13.5px `--text-3` subtitle.
- **Radio items** (`menuitemradio`): the checked item gets weight 650 and a trailing 18px check in `--accent-ink`.
- **Disabled items** (`aria-disabled`, still focusable):
  - The title turns `--text-3` and the icon tile goes to opacity 0.5.
  - The **reason stays at full contrast** in `--text-2`, linked with `aria-describedby`.
  - Never dim the whole row with opacity.
- **On phones (under 800px) every menu is a bottom sheet:**
  - Position: fixed to the bottom and full width, radius 22px at the top only, no bottom border.
  - Size and padding: maximum height 82dvh, padding `8px 8px max(12px, safe-area)`.
  - Handle: a 36×4px `--border-strong` pill at the top.
  - Motion: it slides up over `--dur-3`, above a `--overlay` scrim that fades in over `--dur-2`. Tapping the scrim
    closes it.

### 6.10 Top bar (SHELL, `TopBar`, `.chat-topbar`)

- **The bar:** 60px tall (56 on phones), padding 0 14px 0 12px, on `--bg`, with an 18px fade below (5).
- **Order:** [Conversations ≡, under 1100] · Model menu · conversation title · Plugins · Documents · Avatar.
- **Model button** (`.chat-model-btn`):
  - Size and type: 38px tall, padding 0 10 0 12, radius `--radius`, 16.5px/600 `--text`, with a 15px chevron in
    `--text-3`.
  - Hover `--surface-3`. Open: `--surface` with a 1px `--border` and `--shadow-xs`.
  - It is the one item in the bar that shrinks: it (and any wrapper) is `flex: 0 1 auto; min-width: 0`, its label
    ellipsizes. The display name is never shortened (names are the admin's); the accessible name keeps it whole.
  - Accessible name: "Model: Claude Opus 5.5".
- **Title** (`h1.chat-topbar-title`): a flex item (`flex: 1 1 0`, `min-width: 0`) centred in the space between the
  model button and the actions; serif 15.5/500 `--text-2`, ellipsis. Its basis is 0, so it only takes the space left
  over: a long title never shortens the model name.
  - It shows "New chat" on a new chat.
  - It is visible when the chat column is 720px or wider, `sr-only` below (container query, see below). It is **not**
    absolutely centred, because it would collide with the actions.
- **Action buttons** (`.chat-tb-btn`):
  - Size and type: 38px tall, padding 0 12, gap 7, radius `--radius`, 15px/600 `--text-2`.
  - Hover `--surface-3` and `--text`. A menu open: `--surface` with `--border` and `--shadow-xs`.
  - **Plugins:** an 18px plug icon, "Plugins" and a 14px chevron.
  - **Documents** (`.chat-docs-toggle`): the panel icon and "Documents", with `aria-expanded`. When expanded it is
    `--text` on `color-mix(--surface-3 55%, transparent)`, so the pressed look isn't colour-only.
  - **Chat column under 560px:** icon only, 40×40 (no label, no chevron), accessible names kept. The actions are
    `flex: none`.
- **Compaction by the column, not the viewport (revision 2).** `.chat-main { container-type: inline-size;
  container-name: chat }`, and the label and title rules are `@container chat (max-width: 719.98px)` (title `sr-only`)
  and `@container chat (max-width: 559.98px)` (icon-only buttons). The viewport media queries keep only the
  Conversations button (under 1100), the 56px height and phone paddings (under 800) and the coarse-pointer sizes.
  Typical results: 1440 → an 820px column (labels and title); 1100 → 480 (icons); 1000 → 660 (labels); 820 → 480
  (icons); 390 → 390 (icons). The revision 2 copy of the mockup has these rules; `topbar_check.mjs` finds no element
  outside the bar at 14 widths from 390 to 1440, with the documents panel shown and collapsed and a 40-character model
  name.
- **Attention dot** (`.chat-tb-dot`): 8px `--warning` with a 2px `--bg` ring, on the Plugins icon's top-right
  (Reconnect needed) and on the avatar (usage ≥ 90%). The accessible name adds the reason ("Plugins, 1 needs
  attention").

### 6.11 Plugins menu (SHELL)

- **Layout:** 372px wide, right-aligned to its button. A head reads "Plugins" with "Apps the assistant can use for
  you in chat.", then one item per plugin, a separator and "Manage plugins…" (gear icon, trailing chevron).
- **Plugin item** (`.chat-plugin-item`, a single `menuitem` link):
  - A 40px icon tile with the plugin's 20px icon in its brand-like tone (Gmail: `--tag-red`).
  - Name 15.5/650; an optional description 13.5 `--text-3`, clamped to two lines (the full text is in Settings →
    Plugins & data); the status line (6.7); an optional trailing pill.

| Status | Status line | Trailing | Activation |
|---|---|---|---|
| Connected | connected · "Connected · maya.chen@example.com" (+ " · off in chat") | quiet chevron | opens its card |
| Reconnect needed | attention · "Reconnect needed" + the reason as description | `--accent-soft` pill "Reconnect ›" | opens its card |
| Not connected | neutral · "Not connected" + the plugin description | `--accent-soft` pill "Connect ›" (28px, radius pill, 13.5/650 `--accent-ink`) | opens its card |
| Turned off by the admin, connected | off (lock) · "Turned off by the admin" | quiet chevron | opens its card |
| Turned off by the admin, not connected | off (lock) · "Turned off by the admin" + "The admin turned Gmail off." | — | disabled |
| Not set up, still connected (anyone; rev. 2) | unset · "Not set up · maya.chen@example.com still connected" | quiet chevron | opens its card (which offers Disconnect) |
| Not set up, member | unset (dashed) · "Not set up yet" + "The admin hasn't set up Gmail yet." | — | disabled |
| Not set up, admin | unset · "Not set up" + "Needs a Google OAuth client before anyone can connect." + an `--accent-ink` underlined "Set up in Admin → Plugins" on one line | — | link to `/admin#plugins` |

Loading: one `--text-3` line "Loading plugins…" with a spinner. Error: the message plus a "Try again" item. The head
("Plugins" and its subtitle) is `aria-hidden`: the menu is labelled "Plugins" and described by the subtitle.

### 6.12 Avatar menu (SHELL)

304px wide, right-aligned. From top to bottom:

1. A head: a 40px avatar, the username at 15.5/650 and the role ("Member" / "Administrator") at 13.5 `--text-3`.
2. A usage block: `--bg` fill, 1px `--border`, radius `--radius`, padding 10. It holds two meters, "Tokens this month"
   and "Images this month".
3. "View usage" (chart icon).
4. A separator, then "Settings" (gear).
5. The "THEME" label and a 3-column row of compact radio items: System, Light, Dark.
   - Each item is an icon over a label, 13.5/600, padding 8 4 6.
   - The checked one is `--surface` with a 1px `--border-control` (≥ 3:1), `--shadow-xs`, `--text` at 700 and an
     `--accent-ink` icon.
   - The menu stays open after a pick. A failed save reverts and says why under the row (`role="alert"`), or in the
     chat page's notice when the menu has closed meanwhile.
6. A separator, then "Admin" (shield; admins only) and "Log out".

Semantics (revision 2): the menu is labelled "Account: maya.chen, Member"; the head and the usage block are
`aria-hidden`, and "View usage" is described by a hidden text with the meters' numbers and states. The theme row is a
labelled `group` (Left and Right move along it).

### 6.13 Model menu (SHELL)

- **Layout:** 330px wide, left-aligned. Provider group labels ("ANTHROPIC", "OPENAI"), then `menuitemradio` items.
  - Each item shows the name at 15px. The default model has a `badge-accent` "Default" after its name.
  - A row of model tags sits under the name. The checked item has the trailing check.
- **End:** a separator, then "Set the default in Settings…" (sliders icon).
- **States:** "Loading models…", the load error and "No models available" are disabled items.

### 6.14 Composer (SHELL, `.chat-composer`)

- **Wrapper** (`.chat-composer-wrap`): centred, max 776px. The composer:
  - `--surface`, a 1px `--border`, radius `--radius-xl` (20 on phones), padding 14 12 10 14, `--shadow-composer`.
  - `:focus-within` changes the border to `--border-strong` and adds `0 0 0 4px var(--accent-soft)`.
- **Textarea:** borderless, minimum 28px and maximum 200px, padding 2 6 10, sans 16.5/1.5. Its height comes from
  the existing JS autosize (Composer.tsx today); `field-sizing: content` may be added inside `@supports
  (field-sizing: content)` as an enhancement, never instead (Firefox lacks it).
  - Placeholder: "Ask a question…" on a new chat, "Reply…" in a conversation, or the disabled reason. Disabled:
    the composer fill becomes `--surface-2`.
- **Bar:**
  - **"+" button** (`.chat-plus`): a 36px circle (40 on phones and touch), 1px `--border-strong`, `--surface`,
    `--text-2`. Hover or open: `--surface-3`. Open, its plus rotates 45° into an ×.
  - **Scope chip** (`.chat-scope`): 32px tall (36 on phones), padding 0 12, pill, `--surface-2`, 14/600 `--text-2`,
    with the docs icon and "All documents", "3 documents" or "No documents" (`.chat-scope-off`: transparent with a
    `--border` outline and `--text-3`). Hover adds a `--border` outline.
  - **Send** (`.chat-send`): a 36px circle (40 on touch), `--accent` with an `--accent-text` 18px arrow, stroke 2.25.
    Hover `--accent-hover`; pressed `scale(.94)`. Disabled (empty draft, or waiting for attachments): `--surface-3`
    with a `--text-3` arrow and no shadow.
  - **Stop** (`.chat-stop`, while streaming): the same circle in `--text` with a 16px `--bg` filled square.
- **Hint** (`.chat-composer-hint`): centred under the composer, 13px `--text-3`, with keys as `kbd` at 11.5px.
  - The key part is "Enter to send · Shift Enter for a new line", or "⌘ Enter to send · Enter for a new line"
    (Ctrl elsewhere). It is hidden on phones and coarse pointers.
  - The capability sentence follows: "Ask for a PDF, Word, Excel or Markdown file, or an image".
  - A **waiting** message replaces the hint in `--text-2` 600: "Waiting for report.docx to finish indexing…".
- **Attachment chips** (`.chat-attachment`, above the textarea, wrapping, gap 8):
  - 40px tall, padding 0 4 0 8, radius `--radius`, `--bg` with a 1px `--border`.
  - Contents: a small file tag (22×26), the name (13.5/600, ellipsis at 170px), the state, and a 28px ghost × button.
  - States: "Uploading 40%" in `--info` 600, plus a 2px `--accent` progress line along the bottom edge; "Indexing…"
    in `--info` with a spinner; "✓ Ready" in `--success`; "Failed: <reason>" in `--danger`, with the chip border at
    `danger 35%`.
  - The × is labelled "Cancel uploading <name>" or "Remove <name> from this message; it stays in your documents".

### 6.15 The "+" menu (SHELL)

- **Layout:** 384px wide, opening upward from the "+" (left-aligned, `transform-origin: bottom left`, the `menu-up`
  keyframe). Items use 34px icon tiles and a title over a description.
- **Group 1:** "Attach files…", described "Upload to My documents · up to 25 MB each".
- **"CREATE":**
  - "Create an image", described "GPT Image 2 · 18 of 30 left this month".
  - A "Create a document" row ("Pick a format to start the prompt") that isn't an item: it is the **label of a
    horizontal group** (`role="group"`, `aria-labelledby` its text) holding a row of **format chips** indented 56px
    (10px on phones). Each chip is a real `menuitem` with the full accessible name ("PDF document", "Word document",
    "Excel spreadsheet", "Markdown file"); Left and Right move along the row.
  - Chips: 30px tall, padding 0 10 0 8, pill, 1px `--border`, `--surface`, 13.5/600 `--text-2`, with a 7px rounded
    square in the file-type tone. Hover or focus: `--accent` border, `--accent-soft` fill and `--accent-ink` text.
  - A disabled chip: dashed border, `--surface-2` fill, `--text-3`, sharing its row's reason through
    `aria-describedby`.
- **"GMAIL":** "Search my email" (description: the account), "Draft an email", "Import from Gmail…" (with the
  description "Threads and attachments into My documents"); or "Connect Gmail…" / "Reconnect Gmail…" with a trailing
  chevron. Gmail icons are in `--tag-red`.
- **Disabled items** follow 6.9: the reason stays readable (states sheet, "+" menu).

### 6.16 Sidebar (SHELL, `.chat-sidebar`)

- **Frame:** the desk (`--sidebar`) with a right hairline.
- **Header:** 60px, padding 0 12 0 18, holding the brand (`.ui-brand`: a 30px `--brand` square, radius 9, with a
  serif italic "R" in `--brand-text` and an inset bottom shadow; then the serif 18/600 "RAG Assistant").
- **New chat** (`.chat-new-chat`): 40px tall and full width, inside 2 12 8 padding, radius `--radius`, `--surface`
  with a 1px `--border` and `--shadow-xs`, 15/600, with the new-chat icon in `--accent-ink`. Hover: `--border-strong`
  and `--shadow-sm`.
- **List:** scrolls, padding 0 10 16, thin scrollbar in `--border-strong`.
  - Group labels "TODAY", "PREVIOUS 7 DAYS", "OLDER" (12/650 caps, padding 16 10 6).
  - **Rows:** radius 9 with a 1px gap. The link is padding 7 10, 15px `--text-2`, single-line ellipsis.
    - Hover: `color-mix(--surface-3 70%, --sidebar)`.
    - Active: `--surface-3`, with `--text` at 600 and room for the actions.
    - Actions: a 28px "⋯" (radius 7) shown on hover, focus-within and the active row.
  - **Rename:** the row becomes a `--surface` field with an `--accent` inset ring and a 3px `--accent-soft` halo; the
    input is 30px at 15px.
  - **Delete:** an inline confirmation card, `--surface`, 1px `danger 35%` border, `--shadow-sm`, radius `--radius`,
    padding 10 12. It holds "Delete this chat?" (14.5/650), a line in 13.5 `--text-3` and xs buttons Cancel / Delete
    (danger).
- **States:** skeleton lines (6.26); "No conversations yet" as a 14px `--text-3` paragraph with padding 16 10; an error
  as a `ui-notice-danger` with Retry.
- **No footer.**

### 6.17 Thread and the answer (SHELL)

- **User message:** right-aligned bubble, max `min(580px, 86%)` (88% on phones), padding 12 18, radius `20px 20px 6px
  20px`, `--bubble`, `--text`, sans `--chat-bubble-size`/1.55 (16px by default), `overflow-wrap: anywhere`.
- **Assistant message:** a 30px | 1fr grid with a 16px gap. The mark is a 30px circle in `--accent-soft` with a 16px
  filled sparkle in `--accent-ink`, hidden on phones.
- **Markdown (`.chat-md`):** serif at `--chat-font-size` / `--chat-line-height`.
  - Text: paragraphs have a 0.85em bottom margin; `strong` is 650.
  - Lists: `li::marker` in `--accent`; `ol` markers are sans 650 at 0.9em; `li` spacing 0.35em.
  - Links: `--accent-ink`, underlined at 1px with a 3px offset and a 45%-ink decoration (100% on hover).
  - Inline code: mono 0.82em on `--code-bg` with an inset `--border` ring, radius 5, padding 0.1em 0.36em.
  - Blockquote: a 3px `--border-strong` left rule, italic, `--text-2`. `hr` is a 1px `--border` with 1.6em margins.
- **Citation chip** (`.chat-cite`, a link or button):
  - Shape and type: minimum 19×19, padding 0 5, radius 6, `--accent-soft` fill, `--accent-ink` sans 12/700, raised
    0.14em, no underline (beat the `.chat-md a` rule with `.chat-md .chat-cite`).
  - Hover: `--accent` fill with `--accent-text`.
  - Spacing: margin `0 1px 0 3px`, plus 2px between neighbours.
  - **Glue each chip to the word before it:** insert U+2060 WORD JOINER before the first chip, or wrap the last word
    and the chips in `white-space: nowrap`. A chip must never start a line.
- **Answer tables** (`.chat-md-table`, a scroll wrapper with `overflow-x: auto`) are set like booktabs, with **no
  box**:
  - `table`: sans 15/1.4, `tabular-nums`, `border-top` and `border-bottom` 1.5px `--rule`.
  - `th`: 12.5px/650 caps, 0.06em, `--text-3`, padding 10 16 9 0, `border-bottom` 1px `--rule`.
  - `td`: padding 11 16 11 0, `border-top` 1px `--border` (none on the first row), `--text-2`. The first column is
    `--text` 600 and `nowrap`.
  - On phones the table gets `min-width: 520px` inside its scroller. The scroller has a scroll-shadow cue: a soft
    `--rule` shade at an edge while more of the table lies beyond it (covers in `--bg` hide it at the ends).
- **Code blocks** (`.chat-code`):
  - Frame: 1px `--border`, radius `--radius`, `--code-bg`.
  - Header: 36px tall, padding 0 6 0 14, bottom hairline. It shows the language from the `language-*` class (sans 13/600
    `--text-3`; omit the header text when there is none) and a ghost xs "Copy" (13px; "Copied" for 1.5 s, using the
    same clipboard logic as the answer's CopyButton).
  - `pre`: padding 14 16 15, mono 13.5/1.65, `overflow-x: auto`, `tab-size: 2`. Monochrome text.
  - *The header is the only new behaviour this spec adds (SHELL, a `pre` renderer in `Markdown.tsx`); it changes no
    contract.*
- **Streaming:** the last block of `.chat-md-streaming` ends in a 2px × 1.05em `--accent` caret that blinks over 1 s
  with `steps(1)`. It doesn't blink under reduced motion.
- **Waiting** (`.chat-typing`): three 7px `--text-3` dots that pulse and rise 3px over 1.2 s, staggered 0.15 s.
- **Notes** (`.chat-note`): radius `--radius`, padding 10 12, 14.5/1.45, a 15px icon.
  - Variants: muted (`--surface-2`, `--text-2`), warning (`--warning-soft`, `--text`, a `--warning` icon), error
    (`--danger-soft`, `--text`, a `--danger` icon, a trailing sm "Retry" button).
- **Jump to latest** (`.chat-jump`): a 36px circle centred 12px above the bottom of the thread, `--surface` with a 1px
  `--border` and `--shadow`, and an arrow-down in `--text-2`.
- **Banner** (`.chat-banner`): max 740px, centred, 18px above the thread. It uses the notice look (6.25).

### 6.18 Cards under an answer and the message footer (SHELL)

- **File card** (`.chat-file`):
  - Frame: max 500px wide, padding 12 12 12 14, gap 14, `--surface`, 1px `--border`, radius `--radius-lg`,
    `--shadow-xs`.
  - Contents: a large file tag, the name (15.5/650, ellipsis), and the meta (13.5 `--text-3`). The meta parts are
    joined by a "·" drawn **between** them (`span + span::before`, never `::after`, so nothing trails).
  - Statuses: "✓ Ready" (success), "Indexing…" (info with a spinner), "Writing the file…" (info) with a 3px progress
    bar (`--surface-3` track, `--accent` fill), "Failed: …" (danger), "Stopped" (`--text-3`).
  - Download: an sm button, icon only on phones (40px).
- **Image card** (`.chat-image`):
  - Frame: max 440px wide, the same frame as the file card, with `overflow: hidden`.
  - Preview: a 3:2 button (`cursor: zoom-in`) holding the preview; on hover the image scales to 1.015 over `--dur-3`.
  - While generating: a placeholder of the final aspect ratio with 135° stripes of `--surface-2` and `surface-3 60%`,
    plus a centred spinner line in `--text-2` 14/600 ("Creating the image… this takes up to a minute", with the
    seconds counter).
  - Bar below: padding 10 10 10 14, a top hairline, a file tag, the name, the meta and Download.
- **Footer** (`.chat-message-footer`, 16px under the body, wrapping, gap 8):
  - **Sources toggle** (`.chat-sources-toggle`): 34px pill, padding 0 12 0 7, `--surface`, 1px `--border`,
    `--shadow-xs`, 14/600 `--text-2`.
    - It shows a stack of up to three 24px source dots (`.chat-src-dot`: a file-type soft fill with a 13px file
      icon in the tone, a 2px `--surface` ring, overlapping by 7px), then "3 sources" and a chevron that rotates 180°
      when expanded. Email sources use the mail icon in red.
  - **Meta:** "Claude Opus 5.5 · 6,240 tokens", 13.5 `--text-3` tabular, pushed right (on phones it wraps to its own
    line under the sources pill; without a pill it stays on the row, left-aligned, before Copy).
  - **Copy:** a 32px ghost icon button.
- **Sources list** (`.chat-sources`, expanded, a column with gap 8):
  - Each source (`.chat-source`) is a 22px | 1fr grid with padding 12 14, `--surface`, 1px `--border` and radius
    `--radius`.
    - **Index:** 22×20, radius 6, `--accent-soft`/`--accent-ink` 12/700.
    - **Head:** the name 14.5/650, then "page 12" or the sender and date in 13 `--text-3`.
    - **Snippet:** **serif** 15/1.55 `--text-2`. Highlights use `mark` with an accent 16% fill, `--text`, radius 3,
      padding 0 2.
    - **Email sources:** add "Open in Gmail ↗" (13.5/600 `--accent-ink`).
  - Citation targets (`:target`) get an `--accent` border and a 3px `--accent-soft` ring.

### 6.19 Email cards (SHELL, `plugins.css`)

- **Frame:** the card look (`--surface`, a 1px `--border`, radius `--radius-lg`, `--shadow-xs`).
- **Head:** a 12 14 band on `--bg` with a bottom hairline: a mail icon in `--tag-red`, the title at 14.5/650 and,
  for pending sends, the countdown at the right in `--warning` 13/600 tabular.
- **Rows:** a 64px | 1fr grid with padding 8 14 and hairlines between rows. The label is `--text-3` 600, the address
  `--text` 600, then the display name.
  - Flags ("not mentioned by you", "not named by you", the IDN warning) are 12/650 `--warning` on `--warning-soft`,
    radius 5.
  - Bcc rows get `--warning-soft` behind the label.
- **Body:** 14.5/1.55 `--text-2`, plain text (`pre-wrap`).
- **Actions:** Send (primary sm), Edit (sm), Cancel (ghost sm).
- **Outcomes:** sent, failed, cancelled and expired use the matching status line (6.7) in the head. Activity and
  import cards use the same frame without the rows.

### 6.20 New chat (empty state, SHELL)

The empty state is centred in the thread (max 620px):

1. A 52px `--accent-soft` circle with a 24px sparkle (`--accent-ink`), 22px of space below.
2. The greeting "How can I help, *maya.chen*?" (4.3).
3. The italic serif sub-line "Ask about your documents or anything else. Answers cite the passages they draw on, so
   you can check them."
4. The **contents list** (`.chat-examples`), 34px below:
   - Frame: a 1.5px `--rule` above and below, with 1px `--border` between rows.
   - Each row is a button: padding 13 8 13 4, radius 8, gap 14.
   - Contents: an 18px icon in `--accent-ink`, then the prompt (15.5/600) over a 13.5 `--text-3` sub-line
     ("Across your 6 ready documents", "Saved to My documents", "GPT Image 2 · 18 left this month"), then a trailing
     arrow in `--text-3`.
   - Hover: a `color-mix(--surface-3 45%)` fill, and the arrow turns `--accent-ink` and moves 2px.
   - Image and file rows follow the existing availability rules.

### 6.21 Documents panel (RESTYLE, `documents.css`)

- **Frame:** the desk (`--sidebar`) with a left hairline.
- **Header:** 60px, padding 0 12 0 20, the serif 19/600 "Documents" title. When usable, the right side holds "Import
  from Gmail" (ghost sm, mail icon). In slide-over mode it also holds a close icon button.
- **Upload scope** (`.docs-scope`, when the admin can upload to the shared base): a "UPLOAD TO" caps label, then a
  full-width small segmented control (Private / Shared knowledge base). It sits above the dropzone, padding 0 16 12.
- **Dropzone** (`.docs-dropzone`):
  - Full width, padding 14, gap 14, 1.5px dashed `--border-strong`, radius `--radius-lg`, `--surface`.
  - Contents: a 40px `--accent-soft` icon tile (radius 12) with the upload icon in `--accent-ink`; the title "Drop
    files here or <u>browse</u>" (15/600, "browse" `--accent-ink` underlined); the 13px `--text-3` hint (formats ·
    size).
  - Hover or active drag: `--accent` border and a `color-mix(--accent-soft 35%, --surface)` fill.
- **Selection bar** (`.docs-selectbar`): margin 0 8 2, padding 9 12, radius `--radius`, `--surface` with a 1px
  `--border`, 14 `--text-2`. It holds a `.ui-check` and "Searching **all 6** ready documents".
- **Sections:** caps titles (12/650, padding 16 12 6) with a count pill (`.docs-section-count`: 18px tall, `--surface-2`
  with an inset `--border` ring, 11.5 `--text-2`).
- **Rows** (`.docs-row`): an 18 | 34 | 1fr grid, gap 12, padding 10 12, radius `--radius`.
  - Hover and focus-within: `--surface` with `--shadow-xs` (lifts off the desk).
  - Name: 15/600, ellipsis.
  - Meta: 13 `--text-3`, joined by "·". "Created in chat" and "Imported from Gmail" are `--accent-ink` 600.
  - Status: a row of badges and link-buttons, 7px below.
  - Error: 13 `--danger`.
  - **Actions:** Download and Delete, 30px icon buttons (radius 8, `--text-2`; Delete hovers to
    `--danger-soft`/`--danger`). They float at the top right on hover or focus-within, and the name reserves 64px for
    them.
- **Upload queue** (`.docs-upload-item`): `--surface` cards (radius `--radius`, 1px `--border`, padding 9 10): a small
  tag, the name 13.5/600, a percentage in `--text-3` tabular, and a 3px progress bar across the bottom.
- **Empty** (`.docs-state`, centred, padding 40 24):
  - A 52px icon tile (`--surface`, radius 16, 1px `--border`, `--shadow-xs`).
  - The serif 19/600 "No documents yet".
  - 14px `--text-3` text: "Upload PDF, DOCX, PPTX, XLSX, CSV, HTML, MD or TXT files and ask about them in chat. Files
    you upload are private to you."
- **Loading:** skeleton rows (6.26). An error state uses `ui-notice-danger` with Retry.
- **Footer quota:** two columns, gap 18, padding 12 20 16, a top hairline. It holds "Documents 6 / 50" and "Storage
  6.7 / 500 MB" as meters (4px bars, 13px label row).
- **Image viewer:** the Modal (6.22), widened to `min(1100px, 100%)`.
  - The stage is `--code-bg` with the image `object-fit: contain`.
  - Below it: the caption (serif 15/1.55), a facts row (size · format · quality, 13.5 `--text-3`), the prompt in a
    `--surface-2` block (radius `--radius`, 14.5/1.5), Download (primary) and Close.

### 6.22 Modals and sheets (`Modal`, FOUNDATION)

- **Backdrop:** `--overlay` with `blur(3px) saturate(.9)`, fading in over `--dur-2`, padding 24.
- **Dialog:** `width: min(520px, 100%)`, max height `100dvh - 48`, `--surface`, 1px `--border`, radius
  `--radius-xl`, `--shadow-lg`. It enters with a 10px rise and `scale(.985)` over `--dur-3 --ease-out`.
- **Header:** padding 20 16 6 24, the serif 22/600 title, a ghost 36px close icon.
- **Body:** padding 8 24 20, scrolls.
- **Footer:** padding 14 20, a top hairline, on `--bg`; buttons right-aligned with gap 8 (sm).
- **`hideHeader`, `labelledBy`, `bare`** *(rev. 2, functional brief 12)*: a dialog that draws its own title and close
  button (Settings, 6.23) hides the built-in header and labels itself by its own title; `bare` drops the padded,
  scrolling `.modal-body`, so the caller lays out and scrolls its panes (with its own prefix, never by restyling
  `.modal-*`).
- **`variant="sheet"`:** under 800px the dialog fills the screen (radius 0, no border, the `sheet-up` keyframe) and
  respects the safe areas. Larger dialogs (admin forms, the Gmail browser, the image viewer) set their own width up to
  1100px.

### 6.23 Settings dialog (SETTINGS, `settings.css`)

**Frame**

- **Size:** `width: min(980px, 100%)`, `height: min(820px, 100dvh - 48px)`.
- **Grid:** `236px | 1fr`, `--surface`, radius `--radius-xl`, `--shadow-lg`, with the rise animation.
- **Phones:** under 800px it is the full-screen sheet.

**Navigation (`.settings-nav`)**

- **Frame:** the desk with a right hairline, padding 22 12 14.
- **Title:** "Settings" in serif 25/600, padding 2 4 18 12.
- **Tabs** (`.settings-tab`, vertical):
  - Size and type: 40px tall, padding 0 12, gap 11, radius `--radius`, 15px/500 `--text-2`, an 18px icon (General
    sliders, Account user, Usage chart, Plugins & data plug).
  - Hover: `--surface-3`.
  - Selected: `--surface` with a 1px `--border`, `--shadow-xs`, `--text` 650 and an `--accent-ink` icon.
- **Foot** (pinned to the bottom): a 32px avatar, the username at 14/650 and "Saved to your account" at 13
  `--text-3`.

**Panel (`.settings-panel`)**

- **Scrolling:** each panel scrolls on its own.
- **Header** (`.settings-head`): sticky, padding 22 22 14 40, on `--surface`.
  - It holds the panel `h2` (serif 26/600) and, for Usage, the note "October 2026 (UTC)" (14.5 `--text-3`,
    baseline-aligned).
  - At the right: "✓ Saved" (13.5 `--text-3` with a `--success` check; visual only, `aria-hidden`: the control
    announces its own new state; shown after a PATCH succeeds, fading after 2 s), then the tab's actions (Usage: a
    Refresh icon button) and the close button.
- **Body:** padding 10 40 40.
- **Groups** (`.settings-group`): padding 20 0 with hairlines between groups.
  - A group is a column, or a row (`.settings-row`, gap 24) of a label block (`h3` 16/650 and a 14px `--text-3`
    description) and the control on the right. `.settings-row-wrap` drops the control under the label when narrow.
- **Section labels** (`.settings-sub`): 12/650 caps, margin 26 0 10.

**General**

- **Theme:**
  - The hint "Currently dark, from your system" (System) or "System follows your device's setting and switches with
    it."
  - Three **theme cards** (a radiogroup, the same keyboard behaviour as SegmentedControl): System, Light, Dark.
    - Grid: 3 columns, gap 14 (8 on phones).
    - Card: padding 8, radius `--radius-lg`, a 1px `--border`.
    - Contents: a mini preview (84px tall, 64 on phones, radius 9: a sidebar strip, a bubble, lines and a composer
      with an accent dot). Its colours are fixed per theme, and System is split down the middle, light on the left and dark on the right. Under it,
      a label row: an icon, the name 15/600 and, when selected, a 20px `--accent` check circle.
    - Selected: `--accent` border plus `0 0 0 1px var(--accent), 0 0 0 5px var(--accent-soft)`.
    - Focus: `--focus-ring`.
- **Accent colour:** five **swatches** with their names, a radiogroup.
  - Each label carries `data-accent="<preset>"`, so its dot (38px circle) shows **that preset in the current theme**
    through the tokens. No hex in `settings.css`.
  - Selected: a 3px `--surface` gap and a 2px ring in the preset's accent, an `--accent-text` check in the dot, and
    the name turns `--text` (otherwise 13.5/600 `--text-3`).
  - Focus: the ring in `--text`. Hover: scale 1.06.
  - Phones: the five spread across the width.
- **Chat font size:** the segmented control with "Aa" specimens at 13, 16 and 19px, full width on phones. Below it
  the **preview** block: `--bg`, 1px `--border`, radius `--radius-lg`, padding 16 18, serif at the chosen size, with
  a citation chip.
- **Default chat model:** a select 320px wide (full width on phones). The first option is "Server default (Claude
  Opus 5.5)", with the hint from 10.2.
- **Enter to send:** a switch, with the description showing keys as `kbd`.
- **Errors:** a failed save shows "Couldn't save: …" (`.error-text`, `role="alert"`) under that control.

**Account**

1. **Profile card** (`.settings-profile`, the top half of a joined card, `--bg`, radius 16 16 0 0, padding 18 20):
   - A 56px avatar, the serif username at 23/600.
   - A meta line: the role `badge` and "Accounts are managed by your administrator".
2. **Facts** (`.settings-facts`, the bottom half, `--surface`, radius 0 0 16 16, a 150px | 1fr `dl` with hairlines):
   - Rows: **Username**, **Role**, **Account created**, **Last sign-in** ("Today at 09:14" plus a 13.5 `--text-3`
     line "Safari 18 on macOS · desktop · 81.2.69.x", or "… · via a local network or proxy" when `ip_private`).
   - When the current session is not the last sign-in, add a **This session** row. On phones the `dt` sits above the
     `dd`.
3. **Recent sign-ins** (group):
   - The guidance and IP note as the description.
   - A list card (`.settings-logins`, radius 16, hairlines). Each row: a 36px device icon tile (laptop / phone /
     tablet) on `--surface-2`; "Browser on OS" 15/600 over the place line in 13.5 `--text-3` (the IP, tabular; or
     "Via a local network or proxy" with the masked address in `title` and the accessible description; or "Unknown
     address"); a success "This device" badge; the time at the right (13.5 `--text-2`, relative, with the exact time
     in `title` and the accessible name).
   - "Show all (12)" is a ghost sm disclosure.
   - With no events: "Sign-ins are recorded from now on." plus the last sign-in.
4. **Password** (row):
   - "Change password…" (sm, key icon) expands the inline form (`.settings-inline-form`, max 420px, gap 14): three
     password fields with toggles, then the actions (primary sm "Change password", ghost "Cancel").
   - **Env admin:** no button; the explanation paragraph instead.
5. **Sessions** (row, wrapping):
   - "Log out of all other sessions" (sm) opens the inline confirmation (`.settings-confirm`: `--bg`, a 1px
     `--border`, radius `--radius`, padding 14 16; the text, then primary "Log out others" and ghost "Cancel").
   - A ghost "Log out" for this session.

**Usage**

- **Lede:** "Limits start again on **Sat 1 November, 01:00** (in 13 days). Your administrator sets them." The date is
  local.
- **Meters:** three **tiles** (`.settings-tile`, a 3-column grid, gap 12, one column on phones). Each tile is `--bg`
  with a 1px `--border`, radius 16, padding 14 16 12:
  - A label at 13.5/600 `--text-2`.
  - The serif figure at 27/600, with "/ 1M" in sans 14.5 `--text-3`.
  - A meter (6.6), then a foot line at 13 `--text-3` ("587,620 left", "Reading scanned PDF pages").
- **Facts:** a `dl` grid of 3 columns (2 on phones) with inner hairlines, radius 16:
  - Each fact is a 13px `--text-3` term over a 15/650 tabular value, with a unit in 13 `--text-3`.
  - Facts: Chat answers, Requests per minute, Embedding tokens "not limited", Documents "6 of 50", Storage,
    Largest upload.
- **Estimated cost** (`.settings-cost`, a `--bg` card):
  - The label, then the serif 27/600 total ("$4.82").
  - Part badges ("Chat $4.31", "Images $0.32", "Vision $0.17", "Embeddings $0.02").
  - The note "An estimate from the models' prices; the provider's bill may differ." at 13 `--text-3`.
- **Daily chart** (`DailyUsageChart`, `.settings-chart`, a `--bg` card, padding 16 18 12):
  - **Head:** the `h3` "Per day" and a small segmented control (Chat tokens / Images / Cost).
  - **Readout** (`.settings-chart-readout`, `aria-hidden`, 14px `--text-2`): the hovered or keyboard-selected day,
    "**Tue 14 October** · 44,210 chat tokens · 2 images · $0.51". Default: today. It replaces a tooltip, so nothing
    covers the bars. A separate `sr-only` `aria-live="polite"` element gets the same text only on keyboard moves, so
    pointer hover announces nothing.
  - **Plot** (SVG, width 100%, 176px tall; revision 2: its `viewBox` is `0 0 <measured width> 176`, from a
    `ResizeObserver`, so one unit is one CSS pixel and text never scales; the coordinates below are for a 640px plot,
    and the left gutter (40) and right edge (8) stay fixed while the slots divide the rest):
    - **Axes:** 3 gridlines (0, the middle and a rounded maximum) as 1px `--border` lines from x 40 to 632, with
      sans 12 `--text-3` labels right-aligned at x 32.
    - **Bars:** one per day in equal slots. Each bar is 62% of its slot, top radius 4, `--accent`. The hovered day is
      `--accent-hover`.
    - **Peak:** a 12/650 `--text-2` label ("44.2K") 6px above the tallest bar.
    - **Future days:** 2px stubs in `--border-strong` on the baseline.
    - **Today:** a 2px dot under its bar and the x label "Today" in 650 `--text-2`.
    - **X labels:** the 1st (with the month), every 7th day, today and the last day, skipping labels within two days
      of "Today". Under 420px of plot, only the 1st, today and the last day.
    - **Every label stays 12px or more** at every width (axis, peak, x labels, "Today").
    - **Semantics:** a focusable `role="group"` with `aria-roledescription="chart"` and the summary as `aria-label`;
      Left and Right move the readout day. The "Show as table" disclosure is the screen-reader path.
    - **Empty:** with all zeros, show "No usage yet this month." instead.
  - **Foot** (`.settings-chart-foot`): a top hairline, then "⏱ Up to 20 requests a minute", "↻ Resets 1 Nov, 00:00 UTC"
    and, at the right, the ghost xs disclosure "Show as table" (it renders a `.table`). It is `display: flex;
    flex-wrap: wrap; gap: 6px 16px`, so the parts wrap onto a second line instead of overflowing (390, 800 and 860px
    did in the mockup), and its icons are fixed at 15px (`width` and `height` on the SVG, `flex: none`).
- **By model:** the `.settings-sub` label "BY MODEL", then a `.table` (6.27): Model (650 `--text`), Kind, then Tokens,
  Requests and Cost right-aligned and tabular. When empty: "Nothing used yet."

**Plugins & data**

- **"PLUGINS":** the `ConnectionsPanel` cards (6.24).
- **"YOUR DATA":** rows (`.settings-data-row`: `--bg`, a 1px `--border`, radius 16, padding 16; a 36px icon tile, the
  label block, and the action at the right).
  - **Export my data:** the copy from 10.5 and an sm "Export". While working: a spinner with "Preparing your export…"
    (`role="status"`). The result: "Saved <file> (<size>)", plus a warning note when conversations were left out.
  - **Delete all chats** (`.settings-data-danger`: the border at `danger 35%`, the icon tile `--danger-soft` /
    `--danger`):
    - The description gives the count.
    - The typed confirmation (`.settings-typed`): the label "Type `DELETE` to confirm" (the word as mono 13/600 on
      `--surface-3`, radius 5), a full-width input that focuses with a `--danger` border and a `--danger-soft` halo,
      then right-aligned Cancel (sm) and a danger sm "**Delete 48 chats**", enabled only on a match.
    - The result: "Deleted 48 conversations." in a success notice; focus moves to it.

**Phones (under 800px)**

- **Layout:** the dialog is a full-screen sheet in two rows.
- **Nav:** padding-top `max(10px, safe-area)`.
  - A title row: "Settings" in serif 22, with the close button at the right.
  - Under it, the tabs as a **horizontal strip** (38px tabs, gap 4, padding 0 28 10 12, `overflow-x: auto`,
    `scroll-snap-type: x proximity`, no scrollbar), with a 36px **edge fade** to `--sidebar` at the right, so
    "Plugins & data" reads as more to scroll.
  - The nav foot and the panel's close button are hidden.
- **Panel:** the header padding is 16 16 10 20 (serif 22) and the body 0 20 32.

### 6.24 Connection cards (RESTYLE, `connections.css`)

- **Card** (`.conn-card`): `--bg`, a 1px `--border`, radius 16, `overflow: hidden`; 12px between cards.
- **Head** (padding 16, wrapping, gap 12):
  - A 40px icon tile, then a heading block: the name (16/650), the status line (6.7) and the account line ("Connected
    12 Oct · last used today", 13.5 `--text-3`).
  - At the right: "Browse Gmail" (sm, search icon) and "Disconnect" (ghost sm). Disconnect opens an inline
    confirmation. Not connected: a primary sm "Connect"; Reconnect needed: a primary sm "Reconnect".
- **Capabilities** (`.conn-caps`, padding 0 16 14 68): 24px pills (`--surface`, a 1px `--border`, 13px `--text-2`)
  with a `--success` check when granted. When not granted: a dashed border, `--text-3` and a "Grant access" link.
- **"Use in chat"** (`.conn-switch-field`): a top hairline, padding 14 16 14 68, the label block and a switch.
- **Notes** (`.conn-note`): a top hairline, on `--surface-2`, 13.5 `--text-2` with a 15px icon (the testing-mode end
  date; in its last day the warning tone).
- **Not set up:** the card shows the unset status and the sentence from 10.5. Admins also get the "Set up in Admin →
  Plugins" link. **With a connection left** (revision 2), as for "Turned off by the admin": the account line ("<email>
  is still connected") and a ghost sm "Disconnect" at the right, with its inline confirmation; no Connect, Browse or
  capability pills.
- **On phones:** the inner rows indent 16, not 68.
- **OAuth result banners:** notices (6.25) at the top of the panel.
- **Gmail browser:** a large Modal (up to 1000px).
  - Search: a search field and button.
  - Thread rows: 52px rows with checkbox, sender (600), subject, snippet in `--text-3` and date tabular; unread rows
    `--text` 650 with a 6px `--accent` dot.
  - Message bodies: plain text in `--surface-2` blocks.
  - Footer: the selection count and "Import".

### 6.25 Notices, banners, toasts (`.ui-notice`, `.ui-toast`) *(new)*

- **Notice:**
  - Frame: radius `--radius`, padding 11 12 11 14, gap 10, 14.5/1.45.
  - Contents: a 15px icon, the text, and an optional trailing action (sm button) or a dismiss (28px ghost icon,
    "Dismiss").
  - Tones: neutral (`--surface-2` with a 1px `--border`), info, success, warning and danger (the soft fill, no
    border, the icon in the tone colour, any lead `b` in the tone colour; the text stays `--text`).
  - Roles: `role="status"`, or `alert` for errors.
- **Shared:** `chat-banner`, `chat-notice`, `conn-banner-*`, `admin-notice-*`, `admin-flash-*` and `docs-notice`
  either use these classes or copy these values under their own prefix.
- **Toast** (only where the app already uses transient feedback):
  - An inverted pill-ish bar (radius `--radius`), `--text` fill with `--bg` text, 14.5/500, `--shadow-lg`, max 440px.
  - An optional ghost action with a 35% `--bg` outline.
  - Bottom-centre, 16px above the composer, for 4 s.

### 6.26 Skeletons and loading

- **`.ui-skeleton`:**
  - Shape: blocks of radius 6, 12px tall (10px for sub-lines).
  - Fill: a gradient `--surface-3` → `color-mix(--surface-3 45%, --surface)` → `--surface-3` at 200% width, shifting
    over 1.4 s `--ease-in-out`. Static under reduced motion.
  - Shapes mirror the content: conversation rows (a 40% label and three 70–86% lines), document rows (box, tag,
    two lines), settings tiles, and the chart as a flat card.
  - Accessibility: `aria-busy` on the region and an `sr-only` "Loading…".
- **`.spinner`:** 11px, a 1.75px `currentColor` ring with a transparent right side, turning every 0.8 s. With reduced
  motion it stays visible but doesn't animate.
- **Full-page states** (FOUNDATION: the `FullPageState` component in `components/ui/` and its `ui-fullpage*` rules in
  `global.css`; `ErrorBoundary` uses it, and SETTINGS switches `App.tsx`'s boot spinner and "Can't reach the server"
  to it):
  - The boot spinner (`FullPageSpinner`): centred on `--bg`, 24px.
  - "Can't reach the server" and the error boundary (`FullPageMessage`): a `.card` (max 440px, padding 28, centred),
    with the brand mark, a serif 22/600 title, 15px `--text-2` text and a primary "Try again".

### 6.27 Tables (admin, Settings): `.table`

- **Cells:** `width: 100%`, collapsed borders, 14.5px tabular.
  - `th`: 12/650 caps, 0.06em, `--text-3`, padding 10 14, a bottom 1px `--rule`.
  - `td`: padding 12 14, `--text-2`, bottom hairlines (none on the last row). Row hover: `color-mix(--surface-2 70%)`.
  - `.num` cells are right-aligned.
- **Wrapper:** `.table-wrap` with `overflow-x: auto`.
- **Admin cards:** the card frame holds the table.

### 6.28 Login (RESTYLE: the markup and its `login-*` rules in `src/pages/login.css`, imported by `LoginPage.tsx`)

> **Changed on 2026-10-28:** the aside's specimen cards are replaced by an auto-playing product tour (`src/pages/login/ProductTour.tsx`, `tourScenes.tsx`, `product-tour.css`): six scenes in a mock app window with a segmented progress bar (tabs), Pause/Play, pauses on hover, focus and hidden pages, and finished states without auto-play under reduced motion (WAI-ARIA carousel pattern). The form now comes first in the DOM, with the aside placed on the left by the grid; the aside isn't rendered below 800 px. Where this section describes the specimens or an aria-hidden aside, the code wins.

- **Desktop:** a split, `1.08fr | 1fr`, full height.
- **Aside** (`.login-aside`, the desk with a right hairline, padding 40 56 44, `aria-hidden`):
  - The brand.
  - A soft `--brand` radial glow (520px, 20%→6%→transparent) off the top-right corner.
  - The **hero**: a serif 54/1.05 headline "Ask your documents *anything.*", then a 17/1.55 `--text-2` deck at 42ch.
  - Two rotated **specimen cards**: a PDF page with highlighted lines (-3°), and an answer card (+1.5°, `--shadow-lg`)
    with the serif answer, a citation and a "2 sources" pill.
  - At the bottom, three 14.5px points with `--accent-ink` icons: "Private to you", "Cited answers", "Files and images
    on request".
- **Form side:**
  - A centred card (max 392px): "Welcome back" (serif 36/500), the lede "Sign in to continue to your documents and
    chats." (16 `--text-3`), then the form 32px below with gap 18.
  - Inputs are 46px. The password is FOUNDATION's `PasswordInput` (6.2's toggle). The button is primary, 46px, radius `--radius`, 16px.
  - Errors (`.login-error`): a `--danger-soft` block, radius 10, 14/600 `--danger`, with an alert icon.
  - The foot note: a `--surface-2` block with a shield icon, "Accounts are created by your administrator…".
- **Phones:** the aside is hidden. A **compact hero** goes above the card (`.login-mobile-hero`): the brand, 30px of
  space, the serif 38/1.06 headline with the italic accent word, and the line "Every answer points to the passages
  it came from." at 16. The card title is 26px and the form 20px below it.

### 6.29 Admin (RESTYLE, `admin.css`)

- **Page:** on `--bg`.
- **Top bar** (`.admin-topbar`): sticky, 60px, inner max width 1240, padding 0 24, `color-mix(--bg 92%)` with an 8px
  backdrop blur, a bottom hairline.
  - Contents: the brand; an `accent` badge "Admin"; a spacer; "who am I" (a 32px avatar and the name, 14.5/600
    `--text-2`); "Settings" (ghost sm, gear); "Back to chat" (sm, back arrow); "Sign out" (ghost sm).
  - Phones: only icons and "Back to chat".
- **Main:** max 1240, padding 32 24 56.
  - Title: "Administration" (serif 34/500), then the subtitle (15 `--text-3`).
  - **Overview tiles** (`dl`, a 4-column grid with gap 12, 2 columns on phones):
    - Tile: `--surface`, a 1px `--border`, radius 16, padding 14 16, `--shadow-xs`.
    - Contents: a label at 13.5/600 `--text-2`, then the serif 27/600 value. Model names are sans 15.5/650 with an
      ellipsis.
    - Tones: danger tiles get the border at `danger 35%` and the value in `--danger`; warning values are
      `--warning`.
    - The queue tile's detail is "Indexing" (info with a spinner) or "Idle".
  - Notices below the tiles.
- **Tabs** (`.admin-tabs`, 32px below):
  - Underline tabs on a bottom hairline. Each tab is 44px, padding 0 14, 15/600 `--text-2` with a 15px icon (users,
    cpu, chart, docs, plug).
  - Hover: a `--surface-3` 50% fill on the top corners.
  - Selected: `--text`, an `--accent-ink` icon and a 2.5px `--accent` bar 10px in from each side.
  - The strip scrolls sideways on phones.
- **Cards** (`.card.admin-card`, 24px below the tabs):
  - Header: padding 18 20, a bottom hairline, the serif 21/600 title, the subtitle at 14 `--text-3`, and the primary
    action at the right ("+ New user").
  - Table (6.27): the user cell (a 32px avatar, the name 650 `--text`, the "admin" accent badge, the "inactive"
    neutral badge); meter cells (the value line and a 5px meter, min 112px); sub-text at 13 `--text-3`; the actions
    as ghost sm Edit / Delete (env-managed: disabled, with the reason in `title` and `sr-only`). The actions column
    is sticky at the right (`--surface`, a left hairline), so Edit and Delete stay in view while a wide table
    scrolls; while columns lie under it, a 14px shade falls on them from its edge (a scroll-driven `::before`, gone at
    the scroll end and when the table fits; without scroll-driven animations the hairline is the only cue); usernames
    wrap at 20ch.
  - Footnote: padding 12 20, a top hairline, `--bg`, 13.5 `--text-3`.
- **Forms** (user and model dialogs): `.field`s in a 2-column grid at 720px and up, with `.settings-sub`-style
  section labels, and switches (6.3) for flags.
- **Confirmations:** the `chat-conv-confirm` look, or a Modal with a danger action.
- **Plugins tab:** connection-card styling (6.24) for blocks, `.table` for activity, and mono 13 on `--code-bg` for
  redirect URIs.

---

## 7. Dark mode rules

1. **Tokens only:** nothing re-colours itself under `[data-theme=dark]` except the token blocks. Previews and shadows
   are the exceptions (2).
2. **Elevation:** surfaces get lighter as they rise; shadows switch to black with higher opacity; the hairline
   borders stay visible (`#3a3631` on `#262421`).
3. **Fills carry dark ink:** accent, danger and status fills are light colours with dark text (`--accent-text`,
   `--danger-text`); white text on a fill appears only in light mode.
4. **Soft fills are dark tints** (`--accent-soft` `#44302a`), and the ink on them is the light `--accent-ink`.
5. **Images aren't filtered.** Generated images and thumbnails keep their colours. The login glow drops to the
   dark brand.
6. **Switch thumbs:** light (`--switch-thumb`) on the off track in both themes; on the on track, light in light mode
   and the accent's dark ink in dark mode (`--switch-thumb-on`), because the dark-mode accent track is light.
7. **Highlights** (`mark`, selection) use the accent at 16–24% over the ground, never a solid fill.
8. **Check every new component in both themes and all five accents.** `states.html?theme=dark&accent=…` is the
   reference.

---

## 8. Mobile and touch rules

- **Breakpoints stay 1100 and 800** (the layout contract). The checks run at 1440, 1000, 700 and 390 px. The page
  never scrolls horizontally, and wide things scroll inside their own wrappers (answer tables, admin tables, the tab
  strips).
- **Under 800px:**
  - **Top bar:** 56px. Plugins and Documents are icon-only, 40×40 (the column is under 560px; the rule is a container
    query, 6.10), and a long model name ellipsizes.
  - **Thread:** the assistant mark is hidden and the thread gutter is 16px.
  - **Composer:** radius 20; "+", send and stop are 40px.
  - **Hint:** the static key hint is hidden, but dynamic hint messages still show.
  - **Cards:** file and image card buttons are icon-only at 40px.
  - **Menus:** every menu is a **bottom sheet** (6.9). The documents panel is the right slide-over and the sidebar
    the left drawer.
  - **Settings:** a full-screen sheet with the scrolling tab strip; the text-size control and the model select are
    full width; usage meters stack in one column; facts go to 2 columns.
  - **Login:** the compact hero.
  - **Admin:** tiles go to 2 columns and the top-bar labels collapse.
- **`@media (pointer: coarse)` at any width:** the top-bar buttons and the model button are 40px tall; "+", send and
  stop are 40px; menu items have a minimum height of 44px.
- **Safe areas:** the composer area, bottom sheets and the Settings sheet pad with `env(safe-area-inset-*)`.
- **Inputs are at least 16px** where people type (composer, login), so iOS doesn't zoom.

---

## 9. Motion

| What | How |
|---|---|
| Hover, press, colour | `--dur-1`; buttons move down 0.5px on `:active`; send scales to 0.94 |
| Menus | `--dur-2 --ease-out`: opacity 0→1 with a 4px rise (`menu-in`, or `menu-up` for the "+" menu) and `scale(.985)` from the trigger corner; the "+" glyph rotates 45° |
| Bottom sheets | `--dur-3 --ease-out` from `translateY(100%)`; the scrim fades over `--dur-2` |
| Dialogs, Settings | `--dur-3 --ease-out`: a 10px rise and `scale(.985)`; the backdrop fades over `--dur-2` |
| Drawers, slide-over, documents collapse | `--dur-3 --ease-out` transform (or grid columns) |
| Switch, segmented | `--dur-2 --ease-out` |
| Image preview | hover zoom to 1.015 over `--dur-3` |
| Example rows | the arrow moves 2px over `--dur-1` |
| Streaming caret, typing dots, skeleton shimmer, spinners | the only looping motion |

- **Reduced motion:** under `prefers-reduced-motion: reduce`, every animation and transition is
  `0.001ms !important` (the global rule stays), and loops run once. Spinners stay visible and still.
- **Theme switches:** during a switch (`data-theme-switching`), transitions are off.

---

## 10. Accessibility rules this design depends on

- **Contrast:** every text pair is ≥ 4.5:1 and every control boundary or meaningful mark ≥ 3:1, in both themes and
  all five accents (11). Disabled controls are exempt, but their **reasons** are not.
- **Focus:** a visible focus everywhere: `--focus-ring` (a 2px gap and a 2px accent ring). Menu items use an inset 2px
  accent outline. Inputs show the accent border plus halo. Never remove focus without a replacement.
- **Never colour alone:**
  - Selected theme cards and swatches carry a check.
  - Selected segments carry a 3:1 `--border-control` ring; tabs change fill or weight.
  - Statuses carry words plus a dot, lock or dashed mark.
  - The Documents toggle changes fill and text colour along with `aria-expanded`.
  - Meters carry numbers.
- **Forced colours (Windows contrast themes):** box-shadows and fills are dropped there, so `@media (forced-colors:
  active)` blocks redraw the focus ring as a 2px `Highlight` outline, and the switch track and thumb, the selected
  segment, the meter bar, the selected theme card and the admin tab bar in system colours.
- **Targets:** at least 24×24 everywhere (WCAG 2.5.8): the smallest stand-alone controls are the 28px chips and xs
  buttons; citation chips (19px) sit inside a line of text, which the criterion's inline exception covers; the 18px
  checkboxes and radios get a 24px hit area through their label (padding, or the whole row as the label). 40px for
  the top bar and composer on touch.
- **Type:** the answer prose is never under 15.5px, the UI never under 12px, and line lengths are capped (740px
  thread).
- **Order and names:** the documented hierarchy matches the DOM order. Decorative glyphs (file tags, source dots,
  previews, the assistant mark) are `aria-hidden`; the information they carry is in text or accessible names.

---

## 11. Contrast (WCAG 2.2, relative luminance)

`python3 -I contrast_audit.py paper-clay.css` (revision 2, in `…/wf7/designer-rev/`) checks **330 pairs, 0 under the
minimum** on the revision 2 tokens (3.9). Revision 1 checked 290; the 40 new pairs are the status colours on the quiet
and hover fills, `--text-3` and `--accent-ink` on `--surface-3`, and the switch thumb against both tracks. It reads the
token blocks; FOUNDATION points it at `global.css` (same selectors) and reports the table. It also prints, for
information, the CIE76 ΔE between each accent fill and `--danger` (0.3). The table below has the base pairs; each pair's
minimum is in the last column.

| Foreground | Background | Light | Dark | Needs |
|---|---|---|---|---|
| `--text` | `--bg` / `--sidebar` / `--surface` / `--surface-2` / `--bubble` / `--code-bg` / `--surface-3` | 14.30 / 13.32 / 14.94 / 13.32 / 12.66 / 13.34 / 12.08 | 13.27 / 14.57 / 11.91 / 12.74 / 10.75 / 14.88 / 10.28 | 4.5 |
| `--text-2` | same seven | 7.23 / 6.74 / 7.55 / 6.74 / 6.40 / 6.74 / 6.11 | 9.14 / 10.03 / 8.20 / 8.77 / 7.40 / 10.25 / 7.08 | 4.5 |
| `--text-3` | `--bg` / `--sidebar` / `--surface` / `--surface-2` / `--bubble` / `--code-bg` / `--surface-3` | 5.39 / 5.02 / 5.63 / 5.02 / 4.77 / 5.03 / 4.55 | 5.93 / 6.50 / 5.32 / 5.68 / 4.80 / 6.64 / 4.59 | 4.5 |
| `--success` | `--bg` / `--surface` / `--sidebar` / `--success-soft` | 5.55 / 5.80 / 5.17 / 4.98 | 7.59 / 6.81 / 8.33 / 6.77 | 4.5 |
| `--warning` | `--bg` / `--surface` / `--sidebar` / `--warning-soft` | 5.40 / 5.64 / 5.03 / 4.85 | 8.07 / 7.24 / 8.86 / 6.92 | 4.5 |
| `--danger` | `--bg` / `--surface` / `--sidebar` / `--danger-soft` | 6.11 / 6.38 / 5.69 / 5.21 | 6.50 / 5.83 / 7.13 / 5.88 | 4.5 |
| `--info` | `--bg` / `--surface` / `--sidebar` / `--info-soft` | 5.91 / 6.18 / 5.51 / 5.23 | 7.48 / 6.71 / 8.21 / 6.70 | 4.5 |
| `--success` / `--warning` / `--danger` / `--info` *(rev. 2)* | `--surface-2` | 5.17 / 5.03 / 5.69 / 5.51 | 7.28 / 7.74 / 6.24 / 7.18 | 4.5 |
| `--success` / `--warning` / `--danger` / `--info` *(rev. 2)* | `--surface-3` (hover and pressed fills) | 4.69 / 4.56 / 5.16 / 5.00 | 5.88 / 6.25 / 5.03 / 5.79 | 4.5 |
| `--danger-text` | `--danger` / `--danger-hover` | 6.48 / 8.45 | 7.61 / 9.30 | 4.5 |
| `--text` | `--danger-soft` / `--warning-soft` | 12.19 / 12.85 | 12.00 / 11.38 | 4.5 |
| `--tag-*` | `--tag-*-soft` (red, blue, green, ochre, plum, ink) | 5.10, 5.15, 4.58, 4.88, 5.54, 6.17 | 6.66, 6.83, 7.06, 7.02, 6.60, 7.08 | 4.5 |
| `--avatar-ink` | `--avatar-bg` | 7.51 | 8.22 | 4.5 |
| `--brand-text` | `--brand` | 5.55 | 6.93 | 4.5 |
| `--bg` (toast text) | `--text` | 14.30 | 13.27 | 4.5 |
| `--border-control` | `--bg` / `--sidebar` / `--surface` / `--surface-2` | 3.45 / 3.22 / 3.61 / 3.22 | 3.73 / 4.10 / 3.35 / 3.58 | 3 |
| `--warning`, `--danger` meter fills | `--surface-3` track | 4.56, 5.16 | 6.25, 5.03 | 3 |
| `--switch-thumb` *(rev. 2)* | `--border-control` (the off track) | 3.61 | 4.08 | 3 |

| Preset | Theme | `--accent-text` on accent | … on hover | accent on `--bg` | on `--surface` | on `--sidebar` | on `--accent-soft` | `--accent-ink`, lowest of 7 grounds | accent as a mark, lowest of 5 grounds |
|---|---|---|---|---|---|---|---|---|---|
| Clay | light | 5.55 | 6.91 | 5.23 | 5.46 | 4.87 | 4.64 | 5.43 | 4.42 |
| Clay | dark | 6.93 | 8.37 | 6.04 | 5.42 | 6.62 | 4.81 | 6.90 | 4.67 |
| Ocean | light | 6.23 | 8.08 | 5.87 | 6.13 | 5.47 | 5.18 | 6.24 | 4.96 |
| Ocean | dark | 8.49 | 10.03 | 7.59 | 6.81 | 8.33 | 5.91 | 7.71 | 5.88 |
| Sage | light | 5.43 | 6.99 | 5.12 | 5.35 | 4.77 | 4.52 | 5.77 | 4.33 |
| Sage | dark | 7.95 | 9.47 | 7.33 | 6.58 | 8.04 | 5.67 | 7.49 | 5.68 |
| Plum | light | 6.24 | 7.90 | 5.88 | 6.15 | 5.48 | 5.05 | 6.30 | 4.97 |
| Plum | dark | 7.81 | 9.35 | 6.84 | 6.14 | 7.51 | 5.63 | 7.50 | 5.30 |
| Slate | light | 7.29 | 9.49 | 6.87 | 7.18 | 6.41 | 5.92 | 7.25 | 5.81 |
| Slate | dark | 9.35 | 11.19 | 8.35 | 7.49 | 9.16 | 6.31 | 8.18 | 6.47 |

How to read the last two columns:

- **"Lowest of 7 grounds"** is `--bg`, `--sidebar`, `--surface`, `--surface-2`, `--bubble`, `--code-bg` and
  `--accent-soft`.
- **"Accent as a mark"** covers the non-text uses: the focus ring, the checkbox fill, the switch on, meters and the
  selected-tab bar. It is measured against `--bg`, `--sidebar`, `--surface`, `--surface-2` and `--surface-3`; the
  minimum is 3:1, and the lowest is 4.33.
- **`--accent-ink` on `--surface-3`** *(rev. 2)*: 5.18 to 7.11 in light, 6.71 to 8.38 in dark (Clay lowest in both).
- **The "on" switch thumb** *(rev. 2)* against the accent track: light (`#fffdf9`) 5.35 (Sage) to 7.18 (Slate); dark
  (`--accent-text`) 6.93 (Clay) to 9.35 (Slate). Minimum 3.
- **The tightest pairs are:** Sage on its soft fill (4.52), `--text-3` on `--surface-3` (4.55), `--warning` on
  `--surface-3` (4.56), `--tag-green` on its soft (4.58), Clay on its soft (4.64), `--success` on `--surface-3` (4.69)
  and `--border-control` on the desk (3.22). Don't lighten these tokens.
- **Accent vs danger (informational):** ΔE 23.1 (Clay light), 26.9 (Clay dark), 25.6 (Plum dark), 44 to 80 for the
  rest; contrast ratios between the two fills are 1.04 to 1.28. Words and icons carry danger (6.1, 6.6), never the hue
  alone.

---

## 12. Mock class → app class and owner

| Mockup | App | Owner |
|---|---|---|
| `.ui-menu*`, `.ui-avatar*`, `.ui-segmented`, `.ui-segment`, `.ui-meter*`, `.ui-status*`, `.ui-skeleton`, `.ui-check`, `.ui-radio`, `.ui-password*`, `.ui-notice*`, `.ui-toast`, `.ui-brand*`, `.ui-fullpage*` *(rev. 2)*, `.btn-xs`, `.btn-pill`, `.badge-accent` | the same names in `global.css` (new primitives; `PasswordInput`, `FullPageState`, `Menu` and `Meter` render them) | FOUNDATION |
| `.btn*`, `.badge*`, `.input`, `.select`, `.textarea`, `.field*`, `.error-text`, `.switch`, `.card`, `.table`, `.table-wrap`, `.modal*`, `.spinner`, `.sr-only` | the same (restyled) | FOUNDATION |
| `.chat-topbar`, `.chat-model-btn`, `.chat-topbar-title`, `.chat-tb-btn`, `.chat-tb-dot`, `.chat-docs-toggle`, `.chat-avatar-btn`, `.chat-plugins-menu`, `.chat-plugin-*`, `.chat-avatar-menu*`, `.chat-avatar-usage`, `.chat-theme-row`, `.chat-model-menu`, `.chat-model-*` | new in `chat.css` (`TopBar`, `ModelMenu`, `PluginsMenu`, `AvatarMenu`; replace `.chat-header`, `.chat-model-select`, `.chat-user*`, `.chat-usage*`) | SHELL |
| `.chat-plus`, `.chat-plus-menu`, `.chat-plus-formats`, `.chat-plus-format`, `.chat-plus-doc-row`, `.chat-attachments`, `.chat-attachment*`, `.chat-stop` | new in `chat.css` | SHELL |
| `.chat-cite`, `.chat-cite-glue`, `.chat-md-table`, `.chat-code*`, `.chat-sources-toggle`, `.chat-src-dot`, `.chat-sources`, `.chat-source*`, `.chat-icon-btn`, `.chat-send`, `.chat-scope`, `.chat-messages`, `.chat-empty*`, `.chat-example*`, `.chat-note*`, `.chat-typing`, `.chat-jump`, `.chat-file*`, `.chat-image*`, `.chat-conv*`, `.chat-new-chat`, `.chat-sidebar*` | the existing classes, restyled (the mock's `chat-messages` is the app's `chat-messages`/`chat-thread` pair) | SHELL |
| `.chat-mail-*` | `plugins.css` | SHELL |
| `.docs-*` (including `.docs-tag`, the FileTag) | `documents.css` | RESTYLE |
| `.conn-*` | `connections.css` | RESTYLE |
| `.settings-*`, the `.mini*` previews | `settings.css` | SETTINGS |
| `.login-*` | `src/pages/login.css` (rules, revision 2) and `LoginPage.tsx` (markup) | RESTYLE |
| `.admin-*` | `admin.css` | RESTYLE |
| `.sheet-*`, `.preset-*`, `.is-focus`, `.is-hover`, `.is-active`, the `[data-menu]` and `[data-tab]` switches, `mock.js` | mockup only | — |

---

## 13. Builder checklist

FOUNDATION:

1. **Tokens:** paste 3.9 into `global.css` (keep every old token name; the new ones are listed). Switch `.input`,
   `.select`, `.textarea` and the switch track to `--border-control`, give the checked switch's thumb
   `--switch-thumb-on`, and add the `ui-*` primitives of section 6 (`ui-fullpage*` included). Restyle the old
   primitives. Add the `data-theme-switching` rule. Remove the old `prefers-color-scheme` block. No `login-*` rules
   (RESTYLE's `pages/login.css`).
2. **Fonts:** produce them with appendix A and check the three sha256 values. Write `fonts.css` (appendix B, with the
   provenance comment and the five fallback faces; re-run appendix C if the font files differ), import it from
   `main.tsx` and place the licences beside the files.
3. **Contrast:** run the revision 2 `contrast_audit.py` against `global.css`: 330 pairs, 0 failures. Paste the table
   into the stage report.

SHELL, SETTINGS and RESTYLE:

4. Build their areas to section 6 and compare against the screenshots in the table at the top (same widths, both
   themes).
5. **Check visually:**
   - In dark mode the primary button is clay and the destructive button crimson, and the destructive one always names
     its action; full meters say "Limit reached".
   - The top bar never overflows: 820, 1100 and 1110px with the documents panel open, and a long model name.
   - The usage chart's labels are 12px at 390px, and its foot wraps inside the card.
   - The dark "on" switch thumb is dark ink on every accent.
   - Plugin, avatar and model menus open as bottom sheets at 390.
   - No citation chip starts a line at 390.
   - Disabled "+" items keep readable reasons.
   - The empty state never guesses a first name.
6. **Verify:** `grep -r "prefers-color-scheme" frontend/src` finds nothing; a grep over each CSS file finds only its
   own prefix plus the shared primitives; no hex colours in feature CSS (except the theme previews in `settings.css`).

---

## Appendix A: `subset_fonts.py` (run in a scratch venv; not app code)

```python
"""Subset the upstream Source Serif 4 / Source Sans 3 variable woff2 files to Latin and rename them.

The serif keeps wght 400-700 and opsz 16-60 (the design uses 400-700 at 15.5 px and up); the sans keeps its full
wght axis.

Usage: python -I subset_fonts.py <upstream_dir> <out_dir>
Needs fonttools==4.66.1 and brotli==1.1.0 (a scratch venv; not an app dependency).

The OFL Reserved Font Name "Source" forbids a Modified Version (a subset is one) from using it as the primary font
name, so the family is renamed in the name table ("RAG Serif", "RAG Sans"); copyright, trademark, vendor, designer,
licence and URL records (IDs 0, 7-14) are kept as they are.
"""

import hashlib
import sys
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

UNICODES = (
    "U+0000-00FF,U+0100-017F,U+0192,U+0218-021B,U+0237,U+02BB-02BC,U+02C6-02C7,U+02D8-02DD,"
    "U+0300-0308,U+030A-030C,U+0326-0328,U+1E9E,U+2000-206F,U+20AC,U+20B9,U+2113,U+2116,U+2122,"
    "U+2190-2195,U+2212,U+2215,U+2318,U+2713,U+FB01-FB02,U+FEFF,U+FFFD"
)
FEATURES = (
    "ccmp,locl,mark,mkmk,kern,liga,calt,rvrn,frac,numr,dnom,sups,subs,sinf,ordn,"
    "tnum,pnum,lnum,onum,case,zero,smcp,c2sc"
)
SERIF_AXES = {"wght": (400, 700), "opsz": (16, 60)}
FILES = {  # upstream file -> (bundled file, axis limits)
    "SourceSerif4Variable-Roman.ttf.woff2": ("RAGSerif-Roman.woff2", SERIF_AXES),
    "SourceSerif4Variable-Italic.ttf.woff2": ("RAGSerif-Italic.woff2", SERIF_AXES),
    "SourceSans3VF-Upright.ttf.woff2": ("RAGSans-Upright.woff2", None),
}
FAMILY = [("Source Serif 4 Variable", "RAG Serif"), ("Source Serif 4", "RAG Serif"), ("SourceSans3VF", "RAG Sans"),
          ("Source Sans 3", "RAG Sans")]
POSTSCRIPT = [("SourceSerif4Variable", "RAGSerif"), ("SourceSerif4", "RAGSerif"), ("SourceSans3VF", "RAGSans"),
              ("SourceSans3", "RAGSans")]
KEEP_IDS = {0, 7, 8, 9, 10, 11, 12, 13, 14}


def rename(font: TTFont) -> None:
    postscript_ids = {3, 6, 25}
    if "fvar" in font:
        postscript_ids |= {i.postscriptNameID for i in font["fvar"].instances if i.postscriptNameID != 0xFFFF}
    for record in font["name"].names:
        if record.nameID in KEEP_IDS:
            continue
        text = record.toUnicode()
        for old, new in POSTSCRIPT if record.nameID in postscript_ids else FAMILY:
            text = text.replace(old, new)
        record.string = text
    leftovers = [(r.nameID, r.toUnicode()) for r in font["name"].names
                 if r.nameID not in KEEP_IDS and "Source" in r.toUnicode()]
    if leftovers:
        raise SystemExit(f"name records still say Source: {leftovers}")


def main(src: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    options = subset.Options()
    options.flavor = "woff2"
    options.layout_features = FEATURES.split(",")
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.notdef_outline = True
    options.recalc_timestamp = False
    unicodes = subset.parse_unicodes(UNICODES)
    total = 0
    for upstream, (target, axes) in FILES.items():
        font = subset.load_font(str(src / upstream), options)
        subsetter = subset.Subsetter(options)
        subsetter.populate(unicodes=unicodes)
        subsetter.subset(font)
        if axes:
            font = instancer.instantiateVariableFont(font, axes, updateFontNames=False)
        rename(font)
        path = out / target
        subset.save_font(font, str(path), options)
        data = path.read_bytes()
        total += len(data)
        print(f"{target}  {len(data) / 1024:.1f} KB  sha256 {hashlib.sha256(data).hexdigest()}")
    print(f"total {total / 1024:.1f} KB")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
```

## Appendix B: `fonts.css`

```css
/* RAG Serif / RAG Sans: Latin subsets of Source Serif 4 (adobe-fonts/source-serif 4.005R, 2823e993) and Source Sans 3
   (adobe-fonts/source-sans release, 87b37a2d), SIL OFL 1.1, renamed per the Reserved Font Name clause.
   Upstream and derived sha256: docs/briefs/ui-visual-design.md section 4.1. */
@font-face {
  font-family: "RAG Serif";
  src: url("../assets/fonts/RAGSerif-Roman.woff2") format("woff2");
  font-weight: 400 700;
  font-style: normal;
  font-display: swap;
  unicode-range: U+0000-017F, U+0192, U+0218-021B, U+0237, U+02BB-02BC, U+02C6-02C7, U+02D8-02DD, U+0300-030C,
    U+0326-0328, U+1E9E, U+2000-206F, U+20AC, U+20B9, U+2113, U+2116, U+2122, U+2190-2195, U+2212, U+2215, U+2713,
    U+FB01-FB02, U+FEFF, U+FFFD;
}
@font-face {
  font-family: "RAG Serif";
  src: url("../assets/fonts/RAGSerif-Italic.woff2") format("woff2");
  font-weight: 400 700;
  font-style: italic;
  font-display: swap;
  unicode-range: U+0000-017F, U+0192, U+0218-021B, U+0237, U+02BB-02BC, U+02C6-02C7, U+02D8-02DD, U+0300-030C,
    U+0326-0328, U+1E9E, U+2000-206F, U+20AC, U+20B9, U+2113, U+2116, U+2122, U+2190-2195, U+2212, U+2215, U+2713,
    U+FB01-FB02, U+FEFF, U+FFFD;
}
@font-face {
  font-family: "RAG Sans";
  src: url("../assets/fonts/RAGSans-Upright.woff2") format("woff2");
  font-weight: 200 900;
  font-style: normal;
  font-display: swap;
  unicode-range: U+0000-017F, U+0192, U+0218-021B, U+0237, U+02BB-02BC, U+02C6-02C7, U+02D8-02DD, U+0300-030C,
    U+0326-0328, U+1E9E, U+2000-206F, U+20AC, U+20B9, U+2113, U+2116, U+2122, U+2190-2195, U+2212, U+2215, U+2318,
    U+2713, U+FB01-FB02, U+FEFF, U+FFFD;
}

/* Metric-matched fallbacks (revision 2): local system faces scaled to the bundled fonts' widths and vertical metrics,
   so text doesn't re-wrap or jump when the web fonts arrive. Second in --font-serif / --font-sans. Values from
   appendix C of docs/briefs/ui-visual-design.md; re-run it when the font files change. Where the local face is
   missing (Linux, Android) the rule matches nothing and the stack goes on. */
@font-face {
  font-family: "RAG Serif Fallback";
  src: local("Georgia");
  font-weight: 400;
  font-style: normal;
  size-adjust: 105.33%;
  ascent-override: 98.36%;
  descent-override: 31.8%;
  line-gap-override: 0%;
}
@font-face {
  font-family: "RAG Serif Fallback";
  src: local("Georgia Bold"), local("Georgia-Bold");
  font-weight: 700; /* also used for 600 and 650 */
  font-style: normal;
  size-adjust: 88.08%;
  ascent-override: 117.62%;
  descent-override: 38.03%;
  line-gap-override: 0%;
}
@font-face {
  font-family: "RAG Serif Fallback";
  src: local("Georgia Italic"), local("Georgia-Italic");
  font-weight: 400;
  font-style: italic;
  size-adjust: 93.94%;
  ascent-override: 110.28%;
  descent-override: 35.66%;
  line-gap-override: 0%;
}
@font-face {
  font-family: "RAG Sans Fallback";
  src: local("Arial"), local("ArialMT");
  font-weight: 400;
  font-style: normal;
  size-adjust: 93.68%;
  ascent-override: 106.74%;
  descent-override: 34.8%;
  line-gap-override: 0%;
}
@font-face {
  font-family: "RAG Sans Fallback";
  src: local("Arial Bold"), local("Arial-BoldMT");
  font-weight: 700; /* also used for 600 and 650 */
  font-style: normal;
  size-adjust: 89.51%;
  ascent-override: 111.73%;
  descent-override: 36.42%;
  line-gap-override: 0%;
}
```

## Appendix C: `fallback_metrics.py` (revision 2; run in the appendix A scratch venv; not app code)

Run as `python -I fallback_metrics.py <dir with the three bundled woff2 files> /System/Library/Fonts/Supplemental`
on macOS. With the bundled files of 4.1 it prints the values of 4.1 and appendix B.

```python
"""Metric overrides for the "RAG Serif Fallback" / "RAG Sans Fallback" @font-face rules (visual spec 4.1, appendix B).

Usage: python -I fallback_metrics.py <bundled_fonts_dir> <system_fonts_dir>
Needs fonttools (and brotli for woff2). For each bundled face at the weight (and optical size) the design sets, and the
local system face the browser shows until the web font arrives, it prints size-adjust (the ratio of the weighted
average advance width of English text) and ascent/descent/line-gap overrides (the bundled font's hhea metrics divided
by size-adjust), the method Next.js and Capsize use. The values depend only on the font files, so they are stable.
"""

import sys
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

# Letter frequencies of English prose (space included), as in @capsizecss/metrics' xWidthAvg.
FREQ = {
    " ": 0.1874, "e": 0.0957, "t": 0.0697, "a": 0.0610, "o": 0.0590, "i": 0.0560, "n": 0.0552, "s": 0.0490,
    "r": 0.0469, "h": 0.0416, "l": 0.0313, "d": 0.0298, "c": 0.0237, "u": 0.0222, "m": 0.0189, "f": 0.0167,
    "p": 0.0152, "g": 0.0148, "w": 0.0144, "y": 0.0128, "b": 0.0113, ",": 0.0104, ".": 0.0094, "v": 0.0077,
    "k": 0.0054, "T": 0.0035, "I": 0.0031, "A": 0.0025, "S": 0.0021, "x": 0.0016, "C": 0.0015, "j": 0.0012,
    "q": 0.0009, "z": 0.0007, "M": 0.0012, "W": 0.0008, "B": 0.0008, "H": 0.0010, "N": 0.0008, "P": 0.0008,
}

FACES = [  # (label, bundled file, axes, system file, local() names)
    ("RAG Serif Fallback 400 normal", "RAGSerif-Roman.woff2", {"wght": 400, "opsz": 16.5}, "Georgia.ttf",
     'local("Georgia")'),
    ("RAG Serif Fallback 700 normal", "RAGSerif-Roman.woff2", {"wght": 600, "opsz": 22}, "Georgia Bold.ttf",
     'local("Georgia Bold"), local("Georgia-Bold")'),
    ("RAG Serif Fallback 400 italic", "RAGSerif-Italic.woff2", {"wght": 400, "opsz": 18}, "Georgia Italic.ttf",
     'local("Georgia Italic"), local("Georgia-Italic")'),
    ("RAG Sans Fallback 400 normal", "RAGSans-Upright.woff2", {"wght": 400}, "Arial.ttf",
     'local("Arial"), local("ArialMT")'),
    ("RAG Sans Fallback 700 normal", "RAGSans-Upright.woff2", {"wght": 600}, "Arial Bold.ttf",
     'local("Arial Bold"), local("Arial-BoldMT")'),
]


def avg_width(font: TTFont) -> float:
    cmap = font.getBestCmap()
    hmtx = font["hmtx"]
    upm = font["head"].unitsPerEm
    total = weight = 0.0
    for char, freq in FREQ.items():
        glyph = cmap.get(ord(char))
        if glyph is None:
            continue
        total += hmtx[glyph][0] * freq
        weight += freq
    return total / weight / upm


def main(bundled: Path, system: Path) -> None:
    for label, file, axes, sys_file, local in FACES:
        font = TTFont(str(bundled / file))
        if "fvar" in font:
            present = {a.axisTag for a in font["fvar"].axes}
            font = instancer.instantiateVariableFont(font, {k: v for k, v in axes.items() if k in present})
        fallback = TTFont(str(system / sys_file))
        size_adjust = avg_width(font) / avg_width(fallback)
        upm = font["head"].unitsPerEm
        hhea = font["hhea"]
        ascent = hhea.ascent / upm / size_adjust
        descent = abs(hhea.descent) / upm / size_adjust
        line_gap = hhea.lineGap / upm / size_adjust
        print(f"{label}: src {local}; size-adjust {size_adjust * 100:.2f}%; ascent-override {ascent * 100:.2f}%; "
              f"descent-override {descent * 100:.2f}%; line-gap-override {line_gap * 100:.2f}%")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
```
