"""The caller's plugins and connections (SPEC.md section 6, "Plugins"; docs/briefs/plugins-gmail.md, section 5): the
list, connect, the OAuth callback, "Use in chat" and disconnect. Nobody else's connection appears anywhere here.
Plugin problems never answer 401, which the client takes as a signed-out session."""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Path, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.deps import get_current_user
from app.models import PluginConnection, User
from app.plugins import connections, oauth, registry
from app.plugins.base import Plugin
from app.schemas_plugins import (
    PluginCapabilityInfo,
    PluginConnectionStatus,
    PluginConnectionUpdate,
    PluginConnectRequest,
    PluginConnectResponse,
    PluginInfo,
)

router = APIRouter(prefix="/plugins", tags=["plugins"])

PluginId = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]{1,31}$")]


def _plugin(plugin_id: str) -> Plugin:
    plugin = registry.get_plugin(plugin_id)
    if plugin is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown plugin")
    return plugin


async def _connection(db: AsyncSession, user: User, plugin: Plugin) -> PluginConnection | None:
    return await db.scalar(
        select(PluginConnection)
        .where(PluginConnection.user_id == user.id, PluginConnection.plugin_id == plugin.id)
        .execution_options(populate_existing=True)
    )


@router.get("")
async def list_plugins(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[PluginInfo]:
    """Enabled and configured plugins, plus any the caller still has a connection to (so they can disconnect)."""
    found: list[PluginInfo] = []
    for plugin in registry.all_plugins():
        access = await registry.access_for(db, user, plugin)
        row = await _connection(db, user, plugin) if access.connection is not None else None
        if not access.enabled and row is None:
            continue
        granted = registry.granted_capabilities(plugin, row.scopes or ()) if row is not None else frozenset()
        found.append(
            PluginInfo(
                id=plugin.id,
                name=plugin.name,
                description=plugin.description,
                auth_type=plugin.auth_type,
                enabled=access.enabled,
                testing_mode=plugin.testing_mode(),
                supports_import=plugin.supports_import,
                import_max_items=settings.gmail_import_max_items if plugin.supports_import else None,
                capabilities=[
                    PluginCapabilityInfo(
                        id=capability.id,
                        name=capability.name,
                        description=capability.description,
                        enabled=capability.id in access.enabled_capabilities,
                        granted=capability.id in granted,
                    )
                    for capability in plugin.capabilities
                ],
                connection=connections.status_view(plugin, row) if row is not None else None,
            )
        )
    return found


@router.post("/{plugin_id}/connect")
async def connect(
    plugin_id: PluginId,
    request: Request,
    response: Response,
    body: Annotated[PluginConnectRequest | None, Body()] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PluginConnectResponse:
    plugin = _plugin(plugin_id)
    start = await oauth.start_connect(
        db, user, plugin, body.capabilities if body is not None else None, request.headers.get("origin")
    )
    response.set_cookie(
        key=oauth.cookie_name(plugin.id),
        value=start.nonce,
        max_age=oauth.STATE_SECONDS,
        path=oauth.cookie_path(plugin.id),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    return PluginConnectResponse(authorize_url=start.authorize_url)


@router.get("/{plugin_id}/oauth/callback", response_class=RedirectResponse)
async def oauth_callback(plugin_id: PluginId, request: Request) -> RedirectResponse:
    """The provider sends the browser here after the consent screen. No session cookie arrives (it is SameSite=Strict
    and this is a cross-site navigation): the user comes from the server-side state, bound to this browser by the
    nonce cookie. Always a 303 back to the app with the result."""
    params = {key: value for key, value in request.query_params.items() if key in ("state", "code", "error", "scope")}
    base, result = await oauth.finish_callback(plugin_id, params, request.cookies.get(oauth.cookie_name(plugin_id)))
    redirect = RedirectResponse(f"{base}/?plugin={plugin_id}&oauth={result}", status_code=status.HTTP_303_SEE_OTHER)
    redirect.delete_cookie(
        key=oauth.cookie_name(plugin_id),
        path=oauth.cookie_path(plugin_id),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    redirect.headers["Cache-Control"] = "no-store"
    redirect.headers["Referrer-Policy"] = "no-referrer"
    return redirect


@router.patch("/{plugin_id}/connection")
async def update_connection(
    plugin_id: PluginId,
    body: PluginConnectionUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PluginConnectionStatus:
    plugin = _plugin(plugin_id)
    row = await _connection(db, user, plugin)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not connected")
    row.chat_enabled = body.chat_enabled
    await db.commit()
    return connections.status_view(plugin, row)


@router.delete("/{plugin_id}/connection", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect(
    plugin_id: PluginId, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> None:
    """Also while the plugin is off or not configured; 204 when there was nothing to disconnect."""
    await connections.disconnect(db, user, _plugin(plugin_id), origin="ui")


def mount_plugin_routers(app: FastAPI) -> None:
    """Each built-in plugin's own router under /api/plugins/<id>, after the generic routes above. Its routes check the
    registry per request (registry.require), so a plugin missing from the registry (tests) answers 404."""
    for plugin in registry.builtin_plugins():
        if (own := plugin.router()) is not None:
            app.include_router(own, prefix=f"/api/plugins/{plugin.id}")
