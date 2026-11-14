"""Локальная копия прокси платформы: один OpenAI-совместимый адрес для генерации, эмбеддингов и реранжирования.

На платформе решение получает LLM_BASE_URL прокси и шлёт туда /chat/completions, /embeddings и /rerank.
Здесь так же:
  POST /v1/chat/completions -> пересылается в настоящую LLM (UPSTREAM_BASE_URL: DeepSeek, Ollama, любая
                               OpenAI-совместимая); имя модели подменяется на UPSTREAM_MODEL;
  POST /v1/embeddings       -> BAAI/bge-m3 из сервиса embedder проекта (как на платформе, только dense);
  POST /v1/rerank           -> приближение: косинус bge-m3 между вопросом и документом. Qwen3 Reranker 8B
                               локально не запустить, поэтому порядок кандидатов будет чуть хуже, чем на платформе;
  GET  /stats               -> сколько вызовов и токенов ушло (сбрасывается: POST /stats/reset).

Режим GigaChat (бесплатный Freemium Сбера): если в .env задан GIGACHAT_CREDENTIALS, генерация идёт в GigaChat API.
Прокси сам получает токен доступа (живёт 30 минут, обновляется заранее), убирает поля, которых GigaChat не знает
(response_format), и пропускает запросы по одному: во Freemium генерация идёт в один поток.

Запуск: docker compose --profile eval up -d platform-proxy   (порт 9100)
"""

import asyncio
import os
import time
import uuid

import httpx
import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


def env(name, default=""):
    return os.getenv(name, default).strip()


UPSTREAM_BASE = env("UPSTREAM_BASE_URL").rstrip("/")
UPSTREAM_KEY = env("UPSTREAM_API_KEY")
if UPSTREAM_KEY.startswith("#") or not UPSTREAM_KEY.isascii():
    # «LLM_API_KEY=   # <-- ЗАПОЛНИТЬ» в .env: в заголовке это дало бы UnicodeEncodeError
    print("ВНИМАНИЕ: LLM_API_KEY в .env не задан (там комментарий или русские буквы) — запросы к LLM пойдут без ключа",
          flush=True)
    UPSTREAM_KEY = ""
UPSTREAM_MODEL = env("UPSTREAM_MODEL")
EMBEDDER_URL = env("EMBEDDER_URL", "http://embedder:8003").rstrip("/")
TIMEOUT = float(env("PROXY_TIMEOUT", "180"))

GIGA_CREDENTIALS = env("GIGACHAT_CREDENTIALS")
if GIGA_CREDENTIALS.startswith("#") or not GIGA_CREDENTIALS.isascii():
    print("ВНИМАНИЕ: GIGACHAT_CREDENTIALS в .env не задан (там комментарий или русские буквы)", flush=True)
    GIGA_CREDENTIALS = ""
GIGA = {
    "on": bool(GIGA_CREDENTIALS),
    "scope": env("GIGACHAT_SCOPE", "GIGACHAT_API_PERS"),
    "model": env("GIGACHAT_MODEL", "GigaChat-2-Max"),
    "auth_url": env("GIGACHAT_AUTH_URL", "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"),
    "base_url": env("GIGACHAT_BASE_URL", "https://gigachat.devices.sberbank.ru/api/v1").rstrip("/"),
    # Сертификаты GigaChat выпущены «Russian Trusted Root CA» (Минцифры). Есть файл сертификата — укажите путь
    # в GIGACHAT_CA_BUNDLE; нет — проверка TLS выключается (только для локальных тестов).
    "verify": env("GIGACHAT_CA_BUNDLE") or False,
    "token": "",
    "expires": 0.0,
}
CONCURRENCY = int(env("PROXY_CONCURRENCY", "1" if GIGA["on"] else "8"))
if not GIGA["on"] and ("platform-proxy" in UPSTREAM_BASE or ":9100" in UPSTREAM_BASE):
    # LLM_BASE_URL указывает на сам прокси (режим GigaChat), но ключа GigaChat нет: иначе запрос ушёл бы по кругу
    print("ВНИМАНИЕ: LLM_BASE_URL указывает на platform-proxy, но GIGACHAT_CREDENTIALS не задан", flush=True)
    UPSTREAM_BASE = ""
GIGA_FIELDS = {"model", "messages", "temperature", "top_p", "max_tokens", "repetition_penalty", "stream"}

app = FastAPI(title="platform-proxy")
LIMIT = asyncio.Semaphore(CONCURRENCY)
TOKEN_LOCK = asyncio.Lock()
if GIGA["on"] and not GIGA["verify"]:
    print("GigaChat: проверка TLS выключена (нет GIGACHAT_CA_BUNDLE) — допустимо только для локальных тестов", flush=True)
STATS = {"chat": 0, "chat_errors": 0, "embeddings": 0, "rerank": 0, "prompt_tokens": 0, "completion_tokens": 0,
         "chat_seconds": 0.0, "started": time.time()}


@app.get("/health")
def health():
    if GIGA["on"]:
        return {"status": "ok", "upstream": "gigachat", "model": GIGA["model"], "concurrency": CONCURRENCY}
    return {"status": "ok", "upstream": UPSTREAM_BASE or None, "model": UPSTREAM_MODEL or None,
            "concurrency": CONCURRENCY}


@app.get("/stats")
def stats():
    return {**STATS, "uptime_s": round(time.time() - STATS["started"], 1)}


@app.post("/stats/reset")
def reset_stats():
    for key in STATS:
        STATS[key] = 0 if key != "started" else time.time()
    STATS["chat_seconds"] = 0.0
    return stats()


async def giga_token():
    """Токен доступа GigaChat: OAuth по ключу авторизации, кэш до истечения (минус минута)."""
    async with TOKEN_LOCK:
        if GIGA["token"] and time.time() < GIGA["expires"] - 60:
            return GIGA["token"]
        headers = {"Authorization": f"Basic {GIGA_CREDENTIALS}", "RqUID": str(uuid.uuid4()),
                   "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
        async with httpx.AsyncClient(timeout=30, verify=GIGA["verify"]) as client:
            response = await client.post(GIGA["auth_url"], headers=headers, data={"scope": GIGA["scope"]})
        response.raise_for_status()
        data = response.json()
        GIGA["token"] = data["access_token"]
        expires = float(data.get("expires_at") or 0)
        GIGA["expires"] = expires / 1000 if expires > 1e11 else (expires or time.time() + 1500)  # мс или с
        return GIGA["token"]


def giga_body(body):
    """Запрос OpenAI -> GigaChat: только знакомые поля, модель из .env, температура строго больше нуля."""
    clean = {k: v for k, v in body.items() if k in GIGA_FIELDS}
    clean["model"] = GIGA["model"]
    clean["stream"] = False
    if clean.get("temperature") is not None and float(clean["temperature"]) <= 0:
        clean["temperature"] = 0.01
    return clean


async def giga_chat(body):
    """Вызов GigaChat с повтором при 429 (лимит потока) и при протухшем токене."""
    payload = giga_body(body)
    for attempt in range(5):
        try:
            token = await giga_token()
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            return JSONResponse({"error": f"GigaChat: не получен токен ({type(exc).__name__}); проверьте "
                                          f"GIGACHAT_CREDENTIALS и GIGACHAT_SCOPE"}, status_code=502)
        async with httpx.AsyncClient(timeout=TIMEOUT, verify=GIGA["verify"]) as client:
            response = await client.post(f"{GIGA['base_url']}/chat/completions", json=payload,
                                         headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        if response.status_code == 401 and attempt == 0:
            GIGA["token"] = ""  # токен истёк раньше срока — получить новый
            continue
        if response.status_code == 429:
            await asyncio.sleep(2 * (attempt + 1))
            continue
        if response.status_code == 422 and "temperature" in payload and attempt == 0:
            payload.pop("temperature")  # на случай, если модель не принимает переданную температуру
            continue
        return response
    return response


@app.post("/v1/chat/completions")
async def chat(request: Request):
    if GIGA["on"]:
        body = await request.json()
        started = time.time()
        async with LIMIT:
            try:
                response = await giga_chat(body)
            except httpx.HTTPError as exc:
                STATS["chat_errors"] += 1
                return JSONResponse({"error": f"GigaChat недоступен: {type(exc).__name__}"}, status_code=502)
        if isinstance(response, JSONResponse):
            STATS["chat_errors"] += 1
            return response
        STATS["chat"] += 1
        STATS["chat_seconds"] += time.time() - started
        if response.status_code >= 400:
            STATS["chat_errors"] += 1
            return JSONResponse({"error": response.text[:500]}, status_code=response.status_code)
        data = response.json()
        usage = data.get("usage") or {}
        STATS["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        STATS["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        return data
    if not UPSTREAM_BASE:
        return JSONResponse({"error": "LLM не настроена: задайте в .env LLM_BASE_URL/LLM_API_KEY или GIGACHAT_CREDENTIALS"},
                            status_code=503)
    body = await request.json()
    if UPSTREAM_MODEL:
        body["model"] = UPSTREAM_MODEL
    headers = {"Authorization": f"Bearer {UPSTREAM_KEY}"} if UPSTREAM_KEY else {}
    started = time.time()
    async with LIMIT:  # как лимит параллельных запросов на прокси платформы
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                response = await client.post(f"{UPSTREAM_BASE}/chat/completions", json=body, headers=headers)
        except httpx.HTTPError as exc:
            STATS["chat_errors"] += 1
            return JSONResponse({"error": f"LLM недоступна: {type(exc).__name__}"}, status_code=502)
    STATS["chat"] += 1
    STATS["chat_seconds"] += time.time() - started
    if response.status_code >= 400:
        STATS["chat_errors"] += 1
        return JSONResponse({"error": response.text[:500]}, status_code=response.status_code)
    data = response.json()
    usage = data.get("usage") or {}
    STATS["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
    STATS["completion_tokens"] += int(usage.get("completion_tokens") or 0)
    return data


async def embed(texts):
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.post(f"{EMBEDDER_URL}/embed", json={"texts": texts})
    response.raise_for_status()
    return np.asarray(response.json()["dense"], dtype=np.float32)


@app.post("/v1/embeddings")
async def embeddings(request: Request):
    body = await request.json()
    texts = body["input"] if isinstance(body["input"], list) else [body["input"]]
    STATS["embeddings"] += 1
    try:
        dense = await embed([str(t) for t in texts])
    except httpx.HTTPError as exc:
        return JSONResponse({"error": f"эмбеддер недоступен: {type(exc).__name__}"}, status_code=502)
    return {"object": "list", "model": body.get("model"),
            "data": [{"object": "embedding", "index": i, "embedding": v.tolist()} for i, v in enumerate(dense)]}


@app.post("/v1/rerank")
async def rerank(request: Request):
    body = await request.json()
    documents = [d if isinstance(d, str) else d.get("text", "") for d in body.get("documents", [])]
    if not documents:
        return {"results": []}
    STATS["rerank"] += 1
    try:
        vectors = await embed([body["query"]] + documents)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": f"эмбеддер недоступен: {type(exc).__name__}"}, status_code=502)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    vectors = vectors / np.where(norms == 0, 1, norms)
    scores = vectors[1:] @ vectors[0]
    order = np.argsort(-scores)[: body.get("top_n") or len(documents)]
    return {"model": body.get("model"),
            "results": [{"index": int(i), "relevance_score": float(scores[i])} for i in order]}
