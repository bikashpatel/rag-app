"""Flow 2: every supported format goes upload -> file on the shared volume -> Redis queue -> worker (parse, chunk,
embed through the fake) -> Qdrant -> ready, and is retrievable in chat. Corrupt files fail with a readable error;
Retry works once the cause is fixed."""

import os
from dataclasses import dataclass

import pytest
from harness import (
    User,
    assert_sse_contract,
    chat,
    check,
    document,
    qdrant_points,
    stored_files,
    stored_names,
    unique,
    upload,
    wait_document,
    wait_until,
)
from samples import BUILDERS, sample

FORMATS = sorted(BUILDERS)


@dataclass
class Corpus:
    user: User
    markers: dict[str, str]  # extension -> the unique word in its text
    items: dict[str, dict]  # extension -> the DocumentItem once ready
    embedded: list[str]  # every text the fake embedded while the corpus was indexed


@pytest.fixture(scope="module")
def corpus(user_factory, fake) -> Corpus:
    user = user_factory("ingest")
    mark = fake.mark()
    uploaded = {}
    markers = {}
    for extension in FORMATS:
        marker = unique(f"ledger{extension.strip('.')}")
        text = f"The {marker} register notes that the {marker} audit found seventeen anomalies in the vault archive."
        item = upload(user.client, f"sample-{marker}{extension}", sample(extension, text))
        assert item["status"] == "pending" and item["scope"] == "private" and item["owner_id"] == user.id
        uploaded[extension], markers[extension] = item, marker
    items = {extension: wait_document(user.client, item["id"]) for extension, item in uploaded.items()}
    embedded = [text for entry in fake.log("embedding", after=mark) for text in entry["input"]]
    return Corpus(user, markers, items, embedded)


def test_config_lists_exactly_the_tested_formats(make_user) -> None:
    config = check(make_user("config").client.get("/api/documents/config")).json()
    assert sorted(config["supported_extensions"]) == FORMATS


@pytest.mark.parametrize("extension", FORMATS)
def test_format_is_parsed_chunked_and_embedded(corpus: Corpus, extension: str) -> None:
    item, marker = corpus.items[extension], corpus.markers[extension]
    assert item["status"] == "ready" and item["error"] is None and not item["is_stale"]
    assert item["num_chunks"] >= 1
    if extension == ".pdf":
        assert item["num_pages"] == 1
    assert any(marker in text for text in corpus.embedded), f"the worker never embedded the text of {extension}"


def test_files_are_on_the_shared_volume_and_vectors_in_qdrant(corpus: Corpus, models: dict) -> None:
    ids = [item["id"] for item in corpus.items.values()]
    names = stored_names(ids)
    on_disk = stored_files()
    assert set(names.values()) <= on_disk
    counts = qdrant_points(models["embed_a"]["id"], ids)
    assert {doc_id: counts[doc_id] for doc_id in ids} == {
        item["id"]: item["num_chunks"] for item in corpus.items.values()
    }


@pytest.mark.parametrize("extension", FORMATS)
def test_format_is_retrievable_in_chat(corpus: Corpus, extension: str) -> None:
    item, marker = corpus.items[extension], corpus.markers[extension]
    result = chat(corpus.user.client, f"What does the {marker} register say?", document_ids=[item["id"]])
    assert_sse_contract(result)
    assert result.sources and result.sources[0]["document_id"] == item["id"]
    assert marker in result.sources[0]["snippet"]
    assert "[1]" in result.text and marker in result.text


def test_queue_is_drained(corpus: Corpus, admin) -> None:
    assert check(admin.get("/api/admin/stats")).json()["queue_length"] == 0


@pytest.mark.parametrize(
    ("filename", "data"),
    [
        ("broken.pdf", b"%PDF-1.7\n" + os.urandom(2048)),
        ("broken.docx", b"PK\x03\x04" + os.urandom(2048)),
        ("broken.pptx", b"PK\x03\x04" + os.urandom(2048)),
        ("broken.xlsx", os.urandom(2048)),
    ],
)
def test_corrupt_file_fails_with_a_readable_error(make_user, filename: str, data: bytes) -> None:
    user = make_user("corrupt")
    item = upload(user.client, filename, data)
    failed = wait_document(user.client, item["id"], status="failed")
    error = failed["error"]
    assert error == f"The file is corrupted or not a valid {os.path.splitext(filename)[1]} file."
    retried = check(user.client.post(f"/api/documents/{item['id']}/reprocess")).json()
    assert retried["status"] == "pending"
    assert wait_document(user.client, item["id"], status="failed")["error"] == error


def test_rejected_uploads(make_user) -> None:
    user = make_user("rejects")
    assert user.client.post("/api/documents", files={"file": ("empty.txt", b"")}).status_code == 422
    unsupported = user.client.post("/api/documents", files={"file": ("tool.exe", b"MZ" + b"\0" * 64)})
    assert unsupported.status_code == 415
    legacy = user.client.post("/api/documents", files={"file": ("old.doc", b"\xd0\xcf\x11\xe0" + b"\0" * 64)})
    assert legacy.status_code == 415 and "Legacy .doc" in legacy.json()["detail"]
    data = sample(".txt", f"duplicate {unique('dup')} text that is long enough to be indexed")
    first = upload(user.client, "first.txt", data)
    duplicate = user.client.post("/api/documents", files={"file": ("second.txt", data)})
    assert duplicate.status_code == 409 and duplicate.json()["detail"] == "This file has already been uploaded."
    wait_document(user.client, first["id"])


def test_embedding_outage_fails_the_document_and_retry_recovers(make_user, fake) -> None:
    user = make_user("outage")
    marker = unique("outage")
    with fake.knobs(fail_embeddings=1000):
        item = upload(user.client, f"{marker}.txt", sample(".txt", f"The {marker} report covers the outage drill."))
        failed = wait_document(user.client, item["id"], status="failed")
    assert failed["error"] and "Traceback" not in failed["error"]
    mark = fake.mark()
    check(user.client.post(f"/api/documents/{item['id']}/reprocess"))
    ready = wait_document(user.client, item["id"])
    assert ready["num_chunks"] >= 1
    assert any(marker in text for entry in fake.log("embedding", after=mark) for text in entry["input"])


def test_other_users_cannot_see_or_retry_a_document(corpus: Corpus, make_user) -> None:
    other = make_user("snoop")
    doc_id = corpus.items[".txt"]["id"]
    assert document(other.client, doc_id) is None
    for method, path in (
        ("POST", f"/api/documents/{doc_id}/reprocess"),
        ("DELETE", f"/api/documents/{doc_id}"),
        ("GET", f"/api/documents/{doc_id}/download"),
    ):
        response = other.client.request(method, path)
        assert response.status_code == 404 and response.json()["detail"] == "Document not found", (method, path)
    wait_until(lambda: document(corpus.user.client, doc_id)["status"] == "ready", 5, "the document to stay ready")
