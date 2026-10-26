"""Readiness (public, status only) and Admin -> System (SPEC.md section 6, "Health" and "Admin"; section 10)."""

import asyncio
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from app.deps import get_current_admin
from app.ops import checks, monitor_state
from app.ops.checks import format_age
from app.ops.monitor_state import PublishedStatus
from app.ops.settings import ops_settings
from app.schemas_system import AlertRecord, AlertTestQueued, MonitorInfo, Readiness, SystemCheck, SystemStatus

router = APIRouter(prefix="/health", tags=["health"])
admin_router = APIRouter(prefix="/admin/system", tags=["admin"], dependencies=[Depends(get_current_admin)])

# Anyone may ask; answers within this many seconds share one round of checks.
READY_CACHE_SECONDS = 2.0
_ready: tuple[float, bool] | None = None
_ready_lock = asyncio.Lock()


async def _is_ready() -> bool:
    global _ready
    async with _ready_lock:
        now = time.monotonic()
        if _ready is None or now - _ready[0] >= READY_CACHE_SECONDS:
            _ready = (now, await checks.ready())
        return _ready[1]


@router.get("/ready", response_model=Readiness, responses={503: {"model": Readiness}})
async def readiness() -> JSONResponse:
    ready = await _is_ready()
    return JSONResponse(
        status_code=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "ready" if ready else "unready"},
        headers={"Cache-Control": "no-store"},
    )


def _monitor_view(
    published: PublishedStatus | None, error: str | None, pending: bool, now: float
) -> tuple[list[SystemCheck], MonitorInfo, list[AlertRecord]]:
    """The proxy and monitor checks, the monitor's info and its recent alerts."""
    unchecked = checks.check("proxy", "unknown", "Checked by the monitor service")
    if published is None:
        info = MonitorInfo(running=False, last_run_at=None, interval_seconds=None, channels=[], test_pending=pending)
        if error is not None:
            return (
                [unchecked, checks.check("monitor", "unknown", f"The monitor's results can't be read: {error}")],
                info,
                [],
            )
        return (
            [unchecked, checks.check("monitor", "warning", "The monitor service hasn't reported: no alerts are sent")],
            info,
            [],
        )
    info = MonitorInfo(
        running=published.fresh(now),
        last_run_at=datetime.fromtimestamp(published.updated_at, timezone.utc),
        interval_seconds=published.interval_seconds,
        channels=published.channels,
        test_pending=pending,
    )
    ago = format_age(now - published.updated_at)
    if not info.running:
        monitor = checks.check("monitor", "warning", f"Last reported {ago} ago: no alerts are sent while it is down")
        return [unchecked, monitor], info, published.alerts
    proxy = next((item for item in published.checks if item.id == "proxy"), None)
    if published.channels:
        monitor = checks.check("monitor", "ok", f"Ran {ago} ago; alerts go to {', '.join(published.channels)}")
    else:
        detail = f"Ran {ago} ago. No alert channel is configured: alerts are only logged and shown here"
        monitor = checks.check("monitor", "warning", detail)
    proxy = proxy or checks.check("proxy", "unknown", "Not checked (MONITOR_APP_URL is empty)")
    return [proxy, monitor], info, published.alerts


async def _published() -> tuple[PublishedStatus | None, str | None, bool]:
    try:
        return await monitor_state.read_published(), None, await monitor_state.pending_test_alert()
    except Exception as exc:
        return None, checks.describe_error(exc), False


@admin_router.get("")
async def system_status() -> SystemStatus:
    snapshot, (published, error, pending) = await asyncio.gather(checks.collect(), _published())
    extra, monitor, alerts = _monitor_view(published, error, pending, time.time())
    results = snapshot.checks + extra
    return SystemStatus(
        status=checks.overall(results),
        checked_at=snapshot.checked_at,
        app_version=ops_settings.app_version or None,
        checks=results,
        worker=snapshot.worker,
        queue=snapshot.queue,
        failed_documents_24h=snapshot.failed_documents_24h,
        database_size_bytes=snapshot.database_size_bytes,
        disks=snapshot.disks,
        backup=snapshot.backup,
        gmail_reconnect_needed=snapshot.gmail_reconnect_needed,
        monitor=monitor,
        alerts=alerts,
    )


@admin_router.post("/test-alert", status_code=status.HTTP_202_ACCEPTED)
async def send_test_alert() -> AlertTestQueued:
    """Asks the monitor to send a test alert to every channel on its next cycle (the backend never sends alerts)."""
    now = time.time()
    published = await monitor_state.read_published()
    if published is None or not published.fresh(now):
        raise HTTPException(status.HTTP_409_CONFLICT, "The monitor service isn't running, so no alert can be sent.")
    if not published.channels:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "No alert channel is configured. Set ALERT_WEBHOOK_URL, ALERT_NTFY_URL or the ALERT_SMTP_ settings in "
            ".env, then run docker compose up -d monitor.",
        )
    if not await monitor_state.request_test_alert(now):
        raise HTTPException(status.HTTP_409_CONFLICT, "A test alert is already waiting for the monitor.")
    return AlertTestQueued(queued_at=datetime.fromtimestamp(now, timezone.utc))
