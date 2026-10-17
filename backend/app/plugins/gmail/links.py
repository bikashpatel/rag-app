"""Gmail web links (docs/briefs/plugins-gmail.md, P23 and 9.3). `/u/0/` is the browser's first signed-in Google
account, which may not be the connected one."""

_GMAIL_WEB = "https://mail.google.com/mail/u/0/"


def thread_url(thread_id: str) -> str:
    """A conversation (also a sent email's)."""
    return f"{_GMAIL_WEB}#all/{thread_id}"


def draft_url(message_id: str) -> str:
    """A draft, by the id of the draft's message."""
    return f"{_GMAIL_WEB}#drafts?compose={message_id}"
