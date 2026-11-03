"""Shapes of sign-up, sign-in, Google sign-in, password reset, the account's email and the admin's sign-up settings
(SPEC.md section 6, "Auth", "Account" and "Admin"; docs/briefs/signup-auth.md section 11). Contract only. Every request
body refuses unknown fields (422). Addresses are parsed by app.services.auth.emails after the cheaper checks (its
error is a 422 "Enter a valid email address."), so here they are only bounded."""

import re
import unicodedata
from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    AliasChoices,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
)

PASSWORD_MAX_LENGTH = 256  # code points after NFC, where a password is set (the policy's too_long)
NAME_MAX_LENGTH = 80  # code points after normalizing (users.display_name)
SIGNIN_PASSWORD_MAX_LENGTH = 1024  # accepted wherever a password is typed, so a long ADMIN_PASSWORD still works

PasswordReason = Literal["too_short", "too_long", "common", "context", "breached"]
SignupSource = Literal["env", "admin", "email", "google"]
SignupWarning = Literal["real_ip", "payments_not_live", "mail_webhook_missing", "admin_password_weak"]
GoogleIntent = Literal["signin", "link", "reauth"]
GoogleFrom = Literal["login", "signup"]
PendingEmailSource = Literal["user", "google"]
EmailChangeNeeds = Literal["password", "google", "not_allowed"]


def _identifier(value: object) -> object:
    # Postgres text can't hold NUL characters (the lookup would fail instead of finding no user).
    return value.replace("\x00", "").strip() if isinstance(value, str) else value


# The whitespace a name's runs of collapse to one space, and the joiners its scripts may need (frontend lib/names.ts has
# the same sets).
_SPACES = re.compile(r"[\t\n\v\f\r \xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+")
_JOINERS = frozenset("\u200c\u200d")


def person_name(value: object) -> object:
    """A person's name (users.display_name) as stored: NFC, inner whitespace collapsed to one space, trimmed; 1 to 80
    characters, any script, no control or invisible character (category C, except the joiners) and no "<" or ">"."""
    if not isinstance(value, str):
        return value
    name = _SPACES.sub(" ", unicodedata.normalize("NFC", value)).strip(" ")
    if not name:
        raise ValueError("Enter your name.")
    if len(name) > NAME_MAX_LENGTH:
        raise ValueError(f"Use at most {NAME_MAX_LENGTH} characters.")
    if "<" in name or ">" in name:
        raise ValueError("Leave out < and >.")
    if any(unicodedata.category(char)[0] == "C" and char not in _JOINERS for char in name):
        raise ValueError("Remove the invisible or control characters.")
    return name


def _blank_to_none(value: object) -> object:
    return None if isinstance(value, str) and not value.strip() else value


def _accepted(value: bool) -> bool:
    if not value:
        raise ValueError("Accept the terms to create an account.")
    return value


# A password as typed: the policy (app.services.auth.passwords.check) answers 422 password_rejected beyond 256.
Password = Annotated[str, StringConstraints(min_length=1, max_length=SIGNIN_PASSWORD_MAX_LENGTH)]
EmailInput = Annotated[str, StringConstraints(min_length=1, max_length=320)]
TurnstileToken = Annotated[str, StringConstraints(min_length=1, max_length=2048)]
# A path on this site, sanitized by the flow (safe_next: anything else becomes None).
NextPath = Annotated[str, StringConstraints(max_length=512)]
# Ours are 43 base64url characters; any other string is simply not found (400 link_invalid).
Token = Annotated[str, StringConstraints(min_length=1, max_length=128)]
TermsAccepted = Annotated[StrictBool, AfterValidator(_accepted)]
PersonName = Annotated[str, BeforeValidator(person_name)]
# Optional where a name may be left out (the admin's forms): blank is None
OptionalPersonName = Annotated[PersonName | None, BeforeValidator(_blank_to_none)]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------- Public: configuration ----------------


class SignupAvailability(BaseModel):
    open: bool  # email or google
    email: bool
    google: bool


class AuthConfig(BaseModel):
    """GET /auth/config (public, Cache-Control: no-store)."""

    signup: SignupAvailability
    google_signin: bool  # Continue with Google works for existing accounts (configured), even with sign-up closed
    password_reset: bool  # the mailer and Turnstile are configured
    turnstile_site_key: str | None
    password_min_length: int
    password_max_length: int = PASSWORD_MAX_LENGTH
    terms_version: str
    contact_email: str | None  # Admin → Billing → Business & payments


# ---------------- Public: sign-in, sign-up, reset ----------------


class LoginRequest(_Request):
    """POST /auth/login: an identifier with "@" is an email (verified addresses only), anything else a username."""

    identifier: Annotated[
        str,
        Field(validation_alias=AliasChoices("identifier", "username")),  # "username": scripts and older clients
        StringConstraints(min_length=1, max_length=320),
        BeforeValidator(_identifier),
    ]
    password: Password
    turnstile_token: TurnstileToken | None = None  # after 401 challenge_required


class SignupRequest(_Request):
    """POST /auth/signup."""

    name: PersonName  # kept with the pending sign-up, then the account's display_name
    email: EmailInput
    password: Password
    accept_terms: TermsAccepted
    turnstile_token: TurnstileToken
    next: NextPath | None = None  # kept through verification


class EmailWithChallenge(_Request):
    """POST /auth/signup/resend and POST /auth/password/forgot."""

    email: EmailInput
    turnstile_token: TurnstileToken


class Accepted(BaseModel):
    """202, the same body for every address."""

    status: Literal["accepted"] = "accepted"


class TokenRequest(_Request):
    token: Token


class SignupCheck(BaseModel):
    """POST /auth/signup/check."""

    email: str
    expires_at: datetime
    needs_password: bool  # false when this browser started the sign-up (the rag_signup cookie)
    next: str | None


class SignupComplete(_Request):
    """POST /auth/signup/complete."""

    token: Token
    password: Password | None = None  # required when needs_password


class ResetGoogle(BaseModel):
    email: str  # masked: the Google address at link time
    suggest_disconnect: bool  # its canonical form differs from the account's email


class PasswordTokenInfo(BaseModel):
    """POST /auth/password/check."""

    email: str
    username: str
    has_password: bool
    expires_at: datetime
    google: ResetGoogle | None


class PasswordReset(_Request):
    """POST /auth/password/reset."""

    token: Token
    new_password: Password
    disconnect_google: StrictBool = False


# ---------------- Public: confirming and undoing an email ----------------


class EmailTokenInfo(BaseModel):
    """POST /auth/email/check."""

    email: str
    account: str  # the asking account: its email masked, or its username
    signed_in: bool  # this request's session is that account's


class EmailConfirmed(BaseModel):
    """POST /auth/email/verify."""

    email: str


class UndoInfo(BaseModel):
    """POST /auth/email/undo-check."""

    old_email: str
    new_email: str  # masked


# ---------------- Google sign-in ----------------


class GoogleStart(_Request):
    """POST /auth/google/start: signin without a session; link (password required) and reauth with one."""

    intent: GoogleIntent
    next: NextPath | None = None  # signin, reauth
    from_: Annotated[GoogleFrom | None, Field(alias="from")] = None  # signin
    password: Password | None = None  # link


class GoogleStarted(BaseModel):
    authorize_url: str


class GooglePending(BaseModel):
    """GET /auth/google/pending (the rag_gticket cookie)."""

    model_config = ConfigDict(validate_by_name=True, validate_by_alias=True, serialize_by_alias=True)

    email: str
    email_verified: bool  # gmail or workspace: confirmed at once
    from_: Annotated[GoogleFrom, Field(alias="from")]
    expires_at: datetime


class GoogleFinish(_Request):
    """POST /auth/google/finish."""

    name: PersonName
    accept_terms: TermsAccepted


# ---------------- The caller's account: email, password, Google ----------------


class PendingEmailInfo(BaseModel):
    """AccountInfo.pending_email: an address waiting for its confirmation."""

    address: str
    source: PendingEmailSource
    sent_at: datetime | None
    expires_at: datetime


class PendingEmailRef(BaseModel):
    """AdminUser.pending_email."""

    address: str
    sent_at: datetime | None


class GoogleLink(BaseModel):
    """AccountInfo.google."""

    email: str  # at link time
    linked_at: datetime
    last_used_at: datetime | None


class EmailChange(_Request):
    """POST /me/email."""

    email: EmailInput
    password: Password | None = None  # required when AccountInfo.email_change_needs is "password"


class NewPassword(_Request):
    """POST /me/password/setup."""

    new_password: Password


class PasswordConfirm(_Request):
    """DELETE /me/google."""

    password: Password


class NameChange(_Request):
    """PUT /me/name."""

    name: PersonName


class DisplayName(BaseModel):
    """PUT /me/name: the name as stored."""

    display_name: str


# ---------------- Admin: sign-up settings ----------------


class SignupSettings(_Request):
    """app_state "signup_settings" and PUT /admin/signup-settings; a missing value is the default."""

    open: StrictBool = False
    email: StrictBool = True
    google: StrictBool = True


class SignupStatus(BaseModel):
    """GET and PUT /admin/signup-settings."""

    settings: SignupSettings
    effective: SignupAvailability  # the settings and every prerequisite
    mail_configured: bool
    mail_webhook_configured: bool
    turnstile_configured: bool
    google_configured: bool
    payments_live: bool
    mail_sent_today: int
    mail_queued: int
    mail_daily_limit: int
    public_mail_left: int  # about how many sign-up emails still fit today
    public_paused: bool  # by bounces, until 00:00 UTC
    bounces_today: int
    complaints_7d: int
    mail_last_error: str | None  # our short reason and when, e.g. "SMTP login refused"
    signups_today: int
    signups_7d: int
    paid_7d: int  # of the last 7 days' sign-ups, how many have paid
    warnings: list[SignupWarning]  # the policy-draft check is the page's own


class MailTest(_Request):
    """POST /admin/mail/test."""

    to: EmailInput
