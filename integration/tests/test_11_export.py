"""Flow 11: the data export ZIP (POST /api/me/export) holds the caller's account, documents and conversations, and
nothing of another user's."""

import io
import json
import zipfile

from harness import chat, check, unique, upload, wait_document
from samples import txt


def _seed(user, marker: str) -> tuple[dict, int]:
    item = upload(user.client, f"{marker}-diary.txt", txt(f"The {marker} diary mentions a picnic by the lake."))
    wait_document(user.client, item["id"])
    result = chat(user.client, f"What does the {marker} diary mention?")
    assert result.names[-1] == "done"
    return item, result.meta["conversation_id"]


def test_export_holds_only_the_callers_data(make_user) -> None:
    alice, bob = make_user("exporter"), make_user("other")
    mine, theirs = unique("mine"), unique("theirs")
    my_doc, my_conversation = _seed(alice, mine)
    _seed(bob, theirs)

    response = check(alice.client.post("/api/me/export"))
    assert response.headers["content-type"] == "application/zip"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;") and f"asknuvexusdoc-export-{alice.username}-" in disposition
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-export-conversations"] == "1" and response.headers["x-export-omitted"] == "0"

    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = set(archive.namelist())
    index = json.loads(archive.read("conversations/index.json"))
    assert [entry["id"] for entry in index] == [my_conversation]
    base = f"conversations/{my_conversation:06d}-"
    assert index[0]["markdown"].startswith(base) and index[0]["json"].startswith(base)
    top_level = {"README.txt", "account.json", "billing.json", "documents.json", "conversations/index.json"}
    assert names == top_level | {index[0]["markdown"], index[0]["json"]}
    transcript = json.loads(archive.read(index[0]["json"]))
    assert [message["role"] for message in transcript["messages"]] == ["user", "assistant"]

    account = json.loads(archive.read("account.json"))
    assert account["username"] == alice.username
    assert account["conversations"]["included"] == 1 and account["conversations"]["omitted"] == 0
    documents = json.loads(archive.read("documents.json"))
    assert [document["filename"] for document in documents] == [my_doc["filename"]]

    everything = "\n".join(archive.read(name).decode("utf-8", "replace") for name in names)
    assert mine in everything
    assert theirs not in everything and bob.username not in everything
    for secret in (alice.password, "password_hash", "sk-it-fake"):
        assert secret not in everything


def test_export_of_a_new_user_is_empty(make_user) -> None:
    user = make_user("empty-export")
    response = check(user.client.post("/api/me/export"))
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert response.headers["x-export-conversations"] == "0"
    assert json.loads(archive.read("documents.json")) == []
    assert json.loads(archive.read("conversations/index.json")) == []
    assert json.loads(archive.read("account.json"))["username"] == user.username
