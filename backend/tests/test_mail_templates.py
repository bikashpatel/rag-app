"""The emails' words and frame (app.services.mail.templates; docs/briefs/signup-auth.md 6.4 and S24, the frame of
signup-auth-visual.md 10): every kind renders a text part and the one HTML frame; token mails have one button with the
link written out under it; no image, remote resource or tracking; dates in IST; the reply line only with a Reply-To;
each link (and the contact email) on its own line, never ending a sentence, and an anchor in the HTML; what users typed
is escaped in the HTML."""

import re
from collections.abc import Callable
from datetime import date, datetime, timezone
from html import escape

import pytest

from app.services.mail import templates
from app.services.mail.templates import Rendered, Site

BASE = "https://asknuvexusdoc.com"
SITE = Site(base=BASE, contact="support@asknuvexusdoc.com")
AT = datetime(2026, 11, 25, 8, 35, tzinfo=timezone.utc)  # 14:05 IST
LINK = f"{BASE}/verify-email#t=Qm9vS2VlcGVyLTJ4V3E3bF9rUnRvLVlhMk1TbFhqQ0E"

KINDS: dict[str, Callable[[Site], Rendered]] = {
    "verification": lambda site: templates.verification(site, link=LINK),
    "finishing": lambda site: templates.verification(site, link=LINK, finishing=True),
    "already_password": lambda site: templates.already_registered(site, address="ann@example.com", variant="password"),
    "already_google": lambda site: templates.already_registered(site, address="ann@example.com", variant="google"),
    "already_off": lambda site: templates.already_registered(site, address="ann@example.com", variant="off"),
    "reset": lambda site: templates.reset(site, address="ann@example.com", link=LINK, has_password=True),
    "set_password": lambda site: templates.reset(site, address="ann@example.com", link=LINK, has_password=False),
    "password_changed": lambda site: templates.password_changed(site, address="ann@example.com", at=AT),
    "password_added": lambda site: templates.password_added(site, address="ann@example.com", at=AT),
    "email_confirm": lambda site: templates.email_confirm(site, address="ann@example.com", link=LINK, source="user"),
    "email_confirm_google": lambda site: templates.email_confirm(
        site, address="ann@example.com", link=LINK, source="google"
    ),
    "email_changed": lambda site: templates.email_changed(
        site, old="ann@example.com", new_masked="b***@example.org", at=AT, undo_link=LINK
    ),
    "email_changed_again": lambda site: templates.email_changed(
        site, old="ann@example.com", new_masked="b***@example.org", at=AT, undo_link=None
    ),
    "email_undone": lambda site: templates.email_undone(site, restored_masked="a***@example.com", at=AT),
    "google_linked": lambda site: templates.google_linked(
        site, address="ann@example.com", google="a***@gmail.com", at=AT
    ),
    "google_unlinked": lambda site: templates.google_unlinked(site, address="ann@example.com", google=None, at=AT),
    "receipt": lambda site: templates.receipt(
        site, number="RA/26-27/000001", plan="Starter", amount="₹500.00", period="1 Nov – 30 Nov 2026", method="UPI"
    ),
    "plan_reminder": lambda site: templates.plan_reminder(
        site, which="soon", plan="Starter", last_day=date(2026, 11, 30), business_name="AskNuvexusDoc"
    ),
    "test": templates.admin_test,
}
BUTTONS = {
    "verification": "Finish sign-up",
    "finishing": "Finish sign-up",
    "already_password": "Sign in",
    "already_google": "Sign in",
    "reset": "Choose a new password",
    "set_password": "Set a password",
    "email_confirm": "Confirm this email",
    "email_confirm_google": "Confirm this email",
    "email_changed": "This wasn't me: undo it",
}


def buttons(html: str) -> list[tuple[str, str]]:
    return re.findall(r'<a href="([^"]+)" style="display:inline-block;[^"]*">([^<]+)</a>', html)


@pytest.mark.parametrize("kind", KINDS)
def test_every_kind_renders_both_parts_in_the_frame(kind: str) -> None:
    subject, text, html = KINDS[kind](SITE)

    assert subject and "\n" not in subject and not subject.startswith("[")
    assert html.startswith("<!doctype html>") and '<meta name="color-scheme" content="light">' in html
    assert f"<title>{subject.replace("'", '&#x27;')}</title>" in html
    assert "max-width:560px" in html and "AskNuvexusDoc</td>" in html  # the brand row and the card
    assert '<div style="display:none;max-height:0;overflow:hidden;">' in html  # the preheader
    assert "AskNuvexusDoc · support@asknuvexusdoc.com · asknuvexusdoc.com" in text
    assert 'href="mailto:support@asknuvexusdoc.com"' in html and f'href="{BASE}"' in html
    first = text.split("\n\n")[0].splitlines()[0]
    assert first.replace("'", "&#x27;").replace("&", "&amp;").replace("&amp;#x27;", "&#x27;") in html
    assert text.endswith("\n") and "<p" not in text and "--\n" in text
    assert "Questions? Reply to this email." not in text + html


@pytest.mark.parametrize("kind", KINDS)
def test_at_most_one_button_with_the_link_written_out(kind: str) -> None:
    _, text, html = KINDS[kind](SITE)

    found = buttons(html)
    if kind not in BUTTONS:
        assert found == [] and "Or paste this link" not in html
        return
    ((link, label),) = found
    assert label.replace("&#x27;", "'") == BUTTONS[kind]
    assert "Or paste this link into your browser:" in html
    assert html.count(f">{link}</p>") == 1  # written out under the button
    assert f"{label.replace('&#x27;', "'")}:\n{link}\n" in text  # its own line in the text part


@pytest.mark.parametrize("kind", KINDS)
def test_no_image_remote_resource_or_tracking(kind: str) -> None:
    _, _, html = KINDS[kind](SITE)
    lowered = html.lower()

    for forbidden in ("<img", "src=", "url(", "<link", "<script", "@import", "utm_", "tracking", "pixel"):
        assert forbidden not in lowered, forbidden
    for href in re.findall(r'href="([^"]+)"', html):
        assert href.startswith((BASE, "mailto:")), href


def test_token_links_point_at_the_origin_given() -> None:
    _, text, html = templates.reset(SITE, address="ann@example.com", link=LINK, has_password=True)

    assert re.findall(r"https?://\S+", text) == [LINK]
    assert {link for link, _ in buttons(html)} == {LINK}


def test_dates_are_written_in_ist() -> None:
    assert templates.when(AT) == "25 Nov 2026, 14:05 IST"
    _, text, _ = templates.password_changed(SITE, address="ann@example.com", at=AT)

    assert "The password for ann@example.com was changed on 25 Nov 2026, 14:05 IST." in text


def test_the_reply_line_only_with_a_reply_to() -> None:
    _, text, html = templates.admin_test(Site(base=BASE, reply_to=True))

    assert text.endswith("Questions? Reply to this email.\n")
    assert "Questions? Reply to this email." in html


def test_without_a_contact_email_the_footer_has_none_and_mails_point_at_the_contact_page() -> None:
    _, text, html = templates.password_added(Site(base=BASE), address="ann@example.com", at=AT)

    assert "AskNuvexusDoc · asknuvexusdoc.com\n" in text and "mailto:" not in html
    assert f"If that wasn't you, contact us right away:\n{BASE}/contact\n" in text
    assert f'<a href="{BASE}/contact" style="color:#9a4423;">{BASE}/contact</a>' in html


@pytest.mark.parametrize("site", [SITE, Site(base=BASE)], ids=["contact-email", "contact-page"])
@pytest.mark.parametrize("kind", KINDS)
def test_links_and_the_contact_never_end_a_sentence(kind: str, site: Site) -> None:
    _, text, html = KINDS[kind](site)
    body = text.split("\n\n--\n")[0]  # the footer's first line names the contact between dots

    for line in body.splitlines():
        for found in re.findall(r"https?://\S+|support@asknuvexusdoc\.com", line):
            assert line == found, line  # on a line of its own: no full stop sticks to it
            href = found if "://" in found else f"mailto:{found}"
            assert f'<a href="{escape(href)}"' in html, found


def test_the_verification_copy() -> None:
    subject, text, _ = templates.verification(SITE, link=LINK)
    _, finishing, _ = templates.verification(SITE, link=LINK, finishing=True)

    assert subject == "Finish creating your AskNuvexusDoc account"
    assert text.startswith("Confirm your email to finish signing up for AskNuvexusDoc.\n\nFinish sign-up:\n")
    assert "The link works for 24 hours. In the browser where you signed up, one click finishes;" in text
    assert "Didn't sign up? Ignore this email; no account is created." in text
    assert "this address was entered at asknuvexusdoc.com/signup." in text
    assert finishing.startswith("You asked to reset your password, but your account isn't created yet.\n\n")
    assert "asknuvexusdoc.com/forgot-password." in finishing


def test_the_already_registered_variants() -> None:
    _, password, _ = templates.already_registered(SITE, address="ann@example.com", variant="password")
    _, google, _ = templates.already_registered(SITE, address="ann@example.com", variant="google")
    subject, off, html = templates.already_registered(SITE, address="ann@example.com", variant="off")

    assert subject == "You already have an AskNuvexusDoc account"
    first = "Someone, hopefully you, tried to sign up with ann@example.com, but it already has an account."
    assert all(text.startswith(first) for text in (password, google, off))
    assert f"Forgot your password? Reset it here:\n{BASE}/forgot-password\n" in password
    assert "Sign in with Continue with Google, or set a password with Forgot password." in google
    assert "This account is turned off. Contact us if you need it:\nsupport@asknuvexusdoc.com\n" in off
    assert "Sign in:" not in off and buttons(html) == []
    assert "Didn't try to sign up? Ignore this email; nothing was changed." in off


def test_the_reset_and_set_password_copy() -> None:
    subject, reset, _ = templates.reset(SITE, address="ann@example.com", link=LINK, has_password=True)
    _, first, _ = templates.reset(SITE, address="ann@example.com", link=LINK, has_password=False)

    assert subject == "Reset your AskNuvexusDoc password"
    assert reset.startswith("Reset the password for ann@example.com.\n\nChoose a new password:\n")
    assert first.startswith("Set a password for ann@example.com.\n\nSet a password:\n")
    assert "The link works for 30 minutes. Resetting signs you out everywhere." in reset
    assert "Didn't ask for this? Ignore this email; your password stays the same." in reset


def test_the_security_notices() -> None:
    _, changed, _ = templates.password_changed(SITE, address="ann@example.com", at=AT, google_disconnected=True)
    _, undo, _ = templates.email_changed(SITE, old="ann@example.com", new_masked="b***@x.org", at=AT, undo_link=LINK)
    _, later, _ = templates.email_changed(SITE, old="ann@example.com", new_masked="b***@x.org", at=AT, undo_link=None)
    _, undone, _ = templates.email_undone(SITE, restored_masked="a***@example.com", at=AT)
    _, linked, _ = templates.google_linked(SITE, address="ann@example.com", google="a***@gmail.com", at=AT)
    _, unlinked, _ = templates.google_unlinked(SITE, address="ann@example.com", google=None, at=AT)

    assert "Google sign-in was disconnected." in changed
    assert (
        f"If that wasn't you, reset your password now:\n{BASE}/forgot-password\n\n"
        "Then contact us:\nsupport@asknuvexusdoc.com\n" in changed
    )
    assert "changed from ann@example.com to b***@x.org on 25 Nov 2026, 14:05 IST." in undo
    assert "The link works for 7 days. It puts ann@example.com back, signs out every session" in undo
    assert "undo it:" not in later
    assert "If that wasn't you, contact us right away:\nsupport@asknuvexusdoc.com\n" in later
    assert "If that wasn't you, contact us right away:\nsupport@asknuvexusdoc.com\n" in undone
    assert "changed back to a***@example.com on 25 Nov 2026, 14:05 IST, and every session was signed out." in undone
    assert "a***@gmail.com can now sign in to your account (ann@example.com). Added on 25 Nov 2026" in linked
    assert (
        "If that wasn't you, sign in, disconnect it in Settings → Account, change your password and contact us:\n"
        "support@asknuvexusdoc.com\n" in linked
    )
    assert "A Google account can no longer sign in to your account (ann@example.com)." in unlinked
    assert "If that wasn't you, sign in, change your password and contact us:\nsupport@asknuvexusdoc.com\n" in unlinked


def test_the_confirmation_wording_by_source() -> None:
    subject, user, _ = templates.email_confirm(SITE, address="ann@example.com", link=LINK, source="user")
    _, google, _ = templates.email_confirm(SITE, address="ann@example.com", link=LINK, source="google")

    assert subject == "Confirm your new AskNuvexusDoc email"
    assert user.startswith("Confirm ann@example.com as the email of your AskNuvexusDoc account.")
    assert google.startswith("You signed up with Google using this address. Confirm it so we can send you receipts")
    assert "We'll ask you to sign in to that account first. The link works for 24 hours." in user


def test_the_billing_mails() -> None:
    subject, receipt, _ = KINDS["receipt"](SITE)
    reminder_subject, reminder, _ = KINDS["plan_reminder"](SITE)

    assert subject == "Receipt RA/26-27/000001 for your Starter plan"
    assert "Amount: ₹500.00\n\nPeriod: 1 Nov – 30 Nov 2026\n\nMethod: UPI" in receipt
    assert "The receipt is attached and in Settings → Plan & billing." in receipt
    assert "Turn receipt emails off in Settings → Plan & billing." in receipt
    assert reminder_subject == "Your Starter plan ends in 5 days"
    assert "Your Starter plan on AskNuvexusDoc ends on 30 Nov 2026 (IST)." in reminder
    assert f"Renew it, or choose another plan, here:\n{BASE}/plans\n" in reminder
    assert "Your chats and documents are kept." in reminder
    assert "Turn plan reminder emails off in Settings → Plan & billing." in reminder


def test_what_users_typed_is_escaped_in_the_html() -> None:
    address = "o'brien&co@example.com"
    _, text, html = templates.already_registered(SITE, address=address, variant="password")

    assert address in text
    assert "o&#x27;brien&amp;co@example.com" in html and address not in html
