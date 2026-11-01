# Visual spec: sign-up, sign-in, password reset and account email

> How self-service sign-up and its neighbours look, built on the "Paper & Clay" design system of
> [`ui-visual-design.md`](ui-visual-design.md) (**the base spec**). The behaviour, the contract and the copy that
> carries behaviour are in [`signup-auth.md`](signup-auth.md) (**the functional brief**); `SPEC.md` binds both. On looks
> (layout, type, colour, spacing, the composition of each screen) this document wins; on behaviour, accessibility and
> contract the functional brief and `SPEC.md` win. Section 0.2 records where revision 1 of this spec bent the
> functional brief and how each point was settled. **Revision 2**, 2026-11-01, final: designed and reviewed, not
> built. The functional brief's revision 2 changed some things the mockups show; section 0.4 lists them, and where a
> screenshot and this text differ, **the text wins**.
>
> **Reference mockup** (static HTML on the app's own CSS, revision 1):
> `/private/tmp/claude-501/-Users-bikash-rag-app/73efbff7-64da-4b99-a056-7cb30809179c/scratchpad/wf-signup/visual/`
> (`pages/*.html`; `css/` holds copies of the app's `global.css`, `fonts.css`, `login.css`, `product-tour.css`,
> `settings.css`, `admin.css` and the other feature files, plus the new `auth-components.css`, `auth.css`,
> `settings-auth.css`, `admin-auth.css` and the mock-only `mock.css`; `fonts/` the bundled woff2 files; `img/` the
> owner's `hero-1600.webp` and `hero-800.webp`). The sign-in page's aside (hero picture, copy, product tour), the chat
> behind Settings and the admin page are the **real built app**: `capture/capture.mjs` builds nothing itself, it
> renders a fresh `vite build` of the current `main` with canned API answers (the typed test fixtures of
> `frontend/src/test/`) and saves the DOM; `build.py` and `build_app.py` swap in the new parts, so everything around
> them is pixel-identical to the app. `shoot.mjs` takes the screenshots and checks for page errors, failed requests
> and sideways overflow; `measure.mjs` measures the cards at the sizes the sign-in page was tuned for; `audit.py`
> checks the new colour pairs. Scratch paths can disappear: every value a builder needs is in this file.

## Screenshots

In `…/wf-signup/visual/shots/` (103 files). 1440 px shots at device pixel ratio 1, 390 px shots at 2. The sign-in and
sign-up pages at 1440 are the viewport (the form column is sticky, as in the app); every other shot is the whole page.
Phone shots of the split pages show the whole page (picture, card, the owner's copy below). Settings and admin dialogs
are shown whole (`mock-tall`); the real ones scroll inside. Rows marked † show something section 0.4 changed.

| Screen | Files (best first) |
|---|---|
| **Sign-in, sign-up open, Google on** † | `login-1440-light.png`, `login-390-light.png`, `login-1440-dark.png`, `login-390-dark.png`, `login-1440-light-sage.png` (Sage accent) |
| **Sign-up, ready to submit** † | `signup-1440-light.png`, `signup-390-light.png`, `signup-1440-dark.png`, `signup-390-dark.png`, `signup-1440-dark-ocean.png` (Ocean accent) |
| **Check your inbox** (countdown) and resending † | `signup-sent-1440-light.png`, `signup-sent-390-dark.png`, `signup-sent-1440-dark.png`, `signup-sent-390-light.png`, `signup-resend-1440-light.png`, `signup-resend-390-light.png` |
| Sign-in: wrong password with the two hints † | `login-error-1440-light.png`, `login-error-390-light.png`, `login-error-1440-dark.png`, `login-error-390-dark.png` |
| Sign-in: Turnstile challenge | `login-challenge-1440-dark.png`, `login-challenge-1440-light.png`, `login-challenge-390-light.png`, `login-challenge-390-dark.png` |
| Sign-in: `?google=link_required` | `login-google-link-1440-light.png`, `login-google-link-390-light.png`, `login-google-link-1440-dark.png`, `login-google-link-390-dark.png` |
| Sign-in: sign-up closed, no Google (today's foot note) | `login-closed-1440-light.png`, `login-closed-390-light.png` |
| Sign-up: password refused, empty form, closed † | `signup-error-1440-light.png`, `signup-error-1440-dark.png`, `signup-error-390-*.png`, `signup-empty-1440-light.png`, `signup-closed-1440-light.png`, `signup-closed-390-light.png` |
| **AuthCard states board** (every result state) † | `states-1440-light.png`, `states-1440-dark.png` |
| **Finish creating your account** (`/verify-email`) † | `verify-1440-light.png`, `verify-390-light.png`, `verify-1440-dark.png`, `verify-390-dark.png`, `verify-wrong-1440-light.png`, `verify-wrong-390-light.png`, `verify-expired-1440-light.png`, `verify-expired-390-*.png`, `verify-expired-1440-dark.png` |
| Google sign-up (`/signup/google`) | `google-signup-1440-light.png`, `google-signup-1440-dark.png`, `google-signup-390-light.png`, `google-signup-390-dark.png` |
| Forgot password and sent | `forgot-1440-light.png`, `forgot-390-light.png`, `forgot-1440-dark.png`, `forgot-390-dark.png`, `forgot-sent-1440-light.png`, `forgot-sent-390-dark.png`, `forgot-sent-*` |
| Reset password and done † | `reset-1440-light.png`, `reset-390-light.png`, `reset-1440-dark.png`, `reset-390-dark.png`, `reset-done-1440-light.png`, `reset-done-390-*.png` |
| Confirm email (`/confirm-email`) | `confirm-email-1440-light.png`, `confirm-email-390-light.png`, `confirm-email-1440-dark.png`, `confirm-email-390-dark.png` |
| **Settings → Account** (email user, Google connected, change pending) † | `settings-account-1440-light.png`, `settings-account-1440-dark.png`, `settings-account-390-light.png`, `settings-account-390-dark.png`, `settings-account-1440-dark-plum.png` (Plum accent) |
| Settings → Account, Google-only (set a password, re-authenticate) † | `settings-account-google-1440-light.png`, `settings-account-google-1440-dark.png`, `settings-account-google-390-light.png`, `settings-account-google-390-dark.png` |
| Settings → Account, admin-created without an email (add email, connect Google) | `settings-account-change-1440-light.png`, `settings-account-change-390-light.png` |
| Settings → Plan & billing: the Emails section | `settings-billing-mail-1440-light.png`, `settings-billing-mail-390-light.png` |
| **Admin → Users** with the Sign-up card † | `admin-users-1440-light.png`, `admin-users-1440-dark.png`, `admin-users-390-light.png`, `admin-users-390-dark.png` |
| **Admin: Sign-up dialog** (opening with warnings) † | `admin-signup-1440-light.png`, `admin-signup-1440-dark.png`, `admin-signup-390-light.png`, `admin-signup-390-dark.png` |
| Admin: New user; Edit user † | `admin-user-new-1440-light.png`, `admin-user-new-1440-dark.png`, `admin-user-new-390-*.png`, `admin-user-edit-1440-light.png`, `admin-user-edit-390-light.png` |
| The HTML email frame (verification) † | `email-verify-760-light.png`, `email-verify-390-light.png` |

`shoot.mjs` found no page errors, no failed requests and no sideways page scroll at 390 or 1440 on any page (the admin
user table scrolls in its own box, as today). The admin user table in the mockup leaves out the columns this design
doesn't touch (Images, Rate limit, Uploads, Models); the app keeps them.

---

## 0. Decisions

### 0.1 Visual decisions

| # | Decision | Why |
|---|---|---|
| V1 | **No new tokens.** Every colour comes from `global.css`. Two things aren't ours: the **Google button**'s fill, stroke, text and "G" (Google's branding guidelines, light and dark themes; scoped custom properties on `.auth-google`, the documented exception like the theme previews) and the **Turnstile widget** (Cloudflare's iframe; we only place it) | The base spec's tokens pass AA by construction; the two brand surfaces must look like their owners' or they lose trust and break the guidelines |
| V2 | **Two page families.** The front door (`/login`, `/signup`) is the owner's split layout (`AuthSplit`): hero picture, copy and tour untouched on the left, the card on the right. Every task page (`/verify-email`, `/signup/google`, `/forgot-password`, `/reset-password`, `/confirm-email`, `/undo-email`) is an **AuthCard**: a sheet of paper (`--bg`) on the desk (`--sidebar`) with the brand top-left, the card centred and the policy links at the foot; on phones the card is the page | The front door sells; a task page opened from an email should hold only the task. Desk and paper are the base spec's first principle |
| V3 | **"Continue with Google" comes first on both front-door pages**: under the lede (and the switch line), then an "or" rule, then the email form | One place for Google on both pages, so someone who signed up with Google finds it where they first used it; the 401 hint ("Use Continue with Google") points at a button already in view; Google is the shortest path for a new visitor |
| V4 | **One primary button per card** (Sign in, Create account, Send reset link, Save password, Confirm this email, Undo the change). Google is Google's own secondary look | The base spec's button hierarchy |
| V5 | **The way to the other page is a line under the lede**: "New to AskNuvexusDoc? **Create an account**" on `/login` (only while sign-up is open), "Already have an account? **Sign in**" on `/signup`. The grey foot block keeps only today's admin note, on `/login`, while sign-up is closed | `/` sends signed-out visitors to `/login`; on a phone the foot is below the fold, and a new visitor must see the way in on the first screen (functional brief UX-03) |
| V6 | **"Forgot password?"** sits at the right end of the Password label row (14/600 `--accent-ink`) | Where people look for it; it costs no height |
| V7 | **Password rules are two rows under a new-password field**: a live length rule (a ring that becomes a success check) and the server's rule (a shield: "Not a common or leaked password, and not your email"). A refusal **replaces the row it broke** with the server's sentence in danger 600 (`role="alert"`) and marks the input invalid. No strength meter | Honest: the client can check only the length; the rest is the server's (blocklist, context words, breach check). The error lands where the rule was |
| V8 | **Turnstile sits directly above the submit button**, full width (`size: flexible`, 65 px), in a slot that reserves 65 px. Its theme follows the **app's resolved theme**, not the OS. **Submit buttons stay enabled**: pressing one before the check finishes shows "Checking you're not a robot…" under the widget and sends the form when it does | Nothing moves when it arrives; a widget in the OS theme on a page in the other theme looks broken; a disabled button gives no reason |
| V9 | **The sign-in challenge is not an error**: a caption with an `--accent-ink` shield ("Confirm you're not a robot, then sign in again.") over the widget; the typed values stay | The user did nothing wrong on this attempt; red would read as "wrong password" |
| V10 | **The 401 keeps today's `login-error` block**, with the two static hints as a quieter second part inside it (13.5 `--text-2` regular); "send it again" and "Continue with Google" in the hints are links | One message, read together; the hints never depend on the account |
| V11 | **"Check your inbox" replaces the form in the same card**: a 56 px mail mark, the serif title, the address, **three numbered steps** (the link within 24 hours; one click in this browser, the password on another device; then a plan), "Didn't get it?" with **Resend in 0:42** on the button itself, "Wrong address? Start again" | The password binding surprises people on a second device unless they're told; the countdown belongs to the button it holds back |
| V12 | **AuthCard has two layouts**: forms left-aligned (serif 30/500 title, lede, an identity row, fields, a full-width primary, centred links), results centred (a 64 px status mark in a tone, serif 28/500 title, one or two sentences, actions). The marks are the plans return card's vocabulary | One calm vocabulary for "done", "expired", "check your email" across the app |
| V13 | **An identity row says who a page acts for** (an address with a mail tile, a username with the avatar, a Google account with the "G"): a quiet `--surface` row, never an input | The token pages must show whose account changes before the user types a password |
| V14 | **Phones keep the owner's order** on the front door (brand and picture above the card, copy below). **The sent and closed states drop the picture** (the brand alone leads), so the steps and Resend are on the first screen | Sign in stays on the first screen; the picture adds nothing once the user has signed up |
| V15 | **Settings → Account**: the profile's name is the display name (the email when there is one) and the Email fact is its status; an **Email** row (description and Change/Add email…) with a pending change as an info notice; a **Sign-in methods** list (the sign-ins list look) with Password and Google each carrying their action; a Google **step-up** block ("confirm it's you with Google") opens before a change that needs it; sign-in history says "with Google" or "with password" | Each method is visibly a way in; the pending address never looks like the current one; the address appears once |
| V16 | **Plan & billing gains an "Emails" section**: where mail goes in one sentence, then a bordered box with the two switches. The receipt details lose the email field | One verified address per account, said once, next to the switches it governs |
| V17 | **Admin → Users gains a Sign-up card** above the list: the title with a status line (● Open / ● Closed), four fact tiles (Methods, Sign-ups, Email today, Checks) and **Edit…**. The dialog reuses form sections, switches and the **going-live checklist rows** of Business & payments for its Checks | The admin's existing vocabulary; the state of sign-up is legible without opening anything |
| V18 | **The user list shows the email as the user cell's second line** (a success check, a warning clock with "Pending:", or "No email") and a **Signed up** column (source, with the "G" for Google, over the date; sortable, newest first). A search box leads the toolbar; a sign-up select sits beside the plan chips | Two facts the admin scans per row without widening the table by two columns; finding one new account among many |
| V19 | **Every user email shares one HTML frame**: the desk colour around a paper card, a text brand mark (no image), a Georgia title, system sans body, one clay button with the link written out under it, and a footer that says why the mail came. Light only | Gmail's rules (no images, no remote resources); the same look as the app without its fonts |
| V20 | **Small helpers sit where the problem is**: "Did you mean **name@gmail.com**?" under an email field; in an in-app browser, a neutral notice with **Copy link** in place of the Google button | A typo is fixed before it bounces; a Google button that can't work is never shown |

### 0.2 Where revision 1 bent the functional brief, and how it was settled

Settled in the functional brief's revision 2 (its 0.2, rows B1 to B16): **11 adopted, 5 adapted**. The "If not
adopted" fallbacks of revision 1 are no longer needed and are dropped; section numbers in the "Brief section" column
are the functional brief's revision 1.

| # | Change | Brief section | Settled |
|---|---|---|---|
| B1 | **Google first on `/login`** (V3): `.login-alt` (the button) and the "or" rule between the lede and the form | 13.1 | Adopted |
| B2 | **The foot's "Create an account" as a small secondary button** in today's foot block | 13.1 | Adapted: a line under the lede instead (V5); the foot block holds only today's note while sign-up is closed |
| B3 | **Turnstile's `theme` is the app's resolved theme** (`document.documentElement.dataset.theme`), re-rendered when it changes | 7.3 | Adopted |
| B4 | **`PasswordRules` shows two rows** (V7) and takes the refusal: `PasswordRules({ id, password, minimum, rejected })` | 13.2, 13 | Adopted |
| B5 | **Resend on demand**: "Resend in 0:42" (disabled, the countdown inside), then "Resend email"; a click mounts the widget under the row and the request goes when its token arrives ("Sending…"), then "✓ Sent again" | 13.2, 4.3 | Adopted, with the client-side limits of the brief's 4.3 (a help sentence after the third mail) |
| B6 | **The sent copy**: "We sent an email to **<address>**." and three steps | 13.2 | Adapted: step 2 reads "In this browser that's all it takes. On another device, enter the **password you just chose**." |
| B7 | **`/verify-email` gets a lede and a way out**: the lede, the address in an identity row ("Confirmed by this link"), "Forgot it? Sign up again" | 13.3 | Adopted; the password field and the way out appear only when the browser didn't start the sign-up |
| B8 | **`AuthCard` takes a status mark and a lede** (`lede?`, `mark?`); with `mark` it lays out as a result | 16.3, 13 | Adopted |
| B9 | **`GoogleButton` gains `size?: "lg" \| "sm"`** and the label "Sign in again with Google" | 16.3 | Adapted: `size` adopted; every Google button says **Continue with Google** (Google allows only "Sign in with", "Sign up with" and "Continue with Google"), so the `label` prop goes |
| B10 | **Phones: the sent and closed states hide the picture** (V14) | 13.2 | Adopted (`AuthSplit compactOnPhone`) |
| B11 | **Settings → Account shows the address once**, as the profile's Email fact | 13.7 | Adapted: the address is the profile's name (the display name); the Email fact shows only its status |
| B12 | **The list's email as the user cell's second line**, a "Signed up" column, a source select | 13.9 | Adopted, with a search box and a "Last 7 days" option |
| B13 | **The Sign-up dialog's "Open anyway"** inside a warning notice | 13.9 | Adopted |
| B14 | **The Sign-up card's tiles** computed from returned fields | 13.9 | Adapted: Methods, Sign-ups, Email today, Checks (the brief dropped domain lists) |
| B15 | **Copy for states the brief left open** (checking, `email_taken` on `/verify-email`, the Google ticket expiry, forgot-sent's notice) | 13.3 to 13.6 | Adopted |
| B16 | **The HTML email puts the link on a button** with the full link written out under it | 6.4 | Adopted, with the brief's new copy and labels (10) |

### 0.3 Settled points

- **Google first on the sign-in page**: yes (B1).
- **The Google button's font**: the app's sans at 600, as shown, unless the integrator's spike (functional brief
  18.2.5) finds that Google's guidelines or the font licence require Google Sans Medium from the pre-approved asset;
  then only `.auth-google`'s `font-family` changes.
- **The sending address** (`no-reply@asknuvexusdoc.com`) shows as the From; the email frame needs no logo file.

### 0.4 What changed after the screenshots (functional brief revision 2)

The text of this spec already follows these; the screenshots don't:

- **Front door**: "New to AskNuvexusDoc? Create an account" is a line under the sign-in lede (sign-up open only), and
  "Already have an account? Sign in" one under the sign-up lede; the foot switch blocks are gone, and the sign-in foot
  block shows today's note only while sign-up is closed. The 401's first hint ends with a "send it again" link. A
  `?google=use_password` notice exists; `not_allowed` is gone.
- **Buttons stay enabled** on every auth form (Sign in, Create account, Send reset link, Save password); missing fields
  get their own error on submit, and a pending robot check is said in words under the widget.
- **Sent state**: step 2 is reworded (B6); after the third mail a help notice replaces "Didn't get it?".
- **Verify page**: in the browser that signed up there is no password field (lede "Your email is confirmed. Create
  your account to continue."); a `signup_reset` result ("Too many wrong passwords").
- **Google sign-up from the sign-in page**: the title "No AskNuvexusDoc account uses this address yet" and **I already
  have an account** beside **Create account**.
- **Reset page**: a tick box "Also disconnect Google sign-in (<masked address>)" for accounts with Google; no invite
  variant ("Set your password" now means an account without a password).
- **Confirm page**: "Sign in to confirm" and "You're signed in to another account" states. **A new `/undo-email`
  page** (result layout).
- **Settings → Account**: the profile's name is the email; the Email fact is a "Confirmed" badge; a Username fact;
  "Set a password" and a password-less "Change email" open a **Continue with Google** step-up block first, then the
  form, with no email round trip; Connect's Google button reads "Continue with Google".
- **Admin**: the Sign-up card's tiles are Methods, **Sign-ups**, Email today, Checks (no Email domains tile), with
  more warnings; the dialog has no Email domains section and more checks rows; the user list gains a search box and a
  "Last 7 days" option; the New user form has no email or invite; Edit user's facts box is read-only (no Disconnect,
  no Email field); Admin → Billing shows the email under each username.
- **Public pages**: a **Sign up** button in the public top bar; `/pricing`'s button reads "Create an account to choose
  a plan".
- **Emails**: no welcome mail; new copy and button labels (10); "Questions? Reply to this email." in the footer.
- **New helpers**: the email typo suggestion and the in-app browser notice (3.9, 3.10).

---

## 1. Principles

1. **The front door stays the owner's.** Only the card's content changes; the picture, the copy, the tour and their
   order and timing are the owner's and untouched.
2. **Each way in looks like itself.** Google's button is Google's; the email form is ours; the robot check is
   Cloudflare's, placed calmly.
3. **Every wait has words.** Sent, expired, checking, waiting for confirmation, waiting for the robot check: a mark or
   a line, a title and a sentence, never a bare spinner, a colour or a disabled button.
4. **Errors sit where they happened**: under the field, in place of the rule, or above the button for the form;
   never in a toast.

---

## 2. Tokens, icons and formats

### 2.1 Tokens

None new (V1). Recipes this feature uses:

| Use | Value |
|---|---|
| Google button, light | fill `#ffffff`, 1 px stroke `#747775`, text `#1f1f1f`, hover fill `#f2f2f2` (scoped as `--auth-google-fill`, `-stroke`, `-text`, `-hover` on `.auth-google`) |
| Google button, dark (`:root[data-theme="dark"] .auth-google`) | fill `#131314`, stroke `#8e918f`, text `#e3e3e3`, hover `#1e1f20` |
| Result mark, accent | `--accent-soft` fill, `--accent-ink` icon, halo `0 0 0 9px color-mix(in srgb, var(--accent-soft) 45%, transparent)` |
| Result mark, success / warning | the tone's soft fill and colour, the same 9 px halo at 45% |
| Result mark, neutral | `--surface-2` fill, `--text-2` icon, halo at 55% of `--surface-2` |
| Sent mark (split card) | 56 px, `--accent-soft`, a 7 px halo at 45% |
| AuthCard page glow | the login aside's: a 560 px radial of `--brand` 18% → 5% → transparent off the top-right corner |
| AuthCard sheet | `--bg`, 1 px `--border`, radius `--radius-xl`, `--shadow` |
| Turnstile slot | `min-height: 65px`; loading: 1 px dashed `--border-strong`, radius `--radius` |

### 2.2 New icons (`components/ui/icons.tsx`, 24×24, stroke 1.75, `currentColor`)

| Name | Paths | Used for |
|---|---|---|
| `MailCheckIcon` | `<path d="M20.5 12.5V7a2 2 0 0 0-2-2h-13a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h7"/><path d="m3.8 6.4 8.2 6.1 8.2-6.1"/><path d="m15.5 18 2 2 3.5-3.6"/>` | "Check your inbox", "Check your email" |
| `LinkBrokenIcon` | `<path d="M9.5 14.5 7.7 16.3a3 3 0 0 1-4.2-4.2L6.5 9.1a3 3 0 0 1 4.2 0"/><path d="m14.5 9.5 1.8-1.8a3 3 0 0 1 4.2 4.2l-3 3a3 3 0 0 1-4.2 0"/><path d="M4 4l2 2M20 20l-2-2M9 3.5V5M15 20.5V19"/>` | "This link is incomplete" |
| `AtSignIcon` | `<circle cx="12" cy="12" r="3.6"/><path d="M15.6 12v1.4a2.5 2.5 0 0 0 5 0V12a8.6 8.6 0 1 0-3.4 6.9"/>` | "Confirm your email", "Sign in to confirm" |

The **Google "G"** is not an icon: `components/auth/google-g.svg` (APP stage 1) is Google's standard four-colour mark
from its sign-in branding assets, used unchanged at 18 px (14 px in the admin list), never recoloured; inline it
(no request) and keep `aria-hidden`.

Reused: `Mail`, `Key`, `Shield`, `Clock`, `CheckCircle`, `Check`, `AlertCircle`, `Alert`, `Info`, `Refresh`,
`ArrowLeft`, `ArrowDown`, `Settings`, `User`, `Eye`/`EyeOff`, `Laptop`/`Phone`, `Copy`, `Search`.

### 2.3 Formats

- **Addresses** are shown as stored (the functional brief's 3.2), never truncated on auth pages
  (`overflow-wrap: anywhere`); in the admin list they truncate with an ellipsis at 30ch and carry the full address in
  `title`. Masked addresses (`a***@gmail.com`) appear only where the brief says.
- **Times**: relative in Settings and the admin ("sent 3 minutes ago"), dates as "30 Oct 2026", sign-in rows as
  today ("Today, 23:50").
- **Countdown**: "0:42" (`m:ss`, tabular figures) inside the Resend button.
- **Methods in sign-in rows**: "with Google · 203.0.113.x", "with password · 49.36.12.x".

---

## 3. Shared pieces (`components/auth/`, `auth-components.css`, APP stage 1)

### 3.1 GoogleButton (`.auth-google`)

| Part | Value |
|---|---|
| Size | `lg` (default): 46 px tall, full width, padding 0 12, radius `--radius` (matches the 46 px inputs and **Sign in**); `sm` (`.auth-google-sm`): 38 px, auto width, padding 0 14 0 12, radius `--radius-sm`, 15 px |
| Content | the 18 px "G", 10 px gap, **"Continue with Google"** (sans 16/600, `line-height: 1`, no wrap), centred as a group. The only label, everywhere (0.2, B9) |
| Themes | 2.1; hover the hover fill; `:active` 0.5 px down; focus `--focus-ring`; disabled opacity 0.5 |
| Busy | after the click, while `POST /auth/google/start` runs: `aria-busy`, an 11 px spinner after the label in the button's text colour; the "G" and label stay |
| In-app browser | replaced by the notice of 3.10 |

### 3.2 The "or" rule (`.auth-or`)

A flex row, gap 14, margin 22 0 (18 on phones): two 1 px `--border` lines and the word, 13.5 `--text-3`. Text: "or"
on sign-in, "or sign up with email" on sign-up. `role="separator"` with the word as its label.

### 3.3 Turnstile (`.auth-turnstile`)

- The slot reserves 65 px. The widget is Cloudflare's (size `flexible`: 100% wide, at least 300 px; the card's
  narrowest is 320 px at 360 px wide). The mockup draws an approximation (`.mock-ts`): its square corners and greys
  are Cloudflare's; don't restyle the iframe.
- **Loading**: a dashed slot with a spinner and "Loading the security check…" (14 `--text-3`).
- **Waiting after a submit** (`.auth-turnstile-wait`): 8 px under the slot, a 13 px spinner and "Checking you're not a
  robot…" (14 `--text-2`, `aria-live="polite"`); the form goes out when the token arrives and the line disappears.
- **Couldn't load** (15 s, or `error-callback`): a warning `ui-notice` with the alert icon, the brief's sentence and a
  small **Try again**; where Google is available, "or continue with Google" links to the page's Google button.
- **Challenge caption** (sign-in only): `.auth-turnstile-caption`, 14/1.45 `--text-2`, a 16 px `--accent-ink` shield,
  10 px above the widget.
- The submit button **stays enabled** (V8).

### 3.4 PasswordRules (`.auth-rules`)

Under the input inside the field (the field's 7 px gap): a list, gap 5, 13.5/1.4 `--text-3`, each row a 14 px icon
and the text, gap 7.

| Row | Not met | Met | Refused |
|---|---|---|---|
| "At least 8 characters" (`PASSWORD_MIN_LENGTH` from `/auth/config`) | a 14 px ring (`--text-3`) | `CheckCircle` in `--success`, text `--text-2`, `sr-only` ": done" | `too_short`, `too_long`: the row becomes the server's sentence |
| "Not a common or leaked password, and not your email" | a 14 px shield (`--text-3`) | – (checked on submit) | `common`, `context`, `breached`: the row becomes the server's sentence |

Refused rows: `.auth-rule-error`, `AlertCircle` 14, `--danger` 600, `role="alert"`; the input gets
`aria-invalid="true"`; editing the password puts the row back. The list is linked to the input with
`aria-describedby`; changes aren't announced as they happen.

### 3.5 The terms tick box (`.auth-terms`, `TermsCheck`)

A `<label>`: an 18 px `ui-check` (1.5 px down) and "I agree to the **Terms** and the **Privacy policy**." at 14.5/1.45
`--text-2`, gap 11. Links `--accent-ink` 600, underlined (1 px, offset 2), open in a new tab with an `sr-only`
"(opens in a new tab)". Unticked by default. Submitting unticked shows `error-text` "Tick the box to accept the terms."
under it and moves focus to the box. The reset page's "Also disconnect Google sign-in" tick box uses the same look.

### 3.6 Identity row (`.auth-identity`, `IdentityRow`)

Padding 10 14, gap 12, 1 px `--border`, radius `--radius`, `--surface`. Lead: a 34 px avatar (a username), a 34 px
`--surface-2` circle with a 17 px `Mail` icon in `--text-2` (an address), or the same circle with the "G" (a Google
account). Text: the main line 15/650 `--text` (ellipsis), the sub-line 13.5 `--text-3`.

### 3.7 AuthCard (`.auth-page`, `.auth-card`)

- **Page**: a 3-row grid (top, card, foot), `min-height: 100%`, padding 32 40 28, `--sidebar` with the glow of 2.1.
  Top: the brand (`.ui-brand`, links to `/login`). Foot: Terms, Privacy, Contact (`billing-policy-links`, centred).
- **Card**: `width: min(440px, 100%)`, centred in its row, margin 36 0, padding 38 38 32, the sheet of 2.1.
- **Form layout**: `.auth-title` serif 30/1.12 500, −0.02em, balanced; `.auth-lede` 16/1.5 `--text-3`, margin-top 8,
  bold parts `--text` 650; `.auth-form` a column, gap 18, margin-top 26, inputs 46 px at 16 px (password toggle 5 px
  from the edge); `.auth-submit` 46 px, radius `--radius`, 16 px; `.auth-links` centred, gap 6 20, margin-top 22, 15
  px, links `--accent-ink` 600 (a quiet one `--text-2`), "Back to sign in" with a 15 px arrow.
- **Result layout** (`.auth-result`, centred, when `mark` is given): the mark (64 px circle, margin 6 auto 22, icon 28
  px at stroke 2; tones in 2.1; a spinner for "busy"), the title at 28 px, `.auth-text` 16/1.5 `--text-2`, max 40ch,
  `text-wrap: pretty`; `.auth-actions` a column, gap 10, margin-top 26; a notice 22 px below the text.
- **Phones** (under 800 px): the page is `--bg` with no glow, padding 20 (safe areas); the card loses its frame
  (no border, shadow or padding) and sits under the brand with 30 px above; title 27 px (results 26).
- **Focus**: every title has `tabindex="-1"` and takes focus on each state change; no ring on it.

### 3.8 Where messages go (all auth pages)

| Message | Look | Place |
|---|---|---|
| A field's problem (an invalid or missing address, `wrong_password`, a mismatch, an unticked box) | `error-text` (13.5/600 `--danger`, 13 px alert icon) | under the field |
| A password refusal | the rule row (3.4) | in place of the rule |
| A form-level refusal (`too_many_attempts`, `mail_unavailable`, `challenge_unavailable`, `signup_closed`, `email_taken`) | the `login-error` block (`--danger-soft`, 14/600 `--danger`, alert icon, radius 10); on sign-up, a small Google button under it for the first three | above the submit button |
| A neutral fact or a result from elsewhere (`?google=`, sign-up closed) | `ui-notice` in a tone | under the lede |
| `challenge_failed` | nothing extra: the widget resets and the form waits for a new token | – |

### 3.9 Email typo suggestion (`.auth-suggest`, inside `EmailField`)

On blur, when the brief's typo rule fires: 7 px under the input, 14/1.45 `--text-2`: "Did you mean " then the
suggested address as a text button (`--accent-ink` 600, underlined on hover, `overflow-wrap: anywhere`) and "?". A tap
puts the address in the field, removes the line and keeps focus in the field. It sits above any `error-text` and never
turns red.

### 3.10 In-app browser notice (`.auth-inapp`)

In place of the Google button (same slot, same margins): a neutral `ui-notice` with `Info`: "Google sign-in doesn't
work inside this app. Open asknuvexusdoc.com in Chrome or Safari." and a `btn-sm` with `Copy` 15 **Copy link**, which
becomes "✓ Link copied" (`role="status"`) for 3 s. The "or" rule stays, so the email form reads as the way on.

---

## 4. Sign-in (`/login`) and the public front door

Screenshots: `login-*` (the line under the lede and the foot are as 0.4 says, not as shown).

### 4.1 What stays

The whole aside (brand, picture, deck, tour, pitch, closing line), the grid, the sticky form column, the phone order,
the card's width (392 px), title, lede, field heights, the credit line and the policy links. `login.css` gains rules;
none of the existing ones change.

### 4.2 The card, top to bottom

1. "Welcome back" and the lede, as today.
2. **While sign-up is open**: `.login-new`, 10 px under the lede: "New to AskNuvexusDoc? **Create an account**" (15/1.5
   `--text-2`; the link `--accent-ink` 600, a plain `<a href="/signup">`).
3. A `?google=` notice when present (4.3), 24 px under the lede (or the line).
4. **Continue with Google** (`.login-alt`, 28 px under the lede or the line, 18 px under a notice; 22 on phones), then
   the "or" rule. Hidden with the rule when `config.google_signin` is false; the notice of 3.10 in an in-app browser.
5. The form (gap 18): **Email or username** (`autocomplete="username"`, `inputmode="email"`), **Password** with
   "Forgot password?" at the right of its label row (`.login-label-row`: flex, baseline, space-between; the link 14/600
   `--accent-ink`; shown when `config.password_reset`), the error or the challenge (4.3), **Sign in** (enabled; an
   empty field gets "Enter your email or username." or "Enter your password." under it).
6. **While sign-up is closed only**: the foot block (28 px above, as today) with today's note and shield, unchanged.
7. The credit line and the policy links, as today.

The `PasswordInput` keeps its look; the page renders the label row itself (`label` prop omitted, the input labelled by
its `id`).

### 4.3 States

| State | Look |
|---|---|
| Wrong details (401) | the `login-error` block above **Sign in**: "Incorrect email, username or password." then, in the same block (`.login-hints`, a list, gap 2, 13.5/400 `--text-2`), "Just signed up? Open the link we emailed you, or **send it again**." and "Signed up with Google? Use **Continue with Google**." (both links `--danger` 600 underlined, inside the block) |
| `challenge_required` | the caption and the widget above **Sign in** (3.3); the values kept; pressing **Sign in** before the token shows the waiting line |
| Busy | **Sign in** with its spinner (today's); Google busy (3.1) |
| `?google=denied` | neutral notice, `Info`: "Google sign-in was cancelled." |
| `expired` | warning notice, `Clock`: "That took too long. Try again." |
| `error` | danger notice, `AlertCircle`: "We couldn't sign you in with Google. Try again." |
| `link_required` | info notice, `Info`: "**An account already uses this email.** Sign in with your password, then connect Google in Settings → Account." |
| `use_password` | info notice, `Info`: "Sign in with your email or username, then connect Google in Settings → Account." |
| `signup_closed` | neutral notice, `Info`: "New sign-ups are closed right now." |
| `unavailable` | danger notice, `AlertCircle`: "We couldn't sign you in. If you think this is a mistake, contact us." ("contact us" links to the contact email when `config.contact_email`) |
| Sign-up closed, no Google | today's page plus "Forgot password?" (when reset is available) and the new label (`login-closed-*`) |

`?google=` notices are `role="alert"`; the page removes the parameter after showing them.

### 4.4 Sizes

Measured on the revision 1 mockup (with the foot button; 0.4 replaces it with the 32 px line under the lede, so the
built card is about 50 px shorter while sign-up is open):

| Viewport | Card | Sign in (bottom) | Column |
|---|---|---|---|
| 1440×900 | 613 px (today 522), top at 143 | 584 | fits |
| 1100×800 | 613 | 534 | fits |
| 1366×657 | 613 | 473 | scrolls in its column (as today's rules allow) |
| 390×844 | (phone) | 719 (today 622) | first screen |
| 360×740 | (phone) | 725 | first screen |

With the challenge open the card is 727 px; at 390×844 Sign in ends at 832. The integrator re-measures the built page:
Sign in on the first screen at 390×844 and 360×740, and **"Create an account" and Continue with Google inside a
390×664 viewport**.

### 4.5 The public top bar and pricing

- `PublicLayout`'s top bar, signed out and sign-up open: **Sign in** (today's `btn-sm`) then **Sign up** (`btn-sm
  btn-primary`, a plain `<a href="/signup">`), gap 8. Phones: both stay, the nav wraps as today.
- `/pricing`'s foot, sign-up open: the primary button reads **Create an account to choose a plan**
  (`<a href="/signup?next=/plans">`), and under it, 10 px, "Already have an account? **Sign in**" (15 `--text-2`, the
  link `--accent-ink` 600). Closed: today's "Sign in to choose a plan".

---

## 5. Sign-up (`/signup`)

Screenshots: `signup-*` (the line under the lede and the foot are as 0.4 says). The split layout of 4 (`AuthSplit`),
the card's class `auth-signup`.

### 5.1 The card

1. "Create your account" (the sign-in title's style) and the lede "Chat with your documents. Choose a plan after you
   sign up." (`text-wrap: balance`: two even lines in the 392 px card).
2. `.login-new`'s look, 10 px under the lede: "Already have an account? **Sign in**" (a plain `<a href="/login">`).
3. **Continue with Google** and "or sign up with email" (hidden when `signup.google` is false; with only Google, the
   rule and the form are hidden).
4. The form (gap 18): **Email** (`EmailField`, `autocomplete="email"`), **Password** (`autocomplete="new-password"`)
   with `PasswordRules`, the terms tick box, the Turnstile slot, **Create account** (enabled; the refusals of 3.8 above
   it).
5. The credit line and the policy links (no foot block).

Desktop (revision 1 mockup, with its foot block): 810 px tall at 1440×900 (fits; Create account ends at 683). At
1100×800 and below it scrolls in its column. Phones: Google ends near 460 of 844, **Create account** at 916 (one short
scroll).

### 5.2 Check your inbox (same URL, same card)

`.auth-signup.auth-signup-short` (V14, `AuthSplit compactOnPhone`):

1. The mark (`.auth-sent-mark`, 56 px, `MailCheck` 26, 22 px below, left edge aligned with the text through a 7 px
   indent for its halo).
2. "Check your inbox" (the card title), "We sent an email to **<address>**." (16/1.55 `--text-2`).
3. The steps (`.auth-steps`, an `ol`): padding 16 18, 1 px `--border`, radius `--radius-lg`, `--surface`; each step
   14.5/1.45 `--text-2` behind a 22 px `--accent-soft` disc with its number (12.5/700 `--accent-ink`); bold parts
   `--text`. Copy: "Open the link in it within **24 hours**." / "In this browser that's all it takes. On another
   device, enter the **password you just chose**." / "Choose a plan to start chatting with your documents."
4. `.auth-resend` (a top hairline, 20 px above): "**Didn't get it?** Check your spam folder too." on the left and the
   button on the right (`btn-sm`, `Refresh` 15): "Resend in 0:42" (disabled) → "Resend email" → busy "Sending…" with
   the widget under the row → "✓ Sent again" (14/600 `--success`, `role="status"`). After the third mail the row is
   replaced by a neutral notice: "We've sent 3 emails. Check Spam and Gmail's Promotions and Updates tabs, use Continue
   with Google, or try again in an hour." with the small Google button.
5. "Wrong address? **Start again**" (14.5 `--text-3`, the link `--accent-ink` 600): back to the form, the email kept,
   the password cleared; with the same address, **Create account** reads "Create account in 0:42" until it may send.

**`?resend=1`** (from the sign-in hint): the same card with the address carried over and **Resend email** ready at
once; without an address, the title "Send the link again", an **Email** field, the widget and a primary **Send the
link again**.

### 5.3 Other states

- **Closed** (`signup.open` false): the title and lede, a neutral notice "**New sign-ups are closed right now.** If
  you already have an account, sign in." and a full-width primary **Sign in** (`signup-closed-*`; the picture hidden
  on phones).
- **Refusals**: address errors under Email; `password_rejected` in the rules; the rest above the button (3.8).

---

## 6. Task pages (AuthCard)

Screenshots: `verify-*`, `google-signup-*`, `forgot-*`, `reset-*`, `confirm-email-*`, `states-*`.

| Page and state | Layout | Mark | Title | Body | Actions |
|---|---|---|---|---|---|
| any token page, checking | result | busy | "Checking your link…" | "This takes a moment." | – |
| any, no token | result | warning `LinkBroken` | "This link is incomplete" | "Open it from the email again, or copy the whole link into the address bar." | link "Go to sign in" |
| `/verify-email`, this browser | form | – | "Finish creating your account" | lede "Your email is confirmed. Create your account to continue."; identity row (address, "Confirmed by this link") | **Create account** |
| … another browser | form | – | same | lede "One last step: enter the password you chose when you signed up."; identity row; a visually hidden read-only email input (`autocomplete="username"`) so password managers offer the saved entry; **The password you chose** | **Create account**; link "Forgot it? Sign up again" |
| … `wrong_password` | form | – | same | `error-text` under the field: "That isn't the password you chose when you signed up." | same |
| … `signup_reset` | result | warning `Key` | "Too many wrong passwords" | "For your safety this link stopped working. Sign up again to choose a new password." | **Sign up again**; link "Sign in" |
| … `link_invalid` | result | warning `Clock` | "This link has expired" | "Links work once, for 24 hours. Sign up again to get a new one, or sign in if your account is already set up." | **Sign up again**; link "Sign in" |
| … `email_taken` | result | neutral `User` | "You already have an account" | "This address already has an account. Sign in instead." | **Sign in**; link "Forgot your password?" |
| … signed in elsewhere | form | – | – | a neutral notice above the field: "Finishing signs you out of <display name>." | – |
| `/signup/google` form | form | – | "Create your AskNuvexusDoc account" | lede "You're signing up with Google as:"; identity row ("G", the address, "Google account"); when not `email_verified` an info notice with `Mail`: "We'll email that address so you can confirm it. Until then, receipts and password resets can't use it."; the terms tick box | **Create account**; links "Use a different Google account", "Cancel" (quiet) |
| … from the sign-in page | form | – | "No AskNuvexusDoc account uses this address yet" | the same lede, row, notice and tick box | **Create account** (primary) and **I already have an account** (secondary, full width, 10 px under it); link "Use a different Google account" |
| … expired | result | warning `Clock` | "That took too long" | "For your safety, the Google sign-up waits 15 minutes at most." | **Continue with Google** (the Google button); "Back to sign in" |
| `/forgot-password` | form | – | "Reset your password" | lede "Enter the email address on your account and we'll send you a link to choose a new password."; **Email** (`EmailField`) with the hint "No email on your account? Ask the administrator."; the widget | **Send reset link**; "Back to sign in" |
| … sent | result | accent `MailCheck` | "Check your email" | "If an account uses **<address>**, we've emailed it a link to reset the password. The link works for 30 minutes." and a neutral notice "Nothing arrived? Check your spam folder, or make sure this is the address on your account." | "Back to sign in" |
| … unavailable | result | neutral `Info` | "Password reset isn't available" | "Password reset isn't available right now. Ask the administrator." | "Back to sign in" |
| `/reset-password` | form | – | "Choose a new password" (or "Set your password" without a password) | lede "Saving it signs you out on every device."; identity row (avatar, display name, username); **New password** with `PasswordRules`; **Repeat it** (mismatch: `error-text` "The passwords don't match."); with Google, the tick box "Also disconnect Google sign-in (**a***@gmail.com**)" with the hint "Do this if someone else may have connected it." (ticked when the brief says) | **Save password** |
| … done | result | success `CheckCircle` | "Password changed" / "Your password is set" | "Your password was changed and you were signed out everywhere. Sign in with the new one." / "Sign in with **<address>** and your new password, or keep using Google." | **Sign in** (a full load) |
| … `link_invalid` | result | warning `Clock` | "This link has expired" | "Reset links work once, for 30 minutes. Ask for a new one." | **Ask for a new link**; "Back to sign in" |
| `/confirm-email`, signed in as the account | result | accent `AtSign` | "Confirm your email" | "Use **<address>** as the email of your account?" | **Confirm this email**; quiet link "Not now" |
| … not signed in | result | accent `AtSign` | "Sign in to confirm" | "Sign in to the account <account> to confirm **<address>**." | **Sign in** |
| … signed in to another account | result | neutral `User` | "You're signed in to another account" | "Sign out, then sign in to <account> to confirm this email." | **Sign out**; quiet link "Not now" |
| … done | result | success `CheckCircle` | "Your email is confirmed" | "**<address>** is now the email of your account." | **Continue** |
| … `link_invalid` / `email_taken` | result | warning `Clock` / neutral `User` | "This link has expired" / "That address is taken" | "Ask for a new link in Settings → Account." / "Another account already uses this address." | **Continue** |
| `/undo-email` | result | warning `Alert` | "Undo the email change?" | "Put **<old address>** back as the email of your account? This signs out every session and removes the password; we'll email you a link to choose a new one." and, under it, 13.5 `--text-3`: "The new address was <masked>." | **Undo the change**; quiet link "Not now" |
| … done | result | success `CheckCircle` | "Your email is back" | "We signed out every session and removed the password. Check **<old address>** for a link to choose a new one." | **Go to sign in** |
| … `link_invalid` / `email_taken` | result | warning `Clock` / neutral `User` | "This link has expired" / "That address is taken" | "Undo links work once, for 7 days. Contact us if you still need help." / "That address is now used by another account. Contact us." (the contact link) | "Go to sign in" |

---

## 7. Settings → Account

Screenshots: `settings-account-*` (the profile and the step-up as 0.4 says). Everything outside the panel body is
unchanged.

### 7.1 Profile

- The name is the **display name**: the email when there is one, else the username (`overflow-wrap: anywhere`).
- The meta line's text becomes the source: "You signed up with email", "You signed up with Google", "Your account was
  created by the administrator" (the "Member" badge stays).
- The facts: **Username** (the handle, and 13 px `--text-3` "Also works for sign-in"); **Email** (a success badge with
  a 13 px check "Confirmed" and "since 30 Oct 2026" in `--text-3`; no address: "No email" in `--text-3` 400);
  **Last sign-in** adds "with Google" or "with password" before the address. The address itself appears only as the
  name.
- The avatar menu and the Settings dialog's header show the same display name.

### 7.2 Email

A `settings-row` group: **Email**, the description "Sign in with it, reset your password, and get receipts and plan
reminders." (no address: "Add an email to sign in with it, reset your password and get receipts and plan
reminders."), and `btn-sm` with `Mail` 15: **Change email…** / **Add email…** (`aria-expanded`).

- **The form** (`settings-inline-form`, under the row): **Email address** (`EmailField`, hint "We'll email a link to
  this address. It becomes your email when you open it while signed in."), **Your password** (when
  `email_change_needs` is `password`), then a primary sm **Send confirmation link** and a ghost sm **Cancel**.
- **Step-up first** (`email_change_needs` is `google` and no live grant): instead of the form, a `settings-confirm`
  block (`.settings-reauth`): "For your safety, confirm it's you with Google before changing your email." and the
  small Google button **Continue with Google**. Back from Google (`?google=reauth_ok`), the form opens without a
  password field, with "Confirmed with Google · 5 minutes" in 13 px `--text-3` under its title.
- **Pending** (`.settings-pending`, an info `ui-notice` with `Clock`, 14 px under the row): "Waiting for you to
  confirm **<address>**" over a 13.5 `--text-2` line "We emailed a link 3 minutes ago. Open it while signed in here;
  it works for 24 hours. Until then your email stays <current>." (no current address: "…it works for 24 hours."), and
  at its end `btn-xs` **Resend** and ghost xs **Cancel**.
- The env admin: the group is replaced by a neutral notice "The admin account is managed in .env." (as the Password
  group says today).

### 7.3 Sign-in methods

A group: **Sign-in methods**, "Keep at least one way in that you can use.", then a list (`.settings-methods`: the
sign-ins list frame, 1 px `--border`, radius `--radius-lg`). Each row (`.settings-method-row`: padding 12 16, gap 14):
a 36 px `--surface-2` tile (radius 10; `Key` in `--text-2`, or the "G"), the name 15/600, a 13.5 `--text-3` line,
and the action at the end. Extra content for a row (`.settings-method-more`) sits under its text (padding 0 16 14 66).

| Row | Line | Action | Under the row |
|---|---|---|---|
| Password, set | "Changing it signs you out everywhere else." | `btn-sm` `Key` **Change password…** (today's form opens under the row) | the form; today's success notice |
| Password, none | "Not set. Add one to sign in without Google." | `btn-sm` **Set a password** | first the step-up block of 7.2 ("For your safety, confirm it's you with Google before adding a password."); then **New password** with `PasswordRules`, **Repeat it**, primary sm **Save password** and ghost sm **Cancel**; on success "Password added. You can now sign in with your email and this password." (success notice) |
| Google, connected | "Connected as <address> since 30 Oct 2026" | `btn-sm` **Disconnect…** (a password confirm opens under the row with a danger sm **Disconnect Google**) | disabled with the reason as text: "Set a password first, so you can still sign in without Google." (or "Confirm an email first…") |
| Google, not connected | "Not connected. Sign in with your Google account too." | `btn-sm` **Connect Google…** | a `settings-confirm`: "Enter your password, then choose your Google account.", **Your password**, the small Google button **Continue with Google** and ghost **Cancel** |

`?google=` results show as a notice under the list: `linked` success "Google is connected.", `in_use` danger "That
Google account is already connected to another AskNuvexusDoc account.", `already_linked` neutral, `denied` neutral,
`error` danger, `expired` warning, `reauth_mismatch` danger "That's a different Google account from the one connected
here." (the brief's sentences). `reauth_ok` shows no notice: the form that asked for it opens.

### 7.4 Sign-ins, sessions, narrow panels

- Sign-in rows: the second line reads "with Google · <address>" or "with password · <address>".
- Sessions: unchanged.
- Panel under 520 px (`@container settings-panel`): method rows wrap, the action under the text indented 50 px; the
  extra content loses its indent; the pending notice's actions go under its text (25 px in).

---

## 8. Settings → Plan & billing, and the plan page

Screenshot: `settings-billing-mail-*`.

- A new **EMAILS** section (`settings-sub`) above Receipt details: "Receipts and plan reminders go to **<address>**.
  **Change it in Account**" (the link switches the tab), then `.settings-mail-switches` (padding 16 18, 1 px
  `--border`, radius `--radius-lg`, gap 14) with two `Switch`es: **Email me a receipt for each payment** ("The PDF
  receipt is attached; it is also listed below.") and **Email me before my plan ends** ("5 days before, the day
  before, and when it ends.").
- No verified email: the switches are replaced by a neutral notice "Add an email in **Settings → Account** to get
  receipts and reminders by email." with `btn-sm` **Add email**. Mailer not configured: no section.
- **Receipt details** keeps only **Name on receipts**; the "Plan reminders: in the app." line goes.
- The return page's one-line ask (no verified email): a neutral notice with the same sentence and link.
- `/plans` and checkout without a verified email: an info `ui-notice` with `Mail` above the plan cards (the checkout's
  summary column on checkout): "**Add your email before you pay.** Your receipt goes there." with `btn-sm` **Add
  email** (opens Settings → Account). The buy buttons stay; pressing one shows the same notice's sentence if it was
  scrolled away.
- While payments aren't live, a non-tester's unavailable option shows the plans brief's unavailable look with "Paid
  plans open soon. Check back in a few days."

---

## 9. Admin → Users and Admin → Billing

Screenshots: `admin-users-*`, `admin-signup-*`, `admin-user-*` (tiles, checks and forms as 0.4 says).

### 9.1 The Sign-up card (`.admin-signup`, above the Users card, 20 px gap)

- Header: "Sign-up" (the card title) with the status line beside it (`ui-status-connected` "Open", or `ui-status-off`
  "Closed"; gap 14), the subtitle "Who can create an account, and how. New accounts start without a plan.", and
  `btn-sm` `Settings` 15 **Edit…**.
- Facts (`.admin-signup-facts`, 4 columns, gap 12, padding 8 20 18; 2 columns under 940 px): tiles on `--bg` (padding
  12 14, 1 px `--border`, radius `--radius`), term 13/600 `--text-3`, value 15/650 `--text`, an optional 13 px
  `--text-3` line:
  - **Methods**: "Email and Google", "Email only", "Google only", "None".
  - **Sign-ups**: "3 today", line "14 in 7 days · 2 paid" (the line links to the list filtered to "Last 7 days").
  - **Email today**: "12 of 80", line "About 20 sign-up emails left · resets 05:30 IST" (the reset time is the next UTC
    midnight in IST, computed in the client); when `public_paused`, the value gets an `Alert` 15 `--warning` and the
    line "Sign-up emails paused: too many bounces".
  - **Checks**: `CheckCircle` 15 `--success` or `Alert` 15 `--warning` before Email, Bounce reports, Turnstile, Google.
- Warnings (`real_ip`, `payments_not_live`, `mail_webhook_missing`, `admin_password_weak`, the policy drafts, and
  sign-up open without a mailer or Turnstile): warning notices in the footnote area (padding 0 20 18), each with the
  brief's sentence; the mail check links to Admin → System.

### 9.2 The Sign-up dialog (`SignupSettingsModal`, 720 px, the user form's look)

1. **AVAILABILITY**: the switch **Open sign-up** ("Anyone can create an account with the methods below. New accounts
   have no plan until they pay. Closing it never affects existing accounts."). With warnings present, turning it on
   leaves it off and shows a warning notice under it: "**2 things aren't ready.**", a bullet list of the warnings, and
   `btn-sm` **Open anyway** (which turns it on).
2. **METHODS**: switches **Email and password** ("People confirm their address from an email before the account
   exists.") and **Continue with Google**; one whose prerequisites are missing is disabled, its description "Not
   configured: set GOOGLE_SIGNIN_CLIENT_ID and GOOGLE_SIGNIN_CLIENT_SECRET in .env, then restart the backend."
3. **CHECKS**: the going-live checklist rows (`admin-checklist-rows`; here padding 9 0 and hairlines between):
   `CheckCircle` 20 done, the dashed circle for "not set up", `Alert` 20 `--warning` for a warning, the name 14.5
   `--text` over a 13 px `--text-3` line. Rows: **Email** ("Configured · 12 of 80 sent today · none queued · no
   errors", or the last error and when) with `btn-sm` **Send a test email…** (opens an address field and **Send** in
   the row); **Bounce reports** ("Resend webhook set · 0 bounces today", or "Not set up: add the webhook and
   MAIL_WEBHOOK_SECRET"); **Turnstile**; **Google sign-in**; **Visitor addresses**; **Online payments** ("Live" or "Test
   keys: new accounts can't pay yet"); **Privacy policy and terms**; **Admin password** ("Strong enough" or "Short or
   common: change ADMIN_PASSWORD").
4. Footer: **Cancel**, primary **Save**.

### 9.3 The list

- Toolbar: first a search input (34 px, 14 px, min 220, `Search` 15 inside at the left, placeholder "Search name or
  email", `type="search"`), then the plan chips as today, then a `select` (34 px, 14 px, min 160) **All sign-ups /
  Last 7 days / Email / Google / Admin**. Phones: the search takes the full row; chips and select wrap under it.
- The user cell (`.admin-user-ident`): the name line as today (name, badges), then `.admin-user-email` (13 px
  `--text-3`, max 30ch, ellipsis): `CheckCircle` 13 `--success` and the address (`sr-only` "Confirmed:"),
  `Clock` 13 `--warning` and "Pending: <address>", "No email", or "Managed in .env" for the admin.
- **Signed up** (after User): the source 600 `--text` ("Env", "Admin", "Email", or the 14 px "G" and "Google") over
  the date (`admin-subtext`). The header is a sort button (`ArrowDown` 13; newest first by default).
- No matches: the table's empty row "No users match." with a ghost sm **Clear filters**.

### 9.4 The user forms

- **New user**: as today. A refused password shows the server's sentence as `error-text` under the field;
  `username_retired` under the username.
- **Edit user**, ACCOUNT: first a read-only facts box (`.admin-signin-facts`: `--bg`, 1 px `--border`, radius
  `--radius`, padding 14 16, a two-column `dl` at 14 px): **Signed up** ("With email on 30 Oct 2026" over "Accepted the
  terms of 2026-11-01 on 30 Oct 2026, 14:02"), **Email** (the address with its "Confirmed" badge, "Pending: <address>,
  sent 3 minutes ago", or "No email"), **Google** ("G", "Connected as …", or "Not connected"), **Password** ("Set" /
  "Not set"). Then **Set new password** and Active, as today. Phones: the facts stack (term over value).

### 9.5 Admin → Billing

Wherever a buyer appears (payments, orders, attention items, the expiring and lapsed lists), the username keeps its
place and the email is a second line (13 px `--text-3`, ellipsis at 30ch, the full address in `title`); a deleted
user's row stays as today.

---

## 10. Emails (`services/mail/templates.py`, MAIL)

Screenshot: `email-verify-*` (its copy is revision 1's; the text below wins). One frame for every kind; inline styles
only, tables for layout, no images, fonts or remote resources; `color-scheme: light`.

| Part | Value |
|---|---|
| Background | `#f3f0e8` (the desk), 32 px 16 px around |
| Brand row | a 28 px `#a94d2b` square (radius 8) with a white italic Georgia "N" 18 px, then "AskNuvexusDoc" Georgia bold 18 `#2a2520`, 18 px above the card |
| Card | max 560 px, `#fffdf9`, 1 px `#e5dfd3`, radius 14, padding 34 34 30 |
| Title | Georgia 26/1.2 normal `#2a2520` (the subject's short form: "Confirm your email", "Reset your password", "Your password was changed") |
| Body | system sans (`-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial`) 16/1.55 `#2a2520`, 14 px between paragraphs |
| Button | at most one per mail: `#a94d2b`, radius 10, padding 13 22, white bold 16. Labels: **Finish sign-up** (verification and "finish creating"), **Sign in** (already registered), **Choose a new password** / **Set a password** (reset), **Confirm this email**, **This wasn't me: undo it** (email changed). Other notices have none |
| Fallback | "Or paste this link into your browser:" 14 `#5a5249`, then the link in mono 13 `#9a4423`, `word-break: break-all` |
| "If it wasn't you" | under a hairline, 14 `#5a5249` |
| Footer | 13/1.55 `#6e655a`: "AskNuvexusDoc · <contact> · asknuvexusdoc.com", the line saying why the mail came, and "Questions? Reply to this email." when a Reply-To is set |
| Preheader | a hidden first line (the second sentence), so inboxes show it as the preview |

The plain-text part keeps the functional brief's 6.4 sentences and puts each link on its own line.

---

## 11. Dark mode and accents

- Everything is tokens, checked in both themes and spot-checked in Sage (`login-1440-light-sage`), Ocean
  (`signup-1440-dark-ocean`) and Plum (`settings-account-1440-dark-plum`).
- The Google button switches to Google's dark theme; the "G" stays in colour. The Turnstile widget follows the app's
  theme (B3).
- Primary buttons, the tick box and switches carry `--accent-text` (dark ink in dark mode), as everywhere; marks use
  the soft tones with their light icons.
- The brand mark (page top, emails) stays clay whatever the accent.
- Emails have no dark mode (some clients invert them; the frame survives inversion).

---

## 12. Phones and touch

- Breakpoints stay 800 (and 940 for the Sign-up card's tiles). Checked at 390 and 1440: no page scrolls sideways;
  the admin table scrolls in its box with the sticky actions column, as today.
- Front door: V14; the "Create an account" line and Google inside 390×664; inputs 46 px at 16 px (no iOS zoom); Sign
  in on the first screen at 390×844 and 360×740.
- AuthCard: the card is the page (3.7).
- Settings: method rows and the pending notice wrap (7.4).
- Touch targets: every button 32 px or more inside 44 px rows; the tick box's label is the hit area; "Forgot
  password?", the switch lines' links and the typo suggestion get 8 px of padding on coarse pointers
  (`@media (pointer: coarse)`: `padding: 8px 0; margin: -8px 0`).

---

## 13. Motion

- The result marks scale in from 0.92 with a fade over `--dur-3 --ease-out` (`auth-mark-in`), not under reduced
  motion; spinners stop under reduced motion and stay visible.
- The sign-in aside's entrance is unchanged. Switching between the sign-up form and "Check your inbox" is instant; the
  new heading takes focus.
- Nothing else moves.

---

## 14. Accessibility

- **Headings**: one `h1` per page, focused on each state change (`tabindex="-1"`, no ring).
- **Buttons stay enabled**; on submit, each missing or invalid field gets its `error-text` and focus moves to the first.
- **Google button**: a `button` named by its visible label; the "G" is `aria-hidden`; busy by `aria-busy`.
- **Fields**: visible labels; hints, rules, suggestions and errors linked by `aria-describedby`; `aria-invalid` on
  refusals; errors `role="alert"`; the identity row is text, not a field.
- **Turnstile**: the widget is Cloudflare's (keyboard and screen-reader support are its own); the waiting line is
  `aria-live="polite"`; the couldn't-load notice is `role="alert"` with a real **Try again** button.
- **The terms**: one label for the tick box and its text; the links say they open a new tab.
- **Countdown**: the button's text changes once a second but isn't a live region; "Sent again" and "Link copied" are
  `role="status"`.
- **Status by words**: "Confirmed", "Pending:", "Not set", "Open"/"Closed", the checks' `sr-only` prefixes ("Done:",
  "Not set up:", "Warning:"); colour repeats the words.
- **Forced colours**: the Google button keeps a 1 px `ButtonText` border; marks keep their icons; the rule rows'
  icons stay (`forced-color-adjust: auto`).

---

## 15. Contrast

`python3 -I audit.py` (in the mockup folder) reads the token blocks of `global.css` and checks the pairs this design
adds, in both themes and all five accents: **200 pairs, 0 under their minimum** (4.5:1 for text, 3:1 for icons and
control boundaries). Revision 2 adds no new pair: the switch lines, the suggestion and the waiting line reuse
`--text-2` and `--accent-ink` on `--bg`, already audited. Lowest values:

| Pair | Lowest |
|---|---|
| `--success` on `--success-soft` ("Confirmed" badge) | 4.98 (light) |
| `--text-3` on `--surface` (identity and admin sub-lines) | 5.32 (dark) |
| `--text-3` on `--bg` (rules, the "or" word, facts box) | 5.39 (light) |
| `--accent-ink` on `--accent-soft` (step numbers, sent mark) | 5.45 (light Clay) |
| `--success` on `--bg` (rule met icon) | 5.55 |
| `--danger` on `--bg` (refused rule) | 6.11 |
| `--accent-ink` on `--bg` (links, "Forgot password?", caption icon, suggestion) | 6.13 (light Clay) |
| `--text-2` on `--danger-soft` (sign-in hints) | 6.16 |
| `--text-2` on `--info-soft` (pending line) | 6.40 |
| Google text on its fill | 16.48 light, 14.47 dark |
| Google stroke on the page (control boundary) | 4.27 light, 4.86 dark |

The hint links inside the 401 block are `--danger` on `--danger-soft`, the block's own audited pair.

---

## 16. Class map and owners

Owners per the functional brief's 16.2.

| Mockup classes | App file | Owner |
|---|---|---|
| `.auth-google*`, `.auth-or`, `.auth-turnstile*`, `.auth-rules`, `.auth-rule*`, `.auth-terms`, `.auth-identity*`, `.auth-suggest`, `.auth-inapp`, `.auth-page*`, `.auth-card`, `.auth-title`, `.auth-lede`, `.auth-form`, `.auth-submit`, `.auth-links`, `.auth-link`, `.auth-quiet`, `.auth-result`, `.auth-mark*`, `.auth-text`, `.auth-actions` | `src/components/auth/auth-components.css` | APP (stage 1) |
| `.login-alt`, `.login-new`, `.login-label-row`, `.login-forgot`, `.login-error-body`, `.login-hints`, `.login-card > .ui-notice` | `src/pages/login.css` | APP (stage 2) |
| `.auth-signup*`, `.auth-sent-*`, `.auth-steps`, `.auth-resend*`, `.auth-countdown`, `.auth-done-line` | `src/pages/auth.css` | APP (stage 2) |
| `.settings-email-*`, `.settings-pending*`, `.settings-methods`, `.settings-method*`, `.settings-reauth`, `.settings-mail-switches` | `src/components/settings/settings.css` | APP (stage 2) |
| the top bar's Sign up, the pricing foot's line | `src/pages/public/public.css` | APP (stage 2) |
| `.plans-email-notice` | `src/pages/plans.css` | APP (stage 2) |
| `.admin-signup*`, `.admin-user-ident`, `.admin-user-line`, `.admin-user-email*`, `.admin-email-pending`, `.admin-source*`, `.admin-check-warn`, `.admin-open-warning`, `.admin-signin-facts`, `.admin-user-search`, `.admin-buyer-email` | `src/components/admin/admin.css` | ADMIN |
| icons of 2.2, `google-g.svg` | `src/components/ui/icons.tsx`, `src/components/auth/google-g.svg` | APP (stage 1) |
| the email frame | `backend/app/services/mail/templates.py` | MAIL |
| `.mock-*`, `mock.css`, `mock-tall`; revision 1's `.login-foot-switch`, `.admin-domains`, `.admin-invite-note` | mockup only | – |

Each file holds one prefix; auth components never restyle `login-`, `settings-` or `admin-` classes, and the login
additions never change an existing `login-` rule.

---

## 17. Builder checklist

APP (stage 1): the icons and the "G"; `GoogleButton` (both sizes, both themes, busy, the in-app notice), the "or"
rule, `Turnstile` (slot, loading, waiting line, couldn't-load, the app's theme), `PasswordRules` (live length, the
refusal in place), `TermsCheck`, `IdentityRow`, `EmailField` (the suggestion) and `AuthCard` (both layouts, phones);
`AuthSplit` taken out of `LoginPage.tsx` with no visual change (compare with today's `login` shots at 1440×900,
1366×657, 1100×800 and 390×844).

APP (stage 2):

1. Build 4 to 8 against the screenshots at 1440 and 390, light and dark, with 0.4's changes.
2. Check visually:
   - the aside, picture, copy and tour are unchanged on `/login` and `/signup`;
   - "Create an account" and Google fit in 390×664; Sign in ends on the first screen at 390×844 and 360×740;
   - one primary button per card; the Google button is Google's in both themes and always says "Continue with
     Google";
   - nothing moves when the Turnstile widget arrives; no button is disabled while waiting for it;
   - a refused password replaces its rule row; editing brings the rule back;
   - the sent state on a phone shows the steps and Resend without the picture;
   - Settings: the address appears once (the profile's name), the pending one only in its notice; the step-up block
     comes before the form that needs it.
3. No hex colours in the new CSS except the Google button's scoped properties; no `prefers-color-scheme`.

ADMIN (stage 2): 9 against `admin-*` with 0.4's changes; the Sign-up card's tiles wrap to two columns under 940 px;
the search box and the select on a phone.

MAIL (stage 2): the frame of 10 for every kind; check it in Gmail (web and Android) and Apple Mail, light and with
forced dark.

The integrator: recheck the pairs of 15 on the built CSS; compare the login page with its current screenshots; the
390×664 check.

---

## 18. Suggested changes to the functional brief: outcome

Revision 1's ten suggestions, as settled in the functional brief's revision 2:

1. **13.1** Google first: adopted. The small "Create an account" button: replaced by the line under the lede (V5).
   `?google=` notices in the tones of 4.3: adopted.
2. **7.3** `theme` = the app's resolved theme, re-rendered on change: adopted.
3. **13 and 16.3** `PasswordRules({ id, password, minimum, rejected })`: adopted; `AuthCard` with `lede` and `mark`:
   adopted; `GoogleButton` with `size`: adopted, without the "Sign in again with Google" label.
4. **4.3 and 13.2** Resend on demand with the countdown: adopted; the three-step copy: adopted with step 2 reworded;
   no picture on phones for the sent and closed states: adopted.
5. **13.3** the `/verify-email` lede, identity row and "Forgot it? Sign up again": adopted, the last only where a
   password is asked.
6. **13.3 to 13.6** the copy of B15: adopted.
7. **13.7** the address once: adopted as the profile's name.
8. **13.9** the email line, the "Signed up" column and the select: adopted, with a search box; "Open anyway" in a
   notice: adopted; the tiles: adopted with Sign-ups in place of Email domains.
9. **6.4 and S24** the HTML frame, one button, the link written out: adopted.
10. **13 (front matter)**: the functional brief now points to this file.
