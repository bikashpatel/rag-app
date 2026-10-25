"""Flow 4: access control across backend, Qdrant and the model. A user's private passages never reach another user's
sources or prompt (checked in what the fake received); the admin's shared knowledge base reaches everyone; deleting a
document removes its vectors and file, so it is never retrieved again."""

import pytest
from harness import (
    assert_sse_contract,
    chat,
    check,
    document,
    qdrant_points,
    stored_files,
    stored_names,
    system_prompt,
    unique,
    upload,
    wait_document,
)
from samples import docx, txt


def _prompts_after(fake, mark: int) -> str:
    return "\n".join(system_prompt(entry) for entry in fake.log("chat", after=mark))


@pytest.mark.deadline(240)
def test_private_passages_never_reach_another_user(make_user, fake, models: dict) -> None:
    alice, bob = make_user("alice"), make_user("bob")
    marker, secret = unique("vault"), unique("secretcode")
    private = upload(
        alice.client, f"{marker}.txt", txt(f"The {marker} vault code is {secret}. Keep the {marker} code safe.")
    )
    wait_document(alice.client, private["id"])
    assert qdrant_points(models["embed_a"]["id"], [private["id"]])[private["id"]] >= 1

    question = f"What is the {marker} vault code?"
    mark = fake.mark()
    own = chat(alice.client, question)
    assert own.sources[0]["document_id"] == private["id"] and secret in own.text
    assert secret in _prompts_after(fake, mark)

    mark = fake.mark()
    for selection in (None, [private["id"]]):
        other = chat(bob.client, question, document_ids=selection)
        assert_sse_contract(other)
        assert private["id"] not in {source["document_id"] for source in other.sources}
        assert secret not in other.text
    assert secret not in _prompts_after(fake, mark), "Alice's passage reached Bob's prompt"
    assert document(bob.client, private["id"]) is None


@pytest.mark.deadline(240)
def test_shared_knowledge_base_reaches_every_user(make_user, admin, fake) -> None:
    alice, bob = make_user("alice"), make_user("bob")
    marker, fact = unique("policy"), unique("allowance")
    shared = upload(
        admin, f"{marker}-policy.docx", docx(f"The {marker} travel policy grants the {fact} daily."), scope="shared"
    )
    assert shared["scope"] == "shared"
    wait_document(admin, shared["id"])
    try:
        for user in (alice, bob):
            listed = document(user.client, shared["id"])
            assert listed is not None and listed["scope"] == "shared" and listed["can_delete"] is False
            download = user.client.get(f"/api/documents/{shared['id']}/download")
            assert download.status_code == 200 and download.content[:2] == b"PK"
            mark = fake.mark()
            result = chat(user.client, f"What does the {marker} travel policy grant?")
            assert result.sources[0]["document_id"] == shared["id"]
            assert fact in result.text and fact in _prompts_after(fake, mark)
            assert user.client.delete(f"/api/documents/{shared['id']}").status_code == 404
            assert user.client.post(f"/api/documents/{shared['id']}/reprocess").status_code == 404
    finally:
        check(admin.delete(f"/api/documents/{shared['id']}"), 204)
    assert document(alice.client, shared["id"]) is None


@pytest.mark.deadline(240)
def test_deleting_a_document_removes_vectors_file_and_retrieval(make_user, fake, models: dict) -> None:
    user = make_user("deleter")
    marker, fact = unique("orchard"), unique("harvest")
    item = upload(user.client, f"{marker}.txt", txt(f"The {marker} orchard report says the {fact} starts in May."))
    wait_document(user.client, item["id"])
    (stored,) = stored_names([item["id"]]).values()
    assert stored in stored_files()
    assert qdrant_points(models["embed_a"]["id"], [item["id"]])[item["id"]] >= 1
    question = f"When does the {marker} orchard harvest start?"
    assert chat(user.client, question).sources[0]["document_id"] == item["id"]

    check(user.client.delete(f"/api/documents/{item['id']}"), 204)
    assert document(user.client, item["id"]) is None
    assert qdrant_points(models["embed_a"]["id"], [item["id"]])[item["id"]] == 0
    assert stored not in stored_files()
    mark = fake.mark()
    after = chat(user.client, question)
    assert item["id"] not in {source["document_id"] for source in after.sources}
    assert fact not in _prompts_after(fake, mark)
    assert user.client.get(f"/api/documents/{item['id']}/download").status_code == 404
