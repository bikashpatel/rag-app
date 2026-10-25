"""The thin wrappers around the stores: stored file names that can't leave the upload directory, the Qdrant collection's
creation races, and the best-effort steps when Redis, Qdrant or the disk fail (logged; the request still succeeds)."""

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import ApiClient, chat, fake_embedding
from redis.exceptions import ConnectionError as RedisConnectionError

from app.config import settings
from app.redis_client import forget_ingest_attempts, get_redis, ingest_attempts_key
from app.services import storage, usage, vectorstore

CreateUser = Callable[..., Awaitable[ApiClient]]


async def redis_down(*args: Any, **kwargs: Any) -> Any:
    raise RedisConnectionError("Connection refused")


class RedisDown:
    """A Redis client whose every command and pipeline fails."""

    def __getattr__(self, name: str) -> Any:
        return redis_down

    def pipeline(self, *args: Any, **kwargs: Any) -> Any:
        raise RedisConnectionError("Connection refused")


# ---------------- Stored file names ----------------


@pytest.mark.parametrize(
    "name", ["", ".", "..", "../app.db", "a/b.pdf", "..\\b.pdf", "a\\b.pdf", ".env", "/etc/passwd"]
)
def test_stored_names_that_could_leave_the_upload_directory_are_refused(name: str) -> None:
    with pytest.raises(ValueError, match="invalid stored file name"):
        storage.stored_path(name)


def test_generated_stored_names_stay_in_the_upload_directory() -> None:
    name = storage.new_stored_name(".PDF")
    path = storage.stored_path(name)
    assert name.endswith(".pdf") and len(name) == 36
    assert path.parent == Path(settings.upload_dir) and path.name == name
    for sidecar in (storage.source_name(name), storage.preview_name(name), storage.thumb_name(name)):
        assert storage.stored_path(sidecar).parent == path.parent


def test_a_file_that_cant_be_removed_is_logged_and_the_rest_still_go(caplog: pytest.LogCaptureFixture) -> None:
    name = storage.new_stored_name(".png")
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / name).mkdir()  # can't be unlinked
    (upload_dir / name / "inside").write_text("x")
    preview = upload_dir / storage.preview_name(name)
    preview.write_bytes(b"webp")

    with caplog.at_level(logging.ERROR, logger="app.services.storage"):
        storage.remove_file(name)

    assert f"Could not remove uploaded file {name}" in caplog.text
    assert not preview.exists()


# ---------------- Qdrant ----------------


async def test_a_collection_created_meanwhile_by_another_process_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    client = vectorstore.get_qdrant()
    create = client.create_collection

    async def lose_the_race(**kwargs: Any) -> bool:
        await create(**kwargs)
        raise RuntimeError("Collection `rag_chunks_9` already exists!")

    monkeypatch.setattr(client, "create_collection", lose_the_race)

    await vectorstore.ensure_collection("rag_chunks_9", 4)

    assert await vectorstore.collection_exists("rag_chunks_9")


async def test_a_collection_that_cant_be_created_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    client = vectorstore.get_qdrant()

    async def unreachable(**kwargs: Any) -> bool:
        raise ConnectionError("Qdrant is unreachable")

    monkeypatch.setattr(client, "create_collection", unreachable)

    with pytest.raises(ConnectionError):
        await vectorstore.ensure_collection("rag_chunks_9", 4)
    assert not await vectorstore.collection_exists("rag_chunks_9")


async def test_payload_indexes_that_cant_be_created_dont_fail_the_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    client = vectorstore.get_qdrant()

    async def unsupported(**kwargs: Any) -> None:
        raise RuntimeError("payload indexes aren't supported here")

    monkeypatch.setattr(client, "create_payload_index", unsupported)

    await vectorstore.ensure_collection("rag_chunks_9", 4)
    point = vectorstore.ChunkPoint(1, 0, fake_embedding("tide tables", 4), 7, "private", "a.txt", None, "tide tables")
    await vectorstore.upsert_chunks("rag_chunks_9", [point])

    hits = await vectorstore.search("rag_chunks_9", fake_embedding("tide tables", 4), user_id=7, top_k=5)
    assert [(hit.doc_id, hit.owner_id, hit.scope) for hit in hits] == [(1, 7, "private")]
    assert await vectorstore.search("rag_chunks_9", fake_embedding("tide tables", 4), user_id=8, top_k=5) == []


async def test_writing_no_points_never_calls_qdrant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vectorstore.get_qdrant(), "upsert", redis_down)

    await vectorstore.upsert_chunks("rag_chunks_9", [])


# ---------------- Redis ----------------


async def test_chat_slots_are_released_and_refreshed_best_effort(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(usage, "get_redis", RedisDown)

    with caplog.at_level(logging.WARNING, logger="app.services.usage"):
        await usage.release_chat_slot(7, "slot-a")
        await usage.refresh_chat_slot(7, "slot-a")
        await usage.release_chat_slot(7, None)  # no slot (no budget): Redis isn't asked
        await usage.refresh_chat_slot(7, None)

    assert [record.getMessage() for record in caplog.records] == [
        "Could not release a chat slot of user 7: Connection refused",
        "Could not refresh a chat slot of user 7: Connection refused",
    ]


async def test_an_answer_is_saved_when_its_chat_slot_cant_be_released(
    create_user: CreateUser, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    alice = await create_user("alice", monthly_token_limit=100_000)
    monkeypatch.setattr(get_redis(), "zrem", redis_down)

    with caplog.at_level(logging.WARNING, logger="app.services.usage"):
        response, events = await chat(alice, "Hello?")

    assert response.status_code == 200 and events[-1][0] == "done"
    assert f"Could not release a chat slot of user {alice.user_id}" in caplog.text
    conversation = (await alice.get(f"/api/conversations/{events[0][1]['conversation_id']}")).json()
    assert [message["role"] for message in conversation["messages"]] == ["user", "assistant"]
    again, _ = await chat(alice, "Hello again?")  # the stuck slot expires on its own; one doesn't block the user
    assert again.status_code == 200


async def test_ingestion_attempts_are_forgotten_best_effort(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    redis = get_redis()
    await redis.set(ingest_attempts_key(3), 2)
    await forget_ingest_attempts([3])
    await forget_ingest_attempts([])
    assert await redis.get(ingest_attempts_key(3)) is None

    monkeypatch.setattr(redis, "delete", redis_down)
    with caplog.at_level(logging.WARNING, logger="app.redis_client"):
        await forget_ingest_attempts([3, 4])

    assert "Could not reset the ingestion attempts of 2 document(s): Connection refused" in caplog.text
