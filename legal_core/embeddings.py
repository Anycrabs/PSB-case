"""Эмбеддинги BAAI/bge-m3: dense (1024) + sparse (лексические веса).

Режимы (EMBEDDER_MODE):
  local  — модель грузится в процесс через FlagEmbedding (CPU или GPU);
  remote — OpenAI-совместимый POST /embeddings (прокси платформы, по умолчанию адрес и ключ LLM), только dense;
  fake   — детерминированные хеш-векторы для тестов без модели и сети.
"""

import hashlib
import re
import time

import httpx
import numpy as np

_CACHE = {}  # загруженная модель bge-m3, чтобы не грузить её на каждый запрос
FAKE_DIM = 256


def normalize_rows(matrix):
    matrix = np.asarray(matrix, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.clip(norms, 1e-12, None)


def _fake_tokens(text):
    return [t[:6] for t in re.findall(r"[a-zа-яё0-9]+", text.lower()) if len(t) > 1]


def _hash(token, mod):
    return int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % mod


def embed_fake(texts):
    dense = np.zeros((len(texts), FAKE_DIM), dtype=np.float32)
    sparse = []
    for i, text in enumerate(texts):
        weights = {}
        for token in _fake_tokens(text):
            dense[i, _hash(token, FAKE_DIM)] += 1.0
            key = _hash(token, 250_000)
            weights[key] = weights.get(key, 0.0) + 1.0
        total = sum(weights.values()) or 1.0
        sparse.append({k: v / total for k, v in weights.items()})
    return {"dense": normalize_rows(dense), "sparse": sparse}


def _load_bge_m3(settings):
    key = (settings["embedding_model"], settings["embedding_fp16"])
    if key not in _CACHE:
        from FlagEmbedding import BGEM3FlagModel

        _CACHE[key] = BGEM3FlagModel(settings["embedding_model"], use_fp16=settings["embedding_fp16"])
    return _CACHE[key]


def embed_local(texts, settings):
    model = _load_bge_m3(settings)
    out = model.encode(
        texts,
        batch_size=settings["embedding_batch"],
        max_length=settings["embedding_max_length"],
        return_dense=True,
        return_sparse=True,
        return_colbert_vecs=False,
    )
    sparse = [{int(k): float(v) for k, v in weights.items()} for weights in out["lexical_weights"]]
    return {"dense": normalize_rows(out["dense_vecs"]), "sparse": sparse}


def _post_embeddings(client, settings, batch):
    headers = {"Content-Type": "application/json"}
    if settings["embedding_api_key"]:
        headers["Authorization"] = f"Bearer {settings['embedding_api_key']}"
    payload = {"model": settings["embedding_model"], "input": batch, "encoding_format": "float"}
    last_error = None
    for attempt in range(4):
        try:
            response = client.post(f"{settings['embedding_base_url']}/embeddings", json=payload, headers=headers)
            if response.status_code in (408, 429, 500, 502, 503, 504):
                last_error = f"HTTP {response.status_code}"
                time.sleep(1.5 * (attempt + 1))
                continue
            response.raise_for_status()
            data = response.json()["data"]
            vectors = [None] * len(batch)
            for position, item in enumerate(data):
                vectors[int(item.get("index", position))] = item["embedding"]
            if any(v is None for v in vectors):
                raise ValueError("Получены не все эмбеддинги")
            return vectors
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            last_error = str(exc)
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Эмбеддинги недоступны: {last_error}")


def embed_remote(texts, settings):
    """OpenAI-совместимый POST /embeddings (прокси платформы). Только dense-векторы."""
    vectors = []
    with httpx.Client(timeout=120.0) as client:
        for start in range(0, len(texts), settings["embedding_batch"]):
            vectors.extend(_post_embeddings(client, settings, texts[start:start + settings["embedding_batch"]]))
    return {"dense": normalize_rows(vectors), "sparse": None}


def embed_texts(texts, settings):
    if not texts:
        return {"dense": np.zeros((0, 1), dtype=np.float32), "sparse": []}
    mode = settings["embedder_mode"]
    if mode == "fake":
        return embed_fake(texts)
    if mode == "remote":
        return embed_remote(texts, settings)
    if mode == "local":
        return embed_local(texts, settings)
    raise ValueError(f"Неизвестный EMBEDDER_MODE: {mode}")


def embed_texts_batched(texts, settings, batch=256, progress=None):
    """Для индексации большой базы: считает пачками и склеивает результат."""
    dense_parts, sparse_all, has_sparse = [], [], True
    for start in range(0, len(texts), batch):
        out = embed_texts(texts[start:start + batch], settings)
        dense_parts.append(out["dense"])
        if out["sparse"] is None:
            has_sparse = False
        else:
            sparse_all.extend(out["sparse"])
        if progress:
            progress(min(start + batch, len(texts)), len(texts))
    return {"dense": np.vstack(dense_parts), "sparse": sparse_all if has_sparse else None}

