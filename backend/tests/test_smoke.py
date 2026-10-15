"""Smoke test: the app imports, starts, and serves one upload -> process -> chat round trip."""

import os
import subprocess
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, ApiClient, FakeLLM, chat, ingest, txt_bytes
from sqlalchemy import select

from app.db import SessionLocal
from app.models import LLMModel, User

BACKEND_DIR = Path(__file__).resolve().parents[1]


def test_app_and_worker_import_in_a_fresh_interpreter() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import app.main, app.worker"],
        cwd=BACKEND_DIR,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr


async def test_lifespan_syncs_the_admin_and_seeds_the_models(app: object) -> None:
    async with SessionLocal() as session:
        admin = await session.scalar(select(User).where(User.username == ADMIN_USERNAME))
        models = list(await session.scalars(select(LLMModel)))

    assert admin is not None and admin.is_admin and admin.is_active
    assert len(models) == 8
    defaults = {model.kind: model.litellm_model for model in models if model.is_default}
    assert defaults == {
        "chat": "anthropic/claude-opus-5-5",
        "embedding": "openai/text-embedding-3-small",
        "vision": "anthropic/claude-sonnet-5-5",
    }
    disabled = [model.litellm_model for model in models if not model.enabled]
    assert disabled == ["openai/text-embedding-3-large"]


async def test_health(client: ApiClient) -> None:
    response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_admin_login_and_me(client: ApiClient) -> None:
    login = await client.login(ADMIN_USERNAME, ADMIN_PASSWORD)
    me = await client.get("/api/auth/me")

    assert login.status_code == 200
    assert me.status_code == 200
    body = me.json()
    assert body["username"] == ADMIN_USERNAME
    assert body["is_admin"] is True
    assert body["limits"]["monthly_token_limit"] == 0
    assert set(body["usage"]) == {
        "month",
        "chat_tokens",
        "embedding_tokens",
        "vision_tokens",
        "cost_usd",
        "monthly_token_limit",
    }


async def test_models_are_listed_for_users(admin_client: ApiClient) -> None:
    response = await admin_client.get("/api/models")

    assert response.status_code == 200
    models = response.json()
    assert len(models) == 5
    assert models[0] == {**models[0], "display_name": "Claude Opus 5.5", "provider": "anthropic", "is_default": True}


async def test_upload_process_and_chat_round_trip(
    create_user: Callable[..., Awaitable[ApiClient]], fake_llm: FakeLLM
) -> None:
    alice = await create_user("alice")
    document = await ingest(
        alice, "launch-notes.txt", txt_bytes("The secret launch code for the orchid project is ORCHID-7.")
    )
    fake_llm.chunks = ["The code is ", "ORCHID-7 [1]."]

    response, events = await chat(alice, "What is the launch code of the orchid project?")

    assert document["status"] == "ready", document["error"]
    assert document["num_chunks"] == 1
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    assert [name for name, _ in events] == ["meta", "sources", "delta", "delta", "done"]
    meta, sources, done = events[0][1], events[1][1]["sources"], events[-1][1]
    assert [source["document_id"] for source in sources] == [document["id"]]
    assert sources[0]["index"] == 1
    assert "ORCHID-7" in fake_llm.chat_calls[-1]["messages"][0]["content"]
    assert {key: done["usage"][key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")} == {
        "prompt_tokens": 42,
        "completion_tokens": 5,
        "total_tokens": 47,
    }
    assert done["usage"]["cost_usd"] == pytest.approx((42 * 4.0 + 5 * 20.0) / 1_000_000)  # Opus: $4 / $20 per 1M

    detail = (await alice.get(f"/api/conversations/{meta['conversation_id']}")).json()
    assert [message["role"] for message in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1]["id"] == done["assistant_message_id"]
    assert detail["messages"][1]["content"] == "The code is ORCHID-7 [1]."
    assert detail["messages"][1]["sources"] == sources
    assert detail["created_at"].endswith("Z")

    me = (await alice.get("/api/auth/me")).json()
    assert me["usage"]["chat_tokens"] == 47
    assert me["usage"]["embedding_tokens"] > 0
