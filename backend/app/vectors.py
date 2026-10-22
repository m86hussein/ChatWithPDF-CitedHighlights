import threading

from qdrant_client import QdrantClient, models

from .config import settings

_lock = threading.RLock()
_client: QdrantClient | None = None


def client() -> QdrantClient:
    global _client
    with _lock:
        if _client is None:
            if settings.qdrant_url:
                _client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None, timeout=60)
            else:
                _client = QdrantClient(path=str(settings.data_dir / "qdrant"))
            name = settings.qdrant_collection
            if not _client.collection_exists(name):
                _client.create_collection(name, vectors_config=models.VectorParams(
                    size=settings.embed_dim, distance=models.Distance.COSINE))
                _client.create_payload_index(name, "document_id", models.PayloadSchemaType.KEYWORD)
        return _client


def _guard(fn):
    # Embedded (path-based) Qdrant is not thread-safe; a server instance doesn't need the lock.
    def wrapper(*a, **kw):
        if settings.qdrant_url:
            return fn(*a, **kw)
        with _lock:
            return fn(*a, **kw)
    return wrapper


@_guard
def upsert(document_id: str, items: list[tuple[str, list[float], dict]]) -> None:
    c = _client or client()
    for i in range(0, len(items), 256):
        c.upsert(settings.qdrant_collection, points=[
            models.PointStruct(id=cid_to_uuid(cid), vector=vec, payload={"document_id": document_id, "chunk_id": cid, **meta})
            for cid, vec, meta in items[i:i + 256]
        ])


@_guard
def search(document_id: str, vector: list[float], limit: int) -> list[tuple[str, float]]:
    c = _client or client()
    res = c.query_points(
        settings.qdrant_collection, query=vector, limit=limit, with_payload=["chunk_id"],
        query_filter=models.Filter(must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id))]),
    ).points
    return [(p.payload["chunk_id"], p.score) for p in res]


@_guard
def delete_document(document_id: str) -> None:
    c = _client or client()
    c.delete(settings.qdrant_collection, points_selector=models.FilterSelector(filter=models.Filter(
        must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id))])))


def cid_to_uuid(cid: str) -> str:
    return f"{cid[:8]}-{cid[8:12]}-{cid[12:16]}-{cid[16:20]}-{cid[20:32]}"
