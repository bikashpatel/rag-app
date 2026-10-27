"""Flow 10: deleting a user removes their documents (rows, stored files and sidecars on the shared volume, vectors in
Qdrant), conversations, messages, usage and sessions, checked through the API, Postgres, Qdrant and the volume."""

import pytest
from harness import (
    chat,
    check,
    client,
    qdrant_points,
    sql_value,
    stored_files,
    stored_names,
    unique,
    upload,
    wait_document,
)
from samples import docx, txt

OWNED_ROWS = {
    "documents": "owner_id",
    "conversations": "user_id",
    "usage_events": "user_id",
    "login_events": "user_id",
    "plan_periods": "user_id",
    "user_billing_profiles": "user_id",
}


def _counts(user_id: int) -> dict[str, int]:
    counts = {
        table: int(sql_value(f"select count(*) from {table} where {column} = {user_id}"))
        for table, column in OWNED_ROWS.items()
    }
    conversations = f"select id from conversations where user_id = {user_id}"
    counts["messages"] = int(sql_value(f"select count(*) from messages where conversation_id in ({conversations})"))
    return counts


@pytest.mark.deadline(240)
def test_deleting_a_user_removes_all_their_data(make_user, admin, models: dict) -> None:
    user, bystander = make_user("doomed"), make_user("bystander")
    marker = unique("comet")
    items = [
        upload(user.client, f"{marker}-notes.txt", txt(f"The {marker} comet notes describe a bright tail.")),
        upload(user.client, f"{marker}-log.docx", docx(f"The {marker} observation log lists four nights.")),
    ]
    for item in items:
        wait_document(user.client, item["id"])
    result = chat(user.client, f'Please create a PDF file titled "{marker} summary" about the {marker} comet notes.')
    generated = result.all("file")[-1]["document"]
    wait_document(user.client, generated["id"])
    kept = upload(bystander.client, f"{marker}-other.txt", txt(f"The bystander keeps the {marker} sky chart."))
    wait_document(bystander.client, kept["id"])

    doc_ids = [item["id"] for item in items] + [generated["id"]]
    names = stored_names(doc_ids)
    files_before = stored_files()
    assert set(names.values()) <= files_before
    assert f"{names[generated['id']]}.source.md" in files_before
    assert all(count >= 1 for count in qdrant_points(models["embed_a"]["id"], doc_ids).values())
    before = _counts(user.id)
    assert before["documents"] == 3 and before["conversations"] == 1 and before["messages"] == 2
    assert before["usage_events"] >= 2 and before["login_events"] >= 1

    check(admin.delete(f"/api/admin/users/{user.id}"), 204)

    assert user.id not in {entry["id"] for entry in check(admin.get("/api/admin/users")).json()}
    remaining = {entry["id"] for entry in check(admin.get("/api/admin/documents")).json()}
    assert not set(doc_ids) & remaining and kept["id"] in remaining
    files_after = stored_files()
    assert not {*names.values(), f"{names[generated['id']]}.source.md"} & files_after
    assert stored_names([kept["id"]])[kept["id"]] in files_after
    assert set(qdrant_points(models["embed_a"]["id"], doc_ids).values()) == {0}
    assert qdrant_points(models["embed_a"]["id"], [kept["id"]])[kept["id"]] >= 1
    assert set(_counts(user.id).values()) == {0}
    assert int(sql_value(f"select count(*) from users where id = {user.id}")) == 0
    report = check(admin.get("/api/admin/usage")).json()
    assert user.id not in {row["user_id"] for row in report["by_user"]}

    assert user.client.get("/api/auth/me").status_code == 401
    relogin = client().post("/api/auth/login", json={"username": user.username, "password": user.password})
    assert relogin.status_code == 401
    assert bystander.client.get("/api/auth/me").status_code == 200
