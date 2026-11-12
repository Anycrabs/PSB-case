"""Сервис эмбеддингов: BAAI/bge-m3, dense + sparse. Порт 8003."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from legal_core.config import get_settings
from legal_core.embeddings import embed_texts
from legal_core.schemas import EmbedRequest, EmbedResponse

SETTINGS = get_settings()
STATE = {"ready": False}


@asynccontextmanager
async def lifespan(_app):
    embed_texts(["прогрев модели"], SETTINGS)  # в режиме local веса загружаются один раз при старте
    STATE["ready"] = True
    yield


app = FastAPI(title="embedder", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    return {"ready": STATE["ready"], "mode": SETTINGS["embedder_mode"], "model": SETTINGS["embedding_model"]}


@app.post("/embed", response_model=EmbedResponse)
def embed(request: EmbedRequest):
    out = embed_texts(request.texts, SETTINGS)
    sparse = None
    if out["sparse"] is not None:
        sparse = [{str(k): float(v) for k, v in w.items()} for w in out["sparse"]]
    return {"dense": out["dense"].tolist(), "sparse": sparse}
