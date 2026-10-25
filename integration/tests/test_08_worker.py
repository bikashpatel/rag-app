"""Flow 8: ingestion survives the worker being down. Uploads made while it is stopped wait in the Redis queue as
pending; once the worker starts again it picks them up and they become ready."""

import time

import pytest
from harness import (
    compose,
    document,
    redis_cli,
    services,
    unique,
    upload,
    wait_document,
    wait_queue_idle,
    wait_until,
)
from samples import md, txt


@pytest.mark.deadline(300)
def test_uploads_wait_for_a_stopped_worker(make_user, admin, fake) -> None:
    user = make_user("worker")
    markers = [unique("dormant"), unique("dormant")]
    wait_queue_idle(admin)  # a graceful stop waits for running jobs (up to 60 s)
    compose("stop", "worker", timeout=150)
    try:
        assert services()["worker"]["state"] == "exited"
        mark = fake.mark()
        items = [
            upload(user.client, f"{markers[0]}.txt", txt(f"The {markers[0]} file waited for the worker.")),
            upload(user.client, f"{markers[1]}.md", md(f"The {markers[1]} notes waited for the worker too.")),
        ]
        queued = redis_cli("LRANGE", "rag:ingest_queue", "0", "-1").split()
        assert {str(item["id"]) for item in items} <= set(queued), queued
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:  # nothing indexes them while the worker is down
            assert all(document(user.client, item["id"])["status"] == "pending" for item in items)
            time.sleep(0.5)
        assert fake.log("embedding", after=mark) == []
    finally:
        compose("start", "worker", timeout=150)
    wait_until(lambda: services()["worker"]["state"] == "running", 60, "the worker to run again")
    for item in items:
        assert wait_document(user.client, item["id"], timeout=120)["num_chunks"] >= 1
    embedded = [text for entry in fake.log("embedding", after=mark) for text in entry["input"]]
    assert all(any(marker in text for text in embedded) for marker in markers)
    assert str(items[0]["id"]) not in redis_cli("LRANGE", "rag:ingest_queue", "0", "-1").split()
