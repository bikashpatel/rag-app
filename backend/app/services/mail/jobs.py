"""What an outbox job becomes when the worker sends it (docs/briefs/signup-auth.md 6.4): the recipient, the rendered
mail and its attachments, or nothing (with the reason). Resolving reads the accounts, pending sign-ups and pending
emails as they are now, and mints the link's token (flushed in the caller's transaction, which commits before the
send). Requests never learn what a job resolved to (S6).

Optional `params` a job may carry, beside its ids: `password_changed` `google_disconnected` (bool); `google_linked`
and `google_unlinked` `google_email` (the Google address, masked; the linked identity's is used when it is missing);
`receipt` `receipt_id`; `plan_reminder` `reminder` ("soon", "tomorrow" or "ended") and `period_id`."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final, Literal, get_args

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    BillingReceipt,
    EmailOutbox,
    PendingEmail,
    PendingSignup,
    User,
    UserIdentity,
    UserPreference,
    utcnow,
)
from app.services.auth import emails, tokens
from app.services.billing import business, periods, receipts, reminders
from app.services.mail import templates


@dataclass(frozen=True)
class Attachment:
    filename: str
    content: bytes
    maintype: str = "application"
    subtype: str = "pdf"


@dataclass(frozen=True)
class Outgoing:
    """A mail to send to `to` (an address as stored). `pending_email_id`: an email confirmation, whose pending row
    learns when its mail went. `opt_in`: a sign-up's verification or an email confirmation, which someone asked for
    with this address just now; it may reach a suppressed address once a day (drain.py)."""

    to: str
    mail: templates.Rendered
    attachments: tuple[Attachment, ...] = ()
    pending_email_id: int | None = None
    opt_in: bool = False


@dataclass(frozen=True)
class Skip:
    """Nothing to send; `reason` is stored on the cancelled job (e.g. "no_account", "pending_gone", "pref_off")."""

    reason: str


Result = Outgoing | Skip
OPT_IN: Final = frozenset({"signup", "email_confirm"})  # the kinds that may resolve to an opt-in mail


async def site(db: AsyncSession) -> templates.Site:
    contact = (await business.get_settings(db)).contact_email
    return templates.Site(base=tokens.base_url(), contact=contact, reply_to=bool(settings.mail_reply_to))


async def _holder(db: AsyncSession, canonical: str | None) -> User | None:
    """The account whose verified email this is."""
    if not canonical:
        return None
    user: User | None = await db.scalar(select(User).where(User.email_canonical == canonical))
    return user


async def _live_signup(db: AsyncSession, canonical: str | None, prefer: int | None = None) -> PendingSignup | None:
    """The given pending sign-up while it is live and for this address, else the newest live one for the address."""
    now = utcnow()
    if prefer is not None:
        row = await db.get(PendingSignup, prefer)
        if row is not None and row.canonical == canonical and row.expires_at > now:
            return row
    if not canonical:
        return None
    newest: PendingSignup | None = await db.scalar(
        select(PendingSignup)
        .where(PendingSignup.canonical == canonical, PendingSignup.expires_at > now)
        .order_by(PendingSignup.created_at.desc(), PendingSignup.id.desc())
        .limit(1)
    )
    return newest


async def _switch(db: AsyncSession, user_id: int, name: str) -> bool:
    """An email switch of the user's preferences: on unless they stored false (read leniently, as Preferences)."""
    row = await db.get(UserPreference, user_id)
    data = row.data if row is not None and isinstance(row.data, dict) else {}
    return data.get(name) is not False


async def _account(db: AsyncSession, job: EmailOutbox) -> User | None:
    return await db.get(User, job.user_id) if job.user_id is not None else None


async def _verification(db: AsyncSession, row: PendingSignup, here: templates.Site, *, finishing: bool) -> Outgoing:
    token = await tokens.mint(db, "signup", address_canonical=row.canonical, pending_signup_id=row.id)
    mail = templates.verification(here, link=tokens.link("signup", token), finishing=finishing)
    return Outgoing(row.address, mail, opt_in=not finishing)


# ---------------- Kinds ----------------


async def _signup(db: AsyncSession, job: EmailOutbox, here: templates.Site) -> Result:
    holder = await _holder(db, job.canonical)
    if holder is not None:
        if job.pending_signup_id is not None:
            await db.execute(delete(PendingSignup).where(PendingSignup.id == job.pending_signup_id))
        variant: templates.AlreadyVariant = (
            "off" if not holder.is_active else "password" if holder.password_hash else "google"
        )
        to = job.to_address or holder.email or ""
        return Outgoing(to, templates.already_registered(here, address=to, variant=variant))
    row = await _live_signup(db, job.canonical, job.pending_signup_id)
    if row is None:
        return Skip("pending_gone")
    return await _verification(db, row, here, finishing=False)


async def _password_reset(db: AsyncSession, job: EmailOutbox, here: templates.Site) -> Result:
    user = await _account(db, job) if job.user_id is not None else await _holder(db, job.canonical)
    if user is not None:
        address, canonical = user.email, user.email_canonical
        if not user.is_active or user.is_admin or not address or not canonical or canonical != job.canonical:
            return Skip("no_account")
        token = await tokens.mint(db, "reset", address_canonical=canonical, user_id=user.id)
        link = tokens.link("reset", token)
        return Outgoing(
            address, templates.reset(here, address=address, link=link, has_password=bool(user.password_hash))
        )
    row = None if job.user_id is not None else await _live_signup(db, job.canonical)
    if row is None:
        return Skip("no_account")
    return await _verification(db, row, here, finishing=True)


async def _email_confirm(db: AsyncSession, job: EmailOutbox, here: templates.Site) -> Result:
    row = await db.get(PendingEmail, job.pending_email_id) if job.pending_email_id is not None else None
    if row is None or row.canonical != job.canonical or row.expires_at <= utcnow():
        return Skip("pending_gone")
    user = await db.get(User, row.user_id)
    if user is None or not user.is_active:
        return Skip("no_account")
    token = await tokens.mint(
        db, "email", address_canonical=row.canonical, user_id=row.user_id, pending_email_id=row.id
    )
    source: Literal["user", "google"] = "google" if row.source == "google" else "user"
    mail = templates.email_confirm(here, address=row.address, link=tokens.link("email", token), source=source)
    return Outgoing(row.address, mail, pending_email_id=row.id, opt_in=True)


async def _email_changed(db: AsyncSession, job: EmailOutbox, here: templates.Site) -> Result:
    user = await _account(db, job)
    if user is None or not job.to_address or not job.canonical:
        return Skip("no_account")
    if user.email_canonical == job.canonical:
        return Skip("changed_back")
    undo = None
    if user.is_active and not await tokens.has_live(db, "email_undo", user_id=user.id):
        token = await tokens.mint(db, "email_undo", address_canonical=job.canonical, user_id=user.id)
        undo = tokens.link("email_undo", token)
    new = emails.mask(user.email) if user.email else "a new address"
    mail = templates.email_changed(here, old=job.to_address, new_masked=new, at=job.created_at, undo_link=undo)
    return Outgoing(job.to_address, mail)


async def _email_undone(db: AsyncSession, job: EmailOutbox, here: templates.Site) -> Result:
    user = await _account(db, job)
    if user is None or not user.email or not job.to_address:
        return Skip("no_account")
    mail = templates.email_undone(here, restored_masked=emails.mask(user.email), at=job.created_at)
    return Outgoing(job.to_address, mail)


def _param(job: EmailOutbox, name: str) -> object:
    return (job.params or {}).get(name)


async def _notice(db: AsyncSession, job: EmailOutbox, here: templates.Site) -> Result:
    """The security notices: to the account's verified email now (none: nothing)."""
    user = await _account(db, job)
    if user is None or not user.email:
        return Skip("no_email")
    address, at = user.email, job.created_at
    if job.kind == "password_changed":
        disconnected = _param(job, "google_disconnected") is True
        return Outgoing(
            address, templates.password_changed(here, address=address, at=at, google_disconnected=disconnected)
        )
    if job.kind == "password_added":
        return Outgoing(address, templates.password_added(here, address=address, at=at))
    google = _param(job, "google_email")
    named = google if isinstance(google, str) and google else None
    if job.kind == "google_linked":
        if named is None:
            identity = await db.scalar(select(UserIdentity).where(UserIdentity.user_id == user.id))
            named = emails.mask(identity.email_at_link) if identity is not None else None
        return Outgoing(address, templates.google_linked(here, address=address, google=named, at=at))
    return Outgoing(address, templates.google_unlinked(here, address=address, google=named, at=at))


async def _receipt(db: AsyncSession, job: EmailOutbox, here: templates.Site) -> Result:
    user = await _account(db, job)
    if user is None or not user.email:
        return Skip("no_email")
    if not await _switch(db, user.id, "email_receipts"):
        return Skip("pref_off")
    receipt_id = _param(job, "receipt_id")
    receipt = await db.get(BillingReceipt, receipt_id) if isinstance(receipt_id, int) else None
    if receipt is None or receipt.user_id != user.id:
        return Skip("receipt_gone")
    facts = await receipts.facts(db, receipt)
    filename, pdf = await receipts.pdf_file(db, receipt)
    mail = templates.receipt(
        here, number=receipt.number, plan=facts.plan, amount=facts.amount, period=facts.period, method=facts.method
    )
    return Outgoing(user.email, mail, attachments=(Attachment(filename, pdf),))


async def _plan_reminder(db: AsyncSession, job: EmailOutbox, here: templates.Site) -> Result:
    user = await _account(db, job)
    if user is None or not user.email or not user.is_active:
        return Skip("no_email")
    if not await _switch(db, user.id, "email_plan_reminders"):
        return Skip("pref_off")
    which = _param(job, "reminder")
    now = utcnow()
    last = await reminders.chain_end(db, user, now)
    if (
        which not in get_args(templates.Reminder)
        or last is None
        or last.id != _param(job, "period_id")
        or reminders.due(last.ends_at, now) is None
    ):
        return Skip("not_due")
    seller = await business.get_settings(db)
    mail = templates.plan_reminder(
        here,
        which=which,  # type: ignore[arg-type]
        plan=periods.snapshot(last).name,
        last_day=periods.valid_through(last.ends_at),
        business_name=seller.business_name,
    )
    return Outgoing(user.email, mail)


async def _test(db: AsyncSession, job: EmailOutbox, here: templates.Site) -> Result:
    if not job.to_address:
        return Skip("no_address")
    return Outgoing(job.to_address, templates.admin_test(here))


_KINDS: Final[dict[str, Callable[[AsyncSession, EmailOutbox, templates.Site], Awaitable[Result]]]] = {
    "signup": _signup,
    "password_reset": _password_reset,
    "email_confirm": _email_confirm,
    "email_changed": _email_changed,
    "email_undone": _email_undone,
    "password_changed": _notice,
    "password_added": _notice,
    "google_linked": _notice,
    "google_unlinked": _notice,
    "receipt": _receipt,
    "plan_reminder": _plan_reminder,
    "test": _test,
}


async def resolve(db: AsyncSession, job: EmailOutbox) -> Result:
    """What `job` becomes now (may mint a token; the caller commits before sending)."""
    handler = _KINDS.get(job.kind)
    if handler is None:
        return Skip("unknown_kind")
    return await handler(db, job, await site(db))
