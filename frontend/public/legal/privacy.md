# Privacy policy

Last updated: 1 November 2026

This policy explains what personal data AskNuvexusDoc collects, why we use it, who else handles it, how long we keep it, and the rights you have under India's Digital Personal Data Protection Act, 2023 and the Digital Personal Data Protection Rules, 2025.

## Who we are

AskNuvexusDoc (asknuvexusdoc.com) is run by Elarxsys AI Hub Pvt. Ltd., VSR Classic, #201, RYC, Hyderabad 500090, Telangana, India ("we", "us"). We decide why and how your personal data is used, so we are the data fiduciary for it.

Questions about this policy go to admin@elarxsys.com or to our Grievance Officer (at the end of this page).

## In short

- We collect what the service needs to work: your account, the documents you upload, your chats, your usage and your billing records.
- To answer a question, the app sends it, with the most relevant passages from your documents, to an AI provider (Anthropic or OpenAI).
- We don't sell your data, use it for advertising, or use it to train AI models.
- Your documents and chats are private to your account. The app runs on a computer we operate in India.
- There are no advertising, analytics or tracking cookies.
- You can export your data at any time and ask us to delete your account.

## What we collect

**Your account**

- Your name, as you give it when you sign up or later in Settings → Account (you can change it there).
- A username the app generates for you (for example `user-k3m9q2xa`).
- Your password, stored only as an argon2 hash, never in readable form.
- Your email address once you have confirmed it, and when you confirmed it.
- How you signed up (email, Google, or an account we created for you), and the version of our terms you accepted and when.
- Your settings, such as theme, font size and default model, and your choices for receipt and reminder emails.
- The name to print on your receipts, if you add one.

**Continue with Google** (if you use it): your Google account ID, your email address, whether Google has verified it and, for Google Workspace accounts, the organisation's domain. More under "Google sign-in and Gmail" below.

**Your documents**

- The files you upload, the text we extract from them, and the passages and search index (embeddings) we build from that text.
- For PDF pages that have no text layer, or that contain charts and pictures, the text and descriptions the AI vision model wrote for them.
- Files and images the assistant creates for you in chat, and the prompt used for each image.
- If you use Gmail, the emails and attachments you choose to import.

**Your chats**: your messages, the answers, the sources each answer cites, and the cards shown in the chat (for example a file the assistant made or an email draft). A chat's title is the start of its first message.

**Usage**: for each answer, image and document, the model used, the number of tokens or images, the estimated cost and the time. This is how we apply your plan's limits and show you your usage. It doesn't include the content.

**Sign-in and security records**

- Your 20 most recent sign-ins: an approximate IP address (the last part hidden), your browser, operating system, type of device, and whether you used a password or Google.
- Changes to your account (sign-up, confirmations, password resets and changes, email changes, Google connections) with an approximate IP address.
- Short-lived counters of failed attempts, which stop repeated guessing.

**Billing records**: your plan periods, orders, payments (amount, method such as UPI or card, status, and Razorpay's payment ID), refunds, disputes and receipts. When you pay online, you pay on Razorpay's own page: we never receive or store your card number, UPI ID or bank account, or the phone number and email address you type there. For a direct UPI payment we keep the 12-digit UTR you enter and our order reference. Our bank or UPI app shows us the name and UPI ID you paid from, as for any UPI payment; we don't copy them into AskNuvexusDoc.

**Gmail** (only if you connect it): see "Google sign-in and Gmail" below.

**Email delivery records**: while an email to you waits to be sent, its address and kind; afterwards only a hash of the address. Hashes of addresses whose mail bounced or that reported our mail as spam, so that we stop writing to them.

**Technical data**: our web server logs each request (IP address, time, page, the page that linked to it, and browser) for security and troubleshooting, and deletes these logs after 30 days. Its error messages can include an IP address; they stay in the server's own log, which is trimmed by size (the oldest messages go first). Cloudflare, which carries all traffic to our site, also sees your IP address and browser details.

**Messages you send us**: what you write to us, and our replies.

## Why we use it, and on what basis

We rely on one of these grounds for each use:

- **Consent**: you give it when you create your account and tick the box accepting our terms and this policy, and when you turn on an optional feature such as Gmail.
- **The service you asked for**: data you give us to use a feature (a document you upload, a question, a payment) is used for that purpose, under our [Terms of service](/terms).
- **Legal obligations**: what the law requires of us, such as keeping accounting and tax records or complying with a lawful order.

| Purpose | Data used | Basis |
|---|---|---|
| Create your account, sign you in and keep it secure | Account, Google sign-in, sign-in and security records | Consent; the service |
| Search your documents, answer your questions, create files and images | Documents, chats | Consent; the service |
| Apply your plan's limits and show your usage | Usage | The service |
| Take payments, issue receipts, handle refunds and disputes | Billing records | The service; legal obligations |
| Keep accounting and tax records | Billing records | Legal obligations |
| Send the emails your account needs | Email address, email delivery records | The service |
| Use Gmail in chat, if you connect it | Gmail data | Consent |
| Protect the service against abuse and fix problems | Security records, technical data | The service; legal obligations |
| Answer your requests and complaints | Messages you send us | Consent; legal obligations |

We don't build profiles of you, and we don't use your data for any purpose not listed here.

## Google sign-in and Gmail

**Continue with Google.** The app asks Google only for your account ID and email address (the `openid` and `email` permissions). Google also tells us whether it has verified the address and, for Workspace accounts, the organisation's domain. We keep the ID, the address as it was when you connected, and whether it was verified, and use them only to sign you in and, when the address is verified, as your account's email. We never receive your name, photo, contacts or Google password, and we don't keep any Google token. Signing in with Google never gives the app access to your Gmail. You can disconnect Google in Settings → Account once your account has a password and a confirmed email.

**Gmail** is optional. It is available on plans that include it, and only after you connect your Gmail account in Settings → Plugins & data and grant the permissions it asks for (reading, creating drafts, sending). Then:

- When you ask about your email in chat, the app searches and reads the messages needed and sends their text to the AI model writing the answer. An answer that cites an email keeps its subject, sender, date and a short snippet with the chat.
- Threads and attachments you choose to import become private documents in your account, stored and indexed like an upload.
- The app creates a draft in your Gmail when you ask for one. It sends an email only when you click Send on the exact email it shows you; until then the proposed email is stored with the chat.
- The app keeps the address of the connected Google account and its access tokens, encrypted with a key kept apart from the app's other secrets.
- It counts Gmail actions (searches, drafts, imports, sends) without subjects, addresses or content.

You can disconnect at any time in Settings → Plugins & data. That deletes the tokens and withdraws the app's access at Google (unless another AskNuvexusDoc account is connected to the same Gmail account). Email text in a chat lasts as long as the chat; deleting a chat also clears the stored text of any email proposed in it. Imported emails stay in your documents until you delete them.

AskNuvexusDoc's use of information received from Google APIs adheres to the [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy), including the Limited Use requirements. We use Google data only to provide the features described here. We don't use it for advertising, sell it, or use it to train AI models, and nobody at AskNuvexusDoc reads it unless you ask us to (for example to fix a problem), it is needed for security, or the law requires it.

## Who else processes your data

We use the companies below to run the service. Each processes data for us under its own terms and privacy policy. Some of them are based outside India, so your data may be processed outside India.

**Anthropic and OpenAI** (AI models). Depending on what you do, the app sends them:

- your question and the earlier messages of the chat, to the provider of the model you chose (Anthropic for Claude models, OpenAI for GPT models);
- passages from your documents and from our shared knowledge base, with their file names and page numbers;
- email text, when you use Gmail in chat;
- the text of your documents, in pieces, to build the search index, and each question, to search it (currently OpenAI);
- images of PDF pages that have no text layer or that contain charts and pictures (Anthropic or OpenAI, depending on the model in use);
- the description of each image you ask for (OpenAI);
- your recent messages, for a short safety check before the assistant drafts, sends or imports email, or creates a file or image after reading email.

The app doesn't add your name, username or email address to these requests.

**Cloudflare** carries all traffic between your browser and our server and provides the site's secure (HTTPS) connection, so what you send and receive passes through its network. It also runs the robot check (Turnstile) on sign-up, password reset and, after repeated failed attempts, sign-in; the check sees your IP address, browser details and connection fingerprint. We don't keep its results.

**Resend** sends every email the app sends you. It receives your email address and the email.

**Google** signs you in when you use Continue with Google, and handles the Gmail requests the app makes for you if you connect Gmail.

**Razorpay** processes payments. We send it the amount, a description, our order reference and your account's number, never your name, email or phone. Razorpay collects the payment details it needs on its own page. Direct UPI payments don't go through Razorpay.

**Have I Been Pwned** checks new passwords against known data breaches. Only the first 5 characters of a hash of the new password leave the app, never the password itself or anything about your account.

We share personal data with anyone else only when the law requires it, for example under a court order or a lawful request from a government authority.

## Where your data is stored

The app runs on a computer we operate in India, and everything it keeps is stored there:

- uploaded and created files on the server's disk;
- your account, chats, usage and billing records in the app's database;
- your documents' passages and search index in the app's search database;
- short-lived data (queues, counters, sign-in steps) in a memory store that keeps nothing for long.

Backups are kept on the same computer, in a folder with restricted access. What the processors above receive is handled where they process it.

## How long we keep it

| Data | Kept |
|---|---|
| Your account: username, email, settings, terms record | Until the account is deleted |
| Documents you upload, files and images created in chat, their text and search index | Until you delete them or the account is deleted |
| Chats | Until you delete them or the account is deleted |
| Usage records | Until the account is deleted |
| Accounts without a plan (a plan that ended, or none bought yet) | Nothing is deleted when a plan ends. We delete an account and its data after 12 months with no plan and no sign-in, after emailing you 30 days before |
| Continue with Google: Google's account ID, the Google address when it was connected and whether Google had verified it | Until you disconnect Google or the account is deleted |
| Gmail: the connected address and its tokens | Until you disconnect Gmail or the account is deleted |
| Billing records: orders, payments, refunds, disputes, receipts | 8 years after the end of the financial year of the payment, also after the account is deleted, then with the username and the name and email printed on each receipt |
| A sign-up not yet confirmed: the address, the password (hashed), a hash of the browser's sign-up cookie, the page to open afterwards, the terms record and an approximate IP address | 7 days at most |
| An email address waiting for confirmation | 24 hours after the last confirmation email |
| Hashes of the links in emails and of the addresses they were sent to | A day after they are used or expire |
| The recipient and kind of each email the app sends | Until it is sent (then only a hash); the record 48 hours more |
| Hashes of addresses whose mail bounced or that reported a mail as spam | 30 days for a bounce; a spam report until you confirm the address again |
| Account changes (sign-up, confirmations, password resets and changes, email changes, Google connections) with an approximate IP address | The IP address 90 days; the record 365 days or until the account is deleted |
| The previous email address after an email change (encrypted), so its owner can undo the change | 8 days |
| The usernames of deleted accounts (nothing else), so they are never given to someone else | Always |
| Sign-ins: approximate IP address, browser, operating system, device and the way of signing in | The newest 20 |
| Counters that limit repeated attempts (network prefixes and address hashes) | Their time window, at most a day |
| Web server logs of each request: IP address, time, page, the page that linked to it, browser | 30 days |
| Web server error messages, which can include an IP address | In the server's own log, trimmed by size (the oldest go first) |
| Backups of the database, files and search index | Replaced on a rolling schedule; the oldest is at most about five weeks old |
| Messages you send us | As long as needed to handle your request and keep a record of it |

When an account is deleted, everything kept for it goes at once: documents and their files, chats, usage, Gmail and Google connections, settings, sign-ins and account changes. Only the billing records (if the account ever placed an order) and the username stay, as above. Deleted data disappears from the backups within about five weeks.

The app never keeps the name, picture or tokens of a Google account (it doesn't ask for them), the results of the robot check, or the links in emails, and it stores no full IP addresses in your account's records beyond the short-lived attempt counters.

## How we protect it

- Passwords are stored as argon2 hashes, and new passwords are checked against common and breached passwords.
- Links in our emails are random, stored only as hashes, work once and expire: 24 hours to confirm an email, 30 minutes to reset a password, 7 days to undo an email change.
- All traffic to the site uses HTTPS, and browsers are told to use nothing else.
- Your session is a cookie that page scripts can't read and that is sent only to our site. Changing or resetting your password signs out every session, and Settings → Account can log out all your other sessions.
- Gmail tokens are encrypted when stored.
- Sign-in, sign-up and email requests are rate-limited, and a robot check guards sign-up and password reset.
- Your documents and chats are visible only to your account. The administrator's pages show accounts, plans, usage figures, and document names and sizes (to manage storage), but not your chats, the content of your private documents, your sign-in history or the content of your email. As the operator of the server we can technically reach stored data; we do so only to run and repair the service, when you ask us to, or when the law requires.
- Backups are checked when they are made and kept in a folder with restricted access.

No system is perfectly secure. If a breach affects your personal data, we will tell you and the Data Protection Board of India as the law requires.

## Your rights

Under the Digital Personal Data Protection Act, 2023, you have the right to:

- **Know** what personal data we process about you, how, and who we have shared it with. This policy lists the companies; your data export (below) gives you a copy.
- **Correct, complete or update** it. You can change your email in Settings → Account; ask us for anything else.
- **Erase** it. You can delete your documents and chats yourself, and ask us to delete your account. We keep only what the law requires (the billing records above).
- **Withdraw your consent** at any time, as easily as you gave it. Disconnect Gmail or Google in Settings; to withdraw consent for the service as a whole, ask us to delete your account. Withdrawing doesn't affect what we did before it, and without it we can't provide the service. A paid period isn't refunded because of it, except as our [Cancellation and refunds policy](/refunds) says.
- **Have a grievance redressed** by our Grievance Officer, and, if you are not satisfied, complain to the Data Protection Board of India.
- **Nominate** someone to use these rights for you if you die or become unable to. Email us their name and contact details from your account's email address.

## How to use your rights

- **Get a copy of your data**: Settings → Plugins & data → Export my data. You get a ZIP file with your account details (including your email, Google connection, terms record, sign-ins and account changes), your plans and payments, the details of your documents, and your chats as Markdown and JSON. It doesn't contain the document files themselves: download those from the documents panel. A very large export holds your newest chats up to a size limit and says what it left out. The export works with or without a plan.
- **Delete chats and documents**: delete a chat from the chat list, every chat with Settings → Plugins & data → Delete all chats, and a document from the documents panel. Deleting removes it from the app at once, and from the backups within about five weeks.
- **Delete your account**: the app has no button for this yet. Email admin@elarxsys.com from your account's email address, or give your username. We may ask you to confirm the request.
- **Anything else**, including a complaint: write to the Grievance Officer below.

We acknowledge every request within 48 hours and resolve it within one month.

## Children

AskNuvexusDoc is for adults: you must be 18 or older to create an account. The app doesn't ask your age, and we don't knowingly process children's personal data. If you believe a child has an account, tell us and we will delete it.

## Cookies and browser storage

We use only the cookies the service needs to work. There are no advertising, analytics or tracking cookies, and no analytics or advertising scripts on our pages. The only outside script is Cloudflare's robot check, on the sign-in, sign-up and forgot-password pages.

| Cookie | What it does | How long |
|---|---|---|
| `__Host-rag_session` | Keeps you signed in | Up to 12 hours, or until you log out |
| `__Secure-rag_signup` | Ties an email sign-up to the browser where you started it, so its link finishes in one click | Up to 7 days |
| `__Secure-rag_gsignin`, `__Secure-rag_gticket` | Protect Continue with Google, and hold a new Google sign-up until you confirm it | 10 and 15 minutes |
| `rag_oauth_gmail` | Protects connecting Gmail | 10 minutes |

The app also keeps a few things in your browser's storage: your appearance settings (so pages show your theme before they load), whether the documents column is hidden, and when you dismissed the plan reminder. Steps in progress, such as a sign-up, an email confirmation or an unsent message while you renew a plan, stay in the tab's storage until you close it.

## Emails we send

We send only the emails an account needs:

- confirming a sign-up or a new email address, and telling you when an address already has an account;
- password reset links;
- security notices: your password was changed or added, your email was changed or the change was undone, Google sign-in was added or removed;
- a receipt for each payment, with the PDF attached;
- plan reminders, 5 days before your plan ends, the day before and when it has ended.

You can't turn off confirmations, resets and security notices, because they protect your account. You can turn off receipts and reminders in Settings → Plan & billing. We don't send newsletters or marketing, and our emails have no tracking pixels or click tracking. They come from no-reply@asknuvexusdoc.com through Resend. Razorpay also emails a payment confirmation to the address you give it.

## Changes to this policy

When we change this policy, we post the new version here with a new "Last updated" date. If a change affects how we use your data in a significant way, we will email you before it takes effect.

## Grievance Officer

- Name: Bikash Patel
- Email: admin@elarxsys.com
- Address: Elarxsys AI Hub Pvt. Ltd., VSR Classic, #201, RYC, Hyderabad 500090, Telangana, India

Include your username or account email and what you are asking for. We acknowledge every grievance within 48 hours and resolve it within one month of receiving it. If you are not satisfied with our answer, you can complain to the Data Protection Board of India.
