"""The caller's own account (docs/briefs/ui-shell-settings.md, section 4): preferences, the sign-in history (approximate
IPs, parsed User-Agents), the account view and the usage report of the allowance window. Nothing here reads another
user's rows."""

import ipaddress
import re
from collections import defaultdict
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, LLMModel, LoginEvent, UsageEvent, User, UserPreference, utcnow
from app.schemas import MAX_INT, Preferences
from app.schemas_account import (
    AccountInfo,
    DeviceType,
    DocumentUsage,
    MyUsage,
    MyUsageDay,
    MyUsageModel,
    PreferencesUpdate,
    UsageAmount,
    UsageCosts,
)
from app.schemas_account import LoginEvent as LoginEventInfo
from app.services import storage, usage
from app.services.billing.entitlements import Entitlements, allows_model

LOGIN_HISTORY_LIMIT = 20  # sign-ins kept per user
MODEL_UNAVAILABLE = "That model isn't available to you."

_KINDS = ("chat", "embedding", "vision", "image")
_DEVICES: tuple[DeviceType, ...] = ("desktop", "mobile", "tablet")
_UA_MAX_CHARS = 512
_VERSION = r"([0-9]{1,6})"
# First match wins; a pattern's group is the major version.
_BROWSERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Edge", re.compile(rf"\bEdg(?:A|iOS)?/{_VERSION}")),
    ("Opera", re.compile(rf"\bOPR/{_VERSION}|\bOpera\b(?:[/ ]{_VERSION})?")),
    ("Samsung Internet", re.compile(rf"\bSamsungBrowser/{_VERSION}")),
    ("Firefox", re.compile(rf"\b(?:Firefox|FxiOS)/{_VERSION}")),
    ("Headless Chrome", re.compile(rf"\bHeadlessChrome/{_VERSION}")),
    ("Chrome", re.compile(rf"\b(?:CriOS|Chrome)/{_VERSION}")),
    ("Safari", re.compile(rf"\bVersion/{_VERSION}.*?\bSafari/")),
    ("curl", re.compile(rf"\bcurl/{_VERSION}")),
    ("Python", re.compile(r"\b(?:python-httpx|python-requests|aiohttp)\b")),
)
_SYSTEMS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Windows", re.compile(r"Windows NT")),
    ("iOS", re.compile(r"iPhone")),
    ("iPadOS", re.compile(r"iPad")),
    ("macOS", re.compile(r"Mac OS X|Macintosh")),
    ("Android", re.compile(r"Android")),
    ("ChromeOS", re.compile(r"\bCrOS\b")),
    ("Linux", re.compile(r"Linux")),
)
_DESKTOPS = {"Windows", "macOS", "Linux", "ChromeOS"}


def current_time() -> datetime:
    """The usage report's clock (tests pin it): the month and "today" both come from it."""
    return utcnow()


# ---------------- Preferences ----------------


def _stored_values(data: object) -> tuple[dict[str, Any], int | None]:
    """The valid fields of a stored preferences object (an invalid one is left to its default) and the stored default
    model id when it is a valid id."""
    stored = data if isinstance(data, dict) else {}
    values: dict[str, Any] = {}
    for name in ("theme", "accent", "chat_font_size", "enter_to_send"):
        if name in stored:
            try:
                values[name] = getattr(Preferences.model_validate({name: stored[name]}, strict=True), name)
            except ValidationError:
                pass
    model_id = stored.get("default_model_id")
    if isinstance(model_id, bool) or not isinstance(model_id, int) or not 1 <= model_id <= MAX_INT:
        model_id = None
    return values, model_id


async def usable_chat_model(db: AsyncSession, model_id: int, ent: Entitlements) -> bool:
    """An enabled chat model the user may use now, as GET /models lists them (none without access)."""
    model = await db.get(LLMModel, model_id)
    return model is not None and model.kind == "chat" and model.enabled and allows_model(ent, model.id)


async def _preferences(db: AsyncSession, data: object, ent: Entitlements) -> Preferences:
    values, model_id = _stored_values(data)
    # The stored id stays when the model isn't usable now, so it is the default again once it is.
    if model_id is not None and await usable_chat_model(db, model_id, ent):
        values["default_model_id"] = model_id
    return Preferences(**values)


async def read_preferences(db: AsyncSession, user: User, ent: Entitlements) -> Preferences:
    """One primary-key select (plus the model's when a default is stored)."""
    row = await db.get(UserPreference, user.id)
    return await _preferences(db, row.data if row is not None else None, ent)


async def _locked_row(db: AsyncSession, user_id: int) -> UserPreference | None:
    return await db.get(UserPreference, user_id, with_for_update=True, populate_existing=True)


async def update_preferences(
    db: AsyncSession, user: User, update: PreferencesUpdate, ent: Entitlements
) -> Preferences:
    """Merge the given fields into the stored ones (unknown stored keys are kept) and commit. 422 when
    default_model_id isn't a model the user may use (any id without access)."""
    patch = update.model_dump(exclude_unset=True)
    if not patch:
        return await read_preferences(db, user, ent)
    model_id = patch.get("default_model_id")
    if model_id is not None and not await usable_chat_model(db, model_id, ent):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=MODEL_UNAVAILABLE)
    data = patch
    for attempt in range(2):
        row = await _locked_row(db, user.id)
        data = {**(row.data if row is not None and isinstance(row.data, dict) else {}), **patch}
        if row is None:
            db.add(UserPreference(user_id=user.id, data=data))
        else:
            row.data = data
        try:
            await db.commit()
        except IntegrityError:
            # Another request inserted the first row meanwhile: merge into that one.
            await db.rollback()
            await db.refresh(user)
            if attempt:
                raise
            continue
        break
    return await _preferences(db, data, ent)


# ---------------- Sign-ins ----------------


def approximate_ip(host: str | None) -> str | None:
    """IPv4 with the last part hidden ("203.0.113.x"; an IPv4-mapped IPv6 address too), IPv6 as its /48 network
    ("2001:db8:85a3::/48"); None when it isn't an address ("testclient", "unknown")."""
    if not host:
        return None
    try:
        address = ipaddress.ip_address(host.strip())
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if isinstance(address, ipaddress.IPv4Address):
        return str(address).rsplit(".", 1)[0] + ".x"
    return str(ipaddress.IPv6Network((int(address), 48), strict=False))


def ip_is_private(stored: str | None) -> bool:
    """Whether an approximate IP isn't a public internet address (private, loopback, link-local, shared or reserved):
    "a.b.c.x" is read as a.b.c.0 and a network as its network address, which decides it, since every such range is a
    /24 (IPv4) or /48 (IPv6) prefix or shorter. False for None and anything unparsable."""
    if not stored:
        return False
    try:
        if stored.endswith(".x"):
            address: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.IPv4Address(stored[:-1] + "0")
        else:
            address = ipaddress.ip_network(stored, strict=False).network_address
    except ValueError:
        return False
    return not address.is_global


def _match(patterns: tuple[tuple[str, re.Pattern[str]], ...], text: str) -> tuple[str, str | None] | None:
    for name, pattern in patterns:
        if found := pattern.search(text):
            return name, next((group for group in found.groups() if group), None)
    return None


def parse_user_agent(user_agent: str | None) -> tuple[str | None, str | None, DeviceType]:
    """(browser with its major version, OS, device type) from a User-Agent, which is never stored itself. Only fixed
    names and ASCII digits come out, so the values are short printable ASCII."""
    text = (user_agent or "")[:_UA_MAX_CHARS]
    browser = _match(_BROWSERS, text)
    system = _match(_SYSTEMS, text)
    os_name = system[0] if system is not None else None
    device: DeviceType = "unknown"
    if os_name == "iPadOS" or (os_name == "Android" and "Mobile" not in text):
        device = "tablet"
    elif os_name == "iOS" or "Mobile" in text:
        device = "mobile"
    elif os_name in _DESKTOPS:
        device = "desktop"
    label = None
    if browser is not None:
        name, version = browser
        label = f"{name} {int(version)}" if version else name
    return label, os_name, device


async def record_login(db: AsyncSession, user: User, host: str | None, user_agent: str | None) -> LoginEvent:
    """Add the sign-in to the user's history (flushed, so it has its id) and delete their rows beyond the newest
    LOGIN_HISTORY_LIMIT, in the caller's transaction."""
    browser, os_name, device = parse_user_agent(user_agent)
    event = LoginEvent(
        user_id=user.id, created_at=utcnow(), ip=approximate_ip(host), browser=browser, os=os_name, device=device
    )
    db.add(event)
    await db.flush()
    newest = (
        select(LoginEvent.id)
        .where(LoginEvent.user_id == user.id)
        .order_by(LoginEvent.created_at.desc(), LoginEvent.id.desc())
        .limit(LOGIN_HISTORY_LIMIT)
    )
    await db.execute(
        delete(LoginEvent)
        .where(LoginEvent.user_id == user.id, LoginEvent.id.not_in(newest))
        .execution_options(synchronize_session=False)
    )
    return event


def login_event_info(event: LoginEvent, session_id: int | None) -> LoginEventInfo:
    return LoginEventInfo(
        id=event.id,
        created_at=event.created_at,
        ip=event.ip,
        ip_private=ip_is_private(event.ip),
        browser=event.browser,
        os=event.os,
        device=next((device for device in _DEVICES if device == event.device), "unknown"),
        current=session_id is not None and event.id == session_id,
    )


async def recent_logins(db: AsyncSession, user: User, session_id: int | None) -> list[LoginEventInfo]:
    """The user's sign-ins, newest first; `current` marks the one with id `session_id`."""
    rows = await db.scalars(
        select(LoginEvent)
        .where(LoginEvent.user_id == user.id)
        .order_by(LoginEvent.created_at.desc(), LoginEvent.id.desc())
        .limit(LOGIN_HISTORY_LIMIT)
    )
    return [login_event_info(row, session_id) for row in rows]


async def account_info(db: AsyncSession, user: User, session_id: int | None) -> AccountInfo:
    conversations = await db.scalar(select(func.count(Conversation.id)).where(Conversation.user_id == user.id))
    return AccountInfo(
        id=user.id,
        username=user.username,
        is_admin=user.is_admin,
        password_managed_by_env=user.is_admin,  # the only admin is the env admin
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        conversation_count=int(conversations or 0),
        recent_logins=await recent_logins(db, user, session_id),
    )


# ---------------- Usage ----------------


def _cost(value: float) -> float:
    return round(value, 6)


async def my_usage(db: AsyncSession, user: User, ent: Entitlements) -> MyUsage:
    """The user's usage in the allowance window (usage.usage_window) against their limits (the counts the limits use),
    per day and per model."""
    now = current_time()
    start, end = usage.usage_window(ent, now)
    day = usage.utc_day(UsageEvent.created_at)
    rows = await db.execute(
        select(
            day,
            UsageEvent.kind,
            UsageEvent.model_name,
            func.coalesce(func.sum(UsageEvent.total_tokens), 0),
            func.coalesce(func.sum(UsageEvent.prompt_tokens), 0),
            func.coalesce(func.sum(UsageEvent.completion_tokens), 0),
            func.coalesce(func.sum(UsageEvent.allowance_tokens), 0),
            func.coalesce(func.sum(UsageEvent.cost_usd), 0.0),
            func.count(UsageEvent.id),
        )
        .where(UsageEvent.user_id == user.id, UsageEvent.created_at >= start, UsageEvent.created_at < end)
        .group_by(day, UsageEvent.kind, UsageEvent.model_name)
    )
    tokens: defaultdict[str, int] = defaultdict(int)
    costs: defaultdict[str, float] = defaultdict(float)
    events: defaultdict[str, int] = defaultdict(int)
    allowance = 0
    daily: defaultdict[str, defaultdict[str, float]] = defaultdict(lambda: defaultdict(float))
    by_model: defaultdict[tuple[str, str], list[float]] = defaultdict(lambda: [0, 0, 0, 0.0])
    for day_value, kind, model_name, total, prompt, completion, counted, cost, count in rows.tuples():
        if kind not in _KINDS:
            continue
        total, cost, count = int(total or 0), float(cost or 0.0), int(count or 0)
        counted = int(counted or 0) if kind == "chat" else 0
        tokens[kind] += total
        costs[kind] += cost
        events[kind] += count
        allowance += counted
        today = daily[str(day_value)]
        today[f"{kind}_tokens"] += total
        today[f"{kind}_events"] += count
        today["chat_allowance"] += counted
        today["cost"] += cost
        entry = by_model[(model_name, kind)]
        entry[0] += int(prompt or 0) + int(completion or 0)
        entry[1] += counted
        entry[2] += count
        entry[3] += cost

    names: dict[str, str] = {}
    if by_model:
        found = await db.execute(
            select(LLMModel.litellm_model, LLMModel.display_name)
            .where(LLMModel.litellm_model.in_(sorted({name for name, _ in by_model})))
            .order_by(LLMModel.id)
        )
        for litellm_model, display_name in found.tuples():
            names.setdefault(litellm_model, display_name)
    models = [
        MyUsageModel(
            model_name=names.get(model_name, model_name),
            kind=kind,  # type: ignore[arg-type]
            tokens=int(model_tokens),
            allowance_tokens=int(counted),
            requests=int(requests),
            cost_usd=_cost(cost),
        )
        for (model_name, kind), (model_tokens, counted, requests, cost) in by_model.items()
    ]
    models.sort(key=lambda entry: (-entry.cost_usd, -entry.tokens, entry.model_name, entry.kind))

    days: list[MyUsageDay] = []
    current: date = start.date()
    last = min(now.astimezone(timezone.utc), end - timedelta(microseconds=1)).date()
    while current <= last:
        found_day: Mapping[str, float] = daily.get(current.isoformat(), {})
        days.append(
            MyUsageDay(
                date=current.isoformat(),
                chat_allowance=int(found_day.get("chat_allowance", 0)),
                chat_tokens=int(found_day.get("chat_tokens", 0)),
                requests=int(found_day.get("chat_events", 0)),
                images=int(found_day.get("image_events", 0)),
                vision_tokens=int(found_day.get("vision_tokens", 0)),
                embedding_tokens=int(found_day.get("embedding_tokens", 0)),
                cost_usd=_cost(found_day.get("cost", 0.0)),
            )
        )
        current += timedelta(days=1)

    limits = ent.limits
    document_count, storage_bytes = await storage.owned_totals(db, user.id)
    return MyUsage(
        month=start.strftime("%Y-%m"),
        period_start=start,
        resets_at=end,
        chat_allowance=UsageAmount(used=allowance, limit=limits.monthly_token_limit),
        chat_tokens=tokens["chat"],
        images=UsageAmount(used=events["image"], limit=limits.monthly_image_limit),
        vision_tokens=UsageAmount(used=tokens["vision"], limit=limits.vision_monthly_token_limit),
        embedding_tokens=tokens["embedding"],
        requests=events["chat"],
        rate_limit_per_min=limits.rate_limit_per_min,
        cost_usd=UsageCosts(
            total=_cost(sum(costs.values())),
            chat=_cost(costs["chat"]),
            embedding=_cost(costs["embedding"]),
            vision=_cost(costs["vision"]),
            image=_cost(costs["image"]),
        ),
        documents=DocumentUsage(
            count=document_count,
            max_documents=limits.max_documents,
            storage_bytes=storage_bytes,
            max_storage_mb=limits.max_storage_mb,
            max_upload_mb=limits.max_upload_mb,
        ),
        daily=days,
        by_model=models,
    )
