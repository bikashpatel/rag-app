"""Deleting a user, a conversation or a document leaves nothing of it behind, checked over the whole schema: every row
reachable from the deleted one through an ON DELETE CASCADE foreign key must be gone, every row pointing at a deleted
one through an ON DELETE SET NULL key must have let go of it (and, for a deleted user, be gone too), rows of other
users must be untouched, and the files and vectors must be gone too. A table added later is checked without changing
these tests: every foreign key must cascade or be a listed SET NULL key, and the guards below make sure the setup
gives the deleted user a row in every table and key that the deletion reaches."""

from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from conftest import ApiClient, chat, grant_plan, ingest, txt_bytes
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import Base, SessionLocal
from app.models import (
    BillingOrder,
    BillingPayment,
    BillingReceipt,
    Document,
    GeneratedDocument,
    GeneratedImage,
    GmailPendingSend,
    ImportedDocument,
    LLMModel,
    Message,
    MessageFile,
    MessagePluginItem,
    PlanPeriod,
    PluginAuditEvent,
    PluginConnection,
    UserBillingProfile,
    VisionPage,
    user_allowed_models,
    utcnow,
)
from app.services import storage, vectorstore

CreateUser = Callable[..., Awaitable[ApiClient]]
Rows = dict[str, set[tuple[Any, ...]]]  # table name -> primary keys
Key = tuple[str, str]  # (table, column) of a foreign key

# Keys to a user whose rows outlive them: billing records, kept for the accounts with the username (plans-billing B30).
KEPT_FOR_ACCOUNTS: set[Key] = {
    ("billing_orders", "user_id"),
    ("billing_payments", "user_id"),
    ("billing_receipts", "user_id"),
    ("billing_events", "user_id"),
}
# Keys whose rows outlive what they point at: messages and usage keep their counts when a model goes, a file card its
# name when its document goes, images and pending sends when their conversation goes, and a replaced send when its
# replacement goes.
SET_NULL: set[Key] = {
    ("documents", "embedding_model_id"),
    ("messages", "model_id"),
    ("usage_events", "model_id"),
    ("message_files", "document_id"),
    ("generated_images", "conversation_id"),
    ("gmail_pending_sends", "conversation_id"),
    ("gmail_pending_sends", "replaced_by_id"),
    # Plans and billing (plans-billing 6.2): a period keeps its snapshot when its plan or order goes, an order its
    # quote when its plan, the period it replaced or its payment goes, a user their open-ended plan's end
    ("users", "comp_plan_id"),
    ("plans", "default_chat_model_id"),
    ("plan_periods", "plan_id"),
    ("plan_periods", "order_id"),
    ("billing_orders", "plan_id"),
    ("billing_orders", "replaces_period_id"),
    ("billing_orders", "payment_id"),
    ("billing_payments", "order_id"),
    ("billing_events", "order_id"),
    ("billing_events", "payment_id"),
    *KEPT_FOR_ACCOUNTS,
}


Keyed = dict[Key, set[tuple[Any, ...]]]  # SET NULL key -> primary keys of rows using it


@dataclass
class Reached:
    """What deleting some rows reaches: the rows removed through ON DELETE CASCADE keys, and the rows whose SET NULL key
    points at a removed row."""

    cascaded: Rows
    nulled: Keyed


def reached_by(root: str) -> tuple[set[str], set[Key]]:
    """The tables an ON DELETE CASCADE chain from `root` reaches, and the SET NULL keys pointing at `root` or them."""
    tables: set[str] = set()
    frontier = [root]
    while frontier:
        parent = frontier.pop()
        for table in Base.metadata.sorted_tables:
            if table.name in tables:
                continue
            if any(fk.column.table.name == parent and fk.ondelete == "CASCADE" for fk in table.foreign_keys):
                tables.add(table.name)
                frontier.append(table.name)
    keys = {
        (table.name, fk.parent.name)
        for table in Base.metadata.sorted_tables
        for fk in table.foreign_keys
        if fk.ondelete == "SET NULL" and fk.column.table.name in tables | {root}
    }
    return tables, keys


async def reachable(db: AsyncSession, root: str, ids: set[int]) -> Reached:
    """The rows deleting the `root` rows `ids` must remove (CASCADE keys) or let go of (SET NULL keys)."""
    cascaded: Rows = defaultdict(set)
    nulled: Keyed = defaultdict(set)
    frontier = [(root, ids)]
    while frontier:
        parent, parent_ids = frontier.pop()
        for table in Base.metadata.sorted_tables:
            for fk in table.foreign_keys:
                if fk.column.table.name != parent:
                    continue
                key = list(table.primary_key.columns)
                rows = {tuple(row) for row in (await db.execute(select(*key).where(fk.parent.in_(parent_ids))))}
                if fk.ondelete == "SET NULL":
                    nulled[(table.name, fk.parent.name)] |= rows
                    continue
                new = rows - cascaded[table.name]
                cascaded[table.name] |= new
                if new and len(key) == 1:
                    frontier.append((table.name, {row[0] for row in new}))
    return Reached(
        {name: rows for name, rows in cascaded.items() if rows}, {key: rows for key, rows in nulled.items() if rows}
    )


def outliving(reached: Reached) -> Keyed:
    """The rows a SET NULL key ties to the deleted rows that the deletion keeps."""
    return {key: kept for key, rows in reached.nulled.items() if (kept := rows - reached.cascaded.get(key[0], set()))}


async def still_pointing(db: AsyncSession, rows: Keyed) -> Keyed:
    """Which of `rows` are gone or still hold their SET NULL key."""
    wrong: Keyed = {}
    for (name, column), keys in rows.items():
        table = Base.metadata.tables[name]
        selected = select(*table.primary_key.columns).where(table.c[column].is_(None))
        if bad := keys - {tuple(row) for row in await db.execute(selected)}:
            wrong[(name, column)] = bad
    return wrong


async def remaining(db: AsyncSession, rows: Rows) -> Rows:
    """Which of `rows` still exist."""
    left: Rows = {}
    for name, keys in rows.items():
        table = Base.metadata.tables[name]
        columns = list(table.primary_key.columns)
        existing = {tuple(row) for row in await db.execute(select(*columns))}
        if still := keys & existing:
            left[name] = still
    return left


def pending_send(user_id: int, conversation_id: int, **fields: Any) -> GmailPendingSend:
    return GmailPendingSend(
        user_id=user_id,
        conversation_id=conversation_id,
        account_email="a@example.test",
        account_subject=f"sub-{user_id}",
        to_addrs=[],
        subject="Tides",
        body="High tide at six.",
        content_hash="0" * 64,
        origin="chat",
        expires_at=utcnow() + timedelta(hours=1),
        **fields,
    )


async def populate(client: ApiClient) -> dict[str, int]:
    """A row of the user in every table and SET NULL key that hangs off them: through the API where it has a route, else
    directly."""
    assert client.user_id is not None and client.username is not None
    await grant_plan(client.user_id, "starter", days=30)
    document = await ingest(client, "notes.txt", txt_bytes(f"Notes of {client.username} about the harbour tides."))
    _, events = await chat(client, "What do my notes say about the tides?")
    conversation_id = events[0][1]["conversation_id"]
    assert (await client.patch("/api/me/preferences", json={"theme": "dark"})).status_code == 200
    async with SessionLocal() as db:
        answer = await db.scalar(
            select(Message.id).where(Message.conversation_id == conversation_id, Message.role == "assistant")
        )
        model_id = await db.scalar(select(LLMModel.id).where(LLMModel.kind == "chat", LLMModel.is_default.is_(True)))
        assert answer is not None and model_id is not None
        await db.execute(insert(user_allowed_models).values(user_id=client.user_id, model_id=model_id))
        replacement = pending_send(client.user_id, conversation_id)
        period_id = await db.scalar(select(PlanPeriod.id).where(PlanPeriod.user_id == client.user_id))
        order = BillingOrder(
            public_id=f"order-{client.user_id}",
            user_id=client.user_id,
            username=client.username,
            plan_snapshot={},
            kind="now",
            replaces_period_id=period_id,
            amount_paise=100,
            mode="test",
            status="paid",
            origin="http://testserver",
            expires_at=utcnow(),
            watch_until=utcnow(),
        )
        db.add_all([replacement, order, UserBillingProfile(user_id=client.user_id, name=client.username)])
        await db.flush()
        payment = BillingPayment(
            order_id=order.id,
            user_id=client.user_id,
            username=client.username,
            status="captured",
            amount_paise=100,
            currency="INR",
            mode="test",
        )
        db.add(payment)
        await db.flush()
        db.add(
            BillingReceipt(
                number=f"TEST/26-27/{client.user_id:06d}",
                mode="test",
                financial_year="26-27",
                seq=client.user_id,
                payment_id=payment.id,
                user_id=client.user_id,
                issued_at=utcnow(),
                seller={},
                buyer={},
                lines=[],
                amount_paise=100,
            )
        )
        db.add_all(
            [
                VisionPage(document_id=document["id"], page=1, mode="transcribe", fingerprint="f" * 64, text="p1"),
                GeneratedDocument(document_id=document["id"]),
                GeneratedImage(
                    document_id=document["id"],
                    conversation_id=conversation_id,
                    model_name="openai/gpt-image-2",
                    prompt="tides",
                    alt_text="tides",
                    format="png",
                    quality="low",
                    width=64,
                    height=64,
                ),
                ImportedDocument(
                    document_id=document["id"],
                    plugin_id="gmail",
                    kind="email",
                    external_ref="thread-1",
                    account_email="a@example.test",
                ),
                MessageFile(message_id=answer, document_id=document["id"], filename="tides.pdf", format="pdf"),
                MessagePluginItem(message_id=answer, plugin_id="gmail", card_type="gmail_send", data={}),
                PluginConnection(
                    user_id=client.user_id,
                    plugin_id="gmail",
                    account_subject=f"sub-{client.user_id}",
                    account_email="a@example.test",
                    scopes=[],
                    refresh_token_enc="unreadable",
                    key_id="0" * 16,
                    connected_at=utcnow(),
                ),
                PluginAuditEvent(user_id=client.user_id, plugin_id="gmail", action="search", outcome="ok", origin="ui"),
                pending_send(client.user_id, conversation_id, status="replaced", replaced_by_id=replacement.id),
            ]
        )
        await db.commit()
    return {"document": document["id"], "conversation": conversation_id, "answer": answer}


async def snapshot(root: str, ids: set[int]) -> Reached:
    """The rows deleting `ids` reaches; asserts that they include a row of every table and SET NULL key it can reach,
    so the checks after the deletion cover them all."""
    async with SessionLocal() as db:
        reached = await reachable(db, root, ids)
    tables, keys = reached_by(root)
    assert set(reached.cascaded) == tables, tables - set(reached.cascaded)
    assert set(reached.nulled) == keys, keys - set(reached.nulled)
    return reached


async def stored_name(doc_id: int) -> str:
    async with SessionLocal() as db:
        name = await db.scalar(select(Document.stored_name).where(Document.id == doc_id))
    assert name is not None
    return name


async def vector_count(doc_id: int) -> int:
    client = vectorstore.get_qdrant()
    total = 0
    for collection in (await client.get_collections()).collections:
        found, _ = await client.scroll(collection.name, limit=1000)
        total += sum(1 for point in found if (point.payload or {}).get("doc_id") == doc_id)
    return total


def test_every_foreign_key_cascades_or_is_a_listed_set_null() -> None:
    """A key without ON DELETE makes deleting what it points at fail (a 500 on SQLite and Postgres alike); SET NULL
    keeps the row, so it must be deliberate, and on a key to a user, whose rows go with them, only for the billing
    records kept for the accounts."""
    keys = {(table.name, fk.parent.name): fk for table in Base.metadata.sorted_tables for fk in table.foreign_keys}
    assert {key: fk.ondelete for key, fk in keys.items() if fk.ondelete not in ("CASCADE", "SET NULL")} == {}
    assert {key for key, fk in keys.items() if fk.ondelete == "SET NULL"} == SET_NULL
    users = {key for key, fk in keys.items() if fk.column.table.name == "users" and fk.ondelete != "CASCADE"}
    assert users == KEPT_FOR_ACCOUNTS


async def test_deleting_a_user_removes_every_row_file_and_vector_of_theirs_and_nothing_else(
    admin_client: ApiClient, create_user: CreateUser
) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")
    assert alice.user_id is not None and bob.user_id is not None
    alices, bobs = await populate(alice), await populate(bob)
    theirs = await snapshot("users", {alice.user_id})
    others = await snapshot("users", {bob.user_id})
    # Of what a SET NULL key ties to Alice's rows, only her billing record outlives her (with her username), for the
    # accounts; the rest is hers and goes too.
    assert {key: len(rows) for key, rows in outliving(theirs).items()} == {
        ("billing_orders", "user_id"): 1,
        ("billing_orders", "replaces_period_id"): 1,
        ("billing_payments", "user_id"): 1,
        ("billing_receipts", "user_id"): 1,
        ("billing_events", "user_id"): 3,  # her account created, her open-ended plan and her period granted
    }
    alice_file = storage.stored_path(await stored_name(alices["document"]))
    bob_file = storage.stored_path(await stored_name(bobs["document"]))
    assert alice_file.is_file() and bob_file.is_file() and await vector_count(alices["document"]) > 0

    response = await admin_client.delete(f"/api/admin/users/{alice.user_id}")

    assert response.status_code == 204
    async with SessionLocal() as db:
        assert await remaining(db, {"users": {(alice.user_id,)}, **theirs.cascaded}) == {}
        assert await still_pointing(db, outliving(theirs)) == {}
        assert await reachable(db, "users", {bob.user_id}) == others
    assert not alice_file.exists() and bob_file.is_file()
    assert await vector_count(alices["document"]) == 0 and await vector_count(bobs["document"]) > 0
    assert (await bob.get(f"/api/conversations/{bobs['conversation']}")).status_code == 200


async def test_deleting_a_conversation_removes_its_messages_and_their_rows_and_keeps_its_documents(
    create_user: CreateUser,
) -> None:
    alice = await create_user("alice")
    ids = await populate(alice)
    theirs = await snapshot("conversations", {ids["conversation"]})
    # Images and pending sends made in it outlive it.
    assert {key: len(rows) for key, rows in outliving(theirs).items()} == {
        ("generated_images", "conversation_id"): 1,
        ("gmail_pending_sends", "conversation_id"): 2,
    }

    response = await alice.delete(f"/api/conversations/{ids['conversation']}")

    assert response.status_code == 204
    async with SessionLocal() as db:
        assert await remaining(db, {"conversations": {(ids["conversation"],)}, **theirs.cascaded}) == {}
        assert await still_pointing(db, outliving(theirs)) == {}
    assert [item["id"] for item in (await alice.get("/api/documents")).json()] == [ids["document"]]


async def test_deleting_a_document_removes_its_rows_file_and_vectors_and_keeps_the_answer_that_made_it(
    create_user: CreateUser,
) -> None:
    alice = await create_user("alice")
    ids = await populate(alice)
    theirs = await snapshot("documents", {ids["document"]})
    path = storage.stored_path(await stored_name(ids["document"]))

    response = await alice.delete(f"/api/documents/{ids['document']}")

    assert response.status_code == 204
    async with SessionLocal() as db:
        assert await remaining(db, {"documents": {(ids["document"],)}, **theirs.cascaded}) == {}
        assert await still_pointing(db, outliving(theirs)) == {}
        card = await db.scalar(select(MessageFile).where(MessageFile.message_id == ids["answer"]))
    assert card is not None and (card.document_id, card.filename) == (None, "tides.pdf")
    assert not path.exists() and await vector_count(ids["document"]) == 0
    conversation = (await alice.get(f"/api/conversations/{ids['conversation']}")).json()
    assert [message["role"] for message in conversation["messages"]] == ["user", "assistant"]
