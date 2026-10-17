"""The HTTP client plugins use for their providers (OAuth endpoints, REST APIs): explicit timeouts, no redirects, and a
transport tests replace with a fake provider (set_transport)."""

import asyncio
from weakref import WeakKeyDictionary

import httpx

DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=5.0, pool=5.0)
TOKEN_TIMEOUT = httpx.Timeout(10.0)  # token exchange, refresh and revoke
_USER_AGENT = "rag-app-plugins"

_transport: httpx.AsyncBaseTransport | None = None
# One client per event loop: connection pools belong to the loop that opened them.
_clients: WeakKeyDictionary[asyncio.AbstractEventLoop, httpx.AsyncClient] = WeakKeyDictionary()


def client() -> httpx.AsyncClient:
    loop = asyncio.get_running_loop()
    current = _clients.get(loop)
    if current is None or current.is_closed:
        current = httpx.AsyncClient(
            transport=_transport,
            timeout=DEFAULT_TIMEOUT,
            follow_redirects=False,
            headers={"User-Agent": _USER_AGENT},
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=10),
        )
        _clients[loop] = current
    return current


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Tests: route every plugin request to `transport` (e.g. httpx.ASGITransport of a fake provider), or back to the
    network with None. Clients made before keep their transport until closed."""
    global _transport
    _transport = transport
    _clients.clear()


async def close() -> None:
    """At shutdown, after the plugin tasks drained."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    current = _clients.pop(loop, None)
    if current is not None:
        await current.aclose()
