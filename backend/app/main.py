import json
import logging
import re
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.config import settings
from app.db import SessionLocal, close_db, init_db
from app.redis_client import close_redis
from app.services.vectorstore import close_qdrant

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("app")
# httpx logs every request's full URL at INFO, which would put Gmail search queries in the log.
logging.getLogger("httpx").setLevel(logging.WARNING)

_PLUGIN_QUERY = re.compile(r"^(/api/plugins(?:/[^?]*)?)\?.*$", re.DOTALL)


class _RedactPluginQuery(logging.Filter):
    """uvicorn's access log line of any plugin path without its query: an OAuth callback's holds a one-time code and
    the state, and no plugin request may put search terms, addresses or subjects in the log (brief P20)."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            redacted = _PLUGIN_QUERY.sub(r"\1?[redacted]", args[2])
            if redacted != args[2]:
                record.args = (*args[:2], redacted, *args[3:])
        return True


logging.getLogger("uvicorn.access").addFilter(_RedactPluginQuery())


@asynccontextmanager
async def lifespan(_: FastAPI):
    from app.bootstrap import bootstrap
    from app.plugins import crypto, oauth, registry, tasks
    from app.plugins import http as plugin_http
    from app.routers.chat import drain_answers

    await init_db()
    async with SessionLocal() as db:
        await bootstrap(db)
    plugins = registry.all_plugins()
    oauth.log_startup(plugins)
    await crypto.rotate_all()
    for plugin in plugins:
        try:
            await plugin.on_startup()
        except Exception:
            logger.exception("The %s plugin failed to start", plugin.id)
    logger.info("Startup complete")
    yield
    await drain_answers()
    await tasks.drain(3.0)
    await plugin_http.close()
    await close_redis()
    await close_qdrant()
    await close_db()


class _AsciiJSONResponse(JSONResponse):
    """Escapes non-ASCII, so a lone surrogate (a "\\ud800" JSON escape) renders instead of failing to encode."""

    def render(self, content: Any) -> bytes:
        return json.dumps(content, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    from app.plugins.base import PluginError
    from app.routers import admin, admin_plugins, auth, chat, conversations, documents, me, models, plugins

    for module in (auth, me, models, documents, conversations, chat, admin, admin_plugins, plugins):
        app.include_router(module.router, prefix="/api")
    plugins.mount_plugin_routers(app)

    @app.exception_handler(PluginError)
    async def plugin_error(_: Request, exc: PluginError) -> JSONResponse:
        # A plugin problem a route didn't handle: its own status (409 reconnect needed, 503 unavailable, ...), never
        # 401, which the client takes as a signed-out session.
        return JSONResponse(status_code=exc.http_status, content={"detail": str(exc)})

    @app.exception_handler(RedisConnectionError)
    @app.exception_handler(RedisTimeoutError)
    async def redis_unavailable(_: Request, exc: Exception) -> JSONResponse:
        # Login throttling, rate limits and the ingestion queue need Redis.
        logger.warning("Redis unavailable: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "A required service is temporarily unavailable. Try again shortly."},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_failed(_: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's own handler, except for the encoding: it echoes the rejected input, which may hold a lone
        # surrogate (a 500 when rendered as UTF-8).
        return _AsciiJSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content={"detail": jsonable_encoder(exc.errors())}
        )

    @app.get("/api/health", tags=["health"])
    async def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
