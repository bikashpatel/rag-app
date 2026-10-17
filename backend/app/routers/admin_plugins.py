"""Admin -> Plugins (SPEC.md section 6, "Admin"; docs/briefs/plugins-gmail.md, sections 4.5 and 6): the switches,
configuration, connection counts (never account emails), activity, disconnect-all and the request-check Test."""

import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_admin
from app.models import LLMModel, PluginConnection
from app.plugins import audit, connections, crypto, intent, oauth, registry
from app.plugins.base import Plugin
from app.schemas_plugins import (
    AdminPlugin,
    AdminPluginCapability,
    AdminPluginUpdate,
    DisconnectAllResult,
    PluginActivityReport,
    PluginActivitySummary,
    PluginConnectionCounts,
    RequestCheckTest,
    RequestCheckTestResult,
)
from app.services.usage import month_bounds

router = APIRouter(prefix="/admin/plugins", tags=["admin"], dependencies=[Depends(get_current_admin)])

PluginId = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]{1,31}$")]

TEST_MESSAGE = "Please draft a short reply to Alice's last email thanking her, but don't send it."
_EXPECTED = {action: action == "draft" for action in intent.ACTIONS}


def _plugin(plugin_id: str) -> Plugin:
    plugin = registry.get_plugin(plugin_id)
    if plugin is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown plugin")
    return plugin


async def _counts(db: AsyncSession, plugin: Plugin) -> PluginConnectionCounts:
    rows = await db.execute(
        select(PluginConnection.status, PluginConnection.key_id, func.count())
        .where(PluginConnection.plugin_id == plugin.id)
        .group_by(PluginConnection.status, PluginConnection.key_id)
    )
    cipher = crypto.token_cipher()
    connected = reconnect = old_key = 0
    for row_status, key_id, count in rows.tuples():
        if row_status == "connected":
            connected += count
        else:
            reconnect += count
        if cipher is None or key_id != cipher.key_id:
            old_key += count
    return PluginConnectionCounts(connected=connected, reconnect_needed=reconnect, old_key=old_key)


async def _admin_plugin(db: AsyncSession, plugin: Plugin) -> AdminPlugin:
    missing = registry.configured(plugin)
    current = await registry.state(db, plugin)
    _, _, month = month_bounds(None)
    return AdminPlugin(
        id=plugin.id,
        name=plugin.name,
        description=plugin.description,
        auth_type=plugin.auth_type,
        supports_import=plugin.supports_import,
        configured=not missing,
        missing_settings=missing,
        test_endpoints=plugin.test_endpoints(),
        enabled=current.enabled,
        capabilities=[
            AdminPluginCapability(
                id=capability.id,
                name=capability.name,
                description=capability.description,
                scopes=list(capability.scopes),
                enabled=current.capabilities[capability.id],
                note=capability.admin_note,
            )
            for capability in plugin.capabilities
        ],
        requested_scopes=list(registry.requested_scopes(plugin, current.enabled_capabilities)),
        redirect_uris=[oauth.redirect_uri(base, plugin.id) for base in oauth.public_base_urls()],
        testing_mode=plugin.testing_mode(),
        connections=await _counts(db, plugin),
        activity=PluginActivitySummary(month=month, actions=await audit.monthly_counts(db, plugin.id, None)),
    )


@router.get("")
async def list_plugins(db: AsyncSession = Depends(get_db)) -> list[AdminPlugin]:
    return [await _admin_plugin(db, plugin) for plugin in registry.all_plugins()]


# Declared before the /{plugin_id} routes; plugin ids can't contain "-", so the paths never collide.
@router.post("/request-check/test")
async def test_request_check(body: RequestCheckTest, db: AsyncSession = Depends(get_db)) -> RequestCheckTestResult:
    """Run the request check once with a fixed message asking for a draft. No usage is recorded (like the model
    Test)."""
    model = await db.get(LLMModel, body.model_id)
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found")
    if model.kind != "chat":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Choose a chat model.")
    await db.commit()  # no pooled connection idle in a transaction during the provider call
    started = time.perf_counter()
    outcome = await intent.run_check(model, latest=TEST_MESSAGE, earlier=[], previous_reply=None)
    latency_ms = int((time.perf_counter() - started) * 1000)
    answer: dict[str, bool] | None = None
    if outcome.answer is not None:
        answer = {action: value for action, value in outcome.answer.items()}
    ok = answer == _EXPECTED
    if outcome.error is not None:
        message = outcome.error
    elif answer is None and outcome.finish_reason == "length":
        message = (
            "The model used up its output limit before answering (thinking counts). Raise its max output tokens or "
            "choose another model."
        )
    elif answer is None:
        message = f"The model didn't answer with JSON: {' '.join(outcome.text.split())[:80]}"
    elif ok:
        message = f"OK: the model answered the request check correctly ({latency_ms} ms)."
    else:
        shown = ", ".join(f"{action} {'true' if value else 'false'}" for action, value in answer.items())
        message = (
            f"The model answered, but not as expected: {shown}. Side effects may be refused or allowed wrongly with "
            "this model."
        )
    return RequestCheckTestResult(ok=ok, message=message, latency_ms=latency_ms, answer=answer)


@router.patch("/{plugin_id}")
async def update_plugin(
    plugin_id: PluginId, body: AdminPluginUpdate, db: AsyncSession = Depends(get_db)
) -> AdminPlugin:
    plugin = _plugin(plugin_id)
    known = {capability.id for capability in plugin.capabilities}
    if body.capabilities and (unknown := sorted(set(body.capabilities) - known)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"Unknown capability: {', '.join(unknown)}"
        )
    if body.enabled and (missing := registry.configured(plugin)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Set {', '.join(missing)} in .env first.")
    current = await registry.state(db, plugin)
    enabled = current.enabled if body.enabled is None else body.enabled
    switches = {**current.capabilities, **(body.capabilities or {})}
    if enabled and not any(switches.values()):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Turn on at least one capability, or turn the plugin off.",
        )
    await registry.set_state(db, plugin, enabled=body.enabled, capabilities=body.capabilities)
    return await _admin_plugin(db, plugin)


@router.get("/{plugin_id}/activity")
async def plugin_activity(
    plugin_id: PluginId,
    month: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None,
    db: AsyncSession = Depends(get_db),
) -> PluginActivityReport:
    plugin = _plugin(plugin_id)
    try:
        return await audit.activity_report(db, plugin.id, month)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc


@router.post("/{plugin_id}/disconnect-all")
async def disconnect_all(plugin_id: PluginId, db: AsyncSession = Depends(get_db)) -> DisconnectAllResult:
    """Every user's connection, as each user's disconnect (grants revoked after the commit, once per account)."""
    return DisconnectAllResult(disconnected=await connections.disconnect_all(db, _plugin(plugin_id)))
