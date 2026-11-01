"""The emails' words and frame (docs/briefs/signup-auth.md 6.4; the frame of signup-auth-visual.md 10): one function per
kind returns (subject, text, html). The text part keeps the brief's sentences with each link on its own line; the HTML
is one frame for every kind (inline styles, tables, at most one button with the link written out under it) with no
image, font, remote resource or tracking. A link (or the contact email) never ends a sentence: it follows its sentence
on a line of its own. Mails name the account by its address, never by its username."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from html import escape
from typing import Literal, NamedTuple
from urllib.parse import urlsplit

from app.config import settings
from app.services.billing.money import IST, format_ist_date

AlreadyVariant = Literal["password", "google", "off"]
Reminder = Literal["soon", "tomorrow", "ended"]
_SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
_SERIF = "Georgia,'Times New Roman',serif"
_INK, _INK2, _INK3, _CLAY, _LINK = "#2a2520", "#5a5249", "#6e655a", "#a94d2b", "#9a4423"
_DESK, _CARD, _HAIR = "#f3f0e8", "#fffdf9", "#e5dfd3"
_MARK = "N"  # the logo's letter, in the brand row's clay square


class Rendered(NamedTuple):
    subject: str
    text: str
    html: str


@dataclass(frozen=True)
class Site:
    """What every mail needs from the setup: the links' origin (the first https entry of PUBLIC_BASE_URLS), the
    business's contact email (Admin → Billing → Business & payments) and whether replies reach someone."""

    base: str
    contact: str | None = None
    reply_to: bool = False

    @property
    def host(self) -> str:
        return urlsplit(self.base).netloc

    @property
    def reach(self) -> str:
        """Who to contact: the business's email, else the contact page."""
        return self.contact or f"{self.base}/contact"


Line = str | tuple[str, str]  # a paragraph, or a sentence ending in ":" and its link (a URL or an email address)


@dataclass(frozen=True)
class _Mail:
    subject: str
    title: str  # the subject's short form, the card's heading
    lead: Sequence[Line]  # before the button
    why: str  # the footer line saying why the mail came
    button: tuple[str, str] | None = None  # label, link
    after: Sequence[Line] = field(default_factory=list)  # under the written-out link
    warning: Sequence[Line] = field(default_factory=list)  # under a hairline


def when(moment: datetime) -> str:
    """ "25 Nov 2026, 14:05 IST"."""
    local = moment.astimezone(IST)
    return f"{format_ist_date(local.date())}, {local:%H:%M} IST"


def _brand() -> str:
    return settings.app_name


def _footer(site: Site, why: str) -> list[str]:
    first = " · ".join(part for part in (_brand(), site.contact, site.host) if part)
    return [first, why, *(["Questions? Reply to this email."] if site.reply_to else [])]


def _plain(line: Line) -> str:
    return line if isinstance(line, str) else "\n".join(line)


def _text(site: Site, mail: _Mail) -> str:
    blocks = [_plain(line) for line in mail.lead]
    if mail.button:
        label, link = mail.button
        blocks.append(f"{label}:\n{link}")
    blocks += [_plain(line) for line in [*mail.after, *mail.warning]]
    blocks.append("\n".join(["--", *_footer(site, mail.why)]))
    return "\n\n".join(blocks) + "\n"


def _p(text: str, *, size: int = 16, color: str = _INK, margin: str = "0 0 14px", extra: str = "") -> str:
    style = f"margin:{margin};font-family:{_SANS};font-size:{size}px;line-height:1.55;color:{color};{extra}"
    return f'<p style="{style}">{text}</p>'


def _a(link: str, label: str | None = None, color: str = _LINK) -> str:
    return f'<a href="{escape(link)}" style="color:{color};">{escape(label or link)}</a>'


def _rich(line: Line) -> str:
    """A line's HTML: its sentence, then its link as an anchor of its own (an email address as mailto:)."""
    if isinstance(line, str):
        return escape(line)
    sentence, link = line
    href = f"mailto:{link}" if "@" in link and "://" not in link else link
    return f"{escape(sentence)}<br>{_a(href, link)}"


def _html(site: Site, mail: _Mail, preheader: str) -> str:
    body = [
        f'<h1 style="margin:0 0 14px;font-family:{_SERIF};font-weight:normal;font-size:26px;line-height:1.2;'
        f'color:{_INK};">{escape(mail.title)}</h1>',
        *(_p(_rich(line)) for line in mail.lead),
    ]
    if mail.button:
        label, link = mail.button
        body += [
            '<table role="presentation" cellpadding="0" cellspacing="0" style="margin:10px 0 0;"><tr>'
            f'<td style="background:{_CLAY};border-radius:10px;"><a href="{escape(link)}" style="display:inline-block;'
            f"padding:13px 22px;font-family:{_SANS};font-size:16px;font-weight:bold;color:#ffffff;"
            f'text-decoration:none;">{escape(label)}</a></td></tr></table>',
            _p("Or paste this link into your browser:", size=14, color=_INK2, margin="22px 0 6px"),
            f'<p style="margin:0 0 18px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:13px;'
            f'line-height:1.5;color:{_LINK};word-break:break-all;">{escape(link)}</p>',
        ]
    body += [_p(_rich(line), size=14, color=_INK2) for line in mail.after]
    for number, line in enumerate(mail.warning):
        hairline = f"padding-top:18px;border-top:1px solid {_HAIR};" if number == 0 else ""
        margin = "4px 0 0" if number == 0 else "10px 0 0"
        body.append(_p(_rich(line), size=14, color=_INK2, margin=margin, extra=hairline))
    first, *rest = _footer(site, mail.why)
    contact = _a(f"mailto:{site.contact}", site.contact, _INK3) if site.contact else None
    first_html = " · ".join(part for part in (escape(_brand()), contact, _a(site.base, site.host, _INK3)) if part)
    footer = "<br>".join([first_html, *(escape(line) for line in rest)])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light"><meta name="supported-color-schemes" content="light">
<title>{escape(mail.subject)}</title></head>
<body style="margin:0;padding:0;background:{_DESK};">
<div style="display:none;max-height:0;overflow:hidden;">{escape(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{_DESK};">
<tr><td align="center" style="padding:32px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;">
<tr><td style="padding:0 4px 18px;"><table role="presentation" cellpadding="0" cellspacing="0"><tr>
<td style="width:28px;height:28px;background:{_CLAY};border-radius:8px;text-align:center;vertical-align:middle;\
color:#ffffff;font-family:{_SERIF};font-style:italic;font-weight:bold;font-size:18px;line-height:28px;">\
{_MARK}</td>
<td style="padding-left:10px;font-family:{_SERIF};font-weight:bold;font-size:18px;color:{_INK};">{escape(_brand())}</td>
</tr></table></td></tr>
<tr><td style="background:{_CARD};border:1px solid {_HAIR};border-radius:14px;padding:34px 34px 30px;">
{chr(10).join(body)}
</td></tr>
<tr><td style="padding:18px 6px 0;font-family:{_SANS};font-size:13px;line-height:1.55;color:{_INK3};">
{footer}
</td></tr>
</table></td></tr></table>
</body></html>
"""


def _urgent(site: Site) -> Line:
    return ("If that wasn't you, contact us right away:", site.reach)


def _render(site: Site, mail: _Mail) -> Rendered:
    sentences = [line for line in [*mail.lead[1:], *mail.after, *mail.lead[:1]] if isinstance(line, str)]
    preheader = sentences[0] if sentences else mail.title
    return Rendered(mail.subject, _text(site, mail), _html(site, mail, preheader))


# ---------------- Sign-up ----------------


def verification(site: Site, *, link: str, finishing: bool = False) -> Rendered:
    """The sign-up link; `finishing`: asked for through Forgot password before the account exists."""
    lead = [f"Confirm your email to finish signing up for {_brand()}."]
    if finishing:
        lead.insert(0, "You asked to reset your password, but your account isn't created yet.")
    page = "/forgot-password" if finishing else "/signup"
    return _render(
        site,
        _Mail(
            subject=f"Finish creating your {_brand()} account",
            title="Confirm your email",
            lead=lead,
            button=("Finish sign-up", link),
            after=[
                "The link works for 24 hours. In the browser where you signed up, one click finishes; anywhere else, "
                "you'll be asked for the password you chose."
            ],
            warning=["Didn't sign up? Ignore this email; no account is created."],
            why=f"You get this email because this address was entered at {site.host}{page}.",
        ),
    )


def already_registered(site: Site, *, address: str, variant: AlreadyVariant) -> Rendered:
    """Someone signed up with an address that already has an account: active with a password, Google only, or off."""
    lead: list[Line] = [f"Someone, hopefully you, tried to sign up with {address}, but it already has an account."]
    after: list[Line] = []
    button = None
    if variant == "off":
        lead.append(("This account is turned off. Contact us if you need it:", site.reach))
    else:
        button = ("Sign in", f"{site.base}/login")
        after.append(
            ("Forgot your password? Reset it here:", f"{site.base}/forgot-password")
            if variant == "password"
            else "Sign in with Continue with Google, or set a password with Forgot password."
        )
    return _render(
        site,
        _Mail(
            subject=f"You already have an {_brand()} account",
            title="You already have an account",
            lead=lead,
            button=button,
            after=after,
            warning=["Didn't try to sign up? Ignore this email; nothing was changed."],
            why=f"You get this email because this address was entered at {site.host}/signup.",
        ),
    )


# ---------------- Passwords ----------------


def reset(site: Site, *, address: str, link: str, has_password: bool) -> Rendered:
    action = "Reset the password" if has_password else "Set a password"
    return _render(
        site,
        _Mail(
            subject=f"Reset your {_brand()} password",
            title="Reset your password" if has_password else "Set a password",
            lead=[f"{action} for {address}."],
            button=("Choose a new password" if has_password else "Set a password", link),
            after=["The link works for 30 minutes. Resetting signs you out everywhere."],
            warning=["Didn't ask for this? Ignore this email; your password stays the same."],
            why="You get this email because a password reset was asked for the account with this address.",
        ),
    )


def password_changed(site: Site, *, address: str, at: datetime, google_disconnected: bool = False) -> Rendered:
    lead: list[Line] = [f"The password for {address} was changed on {when(at)}."]
    if google_disconnected:
        lead.append("Google sign-in was disconnected.")
    return _render(
        site,
        _Mail(
            subject=f"Your {_brand()} password was changed",
            title="Your password was changed",
            lead=lead,
            warning=[
                ("If that wasn't you, reset your password now:", f"{site.base}/forgot-password"),
                ("Then contact us:", site.reach),
            ],
            why=f"You get this email because it is about the security of your {_brand()} account.",
        ),
    )


def password_added(site: Site, *, address: str, at: datetime) -> Rendered:
    return _render(
        site,
        _Mail(
            subject=f"A password was added to your {_brand()} account",
            title="A password was added",
            lead=[
                f"You can now sign in with {address} and a password, or keep using Google. Added on {when(at)}.",
            ],
            warning=[_urgent(site)],
            why=f"You get this email because it is about the security of your {_brand()} account.",
        ),
    )


# ---------------- The account's email ----------------


def email_confirm(site: Site, *, address: str, link: str, source: Literal["user", "google"]) -> Rendered:
    lead = (
        "You signed up with Google using this address. Confirm it so we can send you receipts and reminders and you "
        "can reset your password."
        if source == "google"
        else f"Confirm {address} as the email of your {_brand()} account."
    )
    return _render(
        site,
        _Mail(
            subject=f"Confirm your new {_brand()} email",
            title="Confirm your email",
            lead=[lead],
            button=("Confirm this email", link),
            after=["We'll ask you to sign in to that account first. The link works for 24 hours."],
            warning=["Didn't ask for this? Ignore this email; nothing changes."],
            why=f"You get this email because this address was entered as the email of an {_brand()} account.",
        ),
    )


def email_changed(site: Site, *, old: str, new_masked: str, at: datetime, undo_link: str | None) -> Rendered:
    """To the old address; the undo link only with the chain's first change."""
    return _render(
        site,
        _Mail(
            subject=f"Your {_brand()} email address was changed",
            title="Your email address was changed",
            lead=[f"The email of your account changed from {old} to {new_masked} on {when(at)}."],
            button=("This wasn't me: undo it", undo_link) if undo_link else None,
            after=(
                [
                    f"The link works for 7 days. It puts {old} back, signs out every session and asks you to choose a "
                    "new password."
                ]
                if undo_link
                else []
            ),
            warning=[] if undo_link else [_urgent(site)],
            why=f"You get this email because this address was the email of your {_brand()} account.",
        ),
    )


def email_undone(site: Site, *, restored_masked: str, at: datetime) -> Rendered:
    """To the address the undo replaced."""
    return _render(
        site,
        _Mail(
            subject=f"Your {_brand()} email change was undone",
            title="Your email change was undone",
            lead=[
                f"The email of your account was changed back to {restored_masked} on {when(at)}, and every session "
                "was signed out."
            ],
            warning=[_urgent(site)],
            why=f"You get this email because this address was the email of an {_brand()} account.",
        ),
    )


# ---------------- Google sign-in ----------------


def google_linked(site: Site, *, address: str, google: str | None, at: datetime) -> Rendered:
    who = google or "A Google account"
    return _render(
        site,
        _Mail(
            subject=f"Google sign-in was added to your {_brand()} account",
            title="Google sign-in was added",
            lead=[f"{who} can now sign in to your account ({address}). Added on {when(at)}."],
            warning=[
                (
                    "If that wasn't you, sign in, disconnect it in Settings → Account, change your password and "
                    "contact us:",
                    site.reach,
                )
            ],
            why=f"You get this email because it is about the security of your {_brand()} account.",
        ),
    )


def google_unlinked(site: Site, *, address: str, google: str | None, at: datetime) -> Rendered:
    who = google or "A Google account"
    return _render(
        site,
        _Mail(
            subject=f"Google sign-in was removed from your {_brand()} account",
            title="Google sign-in was removed",
            lead=[f"{who} can no longer sign in to your account ({address}). Removed on {when(at)}."],
            warning=[("If that wasn't you, sign in, change your password and contact us:", site.reach)],
            why=f"You get this email because it is about the security of your {_brand()} account.",
        ),
    )


# ---------------- Billing ----------------


def receipt(site: Site, *, number: str, plan: str, amount: str, period: str | None, method: str | None) -> Rendered:
    details = [f"Amount: {amount}"]
    if period:
        details.append(f"Period: {period}")
    if method:
        details.append(f"Method: {method}")
    return _render(
        site,
        _Mail(
            subject=f"Receipt {number} for your {plan} plan",
            title="Your receipt",
            lead=[f"Thank you for your payment. Receipt {number} for your {plan} plan:", *details],
            after=["The receipt is attached and in Settings → Plan & billing."],
            why="Turn receipt emails off in Settings → Plan & billing.",
        ),
    )


def plan_reminder(site: Site, *, which: Reminder, plan: str, last_day: date, business_name: str) -> Rendered:
    """The plans brief's 4.7 reminders: "ends in N days", "ends tomorrow" and "has ended"."""
    days = settings.billing_reminder_days
    subject = {
        "soon": f"Your {plan} plan ends in {days} day{'' if days == 1 else 's'}",
        "tomorrow": f"Your {plan} plan ends tomorrow",
        "ended": f"Your {plan} plan has ended",
    }[which]
    verb = "ended" if which == "ended" else "ends"
    return _render(
        site,
        _Mail(
            subject=subject,
            title=subject,
            lead=[
                f"Your {plan} plan on {business_name} {verb} on {format_ist_date(last_day)} (IST).",
                ("Renew it, or choose another plan, here:", f"{site.base}/plans"),
            ],
            after=["Your chats and documents are kept."],
            why="Turn plan reminder emails off in Settings → Plan & billing.",
        ),
    )


def admin_test(site: Site) -> Rendered:
    return _render(
        site,
        _Mail(
            subject=f"Test email from {_brand()}",
            title="Test email",
            lead=[f"Mail from {_brand()} reaches this address."],
            why="You get this email because the administrator sent a test to this address.",
        ),
    )
