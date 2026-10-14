import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.db import SessionLocal, init_db
from app.redis_client import close_redis
from app.services.vectorstore import close_qdrant

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    from app.bootstrap import bootstrap

    await init_db()
    async with SessionLocal() as db:
        await bootstrap(db)
    logger.info("Startup complete")
    yield
    await close_redis()
    await close_qdrant()


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

    @app.get("/api/health", tags=["health"])
    async def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
