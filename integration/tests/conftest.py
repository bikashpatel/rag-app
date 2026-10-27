"""Fixtures for the integration suite. The stack is started by integration/run.sh; these only wait for it and
configure it through its own APIs: the admin from the generated env file, fake models as the only enabled ones."""

import signal
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from harness import (
    APP_URL,
    ENV,
    FAKE_API_BASE,
    FAKE_LLM_URL,
    FakeLLM,
    User,
    check,
    live_containers,
    login,
    unique,
    wait_until,
)

DEFAULT_DEADLINE = 180
# Users get an open-ended complimentary Professional plan (every model, files, images and Gmail) and no limits unless a
# test sets them, as overrides of the plan's, so tests don't trip over the plan's (30 chats a minute).
NO_LIMITS = {"monthly_token_limit": 0, "rate_limit_per_min": 0, "monthly_image_limit": 0}
LIMIT_NAMES = (
    "monthly_token_limit",
    "rate_limit_per_min",
    "max_upload_mb",
    "max_documents",
    "max_storage_mb",
    "monthly_image_limit",
    "vision_monthly_token_limit",
)
FAKE_MODELS = {
    "chat": {"display_name": "IT Fake Chat", "litellm_model": "openai/gpt-4o-mini", "kind": "chat", "is_default": True},
    "chat2": {"display_name": "IT Fake Chat Two", "litellm_model": "openai/gpt-4.1-mini", "kind": "chat"},
    "embed_a": {
        "display_name": "IT Fake Embed A",
        "litellm_model": "openai/fake-embed-a",
        "kind": "embedding",
        "is_default": True,
    },
    "embed_b": {"display_name": "IT Fake Embed B", "litellm_model": "openai/fake-embed-b", "kind": "embedding"},
    "vision": {
        "display_name": "IT Fake Vision",
        "litellm_model": "openai/gpt-4o-mini",
        "kind": "vision",
        "is_default": True,
    },
    "image": {
        "display_name": "IT Fake Image",
        "litellm_model": "openai/gpt-image-1",
        "kind": "image",
        "is_default": True,
    },
}
# Explicit prices, so costs are exact: USD per 1M tokens.
PRICES = {"chat": (1.0, 2.0), "embedding": (0.5, 0.0), "vision": (3.0, 4.0), "image": (5.0, 40.0)}


# The live stack's containers when the session started (read-only `docker ps`), compared at the end (test_99_final.py).
LIVE_BASELINE: dict[str, tuple[str, str]] = {}


def pytest_sessionstart(session: pytest.Session) -> None:
    LIVE_BASELINE.update(live_containers())


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Iterator[None]:
    marker = item.get_closest_marker("deadline")
    seconds = int(marker.args[0]) if marker else DEFAULT_DEADLINE

    def expired(signum: int, frame: Any) -> None:
        raise TimeoutError(f"{item.nodeid} ran longer than its {seconds} s deadline")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.alarm(seconds)
    try:
        return (yield)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


@pytest.fixture(scope="session")
def stack() -> None:
    """The stack answers through nginx and the fake model server is up."""

    def up() -> bool:
        try:
            return (
                httpx.get(f"{APP_URL}/api/health", timeout=3).status_code == 200
                and httpx.get(f"{FAKE_LLM_URL}/health", timeout=3).status_code == 200
            )
        except httpx.HTTPError:
            return False

    wait_until(up, 120, "the stack to answer /api/health", interval=1)


@pytest.fixture(scope="session")
def admin(stack: None) -> httpx.Client:
    return login(ENV["ADMIN_USERNAME"], ENV["ADMIN_PASSWORD"])


@pytest.fixture(scope="session")
def fake(stack: None) -> FakeLLM:
    return FakeLLM()


def _model_body(spec: dict[str, Any]) -> dict[str, Any]:
    prices = PRICES[spec["kind"]]
    return {
        **spec,
        "enabled": True,
        "is_default": spec.get("is_default", False),
        "api_base": FAKE_API_BASE,
        "input_cost_per_mtok": prices[0],
        "output_cost_per_mtok": prices[1],
    }


@pytest.fixture(scope="session")
def models(admin: httpx.Client) -> dict[str, dict]:
    """Add the fake models (made the defaults: the active embedding, vision and image models and the default chat
    model) and disable every other model, so no seeded real model can be used. Idempotent on a kept stack."""
    existing = {model["display_name"]: model for model in check(admin.get("/api/admin/models")).json()}
    result = {}
    for key, spec in FAKE_MODELS.items():
        body = _model_body(spec)
        if spec["display_name"] in existing:
            update = {name: value for name, value in body.items() if name != "kind"}
            result[key] = check(
                admin.patch(f"/api/admin/models/{existing[spec['display_name']]['id']}", json=update)
            ).json()
        else:
            result[key] = check(admin.post("/api/admin/models", json=body), 201).json()
    for model in check(admin.get("/api/admin/models")).json():
        if model["api_base"] != FAKE_API_BASE and model["enabled"]:
            check(admin.patch(f"/api/admin/models/{model['id']}", json={"enabled": False}))
    enabled = [model for model in check(admin.get("/api/admin/models")).json() if model["enabled"]]
    assert enabled and all(model["api_base"] == FAKE_API_BASE for model in enabled), enabled
    return result


@pytest.fixture(scope="session")
def user_factory(admin: httpx.Client, models: dict[str, dict]) -> Callable[..., User]:
    """user_factory("name", **fields) -> a new user with an open-ended Professional plan, logged in; limits among the
    fields are overrides (none unless given), the rest go into the create request."""
    plans = check(admin.get("/api/admin/plans")).json()
    professional = next(plan["id"] for plan in plans if plan["code"] == "professional")

    def create(name: str = "user", **fields: Any) -> User:
        username = unique(f"it-{name}-")
        password = f"pw-{unique('')}-secret"
        overrides = {**NO_LIMITS, **{key: value for key, value in fields.items() if key in LIMIT_NAMES}}
        body = {
            "username": username,
            "password": password,
            "overrides": overrides,
            "confirm_unlimited": True,
            "grant": {"plan_id": professional, "days": None, "reason": "Integration test user"},
            **{key: value for key, value in fields.items() if key not in LIMIT_NAMES},
        }
        created = check(admin.post("/api/admin/users", json=body), 201).json()
        return User(created["id"], username, password, login(username, password))

    return create


@pytest.fixture
def make_user(user_factory: Callable[..., User]) -> Callable[..., User]:
    return user_factory
