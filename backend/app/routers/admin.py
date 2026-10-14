"""Admin API: users, models, usage, documents and re-indexing. Every route requires the admin."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import ColumnElement, and_, case, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.deps import get_current_admin
from app.models import Conversation, Document, LLMModel, Message, UsageEvent, User, user_allowed_models
from app.redis_client import INGEST_QUEUE, forget_ingest_attempts, get_redis
from app.schemas import MAX_INT, DocumentItem
from app.schemas_admin import (
    AdminConfig,
    AdminModel,
    AdminModelCreate,
    AdminModelUpdate,
    AdminStats,
    AdminUser,
    AdminUserCreate,
    AdminUserUpdate,
    AdminUserUsage,
    DailyUsage,
    ModelRef,
    ModelTestResult,
    ModelUsage,
    ReindexRequest,
    ReindexResult,
    UsageReport,
    UsageTotals,
    UserUsage,
    default_limits,
)
from app.security import hash_password_async
from app.services import llm, vectorstore
from app.services.model_registry import get_active_embedding_model, get_active_vision_model
from app.services.storage import (
    delete_document_completely,
    document_item,
    lock_documents,
    remove_document_vectors,
    remove_file,
    vision_page_counts,
)
from app.services.usage import month_bounds

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(get_current_admin)])

Id = Annotated[int, Path(ge=1, le=MAX_INT)]

_ENV_ADMIN = "The admin account is managed in .env"
_USERNAME_TAKEN = "This username is already taken"
_ACTIVE_EMBEDDING = "This is the active embedding model. Make another embedding model the default first."

_CHAT_TOKENS = func.coalesce(func.sum(case((UsageEvent.kind == "chat", UsageEvent.total_tokens))), 0)
_EMBEDDING_TOKENS = func.coalesce(func.sum(case((UsageEvent.kind == "embedding", UsageEvent.total_tokens))), 0)
_VISION_TOKENS = func.coalesce(func.sum(case((UsageEvent.kind == "vision", UsageEvent.total_tokens))), 0)
_COST = func.coalesce(func.sum(UsageEvent.cost_usd), 0.0)
_CHAT_REQUESTS = func.count(case((UsageEvent.kind == "chat", UsageEvent.id)))


def _cost(value: float | None) -> float:
    return round(float(value or 0.0), 6)


def _stale(active_id: int | None) -> ColumnElement[bool]:
    """Ready documents not embedded with the active model (the same rule as DocumentItem.is_stale)."""
    other_model: ColumnElement[bool]
    if active_id is None:
        other_model = Document.embedding_model_id.is_not(None)
    else:
        other_model = or_(Document.embedding_model_id.is_(None), Document.embedding_model_id != active_id)
    return and_(Document.status == "ready", other_model)


# ---------------- Config and stats ----------------


@router.get("/config")
async def get_config() -> AdminConfig:
    return AdminConfig(
        defaults=default_limits(),
        max_upload_mb_hard=settings.max_upload_mb_hard,
        admin_username=settings.admin_username,
    )


@router.get("/stats")
async def get_stats(db: AsyncSession = Depends(get_db)) -> AdminStats:
    active = await get_active_embedding_model(db)
    vision = await get_active_vision_model(db)
    users = await db.scalar(select(func.count()).select_from(User))
    documents, stale, failed = (
        await db.execute(
            select(
                func.count(Document.id),
                func.count(case((_stale(active.id if active else None), Document.id))),
                func.count(case((Document.status == "failed", Document.id))),
            )
        )
    ).one()
    return AdminStats(
        users=users or 0,
        documents=documents,
        stale_documents=stale,
        failed_documents=failed,
        active_embedding_model=ModelRef(id=active.id, display_name=active.display_name) if active else None,
        active_vision_model=ModelRef(id=vision.id, display_name=vision.display_name) if vision else None,
        queue_length=await get_redis().llen(INGEST_QUEUE),
    )


# ---------------- Users ----------------


async def _get_user(db: AsyncSession, user_id: int, *, lock: bool = False) -> User:
    """`lock`: FOR NO KEY UPDATE until the commit, re-reading the row."""
    user = await db.get(User, user_id, with_for_update={"key_share": True} if lock else None, populate_existing=lock)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


def _editable_user(user: User) -> User:
    if user.is_admin:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_ENV_ADMIN)
    return user


async def _chat_models(db: AsyncSession, model_ids: list[int]) -> list[LLMModel]:
    """The chat models with these ids; 422 if any id isn't an existing chat model."""
    wanted = set(model_ids)
    if not wanted:
        return []
    models = list(await db.scalars(select(LLMModel).where(LLMModel.id.in_(wanted), LLMModel.kind == "chat")))
    missing = wanted - {model.id for model in models}
    if missing:
        unknown = ", ".join(map(str, sorted(missing)))
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"allowed_model_ids must reference existing chat models (unknown: {unknown})",
        )
    return models


async def _usage_this_month(db: AsyncSession, user_id: int | None = None) -> dict[int, AdminUserUsage]:
    start, end, _ = month_bounds(None)
    query = (
        select(UsageEvent.user_id, _CHAT_TOKENS, _EMBEDDING_TOKENS, _VISION_TOKENS, _COST)
        .where(UsageEvent.created_at >= start, UsageEvent.created_at < end)
        .group_by(UsageEvent.user_id)
    )
    if user_id is not None:
        query = query.where(UsageEvent.user_id == user_id)
    return {
        owner: AdminUserUsage(chat_tokens=chat, embedding_tokens=embedding, vision_tokens=vision, cost_usd=_cost(cost))
        for owner, chat, embedding, vision, cost in (await db.execute(query)).all()
    }


async def _documents_by_owner(db: AsyncSession, owner_id: int | None = None) -> dict[int, tuple[int, int]]:
    """owner id -> (document count, storage bytes)"""
    query = select(
        Document.owner_id, func.count(Document.id), func.coalesce(func.sum(Document.size_bytes), 0)
    ).group_by(Document.owner_id)
    if owner_id is not None:
        query = query.where(Document.owner_id == owner_id)
    return {owner: (count, size) for owner, count, size in (await db.execute(query)).all()}


def _admin_user(user: User, usage: AdminUserUsage | None, documents: tuple[int, int] | None) -> AdminUser:
    document_count, storage_bytes = documents or (0, 0)
    return AdminUser(
        id=user.id,
        username=user.username,
        is_admin=user.is_admin,
        is_active=user.is_active,
        monthly_token_limit=user.monthly_token_limit,
        rate_limit_per_min=user.rate_limit_per_min,
        max_upload_mb=user.max_upload_mb,
        max_documents=user.max_documents,
        max_storage_mb=user.max_storage_mb,
        restrict_models=user.restrict_models,
        allowed_model_ids=sorted(model.id for model in user.allowed_models),
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        usage=usage or AdminUserUsage(chat_tokens=0, embedding_tokens=0, vision_tokens=0, cost_usd=0.0),
        document_count=document_count,
        storage_bytes=int(storage_bytes),
    )


async def _admin_user_for(db: AsyncSession, user: User) -> AdminUser:
    usage = await _usage_this_month(db, user.id)
    documents = await _documents_by_owner(db, user.id)
    return _admin_user(user, usage.get(user.id), documents.get(user.id))


@router.get("/users")
async def list_users(db: AsyncSession = Depends(get_db)) -> list[AdminUser]:
    users = await db.scalars(select(User).order_by(User.is_admin.desc(), User.username))
    usage = await _usage_this_month(db)
    documents = await _documents_by_owner(db)
    return [_admin_user(user, usage.get(user.id), documents.get(user.id)) for user in users]


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_user(body: AdminUserCreate, db: AsyncSession = Depends(get_db)) -> AdminUser:
    if await db.scalar(select(User.id).where(User.username == body.username)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_USERNAME_TAKEN)
    user = User(
        **body.model_dump(exclude={"password", "allowed_model_ids"}),
        is_admin=False,
        password_hash=await hash_password_async(body.password),
        allowed_models=await _chat_models(db, body.allowed_model_ids),
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_USERNAME_TAKEN) from None
    return await _admin_user_for(db, user)


@router.patch("/users/{user_id}")
async def update_user(user_id: Id, body: AdminUserUpdate, db: AsyncSession = Depends(get_db)) -> AdminUser:
    changes = body.model_dump(exclude_none=True)
    # Hashed before the row is locked, so the lock isn't held while hashing.
    password_hash = await hash_password_async(changes.pop("password")) if "password" in changes else None
    # Locked: token_version and password_hash are read-modify-write, and change-password writes them too.
    user = _editable_user(await _get_user(db, user_id, lock=True))
    if "allowed_model_ids" in changes:
        user.allowed_models = await _chat_models(db, changes.pop("allowed_model_ids"))
    if changes.get("is_active") is False or password_hash is not None:
        user.token_version += 1
    if password_hash is not None:
        user.password_hash = password_hash
    for field, value in changes.items():
        setattr(user, field, value)
    await db.commit()
    return await _admin_user_for(db, user)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: Id, db: AsyncSession = Depends(get_db)) -> None:
    user = _editable_user(await _get_user(db, user_id))
    documents = await lock_documents(db, Document.owner_id == user.id)
    for doc in documents:
        await remove_document_vectors(db, doc)
        await db.delete(doc)
    # Explicit, so these go even where the database doesn't enforce ON DELETE CASCADE (SQLite).
    conversation_ids = select(Conversation.id).where(Conversation.user_id == user.id)
    await db.execute(delete(Message).where(Message.conversation_id.in_(conversation_ids)))
    await db.execute(delete(Conversation).where(Conversation.user_id == user.id))
    await db.execute(delete(UsageEvent).where(UsageEvent.user_id == user.id))
    await db.delete(user)
    await db.commit()
    for doc in documents:
        remove_file(doc.stored_name)
    await forget_ingest_attempts(doc.id for doc in documents)


# ---------------- Models ----------------


async def _get_model(db: AsyncSession, model_id: int, *, lock: bool = False) -> LLMModel:
    """`lock`: until the commit, so an ingestion run can't recreate a collection being dropped (see ingestion)."""
    model = await db.get(LLMModel, model_id, with_for_update=lock or None)
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found")
    return model


async def _clear_other_defaults(db: AsyncSession, model: LLMModel) -> None:
    await db.execute(
        update(LLMModel)
        .where(LLMModel.kind == model.kind, LLMModel.id != model.id, LLMModel.is_default.is_(True))
        .values(is_default=False)
    )


async def _discard_vectors(db: AsyncSession, model: LLMModel) -> None:
    """Drop an embedding model's collection; the documents indexed with it become stale."""
    try:
        await vectorstore.drop_collection(vectorstore.collection_name(model.id))
    except Exception as exc:
        logger.exception("Could not drop the vector collection of model %s", model.id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The vector database is unavailable. Try again shortly.",
        ) from exc
    await db.execute(update(Document).where(Document.embedding_model_id == model.id).values(embedding_model_id=None))
    model.embedding_dim = None


def _require_enabled_default(is_default: bool | None, enabled: bool) -> None:
    if is_default and not enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Enable the model before making it the default"
        )


@router.get("/models")
async def list_models(db: AsyncSession = Depends(get_db)) -> list[AdminModel]:
    models = await db.scalars(select(LLMModel).order_by(LLMModel.kind, LLMModel.display_name, LLMModel.id))
    return [AdminModel.model_validate(model) for model in models]


@router.post("/models", status_code=status.HTTP_201_CREATED)
async def create_model(body: AdminModelCreate, db: AsyncSession = Depends(get_db)) -> AdminModel:
    _require_enabled_default(body.is_default, body.enabled)
    model = LLMModel(**body.model_dump())
    db.add(model)
    await db.flush()
    if model.is_default:
        await _clear_other_defaults(db, model)
    await db.commit()
    return AdminModel.model_validate(model)


@router.patch("/models/{model_id}")
async def update_model(model_id: Id, body: AdminModelUpdate, db: AsyncSession = Depends(get_db)) -> AdminModel:
    model = await _get_model(db, model_id, lock=True)
    changes = body.changes()
    is_active_embedding = model.kind == "embedding" and model.is_default and model.enabled
    if is_active_embedding and (changes.get("enabled") is False or changes.get("is_default") is False):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_ACTIVE_EMBEDDING)
    _require_enabled_default(changes.get("is_default"), changes.get("enabled", model.enabled))
    if model.kind == "embedding" and any(
        name in changes and changes[name] != getattr(model, name) for name in ("litellm_model", "api_base")
    ):
        await _discard_vectors(db, model)
    for field, value in changes.items():
        setattr(model, field, value)
    if changes.get("is_default"):
        await _clear_other_defaults(db, model)
    await db.commit()
    return AdminModel.model_validate(model)


@router.delete("/models/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model(model_id: Id, db: AsyncSession = Depends(get_db)) -> None:
    model = await _get_model(db, model_id, lock=True)
    if model.kind == "embedding":
        if model.is_default:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_ACTIVE_EMBEDDING)
        await _discard_vectors(db, model)
    # Explicit, so access grants go even where the database doesn't enforce ON DELETE CASCADE (SQLite).
    await db.execute(delete(user_allowed_models).where(user_allowed_models.c.model_id == model.id))
    await db.delete(model)
    await db.commit()


@router.post("/models/{model_id}/test")
async def run_model_test(model_id: Id, db: AsyncSession = Depends(get_db)) -> ModelTestResult:
    model = await _get_model(db, model_id)
    await db.commit()  # don't hold a pooled connection (idle in transaction) during the provider call
    result = ModelTestResult(**await llm.test_model(model))
    if model.kind == "embedding" and result.ok and result.embedding_dim:
        model.embedding_dim = result.embedding_dim
        await db.commit()
    return result


# ---------------- Re-indexing ----------------


@router.post("/reindex")
async def reindex(body: ReindexRequest | None = None, db: AsyncSession = Depends(get_db)) -> ReindexResult:
    active = await get_active_embedding_model(db)
    if active is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="There is no active embedding model. Enable an embedding model and make it the default first.",
        )
    statement = update(Document).values(status="pending", error=None).returning(Document.id)
    if body is None or body.only_stale:
        statement = statement.where(or_(_stale(active.id), Document.status == "failed"))
    document_ids = sorted((await db.execute(statement)).scalars())
    await db.commit()
    await forget_ingest_attempts(document_ids)
    if document_ids:
        await get_redis().rpush(INGEST_QUEUE, *document_ids)
    return ReindexResult(queued=len(document_ids))


# ---------------- Usage ----------------


@router.get("/usage")
async def usage_report(
    month: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None, db: AsyncSession = Depends(get_db)
) -> UsageReport:
    try:
        start, end, label = month_bounds(month)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="month must look like YYYY-MM"
        ) from None
    in_month = (UsageEvent.created_at >= start, UsageEvent.created_at < end)

    chat, embedding, vision, cost, requests = (
        await db.execute(
            select(_CHAT_TOKENS, _EMBEDDING_TOKENS, _VISION_TOKENS, _COST, _CHAT_REQUESTS).where(*in_month)
        )
    ).one()

    by_user = await db.execute(
        select(User.id, User.username, _CHAT_TOKENS, _EMBEDDING_TOKENS, _VISION_TOKENS, _COST, _CHAT_REQUESTS)
        .select_from(UsageEvent)
        .join(User, User.id == UsageEvent.user_id)
        .where(*in_month)
        .group_by(User.id, User.username)
        .order_by(_CHAT_TOKENS.desc(), User.username)
    )

    total_tokens = func.sum(UsageEvent.total_tokens)
    by_model = await db.execute(
        select(
            UsageEvent.model_name,
            UsageEvent.kind,
            func.coalesce(func.sum(UsageEvent.prompt_tokens), 0),
            func.coalesce(func.sum(UsageEvent.completion_tokens), 0),
            _COST,
            func.count(UsageEvent.id),
        )
        .where(*in_month)
        .group_by(UsageEvent.model_name, UsageEvent.kind)
        .order_by(UsageEvent.kind, total_tokens.desc(), UsageEvent.model_name)
    )

    day = func.date(UsageEvent.created_at)
    daily = await db.execute(
        select(day, _CHAT_TOKENS, _EMBEDDING_TOKENS, _VISION_TOKENS, _COST)
        .where(*in_month)
        .group_by(day)
        .order_by(day)
    )

    return UsageReport(
        month=label,
        totals=UsageTotals(
            chat_tokens=chat,
            embedding_tokens=embedding,
            vision_tokens=vision,
            cost_usd=_cost(cost),
            requests=requests,
        ),
        by_user=[
            UserUsage(
                user_id=user_id,
                username=username,
                chat_tokens=user_chat,
                embedding_tokens=user_embedding,
                vision_tokens=user_vision,
                cost_usd=_cost(user_cost),
                requests=user_requests,
            )
            for user_id, username, user_chat, user_embedding, user_vision, user_cost, user_requests in by_user.all()
        ],
        by_model=[
            ModelUsage(
                model_name=model_name,
                kind=kind,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=_cost(model_cost),
                requests=model_requests,
            )
            for model_name, kind, prompt_tokens, completion_tokens, model_cost, model_requests in by_model.all()
        ],
        daily=[
            DailyUsage(
                date=str(date),
                chat_tokens=day_chat,
                embedding_tokens=day_embedding,
                vision_tokens=day_vision,
                cost_usd=_cost(day_cost),
            )
            for date, day_chat, day_embedding, day_vision, day_cost in daily.all()
        ],
    )


# ---------------- Documents ----------------


@router.get("/documents")
async def list_documents(
    admin: User = Depends(get_current_admin), db: AsyncSession = Depends(get_db)
) -> list[DocumentItem]:
    active = await get_active_embedding_model(db)
    documents = await db.scalars(select(Document).order_by(Document.created_at.desc(), Document.id.desc()))
    vision_pages = await vision_page_counts(db, select(Document.id).where(Document.status == "ready"))
    return [
        document_item(
            doc,
            viewer=admin,
            active_embedding_model_id=active.id if active else None,
            admin_view=True,
            vision_pages=vision_pages.get(doc.id, 0),
        )
        for doc in documents
    ]


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: Id, db: AsyncSession = Depends(get_db)) -> None:
    document = await db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    await delete_document_completely(db, document)
