"""Qdrant wrapper. One collection per embedding model; every search is access-filtered."""

import asyncio
import logging
import uuid
from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qm

from app.config import settings

logger = logging.getLogger(__name__)

_NAMESPACE = uuid.UUID("6f1c7a52-3b0e-4b5e-9d7a-2a1f0c9e8b11")
_client: AsyncQdrantClient | None = None
_create_lock = asyncio.Lock()


def get_qdrant() -> AsyncQdrantClient:
    global _client
    if _client is None:
        if settings.qdrant_url == ":memory:":
            _client = AsyncQdrantClient(location=":memory:")
        else:
            _client = AsyncQdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key, timeout=60)
    return _client


async def close_qdrant() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


def collection_name(embedding_model_id: int) -> str:
    return f"{settings.qdrant_collection_prefix}_{embedding_model_id}"


def point_id(doc_id: int, chunk_index: int) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"{doc_id}:{chunk_index}"))


@dataclass
class ChunkPoint:
    doc_id: int
    chunk_index: int
    vector: list[float]
    owner_id: int
    scope: str
    filename: str
    page: int | None
    text: str


@dataclass
class SearchHit:
    doc_id: int
    chunk_index: int
    filename: str
    page: int | None
    text: str
    score: float
    scope: str
    owner_id: int


async def collection_exists(name: str) -> bool:
    return await get_qdrant().collection_exists(name)


async def ensure_collection(name: str, dim: int) -> None:
    """Create the collection (cosine, `dim`) with payload indexes if it doesn't exist.

    Raises ValueError if it exists with a different vector size.
    """
    client = get_qdrant()
    async with _create_lock:
        if await client.collection_exists(name):
            info = await client.get_collection(name)
            vectors = info.config.params.vectors
            size = getattr(vectors, "size", None)
            if size is not None and size != dim:
                raise ValueError(
                    f"Vector collection {name} has dimension {size} but the embedding model returned {dim}. "
                    "Delete and re-add the embedding model, then re-index."
                )
            return
        try:
            await client.create_collection(
                collection_name=name,
                vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
            )
        except Exception:
            if await client.collection_exists(name):  # created concurrently by another process
                return
            raise
        for field, schema in (
            ("doc_id", qm.PayloadSchemaType.INTEGER),
            ("owner_id", qm.PayloadSchemaType.INTEGER),
            ("scope", qm.PayloadSchemaType.KEYWORD),
        ):
            try:
                await client.create_payload_index(collection_name=name, field_name=field, field_schema=schema)
            except Exception as exc:  # local/in-memory mode doesn't support indexes
                logger.debug("Payload index %s on %s not created: %s", field, name, exc)


async def upsert_chunks(name: str, points: list[ChunkPoint]) -> None:
    if not points:
        return
    await get_qdrant().upsert(
        collection_name=name,
        points=[
            qm.PointStruct(
                id=point_id(p.doc_id, p.chunk_index),
                vector=p.vector,
                payload={
                    "doc_id": p.doc_id,
                    "owner_id": p.owner_id,
                    "scope": p.scope,
                    "filename": p.filename,
                    "chunk_index": p.chunk_index,
                    "page": p.page,
                    "text": p.text,
                },
            )
            for p in points
        ],
        wait=True,
    )


async def delete_document_points(name: str, doc_id: int) -> None:
    client = get_qdrant()
    if not await client.collection_exists(name):
        return
    await client.delete(
        collection_name=name,
        points_selector=qm.FilterSelector(
            filter=qm.Filter(must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))])
        ),
        wait=True,
    )


def access_filter(user_id: int, document_ids: list[int] | None) -> qm.Filter:
    """(own private docs OR shared docs) AND optionally doc_id IN document_ids."""
    access = qm.Filter(
        should=[
            qm.Filter(
                must=[
                    qm.FieldCondition(key="scope", match=qm.MatchValue(value="private")),
                    qm.FieldCondition(key="owner_id", match=qm.MatchValue(value=user_id)),
                ]
            ),
            qm.Filter(must=[qm.FieldCondition(key="scope", match=qm.MatchValue(value="shared"))]),
        ]
    )
    must: list = [access]
    if document_ids is not None:
        must.append(qm.FieldCondition(key="doc_id", match=qm.MatchAny(any=list(document_ids))))
    return qm.Filter(must=must)


async def search(
    name: str, vector: list[float], *, user_id: int, top_k: int, document_ids: list[int] | None = None
) -> list[SearchHit]:
    if document_ids is not None and len(document_ids) == 0:
        return []
    client = get_qdrant()
    if not await client.collection_exists(name):
        return []
    response = await client.query_points(
        collection_name=name,
        query=vector,
        query_filter=access_filter(user_id, document_ids),
        limit=top_k,
        with_payload=True,
    )
    hits: list[SearchHit] = []
    for point in response.points:
        payload = point.payload or {}
        hits.append(
            SearchHit(
                doc_id=int(payload.get("doc_id", 0)),
                chunk_index=int(payload.get("chunk_index", 0)),
                filename=str(payload.get("filename", "")),
                page=payload.get("page"),
                text=str(payload.get("text", "")),
                score=float(point.score or 0.0),
                scope=str(payload.get("scope", "")),
                owner_id=int(payload.get("owner_id", 0)),
            )
        )
    return hits


async def drop_collection(name: str) -> None:
    client = get_qdrant()
    if await client.collection_exists(name):
        await client.delete_collection(name)
