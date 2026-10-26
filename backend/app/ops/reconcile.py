"""After a restore: every document consistent with its file and its vectors again (SPEC.md section 10).

A backup takes the uploads, Qdrant and Postgres one after the other while the app runs (scripts/backup/lib.sh), so a
document added, indexed or deleted meanwhile can come back with only some of its parts. `python -m app.ops.reconcile`
runs while the app is stopped (scripts/restore.sh, after the swap). It first migrates the database to this release's
schema, as the backend does when it starts (a restore with --migrate brings an older one), then fixes each case:

- a document whose file is missing, or has another size than its row says: failed, asking for the upload again, and
  its vectors deleted;
- a ready document whose vectors in its model's collection don't number its chunks: pending again, so the worker
  re-indexes it (a ready document without a model is stale on purpose: the admin dropped that model's vectors);
- the vectors of a failed document, or of one the database doesn't have: deleted;
- a file no document has (its document was deleted during the backup): deleted.

It prints what it changed, naming documents by id only. Never run it while the app runs: an upload writes its file
before its row.
"""

import asyncio
import logging
import re
import sys
from collections import Counter
from dataclasses import dataclass, field

from qdrant_client import models as qm
from sqlalchemy import select, update

from app.config import settings
from app.db import SchemaMismatchError, SessionLocal, close_db, init_db
from app.models import Document
from app.ops.checks import describe_error
from app.services import storage, vectorstore
from app.services.vectorstore import close_qdrant, get_qdrant

MISSING_FILE = "The file wasn't in the backup this document was restored from. Upload it again."
SCROLL_BATCH = 1000
DELETE_BATCH = 500


@dataclass
class Report:
    documents: int = 0
    reindexed: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)
    removed_points: int = 0
    removed_from: list[int] = field(default_factory=list)  # documents whose leftover vectors were deleted
    removed_files: int = 0

    def lines(self) -> list[str]:
        if not (self.reindexed or self.failed or self.removed_points or self.removed_files):
            return [f"Checked {_count(self.documents, 'document')}: each has its file and its vectors."]
        lines = [f"Checked {_count(self.documents, 'document')}:"]
        if self.reindexed:
            lines.append(f"- set to re-index, their vectors didn't match: {_ids(self.reindexed)}")
        if self.failed:
            lines.append(f"- marked failed, their file wasn't in the backup: {_ids(self.failed)}")
        if self.removed_points:
            lines.append(
                f"- removed {_count(self.removed_points, 'vector')} of documents that are deleted or failed: "
                f"{_ids(self.removed_from)}"
            )
        if self.removed_files:
            lines.append(f"- removed {_count(self.removed_files, 'file')} that no document has")
        return lines


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"


def _ids(doc_ids: list[int]) -> str:
    return ", ".join(str(doc_id) for doc_id in sorted(doc_ids))


def _file_matches(stored_name: str, size: int) -> bool:
    try:
        return storage.stored_path(stored_name).stat().st_size == size
    except (OSError, ValueError):
        return False


def _remove_orphan_files(known: set[str]) -> int:
    """Deletes the files on the uploads volume that no document has (dot-folders are a restore's, kept)."""
    orphans = [
        path
        for path in storage.upload_dir().iterdir()
        if path.is_file() and not path.name.startswith(".") and path.name not in known
    ]
    for path in orphans:
        path.unlink(missing_ok=True)
    return len(orphans)


async def _collections() -> list[str]:
    """The app's collections: <prefix>_<embedding model id>."""
    pattern = re.compile(rf"{re.escape(settings.qdrant_collection_prefix)}_\d+")
    listed = await get_qdrant().get_collections()
    return sorted(item.name for item in listed.collections if pattern.fullmatch(item.name))


async def _points_per_document(name: str) -> Counter[int]:
    counts: Counter[int] = Counter()
    offset: qm.ExtendedPointId | None = None
    while True:
        points, offset = await get_qdrant().scroll(
            name, limit=SCROLL_BATCH, offset=offset, with_payload=["doc_id"], with_vectors=False
        )
        for point in points:
            doc_id = (point.payload or {}).get("doc_id")
            if isinstance(doc_id, int):
                counts[doc_id] += 1
        if offset is None:
            return counts


async def _delete_points(name: str, doc_ids: list[int]) -> None:
    for start in range(0, len(doc_ids), DELETE_BATCH):
        selector = qm.FilterSelector(
            filter=qm.Filter(
                must=[qm.FieldCondition(key="doc_id", match=qm.MatchAny(any=doc_ids[start : start + DELETE_BATCH]))]
            )
        )
        await get_qdrant().delete(name, points_selector=selector, wait=True)


async def reconcile() -> Report:
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(
                    Document.id,
                    Document.status,
                    Document.stored_name,
                    Document.size_bytes,
                    Document.num_chunks,
                    Document.embedding_model_id,
                )
            )
        ).all()
    points = {name: await _points_per_document(name) for name in await _collections()}

    report = Report(documents=len(rows))
    keep: set[int] = set()  # documents whose vectors stay
    known = {name for row in rows for name in storage.file_names(row.stored_name)}
    for row in rows:
        if not _file_matches(row.stored_name, row.size_bytes):
            if row.status != "failed":
                report.failed.append(row.id)
            continue
        if row.status == "failed":
            continue
        keep.add(row.id)
        if row.status == "ready" and row.embedding_model_id is not None:
            name = vectorstore.collection_name(row.embedding_model_id)
            if points.get(name, Counter())[row.id] != row.num_chunks:
                report.reindexed.append(row.id)

    async with SessionLocal() as db:
        if report.failed:
            await db.execute(
                update(Document)
                .where(Document.id.in_(report.failed))
                .values(status="failed", error=MISSING_FILE, num_chunks=0)
            )
        if report.reindexed:
            await db.execute(
                update(Document).where(Document.id.in_(report.reindexed)).values(status="pending", error=None)
            )
        await db.commit()

    removed: set[int] = set()
    for name, counts in points.items():
        leftovers = sorted(doc_id for doc_id in counts if doc_id not in keep)
        if leftovers:
            await _delete_points(name, leftovers)
            report.removed_points += sum(counts[doc_id] for doc_id in leftovers)
            removed.update(leftovers)
    report.removed_from = sorted(removed)
    report.removed_files = await asyncio.to_thread(_remove_orphan_files, known)
    return report


async def main() -> int:
    try:
        await init_db()
        report = await reconcile()
    except SchemaMismatchError as exc:  # its message is a fixed sentence; the differences are logged
        print(f"The restored database can't be migrated to this release's schema: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # no traceback: messages can hold connection details
        print(f"Reconciling the documents failed: {describe_error(exc)}", file=sys.stderr)
        return 1
    finally:
        await close_qdrant()
        await close_db()
    print("\n".join(report.lines()))
    return 0


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stdout, level=logging.WARNING, format="%(message)s")  # in order with the report
    logging.getLogger("app.db").setLevel(logging.INFO)  # "Database schema migrated from ... to revision ..."
    sys.exit(asyncio.run(main()))
