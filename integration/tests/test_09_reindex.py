"""Flow 9: switching the active embedding model (to the fake's 384-dimension model) marks documents stale; re-indexing
re-embeds them through the worker into the new model's own Qdrant collection, and they are searchable again. The test
switches back to the first model at the end, so later tests see the suite's normal setup."""

import pytest
from harness import (
    chat,
    check,
    document,
    qdrant_points,
    qdrant_vector_size,
    unique,
    upload,
    wait_document,
    wait_queue_idle,
    wait_until,
)
from samples import txt


def _switch(admin, model_id: int) -> int:
    check(admin.patch(f"/api/admin/models/{model_id}", json={"is_default": True}))
    stats = check(admin.get("/api/admin/stats")).json()
    assert stats["active_embedding_model"]["id"] == model_id
    return stats["stale_documents"]


@pytest.mark.deadline(600)
def test_switching_the_embedding_model_reindexes(make_user, admin, models: dict, fake) -> None:
    first, second = models["embed_a"]["id"], models["embed_b"]["id"]
    user = make_user("reindex")
    marker = unique("glacier")
    question = f"What did the {marker} glacier survey measure?"
    item = upload(user.client, f"{marker}.txt", txt(f"The {marker} glacier survey measured eleven deep crevasses."))
    chunks = wait_document(user.client, item["id"])["num_chunks"]
    assert qdrant_points(first, [item["id"]])[item["id"]] == chunks
    wait_queue_idle(admin)
    try:
        assert _switch(admin, second) >= 1
        stale = document(user.client, item["id"])
        assert stale["status"] == "ready" and stale["is_stale"] is True
        assert item["id"] not in {source["document_id"] for source in chat(user.client, question).sources}

        mark = fake.mark()
        assert check(admin.post("/api/admin/reindex", json={"only_stale": True})).json()["queued"] >= 1
        wait_queue_idle(admin, timeout=400)
        assert check(admin.get("/api/admin/stats")).json()["stale_documents"] == 0
        fresh = wait_document(user.client, item["id"])
        assert fresh["is_stale"] is False and fresh["num_chunks"] == chunks
        assert qdrant_vector_size(second) == 384
        assert qdrant_points(second, [item["id"]])[item["id"]] == chunks
        assert qdrant_points(first, [item["id"]])[item["id"]] == 0
        embeddings = fake.log("embedding", after=mark)
        assert embeddings and {entry["model"] for entry in embeddings} == {"fake-embed-b"}
        assert any(marker in text for entry in embeddings for text in entry["input"])
        assert chat(user.client, question).sources[0]["document_id"] == item["id"]
    finally:
        _switch(admin, first)
        check(admin.post("/api/admin/reindex", json={"only_stale": True}))
        wait_queue_idle(admin, timeout=400)
    wait_until(lambda: not document(user.client, item["id"])["is_stale"], 30, "the document to be fresh again")
    assert qdrant_points(first, [item["id"]])[item["id"]] == chunks
    assert chat(user.client, question).sources[0]["document_id"] == item["id"]


def test_the_active_embedding_model_cannot_be_disabled(admin, models: dict) -> None:
    active = models["embed_a"]["id"]
    for change in ({"enabled": False}, {"is_default": False}):
        response = admin.patch(f"/api/admin/models/{active}", json=change)
        assert response.status_code == 409, change
    assert admin.delete(f"/api/admin/models/{active}").status_code == 409
