# Gmail setup

This guide is for the app's owner. It covers connecting the Gmail plugin to Google: a Google Cloud project, the
OAuth client, the `.env` settings, turning the plugin on and connecting accounts. Allow about 15 minutes.

The app calls Google only after two things: the three settings below are in `.env`, and the admin has turned Gmail
on in Admin → Plugins. Until then Gmail is off and nothing in the UI mentions it.

> **Status (2026-10-16):** the plugin has been tested only against a local fake Google server, never against real
> Google. Section 13 lists what to check on the first real run.

## What you need

- A Google account to own the Cloud project. A personal @gmail.com account works. The "Internal" option in step 2
  needs a Google Workspace organization.
- The app running in Docker (`http://localhost:8080`), its admin login, and a shell in the repo directory.
- The Google accounts that will connect. While the Google app is in "Testing", each one must be added as a test user
  (step 3).

## 1. Create a project and enable the Gmail API

1. Open <https://console.cloud.google.com/>. Create a project (for example "AskNuvexusDoc") or pick an existing one,
   and check that it is selected in the top bar.
2. Go to **APIs & Services → Library**, search for **Gmail API** and click **Enable**.

## 2. Configure the Google Auth Platform

1. Open **Google Auth Platform** (<https://console.cloud.google.com/auth/overview>) and click **Get started**.
2. Fill in **App information**: app name "AskNuvexusDoc", and your email as the user support email. Users see this
   name on Google's consent screen. An app set up before the rename can be renamed under **Branding**.
3. Under **Audience**, choose **External**. **Internal** works only when the project belongs to a Workspace
   organization and every user is in that organization; see section 9.
4. Under **Contact information**, enter your email. Accept the policy and click **Create**.

Don't upload a logo. A logo makes Google require brand verification before the consent screen shows it.

## 3. Audience: test users

Open **Audience** and leave **Publishing status: Testing**. Under **Test users**, click **Add users** and add every
Google account that will connect, up to 100.

Google blocks any other account. It shows "Access blocked: … has not completed the Google verification process"
(Error 403: access_denied) and never sends the user back to the app. The app's Connections dialog warns about this
before redirecting.

## 4. Data Access: scopes

Open **Data Access → Add or remove scopes**. Tick the following scopes, or paste them under **Manually add scopes**,
then click **Update** and **Save**:

| Scope | Used for |
|---|---|
| `openid` and `.../auth/userinfo.email` | the Google account's id and address. The app needs no Gmail call to learn who connected |
| `https://www.googleapis.com/auth/gmail.readonly` | **Search and read**: chat search and reading, the Gmail browser and imports |
| `https://www.googleapis.com/auth/gmail.compose` | **Drafts**. Google describes it as "Manage drafts and send emails", so it also covers **Send** |
| `https://www.googleapis.com/auth/gmail.send` | **Send**, only when the admin turns Drafts off |

Google marks `gmail.readonly` and `gmail.compose` as restricted and `gmail.send` as sensitive. That's fine while
testing; see section 9 for what it means later.

The app asks only for what the admin has turned on. With all three capabilities on, it requests `openid email
gmail.readonly gmail.compose`. Admin → Plugins lists the exact set under "Scopes requested".

## 5. Create the OAuth client

1. Open **Clients → Create client**. Choose application type **Web application** and name it, for example
   "AskNuvexusDoc (local)".
2. Under **Authorized redirect URIs**, click **Add URI** and enter exactly:

   ```
   http://localhost:8080/api/plugins/gmail/oauth/callback
   ```

   It must match character for character. It is `http` (Google allows plain http only for localhost), uses
   `localhost` (not `127.0.0.1`) and has no trailing slash. If you changed `APP_PORT`, use that port here and in
   `PUBLIC_BASE_URLS`. Add tunnel or public addresses as well (section 8).
3. Leave **Authorized JavaScript origins** empty and click **Create**.
4. Copy the **Client ID** and **Client secret** now, or download the JSON. Google may not show the secret again. If
   you lose it, add a new secret to the client, put it in `.env`, and delete the old one (section 11).

## 6. Put the credentials in `.env`

Edit `.env` in the repo root yourself. Never commit it, and never paste its values into a chat, a ticket or a log.

1. Generate an encryption key for users' Google tokens:

   ```bash
   python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
   ```

2. Set these lines in `.env`. `.env.example` has the full Plugins block with comments:

   ```bash
   GOOGLE_OAUTH_CLIENT_ID=<client id from step 5>
   GOOGLE_OAUTH_CLIENT_SECRET=<client secret from step 5>
   TOKEN_ENCRYPTION_KEY=<output of the command above>
   PUBLIC_BASE_URLS=http://localhost:8080
   # GOOGLE_OAUTH_TESTING_MODE=true   (the default; set false only after section 9)
   ```

   - `TOKEN_ENCRYPTION_KEY` is deliberately separate from `SECRET_KEY`. Rotating `SECRET_KEY` signs everyone out but
     keeps their Gmail connections. If this key is lost, every user has to reconnect. Keep a copy somewhere safe.
   - Leave `GOOGLE_OAUTH_AUTHORIZE_URL`, `GOOGLE_OAUTH_TOKEN_URL`, `GOOGLE_OAUTH_REVOKE_URL` and `GMAIL_API_BASE_URL`
     unset. They exist only to point the app at a fake Google in tests.
   - The optional limits (`GMAIL_*`) are explained in `.env.example`. The defaults are 5 Gmail tool calls and 40,000
     characters of email per answer, 20 sends per user per UTC day, 30 minutes to click Send, 15 MB of attachments
     per email, and 20 items per import.

3. Restart, then check:

   ```bash
   export PATH="$HOME/.docker/bin:$PATH"     # macOS, if docker isn't found
   docker compose up -d
   docker compose up -d --no-deps --force-recreate frontend
   curl -s http://localhost:8080/api/health
   ```

   - `docker compose up -d` recreates the backend and the worker with the new settings.
   - The second command restarts nginx, which looks up the backend's address only when it starts. Without it, a
     backend container with a new address can leave the site answering 502.
   - To see which settings the backend still lacks (it prints names only, never values):

     ```bash
     docker compose exec -T backend python -c "from app.plugins import registry; print(registry.configured(registry.get_plugin('gmail')) or 'configured')"
     ```

     The answer should be `configured`. `TOKEN_ENCRYPTION_KEY` listed although it is set means the key is invalid: it
     must be 44 characters of URL-safe base64. The backend log says so too.

## 7. Turn Gmail on and connect

**As the admin:** open Admin → **Plugins**.

- Gmail should show **Configured** and no "Test endpoints" badge. Turn **Enabled** on.
- Check that "Redirect URIs to register in Google Cloud" matches step 5 exactly. It has a Copy button.
- Check that "Scopes requested" matches step 4.
- The capability switches (**Search and read**, **Drafts**, **Send**) are all on by default. The note under Drafts
  is expected: Google's draft permission also allows sending, and the app still sends only on the user's click.
- Optional: under **Request check**, pick each chat model users may choose and click **Test**. Each test is one small
  model call (a fraction of a cent). The check is a separate model call that confirms the user asked for a draft,
  send proposal or import in their own words. A model that fails it refuses all of those.

**Each user, the admin included:** **Connections** appears in the account menu once Gmail is on (reload the page if
it doesn't).

1. Open Connections and click **Connect Gmail**. While the app is in testing mode, a note explains that only test
   users can connect. Click **Continue**.
2. At Google, choose the account and click **Continue** on "Google hasn't verified this app". Tick every box on the
   permissions screen.
3. Back in the app: "Gmail is connected as <address>." Admin → Plugins now counts 1 connected.

In the Connections dialog:

- An unticked box shows as **Grant access** next to that capability.
- **Use in chat** (on by default) decides whether the assistant gets the Gmail tools. They add about 1,900 input tokens
  to every model call, so turning it off saves tokens when you don't need email in chat.
- **Browse Gmail**, and **Import from Gmail** in the document panel, open the import browser.

What users can then do:

- **Ask about email in chat.** For example, "what did Alice say about the invoice?". The assistant searches and reads
  messages and cites them as `[n]` sources with an "Open in Gmail" link.
- **Import** threads and their supported attachments (PDF, Word, Excel, PowerPoint, HTML, Markdown, text, CSV, JSON)
  into their private documents. They are marked "Imported from Gmail".
- **Drafts.** "Draft a reply to Bob saying …" creates a Gmail draft and shows a card with "Open in Gmail".
- **Send.** The assistant can only propose an email. The card shows the exact recipients, subject, body and
  attachments, and nothing is sent until the user clicks **Send** within 30 minutes. Recipients the user never typed,
  attachments they never named, Bcc and the email being replied to are highlighted on the card.

Drafts, send proposals and imports happen only when the user's own message asks for them, never because an email
says so. A bare "yes" to the assistant's offer doesn't count once the conversation has touched email, so users should
say what they want, for example "yes, draft the reply to Alice".

## 8. Tunnel and other addresses

The app builds the redirect URI from the address the browser used, and only from addresses listed in
`PUBLIC_BASE_URLS`. Every address users connect from needs two entries:

1. In `.env`, add the origin to the comma-separated list. Use `scheme://host[:port]`, with no path and no trailing
   slash:

   ```bash
   PUBLIC_BASE_URLS=http://localhost:8080,https://abc-def.trycloudflare.com
   ```

2. In Google Cloud, under **Clients →** your client **→ Authorized redirect URIs**, add
   `https://abc-def.trycloudflare.com/api/plugins/gmail/oauth/callback`. Google says a change can take from 5 minutes to
   a few hours to apply.
3. If the console asks, also add the host (`abc-def.trycloudflare.com`) under **Branding → Authorized domains**.
   `trycloudflare.com` is on the Public Suffix List, so each quick-tunnel host counts as its own domain.
4. Restart as in step 6.3.

A Cloudflare quick tunnel gets a new URL on every restart, so all four steps repeat each time. Two ways around that:

- **Connect at `http://localhost:8080`, on the Mac that runs Docker.** Tokens are kept on the server, so a connection
  made once at localhost then works from any address, the tunnel included. This doesn't help users who can only reach
  the app through the tunnel.
- **Use a tunnel with a fixed hostname** (a named Cloudflare tunnel on your own domain). Then you register it once.

Opening the app at an address that isn't listed gives "Connect from http://localhost:8080: this address isn't in
PUBLIC_BASE_URLS." That includes `http://127.0.0.1:8080`, which is a different origin from `localhost`. Nothing is
sent to Google in that case.

## 9. The 7-day limit in Testing, and how to avoid it

While the publishing status is **Testing**, Google ends every connection 7 days after consent: the refresh token
stops working. The app tracks this itself:

- The Connections dialog says "Google ends this connection around <date>; reconnect then".
- In the last day it warns "Google will end this connection within a day. Reconnect now".
- At day 7 the connection reads "Reconnect needed", without waiting for a failed call.

Reconnecting takes two clicks and resets the 7 days. To avoid the limit, change how the Google app is published:

| Option | Who can connect | Cost |
|---|---|---|
| **Testing** (default) | up to 100 listed test users | reconnect every 7 days |
| **Internal** (Audience → User type) | every account in your Workspace organization, nobody else | needs a Workspace-owned project. A personal @gmail.com project can't choose it |
| **In production, unverified** (Audience → Publish app) | any Google account, at most 100 users in total | users see "Google hasn't verified this app" and must click through **Advanced** to continue. Google accepts this for personal use by fewer than 100 users |
| **In production, verified** | anyone | Google's review of the restricted Gmail scopes, including a yearly third-party security assessment. Rarely worth it for a self-hosted app |

After switching to Internal or In production:

1. Set `GOOGLE_OAUTH_TESTING_MODE=false` in `.env` and restart as in step 6.3. Otherwise the app keeps marking
   connections as expired after 7 days.
2. Have each user reconnect once, so their connection gets a token issued under the new status.

## 10. Disconnecting and revoking access

- **A user:** Connections → **Disconnect**. The app deletes its copy of the tokens and cancels that user's unsent
  proposals. It also revokes the grant at Google, unless another user of this app has connected the same Google
  account (revoking would cut them off too). Drafts and sent mail stay in Gmail, and imported documents stay in the
  user's documents.
- **At Google:** the account owner can remove "AskNuvexusDoc" at <https://myaccount.google.com/permissions> at any
  time. The app notices on the next Gmail call and shows "Reconnect needed".
- **The admin, for everyone:** Admin → Plugins → **Disconnect all users** deletes every Gmail connection and revokes
  each Google account's grant once.
- **Turning Gmail off** (the Enabled switch) takes effect at once:
  - answers already streaming have their next Gmail action refused;
  - the next answer gets no Gmail tools;
  - open pages hide the Gmail UI the next time they load or regain focus.

  Connections are kept, so turning it back on needs no reconnect. Users can still disconnect while it is off.
- **Deleting a user** (Admin → Users) deletes their connection and revokes it in the same way.
- **Removing everything:** disconnect all users, then clear `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET`
  in `.env` and restart. Gmail then shows "Not configured". To shut it at Google's side as well, delete the OAuth
  client in Google Cloud.

## 11. Rotating keys and secrets

- **`TOKEN_ENCRYPTION_KEY`:**
  1. Generate a new key (step 6.1) and put it **first**: `TOKEN_ENCRYPTION_KEY=<new>,<old>`.
  2. Restart as in step 6.3. Every start re-encrypts stored connections under the first key.
  3. Remove the old key only when Admin → Plugins no longer warns that connections "still use an older encryption
     key". A connection no listed key can read shows "The server's encryption key changed…" and must reconnect.
     Adding the old key back recovers it.
- **The client secret:**
  1. In Google Cloud, open the client and add a new secret.
  2. Put it in `.env` and restart.
  3. Delete the old secret in Google.

  Connections survive this, because Google ties them to the client ID, not to the secret.
- **`SECRET_KEY`:** rotating it signs everyone out of the app but leaves Gmail connections working.

## 12. Troubleshooting

Backend messages about Gmail are in `docker compose logs --since 30m backend`. Search them for "gmail" or "Gmail".
The app never logs tokens, codes, search terms, subjects, addresses or email text, so the log names a cause (an HTTP
status and Google's error code), never the content.

| What you see | Cause and fix |
|---|---|
| Admin → Plugins: "Missing in .env: …", or the Enabled switch is greyed out | A listed setting is empty or invalid. Fix `.env` and restart (step 6.3). An invalid key is reported as missing `TOKEN_ENCRYPTION_KEY` |
| A "Test endpoints" badge | One of the four `GOOGLE_OAUTH_*_URL`/`GMAIL_API_BASE_URL` settings is set. Remove it and restart |
| No **Connections** item in the account menu | Gmail is off or not configured, or the page predates the change. Reload |
| "Connect from http://localhost:8080: this address isn't in PUBLIC_BASE_URLS." | The app was opened at an unlisted address (`127.0.0.1`, a new tunnel URL). Use a listed address or add it (section 8) |
| Google: "Error 400: redirect_uri_mismatch" | The redirect URI in Google differs from the one in Admin → Plugins: http vs https, the port, `localhost` vs `127.0.0.1`, a trailing slash, or an old tunnel URL. Copy it from Admin → Plugins. A change in Google can take from 5 minutes to a few hours to apply |
| Google: "Access blocked: … has not completed the Google verification process", Error 403: access_denied | The account isn't a test user. Add it under Audience → Test users. The user isn't sent back to the app |
| Google: "Error 401: invalid_client" or "The OAuth client was not found" | `GOOGLE_OAUTH_CLIENT_ID` is wrong (a typo, a stray character, or a deleted client) |
| Google: "Error 403: org_internal" | The app is Internal and the account is outside your Workspace organization |
| Google: "Error 400: admin_policy_enforced" | A Workspace admin blocks this app or Gmail access. They must trust the client ID under API controls |
| App: "Google sign-in was cancelled." | The user clicked Cancel at Google. Connect again |
| App: "No Gmail access was allowed. Connect again and tick the boxes." | Every Gmail box was unticked on Google's permissions screen |
| A capability shows **Grant access** | Its box was unticked. Click Grant access and tick it |
| App: "The sign-in took too long or was already used. Try again." | More than 10 minutes passed at Google, or the return page was reloaded or opened twice |
| App: "This sign-in didn't start in this browser, or your session changed. Try again." | Connect started in another browser or profile, cookies were blocked, or the user signed out or changed their password meanwhile |
| App: "Couldn't connect Gmail. Try again; if it keeps failing, tell the admin." | The code exchange failed. The log shows `The gmail token exchange failed: HTTP 401 invalid_client` when the secret is wrong, `HTTP 400 invalid_grant` when the code was rejected (for example a redirect URI or clock problem), or a network error |
| "Reconnect needed": "Google ended this connection: while this app is in Google's testing mode, connections last 7 days…" | The 7-day limit (section 9). Reconnect |
| "Reconnect needed": "Google access was removed or has expired…" | Google answered `invalid_grant`: access was removed at myaccount.google.com, the password changed, or the token expired. Reconnect. The log shows `Connection N of user M needs a reconnect (revoked)` |
| "Reconnect needed": "The server's encryption key changed…" | `TOKEN_ENCRYPTION_KEY` no longer includes the key the tokens were stored under. Put the old key back after the new one (section 11), or reconnect |
| "Reconnect needed": "Google no longer allows this app to use your Gmail as before…" | Gmail refused for missing permissions. Reconnect and tick every box |
| In chat: "Gmail isn't working right now; tell the admin." | Google rejected the OAuth client when refreshing (secret changed or deleted). The log shows `The provider rejected the gmail OAuth client (invalid_client)…`. Fix the secret in `.env` and restart |
| In chat: "Gmail refused this request." | Usually the Gmail API isn't enabled for the project (step 1). The log shows `Gmail GET /gmail/v1/… -> 403 (…)`. Connecting still works, because it makes no Gmail call |
| "Gmail is busy right now. Try again in a minute." or "Gmail is unreachable right now." | Gmail's rate limits, or network trouble, after the app's retries. Try again |
| Drafts, sends or imports are refused although the user asked ("Not done: the app couldn't check your request…", or "…your messages didn't ask for this") | The request check doesn't work with that chat model, or the request was too vague. Run Admin → Plugins → Request check → Test for the model and use another one if it fails; otherwise ask again in plain words ("draft a reply to Alice saying …") |
| "You've sent 20 emails today, the daily limit." | `GMAIL_DAILY_SEND_LIMIT` per user per UTC day, the admin included |
| An "Open in Gmail" link opens the wrong mailbox | Links use `/mail/u/0/`, the browser's first signed-in Google account. Switch accounts in Gmail, or sign in with the connected account first |

## 13. First run with real Google: what to check

The build was tested end to end against a fake Google, so these points are known only from Google's documentation.
Use a throwaway `smoke-*` app user for the first run, and send a test email only to yourself.

- The consent flow: the "Google hasn't verified this app" screen, granular checkboxes, and the return to the app
  with "Gmail is connected as …".
- The connection lists all three capabilities as granted, and Admin → Plugins counts it. With Drafts and Send both on,
  a test send to yourself confirms that a token holding only `gmail.compose` can send.
- Search and read in chat cite emails with working "Open in Gmail" links.
- An import of a thread and one PDF attachment becomes ready documents marked "Imported from Gmail". Check how
  real-world charsets look.
- A draft reply appears threaded under the original, and its card link opens it.
- After you remove access at myaccount.google.com, the next Gmail use shows "Reconnect needed".
- `docker compose logs --since 30m backend frontend` holds no `code=4/`, `ya29.`, `1//`, your address or the test
  subject.
- If you use a tunnel, whether the console asks for the Authorized domains entry (section 8).

The developer checklist is in `docs/briefs/plugins-gmail.md`, section 18.4.
