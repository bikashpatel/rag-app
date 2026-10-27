"""Admin API: users (with their plan, overrides and usage in their allowance window), models, usage, documents and
re-indexing. Every route requires the admin."""

import logging
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import ColumnElement, and_, case, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.deps import get_current_admin
from app.models import (
    Conversation,
    Document,
    GmailPendingSend,
    LLMModel,
    LoginEvent,
    Message,
    MessagePluginItem,
    Plan,
    PluginAuditEvent,
    PluginConnection,
    UsageEvent,
    User,
    UserPreference,
    user_allowed_models,
    utcnow,
)
from app.plugins import connections
from app.redis_client import INGEST_QUEUE, forget_ingest_attempts, get_redis
from app.schemas import MAX_INT, DocumentItem, UserLimits
from app.schemas_admin import (
    UNLIMITED_ALLOWANCE,
    AdminConfig,
    AdminModel,
    AdminModelCreate,
    AdminModelUpdate,
    AdminStats,
    AdminUser,
    AdminUserCreate,
    AdminUserPlan,
    AdminUserUpdate,
    AdminUserUsage,
    DailyUsage,
    LimitName,
    LimitOverrides,
    ModelRef,
    ModelTestResult,
    ModelUsage,
    ReindexRequest,
    ReindexResult,
    UsageReport,
    UsageTotals,
    UserUsage,
)
from app.schemas_billing import PlanRef
from app.security import hash_password_async
from app.services import image_tool, llm, vectorstore
from app.services.billing import audit, entitlements, periods
from app.services.billing.entitlements import Entitlements, Limits
from app.services.billing.periods import LIMIT_NAMES
from app.services.model_registry import (
    get_active_embedding_model,
    get_active_image_model,
    get_active_vision_model,
)
from app.services.storage import (
    delete_document_completely,
    document_item,
    generated_ids,
    imported_sources,
    lock_documents,
    remove_document_vectors,
    remove_file,
    vision_page_counts,
)
from app.services.usage import month_bounds, usage_window, utc_day

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(get_current_admin)])

Id = Annotated[int, Path(ge=1, le=MAX_INT)]

_ENV_ADMIN = "The admin account is managed in .env"
_USERNAME_TAKEN = "This username is already taken"
_ACTIVE_EMBEDDING = "This is the active embedding model. Make another embedding model the default first."

_CHAT_TOKENS = func.coalesce(func.sum(case((UsageEvent.kind == "chat", UsageEvent.total_tokens))), 0)
_CHAT_ALLOWANCE = func.coalesce(func.sum(case((UsageEvent.kind == "chat", UsageEvent.allowance_tokens))), 0)
_EMBEDDING_TOKENS = func.coalesce(func.sum(case((UsageEvent.kind == "embedding", UsageEvent.total_tokens))), 0)
_VISION_TOKENS = func.coalesce(func.sum(case((UsageEvent.kind == "vision", UsageEvent.total_tokens))), 0)
_COST = func.coalesce(func.sum(UsageEvent.cost_usd), 0.0)
_CHAT_REQUESTS = func.count(case((UsageEvent.kind == "chat", UsageEvent.id)))
_IMAGES = func.count(case((UsageEvent.kind == "image", UsageEvent.id)))


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
async def get_config(db: AsyncSession = Depends(get_db)) -> AdminConfig:
    cheapest = await db.scalar(
        select(Plan).where(Plan.status == "active").order_by(Plan.price_paise, Plan.rank).limit(1)
    )
    return AdminConfig(
        defaults=periods.plan_limits(cheapest) if cheapest is not None else UserLimits(**dict.fromkeys(LIMIT_NAMES, 0)),
        max_upload_mb_hard=settings.max_upload_mb_hard,
        admin_username=settings.admin_username,
    )


@router.get("/stats")
async def get_stats(db: AsyncSession = Depends(get_db)) -> AdminStats:
    active = await get_active_embedding_model(db)
    vision = await get_active_vision_model(db)
    image = await get_active_image_model(db)
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
        active_image_model=ModelRef(id=image.id, display_name=image.display_name) if image else None,
        queue_length=await get_redis().llen(INGEST_QUEUE),
    )


# ---------------- Users ----------------


async def _get_user(db: AsyncSession, user_id: int, *, lock: bool = False) -> User:
    """`lock`: FOR NO KEY UPDATE until the commit, re-reading the row (the user lock of plans-billing B42)."""
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


def _confirmed(body: AdminUserCreate | AdminUserUpdate) -> None:
    """An override of 0 for monthly_token_limit (an unlimited chat allowance) needs confirm_unlimited."""
    unlimited = body.overrides is not None and body.overrides.changes().get("monthly_token_limit") == 0
    if unlimited and not body.confirm_unlimited:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=UNLIMITED_ALLOWANCE)


class _WithoutOverrides:
    """A user as the plan alone sees them (every limit override NULL): the entitlements' limits are then the plan's,
    to compare the overrides with (above_plan)."""

    def __init__(self, user: User) -> None:
        self._user = user

    def __getattr__(self, name: str) -> Any:
        return None if name in LIMIT_NAMES else getattr(self._user, name)


def _audited(user: User) -> dict[str, Any]:
    """The values a user.updated event records before and after (plans-billing B45)."""
    return {
        "overrides": {name: getattr(user, name) for name in LIMIT_NAMES},
        "restrict_models": user.restrict_models,
        "allowed_model_ids": sorted(model.id for model in user.allowed_models),
        "billing_tester": user.billing_tester,
    }


async def _usage_in_windows(
    db: AsyncSession, windows: dict[int, tuple[datetime, datetime]]
) -> dict[int, AdminUserUsage]:
    """user id -> their usage in their window, in one query (users sharing a window, as the calendar month, share a
    condition)."""
    by_window: defaultdict[tuple[datetime, datetime], list[int]] = defaultdict(list)
    for user_id, window in windows.items():
        by_window[window].append(user_id)
    rows = await db.execute(
        select(UsageEvent.user_id, _CHAT_TOKENS, _CHAT_ALLOWANCE, _EMBEDDING_TOKENS, _VISION_TOKENS, _IMAGES, _COST)
        .where(
            or_(
                *(
                    and_(UsageEvent.user_id.in_(ids), UsageEvent.created_at >= start, UsageEvent.created_at < end)
                    for (start, end), ids in by_window.items()
                )
            )
        )
        .group_by(UsageEvent.user_id)
    )
    found = {row[0]: row[1:] for row in rows.tuples()}
    usage = {}
    for user_id, (start, end) in windows.items():
        chat, allowance, embedding, vision, images, cost = found.get(user_id, (0, 0, 0, 0, 0, 0.0))
        usage[user_id] = AdminUserUsage(
            chat_tokens=chat,
            chat_allowance_used=allowance,
            embedding_tokens=embedding,
            vision_tokens=vision,
            images=images,
            cost_usd=_cost(cost),
            window_start=start,
            window_end=end,
        )
    return usage


async def _documents_by_owner(db: AsyncSession, owner_id: int | None = None) -> dict[int, tuple[int, int]]:
    """owner id -> (document count, storage bytes)"""
    query = select(
        Document.owner_id, func.count(Document.id), func.coalesce(func.sum(Document.size_bytes), 0)
    ).group_by(Document.owner_id)
    if owner_id is not None:
        query = query.where(Document.owner_id == owner_id)
    return {owner: (count, size) for owner, count, size in (await db.execute(query)).all()}


def _above_plan(user: User, plan: Limits) -> list[LimitName]:
    """The overrides more generous than the plan's value: unlimited (0) against a limit, or a higher limit."""
    above: list[LimitName] = []
    for name in LIMIT_NAMES:
        own, planned = getattr(user, name), getattr(plan, name)
        if own is not None and planned and (own == 0 or own > planned):
            above.append(name)  # type: ignore[arg-type]
    return above


def _user_plan(user: User, ent: Entitlements, codes: dict[int, str], now: datetime) -> AdminUserPlan:
    expiring = (
        ent.state == "active"
        and ent.period_id is not None
        and user.comp_plan_id is None
        and ent.paid_through is not None
        and ent.paid_through - now <= timedelta(days=settings.billing_reminder_days)
    )
    plan = None
    if ent.plan_id is not None and ent.plan_name is not None:
        plan = PlanRef(id=ent.plan_id, code=codes.get(ent.plan_id, ""), name=ent.plan_name)
    return AdminUserPlan(
        state=ent.state,
        plan=plan,
        valid_through=ent.valid_through,
        paid_through=ent.paid_through,
        complimentary=ent.complimentary,
        open_ended=ent.open_ended,
        test_mode=ent.test_mode,
        expiring_soon=expiring,
        ended_at=ent.paid_through if ent.state == "expired" else None,
    )


async def _admin_users(db: AsyncSession, users: Sequence[User]) -> list[AdminUser]:
    """AdminUser of each: limits and usage by their entitlements (one query for every user's periods)."""
    now = utcnow()
    loaded = await entitlements.load_many(db, users, now=now)
    planned = await entitlements.load_many(db, [cast(User, _WithoutOverrides(user)) for user in users], now=now)
    codes = dict((await db.execute(select(Plan.id, Plan.code))).tuples().all())
    usage = await _usage_in_windows(db, {user.id: usage_window(loaded[user.id], now) for user in users})
    documents = await _documents_by_owner(db, users[0].id if len(users) == 1 else None)
    found = []
    for user in users:
        ent = loaded[user.id]
        document_count, storage_bytes = documents.get(user.id, (0, 0))
        found.append(
            AdminUser(
                id=user.id,
                username=user.username,
                is_admin=user.is_admin,
                is_active=user.is_active,
                restrict_models=user.restrict_models,
                allowed_model_ids=sorted(model.id for model in user.allowed_models),
                created_at=user.created_at,
                last_login_at=user.last_login_at,
                usage=usage[user.id],
                document_count=document_count,
                storage_bytes=int(storage_bytes),
                billing_tester=user.billing_tester,
                limits=entitlements.user_limits(ent),
                overrides=LimitOverrides(**{name: getattr(user, name) for name in LIMIT_NAMES}),
                above_plan=[] if user.is_admin else _above_plan(user, planned[user.id].limits),
                no_models=ent.access and user.restrict_models and not user.is_admin and not ent.chat_model_ids,
                plan=_user_plan(user, ent, codes, now),
            )
        )
    return found


@router.get("/users")
async def list_users(db: AsyncSession = Depends(get_db)) -> list[AdminUser]:
    users = list(await db.scalars(select(User).order_by(User.is_admin.desc(), User.username)))
    return await _admin_users(db, users) if users else []


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_user(
    body: AdminUserCreate, admin: User = Depends(get_current_admin), db: AsyncSession = Depends(get_db)
) -> AdminUser:
    """With `grant`, a complimentary plan from now (as Admin → Billing grants it); else no plan until the user pays."""
    _confirmed(body)
    if await db.scalar(select(User.id).where(User.username == body.username)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_USERNAME_TAKEN)
    plan = await db.get(Plan, body.grant.plan_id) if body.grant is not None else None
    if body.grant is not None and plan is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Unknown plan.")
    user = User(
        username=body.username,
        password_hash=await hash_password_async(body.password),
        is_admin=False,
        is_active=body.is_active,
        restrict_models=body.restrict_models,
        billing_tester=body.billing_tester,
        allowed_models=await _chat_models(db, body.allowed_model_ids),
        **(body.overrides.changes() if body.overrides is not None else {}),
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_USERNAME_TAKEN) from None
    await audit.record_event(
        db, source="admin", type="user.created", actor=admin.username, user_id=user.id, detail=_audited(user)
    )
    if body.grant is not None and plan is not None:
        grant = body.grant
        await periods.grant(db, user, plan, days=grant.days, reason=grant.reason, actor=admin.username, start="now")
    await db.commit()
    return (await _admin_users(db, [user]))[0]


@router.patch("/users/{user_id}")
async def update_user(
    user_id: Id, body: AdminUserUpdate, admin: User = Depends(get_current_admin), db: AsyncSession = Depends(get_db)
) -> AdminUser:
    """Every change of an override, the allowed list, restrict_models or billing_tester writes a user.updated event
    with the values before and after."""
    _confirmed(body)
    changes = body.model_dump(exclude_none=True, exclude={"overrides", "confirm_unlimited"})
    # Hashed before the row is locked, so the lock isn't held while hashing.
    password_hash = await hash_password_async(changes.pop("password")) if "password" in changes else None
    # Locked: token_version and password_hash are read-modify-write, and change-password writes them too.
    user = _editable_user(await _get_user(db, user_id, lock=True))
    before = _audited(user)
    if "allowed_model_ids" in changes:
        user.allowed_models = await _chat_models(db, changes.pop("allowed_model_ids"))
    if changes.get("is_active") is False or password_hash is not None:
        user.token_version += 1
    if password_hash is not None:
        user.password_hash = password_hash
    for field, value in {**changes, **(body.overrides.changes() if body.overrides is not None else {})}.items():
        setattr(user, field, value)
    after = _audited(user)
    if after != before:
        changed = {key: {"before": before[key], "after": after[key]} for key in after if after[key] != before[key]}
        await audit.record_event(
            db, source="admin", type="user.updated", actor=admin.username, user_id=user.id, detail=changed
        )
    await db.commit()
    return (await _admin_users(db, [user]))[0]


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: Id, db: AsyncSession = Depends(get_db)) -> None:
    """Their open payment links are cancelled first (best effort); billing records stay with the username, except the
    events of a user who never had an order."""
    user = _editable_user(await _get_user(db, user_id))
    await periods.on_user_deleted(db, user)
    # Their plugin grants, revoked at the providers once the rows are gone (unless another connection shares one).
    revocations = await connections.revoke_all_for_user(db, user.id)
    documents = await lock_documents(db, Document.owner_id == user.id)
    for doc in documents:
        await remove_document_vectors(db, doc)
        await db.delete(doc)
    # ON DELETE CASCADE removes these too (test_deletion_completeness checks every key); they are deleted explicitly
    # as well for a database whose tables predate a key's ON DELETE, since create_all never alters an existing table.
    conversation_ids = select(Conversation.id).where(Conversation.user_id == user.id)
    message_ids = select(Message.id).where(Message.conversation_id.in_(conversation_ids))
    await db.execute(delete(MessagePluginItem).where(MessagePluginItem.message_id.in_(message_ids)))
    await db.execute(delete(GmailPendingSend).where(GmailPendingSend.user_id == user.id))
    await db.execute(delete(Message).where(Message.conversation_id.in_(conversation_ids)))
    await db.execute(delete(Conversation).where(Conversation.user_id == user.id))
    await db.execute(delete(UsageEvent).where(UsageEvent.user_id == user.id))
    await db.execute(delete(PluginConnection).where(PluginConnection.user_id == user.id))
    await db.execute(delete(PluginAuditEvent).where(PluginAuditEvent.user_id == user.id))
    await db.execute(delete(UserPreference).where(UserPreference.user_id == user.id))
    await db.execute(delete(LoginEvent).where(LoginEvent.user_id == user.id))
    await db.delete(user)
    await db.commit()
    for doc in documents:
        remove_file(doc.stored_name)
    await forget_ingest_attempts(doc.id for doc in documents)
    for plugin, account_subject, token in revocations:
        await connections.revoke_unless_shared(plugin, account_subject, token)


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


def _require_supported_image_model(litellm_model: str, api_base: str | None) -> None:
    """Image models must be OpenAI or Azure OpenAI GPT image models (llm.image_model_problem)."""
    if (problem := llm.image_model_problem(litellm_model, api_base)) is not None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=problem)


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
    if body.kind == "image":
        _require_supported_image_model(body.litellm_model, body.api_base)
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
    if model.kind == "image" and ("litellm_model" in changes or "api_base" in changes):
        _require_supported_image_model(
            changes.get("litellm_model", model.litellm_model), changes.get("api_base", model.api_base)
        )
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
async def run_model_test(
    model_id: Id, admin: User = Depends(get_current_admin), db: AsyncSession = Depends(get_db)
) -> ModelTestResult:
    model = await _get_model(db, model_id)
    await db.commit()  # don't hold a pooled connection (idle in transaction) during the provider call
    if model.kind == "image":  # recorded as the admin's image usage (an image Test costs about $0.01)
        return ModelTestResult(**await image_tool.test_image_model(model, admin))
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

    chat, embedding, vision, images, cost, requests = (
        await db.execute(
            select(_CHAT_TOKENS, _EMBEDDING_TOKENS, _VISION_TOKENS, _IMAGES, _COST, _CHAT_REQUESTS).where(*in_month)
        )
    ).one()

    by_user = await db.execute(
        select(User.id, User.username, _CHAT_TOKENS, _EMBEDDING_TOKENS, _VISION_TOKENS, _IMAGES, _COST, _CHAT_REQUESTS)
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

    day = utc_day(UsageEvent.created_at)
    daily = await db.execute(
        select(day, _CHAT_TOKENS, _EMBEDDING_TOKENS, _VISION_TOKENS, _IMAGES, _COST)
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
            images=images,
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
                images=user_images,
                cost_usd=_cost(user_cost),
                requests=user_requests,
            )
            for (
                user_id,
                username,
                user_chat,
                user_embedding,
                user_vision,
                user_images,
                user_cost,
                user_requests,
            ) in by_user.all()
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
                images=day_images,
                cost_usd=_cost(day_cost),
            )
            for date, day_chat, day_embedding, day_vision, day_images, day_cost in daily.all()
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
    generated = await generated_ids(db, select(Document.id))
    imported = await imported_sources(db, select(Document.id))
    return [
        document_item(
            doc,
            viewer=admin,
            active_embedding_model_id=active.id if active else None,
            admin_view=True,
            vision_pages=vision_pages.get(doc.id, 0),
            generated=doc.id in generated,
            imported_from=imported.get(doc.id),
        )
        for doc in documents
    ]


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: Id, db: AsyncSession = Depends(get_db)) -> None:
    document = await db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    await delete_document_completely(db, document)
