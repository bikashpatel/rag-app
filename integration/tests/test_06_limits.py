"""Flow 6: the per-user usage controls, enforced by the real backend with Redis and Postgres: requests per minute,
the chat allowance, allowed models, and the upload limits (file size, document count, storage)."""

import time

import pytest
from harness import chat, check, unique, upload, wait_until


def _text_of_size(marker: str, size: int) -> bytes:
    line = f"The {marker} storage test line repeats words for the upload limit checks.\n".encode()
    return (line * (size // len(line) + 1))[:size]


def test_requests_per_minute(make_user) -> None:
    user = make_user("rate", rate_limit_per_min=2)
    # The window is the clock minute: start early in one so three requests can't straddle two windows.
    wait_until(lambda: time.time() % 60 < 40, 25, "the start of a fresh minute", interval=0.5)
    first, second = chat(user.client, "first question"), chat(user.client, "second question")
    assert first.status == second.status == 200 and first.names[-1] == second.names[-1] == "done"
    third = chat(user.client, "third question")
    assert third.status == 429
    assert third.body["detail"].startswith("Rate limit reached (2 requests per minute)")
    assert 1 <= int(third.headers["retry-after"]) <= 60


def test_chat_allowance(make_user, admin) -> None:
    user = make_user("budget", monthly_token_limit=1)
    first = chat(user.client, "What is a budget?")
    assert first.status == 200 and first.names[-1] == "done"
    second = chat(user.client, "And a second question?")
    assert second.status == 429 and second.body["code"] == "allowance_used"
    assert second.body["detail"].startswith("You've used this period's chat allowance (")
    usage = check(user.client.get("/api/me/usage")).json()
    assert usage["chat_allowance"]["limit"] == 1 and usage["chat_allowance"]["used"] >= 1
    assert usage["chat_tokens"] == first.done["usage"]["total_tokens"]
    check(
        admin.patch(
            f"/api/admin/users/{user.id}", json={"overrides": {"monthly_token_limit": 0}, "confirm_unlimited": True}
        )
    )
    assert chat(user.client, "Can I ask again?").names[-1] == "done"


def test_allowed_models(make_user, admin, models: dict) -> None:
    chat_id, other_id = models["chat"]["id"], models["chat2"]["id"]
    user = make_user("models", restrict_models=True, allowed_model_ids=[chat_id])
    assert [model["id"] for model in check(user.client.get("/api/models")).json()] == [chat_id]
    refused = chat(user.client, "Use the other model", model_id=other_id)
    assert refused.status == 403
    allowed = chat(user.client, "Use my model", model_id=chat_id)
    assert allowed.status == 200 and allowed.meta["model"]["id"] == chat_id
    check(admin.patch(f"/api/admin/users/{user.id}", json={"allowed_model_ids": [other_id]}))
    assert [model["id"] for model in check(user.client.get("/api/models")).json()] == [other_id]
    assert chat(user.client, "Now the other one", model_id=other_id).meta["model"]["id"] == other_id
    assert chat(user.client, "And mine again", model_id=chat_id).status == 403


def test_max_file_size(make_user) -> None:
    user = make_user("size", max_upload_mb=1)
    response = user.client.post("/api/documents", files={"file": ("big.txt", _text_of_size(unique("big"), 1_200_000))})
    assert response.status_code == 413
    assert "1 MB" in response.json()["detail"]
    upload(user.client, "small.txt", _text_of_size(unique("small"), 200_000))


def test_max_documents(make_user) -> None:
    user = make_user("count", max_documents=2)
    for number in range(2):
        upload(user.client, f"doc{number}.txt", _text_of_size(unique("count"), 2_000))
    response = user.client.post("/api/documents", files={"file": ("third.txt", _text_of_size(unique("count"), 2_000))})
    assert response.status_code == 409
    assert "2" in response.json()["detail"] and "document" in response.json()["detail"].lower()
    config = check(user.client.get("/api/documents/config")).json()
    assert (config["document_count"], config["max_documents"]) == (2, 2)


@pytest.mark.deadline(240)
def test_max_storage(make_user) -> None:
    user = make_user("storage", max_storage_mb=1)
    upload(user.client, "first.txt", _text_of_size(unique("store"), 600_000))
    response = user.client.post(
        "/api/documents", files={"file": ("second.txt", _text_of_size(unique("store"), 600_000))}
    )
    assert response.status_code == 409
    assert response.json()["detail"].startswith("This upload would exceed your storage limit of 1 MB")
    config = check(user.client.get("/api/documents/config")).json()
    assert config["storage_bytes"] == 600_000 and config["max_storage_mb"] == 1


def test_admin_bypasses_limits(admin) -> None:
    me = check(admin.get("/api/auth/me")).json()
    assert me["limits"]["monthly_token_limit"] == 0 and me["limits"]["rate_limit_per_min"] == 0
    results = [chat(admin, f"admin question {number}", document_ids=[]) for number in range(3)]
    assert all(result.names[-1] == "done" for result in results)
