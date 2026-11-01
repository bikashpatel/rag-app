"""Email addresses (docs/briefs/signup-auth.md 3.2): one parser for sign-up, resend, forgot, sign-in, changes and
Google's claims. The canonical form identifies an account; the delivery key only groups a mailbox's variants for
throttles. Redis keys and stored hashes use digest(), and logs mask(); never the address itself."""

import hashlib
import re
import unicodedata
from dataclasses import dataclass

import idna

INVALID = "Enter a valid email address."
MAX_LENGTH = 254
_LOCAL = re.compile(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]{1,64}")
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_TOP_LEVEL = re.compile(r"[a-z]+|xn--[a-z0-9-]+")
_GMAIL = ("gmail.com", "googlemail.com")


class EmailError(ValueError):
    """Not an address we accept; str() is the user-facing sentence."""

    def __init__(self) -> None:
        super().__init__(INVALID)


@dataclass(frozen=True)
class Email:
    address: str  # the local part as typed, "@", the domain in lowercase ASCII: what is stored and mailed to
    canonical: str  # the address lowercased: the key for sign-in, uniqueness and linking
    local: str
    domain: str
    delivery_key: str  # the canonical form without a +tag (and Gmail's dots): throttles only, never identity


def parse(value: str) -> Email:
    text = value.strip()
    if any(unicodedata.category(char) == "Cc" for char in text):
        raise EmailError()
    text = unicodedata.normalize("NFC", text)
    if len(text) > MAX_LENGTH or text.count("@") != 1:
        raise EmailError()
    local, _, domain = text.partition("@")
    if not _LOCAL.fullmatch(local) or local.startswith(".") or local.endswith(".") or ".." in local:
        raise EmailError()
    ascii_domain = _ascii_domain(domain)
    address = f"{local}@{ascii_domain}"
    if len(address) > MAX_LENGTH:
        raise EmailError()
    return Email(address, address.lower(), local, ascii_domain, _delivery_key(local.lower(), ascii_domain))


def _ascii_domain(domain: str) -> str:
    try:
        converted = idna.encode(domain, uts46=True, transitional=False).decode("ascii")
    except (idna.IDNAError, UnicodeError):
        raise EmailError() from None
    labels = converted.split(".")
    if len(labels) < 2 or not all(_LABEL.fullmatch(label) for label in labels):
        raise EmailError()
    if not _TOP_LEVEL.fullmatch(labels[-1]):
        raise EmailError()
    return converted


def _delivery_key(local: str, domain: str) -> str:
    local = local.split("+", 1)[0]
    if domain in _GMAIL:
        local, domain = local.replace(".", ""), "gmail.com"
    return f"{local}@{domain}"


def digest(value: str) -> str:
    """SHA-256 (hex) of a canonical address or a delivery key."""
    return hashlib.sha256(value.encode()).hexdigest()


def mask(address: str) -> str:
    """"j***@example.com": what logs, events and other people's views may show of an address."""
    local, at, domain = address.partition("@")
    return f"{local[:1]}***{at}{domain}"
