<!-- draft -->
# Terms of service

**Draft.** This page is a placeholder. Replace it with your own text before going live.

## The service

[Describe the service: an assistant that answers questions about the documents a user uploads and a shared knowledge base, with accounts that people create themselves or that you create]

## Accounts

[Who can have an account, how accounts are created, and what users must do: keep their password safe and use the service lawfully]

## Plans and payment

[Say that plans are paid in advance for 30 days, in rupees, through Razorpay; that a plan doesn't renew by itself; and what happens when it ends]

## Acceptable use

[What users must not do with the service]

## Your content

[Who owns the documents and chats users add, and what the service does with them]

## Changes and availability

[How the service or these terms may change, and how users are told]

## Liability

[The limits of your liability, as your adviser recommends]

## Law and contact

[The law and courts that apply, and how to reach you]

## Draft facts: creating an account

[Facts about how sign-up works, written out for you to keep, rewrite or remove. They describe what the software does; they are not legal advice. Merge what you keep into "Accounts" above and delete this section.]

- While sign-up is open, anyone can create an account with an email address and a password, or with a Google account. An email address is confirmed by a link sent to it before the account exists. You can close sign-up, or one of the two ways, at any time; existing accounts keep working.
- Creating an account means ticking a box to accept these terms. The app records the version of the terms (`TERMS_VERSION` in `.env`) and the time. When you change these terms, change `TERMS_VERSION` too, so new sign-ups record the version they accepted.
- A new account has no plan. It can see the plans and pay for one; it can't ask questions until it has a plan. A receipt is sent to the account's confirmed email address, so paying needs one.
- Each account gets a random username (for example `user-k3m9q2xa`) that also works for signing in. The username of a deleted account is never given to anyone else.
- Passwords need at least 8 characters (`PASSWORD_MIN_LENGTH`) and can't be a common password, the app's name, the username or the email address, or one found in a known data breach.
- An account can connect one Google account for signing in. Users can change their email address in Settings; the old address gets a notice with a link that undoes the change (valid 7 days).
- You can deactivate or delete any account, for example one that breaks these terms. [Say when you would, and what happens to a paid plan]
