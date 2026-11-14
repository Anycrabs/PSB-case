"""Индекс фрагментов: снимок на диске (для solution.py и сервисов) и загрузка в Qdrant.

Снимок index/:
  chunks.jsonl — фрагменты с метаданными;
  dense.npy    — матрица dense-векторов (n × 1024, нормированы);
  sparse.json  — sparse-веса bge-m3 (если есть);
  meta.json    — модель, размерность, число фрагментов.
"""

import json
from pathlib import Path

import numpy as np

from legal_core.lexical import build_bm25, search_bm25
from legal_core.chunking import CHUNKER_VERSION


# ---------- сборка, сохранение, загрузка ----------

def _sparse_postings(sparse):
    postings = {}
    for i, weights in enumerate(sparse or []):
        for token, weight in weights.items():
            postings.setdefault(int(token), []).append((i, float(weight)))
    return postings


def finalize_index(chunks, dense, sparse, meta):
    return {
        "chunks": chunks,
        "dense": np.asarray(dense, dtype=np.float32),
        "sparse": sparse,
        "sparse_postings": _sparse_postings(sparse),
        "bm25": build_bm25([c["embed_text"] for c in chunks]),
        "by_id": {c["chunk_id"]: i for i, c in enumerate(chunks)},
        "meta": meta,
    }


def build_index(chunks, embeddings, settings):
    meta = {
        "embedding_model": settings["embedding_model"],
        "embedder_mode": settings["embedder_mode"],
        "chunker_version": CHUNKER_VERSION,
        "dim": int(embeddings["dense"].shape[1]) if len(chunks) else 0,
        "count": len(chunks),
        "has_sparse": embeddings["sparse"] is not None,
    }
    return finalize_index(chunks, embeddings["dense"], embeddings["sparse"], meta)


def save_index(index, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / "chunks.jsonl", "w", encoding="utf-8") as fh:
        for chunk in index["chunks"]:
            fh.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    np.save(folder / "dense.npy", index["dense"])
    if index["sparse"] is not None:
        payload = [{str(k): round(v, 5) for k, v in w.items()} for w in index["sparse"]]
        (folder / "sparse.json").write_text(json.dumps(payload), encoding="utf-8")
    (folder / "meta.json").write_text(json.dumps(index["meta"], ensure_ascii=False, indent=1), encoding="utf-8")


def load_index(folder):
    folder = Path(folder)
    with open(folder / "chunks.jsonl", encoding="utf-8") as fh:
        chunks = [json.loads(line) for line in fh if line.strip()]
    dense = np.load(folder / "dense.npy")
    sparse_path = folder / "sparse.json"
    sparse = None
    if sparse_path.exists():
        sparse = [{int(k): v for k, v in w.items()} for w in json.loads(sparse_path.read_text(encoding="utf-8"))]
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    return finalize_index(chunks, dense, sparse, meta)


# ---------- фильтры ----------

def is_valid_on(chunk, event_date):
    """Редакция действует на дату события: valid_from <= date < valid_to (пустые границы открыты).
    Без даты фильтр не применяется: обе редакции попадают в контекст с пометкой, какая действует,
    и модель выбирает нужную по дате из вопроса."""
    if not event_date:
        return True
    start, end = chunk.get("valid_from"), chunk.get("valid_to")
    return (start is None or start <= event_date) and (end is None or event_date < end)


def allowed_mask(index, filters):
    filters = filters or {}
    doc_ids = set(filters.get("doc_ids") or [])
    doc_types = set(filters.get("doc_types") or [])
    event_date = filters.get("event_date")
    return [
        (not doc_ids or c["doc_id"] in doc_ids)
        and (not doc_types or c["doc_type"] in doc_types)
        and is_valid_on(c, event_date)
        for c in index["chunks"]
    ]


# ---------- локальный поиск (LocalBackend) ----------

def search_dense(index, query_vec, allowed, k):
    if not len(index["chunks"]):
        return []
    scores = index["dense"] @ np.asarray(query_vec, dtype=np.float32)
    scores = np.where(np.asarray(allowed), scores, -np.inf)
    top = np.argsort(-scores)[:k]
    return [(int(i), float(scores[i])) for i in top if np.isfinite(scores[i])]


def search_sparse(index, query_weights, allowed, k):
    scores = {}
    for token, q_weight in (query_weights or {}).items():
        for i, d_weight in index["sparse_postings"].get(int(token), []):
            if allowed[i]:
                scores[i] = scores.get(i, 0.0) + q_weight * d_weight
    return sorted(scores.items(), key=lambda item: -item[1])[:k]


def search_lexical(index, query_text, query_weights, allowed, k):
    """bge-m3 sparse, если он есть в индексе и у запроса; иначе BM25."""
    if index["sparse"] is not None and query_weights:
        return search_sparse(index, query_weights, allowed, k)
    return search_bm25(index["bm25"], query_text, allowed, k)


# ---------- Qdrant (QdrantBackend) ----------

def date_to_int(value):
    return int(value.replace("-", "")) if value else None


def qdrant_client(settings):
    from qdrant_client import QdrantClient

    url = settings["qdrant_url"]
    return QdrantClient(location=":memory:") if url == ":memory:" else QdrantClient(url=url, timeout=60)


def qdrant_upload(client, index, collection, batch=256):
    from qdrant_client import models

    dim = int(index["dense"].shape[1])
    sparse_config = {"sparse": models.SparseVectorParams()} if index["sparse"] is not None else None
    if client.collection_exists(collection):
        client.delete_collection(collection)
    client.create_collection(
        collection,
        vectors_config={"dense": models.VectorParams(size=dim, distance=models.Distance.COSINE)},
        sparse_vectors_config=sparse_config,
    )
    for field in ("doc_id", "doc_type"):
        client.create_payload_index(collection, field, models.PayloadSchemaType.KEYWORD)
    for field in ("valid_from_n", "valid_to_n"):
        client.create_payload_index(collection, field, models.PayloadSchemaType.INTEGER)

    points = []
    for i, chunk in enumerate(index["chunks"]):
        vector = {"dense": index["dense"][i].tolist()}
        if index["sparse"] is not None:
            indices, values = sparse_lists(index["sparse"][i])
            vector["sparse"] = models.SparseVector(indices=indices, values=values)
        payload = {
            "chunk_id": chunk["chunk_id"],
            "doc_id": chunk["doc_id"],
            "doc_type": chunk["doc_type"],
            "citation": chunk["citation"],
            "valid_from_n": date_to_int(chunk.get("valid_from")),
            "valid_to_n": date_to_int(chunk.get("valid_to")),
        }
        points.append(models.PointStruct(id=i, vector=vector, payload=payload))
        if len(points) >= batch:
            client.upsert(collection, points)
            points = []
    if points:
        client.upsert(collection, points)


def sparse_lists(weights):
    items = sorted((int(k), float(v)) for k, v in weights.items())
    return [k for k, _ in items], [v for _, v in items]


def qdrant_filter(filters):
    from qdrant_client import models

    filters = filters or {}
    must = []
    if filters.get("doc_ids"):
        must.append(models.FieldCondition(key="doc_id", match=models.MatchAny(any=list(filters["doc_ids"]))))
    if filters.get("doc_types"):
        must.append(models.FieldCondition(key="doc_type", match=models.MatchAny(any=list(filters["doc_types"]))))
    event = date_to_int(filters.get("event_date"))
    if event is not None:
        must.append(models.Filter(should=[
            models.IsEmptyCondition(is_empty=models.PayloadField(key="valid_from_n")),
            models.FieldCondition(key="valid_from_n", range=models.Range(lte=event)),
        ]))
        must.append(models.Filter(should=[
            models.IsEmptyCondition(is_empty=models.PayloadField(key="valid_to_n")),
            models.FieldCondition(key="valid_to_n", range=models.Range(gt=event)),
        ]))
    return models.Filter(must=must) if must else None


def qdrant_search(client, collection, query_vec, query_weights, filters, k):
    """Гибрид dense + sparse одним запросом с RRF на стороне Qdrant. Возвращает [(idx, score)]."""
    from qdrant_client import models

    flt = qdrant_filter(filters)
    prefetch = [models.Prefetch(query=list(map(float, query_vec)), using="dense", limit=k, filter=flt)]
    if query_weights:
        indices, values = sparse_lists(query_weights)
        prefetch.append(models.Prefetch(
            query=models.SparseVector(indices=indices, values=values), using="sparse", limit=k, filter=flt))
    if len(prefetch) == 1:
        result = client.query_points(collection, query=prefetch[0].query, using="dense",
                                     query_filter=flt, limit=k)
    else:
        result = client.query_points(collection, prefetch=prefetch,
                                     query=models.FusionQuery(fusion=models.Fusion.RRF), limit=k)
    return [(int(p.id), float(p.score)) for p in result.points]
