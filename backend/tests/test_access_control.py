"""Retrieval access control: chat sources only ever come from the caller's own private documents and the
shared knowledge base, optionally narrowed by `document_ids`."""

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from conftest import ApiClient, FakeLLM, SseEvent, chat, fake_embedding, ingest, txt_bytes
from fastapi import FastAPI

from app.db import SessionLocal
from app.models import Document
from app.services import vectorstore

CreateUser = Callable[..., Awaitable[ApiClient]]

VAULT = "Alice's ledger: the vault combination for the quokka archive is 4417-ZEBRA."
GARDEN = "Bob's garden notes: water the tomato seedlings every morning before work."
HANDBOOK = "Company handbook: the office closes early on Fridays for the quokka picnic."
ADMIN_MEMO = "Admin memo: the server room door code was rotated to 9902-HERON last week."
QUESTION = "What is the vault combination for the quokka archive?"
NO_PASSAGES = "no relevant passages were found"


def _sources(events: list[SseEvent]) -> list[dict[str, Any]]:
    return next(data["sources"] for name, data in events if name == "sources")


def _source_ids(events: list[SseEvent]) -> list[int]:
    return [source["document_id"] for source in _sources(events)]


def _system_prompt(fake_llm: FakeLLM) -> str:
    return fake_llm.chat_calls[-1]["messages"][0]["content"]


@pytest.fixture
def qdrant_queries(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Keyword arguments of every Qdrant `query_points` call made during the test."""
    client = vectorstore.get_qdrant()
    query_points = client.query_points
    calls: list[dict[str, Any]] = []

    async def spy(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return await query_points(**kwargs)

    monkeypatch.setattr(client, "query_points", spy)
    return calls


async def test_private_documents_of_other_users_are_never_sources(create_user: CreateUser, fake_llm: FakeLLM) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")
    vault = await ingest(alice, "vault.txt", txt_bytes(VAULT))
    garden = await ingest(bob, "garden.txt", txt_bytes(GARDEN))

    _, alice_events = await chat(alice, QUESTION)
    alice_prompt = _system_prompt(fake_llm)
    bob_response, bob_events = await chat(bob, QUESTION)
    bob_prompt = _system_prompt(fake_llm)

    assert vault["status"] == garden["status"] == "ready"
    assert _source_ids(alice_events) == [vault["id"]]
    assert "4417-ZEBRA" in alice_prompt
    assert _source_ids(bob_events) == [garden["id"]]
    assert "4417-ZEBRA" not in bob_prompt
    assert "4417-ZEBRA" not in bob_response.text
    detail = (await bob.get(f"/api/conversations/{bob_events[0][1]['conversation_id']}")).json()
    stored = [source["document_id"] for message in detail["messages"] for source in message["sources"]]
    assert stored == [garden["id"]]


async def test_naming_another_users_documents_does_not_reach_them(create_user: CreateUser, fake_llm: FakeLLM) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")
    vault = await ingest(alice, "vault.txt", txt_bytes(VAULT))
    garden = await ingest(bob, "garden.txt", txt_bytes(GARDEN))

    response, only_alices = await chat(bob, QUESTION, document_ids=[vault["id"]])
    prompt = _system_prompt(fake_llm)
    _, mixed = await chat(bob, QUESTION, document_ids=[vault["id"], garden["id"]])

    assert response.status_code == 200
    assert [name for name, _ in only_alices][:3] == ["meta", "sources", "delta"]
    assert _sources(only_alices) == []
    assert "4417-ZEBRA" not in prompt
    assert NO_PASSAGES in prompt
    assert _source_ids(mixed) == [garden["id"]]
    assert "4417-ZEBRA" not in _system_prompt(fake_llm)


async def test_admin_chat_only_searches_its_own_private_documents(
    admin_client: ApiClient, create_user: CreateUser, fake_llm: FakeLLM
) -> None:
    alice = await create_user("alice")
    vault = await ingest(alice, "vault.txt", txt_bytes(VAULT))
    memo = await ingest(admin_client, "memo.txt", txt_bytes(ADMIN_MEMO))

    _, admin_events = await chat(admin_client, QUESTION)
    admin_prompt = _system_prompt(fake_llm)
    _, alice_events = await chat(alice, "What is the server room door code?")

    assert memo["scope"] == "private"
    assert _source_ids(admin_events) == [memo["id"]]
    assert "4417-ZEBRA" not in admin_prompt
    assert _source_ids(alice_events) == [vault["id"]]
    assert "9902-HERON" not in _system_prompt(fake_llm)


async def test_shared_documents_are_retrievable_by_everyone(
    admin_client: ApiClient, create_user: CreateUser, fake_llm: FakeLLM
) -> None:
    alice, bob, carol = await create_user("alice"), await create_user("bob"), await create_user("carol")
    handbook = await ingest(admin_client, "handbook.txt", txt_bytes(HANDBOOK), scope="shared")
    vault = await ingest(alice, "vault.txt", txt_bytes(VAULT))
    garden = await ingest(bob, "garden.txt", txt_bytes(GARDEN))

    _, alice_events = await chat(alice, "When does the office close on Fridays?")
    _, bob_events = await chat(bob, "When does the office close on Fridays?")
    _, carol_events = await chat(carol, "When does the office close on Fridays?")
    _, selected = await chat(bob, "When does the office close on Fridays?", document_ids=[handbook["id"]])
    _, admin_events = await chat(admin_client, "When does the office close on Fridays?")

    assert handbook["scope"] == "shared"
    assert set(_source_ids(alice_events)) == {handbook["id"], vault["id"]}
    assert set(_source_ids(bob_events)) == {handbook["id"], garden["id"]}
    assert _source_ids(carol_events) == [handbook["id"]]
    assert _source_ids(selected) == [handbook["id"]]
    assert _source_ids(admin_events) == [handbook["id"]]
    assert "Fridays for the quokka picnic" in _system_prompt(fake_llm)


async def test_document_ids_narrow_retrieval(
    admin_client: ApiClient, create_user: CreateUser, fake_llm: FakeLLM
) -> None:
    alice = await create_user("alice")
    vault = await ingest(alice, "vault.txt", txt_bytes(VAULT))
    garden = await ingest(alice, "garden.txt", txt_bytes(GARDEN))
    handbook = await ingest(admin_client, "handbook.txt", txt_bytes(HANDBOOK), scope="shared")

    _, everything = await chat(alice, QUESTION)
    _, garden_only = await chat(alice, QUESTION, document_ids=[garden["id"]])
    garden_prompt = _system_prompt(fake_llm)
    _, garden_and_handbook = await chat(alice, QUESTION, document_ids=[garden["id"], handbook["id"]])
    _, unknown = await chat(alice, QUESTION, document_ids=[999_999])

    assert _source_ids(everything)[0] == vault["id"]
    assert set(_source_ids(everything)) == {vault["id"], garden["id"], handbook["id"]}
    assert _source_ids(garden_only) == [garden["id"]]
    assert "tomato seedlings" in garden_prompt
    assert "4417-ZEBRA" not in garden_prompt
    assert set(_source_ids(garden_and_handbook)) == {garden["id"], handbook["id"]}
    assert _sources(unknown) == []


async def test_empty_document_ids_disable_retrieval(
    create_user: CreateUser, fake_llm: FakeLLM, qdrant_queries: list[dict[str, Any]]
) -> None:
    alice = await create_user("alice")
    await ingest(alice, "vault.txt", txt_bytes(VAULT))
    embedding_calls = len(fake_llm.embedding_calls)
    embedding_tokens = (await alice.get("/api/auth/me")).json()["usage"]["embedding_tokens"]

    response, events = await chat(alice, QUESTION, document_ids=[])

    assert response.status_code == 200
    assert [name for name, _ in events] == ["meta", "sources", *["delta"] * 5, "done"]
    assert events[1] == ("sources", {"sources": []})
    assert len(fake_llm.embedding_calls) == embedding_calls
    assert qdrant_queries == []
    prompt = _system_prompt(fake_llm)
    assert "</context>" not in prompt
    assert "4417-ZEBRA" not in prompt
    assert NO_PASSAGES not in prompt
    assert (await alice.get("/api/auth/me")).json()["usage"]["embedding_tokens"] == embedding_tokens


async def test_every_search_carries_the_access_filter(
    admin_client: ApiClient, create_user: CreateUser, qdrant_queries: list[dict[str, Any]]
) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")
    vault = await ingest(alice, "vault.txt", txt_bytes(VAULT))
    handbook = await ingest(admin_client, "handbook.txt", txt_bytes(HANDBOOK), scope="shared")
    admin_id = (await admin_client.get("/api/auth/me")).json()["id"]
    searches = [
        (alice, None),
        (alice, [vault["id"]]),
        (bob, None),
        (bob, [vault["id"], handbook["id"]]),
        (admin_client, None),
    ]

    for client, document_ids in searches:
        response, _ = await chat(client, QUESTION, document_ids=document_ids)
        assert response.status_code == 200

    expected_users = [alice.user_id, alice.user_id, bob.user_id, bob.user_id, admin_id]
    assert len(qdrant_queries) == len(searches)
    for call, user_id, (_, document_ids) in zip(qdrant_queries, expected_users, searches, strict=True):
        assert user_id is not None
        assert call["query_filter"] == vectorstore.access_filter(user_id, document_ids)


async def test_deleted_documents_are_no_longer_retrieved(create_user: CreateUser, fake_llm: FakeLLM) -> None:
    alice = await create_user("alice")
    vault = await ingest(alice, "vault.txt", txt_bytes(VAULT))
    garden = await ingest(alice, "garden.txt", txt_bytes(GARDEN))

    deleted = await alice.delete(f"/api/documents/{vault['id']}")
    _, events = await chat(alice, QUESTION)

    assert deleted.status_code == 204
    assert _source_ids(events) == [garden["id"]]
    assert "4417-ZEBRA" not in _system_prompt(fake_llm)


async def test_vectors_outliving_their_document_are_never_cited(create_user: CreateUser, fake_llm: FakeLLM) -> None:
    alice = await create_user("alice")
    vault = await ingest(alice, "vault.txt", txt_bytes(VAULT))
    garden = await ingest(alice, "garden.txt", txt_bytes(GARDEN))
    async with SessionLocal() as db:  # the row goes but its vectors stay, as when a delete races ingestion
        doc = await db.get(Document, vault["id"])
        assert doc is not None and doc.embedding_model_id is not None
        collection = vectorstore.collection_name(doc.embedding_model_id)
        await db.delete(doc)
        await db.commit()

    _, events = await chat(alice, QUESTION)

    assert alice.user_id is not None
    query = fake_embedding(QUESTION, fake_llm.embedding_dim)
    leftovers = await vectorstore.search(collection, query, user_id=alice.user_id, top_k=10)
    assert vault["id"] in {hit.doc_id for hit in leftovers}
    assert _source_ids(events) == [garden["id"]]
    assert [source["index"] for source in _sources(events)] == [1]
    assert "4417-ZEBRA" not in _system_prompt(fake_llm)


@pytest.mark.parametrize(
    ("user_id", "document_ids", "visible"),
    [
        (1, None, {1, 3, 4}),
        (2, None, {2, 3, 4}),
        (3, None, {3, 4}),
        (1, [1], {1}),
        (1, [2], set()),
        (1, [2, 3], {3}),
        (3, [1, 2], set()),
        (1, [], set()),
    ],
)
async def test_vector_search_admits_own_private_and_shared_points_only(
    user_id: int, document_ids: list[int] | None, visible: set[int]
) -> None:
    collection = "access_control_test"
    owners_and_scopes = {1: (1, "private"), 2: (2, "private"), 3: (2, "shared"), 4: (1, "shared")}
    await vectorstore.ensure_collection(collection, 2)
    await vectorstore.upsert_chunks(
        collection,
        [
            vectorstore.ChunkPoint(
                doc_id=doc_id,
                chunk_index=0,
                vector=[1.0, 0.0],
                owner_id=owner_id,
                scope=scope,
                filename=f"doc{doc_id}.txt",
                page=None,
                text=f"Passage of document {doc_id}",
            )
            for doc_id, (owner_id, scope) in owners_and_scopes.items()
        ],
    )

    hits = await vectorstore.search(collection, [1.0, 0.0], user_id=user_id, top_k=10, document_ids=document_ids)

    assert {hit.doc_id for hit in hits} == visible
    assert all(hit.scope == "shared" or hit.owner_id == user_id for hit in hits)
