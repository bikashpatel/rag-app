"""Encryption of users' OAuth tokens at rest (brief 5.3).

TOKEN_ENCRYPTION_KEY holds one or more comma-separated Fernet keys: the first encrypts, all decrypt (MultiFernet). It is
independent of SECRET_KEY, so signing everyone out doesn't break their connections. The plaintext binds the token to
its user and plugin, so a ciphertext copied into another row doesn't decrypt there.
"""

import hashlib
import json
import logging
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import PluginConnection

logger = logging.getLogger(__name__)


class TokenUnreadable(Exception):
    """No configured key decrypts the token, or it belongs to another user or plugin."""


def key_id(key: bytes) -> str:
    """The first 16 hex digits of the key's SHA-256: identifies it without revealing it."""
    return hashlib.sha256(key).hexdigest()[:16]


class TokenCipher:
    def __init__(self, keys: list[bytes]) -> None:
        self._multi = MultiFernet([Fernet(key) for key in keys])
        self.key_id = key_id(keys[0])
        self.key_ids = tuple(key_id(key) for key in keys)

    def encrypt(self, user_id: int, plugin_id: str, token: str) -> str:
        plaintext = json.dumps({"u": user_id, "p": plugin_id, "t": token}, separators=(",", ":")).encode()
        return self._multi.encrypt(plaintext).decode("ascii")

    def decrypt(self, user_id: int, plugin_id: str, ciphertext: str) -> str:
        try:
            data = json.loads(self._multi.decrypt(ciphertext.encode("ascii")))
        except (InvalidToken, ValueError, UnicodeError) as exc:
            raise TokenUnreadable from exc
        if not isinstance(data, dict) or data.get("u") != user_id or data.get("p") != plugin_id:
            raise TokenUnreadable
        token = data.get("t")
        if not isinstance(token, str):
            raise TokenUnreadable
        return token

    def rotate(self, ciphertext: str) -> str:
        """The same plaintext (and so the same binding) encrypted under the first key."""
        try:
            return self._multi.rotate(ciphertext.encode("ascii")).decode("ascii")
        except (InvalidToken, UnicodeError) as exc:
            raise TokenUnreadable from exc

    def needs_rotation(self, row_key_id: str) -> bool:
        return row_key_id != self.key_id


@dataclass(frozen=True)
class _Built:
    source: str
    cipher: TokenCipher | None


_built: _Built | None = None


def token_cipher() -> TokenCipher | None:
    """The cipher for TOKEN_ENCRYPTION_KEY, built once per value; None when it is empty or any key is invalid (logged
    once, without the value)."""
    global _built
    source = settings.token_encryption_key
    if _built is None or _built.source != source:
        _built = _Built(source, _parse(source))
    return _built.cipher


def _parse(source: str) -> TokenCipher | None:
    keys = [part.strip().encode("ascii", "replace") for part in source.split(",") if part.strip()]
    if not keys:
        return None
    try:
        return TokenCipher(keys)
    except ValueError:
        logger.error(
            "TOKEN_ENCRYPTION_KEY is invalid (each key must be 32 bytes in URL-safe base64, 44 characters); plugins "
            "that connect accounts are off until it is fixed"
        )
        return None


async def rotate_all() -> tuple[int, int]:
    """At startup, before serving: re-encrypt every connection stored under another key than the first, keeping its
    binding. Returns (rotated, unreadable); rows no configured key reads are left as they are (re-adding the key
    recovers them; used meanwhile, they need a reconnect)."""
    cipher = token_cipher()
    if cipher is None:
        return 0, 0
    rotated = unreadable = 0
    async with SessionLocal() as db:
        rows = await db.scalars(select(PluginConnection).where(PluginConnection.key_id != cipher.key_id))
        for row in rows:
            try:
                row.refresh_token_enc = cipher.rotate(row.refresh_token_enc)
            except TokenUnreadable:
                unreadable += 1
                continue
            if row.access_token_enc is not None:
                try:
                    row.access_token_enc = cipher.rotate(row.access_token_enc)
                except TokenUnreadable:  # an access token is disposable: the next use refreshes it
                    row.access_token_enc = None
                    row.access_expires_at = None
            row.key_id = cipher.key_id
            rotated += 1
        await db.commit()
    if rotated or unreadable:
        logger.info(
            "Re-encrypted %d plugin connection(s) under the first TOKEN_ENCRYPTION_KEY; %d can't be read with any "
            "configured key",
            rotated,
            unreadable,
        )
    return rotated, unreadable
