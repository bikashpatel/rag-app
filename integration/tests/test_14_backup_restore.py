"""Flow 14: backups and a tested restore (scripts/backup.sh, scripts/restore.sh, the backup service; SPEC.md 10).

The drill: users with indexed documents, a conversation with a generated file, and a Gmail connection (an encrypted
token) are backed up; the whole stack is destroyed with its volumes (`down -v`) and started empty; the backup is
restored; and everything comes back: logins, the same file bytes, the same passages from search without re-embedding,
the conversation, the token decrypting with the same key. Also: the start-up backup (with no alert while it is due), a
backup's files and modes, the refusal without --yes, the refusal of a backup whose schema revision this release doesn't
know or would migrate (without --migrate), retention, and the scheduler catching up on a missed backup.

The hard cases: a restore over a stack that changed since the backup (a new and a deleted document, a re-indexed
collection, an extra one); a backup taken while documents were deleted and added, which the restore reconciles; a
restore with --migrate, reconciling only after the migration; snapshot uploads to a slow Qdrant. And failure injection,
where the stack must come out with exactly its data from before, running: damaged backups that verification refuses
before anything is stopped (a corrupt or missing snapshot, a corrupt archive, a checksum list that disagrees with the
archive); a dump that fails to restore and a snapshot Qdrant refuses half-way through, refused before anything is
replaced; and a swap failing at each step, undone."""

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
from harness import (
    ENV,
    User,
    alerts_received,
    chat,
    check,
    client,
    compose,
    compose_started,
    documents,
    exec_in,
    gmail_connect,
    leaked_secrets,
    limit_cpus,
    login,
    python_in,
    qdrant,
    qdrant_collections,
    qdrant_points,
    script,
    sql,
    stored_files,
    table_counts,
    unique,
    upload,
    wait_document,
    wait_queue_idle,
    wait_until,
)
from samples import md, txt

TOKEN_DIGEST = """
import asyncio, hashlib
from sqlalchemy import select
from app.db import SessionLocal
from app.models import PluginConnection
from app.plugins import crypto

async def main():
    async with SessionLocal() as db:
        row = (await db.execute(select(PluginConnection).where(PluginConnection.user_id == {user_id}))).scalar_one()
    token = crypto.token_cipher().decrypt(row.user_id, row.plugin_id, row.refresh_token_enc)
    print(hashlib.sha256(token.encode()).hexdigest())

asyncio.run(main())
"""


def backups() -> list[str]:
    """The complete backups, oldest first, as the restore service lists them."""
    output = compose("run", "--rm", "--no-deps", "-T", "restore", "list", timeout=120)
    return [line.split()[0] for line in output.splitlines() if line.strip()]


def in_backup_dir(command: str) -> str:
    """A shell command in a backup-service container, the backups mounted writable."""
    return compose("run", "--rm", "--no-deps", "-T", "--entrypoint", "/bin/sh", "backup", "-c", command, timeout=120)


def backup_now() -> str:
    result = script("backup.sh")
    assert result.returncode == 0, result.stdout + result.stderr
    lines = [line for line in result.stdout.splitlines() if line.startswith("BACKUP_NAME=")]
    assert lines, result.stdout
    return lines[-1].split("=", 1)[1]


def token_digest(user_id: int) -> str:
    return python_in("backend", TOKEN_DIGEST.format(user_id=int(user_id)))


APP_SERVICES = ("frontend", "monitor", "backup", "worker", "backend")  # what restore.sh stops
MISSING_FILE = "The file wasn't in the backup this document was restored from. Upload it again."

# The stack's files and points, read from a restore-service container: it works while the app is stopped.
FILES_AND_POINTS = r"""
. /opt/backup/lib.sh
(cd /data/uploads && find . -type f | sort | xargs -r sha256sum) | sed 's/^/file /'
for name in $(q_get /collections | grep -o '"name":"[^"]*"' | sed 's/^"name":"//; s/"$//' | sort); do
  count=$(qwget -T 60 -O - --header 'Content-Type: application/json' --post-data '{"exact": true}' \
    "${QDRANT_URL%/}/collections/$name/points/count" | sed -n 's/.*"count":\([0-9]*\).*/\1/p')
  echo "points $name $count"
done
"""

# Makes SHA256SUMS and manifest.json agree with the files of the backup folder $1 again, as if the backup had written
# them: a damage the checksums can't show.
RESEAL = r"""
reseal() {
  (cd "$1" && . /opt/backup/lib.sh && write_checksums . && while read -r sum file; do
    entry="\"$file\": {\"sha256\": \"$sum\", \"bytes\": $(stat -c %s "$file")}"
    sed -i "s|\"$file\": {\"sha256\": \"[0-9a-f]*\", \"bytes\": [0-9]*}|$entry|" manifest.json
  done < SHA256SUMS)
}
"""


def in_restore_shell(command: str, *args: str, timeout: float = 600) -> str:
    """A shell command in a restore-service container (the backups read-only, the uploads writable), its stdout."""
    return compose(
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "--entrypoint",
        "/bin/sh",
        "restore",
        "-c",
        command,
        "sh",
        *args,
        timeout=timeout,
    )


def table_digests() -> dict[str, str]:
    """Every table's rows and a checksum of their contents."""
    tables = [row[0] for row in sql("select tablename from pg_tables where schemaname = 'public' order by 1")]
    query = " union all ".join(
        f"select '{table}', count(*) || ' ' || coalesce(md5(string_agg(r::text, '|' order by r::text)), '') "
        f'from public."{table}" r'
        for table in tables
    )
    return dict(sql(query))


def stack_state() -> dict[str, Any]:
    """Everything a restore replaces: the database's tables, the uploaded files and Qdrant's collections."""
    files: dict[str, str] = {}
    points: dict[str, int] = {}
    for line in in_restore_shell(FILES_AND_POINTS).splitlines():
        kind, first, second = line.split(maxsplit=2)
        if kind == "file":
            files[second] = first
        elif kind == "points":
            points[first] = int(second)
    return {"tables": table_digests(), "files": files, "points": points}


def leftovers() -> list[str]:
    """What a restore prepares or keeps aside, still there."""
    state = stack_state()
    found = [row[0] for row in sql("select datname from pg_database where datname ~ '_(restoring|previous)$'")]
    found += [name for name in state["points"] if name.endswith("-restoring")]
    return found + [path for path in state["files"] if path.startswith("./.restore-")]


def inconsistencies() -> list[str]:
    """Documents that aren't ready to use as their row says (a missing file, vectors that don't number the chunks), and
    files no document has."""
    rows = sql("select id, status, stored_name, num_chunks, coalesce(embedding_model_id, 0) from documents")
    files = stored_files()
    problems = [f"{doc} has no file" for doc, status, name, _, _ in rows if status != "failed" and name not in files]
    stems = {name.split(".", 1)[0] for _, _, name, _, _ in rows}  # a file and its sidecars share the stem
    problems += [f"{file} has no document" for file in files if file.split(".", 1)[0] not in stems]
    ready: dict[int, dict[int, int]] = {}
    for doc, status, _, chunks, model in rows:
        if status == "ready" and model != "0":
            ready.setdefault(int(model), {})[int(doc)] = int(chunks)
    for model, chunks in ready.items():
        for doc, points in qdrant_points(model, list(chunks)).items():
            if points != chunks[doc]:
                problems.append(f"{doc} has {points} vectors for {chunks[doc]} chunks")
    return problems


def app_collections() -> list[str]:
    return [name for name in qdrant_collections() if name.startswith("rag_chunks_") and name[11:].isdigit()]


def recent_name(minutes_ago: int) -> str:
    """A backup name from `minutes_ago` minutes ago: younger than 48 h, so retention keeps it."""
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(time.time() - minutes_ago * 60)) + "-manual"


@contextmanager
def crafted(source: str, damage: str, minutes_ago: int = 60) -> Iterator[str]:
    """A copy of the backup `source`, changed by the shell command `damage` (the copy's folder is $d, and reseal is
    defined), removed afterwards."""
    name = recent_name(minutes_ago)
    in_backup_dir(f"{RESEAL}\nd=/backups/{name} && cp -a /backups/{source} $d && {damage}")
    try:
        yield name
    finally:
        in_backup_dir(f"rm -rf /backups/{name}")


def ready() -> bool:
    try:
        return client().get("/api/health/ready", timeout=10).status_code == 200
    except httpx.HTTPError:
        return False


def sources(result) -> list[tuple[int, int, str]]:
    return [(item["document_id"], item["chunk_index"], item["snippet"]) for item in result.sources]


def test_the_backup_service_backs_up_once_after_start(admin: httpx.Client) -> None:
    wait_until(lambda: any(name.endswith("-startup") for name in backups()), 180, "the start-up backup", interval=3)
    status = check(admin.get("/api/admin/system")).json()
    backup = status["backup"]
    assert backup["status_seen"] and backup["schedule"] == "02:30" and backup["next_run_at"]
    assert backup["last_success_name"] and backup["failing"] is False
    (check_result,) = [item for item in status["checks"] if item["id"] == "backup"]
    assert check_result["status"] == "ok", check_result
    # The monitor confirms a problem within seconds, the start-up backup came 20 s after the start: no backup yet was
    # no problem while it was due.
    assert not [alert for alert in alerts_received() if "Backups" in alert["text"]]


@pytest.mark.deadline(240)
def test_a_backup_is_complete_private_and_verified() -> None:
    name = backup_now()
    manifest = json.loads(compose("run", "--rm", "--no-deps", "-T", "restore", "show", name, timeout=120))
    assert manifest["name"] == name and manifest["trigger"] == "manual"
    assert manifest["app_version"] == ENV["APP_VERSION"]
    assert manifest["alembic_revision"] == sql_value_or_none("select version_num from alembic_version")
    assert set(manifest["postgres"]["tables"]) == set(table_counts())
    assert set(manifest["files"]) >= {"postgres.dump", "postgres.rows", "uploads.tar.gz", "uploads.sha256"}
    modes = in_backup_dir(f"cd /backups/{name} && find . -exec stat -c '%a %n' {{}} +").split("\n")
    assert {line.split()[0] for line in modes if line} <= {"700", "600"}, modes
    assert in_backup_dir("stat -c %a /backups").strip() == "700"
    assert not leaked_secrets(in_backup_dir(f"cat /backups/{name}/manifest.json /backups/{name}/postgres.rows"))


def sql_value_or_none(query: str) -> str | None:
    try:
        rows = sql(query)
    except RuntimeError:
        return None
    return rows[0][0] if rows else None


@pytest.mark.deadline(300)
@pytest.mark.parametrize(
    ("revision", "code", "refusal"),
    [
        ("0099", 1, "this release doesn't know the backup's schema revision 0099"),
        ("0001", 2, "Refusing without --migrate"),
        (None, 2, "Refusing without --migrate"),  # made before migrations
    ],
)
def test_restore_refuses_a_backup_of_another_schema_revision(revision: str | None, code: int, refusal: str) -> None:
    """The backend migrates at start-up: it wouldn't start on a revision it doesn't know, and it would migrate an
    older one (or one made before migrations) forward, so the release that made the backup couldn't use it again.
    Both are refused before anything is stopped."""
    name = backups()[-1]
    other = "20200102T000000Z-manual"
    value = f'"{revision}"' if revision else "null"
    in_backup_dir(
        f"cp -a /backups/{name} /backups/{other} && sed -i "
        f"""'s/^  "alembic_revision": .*,$/  "alembic_revision": {value},/' /backups/{other}/manifest.json"""
    )
    try:
        before = backups()
        refused = script("restore.sh", other, "--yes")
        assert refused.returncode == code, refused.stdout + refused.stderr
        assert refusal in refused.stderr
        assert "Stopping the app" not in refused.stdout
        assert backups() == before  # not even a safety backup
        assert compose("ps", "--status", "running", "--services").split().count("backend") == 1
        if code == 2:
            head = sql("select version_num from alembic_version")[0][0]
            dry = script("restore.sh", other)
            assert dry.returncode == 2, dry.stdout + dry.stderr
            assert f"starting this release {'migrates' if revision else 'adopts its schema and migrates'} it to" in (
                dry.stdout
            )
            assert head in dry.stdout and f"scripts/restore.sh {other} --yes --migrate\n" in dry.stdout
    finally:
        in_backup_dir(f"rm -rf /backups/{other}")


# A copy of the newest backup, damaged (in $d), and why verification refuses it.
DAMAGED = {
    "corrupt-snapshot": (
        "f=$(ls $d/qdrant/*.snapshot | tail -1) && printf X | dd of=$f bs=1 seek=1000 conv=notrunc 2> /dev/null",
        "verify: a checksum doesn't match",
    ),
    "missing-snapshot": ("rm $(ls $d/qdrant/*.snapshot | tail -1) && reseal $d", "is missing"),
    "corrupt-archive": ("printf X >> $d/uploads.tar.gz", "verify: a checksum doesn't match"),
    # uploads.sha256 disagrees with the archive, SHA256SUMS and manifest.json agree with both: only the archived files
    # themselves, checked one by one, show it.
    "checksum-list-disagrees": (
        "sed -i '1{s/^0/x/;s/^[^x]/0/;s/^x/1/}' $d/uploads.sha256 && reseal $d",
        "verify: an archived file doesn't match uploads.sha256",
    ),
}


@pytest.fixture(scope="module")
def backup_with_a_document(admin: httpx.Client, user_factory) -> str:
    """A backup holding at least one document: a file in the archive, a collection with its vectors."""
    owner: User = user_factory("damaged")
    item = upload(owner.client, f"{unique('tern')}.txt", txt("The tern colony returns in April."))
    wait_document(owner.client, item["id"])
    wait_queue_idle(admin)
    return backup_now()


@pytest.mark.deadline(240)
@pytest.mark.parametrize("damage", list(DAMAGED))
def test_verification_refuses_a_damaged_backup_before_stopping_anything(
    damage: str, backup_with_a_document: str
) -> None:
    script_text, reason = DAMAGED[damage]
    before = backups()
    with crafted(backup_with_a_document, script_text) as damaged:
        refused = script("restore.sh", damaged, "--yes")
        assert refused.returncode == 1, refused.stdout + refused.stderr
        assert "failed verification" in refused.stderr and reason in refused.stdout
        assert "Stopping the app" not in refused.stdout
    assert backups() == before  # not even a safety backup
    assert compose("ps", "--status", "running", "--services").split().count("backend") == 1


def test_restore_refuses_without_yes() -> None:
    name = backups()[-1]
    dry = script("restore.sh", name)
    assert dry.returncode == 2, dry.stdout + dry.stderr
    assert "Refusing without --yes" in dry.stdout and f"{name} verified" in dry.stdout
    assert compose("ps", "--status", "running", "--services").split().count("backend") == 1


def test_retention_keeps_daily_and_weekly_backups() -> None:
    names = [
        "20261025T023000Z-scheduled",
        "20261024T143000Z-manual",
        "20261024T023000Z-scheduled",
        "20261023T200000Z-startup",
        "20261023T023000Z-scheduled",
        "20261018T023000Z-scheduled",
        "20261017T023000Z-scheduled",
        "20261011T023000Z-scheduled",
        "20261004T023000Z-scheduled",
        "20260927T023000Z-scheduled",
        "20261001T023000Z-pre-restore",
    ]
    setup = " && ".join(f"mkdir -p /tmp/r/{name} && echo '{{}}' > /tmp/r/{name}/manifest.json" for name in names)
    # lib.sh is sourced with its clock fixed at Sunday 2026-10-25 15:00 UTC, so the ages don't depend on today.
    output = in_backup_dir(
        f"{setup} && mkdir -p /tmp/s && BACKUP_ROOT=/tmp/r STATUS_DIR=/tmp/s BACKUP_KEEP_DAILY=2 BACKUP_KEEP_WEEKLY=3 "
        "sh -c '. /opt/backup/lib.sh && now() { echo 1792940400; } && prune' > /dev/null && ls /tmp/r"
    )
    # Days 25 and 24 (their newest); everything under 48 h old (the 23rd's start-up backup, not its scheduled one); the
    # weeks of the 25th (already kept), the 18th and the 11th; the pre-restore backup is past its 2 days.
    assert output.split() == [
        "20261011T023000Z-scheduled",
        "20261018T023000Z-scheduled",
        "20261023T200000Z-startup",
        "20261024T023000Z-scheduled",
        "20261024T143000Z-manual",
        "20261025T023000Z-scheduled",
    ]


def prune(names: list[str], keep_daily: int, keep_weekly: int) -> list[str]:
    """The backups left after retention, among empty ones with these names, the clock at Sunday 2026-10-25 15:00 UTC."""
    setup = " && ".join(f"mkdir -p /tmp/r/{name} && echo '{{}}' > /tmp/r/{name}/manifest.json" for name in names)
    output = in_backup_dir(
        f"{setup} && mkdir -p /tmp/s && BACKUP_ROOT=/tmp/r STATUS_DIR=/tmp/s BACKUP_KEEP_DAILY={keep_daily} "
        f"BACKUP_KEEP_WEEKLY={keep_weekly} sh -c '. /opt/backup/lib.sh && now() {{ echo 1792940400; }} && prune' "
        "> /dev/null && ls /tmp/r"
    )
    return output.split()


def test_retention_never_lets_a_manual_backup_push_out_another() -> None:
    """A backup before a deploy and one after it, the same day: the first is the way back. Manual backups don't take
    a day's place, and stay BACKUP_KEEP_DAILY days."""
    names = [
        "20261025T093000Z-manual",
        "20261025T090000Z-manual",
        "20261025T023000Z-scheduled",
        "20261021T180000Z-manual",  # 4 days old, past the 48 hours
        "20261021T120000Z-manual",
        "20261021T023000Z-scheduled",
        "20261017T120000Z-manual",  # 8 days old: past BACKUP_KEEP_DAILY
        "20261017T023000Z-scheduled",
    ]
    assert prune(names, 7, 4) == sorted(set(names) - {"20261017T120000Z-manual"})


@pytest.mark.parametrize(
    ("existing", "catch_up"),
    [
        ([], True),  # a new install
        (["20261022T023000Z-scheduled"], True),  # the stack was down at 02:30 for three days
        (["20261024T023000Z-scheduled", "20261025T100000Z-pre-restore"], True),  # a safety backup doesn't count
        (["20261025T023000Z-scheduled"], False),
        (["20261024T023000Z-scheduled", "20261025T090000Z-manual"], False),
    ],
    ids=["none", "missed", "only-a-safety-backup", "today", "manual-today"],
)
def test_the_scheduler_catches_up_on_a_missed_backup(existing: list[str], catch_up: bool) -> None:
    """At start, a backup follows BACKUP_START_DELAY_SECONDS later when none began since the latest scheduled time."""
    setup = "".join(f"mkdir -p /tmp/r/{name} && echo '{{}}' > /tmp/r/{name}/manifest.json && " for name in existing)
    # lib.sh's clock is fixed at 2026-10-25 15:00 UTC; the scheduler stops at its first wait.
    output = in_backup_dir(
        f"{setup}mkdir -p /tmp/r /tmp/s && BACKUP_ROOT=/tmp/r STATUS_DIR=/tmp/s BACKUP_SCHEDULE=02:30 "
        "BACKUP_START_DELAY_SECONDS=120 sh -c '. /opt/backup/lib.sh && now() { echo 1792940400; } && "
        "pause() { exit 0; } && schedule' && cat /tmp/s/status.json"
    )
    scheduler = json.loads(output.splitlines()[-1])["scheduler"]
    assert scheduler["next_run_at"] == "2026-10-26T02:30:00Z"
    assert scheduler["catch_up_at"] == ("2026-10-25T15:02:00Z" if catch_up else None)
    assert ("one starts in 120 s" in output) is catch_up


@pytest.mark.deadline(900)
def test_the_restore_drill(admin: httpx.Client, user_factory, fake) -> None:
    # ---- Data worth keeping ----
    alice: User = user_factory("restore-a")
    bob: User = user_factory("restore-b")
    marker = unique("quokka")
    uploads = [
        upload(alice.client, f"{marker}-notes.txt", txt(f"The {marker} notes say the lighthouse opens at dawn.")),
        upload(alice.client, f"{marker}-plan.md", md(f"# Plan\n\nThe {marker} plan needs nine lanterns.")),
        upload(bob.client, f"{marker}-bob.txt", txt(f"Bob's {marker} diary mentions a blue bicycle.")),
        upload(admin, f"{marker}-rules.txt", txt(f"The shared {marker} handbook lists harbour rules."), scope="shared"),
    ]
    owners = [alice.client, alice.client, bob.client, admin]
    for session, item in zip(owners, uploads, strict=True):
        wait_document(session, item["id"])

    check(admin.patch("/api/admin/plugins/gmail", json={"enabled": True}))
    assert gmail_connect(alice) == "connected"
    digest = token_digest(alice.id)
    # Billing rows: a bank transfer the admin records (an order, a payment, a receipt, a period and their events)
    starter = next(plan["id"] for plan in check(admin.get("/api/admin/plans")).json() if plan["code"] == "starter")
    transfer = {"user_id": bob.id, "plan_id": starter, "amount_paise": 50_000, "received_on": "2026-10-24"}
    transfer |= {"reference": f"UTR-{marker}", "note": None, "apply": True}
    check(admin.post("/api/admin/billing/offline-payments", json=transfer), 201)

    made = chat(alice.client, f'Please create a Markdown file titled "Notes {marker}" summarizing the {marker} notes.')
    created = [event for event in made.all("file") if event["status"] == "created"]
    assert created, made.names
    generated = wait_document(alice.client, created[0]["document"]["id"])
    conversation_id = made.meta["conversation_id"]
    question = f"What do the {marker} notes say about the lighthouse?"
    asked = chat(alice.client, question)  # a new conversation: the retrieval query is the question alone
    assert asked.sources, asked.names
    wait_queue_idle(admin)

    alice_documents = {item["id"]: item for item in documents(alice.client)}
    assert generated["id"] in alice_documents
    downloads = {
        doc_id: check(alice.client.get(f"/api/documents/{doc_id}/download")).content for doc_id in alice_documents
    }
    conversation = check(alice.client.get(f"/api/conversations/{conversation_id}")).json()
    files = stored_files()
    points = qdrant_collections()
    counts = table_counts()
    assert counts["plugin_connections"] >= 1 and counts["message_files"] >= 1 and points
    assert counts["billing_receipts"] >= 1 and counts["plan_periods"] >= 1

    # ---- Back up, then lose everything ----
    name = backup_now()
    manifest = json.loads(compose("run", "--rm", "--no-deps", "-T", "restore", "show", name, timeout=120))
    assert manifest["postgres"]["tables"] == counts
    assert manifest["uploads"]["files"] == len(files)
    expected = {collection: {"points": count, "exact": True} for collection, count in points.items()}
    assert manifest["qdrant"]["collections"] == expected

    logs = compose("logs", "--no-color", timeout=120)  # these containers are about to go
    assert not leaked_secrets(logs), "secrets found in the stack logs"
    compose("down", "-v", "--remove-orphans", timeout=300)
    compose("up", "-d", "--wait", "--wait-timeout", "300", timeout=420)
    gone = client().post("/api/auth/login", json={"username": alice.username, "password": alice.password})
    assert gone.status_code == 401
    assert stored_files() == set() and qdrant_collections() == {}
    assert sql("select count(*) from users") == [["1"]]

    # ---- Restore ----
    mark = fake.mark()
    restored = script("restore.sh", name, "--yes")
    if restored.returncode != 0:  # leave a running stack to the tests after this one
        compose("up", "-d", "--wait", "--wait-timeout", "300", timeout=420)
    assert restored.returncode == 0, restored.stdout + restored.stderr
    assert "Safety backup:" in restored.stdout and f"Restored {name}" in restored.stdout
    assert "this release's newest: nothing to migrate" in restored.stdout  # the empty stack was migrated at start
    assert "row counts equal the backup's" in restored.stdout
    assert "every checksum equals the backup's" in restored.stdout

    # ---- Everything is back ----
    assert table_counts() == counts  # before anyone signs in again (sign-ins add rows)
    assert stored_files() == files
    assert qdrant_collections() == points
    assert token_digest(alice.id) == digest

    again = login(alice.username, alice.password)
    login(bob.username, bob.password)
    assert {item["id"]: item for item in documents(again)} == alice_documents
    for doc_id, data in downloads.items():
        assert check(again.get(f"/api/documents/{doc_id}/download")).content == data, doc_id
    assert check(again.get(f"/api/conversations/{conversation_id}")).json() == conversation
    (gmail,) = [plugin for plugin in check(again.get("/api/plugins")).json() if plugin["id"] == "gmail"]
    assert gmail["connection"]["status"] == "connected"

    searched = chat(again, question)
    assert sources(searched) == sources(asked)
    embedded = [text for entry in fake.log("embedding", after=mark) for text in entry["input"]]
    assert len(embedded) == 1 and question in embedded[0], "only the question was embedded: nothing was re-indexed"
    assert check(client().get("/api/health/ready")).json() == {"status": "ready"}
    assert any(item.endswith("-pre-restore") for item in backups())
    check(admin.patch("/api/admin/plugins/gmail", json={"enabled": False}))


def test_the_restored_stack_reports_no_restore_leftovers() -> None:
    databases = sql("select datname from pg_database where datname like '%restoring%'")
    assert databases == []
    assert ".restore-staging" not in exec_in("backend", "ls", "-a", "/data/uploads")
    assert exec_in("redis", "redis-cli", "LLEN", "rag:ingest_queue").strip() == "0"


# ---------------- The hard cases ----------------

# The newest backup's dump, with a document pointing at a user that doesn't exist (foreign keys off while it is
# changed): pg_dump and pg_restore --list don't mind, and the rows are the same, but restoring it fails when the
# foreign key is put back.
DUMP_THAT_FAILS = r"""
psql -X -q -v ON_ERROR_STOP=1 -d postgres -c 'DROP DATABASE IF EXISTS scratch_broken' \
  -c 'CREATE DATABASE scratch_broken' &&
pg_restore --no-owner --no-privileges --exit-on-error -d scratch_broken $d/postgres.dump &&
psql -X -q -v ON_ERROR_STOP=1 -d scratch_broken -c 'SET session_replication_role = replica' \
  -c 'UPDATE documents SET owner_id = 2147483647 WHERE id = (SELECT min(id) FROM documents)' &&
pg_dump --format=custom --file=$d/postgres.dump scratch_broken &&
psql -X -q -d postgres -c 'DROP DATABASE scratch_broken' && reseal $d
"""
# The last collection's snapshot cut in half, its checksums made to agree: Qdrant takes the upload, then can't read it,
# after the collections before it were prepared.
SNAPSHOT_THAT_FAILS = r"""
f=$d/qdrant/$(tail -1 $d/qdrant.collections | cut -d' ' -f1).snapshot &&
head -c $(($(stat -c %s $f) / 2)) $f > $f.half && mv $f.half $f && reseal $d
"""


def ensure_two_collections() -> None:
    """At least two collections, so a failure can come half-way through Qdrant: an empty one more if needed (test_09
    leaves the second embedding model's)."""
    if len(app_collections()) < 2:
        body = {"vectors": {"size": 4, "distance": "Cosine"}}
        (created,) = qdrant([{"method": "PUT", "path": "/collections/rag_chunks_987650", "body": body}])
        assert created["status"] == 200


@pytest.mark.deadline(600)
@pytest.mark.parametrize(
    ("damage", "reason", "options"),
    [
        (DUMP_THAT_FAILS, "pg_restore failed", ()),  # with the safety backup, as by default
        (SNAPSHOT_THAT_FAILS, "Qdrant refused the snapshot for", ("--no-safety-backup",)),
    ],
    ids=["dump-fails-to-restore", "snapshot-refused-half-way"],
)
def test_a_restore_that_fails_while_preparing_changes_nothing(damage: str, reason: str, options: tuple) -> None:
    ensure_two_collections()
    source = backup_now()
    before = stack_state()
    with crafted(source, damage) as damaged:
        failed = script("restore.sh", damaged, "--yes", *options)
        collections = in_backup_dir(f"cut -d' ' -f1 /backups/{damaged}/qdrant.collections").split()
    assert failed.returncode == 1, failed.stdout + failed.stderr
    assert "Stopping the app" in failed.stdout and reason in failed.stdout
    assert "The backup couldn't be prepared; nothing was changed" in failed.stdout
    assert "Nothing was changed; starting the app again." in failed.stderr
    assert "Database restored" not in failed.stdout and "Uploads restored" not in failed.stdout
    if damage == SNAPSHOT_THAT_FAILS:  # half-way: the collections before it were prepared
        assert len(collections) >= 2 and f"Preparing the collection {collections[0]} " in failed.stdout
        assert f"{reason} {collections[-1]}-restoring" in failed.stdout
    assert stack_state() == before
    assert leftovers() == []
    assert ready() and compose("ps", "--status", "running", "--services").split().count("worker") == 1


# Failures injected into a restore's swap, run by hand in a restore-service container (scripts/backup/lib.sh sourced,
# one of its steps replaced). $1: the backup.
SWAP_FAILURES = {
    # After Qdrant and the uploads were swapped
    "database": r"""swap_db() { log "Injected: the database swap fails"; return 1; }""",
    # After the current files were moved aside and the backup's moved in
    "uploads": r"""
eval "$(sed -n '/^swap_uploads() {/,/^}/p' /opt/backup/lib.sh |
  sed 's/rmdir "\$staging" || return 1/{ log "Injected: the uploads swap fails"; return 1; }/')"
""",
    # After the collections before it were swapped: its prepared copy and its upload both fail
    "last-collection": r"""
last=$(tail -1 "/backups/$1/qdrant.collections" | cut -d' ' -f1)
eval "$(sed -n '/^recover_from() {/,/^}/p; /^upload_snapshot() {/,/^}/p' /opt/backup/lib.sh |
  sed 's/^\([a-z_]*\)() {/real_\1() {/')"
recover_from() {
  case $1:$2 in "$last":*-restoring/*) log "Injected: Qdrant can't recover $1"; return 1 ;; esac
  real_recover_from "$@"
}
upload_snapshot() { if [ "$1" = "$last" ]; then echo 500; else real_upload_snapshot "$@"; fi; }
""",
}


@pytest.mark.deadline(600)
def test_a_swap_that_fails_is_undone() -> None:
    """The database is swapped last, in one transaction; a failure before it puts the files and collections back."""
    ensure_two_collections()
    name = backup_now()
    before = stack_state()
    assert len(before["points"]) >= 2
    compose("stop", *APP_SERVICES, timeout=300)
    try:
        for step, inject in SWAP_FAILURES.items():
            output = in_restore_shell(f'. /opt/backup/lib.sh\n{inject}\nrestore_backup "$1"; echo "exit $?"', name)
            assert "Injected: " in output, output
            assert output.rstrip().endswith("exit 1"), (step, output)
            assert "Everything is as it was before the restore; nothing was changed" in output, (step, output)
            assert stack_state() == before, step
            assert leftovers() == [], step
    finally:
        compose("up", "-d", "--wait", "--wait-timeout", "300", *APP_SERVICES, timeout=420)
    assert ready()


@pytest.mark.deadline(900)
def test_a_restore_over_a_changed_stack_brings_back_exactly_the_backup(
    admin: httpx.Client, user_factory, models: dict
) -> None:
    owner: User = user_factory("over")
    marker = unique("puffin")
    kept = upload(owner.client, f"{marker}-kept.txt", txt(f"The {marker} log notes a calm harbour."))
    deleted = upload(owner.client, f"{marker}-deleted.txt", txt(f"The {marker} chart shows two lighthouses."))
    for item in (kept, deleted):
        wait_document(owner.client, item["id"])
    wait_queue_idle(admin)
    downloads = {
        item["id"]: check(owner.client.get(f"/api/documents/{item['id']}/download")).content for item in (kept, deleted)
    }
    name = backup_now()
    backed_up = stack_state()
    stale = check(admin.get("/api/admin/stats")).json()["stale_documents"]

    # ---- The stack moves on: a deleted and a new document, re-indexing into another collection, an extra one ----
    check(owner.client.delete(f"/api/documents/{deleted['id']}"), 204)
    added = upload(owner.client, f"{marker}-added.txt", txt(f"The {marker} diary mentions a red kite."))
    wait_document(owner.client, added["id"])
    first, second = models["embed_a"]["id"], models["embed_b"]["id"]
    check(admin.patch(f"/api/admin/models/{second}", json={"is_default": True}))
    check(admin.post("/api/admin/reindex", json={"only_stale": True}))
    wait_queue_idle(admin, timeout=400)
    assert qdrant_points(second, [kept["id"], added["id"]]) == {kept["id"]: 1, added["id"]: 1}
    extra = "rag_chunks_987654"
    created = qdrant(
        [{"method": "PUT", "path": f"/collections/{extra}", "body": {"vectors": {"size": 4, "distance": "Cosine"}}}]
    )
    assert created[0]["status"] == 200
    changed = stack_state()
    assert changed["files"] != backed_up["files"] and changed["points"] != backed_up["points"]

    # ---- Restore over it ----
    restored = script("restore.sh", name, "--yes")
    if restored.returncode != 0:  # leave a running stack to the tests after this one
        compose("up", "-d", "--wait", "--wait-timeout", "300", timeout=420)
    assert restored.returncode == 0, restored.stdout + restored.stderr
    assert f"Removing the collection {extra}, which the backup doesn't have" in restored.stdout
    assert "Database schema up to date" in restored.stdout
    assert "each has its file and its vectors" in restored.stdout, restored.stdout

    assert stack_state() == backed_up
    assert extra not in qdrant_collections() and leftovers() == [] and inconsistencies() == []
    stats = check(admin.get("/api/admin/stats")).json()
    assert stats["active_embedding_model"]["id"] == first and stats["stale_documents"] == stale
    again = owner.login_again()
    assert {item["id"] for item in documents(again) if item["owner_id"] == owner.id} == {kept["id"], deleted["id"]}
    for doc_id, data in downloads.items():
        assert check(again.get(f"/api/documents/{doc_id}/download")).content == data
    assert again.get(f"/api/documents/{added['id']}/download").status_code == 404


# A backup that waits, after archiving the uploads and before Qdrant's snapshot and the dump, until the test has
# changed the stack: what a slow backup of a busy stack sees.
PAUSED_BACKUP = r"""
. /opt/backup/lib.sh
collections() {
  touch /backups/.paused
  while [ ! -e /backups/.resume ]; do sleep 0.2; done
  q_get /collections | grep -o '"name":"[^"]*"' | sed 's/^"name":"//; s/"$//' | grep -E "^${PREFIX}_[0-9]+\$"
}
run_backup manual
"""


@pytest.mark.deadline(600)
def test_a_backup_taken_while_documents_change_is_reconciled_on_restore(admin: httpx.Client, user_factory) -> None:
    owner: User = user_factory("busy")
    marker = unique("gannet")
    kept = upload(owner.client, f"{marker}-kept.txt", txt(f"The {marker} survey counts nesting birds."))
    gone = upload(owner.client, f"{marker}-gone.txt", txt(f"The {marker} draft is deleted during the backup."))
    for item in (kept, gone):
        wait_document(owner.client, item["id"])
    wait_queue_idle(admin)
    gone_file = stored_names_of([gone["id"]])[gone["id"]]

    exec_in("backup", "rm", "-f", "/backups/.paused", "/backups/.resume")
    running = compose_started(
        "run", "--rm", "--no-deps", "-T", "--entrypoint", "/bin/sh", "backup", "-c", PAUSED_BACKUP
    )
    try:
        wait_until(
            lambda: "paused" in exec_in("backup", "sh", "-c", "[ -e /backups/.paused ] && echo paused || true"),
            120,
            "the backup to archive the uploads",
            interval=0.5,
        )
        # The uploads are archived: one document goes, another comes (file, vectors and row) before the rest is taken.
        check(owner.client.delete(f"/api/documents/{gone['id']}"), 204)
        added = upload(owner.client, f"{marker}-added.txt", txt(f"The {marker} note arrived during the backup."))
        added_chunks = wait_document(owner.client, added["id"])["num_chunks"]
        exec_in("backup", "touch", "/backups/.resume")
        output, _ = running.communicate(timeout=300)
    finally:
        if running.poll() is None:
            running.kill()
        exec_in("backup", "rm", "-f", "/backups/.paused", "/backups/.resume")
    assert running.returncode == 0, output
    name = output.split("BACKUP_NAME=")[-1].strip()
    model = int(sql_value_or_none(f"select embedding_model_id from documents where id = {int(added['id'])}") or 0)

    restored = script("restore.sh", name, "--yes", "--no-safety-backup")
    if restored.returncode != 0:
        compose("up", "-d", "--wait", "--wait-timeout", "300", timeout=420)
    assert restored.returncode == 0, restored.stdout + restored.stderr
    # The added document's row and vectors are in the backup, its file isn't; the deleted one's file is, alone.
    assert f"- marked failed, their file wasn't in the backup: {added['id']}" in restored.stdout
    assert (
        f"- removed {added_chunks} vector" in restored.stdout and f"deleted or failed: {added['id']}" in restored.stdout
    )
    assert "- removed 1 file that no document has" in restored.stdout

    listed = {item["id"]: item for item in documents(owner.login_again()) if item["owner_id"] == owner.id}
    assert set(listed) == {kept["id"], added["id"]}
    assert listed[kept["id"]]["status"] == "ready"
    assert (listed[added["id"]]["status"], listed[added["id"]]["error"]) == ("failed", MISSING_FILE)
    assert gone_file not in stored_files()
    assert qdrant_points(model, [added["id"]]) == {added["id"]: 0}
    assert inconsistencies() == [] and leftovers() == []


def stored_names_of(doc_ids: list[int]) -> dict[int, str]:
    ids = ",".join(str(int(doc_id)) for doc_id in doc_ids)
    return {int(doc_id): name for doc_id, name in sql(f"select id, stored_name from documents where id in ({ids})")}


# Migration 0003 (plans and billing) undone, as the schema was at 0001 and 0002 (0002 only aligns databases made before
# migrations): its tables and columns dropped, the limits back to NOT NULL and user_image_limits back.
UNDO_0003 = (
    "ALTER TABLE users DROP CONSTRAINT users_comp_plan_id_fkey; "
    "DROP TABLE plan_chat_models, plan_periods, billing_receipts, billing_refunds, billing_disputes, billing_events, "
    "user_billing_profiles, billing_payments, billing_orders, plans CASCADE; "
    "ALTER TABLE users DROP COLUMN monthly_image_limit, DROP COLUMN vision_monthly_token_limit, "
    "DROP COLUMN billing_tester, DROP COLUMN comp_plan_id, DROP COLUMN comp_reason, DROP COLUMN comp_granted_by, "
    "DROP COLUMN comp_granted_at; "
    "UPDATE users SET monthly_token_limit = coalesce(monthly_token_limit, 0), "
    "rate_limit_per_min = coalesce(rate_limit_per_min, 0), max_upload_mb = coalesce(max_upload_mb, 25), "
    "max_documents = coalesce(max_documents, 0), max_storage_mb = coalesce(max_storage_mb, 0); "
    "ALTER TABLE users ALTER COLUMN monthly_token_limit SET NOT NULL, ALTER COLUMN rate_limit_per_min SET NOT NULL, "
    "ALTER COLUMN max_upload_mb SET NOT NULL, ALTER COLUMN max_documents SET NOT NULL, "
    "ALTER COLUMN max_storage_mb SET NOT NULL; "
    "ALTER TABLE usage_events DROP COLUMN allowance_tokens; "
    "CREATE TABLE user_image_limits (user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
    "monthly_image_limit integer NOT NULL, updated_at timestamp with time zone NOT NULL, PRIMARY KEY (user_id)); "
    "DELETE FROM app_state WHERE key IN ('plans_seeded', 'plans_rollout', 'billing_settings') "
    "OR key LIKE 'receipt_seq:%'; "
    "UPDATE alembic_version SET version_num = '0001'"
)
# The newest backup's dump at revision 0001 (the baseline), as an older release would have made it.
AT_REVISION_0001 = r"""
psql -X -q -v ON_ERROR_STOP=1 -d postgres -c 'DROP DATABASE IF EXISTS scratch_old' -c 'CREATE DATABASE scratch_old' &&
pg_restore --no-owner --no-privileges --exit-on-error -d scratch_old $d/postgres.dump &&
psql -X -q -v ON_ERROR_STOP=1 -d scratch_old -c "{undo}" &&
pg_dump --format=custom --file=$d/postgres.dump scratch_old && psql -X -q -d postgres -c 'DROP DATABASE scratch_old' &&
(. /opt/backup/lib.sh && count_rows $d/postgres.dump $d/postgres.rows) &&
sed -i 's/^  "alembic_revision": .*,$/  "alembic_revision": "0001",/' $d/manifest.json && reseal $d
""".replace("{undo}", UNDO_0003)


@pytest.mark.deadline(600)
def test_a_restore_with_migrate_reconciles_only_after_migrating() -> None:
    head = sql("select version_num from alembic_version")[0][0]
    with crafted(backups()[-1], AT_REVISION_0001) as older:
        refused = script("restore.sh", older, "--yes", "--no-safety-backup")
        assert refused.returncode == 2 and "Refusing without --migrate" in refused.stderr
        restored = script("restore.sh", older, "--yes", "--no-safety-backup", "--migrate")
    if restored.returncode != 0:
        compose("up", "-d", "--wait", "--wait-timeout", "300", timeout=420)
    assert restored.returncode == 0, restored.stdout + restored.stderr
    out = restored.stdout
    steps = [
        f"starting this release migrates it to revision {head}",
        "Database restored",
        "==> Migrating the database to this release's schema, then reconciling",
        f"Database schema migrated from 0001 to revision {head}",
        "Checked ",
        "==> Starting the app",
    ]
    positions = [out.find(step) for step in steps]
    assert -1 not in positions and positions == sorted(positions), list(zip(steps, positions, strict=True))
    assert sql("select version_num from alembic_version") == [[head]]
    assert ready() and inconsistencies() == []


# Uploads one snapshot again and again, as a restore does, and counts the answers. $1: the snapshot's name in Qdrant,
# $2: how many times, $3: its points.
UPLOADS = r"""
. /opt/backup/lib.sh
qdrant_address
qwget -T 600 -O /tmp/drill.snapshot "${QDRANT_URL%/}/collections/upload-drill-source/snapshots/$1" || exit 1
sum=$(sha256sum /tmp/drill.snapshot | cut -d' ' -f1)
ok=0 failed=0
for attempt in $(seq 1 "$2"); do
  status=$(upload_snapshot upload-drill /tmp/drill.snapshot "$sum")
  if [ "$status" = 200 ] && [ "$(points upload-drill)" = "$3" ]; then
    ok=$((ok + 1))
  else
    failed=$((failed + 1))
    echo "upload $attempt: HTTP $status $(tail -c 300 "$RESPONSE")"
  fi
  drop_collection upload-drill
done
echo "uploads: $ok ok, $failed failed"
"""
# A collection of 2000 points (about 4 MB as a snapshot), and its snapshot's name.
SOURCE_COLLECTION = """
import json, os, random, urllib.request
base = os.environ["QDRANT_URL"] + "/collections/upload-drill-source"
def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(base + path, data, {"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(request, timeout=300) as reply:
        return json.load(reply)
call("PUT", "", {"vectors": {"size": 256, "distance": "Cosine"}})
random.seed(14)
for start in range(0, 2000, 500):
    vectors = [[random.random() for _ in range(256)] for _ in range(500)]
    points = [{"id": start + i, "vector": vector, "payload": {"doc_id": i % 40, "text": "x" * 800}}
              for i, vector in enumerate(vectors)]
    call("PUT", "/points?wait=true", {"points": points})
print(call("POST", "/snapshots?wait=true")["result"]["name"])
"""


@pytest.mark.deadline(900)
def test_snapshot_uploads_succeed_against_a_slow_qdrant() -> None:
    """A restore uploads every snapshot. busybox nc used to half-close the connection when its input ended, and a busy
    Qdrant then refused, now and then, a body it hadn't read yet ("Payload error"): a quarter of the uploads failed
    on half a CPU. Each upload must now succeed the first time."""
    snapshot = python_in("backend", SOURCE_COLLECTION)
    limit_cpus("qdrant", 0.5)
    try:
        output = in_restore_shell(UPLOADS, snapshot, "24", "2000", timeout=800)
    finally:
        limit_cpus("qdrant", None)
        qdrant(
            [
                {"method": "DELETE", "path": f"/collections/upload-drill-source/snapshots/{snapshot}"},
                {"method": "DELETE", "path": "/collections/upload-drill-source"},
                {"method": "DELETE", "path": "/collections/upload-drill"},
            ]
        )
    assert output.strip().endswith("uploads: 24 ok, 0 failed"), output
