"""Users' connections to plugin providers (brief 5.2 to 5.5 and 5.8): storing an OAuth result, access tokens (refresh,
expiry, revocation), status changes, disconnecting and revoking.

One asyncio lock per (user, plugin) serializes a connection's refresh, the OAuth callback's upsert and disconnects (the
backend is one process, brief 4.10). A refresh is written with a compare-and-set on the refresh token it started from,
so a refresh racing a reconnect or an account switch is discarded instead of overwriting the newer tokens.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Literal
from weakref import WeakValueDictionary

import httpx
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import PluginConnection, User, utcnow
from app.plugins import audit, crypto, http, registry
from app.plugins.base import (
    AccountChanged,
    AccountIdentity,
    ConnectionInfo,
    NotConnected,
    Origin,
    Plugin,
    PluginUnavailable,
    ReconnectNeeded,
    TokenResponse,
    reconnect_message,
)
from app.schemas_plugins import ConnectionReason, PluginConnectionStatus

logger = logging.getLogger(__name__)

REFRESH_MARGIN = timedelta(seconds=60)  # an access token closer to its expiry is refreshed first
TESTING_LIFETIME = timedelta(days=7)  # Google ends refresh tokens of apps in "Testing" after 7 days
_TESTING_EXPIRY_AGE = timedelta(days=6)  # invalid_grant from a connection this old in testing mode: expired_testing
_TOUCH_INTERVAL = timedelta(seconds=60)
_GONE = "This connection was removed. Connect again from the account menu (Connections)."

_locks: WeakValueDictionary[tuple[int, str], asyncio.Lock] = WeakValueDictionary()
_sleep = asyncio.sleep  # patched in tests
_client_rejected: set[str] = set()  # plugin ids whose OAuth client the provider rejected (logged once)
_unreadable_logged: set[int] = set()  # connection ids


def lock(user_id: int, plugin_id: str) -> asyncio.Lock:
    """The connection's lock (it lives while anyone holds a reference to it)."""
    key = (user_id, plugin_id)
    found = _locks.get(key)
    if found is None:
        found = _locks[key] = asyncio.Lock()
    return found


# ---------------- Views ----------------


def connection_info(row: PluginConnection) -> ConnectionInfo:
    return ConnectionInfo(
        id=row.id,
        user_id=row.user_id,
        plugin_id=row.plugin_id,
        account_email=row.account_email,
        account_subject=row.account_subject,
        scopes=frozenset(row.scopes or ()),
        status="connected" if row.status == "connected" else "reconnect_needed",
        status_reason=row.status_reason,  # type: ignore[arg-type]
        chat_enabled=row.chat_enabled,
        connected_at=row.connected_at,
    )


def refresh_expires_at(plugin: Plugin, connected_at: datetime) -> datetime | None:
    """When the provider ends the connection: in Google's testing mode, 7 days after the consent; else never."""
    return connected_at + TESTING_LIFETIME if plugin.testing_mode() else None


def testing_expired(plugin: Plugin, connected_at: datetime, now: datetime | None = None) -> bool:
    return plugin.testing_mode() and connected_at + TESTING_LIFETIME <= (now or utcnow())


def status_view(plugin: Plugin, row: PluginConnection) -> PluginConnectionStatus:
    reason = row.status_reason if row.status != "connected" else None
    return PluginConnectionStatus(
        account_email=row.account_email,
        status="connected" if row.status == "connected" else "reconnect_needed",
        status_reason=reason,  # type: ignore[arg-type]
        status_message=reconnect_message(reason or "revoked", plugin.name) if row.status != "connected" else None,
        granted_scopes=sorted(row.scopes or ()),
        chat_enabled=row.chat_enabled,
        connected_at=row.connected_at,
        last_used_at=row.last_used_at,
        refresh_expires_at=refresh_expires_at(plugin, row.connected_at),
    )


def readable_token(cipher: crypto.TokenCipher | None, row: PluginConnection) -> str | None:
    """The row's refresh token, else its access token, for a revoke; None when neither can be decrypted."""
    if cipher is None:
        return None
    for ciphertext in (row.refresh_token_enc, row.access_token_enc):
        if ciphertext:
            try:
                return cipher.decrypt(row.user_id, row.plugin_id, ciphertext)
            except crypto.TokenUnreadable:
                continue
    return None


# ---------------- Storing a consent (the OAuth callback) ----------------


async def store(
    user: User, plugin: Plugin, token: TokenResponse, identity: AccountIdentity
) -> Literal["connected", "error"]:
    """Insert or update the user's connection from a token response (brief 5.2, "Storing"). The same account keeps its
    row and revokes nothing; another account replaces it, and its grant is revoked after the commit unless another
    connection uses it."""
    cipher = crypto.token_cipher()
    if cipher is None:
        return "error"
    revoke_after: tuple[str, str] | None = None
    async with lock(user.id, plugin.id):
        async with SessionLocal() as db:
            row = await db.scalar(
                select(PluginConnection).where(
                    PluginConnection.user_id == user.id, PluginConnection.plugin_id == plugin.id
                )
            )
            now = utcnow()
            same = row is not None and row.account_subject == identity.subject
            if token.refresh_token is None and not same:
                logger.warning("The %s token response had no refresh token for a new connection", plugin.id)
                return "error"
            if row is None:
                row = PluginConnection(user_id=user.id, plugin_id=plugin.id, chat_enabled=True, created_at=now)
                db.add(row)
            elif same:
                if token.refresh_token is None:
                    try:  # kept, under the first key like the new access token
                        row.refresh_token_enc = cipher.rotate(row.refresh_token_enc)
                    except crypto.TokenUnreadable:
                        return "error"
            else:
                old = connection_info(row)
                old_token = readable_token(cipher, row)
                await plugin.on_disconnect(db, old)
                if old_token is not None:
                    revoke_after = (old.account_subject, old_token)
            if token.refresh_token is not None:
                row.refresh_token_enc = cipher.encrypt(user.id, plugin.id, token.refresh_token)
                row.connected_at = now
            row.account_subject = identity.subject
            row.account_email = identity.email
            row.scopes = sorted(registry.normalize_scopes(token.scopes))
            row.status = "connected"
            row.status_reason = None
            row.access_token_enc = cipher.encrypt(user.id, plugin.id, token.access_token)
            row.access_expires_at = now + timedelta(seconds=token.expires_in)
            row.refreshed_at = now
            row.key_id = cipher.key_id
            row.updated_at = now
            audit.add(db, user.id, plugin.id, "connect", origin="ui")
            await db.commit()
            _unreadable_logged.discard(row.id)
    if revoke_after is not None:
        await revoke_unless_shared(plugin, *revoke_after)
    return "connected"


# ---------------- Access tokens ----------------


async def access_token(connection_id: int, *, expected_subject: str, force_refresh: bool = False) -> str:
    """A valid access token for the connection, refreshed when it is within a minute of its expiry, or always with
    `force_refresh` (after a 401). Raises NotConnected, ReconnectNeeded, AccountChanged (the connection now holds
    another account than `expected_subject`) or PluginUnavailable. Uses sessions of its own and never holds a
    database transaction during an HTTP call."""
    started = utcnow()
    for _ in range(3):
        row = await _load(connection_id)
        plugin, cipher = await _check(row, expected_subject)
        assert row is not None
        if not force_refresh and (token := _fresh_access(cipher, row)) is not None:
            if cipher.needs_rotation(row.key_id):
                await _rotate_lazily(cipher, row)
            return token
        async with lock(row.user_id, row.plugin_id):
            row = await _load(connection_id)  # another holder of the lock may have refreshed meanwhile
            plugin, cipher = await _check(row, expected_subject)
            assert row is not None
            token = _fresh_access(cipher, row)
            # A forced refresh (after a 401) reuses a token another caller got since this one started waiting.
            newer = row.refreshed_at is not None and row.refreshed_at >= started
            if token is not None and (not force_refresh or newer):
                return token
            result = await _refresh(plugin, cipher, row)
            if await _write_refresh(cipher, row, result):
                return result.access_token
        # A reconnect or an account switch replaced the tokens while the refresh ran: start over from the new row.
        logger.info("Discarded a token refresh of connection %s that raced a reconnect", connection_id)
    raise PluginUnavailable("The connection is changing right now. Try again shortly.")


async def _load(connection_id: int) -> PluginConnection | None:
    async with SessionLocal() as db:
        return await db.get(PluginConnection, connection_id)


async def _check(row: PluginConnection | None, expected_subject: str) -> tuple[Plugin, crypto.TokenCipher]:
    plugin = registry.get_plugin(row.plugin_id) if row is not None else None
    if row is None or plugin is None:
        raise NotConnected(_GONE)
    if row.account_subject != expected_subject:
        raise AccountChanged(f"Your connected {plugin.name} account changed. Ask again.")
    if row.status != "connected":
        reason: ConnectionReason = row.status_reason or "revoked"  # type: ignore[assignment]
        raise ReconnectNeeded(reason, reconnect_message(reason, plugin.name))
    if testing_expired(plugin, row.connected_at):
        await mark_reconnect_needed(row.id, "expired_testing")
        raise ReconnectNeeded("expired_testing", reconnect_message("expired_testing", plugin.name))
    cipher = crypto.token_cipher()
    if cipher is None:
        raise PluginUnavailable(f"{plugin.name} isn't set up on this server right now; tell the admin.")
    return plugin, cipher


def _fresh_access(cipher: crypto.TokenCipher, row: PluginConnection) -> str | None:
    if row.access_token_enc is None or row.access_expires_at is None:
        return None
    if row.access_expires_at - utcnow() <= REFRESH_MARGIN:
        return None
    try:
        return cipher.decrypt(row.user_id, row.plugin_id, row.access_token_enc)
    except crypto.TokenUnreadable:
        return None


async def _rotate_lazily(cipher: crypto.TokenCipher, row: PluginConnection) -> None:
    """A row written under an older key by another process (a rolling restart): re-encrypt it, best effort."""
    try:
        values = {"refresh_token_enc": cipher.rotate(row.refresh_token_enc), "key_id": cipher.key_id}
        if row.access_token_enc is not None:
            values["access_token_enc"] = cipher.rotate(row.access_token_enc)
        async with SessionLocal() as db:
            await db.execute(
                update(PluginConnection)
                .where(PluginConnection.id == row.id, PluginConnection.refresh_token_enc == row.refresh_token_enc)
                .values(**values)
            )
            await db.commit()
    except Exception as exc:
        logger.warning("Could not re-encrypt connection %s: %s", row.id, type(exc).__name__)


async def _refresh(plugin: Plugin, cipher: crypto.TokenCipher, row: PluginConnection) -> TokenResponse:
    """POST the refresh grant (retried once after 1 s on connect errors, timeouts and 5xx). Marks the connection on
    invalid_grant (reconnect needed) and raises; a provider that rejects the client is temporary (the admin's
    problem), never a status change."""
    try:
        refresh_token = cipher.decrypt(row.user_id, row.plugin_id, row.refresh_token_enc)
    except crypto.TokenUnreadable:
        if row.id not in _unreadable_logged:
            _unreadable_logged.add(row.id)
            logger.warning(
                "Connection %s of user %s can't be decrypted (stored under key %s)", row.id, row.user_id, row.key_id
            )
        await mark_reconnect_needed(row.id, "key_changed")
        raise ReconnectNeeded("key_changed", reconnect_message("key_changed", plugin.name)) from None
    endpoints = plugin.oauth()
    form = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": endpoints.client_id,
        "client_secret": endpoints.client_secret,
    }
    unreachable = PluginUnavailable(f"{plugin.name} is unreachable right now. Try again shortly.")
    response: httpx.Response | None = None
    for attempt in range(2):
        try:
            response = await http.client().post(endpoints.token_url, data=form, timeout=http.TOKEN_TIMEOUT)
        except httpx.TransportError as exc:
            logger.warning("Refreshing connection %s failed: %s", row.id, type(exc).__name__)
            response = None
        if (response is None or response.status_code >= 500) and attempt == 0:
            await _sleep(1.0)
            continue
        break
    if response is None:
        raise unreachable
    error = error_code(response)
    if response.status_code == 200:
        try:
            return parse_token_response(response.json())
        except ValueError:
            logger.warning("The %s token endpoint returned an unexpected refresh response", plugin.id)
            raise unreachable from None
    if response.status_code == 400 and error == "invalid_grant":
        old = utcnow() - row.connected_at >= _TESTING_EXPIRY_AGE
        reason: ConnectionReason = "expired_testing" if plugin.testing_mode() and old else "revoked"
        await mark_reconnect_needed(row.id, reason)
        raise ReconnectNeeded(reason, reconnect_message(reason, plugin.name))
    if response.status_code in (400, 401) and error in ("invalid_client", "unauthorized_client"):
        if plugin.id not in _client_rejected:
            _client_rejected.add(plugin.id)
            logger.error(
                "The provider rejected the %s OAuth client (%s); check its client id and secret in .env",
                plugin.id,
                error,
            )
        raise PluginUnavailable(f"{plugin.name} isn't working right now; tell the admin.")
    logger.warning("Refreshing connection %s failed: HTTP %s %s", row.id, response.status_code, error or "")
    raise unreachable


async def _write_refresh(cipher: crypto.TokenCipher, row: PluginConnection, result: TokenResponse) -> bool:
    """The compare-and-set: written only while the row still holds the account and the refresh token the refresh
    started from. False: something replaced them meanwhile, and the result is discarded."""
    now = utcnow()
    if result.refresh_token is not None:
        refresh_enc = cipher.encrypt(row.user_id, row.plugin_id, result.refresh_token)
    elif cipher.needs_rotation(row.key_id):
        refresh_enc = cipher.rotate(row.refresh_token_enc)
    else:
        refresh_enc = row.refresh_token_enc
    values: dict[str, object] = {
        "access_token_enc": cipher.encrypt(row.user_id, row.plugin_id, result.access_token),
        "access_expires_at": now + timedelta(seconds=result.expires_in),
        "refresh_token_enc": refresh_enc,
        "refreshed_at": now,
        "key_id": cipher.key_id,
        "updated_at": now,
    }
    if result.scopes:
        values["scopes"] = sorted(registry.normalize_scopes(result.scopes))
    async with SessionLocal() as db:
        written = await db.execute(
            update(PluginConnection)
            .where(
                PluginConnection.id == row.id,
                PluginConnection.account_subject == row.account_subject,
                PluginConnection.refresh_token_enc == row.refresh_token_enc,
            )
            .values(**values)
        )
        await db.commit()
    return written.rowcount == 1  # type: ignore[attr-defined]


def error_code(response: httpx.Response) -> str | None:
    """The OAuth `error` field of a JSON error body (a short code; never the description)."""
    try:
        data = response.json()
    except ValueError:
        return None
    error = data.get("error") if isinstance(data, dict) else None
    return error[:40] if isinstance(error, str) else None


def parse_token_response(data: object) -> TokenResponse:
    """The token endpoint's JSON; ValueError when it isn't a usable token response."""
    if not isinstance(data, dict):
        raise ValueError("not an object")
    access = data.get("access_token")
    if not isinstance(access, str) or not access:
        raise ValueError("no access_token")
    expires_in = data.get("expires_in", 3600)
    if isinstance(expires_in, str) and expires_in.isdigit():
        expires_in = int(expires_in)
    if not isinstance(expires_in, int) or isinstance(expires_in, bool) or expires_in <= 0:
        raise ValueError("bad expires_in")
    refresh = data.get("refresh_token")
    scope = data.get("scope")
    id_token = data.get("id_token")
    return TokenResponse(
        access_token=access,
        expires_in=min(expires_in, 86_400),
        refresh_token=refresh if isinstance(refresh, str) and refresh else None,
        scopes=tuple(scope.split()) if isinstance(scope, str) else (),
        id_token=id_token if isinstance(id_token, str) and id_token else None,
    )


# ---------------- Status ----------------


async def mark_reconnect_needed(connection_id: int, reason: ConnectionReason) -> None:
    """Connection `connected` -> `reconnect_needed` with `reason`, audited (origin system). A connection already
    needing a reconnect keeps its first reason."""
    async with SessionLocal() as db:
        row = await db.get(PluginConnection, connection_id)
        if row is None or row.status != "connected":
            return
        changed = await db.execute(
            update(PluginConnection)
            .where(PluginConnection.id == connection_id, PluginConnection.status == "connected")
            .values(status="reconnect_needed", status_reason=reason, updated_at=utcnow())
        )
        if changed.rowcount:  # type: ignore[attr-defined]
            audit.add(db, row.user_id, row.plugin_id, "reconnect_needed", origin="system")
        await db.commit()
    logger.info("Connection %s of user %s needs a reconnect (%s)", connection_id, row.user_id, reason)


async def touch(connection_id: int) -> None:
    """Note a successful use (last_used_at), at most once a minute. Never raises."""
    now = utcnow()
    try:
        async with SessionLocal() as db:
            await db.execute(
                update(PluginConnection)
                .where(
                    PluginConnection.id == connection_id,
                    or_(PluginConnection.last_used_at.is_(None), PluginConnection.last_used_at < now - _TOUCH_INTERVAL),
                )
                .values(last_used_at=now)
            )
            await db.commit()
    except Exception as exc:
        logger.warning("Could not note the use of connection %s: %s", connection_id, type(exc).__name__)


# ---------------- Disconnecting and revoking ----------------


async def disconnect(db: AsyncSession, user: User, plugin: Plugin, *, origin: Origin) -> bool:
    """Delete the user's connection (the plugin's on_disconnect first, in the same transaction), then revoke its grant
    at the provider unless another connection uses the same account. False when there was none."""
    cipher = crypto.token_cipher()
    async with lock(user.id, plugin.id):
        row = await db.scalar(
            select(PluginConnection)
            .where(PluginConnection.user_id == user.id, PluginConnection.plugin_id == plugin.id)
            .execution_options(populate_existing=True)
        )
        if row is None:
            return False
        info = connection_info(row)
        token = readable_token(cipher, row)
        await plugin.on_disconnect(db, info)
        await db.delete(row)
        audit.add(db, user.id, plugin.id, "disconnect", origin=origin)
        await db.commit()
    if token is not None:
        await revoke_unless_shared(plugin, info.account_subject, token)
    return True


async def disconnect_all(db: AsyncSession, plugin: Plugin) -> int:
    """Delete every connection of the plugin, each under its lock, then revoke each distinct account once."""
    cipher = crypto.token_cipher()
    ids = (await db.execute(select(PluginConnection.id, PluginConnection.user_id).where(
        PluginConnection.plugin_id == plugin.id
    ))).tuples().all()
    await db.commit()
    tokens: dict[str, str] = {}
    count = 0
    for connection_id, user_id in ids:
        async with lock(user_id, plugin.id):
            row = await db.get(PluginConnection, connection_id, populate_existing=True)
            if row is None:
                continue
            info = connection_info(row)
            token = readable_token(cipher, row)
            await plugin.on_disconnect(db, info)
            await db.delete(row)
            audit.add(db, user_id, plugin.id, "disconnect", origin="system")
            await db.commit()
        count += 1
        if token is not None:
            tokens.setdefault(info.account_subject, token)
    for subject, token in tokens.items():
        await revoke_unless_shared(plugin, subject, token)
    return count


async def revoke_all_for_user(db: AsyncSession, user_id: int) -> list[tuple[Plugin, str, str]]:
    """(plugin, account subject, token) of each of the user's connections, to revoke after the commit that deletes
    them (admin.delete_user)."""
    cipher = crypto.token_cipher()
    found: list[tuple[Plugin, str, str]] = []
    for row in await db.scalars(select(PluginConnection).where(PluginConnection.user_id == user_id)):
        plugin = registry.get_plugin(row.plugin_id)
        token = readable_token(cipher, row)
        if plugin is not None and token is not None:
            found.append((plugin, row.account_subject, token))
    return found


async def revoke_unless_shared(plugin: Plugin, account_subject: str, token: str) -> None:
    """After the commit that removed a connection: revoke its grant, unless any connection (any user's) still uses the
    same account, since revoking one token ends the account's whole grant for this client (P25). Best effort."""
    try:
        async with SessionLocal() as db:
            shared = await db.scalar(
                select(PluginConnection.id)
                .where(PluginConnection.plugin_id == plugin.id, PluginConnection.account_subject == account_subject)
                .limit(1)
            )
    except Exception as exc:
        logger.warning("Could not check whether a %s grant is shared: %s", plugin.id, type(exc).__name__)
        return
    if shared is not None:
        logger.info("Not revoking a %s grant: connection %s uses the same account", plugin.id, shared)
        return
    await revoke(plugin, token)


async def revoke(plugin: Plugin, token: str) -> None:
    """Revoke a token (and with it the grant) at the provider. Best effort: failures are logged (status only)."""
    url = plugin.oauth().revoke_url
    if not url:
        return
    try:
        response = await http.client().post(url, data={"token": token}, timeout=http.TOKEN_TIMEOUT)
    except httpx.HTTPError as exc:
        logger.warning("Revoking a %s grant failed: %s", plugin.id, type(exc).__name__)
        return
    if response.status_code == 200:
        logger.info("Revoked a %s grant", plugin.id)
    else:
        logger.warning("Revoking a %s grant failed: HTTP %s", plugin.id, response.status_code)
