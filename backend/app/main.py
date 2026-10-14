import json
import logging
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


@asynccontextmanager
async def lifespan(_: FastAPI):
    from app.bootstrap import bootstrap
    from app.routers.chat import drain_answers

    await init_db()
    async with SessionLocal() as db:
        await bootstrap(db)
    logger.info("Startup complete")
    yield
    await drain_answers()
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

    from app.routers import admin, auth, chat, conversations, documents, models

    for module in (auth, models, documents, conversations, chat, admin):
        app.include_router(module.router, prefix="/api")

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
