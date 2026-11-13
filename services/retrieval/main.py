"""Сервис поиска: гибридный поиск по индексу, сборка контекста для LLM, выдача документов. Порт 8002."""

import json
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import numpy as np
from fastapi import FastAPI, HTTPException

from legal_core.config import get_settings
from legal_core.index import load_index, qdrant_client, qdrant_upload
from legal_core.retrieval import retrieve_context
from legal_core.schemas import SearchRequest

SETTINGS = get_settings()
STATE = {"index": None, "docs": {}, "qdrant": None}


def embed_via_service(texts):
    response = httpx.post(f"{SETTINGS['embedder_url']}/embed", json={"texts": texts},
                          timeout=SETTINGS["service_timeout"])
    response.raise_for_status()
    data = response.json()
    sparse = [{int(k): v for k, v in w.items()} for w in data["sparse"]] if data.get("sparse") else None
    return {"dense": np.asarray(data["dense"], dtype=np.float32), "sparse": sparse}


def load_docs(folder):
    path = Path(folder) / "docs.jsonl"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as fh:
        docs = [json.loads(line) for line in fh if line.strip()]
    return {d["doc_id"]: d for d in docs}


def connect_qdrant(index):
    client = qdrant_client(SETTINGS)
    collection = SETTINGS["qdrant_collection"]
    stale = not client.collection_exists(collection)
    if not stale:
        vectors = client.get_collection(collection).config.params.vectors
        stale = (
            client.count(collection).count != len(index["chunks"])
            or vectors["dense"].size != index["dense"].shape[1]
        )
    if stale:
        qdrant_upload(client, index, collection)
    return {"client": client, "collection": collection}


def check_dimensions(index):
    probe = embed_via_service(["проверка размерности"])["dense"]
    if probe.shape[1] != index["dense"].shape[1]:
        raise RuntimeError(
            f"Индекс собран моделью {index['meta'].get('embedding_model')} "
            f"({index['meta'].get('embedder_mode')}, размерность {index['dense'].shape[1]}), "
            f"а embedder отдаёт размерность {probe.shape[1]}. Пересоберите индекс тем же режимом эмбеддингов."
        )


@asynccontextmanager
async def lifespan(_app):
    STATE["index"] = load_index(SETTINGS["index_dir"])
    check_dimensions(STATE["index"])
    STATE["docs"] = load_docs(SETTINGS["index_dir"])
    if SETTINGS["retrieval_backend"] == "qdrant":
        STATE["qdrant"] = connect_qdrant(STATE["index"])
    yield


app = FastAPI(title="retrieval", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    index = STATE["index"]
    return {"ready": index is not None, "chunks": len(index["chunks"]) if index else 0,
            "backend": SETTINGS["retrieval_backend"]}


@app.post("/search")
def search(request: SearchRequest):
    context = retrieve_context(STATE["index"], request.queries, embed_via_service, SETTINGS,
                               request.filters, STATE["qdrant"])
    return {"context": context}


@app.get("/documents")
def documents():
    counts = {}
    for chunk in STATE["index"]["chunks"]:
        counts[chunk["doc_id"]] = counts.get(chunk["doc_id"], 0) + 1
    titles = {c["doc_id"]: (c["doc_title"], c["doc_type"]) for c in STATE["index"]["chunks"]}
    return [{"doc_id": d, "title": titles[d][0], "doc_type": titles[d][1], "chunks": n} for d, n in counts.items()]


@app.get("/documents/{doc_id}")
def document(doc_id: str):
    chunks = [c for c in STATE["index"]["chunks"] if c["doc_id"] == doc_id]
    if not chunks:
        raise HTTPException(404, "Документ не найден")
    doc = STATE["docs"].get(doc_id, {})
    text = doc.get("text") or "\n\n".join(c["text"] for c in chunks)
    return {"doc_id": doc_id, "title": chunks[0]["doc_title"], "doc_type": chunks[0]["doc_type"], "text": text,
            "chunks": [{k: c[k] for k in ("chunk_id", "citation", "char_span")} for c in chunks]}


@app.get("/chunks/{chunk_id}")
def chunk(chunk_id: str):
    idx = STATE["index"]["by_id"].get(chunk_id)
    if idx is None:
        raise HTTPException(404, "Фрагмент не найден")
    return {k: v for k, v in STATE["index"]["chunks"][idx].items() if k != "embed_text"}
