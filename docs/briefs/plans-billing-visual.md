# Visual spec: plans, checkout and billing

> How the paid-plans feature looks, built on the "Paper & Clay" design system of
> [`ui-visual-design.md`](ui-visual-design.md) (**the base spec**). The behaviour, the contract and the copy that
> carries behaviour are in [`plans-billing.md`](plans-billing.md) (**the functional brief**); `SPEC.md` binds both. On
> looks (layout, type, colour, spacing, the composition of each screen) this document wins; on behaviour,
> accessibility and contract the functional brief and `SPEC.md` win. Section 0.2 records where revision 1 of this
> spec bent the functional brief and how each point was settled. **Revision 2**, 2026-10-26, final: designed and
> reviewed; built and deployed on 2026-10-27. The functional brief's revision 2 changed some things the mockups show; section 0.4 lists
> them, and where a screenshot and this text differ, **the text wins**.
>
> **Reference mockup** (static HTML on the app's own CSS):
> `/private/tmp/claude-501/-Users-bikash-rag-app/73efbff7-64da-4b99-a056-7cb30809179c/scratchpad/wf-plans/visual/`
> (`pages/*.html`; `css/` holds copies of the app's `global.css`, `fonts.css` and feature CSS plus the new
> `billing.css`; `fonts/` the bundled woff2 files). The chat, Settings and Admin mockups are the **real built app**
> (`frontend/dist`) rendered with canned API answers by `capture/capture.mjs` and then edited by `build.py`, so
> everything around the new parts is pixel-identical to the app. `shoot.mjs` takes the screenshots and checks for page
> errors, failed requests and horizontal overflow; `audit.py` checks the new colour pairs; `receipt_mock.py` renders
> the receipt layout with fpdf2 and the app's Noto Sans. Scratch paths can disappear: every value a builder needs is in
> this file.

## Screenshots

In `…/wf-plans/visual/shots/`. 1440 px shots at device pixel ratio 1, 390 px shots at 2. The standalone pages (plans,
checkout, return, admin, public) are full-page shots; the chat and Settings shots are the viewport, as the app shows
them, unless noted.

| Screen | Files |
|---|---|
| **Plan page, new user** (`none`, test mode, Standard recommended) | `plans-none-1440-light.png`, `plans-none-1440-dark.png`, `plans-none-390-light.png`, `plans-none-390-dark.png`, `plans-none-1440-dark-ocean.png` (viewport, Ocean accent) |
| **Plan page, active** (Starter, 5 days left, a payment in progress, upgrade prices) | `plans-active-1440-light.png`, `plans-active-1440-dark.png`, `plans-active-390-light.png` |
| **Checkout** (review and pay) | `checkout-1440-light.png`, `checkout-1440-dark.png`, `checkout-390-light.png` (whole page), `checkout-390-dark.png` (viewport: the sticky pay bar) |
| Leaving for Razorpay | `checkout-redirect-1440-light.png`, `checkout-redirect-390-dark.png` (viewport) |
| **Return: payment received** | `return-success-1440-light.png`, `return-success-1440-dark.png`, `return-success-390-light.png`, `return-success-390-dark.png` |
| **Return: not completed** | `return-failure-1440-light.png`, `return-failure-1440-dark.png`, `return-failure-390-light.png`, `return-failure-390-dark.png` |
| Return: pending, still waiting, review, stopped checking | `return-states-1440-light.png`, `return-states-1440-dark.png` |
| **Chat, Starter: expiry banner and the locked "+" menu** | `chat-locked-1440-light.png`, `chat-locked-1440-dark.png`, `chat-locked-390-light.png`, `chat-locked-390-dark.png` (bottom sheet), `chat-locked-1440-light-sage.png` (Sage accent) |
| Model menu, Plugins menu, avatar menu (Starter) | `chat-model-menu-1440-light.png`, `chat-plugins-menu-1440-light.png`, `chat-plugins-menu-1440-dark.png`, `chat-avatar-menu-1440-light.png` (viewport) |
| **Settings → Plan & billing** (whole panel shown; the real dialog scrolls it) | `settings-billing-1440-light.png`, `settings-billing-1440-dark.png`, `settings-billing-1440-dark-plum.png`, `settings-billing-390-light.png`, `settings-billing-390-dark.png` |
| **Admin → Billing → Overview** | `admin-billing-1440-light.png`, `admin-billing-1440-dark.png`, `admin-billing-390-light.png`, `admin-billing-390-dark.png` |
| Admin → Plans, and the plan editor | `admin-plans-table-1440-light.png`, `admin-plans-1440-light.png`, `admin-plans-1440-dark.png` (viewport, 1440×1000) |
| Receipt PDF (live; test, refunded; the live sample still shows a GST block, which version 1 leaves out) | `receipt-pdf.png` (QuickLook render of `receipt/receipt-live.pdf` and `receipt/receipt-test.pdf`) |
| Public page with the Draft band | `public-refunds-1440-light.png`, `public-refunds-390-dark.png` |

Every shot was checked by `shoot.mjs`: no page errors, no failed requests, no horizontal page scroll at 390 or 1440.
(At 390 the check lists the closed documents slide-over, which sits off-canvas by design, as in the app today.)
The shots were taken before the functional brief's revision 2; section 0.4 lists what changed since.

---

## 0. Decisions

### 0.1 Visual decisions

| # | Decision | Why |
|---|---|---|
| V1 | **No new tokens.** Every colour comes from `global.css`; the only new grounds that carry text are the compare table's emphasised column (`color-mix(in srgb, var(--accent-soft) 55%, transparent)`) and the admin Net tile (`color-mix(in srgb, var(--accent-soft) 45%, var(--surface))`), both audited in all five accents and both themes (17) | The base spec's tokens pass AA by construction; new surfaces would need a new audit and a FOUNDATION change |
| V2 | **The plan, checkout, return and public pages are standalone pages**, not the chat's three-column shell: a 60 px translucent top bar (the admin bar's look) over a centred column on `--bg` | Lapsed users have no chat to show; the admin page already set the look for full pages |
| V3 | **One card is emphasised**: the current plan (`active`), else the last plan when it can be renewed (`expired`), else the recommended plan. Emphasis is the base spec's "selected card" look (an `--accent` border, a 1 px accent ring and a 5 px `--accent-soft` halo) plus a **flag** pill on the top border that says why: "Recommended" (accent fill, sparkle) or "Current plan" / "Your last plan" (ink fill, check or clock) | One focal point. The flag carries the meaning in words, so the border colour is never the only cue |
| V4 | **At most one primary button per page**: the emphasised card's main action ("Choose Standard", "Renew Starter"). Every other option is a secondary button; the alternative option (4.3's "or switch on 31 Oct") is a link under it | The base spec's button hierarchy; the user is never asked to compare two filled buttons |
| V5 | **Plan cards share one grid**: each card is a 5-row subgrid (head, price, actions, included, limits), so names, prices, buttons and lists line up across the plans whatever the description length | Comparison by eye. `grid-template-rows: subgrid` is in every browser the app supports |
| V6 | **Prices read "₹2,000 / month"** with the line "30 days, paid in advance" under them | The owner and Indian buyers think in months; the line keeps B2's exact 30 days visible. A bend of 8.2's "per 30 days" (0.2) |
| V7 | **Features in a fixed order with words**: Chat with your documents, Files from chat, Images from chat, Gmail plugin. Included: a check in `--accent-ink` (a list marker). Not included: a minus in `--text-3`, the label in `--text-3`, and "Not included" written out | 8.11: included and not included in text, not only an icon. Clay is for list markers (base spec principle 3) |
| V8 | **A comparison table under the cards on every width**: booktabs rules like answer tables, grouped (Features, Models, Limits per 30 days, Documents), the emphasised plan's column tinted. On phones it scrolls in its own box with the first column sticky | The cards stay short; the table answers "which plan has Sonnet?" without reading three cards |
| V9 | **Compact cards on phones**: name and price on one row, the features as four chips, three figures (answers, documents, storage), the button last | Three full cards are three screens tall at 390 px; a compact card is about 320 px |
| V10 | **Checkout is a review page with a summary aside**: the plan, its 30 days and the receipt details on the left; the charge, the credit line of a `now` checkout, the total and **"Pay ₹2,000"** on the right (sticky). On phones a sticky pay bar holds the total and the button | The amount is on the button the user presses. No surprise on Razorpay's page |
| V11 | **Leaving for Razorpay shows an interstitial** ("Opening Razorpay…", a spinner, then a "Continue to Razorpay" link after 3 s) | `location.assign` can take a second or two on a slow phone; a dead button invites a second click |
| V12 | **The return page is one result card**: a 68 px status mark in one of four tones, a serif title, one sentence, a receipt "slip" with the facts, then the actions. Status words, icons and tones never rely on colour alone | One calm place to land, with the receipt number the user may need |
| V13 | **Locked features stay visible and actionable**: the item keeps its normal title and icon, the icon tile gets a small lock badge, the description says "Included in Standard", and an "Upgrade ›" pill (the plugins menu's "Connect ›" look) sits at the end. Locked document creation is **one** item, not four format chips | 8.6: an action, not a disabled item. Four chips that all go to `/plans` would be four tab stops for one message |
| V14 | **Capability tags follow the plan**: the model menu's "Files" and "Images" tags show only when the model can and the plan includes it; models outside the plan collapse into one "More models with Standard ›" item; a model whose allowance rate isn't 1 carries a rate tag ("×2", "×0.05") | A Starter user must not read "Files" on Haiku and then be refused |
| V15 | **The expiry banner is the chat notice under the top bar** (`chat-notice`): info tone from 5 days, warning tone on the last day; "Renew" is a secondary small button with a dismiss beside it | The existing notice slot; urgency by tone and words, not by a louder button |
| V16 | **Settings → Plan & billing leads with a joined plan card** (the Account tab's profile-card pattern): plan name, status, days left as a serif figure, a period bar, facts, actions. The payment history is a table that becomes stacked rows when the panel is narrower than 600 px (a container query on the existing `settings-panel` container) | Consistent with Account; no sideways scrolling for money rows on phones |
| V17 | **Admin Plans is a table with a cost meter per plan**; the editor is a wide dialog with the live cost estimate in a sticky aside. **Admin Billing** uses a small segmented control for its five sub-sections, the admin tiles for money, a "Needs attention" card first, and CSS bars for the 12 months | The admin page's existing vocabulary (tiles, cards, `.table`, `admin-bar`); no chart library |
| V18 | **The receipt PDF is a quiet document**: Noto Sans in warm inks, one 2.6 mm clay band along the top edge, a status pill, a tinted table header. Nothing else is coloured; refunds are in the danger red; test receipts carry a light diagonal watermark | It is printed and filed; the brand shows once |
| V19 | **Public pages are an editorial reading page**: the serif prose of answers (17.5/1.7) at a 712 px measure, a warning-tone "Draft" band for placeholders, the desk-coloured footer with every policy link | Razorpay's reviewer and customers read them; they should look finished even as drafts |
| V20 | **Money is set in lining tabular figures** everywhere (`font-variant-numeric: lining-nums tabular-nums`), formatted by `formatInr` (en-IN grouping, no ".00" for whole rupees in the UI, always two decimals on receipts). The ₹ sign comes from the bundled fonts (both subsets include U+20B9) | Columns of amounts align; no fallback glyph for ₹ |

### 0.2 Where revision 1 bent the functional brief, and how it was settled

Settled in the functional brief's revision 2 (its 0.2, DS-1 to DS-15): **all adopted**, except S9, which is
superseded (the server now computes the answers figures, so the cards and the compare table show them: 3.2 and 4.4
below). S2 is adopted without its GST part, since version 1 has no GST. The last column is kept as the record of a
fallback that is no longer needed. Section numbers in the table are revision 1's.

| # | Change | Brief section | If not adopted |
|---|---|---|---|
| S1 | **A `recommended` flag on plans**: `PlanInput.recommended: boolean` (saving one clears the others), on `AdminPlan`, `PlanOffer` and `PublicPlan`; seeded on Standard; a "Recommended" switch in the plan editor | B1, 3.1, 7.1, 8.2, 8.8 | No "Recommended" flag; a `none` user's page has no emphasised card and no primary button |
| S2 | **Price wording**: "₹2,000 / month" plus "30 days, paid in advance · includes GST" (or "+ 18% GST", or nothing without a GSTIN) on cards and the public pricing page. Checkout, receipts and admin keep "30 days" | 8.2 | Write "₹2,000 / 30 days" and drop "paid in advance" |
| S3 | **Locked "Create a document" is one item** ("Create a document · Included in Standard") instead of four locked chips; **capability tags follow the plan** (V14). Say in 7.3 that `ChatModel.can_create_files`/`can_create_images` stay server capabilities and the UI combines them with `Me.plan.features` | 8.6, 7.3 | Four locked chips with one shared description |
| S4 | **The checkout interstitial** with its fallback link (V11), the phone pay bar, and the block title "Receipt details (optional)" | 8.3 | Only the busy button ("Opening Razorpay…") |
| S5 | **`OrderView` gains `receipt: {id, number} \| null` and `method: string \| null`**, so the return page shows "Receipt RA/26-27/000014" and "Paid with UPI" without a second request | 7.1, 8.4 | The slip shows plan, period and total only, and "Download receipt" uses `receipt_id` |
| S6 | **`PaymentView` gains `period_start`/`period_end`** (null for a receipt-only offline payment), shown as the history row's sub-line | 7.1, 8.5 | The sub-line shows the method only |
| S7 | **`BillingSummary` gains `users_without_plan: number`** (the "Users now" bar's "No plan" segment) and `refunds: number` (the Refunds tile's "1 refund"); **`AttentionItem` gains `detail: string \| null`** (the attention row's second line, such as the review reason or Razorpay's refusal) | 7.1, 8.9 | The bar shows the three plans only; the tile says "this month"; attention rows have one line |
| S8 | **Copy for two return states** the brief leaves open: "No answer from Razorpay yet" (after 10 minutes, with Check again) and, in review, "Please don't pay again." | 8.4 | Use 8.4's texts only |
| S9 | **Per-plan "about N answers" is not shown** on cards: the client can't compute the brief's per-plan figures (6,000 to 8,500 tokens per answer). The table's token row carries 8.2's "A typical answer uses 5,000 to 10,000 tokens." once | 8.2 | Unchanged (8.2 already says this) |
| S10 | **The avatar menu's subtitle becomes the plan line** for members ("Starter · until 31 Oct"; "Complimentary Professional · until 25 Nov"; "No active plan"); "Administrator" stays for the admin. "Plan & billing" carries a "5 days left" warning badge while `expiring_soon` | 8.6 | Keep the role line and add the plan line as a third line |
| S11 | **Admin tab order**: Users, Plans, Billing, Models, Usage, Documents, Plugins, System | 8.8, 8.9 | Append Plans and Billing at the end |
| S12 | **Seed descriptions** (≤ 300 characters): Starter "Ask questions across your documents, for everyday use.", Standard "Adds files from chat and a stronger model for regular work.", Professional "Everything: files, images and Gmail, with every model." | 3.2 | Empty descriptions (the card then has no second line) |
| S13 | **`src/components/billing/plan-card.css`** (new, APP stage 1) holds the card's `plans-` rules, so `/pricing` (ADMIN) gets the card without importing the plan page's CSS | 13.2 | Put the card rules in `billing.css` and import it from `PlanCard.tsx` |
| S14 | **Reminder emails end with why they arrive and how to stop them** ("You get this email because you gave this address for plan reminders. Remove it in Settings → Plan & billing to stop them.") | 4.7 | Omit the two lines |
| S15 | **The compare table needs resolved model lists**: `PlanOffer.chat_models` and `PublicPlan.chat_models` carry the resolved list (every enabled chat model when the plan is unrestricted) plus `all_chat_models: boolean`, and both gain `default_chat_model: PlanModel \| null` | 7.1 | The Models group lists only the models some plan names, then one row "Every other chat model" (✓ for unrestricted plans); no "New chats start with" row |

### 0.3 Settled points

- **Recommended plan**: Standard (seeded; the admin can move the flag).
- **Price wording**: "₹2,000 / month" with "30 days, paid in advance" under it.
- **The business name on public pages and receipts** comes from Admin → Billing → Business & payments (default
  `APP_NAME`, "RAG Assistant"). No logo exists, so the receipt and the public pages use the name only.

### 0.4 What changed after the screenshots (functional brief revision 2)

The text of this spec already follows these; the screenshots don't:

- **No GST in version 1**: no "includes GST" notes, GST lines, GST tile, GSTIN fields or "Prices include GST" switch
  anywhere; the receipt has no tax block.
- **Chat limits read as answers**: cards, the compare table, Settings and the admin say "About 1,600 answers a month
  with Claude Haiku 5.5" instead of token counts; the compare table's model rows carry an answers figure per plan.
- **Upgrades are `now` checkouts with a credit**: "Upgrade now · ₹1,700" (the full price minus a credit for what's
  left), one credit line at checkout and on the receipt, and "Start a new period now" on the current plan when an
  allowance is nearly used up.
- **Test notice**: test cards only (UPI Collect is gone), shown only to users the admin marked as testers.
- **Checkout**: the consent is a line under Pay (no tick box); the receipt details are a collapsed block; a "What
  changes on <date>" box for cheaper plans; a UPI-limit note above ₹2,000.
- **Return page (paid)**: a one-line "Remind me before my plan ends" email ask when reminder emails are set up.
- **Chat**: a plan that ends in an open chat replaces the composer with a panel (7.7); rate tags in the model menu;
  429 notes offer "Start a new period now".
- **Settings**: "Your data" for lapsed users (8.5); the email field only when reminder emails exist.
- **Admin**: no Pages section and no "Hidden" plan status; Billing gains a Disputes tile, Expiring and Lapsed lists,
  Export CSV, a UPI limit setting and a longer checklist; Users gains filter chips, a tester switch, "Above plan"
  badges and open-ended complimentary plans.
- **Public pages**: Markdown files in the repo; the Draft band comes from a marker in the file; no editor.

---

## 1. Principles

1. **The page is still paper.** Plans, checkout and receipts are part of the same editorial product: serif display
   titles and figures, sans for interface and data, clay only where it marks something.
2. **Money is plain.** Amounts are large and aligned, and a credit is always its own line. The amount the user will pay
   is on the button they press.
3. **Nothing is hidden, nothing nags.** Locked features stay where they are with one quiet "Upgrade" pill; the plan
   page is the one place that sells. Banners appear only near the end of a period.
4. **Every state has words.** Plan states, payment results and attention items carry a word and an icon; colour
   repeats the meaning, never carries it alone.

---

## 2. Tokens, icons and formats

### 2.1 Tokens

None new (V1). Recipes used by this feature:

| Use | Value |
|---|---|
| Emphasised card | `border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent), 0 0 0 5px var(--accent-soft), var(--shadow)` |
| Flag, recommended / current | `--accent` with `--accent-text`; `--text` with `--bg` (the toast pair, 13.27:1 at worst) |
| Compare table, emphasised column | `color-mix(in srgb, var(--accent-soft) 55%, transparent)` |
| Admin Net tile | border `color-mix(in srgb, var(--accent) 40%, var(--border))`, fill `color-mix(in srgb, var(--accent-soft) 45%, var(--surface))` |
| Result mark halos | `0 0 0 9px color-mix(in srgb, var(--<tone>-soft) 45%, transparent)` |
| Plan shades in admin charts | Starter `color-mix(in srgb, var(--accent) 45%, var(--surface-3))`, Standard 70%, Professional `--accent`; "No plan" `--surface-3` with an inset 1 px `--border-strong` |
| Phone pay bar shadow | `0 -10px 28px -14px rgba(51,38,25,.22)` (dark: `rgba(0,0,0,.6)`), the documented shadow exception |

### 2.2 New icons (`components/ui/icons.tsx`, 24×24, stroke 1.75, `currentColor`)

| Name | Paths | Used for |
|---|---|---|
| `CardIcon` | `<rect x="3" y="5.5" width="18" height="13" rx="2.5"/><path d="M3 10h18M7 14.5h3.5"/>` | Settings tab and avatar item "Plan & billing" |
| `TagIcon` | `<path d="M3.5 12V5a1.5 1.5 0 0 1 1.5-1.5h7a1.5 1.5 0 0 1 1.06.44l7.5 7.5a1.5 1.5 0 0 1 0 2.12l-7 7a1.5 1.5 0 0 1-2.12 0l-7.5-7.5A1.5 1.5 0 0 1 3.5 12z"/><circle cx="8.5" cy="8.5" r="1.5"/>` | Admin "Plans" tab, the plan tile in checkout and Settings |
| `ReceiptIcon` | `<path d="M6.5 3.5h11v17l-2.2-1.4-1.9 1.4-1.9-1.4-1.9 1.4-1.9-1.4-1.2 1z"/><path d="M9.5 8h5M9.5 11.5h5M9.5 15h3"/>` | Admin "Billing" tab |
| `BankIcon` | `<path d="M3.5 9 12 4.5 20.5 9M5.5 9.5v7.5M10 9.5v7.5M14 9.5v7.5M18.5 9.5v7.5M3.5 20h17"/>` | "Record bank transfer" |
| `GiftIcon` | `<rect x="3.5" y="8.5" width="17" height="4" rx="1"/><path d="M5 12.5v6a1.5 1.5 0 0 0 1.5 1.5h11a1.5 1.5 0 0 0 1.5-1.5v-6M12 8.5V20M12 8.5c-1.6-3.6-5.2-4-5-1.6.1 1.4 2.6 1.6 5 1.6zM12 8.5c1.6-3.6 5.2-4 5-1.6-.1 1.4-2.6 1.6-5 1.6z"/>` | Complimentary periods, "Grant a complimentary plan" |
| `MinusIcon` | `<path d="M6.5 12h11"/>` | "Not included" |
| `ArrowUpRightIcon` | `<path d="M7 17 17 7M8.5 7H17v8.5"/>` | "Resume payment" (leaves the app) |
| `FlaskIcon` | `<path d="M9.5 3.5h5M10 3.5v5.2L4.9 17.6A2 2 0 0 0 6.6 20.5h10.8a2 2 0 0 0 1.7-2.9L14 8.7V3.5"/><path d="M7.3 14.5h9.4"/>` | Test-mode notices |

Existing icons reused: `Lock` (locked items, the pay button), `Shield` (secure payment, review), `Calendar`, `Clock`,
`CheckCircle`, `AlertCircle`, `Alert`, `Info`, `Refresh`, `Download`, `Sparkle` (the Recommended flag), `Cpu`.

### 2.3 Money and dates

- `formatInr(paise)`: `Intl.NumberFormat("en-IN", {style: "currency", currency: "INR"})`, whole rupees without
  ".00" in the UI ("₹2,000", "₹1,234.50", "₹1,00,000"); receipts always show two decimals ("₹2,000.00").
- Big figures (prices, totals, admin tiles): serif 600, `lining-nums tabular-nums`, tracking −0.015 to −0.025em.
  Table amounts: sans, right-aligned (`.num`), tabular.
- Secondary money lines are quieter than the amount they explain: 13.5 px `--text-3` ("Credit for unused Starter
  −₹300"; "30 days, paid in advance").
- Dates: "26 Oct 2026"; ranges "26 Oct to 25 Nov 2026" in running text, "26 Oct 2026 – 25 Nov 2026" in tables and the
  receipt; relative words only for "Today, 15:02" and "5 days left".
- Receipt numbers and Razorpay ids: sans tabular in the UI (they are read, not copied), mono 13 px in admin detail
  views (`admin-code`) where they are copied.

---

## 3. Shared pieces

### 3.1 Page frame (`plans-`; `public-` copies the values)

- **Top bar** (`.plans-topbar`): sticky, z 20, 60 px, a bottom hairline, `color-mix(in srgb, var(--bg) 92%,
  transparent)` with `backdrop-filter: blur(8px)`. Inner row max 1240 px, padding 0 24, gap 10: the brand
  (`.ui-brand`, links to `/` with access, else `/plans`), a spacer, "Back to chat" (`btn btn-sm`, arrow-left 15; only
  with access), then the avatar button and menu of the chat (`AvatarMenu`, 7.5).
- **Phones** (under 800 px): 56 px, padding 0 12, the brand name hidden (the "R" mark stays), "Back to chat" becomes a
  36 px icon button (accessible name kept).
- **Main** (`.plans-main`): max 1180 px, padding 52 24 64 (phones 30 16 40). Checkout uses `.plans-main-narrow` (max
  1060, padding-top 32; phones 18 and no bottom padding, the pay bar sits there); the return page
  `.plans-main-result` (max 620, padding-top 64; phones 28).
- The Settings dialog opens over these pages exactly as over the chat (`?settings=billing`).

### 3.2 Plan card (`PlanCard`, `src/components/billing/PlanCard.tsx`, frozen props)

Used by `/plans` (with actions), the public pricing page (without), and nowhere else. A `<li>` labelled by its
heading.

| Part | Desktop | Phones (under 800 px) |
|---|---|---|
| Frame | `--surface`, 1 px `--border`, radius `--radius-lg`, `--shadow-xs`; a subgrid of 5 rows (`grid-row: 1 / span 5; grid-template-rows: subgrid`) | a flex column; the parent list is a column with gap 22, max 560 px centred (from 940 px down) |
| Flag (emphasised card only) | absolute, top −12, left 22; 24 px pill, padding 0 11 0 9, 12.5/650, a 13 px icon, gap 5; Recommended: `--accent` + `--accent-text` + sparkle; Current plan / Your last plan: `--text` + `--bg` + check / clock | same |
| Head | padding 28 24 0; name `h2` serif 26/1.2 600 −0.015em; description 14.5/1.45 `--text-2`, margin-top 6 | padding 22 18 0; name 23 px, `max-width: calc(100% - 132px)` |
| Price | padding 20 24 0; the amount serif 46/1 600 −0.025em, tabular; "/ month" 15 `--text-3`; the note "30 days, paid in advance" 13.5 `--text-3`, margin-top 8 | absolutely placed at the head's right (top 22, right 18), right-aligned; amount 30 px, "/ month" 13.5; the note hidden (the compare table and checkout say it) |
| Actions | padding 20 24 22, a bottom hairline, gap 8; the main option a 44 px full-width button (radius `--radius`, 15.5 px); the alternative a centred 14 px link (600) in `--text-3` text; an unavailable option: the button disabled and its reason under it (13.5 `--text-2`, a 15 px info icon in `--text-3`) | moved last (`order`), padding 4 18 18, no hairline |
| Included | padding 18 24 20; label "INCLUDED" (12/650 caps, 0.07em, `--text-3`, margin-bottom 10); a list, gap 10; each row a `18px 1fr auto` grid: icon (check `--accent-ink` / minus `--text-3`), label 14.5/1.4 `--text` with a 13 px `--text-3` detail line (included only), and for missing ones "Not included" 12.5 `--text-3` (visual, `aria-hidden`) plus an `sr-only` ", not included" | **chips**: 28 px pills, padding 0 10 0 8, 1 px `--border`, `--bg`, 13.5/600, a 15 px icon, the short label (Chat, Files, Images, Gmail); missing: dashed border, transparent, 500, `--text-3` |
| Limits | label "LIMITS"; a `dl` of rows (flex, space-between, padding 9 0, hairlines): dt 14 `--text-3`, dd 14/600 `--text` right-aligned with an optional 13 px `--text-3` second line. Rows: Chat "About 1,600 answers / a month" over 13 px "with Claude Haiku 5.5" (`answersLabel`; "Unlimited" for 0); Documents "50 / up to 10 MB each"; Storage "200 MB"; Images "60 / a month" (only with images); Models "Claude Haiku 5.5, GPT-5.4 mini" / "Starter's, plus Claude Sonnet 5.5" / "All models" | **three figures**: a 3-column grid, gap 8; each a tile (padding 10 12, 1 px `--border`, radius `--radius`, `--bg`) with the value 15/600 above the term 12.5 `--text-3`; rows after the third hidden (the compare table has them) |

- Feature rows always appear in the order of V7, and a plugin with `available: false` reads "Gmail (not available
  yet)" in `--text-3` with no check.
- Numbers on cards come from `PlanOffer.answers_estimate`, `limits` and `chat_models`. The Models row: null → "All models"; a list that
  contains the next lower plan's whole list → "<Lower plan>'s, plus <the new names>"; else up to two names joined by
  a comma, or "N models".
- **Skeleton** (loading): three cards with `ui-skeleton` blocks: name 40% × 18, description 80% and 60%, price 30%
  × 36, a 44 px full-width block, four 70% lines; `aria-busy` on the list and an `sr-only` "Loading plans…".

### 3.3 Plan state vocabulary

| State | Badge (`.badge`) | Where |
|---|---|---|
| active | success status line "● Active" (`ui-status-connected`) | Settings plan card, admin user detail |
| expiring soon | warning badge with a 13 px clock: "Ends in 5 days", "Ends tomorrow", "Ends today" | plan page intro, avatar "Plan & billing" item ("5 days left") |
| complimentary | neutral badge with a 13 px gift: "Complimentary" | intro, Settings, admin |
| open-ended complimentary | neutral badge with a 13 px gift: "Complimentary · no end date" | intro, Settings, admin user detail |
| test mode | warning badge with a 13 px flask: "Test mode" | intro, Settings, return slip, admin rows |
| scheduled (paid ahead) | neutral "Paid"; comp "Complimentary" | Settings "Coming up" |
| expired | warning "Ended 25 Nov" | admin Users column |
| none | neutral badge "No plan" led by the 9 px dashed circle (`ui-dashed`) | admin Users column, avatar menu |
| exempt | accent "Admin" | admin views of the admin |
| payment: captured / refunded / partially refunded / review / failed / disputed | success "Paid" / neutral "Refunded" / neutral "Refunded ₹500" / warning "Review" / danger "Failed" / danger "Disputed" | history tables |
| order: pending / paid / expired / cancelled / review | info "Pending" (spinner) / success "Paid" / neutral "Expired" / neutral "Cancelled" / warning "Review" | admin Orders |

### 3.4 Notices on billing pages

All are `.ui-notice` (base spec 6.25) with an icon, text and optional trailing actions (`.plans-notice-actions`:
small buttons, gap 6). Under 800 px a notice with actions wraps them onto their own line, indented 25 px under the
text.

| Notice | Tone, icon | Text and actions |
|---|---|---|
| Test mode (testers only) | warning, flask | "**Test mode:** no real money moves. Pay with card `4100 2800 0000 1007`, any future expiry and any CVV." (the test values in mono 0.9em) |
| Payments not set up | neutral, info | "Online payment isn't set up yet. Contact billing@example.com." (admin: "Set up payments in Admin → Billing" as a link) |
| Open checkout | info, info | "A payment for **Standard** is in progress." + `btn-sm` "Check status" + ghost sm "Resume payment" (while `pay_url`) + ghost sm "Cancel" |
| Unknown return (`?payment=unknown`) | info, info | 8.2's text |
| Admin preview (exempt) | neutral, shield | "You're the admin, so plans don't apply to you. This is what users see." |

### 3.5 Lock badge and Upgrade pill (`LockedHint`, `billing-`)

- **Lock badge** (`.billing-lock-badge`): an 18 px circle at the bottom-right of a 34 px menu icon tile (right −5,
  bottom −5), `--surface` with a 1 px `--border-strong` ring, an 11 px `LockIcon` in `--text-2`. `aria-hidden`.
- **Upgrade pill** (`.billing-upgrade-pill`): the plugins menu's "Connect ›" look under the billing prefix: 28 px,
  padding 0 8 0 12, pill, `--accent-soft`, `--accent-ink` 13.5/650, a 15 px chevron. Inside a menu item it is
  `aria-hidden`; the item's description says "Included in Standard. Opens the plans page." for screen readers.
- **Policy links** (`PolicyLinks`): a wrapping list, gap 6 18, 13.5 px `--text-2` links (underline on hover): Pricing,
  Terms, Privacy, Cancellation and refunds, Contact, About, Shipping.

---

## 4. The plan page (`/plans`)

Screenshots: `plans-none-*`, `plans-active-*`.

### 4.1 Layout, top to bottom

1. **Intro** (`.plans-intro`, centred, max 760): the title serif 46/1.06 500 −0.025em with `text-wrap: balance`, its
   key word italic 450 in `--accent-ink` (the login hero's device); the lede serif italic 18/1.5 `--text-2`, max 50ch,
   margin-top 14; an optional row of state badges (3.3), margin-top 14. Phones: 34 px and 16.5 px.
2. **Notices** (`.plans-notices`, max 780, centred, margin-top 28, gap 10), in this order: admin preview, payments not
   set up, test mode, open checkout, unknown return.
3. **Cards** (`.plans-grid`, margin-top 44, column gap 20; `grid-auto-flow: column`, so up to four plans sit in one
   row; from 940 px down a single column). Five or more offered plans wrap into rows of three and lose cross-row
   alignment (acceptable; the seeded catalogue has three).
4. **Compare plans** (4.4), margin-top 64.
5. **How payment works** (`.plans-how`): three items in a 3-column grid (gap 24 32, margin-top 64; one column on
   phones). Each: a 40 px `--accent-soft` tile (radius 12, a 20 px `--accent-ink` icon), a 15.5/650 title and a 14 px
   `--text-3` line:
   - shield, "Pay on Razorpay's secure page", "UPI, cards, netbanking or wallets. We never see your card or bank
     details.";
   - calendar, "30 days, no automatic renewal", "Nothing is charged again. We remind you before your plan ends.";
   - docs, "Your data stays yours", "Chats and documents are kept, even if your plan ends, and come back when you
     renew."
6. **Footer** (`.plans-foot`, margin-top 56, a top hairline, padding-top 18, 13.5 `--text-3`): `PolicyLinks` left,
   "Payments are processed by Razorpay." right. Phones: stacked.

### 4.2 Intro and emphasis by state

| State | Title | Lede | Badges | Emphasised card (V3) |
|---|---|---|---|---|
| `none` | "Choose a *plan*" | "Your account is ready. Pick a plan to start chatting with your documents." | – | recommended, flag "Recommended" |
| `expired` | "Your *Standard* plan has ended" | "It ended on 25 Nov 2026. Your chats and documents are kept: renew or choose another plan to pick up where you left off." | – | the last plan when it has a `period` option, flag "Your last plan" (clock); else recommended |
| `active` | "Your plan: *Starter*" | "Valid through 31 Oct 2026. Upgrade now with credit for what's left, or switch plans when this one ends." ("…until 7 Dec 2026, paid ahead" when stacked; "From 31 Oct: Standard." added when `next_plan`) | Ends in N days (when `expiring_soon`), Complimentary, Test mode | the current plan, flag "Current plan" (check) |
| `active`, open-ended | "Your plan: *Professional*" | "Complimentary, with no end date. You can still choose a paid plan; your complimentary plan continues after it." | Complimentary · no end date | the current plan, flag "Current plan" (check) |
| `exempt` | "Plans" | "This is the page users see." | Admin | as `none`; every button disabled (`aria-disabled`, title "The admin doesn't need a plan.") |

### 4.3 Actions per option (from `PlanOffer.options`, labels of the brief 4.3)

| Case | Main action | Alternative | Primary? |
|---|---|---|---|
| no access, any plan | "Choose Standard" | – | on the emphasised card only |
| current plan | "Renew Starter" | caption "Adds 30 days from 31 Oct" (`.plans-alt`, plain text) | when `expiring_soon` or `expired`, else secondary |
| current plan, an allowance 90% used (`can_start_new_period`) | "Start a new period now · ₹450" | link "or renew from 31 Oct" | as "Renew" |
| higher plan, `active` | "Upgrade now · ₹1,700" | link "or switch on 31 Oct" | never (the user isn't asked to upgrade loudly) |
| lower plan, `active` | "Switch on 31 Oct" | – | never |
| unavailable | the option's label, disabled | the reason as text (3.2) | – |

### 4.4 Compare plans (`.plans-compare`)

- Heading `h2` "Compare plans" serif 30/1.15 500 −0.02em centred (25 on phones), sub-line 15 `--text-3`: "Every plan
  includes the shared knowledge base and keeps your chats and documents."
- The table (`.plans-table` in a focusable `role="region"` scroller labelled by the heading): `table-layout: fixed`,
  min-width 640 (560 on phones), first column 31% (34%), 1.5 px `--rule` top and bottom, no outer box.
  - Header cells: padding 18 16 14, a 1 px `--rule` under them; the plan name serif 20/600, its price 13.5
    `--text-3` ("₹2,000 / month").
  - Group rows ("FEATURES", "MODELS", "LIMITS PER 30 DAYS", "DOCUMENTS"): `th colspan` 12/650 caps `--text-3`,
    padding 22 0 8; the next row's top border is a 1 px `--rule`.
  - Body: row headers 14.5/600 `--text` with an optional 13 px `--text-3` detail; cells 14.5 `--text-2`, padding 11
    16, hairlines between rows.
  - Yes: a 17 px check in `--accent-ink` plus `sr-only` "Included"; no: "—" in `--text-3` (`aria-hidden`) plus
    `sr-only` "Not included".
  - The emphasised plan's column (header and cells) is tinted (2.1); its header cell has rounded top corners.
- Rows: Chat with your documents; Files from chat (PDF, Word, Excel, Markdown); Images from chat; Gmail plugin (Search,
  read, draft and send); one row per enabled chat model, by provider then name, whose cells read "About 1,600
  answers" (`PlanModel.answers`; "Unlimited" when the allowance is 0) or "Not included", and "New chats start with"
  (the default model), both from S15; Chat allowance (standard tokens, detail "Counted at each model's rate: Haiku
  ×0.05, Opus ×2"); Images; Scanned PDF pages read ("About 50", tokens ÷ 3,000); Requests a minute; Documents;
  Storage; Largest file. 0 shows "Unlimited".
- **Phones**: the scroller bleeds to the screen edges (margin 0 −16, padding 0 16); the first column is sticky
  (`left: -16px`, `--bg`, an inset right hairline) so the row names stay while the plans scroll.

### 4.5 Other states

- **Loading**: the intro renders at once (it depends only on `Me`); cards show the skeleton (3.2); the table and the
  "how" strip wait.
- **Error**: `ui-notice-danger` "Couldn't load the plans." with `btn-sm` "Try again", in place of the cards.
- **No plans**: a centred `.card` (max 440, padding 28) like `FullPageMessage`: serif 22/600 "No plans are available
  yet", the contact line, and for the admin a link "Create plans in Admin → Plans".

---

## 5. Checkout (`/plans/checkout/:code`)

Screenshots: `checkout-*`, `checkout-redirect-*`.

### 5.1 Layout

1. "← All plans" (`.plans-back`: 14.5/600 `--text-2`, a 16 px arrow, gap 6).
2. **Steps** (`.plans-steps`, an `ol`, 13.5/600 `--text-3`, gap 8, margin-top 22): "Choose a plan" (done: a 22 px
   `--success-soft` circle with a 13 px `--success` check), "Review and pay" (`aria-current="step"`: an `--accent`
   circle with the number in `--accent-text`, the label `--text`), "Confirmation" (a 22 px circle with a 1 px
   `--border-control` ring, 12/700 number). 28 × 1 px `--border-strong` connectors. **Phones**: only the current
   step's label shows; connectors 14 px.
3. **Title** "Review and pay", serif 38/1.1 500 −0.022em (30 on phones), margin-top 14.
4. Notices (the test-mode one in checkout spans the full width), margin-top 18.
5. **Grid** (`.plans-checkout`): `minmax(0,1fr) 380px`, gap 28, margin-top 26; one column from 940 px down, the
   summary after the form.

### 5.2 Left column

- **Your plan** (`.plans-panel`: `--surface`, 1 px `--border`, radius `--radius-lg`, `--shadow-xs`, padding 22 24 24;
  panels 16 apart; title serif 21/600):
  - a row: a 48 px `--accent-soft` tile (radius 14, a 22 px `TagIcon` in `--accent-ink`; hidden on phones), the plan
    name serif 24/600, its description 14.5 `--text-2`, then up to four `.badge` chips with 13 px checks ("Chat with
    documents", "Files from chat", "Claude Sonnet 5.5", "About 260 answers a month"); a ghost sm "Change" at the right (to
    `/plans`);
  - the **dates block** (`.plans-dates`: `--bg`, 1 px `--border`, radius `--radius`, padding 12 14, a 20 px calendar
    in `--accent-ink`): bold "30 days: 26 Oct 2026 to 25 Nov 2026", then 13.5 `--text-3` "Starts as soon as your
    payment is confirmed. It doesn't renew by itself." (a stacked period: "Starts 26 Nov 2026, when your current plan
    ends."; a `now` checkout: "Starts now. Your Starter period ends today, and its unused part is credited below.").
- **What changes on 31 Oct** (only for an option with `changes`; `.plans-changes`): a `ui-notice-warning` panel
  between the plan panel and the receipt details, its title 15/650, then the server's sentences as a list (14.5,
  `--text-2`, a 15 px minus in `--text-3` as the marker).
- **Receipt details (optional)** (`.plans-panel` collapsed to one row: a `button` with `aria-expanded`, the title with
  "(optional)" in `--text-3` 400, the prefilled name in `--text-3` at the right, a 16 px chevron): open, the sub-line
  "Saved to your account for next time." and a 2-column field grid (gap 16; one column on phones): "Name on the
  receipt" (placeholder: the username) and, only when reminder emails are set up, "Email for reminders" with the hint
  "Used only to remind you before your plan ends.".

### 5.3 The summary (`.plans-summary`, `aside`)

- Sticky at top 84 on desktop; `--surface`, 1 px `--border`, radius `--radius-lg`, `--shadow`, padding 22 22 20.
- "SUMMARY" caps label; lines (`dl`, gap 9, 14.5): "Standard plan" with a 13 px `--text-3` "30 days, 26 Oct to 25
  Nov" and "₹2,000" (600, right, tabular); for a `now` checkout the credit line, quieter (13.5 `--text-3`): "Credit
  for unused Starter  −₹300" (`.plans-credit`), and the total and the button then read ₹1,700.
- **Total**: a dashed `--border-strong` rule (margin-top 14, padding-top 14), "Total" 15/650 left, the amount serif
  32/600 tabular right.
- **Pay** (`btn btn-primary plans-pay`): full width, 48 px, radius `--radius`, 16 px, a 17 px lock, "Pay ₹2,000".
  Busy: a 14 px spinner and "Opening Razorpay…" (`aria-busy`). Errors (by `code`, 8.3) appear under it as
  `error-text`, focus on them.
- Above the button, when the amount is over the UPI limit: an info `ui-notice` (13.5, `.plans-upi-note`) "UPI is
  limited to ₹2,000 per payment for now. Pay ₹5,000 by netbanking, debit card or credit card on the next page."
- Under the button: the consent line (13.5 `--text-2`, `.plans-consent`): "By paying you agree to the Terms of service
  and the Cancellation and refunds policy." (links underlined, opening the public pages in a new tab).
- Under it: a 13.5 `--text-3` line with a 16 px `--success` shield: "You'll pay on Razorpay's secure page, then come
  back here."; then the methods as text pills (`.plans-method`: 26 px, padding 0 9, 1 px `--border`, `--bg`, 12.5/600
  `--text-2`): UPI, Cards, Netbanking, Wallets. **No payment brand logos** (licensing, and they would be the only
  third-party marks in the app).

### 5.4 Phones: the pay bar

`.plans-paybar` (only under 800 px): sticky at the bottom of the page, margin 20 −16 0, padding 12 16 `max(12px,
env(safe-area-inset-bottom))`, `--surface`, a top hairline and the upward shadow (2.1). Left: the total serif 24/600
over "30 days, paid in advance" 12.5 `--text-3`; right: the Pay button (flex 1, 46 px). The consent line sits
just above the bar, at the end of the summary. The summary card's own Pay button and secure line are hidden; the summary keeps the lines, total and methods.

### 5.5 Leaving for Razorpay (V11)

After `POST /billing/checkout` succeeds, before `window.location.assign(pay_url)`:

- `.plans-redirect`: fixed, z 100, `--overlay` with `blur(3px) saturate(.9)` (the modal backdrop), `role="status"`.
- A card (max 440, padding 30 28 26, radius `--radius-xl`, `--shadow-lg`, centred text): a 56 px `--accent-soft`
  circle holding a 28 px spinner (2.5 px ring in `--accent-ink`); serif 23/600 "Opening Razorpay…"; 15/1.5
  `--text-2` (max 34ch) "You'll pay ₹2,000 on Razorpay's secure page, then come back here automatically."
- After 3 s, under a hairline: 13.5 `--text-3` "Not moving? **Continue to Razorpay**" (a link to `pay_url`).
- It never closes by itself; the page is replaced by the navigation. Back from Razorpay (bfcache) removes it on
  `pageshow`.

---

## 6. The return page (`/billing/return`)

Screenshots: `return-success-*`, `return-failure-*`, `return-states-*`.

### 6.1 The result card (`.plans-result`)

- `--surface`, 1 px `--border`, radius `--radius-xl` (`--radius-lg` on phones), `--shadow`, padding 40 40 30 (30 20
  24), centred text. The whole `main` is `aria-live="polite"`; on a final state focus moves to the title
  (`tabindex="-1"`).
- **Mark** (`.plans-result-mark`): a 68 px circle (60 on phones), the tone's soft fill, the 9 px halo (2.1), a 32 px
  icon with stroke 2.1 in the tone colour; the pending spinner is a 28 px, 2.5 px ring.
- **Title** serif 32/1.15 500 −0.02em (27 on phones); **text** 16/1.5 `--text-2`, max 42ch, margin-top 10; bold
  words in `--text` 650.
- **Slip** (`.plans-receipt`, a `dl`): margin-top 26, `--bg`, 1 px `--border`, radius `--radius-lg`, padding 2 18;
  rows flex space-between, padding 11 0, hairlines, 14.5: term `--text-3`, value `--text` 600 right-aligned, tabular,
  with an optional 13 px `--text-3` second line. The total row's top border is dashed `--border-strong` and its value
  serif 21 px (the second line stays sans).
- **Actions** (`.plans-result-actions`): centred, gap 10, margin-top 26, 42 px buttons with padding 0 18. Phones: a
  full-width column, the primary on top (`column-reverse`).
- **Note** (`.plans-result-note`): `--surface-2`, radius `--radius`, padding 12 14, 14/1.45 `--text-2`, a 16 px info
  icon in `--text-3`.
- Under the card: 13.5 `--text-3` centred foot line.

### 6.2 States

| State | Tone · icon | Title | Text | Slip | Actions |
|---|---|---|---|---|---|
| pending, first 60 s | info · spinner | "Confirming your payment…" | "This usually takes a few seconds. You can keep this page open." | Plan "Standard · ₹2,000" | – |
| pending, after 60 s | warning · clock | "Still waiting for Razorpay" | "If you closed Razorpay's page before paying, you can go back to it. We keep checking either way." | – | "Choose another plan", primary "Resume payment ↗" (while `pay_url`), ghost "Cancel this payment" |
| polling stopped (10 min) | info · refresh | "No answer from Razorpay yet" | "If you paid, your plan will start automatically within a few minutes. You can close this page." | – | "Back to plans", primary "Check again" |
| `check=failed` (any pending state) | + an info notice under the text: 8.4's sentence | | | | |
| **paid** | success · check | "Payment received" | "You're on **Standard** until **25 Nov 2026**. Files from chat and Claude Sonnet 5.5 are ready to use." (the second sentence names what the plan added over the previous one, when known; else omitted). Stacked: "Your Standard plan continues from 25 Nov to 25 Dec 2026." | Plan "Standard, 30 days"; Period; Paid with "UPI" (S5); Receipt "RA/26-27/000014" (S5); total "₹2,000" (a `now` payment: "after ₹300 credit" as its second line); Test mode badge in the plan row when test | "Download receipt" (download icon), primary "Start chatting →" |
| **expired / cancelled** | danger · alert-circle | "Payment not completed" | "No payment was received for this checkout, so your plan hasn't changed." + a danger notice "**Last attempt:** Your bank declined the payment." when `last_payment_error` | Plan; Amount; Started "Today, 15:02" | "Back to plans", primary "Try again" (refresh icon) + the note "If money left your account, it will be matched automatically within a few days, or Razorpay returns it to you." |
| review | warning · shield | "We're checking your payment" | "We received a payment and are checking it. Your plan starts as soon as it's confirmed. Please don't pay again." | – | "Back to plans" |
| 404 | danger · alert-circle | "This payment isn't in your account" | – | – | "Back to plans" |

Foot line under the paid card: "Razorpay also emails a confirmation to the address you gave it."

**Reminder ask** (paid state only, when reminder emails are set up and the profile has no email): under the slip, a
compact row (`.plans-remind`: `--surface-2`, radius `--radius`, padding 12 14; a 14.5/600 label "Remind me before my
plan ends", an email `input` with the label visually hidden, `btn-sm` "Save", ghost xs "Not now"; one column on
phones). After saving it reads "✓ We'll email you before 25 Nov." and stays out of the focus order.

---

## 7. In the chat

Screenshots: `chat-locked-*`, `chat-model-menu-*`, `chat-plugins-menu-*`, `chat-avatar-menu-*`.

### 7.1 Banners under the top bar (`PlanBanner`, `PendingCheckoutBanner`)

- Markup: `ui-notice ui-notice-<tone> chat-notice billing-banner` (the existing notice slot: centred, `min(100% -
  32px, 740px)`, above the bar's fade), items centred, a 15 px icon, the text, then a `btn btn-sm` (30 px, negative
  vertical margin so the notice keeps one line) and a 28 px ghost dismiss "Dismiss until tomorrow".
- **Expiring**: clock icon. Info tone from `BILLING_REMINDER_DAYS` days to 2 days ("Your **Starter** plan ends in 5
  days, on 31 Oct."); warning tone on the last day ("…ends tomorrow, on 31 Oct." / "…ends tonight at midnight."). Action
  "Renew" (or "Choose a plan" when the plan is retired); complimentary: "Your complimentary **Professional** plan
  ends…".
- **Payment in progress**: info tone, info icon, "A payment for **Standard** is in progress.", action "Check status";
  no dismiss (it goes when the order settles).
- One banner at a time, in 8.7's order. On phones the text wraps beside the button; the dismiss stays at the right.

### 7.2 The "+" menu (V13; `chat-locked-*`)

- Items keep their places: "Attach files…", then CREATE ("Create an image", "Create a document"), then GMAIL.
- A **locked item** (`ui-menu-item billing-locked-item`, a normal `menuitem`, not `aria-disabled`): the usual 34 px
  icon tile with the lock badge (3.5); the title in `--text`; the description "Included in Professional"; the Upgrade
  pill at the end (vertically centred). Activating it closes the menu and goes to `/plans`.
- Locked document creation is one item, "Create a document" / "Included in Standard"; the format chips appear only
  when files are in the plan.
- Gmail outside the plan: one item under GMAIL, "Search, draft and import email" / "Included in Professional", its
  icon in `--tag-red`, shown only when Gmail is set up and turned on (as today).
- A feature that is in the plan but unavailable now keeps today's disabled style and reason (base spec 6.9).
- Phones: the same rows in the bottom sheet.

### 7.3 Model menu

- Capability tags ("Files", "Images") render only when the model can and `Me.plan.features` allows it (V14), so a
  Starter user sees model names only.
- A model whose `allowance_rate` isn't 1 shows a `model-tag` after its name with `formatRate` ("×2", "×0.05";
  `.billing-rate-tag`), its `title` "Counts ×2 against your chat allowance".
- When `unlock.models` is set: a separator, then `ui-menu-item billing-more-models` with an 18 px lock icon, "More
  models with Standard" and a 16 px chevron at the end; it goes to `/plans`. Then the existing "Set the default in
  Settings…".

### 7.4 Plugins menu and the connection card

- **Menu item** (`chat-plugin-item`): status line `ui-status ui-status-off` with the 13 px lock, "Not in your plan";
  the description (two lines at most) "Included in Professional. Your connection is kept."; the Upgrade pill at the
  end. Activation opens the plugin's card in Settings → Plugins & data (as every plugin item does).
- **Card** (`conn-card`): the same status line; the account line "maya.chen@example.com is still connected"; actions
  primary sm "Upgrade" (to `/plans`, closing Settings) and ghost sm "Disconnect"; no Browse, no capability pills; the
  "Use in chat" switch is not shown. A note band (`conn-note`, info icon): "Your plan doesn't include Gmail. Upgrade to
  Professional to use it again; you won't need to reconnect."
- Not connected and not in the plan: the status line, "Included in Professional." and "Upgrade" (no Connect).

### 7.5 Avatar menu

- Head: the avatar, the username, and the **plan line** as the subtitle (S10): "Starter · until 31 Oct",
  "Complimentary Professional · until 25 Nov", "Standard · paid through 7 Dec", "No active plan"; the admin keeps
  "Administrator".
- Usage block: "Chat allowance this period" (and "Images this period" only when images are in the plan). Lapsed users: no
  usage block.
- After "View usage": "Plan & billing" (`CardIcon`), with a warning badge "5 days left" at the end while
  `expiring_soon`, or a neutral "No plan" when lapsed; it opens Settings → Plan & billing.
- The avatar's attention dot (base spec 6.8) also shows on the last day of a period without a renewal.

### 7.6 Composer, empty state, notes, documents

- **Composer hint**: the capability sentence follows the plan: none (Starter), "Ask for a PDF, Word, Excel or Markdown
  file" (Standard), "…, or an image" (Professional).
- **Empty state**: example rows for features outside the plan are left out (no upsell in the greeting).
- **Chat errors** (`chat-note`): 402 `feature_not_in_plan` → `chat-note-warning` with a 15 px lock, the server's
  `detail`, and `btn-sm` "See plans"; 429 `allowance_used` → `chat-note-warning` with the alert icon, the `detail`,
  a primary sm "Start a new period now" (when `can_start_new_period`) and `btn-sm` "Upgrade" when a higher plan
  exists. 402 `plan_required`/`plan_expired` never render a note: the plan-ended panel (7.7) takes the composer's
  place.
- **Documents panel**: over a lowered limit (B28), a `ui-notice-warning` at the top of the list: "You have 62
  documents; your plan allows 50. Delete some or upgrade to add more." with a ghost xs "See plans". The quota footer's
  meters show 100% in the danger style with "Limit reached" (base spec 6.6). Refused uploads keep today's messages.

### 7.7 The plan-ended panel (`PlanEndedPanel`, `billing-ended`)

- Takes the composer's place, at the composer's width and position (the message list stays above it): `--surface`,
  1 px `--border`, radius `--radius-lg`, `--shadow-xs`, padding 18 20; a row with a 36 px `--warning-soft` circle
  holding a 20 px clock in `--warning`, then the text.
- Title (`h2`, 16.5/650 `--text`, `tabindex="-1"`, focused and announced): "Your Standard plan ended on 25 Nov".
  Text 14.5/1.45 `--text-2`: "Renew to continue. Your chats and documents are kept." When a draft was kept, a 13 px
  `--text-3` line: "Your unsent message is saved and comes back when you renew."
- Actions (gap 8, margin-top 12): primary sm "Renew ₹2,000" and `btn-sm` "See plans". Phones: the actions share the
  width.

---

## 8. Settings → Plan & billing (`?settings=billing`)

Screenshots: `settings-billing-*`.

### 8.1 Frame

- A tab between Account and Usage: `CardIcon`, "Plan & billing" (the phone strip scrolls it into view as today).
- Header (`settings-head`): `h2` "Plan & billing" and the close button. "✓ Saved" appears after the receipt details save (base spec 6.23).

### 8.2 The plan card (`.billing-current`)

A joined card: `--surface`, 1 px `--border`, radius `--radius-lg`, `overflow: hidden`.

1. **Head** (`--bg`, padding 18 20, a bottom hairline): a 52 px `--accent-soft` tile (radius 15, a 24 px `TagIcon` in
   `--accent-ink`); the plan name serif 24/600; a meta row (14 px `--text-3`, gap 6 10): the status line "● Active"
   (`ui-status-connected`), "₹2,000 a month", and badges (Complimentary, Test mode). At the right, `.billing-days`:
   the days left as a serif 30/600 tabular figure over 13.5 `--text-3` "days left in this period" ("day left"; "Ends
   today"). Phones: the days row wraps under the name, inline (figure 22 px), indented 60 px.
2. **Period bar** (`.billing-timeline`, padding 16 20): "**8 Oct**" · a 6 px `ui-meter` of the elapsed days (`role
   meter`, "Day 18 of 30") · "**7 Nov 2026**", 13.5 `--text-3` with bold dates in `--text`. Its fill is `--accent`;
   in the last `BILLING_REMINDER_DAYS` days it takes the meter's warning style **without** the "Almost used up" words
   (time, not an allowance; the badge says "Ends in N days").
3. **Facts** (`.billing-facts`, the Account facts grid: 150 px | 1fr, hairlines, 14.5; on phones the term sits above
   the value): "Paid through" (only when stacked) "7 Dec 2026 / One more period is already paid"; "Then" "Standard
   from 7 Nov" (only with `next_plan`); "Includes" "Files from chat · Claude Haiku 5.5, GPT-5.4 mini and Claude Sonnet
   5.5" over "About 260 answers with Claude Sonnet 5.5, 200 documents and 1,000 MB per period"; "Complimentary" with the comp reason (comps
   only).
4. **Foot** (`--bg`, padding 14 20, a top hairline): 13.5 `--text-3` "Renewing adds 30 days after 7 Dec. Upgrading
   starts a new period now, with credit for what's left." and, right-aligned, `btn-sm` "Change plan" and primary sm "Renew" (both close
   Settings and go to `/plans` or straight to the checkout). Phones: the two buttons share the width.

Other states of the card:

| State | Head | Body | Foot |
|---|---|---|---|
| lapsed (`none`) | tile with a dashed `--border-strong` ring and a `--text-3` icon; "No active plan" | "Choose a plan to start chatting with your documents. Your chats and documents are kept." | primary sm "Choose a plan" |
| `expired` | "Standard" + warning badge "Ended 25 Nov" | the same sentence | primary sm "Renew Standard", sm "Change plan" |
| `exempt` | – | a neutral notice "Plans don't apply to the admin." (no card) | – |
| open-ended complimentary | as active, badge "Complimentary · no end date"; no days figure and no period bar | the facts with the comp reason | sm "Change plan" |
| expiring soon | as active | + a warning notice above the facts: "Your plan ends in 5 days. Renew to keep using the assistant without a break." | primary "Renew" |

### 8.3 Sections under the card

Section labels are `settings-sub` caps.

- An open checkout: the info notice of 3.4 above the plan card.
- **COMING UP** (only with scheduled periods): a list card (`.billing-list`, the sign-ins list look): per period a 36
  px `--surface-2` icon tile (calendar; gift for comps), "Standard" 15/600 over "7 Nov to 7 Dec 2026" 13.5
  `--text-3`, and a badge "Paid" / "Complimentary" at the right.
- **RECEIPT DETAILS**: a `settings-desc` line "Optional. Shown on your receipts; the email is used only for reminders
  before your plan ends."; a 2-column field grid (one column under 800 px): "Name on receipts" and, only when
  reminder emails are set up, "Email for reminders"; under it a 13.5 `--text-3` line naming the reminder channel
  (functional 8.5); a `btn-sm` "Save" (disabled until changed). Errors under the
  field (`error-text`); success shows "✓ Saved" in the header.
- **PAYMENT HISTORY**: a `.table.billing-table` in `table-wrap`: Date, Description (bold "Standard, 30 days" over 13
  px `--text-3` "7 Nov to 7 Dec 2026 · UPI"; S6), Amount (`.num`), Status (3.3 badges), Receipt (ghost xs with a 14
  px download icon and the number, linking to `receiptUrl(id)`). Cells don't wrap except the description. **Panel
  narrower than 600 px** (`@container settings-panel`): the head is visually hidden and each row is a 2 × 2 grid:
  description | amount (650 `--text`), receipt link | status; the date column is hidden (the period line dates the
  payment). Empty: `settings-desc` "No payments yet." Loading: three skeleton rows.
- **Help line**: 13.5 `--text-3` "Questions about a payment? Write to <contact email>." and `PolicyLinks` under it.

### 8.4 The Usage tab

- The header note reads "This period: 8 Oct to 7 Nov 2026" (subscribers) or "October 2026 (UTC)" (admin).
- The lede: "Your allowances start again on **Sat 7 November** (in 12 days) with your next period." / "…end on
  **Sat 7 November** with your plan." when nothing is scheduled; an open-ended plan: "…start again on **Sun 1
  November**." The chat meter is the allowance ("Chat allowance", standard tokens) with "Counted at each model's
  rate." under it; the per-model table keeps plain tokens.
- Lapsed users: the tiles show the counts without meters, the lede "No active plan. Choose a plan to start again."
  with a link.

### 8.5 "Your data" (lapsed users, Settings → Plugins & data)

- A section above the plugin cards (`.billing-yourdata`), with the `settings-sub` labels YOUR DOCUMENTS and YOUR
  CHATS and an intro `settings-desc` "Everything comes back when you choose a plan. To keep a copy of your chats, use
  Export your data below."
- Each list is the sign-ins list look (`.billing-list`): documents show a 36 px `--surface-2` tile with the file
  type's icon, the name 15/600 (ellipsis), "2.1 MB · 4 Nov 2026 · Generated file" 13.5 `--text-3`, then ghost xs
  "Download" (download icon) and ghost danger xs "Delete…"; chats show the title and the date, and "Delete…". Delete
  confirms inline in the row ("Delete "Q3 report.pdf"? It can't be undone." with danger xs "Delete" and "Cancel").
- More than 20 rows: "Show all 62" under the list. Empty: `settings-desc` "No documents." / "No chats."

---

## 9. Admin

Screenshots: `admin-billing-*`, `admin-plans-*`.

### 9.1 Tabs

Users, **Plans** (`TagIcon`), **Billing** (`ReceiptIcon`), Models, Usage, Documents, Plugins, System (S11). Billing
sub-sections live in the hash (`#billing/payments`).

### 9.2 Plans tab

- One `card admin-card`: title "Plans", subtitle "What users can buy, by rank. Changes apply to new periods; current
  periods keep what was paid for unless you choose otherwise.", primary "+ New plan".
- `.table.admin-table` columns: **Plan** (the name 15/650 over the code in `admin-code`); **Status** (success "Active",
  neutral "Retired" with the dashed mark; plus accent "Recommended"); **Price** ("₹2,000" 600 `--text` over 13 px
  "per 30 days"); **Users now · next · free** ("5 · 2 next · 1 free"); **Features** (model-tag chips "Files", "Images", "Gmail", or `--text-3` "Chat only"); **Key limits** (13 px
  `--text-3`, "≈260 Sonnet answers · 200 docs · 1,000 MB · 3 models"); **AI cost per period** (`.admin-cost`, min 150 px: a row
  "Worst case  ₹666 · 34%", a 6 px `ui-meter` of the share, a row "Typical  ₹267 · 14%"; at 60% or more the figures and
  an "⚠ High" label turn `--warning`; an unlimited limit replaces the worst case with "No ceiling" in `--warning`); the
  sticky actions "Edit" and "Delete" (disabled with the reason "Used: retire it instead").
- Footnote: "Cost estimates use the models' prices and ₹88 to the dollar, against what you keep after Razorpay's
  fee. A plan that has been bought can't be deleted; retire it instead."

### 9.3 The plan editor (`PlanFormModal`)

- A Modal 980 px wide; header "New plan" / "Edit plan: Professional". The body is a grid `1fr | 300px`: the form
  (padding 4 24 24) and the **estimate aside** (`--bg`, a left hairline, padding 18 20 20, sticky). Under 940 px the
  aside moves under the form with a top hairline.
- Form sections (`admin-form-section` legends):
  - **PLAN**: Code (create only, mono), Name, "Price per 30 days" with a "₹" suffix inside the input
    (`.admin-input-unit`: right padding 76, the unit 13.5 `--text-3` at 14 px from the right), Description (full width,
    the hint counts characters), Rank (hint "Higher ranks are upgrades and must cost more."), Status as a small
    segmented control Active / Retired with the hint "Retired: nobody can buy or renew it; current periods run on.";
    Recommended switch (one plan at a time).
  - **FEATURES**: switch rows (`ui-switch`, gap 14): Files from chat, Images from chat, one per registered plugin
    (description from the plugin), a plugin that isn't set up shows "Not set up yet" in its description.
  - **LIMITS PER 30 DAYS · 0 = UNLIMITED**: seven inputs in two columns with units (tokens, images, tokens, per min,
    documents, MB, MB), hints only where they help ("About 350 answers", "About 340 pages", "Up to 100 MB (the server's
    cap).", "Was 2,500,000." after an edit).
  - **MODELS**: segmented "All chat models" / "Only these", then a 2-column checklist of chat models (`ui-check`, the
    default marked with an accent "Default" badge; a "Make default" ghost xs on hover).
- **Estimate aside** (live, recomputed in the browser on every change by `planCost.ts`): label "AI COST PER PERIOD"; at 60% or more a warning line
  "⚠ High: over 60% of what you keep"; the worst case as a serif 27/600 figure with "worst case" 14.5 `--text-3`; a
  10 px stacked bar on `--surface-3` (typical in `--accent`, the rest of the worst case in `color-mix(accent 40%,
  surface-3)`, the remainder = what you keep); a legend `dl` with swatches: Typical "₹1,220 · 25%", Worst case "₹3,050 ·
  62%" (an edited plan over the warning line), You keep "₹4,882" (values `nowrap`); a 13 px `--text-3` note naming the standard token's price ($3.20 per
  million), Razorpay's fee and the rupee rate. "No ceiling" when a limit is 0.
- **Apply choice** (only when the plan has current or scheduled periods): a band above the footer (`--surface-2`, a
  top hairline, padding 14 24) with two `ui-radio` rows: "Apply to new periods only" (default) and "Also apply to the 3
  current and scheduled Professional periods". A 409 `confirmation_required` adds a warning notice in the band with
  its text and a `btn-sm` "Apply anyway".
- Footer: "Cancel", primary "Save changes" / "Create plan".

### 9.4 Billing tab

**Bar** (`.admin-billing-bar`): a small segmented control (Overview, Payments, Orders, Business & payments;
scrolls sideways on phones) and, for Overview and Payments, the existing month picker at the right (IST months). On
phones the picker goes under the control at full width.

**Overview** (`admin-billing-1440-*`):

1. Notices: test mode ("**Test mode.** Payments use Razorpay's test keys; no real money moves. 7 of about 30 test
   payment links used." + `btn-sm` "Going-live checklist"); payments not set up (neutral, with "Set up payments").
2. **Needs attention** (only when there are items): an admin card whose title carries a warning count badge; one row
   per `AttentionItem` (`.admin-attention-row`: an 18 px icon in the tone (warning alert, danger alert-circle, info
   info), the message 14.5 `--text` over the 13 px `--text-3` detail (S7), a `btn-sm` action: Review, Open payment, Open
   order, Edit pages, Set up payments). Phones: the button wraps under the text, indented 30 px.
3. **Tiles** (`admin-tiles admin-tiles-billing`, 5 columns; 3 under 940; 2 on phones with Net spanning both): Gross
   (sub "10 payments"), Razorpay fees ("2.36% with GST on the fee"), Refunds ("1 refund", S7), Disputes lost ("None"
   or "1 dispute"), **Net** ("what you keep", the tinted tile of 2.1). Sub-lines 13 px `--text-3`.
4. **A 2-column grid** (`1.15fr | 1fr`, gap 16; one column under 940; equal heights, footnotes pinned to the bottom):
   - **By plan**: a table (Plan with a shade dot, Payments, Gross, Users now "4 paid · 1 free"), then **Users now**: a
     10 px stacked bar (2 px gaps, pill ends) of users per plan in the plan shades plus "No plan" (S7), a legend with
     counts; footnote "“Free” counts complimentary periods you granted. Months are in Indian time (IST)."
   - **Last 12 months**: an `ol` of rows `64px | 1fr | 10ch` (gap 12, 13.5 px): month (the year on the first row and
     on January; the current month 650 `--text`), an 8 px `admin-bar`, the gross right-aligned tabular. Scaled to the
     largest month.
5. **Expiring in 7 days** and **Lapsed in the last 30 days**: two list cards side by side (one column under 940;
   `.admin-expiring`): rows of the avatar, the username 600, the plan, the date 13 px `--text-3`, the last payment,
   and a ghost xs "Open" (the user's form); empty: "Nobody" in `--text-3`.
6. **Recent payments**: the latest five as the Payments table (below) with "Record bank transfer" (`BankIcon`) and
   "Export CSV" (download icon, `.admin-export`, the selected month) in the card header.

**Payments**: filters (month picker, a user `select`, status segmented: All, Paid, Refunded, Review); a paged
`admin-table`: Date, User (avatar + name), Plan, Kind (Period / Upgrade), Amount, Method (UPI, Card, Netbanking,
Wallet, Bank transfer), Status (3.3), Receipt (link), and a sticky "Open". Test-mode rows carry a warning "Test" badge
after the amount. Pagination: "1–50 of 213" with previous/next icon buttons at the card's foot.

**Payment dialog** (Modal 640): a facts `dl` like Settings' (Paid on, User, Plan and period, Amount (and the credit a later upgrade
took from it), Method, Razorpay payment id in `admin-code` with a copy button, Fee "₹47.20 incl. GST", Receipt with Download);
**Disputes** (each: amount, status badge, the respond-by date in `--warning` while action is needed, "Respond in
Razorpay's Dashboard"); **Refunds** list (each: amount, status badge, who and when, reason; failed ones in a danger notice with Razorpay's
reason); **Refund** form (shown by a `btn-danger` "Refund…" in the footer): Amount with a "₹" prefix, prefilled with
the refundable amount ("Refund all ₹1,700" hint; "₹300 of this payment went as credit to an upgrade." when so), Reason (required), a `ui-check` "End access now" with the hint "A full refund
always ends this payment's period.", a warning notice "The refund goes back to the payer's account in 5 to 7 working
days.", and the confirm button `btn-danger` "Refund ₹2,000" (names the amount, base spec 6.1).

**Record bank transfer** (Modal 560): User (select), Plan (select), Amount (₹, at least 1), Received on (date), Bank reference,
Note (textarea; required when the amount is below the plan's price), a switch "Start a period" (on; off = "Receipt only"), and primary "Record ₹2,000".

**Orders**: a status segmented control with counts ("Review 1"); a table: Created, User, Plan, Kind, Amount, Status,
Checks ("3 checks · next in 30 s", 13 px `--text-3`), Last error (13 px `--danger`, ellipsis); an order of the other key mode carries a neutral "Paused" badge. The order dialog: the
facts, then an **events timeline** (`ol`: time in 13 px mono `--text-3`, a source badge, the event type 600, the
detail as `admin-code` key=value chips), and actions "Check now", and for review "Grant instead" and primary "Mark
resolved" (with a required note).

**Business & payments**: two cards side by side (one column under 940):

- **Business details** (`admin-form-grid`): Business name, Address (textarea), Contact email, Contact phone, Receipt
  prefix (mono, 1 to 4 characters, with a live example "RA/26-27/000001"), UPI limit per payment (₹, with the hint
  "Razorpay may limit UPI to ₹2,000 per payment for new accounts. Set 0 once Razorpay confirms there's no limit.");
  primary "Save".
- **Razorpay**: a facts list: Mode (warning "Test" / success "Live" badge), Key "rzp_test_…AbCd" (mono), Webhook secret
  ("Set" success / "Not set" neutral; "Previous secret also accepted" when set), Last webhook (relative time or
  "Never"), Webhook URL (`admin-code` block with Copy) and the events to subscribe (fourteen `admin-code` chips), Test
  links "7 of about 30" with a 5 px meter, Testers ("2 users can make test payments"), Reconciler last run and last
  error (a danger notice when the keys were rejected), Paused orders ("3 live orders can't be checked with test
  keys"), Reminder emails ("On" / "Off: set BILLING_EMAIL_FROM and the SMTP settings"); `btn-sm` "Check pending
  payments now".
- **Going-live checklist** (a card under both): rows with a 20 px `CheckCircleIcon` in `--success` when done or the
  dashed circle mark (`ui-dashed` at 20 px) when not, the item 14.5, and a ghost xs link to fix it: Business details
  filled, No draft policy pages, Live keys in `.env`, Webhook secret set and a webhook received; then three
  instruction rows with a 20 px info icon in `--text-3` (never ticked): "Confirm the UPI limit with Razorpay and set it
  above", "Keep Customer Fee Bearer and Offers off in Razorpay's Dashboard", "Refund from here, not from Razorpay's
  Dashboard"; and an optional row "Reminder emails set up".

### 9.5 Users tab

- A **Plan** column after the user: "Standard" 600 `--text` over "until 25 Nov" 13 px `--text-3`; badges
  "Complimentary" (gift), "Test", "Ended 25 Nov" (warning), "No plan" (the dashed mark); the admin row "Exempt"
  (accent).
- **Filter chips** above the table (the segmented control's small size): All, Active, Expiring, Lapsed,
  Complimentary, No plan, each with its count; the Plan column header sorts.
- **User form**: a **PLAN** section first after Account: the state line (as the Settings head, compact), the
  open-ended plan when there is one ("Complimentary Professional, no end date · granted by admin on 26 Oct" with a
  ghost danger xs "End…"), a small periods table (Plan, Dates, Source badge, a ghost danger xs "Revoke…" per row with an inline confirmation naming the
  user and the date), then buttons "Grant a complimentary plan…" (`GiftIcon`), "Change plan…" and a link "View
  payments". Grant and change open inline forms in the section (plan select, days input 1 to 90 with a
  "No end date" switch beside it, reason, start segmented "Now" / "After the current plan"). Under the section, the
  newest five billing events (time, type, actor) as 13 px `--text-3` rows, and a switch row "Can make test payments"
  (`billing_tester`).
- **USAGE LIMITS** become overrides: each input is empty when not overridden, with the plan's value as its placeholder
  ("Plan: 1,200,000") and the hint "Blank uses the plan's value. 0 = unlimited."; an overridden field shows an accent
  "Custom" badge in its label row and a ghost xs "Reset to plan"; an override above the plan's value also shows a
  warning badge "Above plan". Setting the chat allowance to 0 opens an inline warning notice under the field
  ("Unlimited chat allowance: this user has no cost ceiling.") with `btn-sm` "Allow unlimited". When the allowed
  models and the plan don't overlap, a warning notice in the MODELS section says so.
- Creating a user: a "Start with a complimentary plan" switch revealing plan, days (or "No end date") and reason; on
  by default with Professional and 30 days, plus a neutral notice, while regular users can't pay (functional 4.8).
- Delete confirmation with paid time left: a warning notice "maya.chen has a paid plan until 7 Dec 2026. No refund is
  made automatically."

### 9.6 System tab

The new `billing` check ("Payments") is one more row of the existing checks list, same status words and icons.

---

## 10. Receipt PDF (fpdf2, `billing/receipts.py`)

Screenshot: `receipt-pdf.png` (its live sample shows a GST block and a GSTIN, which version 1 leaves out). A4 portrait, millimetres, Noto Sans Regular and Bold from `app/fonts` (both have ₹ and
–). Prototype: `receipt_mock.py` (appendix B).

| Element | Position and style |
|---|---|
| Colours | ink `#2a2520`, secondary `#5a5249`, tertiary `#6e655a`, rule `#b8ad9b`, hairline `#e5dfd3`, header fill `#f3f0e8`, clay `#a94d2b`, success `#39703f` on `#e4efe1`, danger `#b02a3a`, watermark `#ece6dc` (the light-theme tokens, so print matches the app) |
| Top band | a clay rectangle across the page, y 0, 2.6 mm tall |
| Margins | left 18, right 18 (content 18 to 192) |
| Seller | business name Bold 15 at (18, 18); address lines, "email · phone" Regular 8.8 secondary, from y 27, 4.6 apart |
| Identity (right-aligned to 192) | "RECEIPT" Bold 7.2, tertiary, letter-spacing 0.6, y 18.5; the number Bold 13, y 24; "Issued 26 Oct 2026" 8.8 secondary, y 31.5; the status pill at y 37.5, 5.6 tall, radius 2.8: "PAID" (success on success-soft), "PARTIALLY REFUNDED" / "REFUNDED" / "DISPUTED" (secondary on header fill), Bold 7.5, spacing 0.5 |
| Rule | y 52, 0.35 pt rule colour |
| Billed to (x 18) | label "BILLED TO" at y 57; name Bold 10.5 at y 62 (the profile name, else the username); "maya.chen (account)" and the email (when given) 8.8 secondary, 4.6 apart |
| Payment (x 112 to 192) | label "PAYMENT"; rows from y 62, 4.8 apart, term 8.8 tertiary at x 112, value right-aligned: Method (Bold), "Razorpay payment" id (or "Bank reference"), "Paid on 26 Oct 2026, 15:02 IST" |
| Lines table | header band y 86, 8 tall, header fill; labels DESCRIPTION (x 21), PERIOD (x 104), AMOUNT (right at 189), as the identity label; each line at y 97: description Bold 10, under it 8.2 tertiary "One-time payment for 30 days; it doesn't renew by itself." (a `now` payment adds a credit line: "Credit for unused Starter", period "26 Oct – 25 Nov 2026", amount "−₹300.00" in the secondary ink); period 9.2 secondary; amount Bold 10 right; a hairline 11.5 below the last line |
| Totals (x 118 to 192) | rows 5.6 apart, 9.2: a 0.35 pt rule, then "Total paid" Bold 11 and the amount Bold 13 (no GST block, functional B10) |
| Note (x 18) | on the total's line, 8.5 tertiary: "One-time payment; plans don't renew by themselves." |
| Refunds | under the total, 10 below: "Refunded on 4 Sep 2026" 9.2 danger and "−₹500.00" Bold danger right; under it 7.8 tertiary "Refund rfnd_… · <reason>"; a lost dispute the same way: "Disputed, lost on 9 Dec 2026" and "−₹2,000.00" |
| Footer | a hairline at y 276; at y 279.5, 8 tertiary: "This receipt was generated electronically and needs no signature." left, "<number> · page 1 of 1" right |
| Test mode | the number in the TEST series and the watermark "TEST: not a real payment" Bold 40, watermark colour, rotated 35° about (105, 160), centred, drawn **first** so the content prints over it |
| Title | "Receipt" (the PDF's title and the identity label); tax invoices are a later feature (functional B10) |

Long descriptions wrap within the description column (x 21 to 100, `multi_cell`); a receipt never needs a second page
(one payment: one line, plus a credit line).

---

## 11. Public pages (`PublicLayout`, `public-`)

Screenshots: `public-refunds-*`.

1. **Top bar**: the page frame of 3.1 with the business name as the brand text; a nav (`.public-nav`: 36 px links,
   padding 0 11, 14.5/600 `--text-2`, `aria-current="page"` in `--text`): Pricing, About, Contact; then `btn-sm` "Sign
   in" (or "Open the app"). Phones: the nav is hidden (the footer has every link).
2. **Draft band** (the Markdown file starts with `<!-- draft -->`): full width under the bar, `--warning-soft`, a bottom border of
   `color-mix(--warning 25%)`, padding 9 16, 14 px centred: a warning badge "Draft" and "This page is a placeholder.
   The owner hasn't published its final text yet." (`role="note"`).
3. **Main** (`.public-main`: max 760, padding 56 24 72; phones 34 18 48): the title serif 44/1.08 500 −0.024em (34 on
   phones); the title from the file's first heading; a "Last updated" line only when the text has one; the prose (`.public-prose`) after a 1.5 px `--rule` (margin-top
   30, padding-top 26): serif 17.5/1.7 (17 on phones); `h2` 24/600 with 1.6em above; list markers `--accent`; links
   `--accent-ink` underlined at 45%. Rendered by the app's Markdown renderer without citations, from `frontend/public/legal/<slug>.md` (no editor).
4. **Footer** (`.public-footer`: `--sidebar`, a top hairline; inner max 1180, padding 28 24 32, wrapping
   space-between): the business name serif 17/600 over the email, phone and address (13.5 `--text-3`); `PolicyLinks`
   and "Payments are processed by Razorpay."
5. **Pricing** (`/pricing`): the page intro, then the plan cards of 3.2 without actions (the price note visible) and
   the compare table of 4.4, then the sentence of brief section 10 and `btn btn-primary` "Sign in to choose a plan".
6. **Contact**: the Markdown, then a facts card (email, phone, address; bank details when the owner adds them to the
   page text).

The login page's footer gains `PolicyLinks` (Pricing, Terms, Privacy, Cancellation and refunds, Contact), 13.5 px,
centred under the card.

---

## 12. Reminder emails

Plain text, no HTML, no images, no tracking; lines under 72 characters. Subject: "Your Standard plan ends in 5 days"
("ends tomorrow", "has ended").

```
Hello Maya,

Your Standard plan on RAG Assistant ends on 7 Nov 2026 (IST).

Renew it, or choose another plan, here:
https://<first PUBLIC_BASE_URLS entry>/plans

Your chats and documents are kept.

RAG Assistant · billing@example.com
You get this email because you gave this address for plan reminders.
Remove it in Settings → Plan & billing to stop them.
```

---

## 13. Dark mode and accents

- Everything is tokens; checked in both themes and spot-checked in Ocean, Sage and Plum (`plans-none-1440-dark-ocean`,
  `chat-locked-1440-light-sage`, `settings-billing-1440-dark-plum`).
- The "Current plan" flag inverts (`--text` on `--bg`): light ink on dark in dark mode.
- Primary buttons and the Recommended flag carry `--accent-text` (dark ink in dark mode), as everywhere.
- The brand (top bar mark, receipt band, public footer name) stays clay whatever the accent.
- The receipt PDF has no dark mode.

---

## 14. Phones and touch

- Breakpoints stay 1100 and 800 (plus 940 for the plan grid, the checkout grid and the admin billing grids, which
  need three 300 px columns). Checked at 390 and 1440: no page scrolls sideways; the compare table, admin tables and
  the billing segmented control scroll in their own boxes.
- Plan cards compact (3.2); checkout's sticky pay bar (5.4); return actions full width, primary on top; Settings
  history as stacked rows; admin tiles in two columns with Net spanning; attention actions under their text.
- Touch targets: CTA 44 px, Pay 46 to 48 px, the Upgrade pill sits inside a 44 px menu item on coarse pointers, the
  plan feature chips are not interactive.
- Safe areas: the pay bar pads `env(safe-area-inset-bottom)`.

---

## 15. Motion

- Nothing new loops except the spinners (pending result, redirect card, busy Pay button); they stop under reduced
  motion and stay visible.
- The return card's mark scales in from 0.92 with a fade over `--dur-3 --ease-out` when the state becomes final; off
  under reduced motion.
- The redirect overlay fades in over `--dur-2` (the modal backdrop's `fade-in`).
- The plan banner uses the notice's existing `fade-in`.

---

## 16. Accessibility

- **Plan cards** are a list labelled "Plans"; each `li` is labelled by its `h2`. Prices read in full through an
  `sr-only` sentence ("2,000 rupees a month, for 30 days"); the visual price is `aria-hidden`.
  Buttons name the plan, and where the visible label doesn't, an `sr-only` part inside the button adds it after the
  visible words, so the visible text starts the accessible name (WCAG 2.5.3): "Upgrade now · ₹1,700" reads "Upgrade now
  to Standard · ₹1,700". The flag is text inside the card, read with it.
- **Included / not included** in words (V7); the compare table has row and column headers, a caption, and
  `sr-only` "Included" / "Not included" in its cells.
- **Checkout**: the steps are an `ol` with `aria-current="step"`; the summary is an `aside` labelled "Summary"; the
  Pay button's name includes the amount; the redirect overlay is `role="status"`.
- **Return page**: a polite live region; focus to the title on a final state; status by title, icon and words.
- **Locked items** are enabled `menuitem`s whose description says where the feature is included and that they open
  the plans page; the lock badge and pill are `aria-hidden`.
- **Meters**: the period bar is a `meter` with "Day 18 of 30"; the admin cost meter has `aria-valuetext` "41% of what
  you keep".
- **Contrast**: every new text and mark pair passes (17). Disabled plan buttons keep their reason in readable text.
- **Forced colours**: the emphasised card keeps a 2 px `CanvasText` border (shadows drop), the flag gets a 1 px
  border, meters and the stacked bars redraw in system colours, like the base spec's `forced-colors` blocks.

---

## 17. Contrast

`python3 -I audit.py` (in the mockup folder) reads the token blocks of `global.css` and checks the pairs this design
adds, in both themes and all five accents: **100 pairs, 0 under 4.5:1**. Lowest values:

| Pair | Lowest |
|---|---|
| `--text-3` on the compare column tint | 4.95 (light Plum) |
| `--text-3` on the Net tile | 4.93 (dark Slate) |
| `--text-2` on the compare column tint / Net tile | 6.64 / 6.91 |
| `--accent-ink` on the compare column tint / Net tile | 5.74 / 5.98 |
| `--text` on either | 11.05 |
| `--accent-text` on `--accent` (Recommended flag) | 5.43 (light Sage) |
| `--bg` on `--text` (Current plan flag) | 13.27 |

Every other pair is an audited base-spec pair: status colours on their soft fills (badges, notices, result marks),
`--accent-ink` on `--accent-soft` (Upgrade pill, plan tiles), `--text-3` on `--surface`/`--bg`/`--surface-2`.
`audit.py` is a design-time script in the mockup folder, not in the repo; the integrator rechecks these pairs on the
built CSS (functional brief 13.2).

---

## 18. Class map and owners

Prefixes per the functional brief section 8: `plans-` (plans, checkout, return pages), `billing-` (Settings tab,
banners, locked hints, policy links), `public-`, and `admin-` additions. Owners per its 13.2.

| Mockup classes | App file | Owner |
|---|---|---|
| `.plans-topbar*`, `.plans-main*`, `.plans-intro`, `.plans-title`, `.plans-lede`, `.plans-notices`, `.plans-notice-actions`, `.plans-grid`, `.plans-how*`, `.plans-foot`, `.plans-back`, `.plans-steps`, `.plans-step*`, `.plans-checkout*`, `.plans-panel*`, `.plans-chosen*`, `.plans-chips`, `.plans-dates`, `.plans-form`, `.plans-consent`, `.plans-summary`, `.plans-lines`, `.plans-total*`, `.plans-pay`, `.plans-secure`, `.plans-methods`, `.plans-method`, `.plans-paybar*`, `.plans-redirect*`, `.plans-result*`, `.plans-receipt*`, `.plans-changes`, `.plans-credit`, `.plans-upi-note`, `.plans-remind` | `src/pages/plans.css` | APP |
| `.plans-card*`, `.plans-flag*`, `.plans-price*`, `.plans-amount*`, `.plans-per`, `.plans-actions`, `.plans-cta`, `.plans-alt`, `.plans-reason`, `.plans-section`, `.plans-label`, `.plans-feature*`, `.plans-limits`, and the compare table's `.plans-compare`, `.plans-table*`, `.plans-h2*` (both also used on `/pricing`) | `src/components/billing/plan-card.css`, imported by `PlanCard.tsx` and `PlanCompareTable.tsx`; it holds only these `plans-` rules (S13) | APP (stage 1) |
| `.billing-banner`, `.billing-locked-*`, `.billing-lock-badge`, `.billing-upgrade-pill`, `.billing-more-models`, `.billing-policy-links`, `.billing-current*`, `.billing-plan-tile`, `.billing-days`, `.billing-timeline`, `.billing-facts`, `.billing-list*`, `.billing-form*`, `.billing-table*`, `.billing-receipt-link`, `.billing-help`, `.billing-ended*`, `.billing-yourdata*`, `.billing-rate-tag` | `src/components/billing/billing.css` | APP |
| `.admin-billing-*`, `.admin-attention-*`, `.admin-title-count`, `.admin-tiles-billing`, `.admin-tile-sub`, `.admin-tile-net`, `.admin-months`, `.admin-month-*`, `.admin-plan-*`, `.admin-cost*`, `.admin-feature-chips`, `.admin-estimate-*`, `.admin-swatch*`, `.admin-limit-grid`, `.admin-input-unit`, `.admin-model-check*`, `.admin-switch-rows`, `.admin-apply-choice`, `.admin-expiring*`, `.admin-export` | `src/components/admin/admin.css` | ADMIN |
| `.public-*` (and its copy of the top bar values) | `src/pages/public/public.css` | ADMIN |
| icons of 2.2 | `src/components/ui/icons.tsx` | APP (stage 1; ADMIN uses them) |
| `.mock-tall`, `data-mock-above`, the capture harness | mockup only | – |

The mockup keeps every rule in one `css/billing.css`; split it by this table. Each file holds one prefix:
`billing-` rules that sit inside chat menus or Settings use their own classes on elements the billing components
render, and never restyle `chat-`, `settings-` or `conn-` classes.

---

## 19. Builder checklist

APP (stage 1): the icons of 2.2; `lib/billing.ts` (2.3); `PlanCard` with 3.2's desktop and phone layouts and its
skeleton; `PlanCompareTable` (4.4); `PolicyLinks`.

APP (stage 2):

1. Build 4 to 8 against the screenshots at 1440 and 390, light and dark.
2. Check visually:
   - exactly one primary button on `/plans` (none for an `active` user who isn't expiring);
   - prices, buttons and "Included" line up across the three cards at 1440 and 1000 px;
   - the compare table's first column stays put while the plans scroll at 390;
   - "Pay ₹X" equals the summary total; the pay bar shows on phones and the summary's Pay doesn't;
   - a Starter user sees no "Files"/"Images" tags in the model menu, one locked "Create a document", and the Upgrade
     pill on locked items;
   - the payment history becomes stacked rows in the phone Settings sheet.
3. No hex colours in the new CSS (except the receipt PDF, which is not CSS), no `prefers-color-scheme`.

ADMIN (stage 2): 9 and 11 against `admin-*` and `public-*` shots; the Net tile and compare tints are in the audit.

CORE (stage 2): the receipt per section 10 and appendix B; compare with `receipt-pdf.png`; check ₹ and – render in
both fonts and the test watermark sits under the text.

The integrator: recheck the pairs of 17 on the built CSS, and the screens against 0.4's changes.

---

## Appendix A: the plan card grid (the rules that make V5 and V9 work)

```css
.plans-grid {
  display: grid;
  grid-auto-columns: minmax(0, 1fr);
  grid-auto-flow: column;
  grid-template-rows: repeat(5, auto);
  column-gap: 20px;
  row-gap: 0;
  margin: 44px 0 0;
  padding: 0;
  list-style: none;
}
.plans-card {
  position: relative;
  display: grid;
  grid-row: 1 / span 5;
  grid-template-rows: subgrid; /* head · price · actions · included · limits */
  min-width: 0;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--surface);
  box-shadow: var(--shadow-xs);
}
.plans-card-featured {
  border-color: var(--accent);
  box-shadow: 0 0 0 1px var(--accent), 0 0 0 5px var(--accent-soft), var(--shadow);
}
.plans-feature-short { display: none; }

@media (max-width: 939.98px) {
  .plans-grid { display: flex; flex-direction: column; gap: 22px; max-width: 560px; margin-inline: auto; }
  .plans-card { display: flex; flex-direction: column; }
}
@media (max-width: 799.98px) {
  .plans-price { position: absolute; top: 22px; right: 18px; padding: 0; text-align: right; }
  .plans-card-name { max-width: calc(100% - 132px); }
  .plans-actions { order: 9; padding: 4px 18px 18px; border-bottom: 0; }
  .plans-card .plans-label, .plans-price-note, .plans-feature small, .plans-feature-tag { display: none; }
  .plans-features { display: flex; flex-wrap: wrap; gap: 6px; }
  .plans-feature {
    display: inline-flex; align-items: center; gap: 5px; height: 28px; padding: 0 10px 0 8px;
    border: 1px solid var(--border); border-radius: var(--radius-pill); background: var(--bg);
    font-size: 13.5px; font-weight: 600;
  }
  .plans-feature-off { border-style: dashed; background: transparent; font-weight: 500; }
  .plans-feature-short { display: inline; }
  .plans-feature-long { display: none; }
  .plans-limits { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
  .plans-limits > div {
    flex-direction: column-reverse; justify-content: flex-end; gap: 1px; padding: 10px 12px;
    border: 1px solid var(--border); border-radius: var(--radius); background: var(--bg);
  }
  .plans-limits > div:nth-child(n + 4) { display: none; }
}

/* Settings: the payment history as stacked rows in a narrow panel */
@container settings-panel (max-width: 600px) {
  .billing-table thead { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
  .billing-table, .billing-table tbody, .billing-table td { display: block; }
  .billing-table tr {
    display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 2px 12px;
    padding: 12px 0; border-bottom: 1px solid var(--border);
  }
  .billing-table td { padding: 0; border: 0; }
  .billing-table .billing-table-date { display: none; }
  .billing-table .billing-table-desc { grid-area: 1 / 1; }
  .billing-table .billing-table-amount { grid-area: 1 / 2; color: var(--text); font-weight: 650; }
  .billing-table .billing-table-receipt { grid-area: 2 / 1; }
  .billing-table .billing-table-status { grid-area: 2 / 2; justify-self: end; }
}
```

## Appendix B: the receipt layout in fpdf2 (prototype, not app code)

The core of `receipt_mock.py`, run with the backend venv (`fpdf2==2.8.9`). CORE turns it into `billing/receipts.py`
reading the receipt snapshot.

```python
pdf = FPDF(format="A4", unit="mm")
pdf.set_auto_page_break(False)
pdf.set_margins(18, 18, 18)
pdf.add_page()
pdf.add_font("Noto", "", "app/fonts/NotoSans-Regular.ttf")
pdf.add_font("Noto", "B", "app/fonts/NotoSans-Bold.ttf")

def text(x, y, s, size=9.5, style="", color=INK, align="L", w=0, spacing=0.0):
    pdf.set_font("Noto", style, size); pdf.set_text_color(*color); pdf.set_char_spacing(spacing)
    pdf.set_xy(x, y); pdf.cell(w, size * 0.45, s, align=align); pdf.set_char_spacing(0)

if test_mode:  # first, so everything prints over it
    pdf.set_font("Noto", "B", 40); pdf.set_text_color(236, 230, 220)
    mark = "TEST: not a real payment"
    with pdf.rotation(35, 105, 160):
        pdf.text(105 - pdf.get_string_width(mark) / 2, 164, mark)

pdf.set_fill_color(169, 77, 43); pdf.rect(0, 0, 210, 2.6, style="F")          # clay band
text(18, 18, seller.name, 15, "B")                                               # seller block from y 27, 4.6 apart
text(120, 18.5, "PAYMENT RECEIPT", 7.2, "B", INK3, "R", 72, spacing=0.6)
text(120, 24, number, 13, "B", align="R", w=72)
text(120, 31.5, f"Issued {issued}", 8.8, color=INK2, align="R", w=72)
# status pill: rect(192 - w, 37.5, w, 5.6, style="F", round_corners=True, corner_radius=2.8), Bold 7.5, spacing 0.5
# rule at y 52; "BILLED TO" / "PAYMENT" blocks at y 57; table header band at y 86 (8 mm, #f3f0e8);
# lines from y 97; totals at x 118..192, 5.6 apart; footer hairline at y 276, text at y 279.5
```
