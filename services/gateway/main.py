"""Шлюз: единая точка входа /api/v1, хранение диалогов, простая страница чата. Порт 8000.

Диалоги, сообщения, ссылки и оценки хранятся в PostgreSQL (DATABASE_URL), без него — в памяти: storage.py.
Каждый запрос получает request_id (заголовок X-Request-ID): он передаётся в orchestrator, пишется в логи
всех сервисов и возвращается клиенту, а по нему можно получить трассировку: GET /api/v1/trace/{request_id}.
"""

import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from legal_core.config import get_settings
from legal_core.schemas import ChatRequest, ChatResponse
from legal_core.sse import REQUEST_ID_HEADER, SSE_HEADERS, get_logger, new_request_id, parse_sse, sse_event
from services.gateway import storage

SETTINGS = get_settings()
STATIC = Path(__file__).parent / "static"
LOG = get_logger("gateway")


@asynccontextmanager
async def lifespan(_app):
    LOG.info("storage=%s", storage.init_storage())
    yield


app = FastAPI(title="Юридический ассистент — gateway", version="0.3.0", lifespan=lifespan)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request.state.request_id = new_request_id(request.headers.get(REQUEST_ID_HEADER))
    started = time.time()
    response = await call_next(request)
    response.headers[REQUEST_ID_HEADER] = request.state.request_id
    if request.url.path not in ("/health", "/ready"):
        LOG.info("request_id=%s %s %s -> %s %dms", request.state.request_id, request.method, request.url.path,
                 response.status_code, (time.time() - started) * 1000)
    return response


def call(method, url, request_id=None, **kwargs):
    headers = {REQUEST_ID_HEADER: request_id} if request_id else {}
    try:
        response = httpx.request(method, url, timeout=SETTINGS["service_timeout"], headers=headers, **kwargs)
    except httpx.HTTPError as exc:
        raise HTTPException(503, f"Сервис недоступен: {exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(response.status_code, response.text[:500])
    return response.json()


def open_dialog(request: ChatRequest):
    dialog_id = request.dialog_id or uuid.uuid4().hex[:10]
    history = [m.model_dump() for m in request.history] or storage.get_history(dialog_id)
    return dialog_id, history


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    try:
        return {**call("GET", f"{SETTINGS['orchestrator_url']}/ready"), "storage": storage.backend()}
    except HTTPException:
        return {"ready": False, "storage": storage.backend()}


@app.post("/api/v1/chat", response_model=ChatResponse)
def chat(request: ChatRequest, http_request: Request):
    request_id = http_request.state.request_id
    dialog_id, history = open_dialog(request)
    result = call("POST", f"{SETTINGS['orchestrator_url']}/answer", request_id,
                  json={"question": request.question, "history": history})
    result = {**result, "dialog_id": dialog_id}
    storage.save_turn(dialog_id, request.question, result)
    return result


@app.post("/api/v1/chat/stream")
def chat_stream(request: ChatRequest, http_request: Request):
    """Поток SSE: meta → stage… → result (или error). Формат событий — в legal_core/sse.py."""
    request_id = http_request.state.request_id
    dialog_id, history = open_dialog(request)

    def stream():
        yield sse_event("meta", {"request_id": request_id, "dialog_id": dialog_id})
        try:
            with httpx.stream("POST", f"{SETTINGS['orchestrator_url']}/answer/stream",
                              json={"question": request.question, "history": history},
                              headers={REQUEST_ID_HEADER: request_id},
                              timeout=SETTINGS["service_timeout"]) as upstream:
                upstream.raise_for_status()
                for event, data in parse_sse(upstream.iter_lines()):
                    if event == "result":
                        data = {**data, "dialog_id": dialog_id}
                        storage.save_turn(dialog_id, request.question, data)
                    yield sse_event(event, data)
        except httpx.HTTPError as exc:
            LOG.warning("request_id=%s stream failed: %s", request_id, exc)
            yield sse_event("error", {"message": "Сервис ответа недоступен. Попробуйте ещё раз.",
                                      "request_id": request_id})

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.get("/api/v1/trace/{request_id}")
def trace(request_id: str):
    """Как был получен ответ: запросы к поиску, найденные фрагменты, время этапов, замечания проверки."""
    found = storage.get_trace(request_id)
    if not found:
        raise HTTPException(404, "Трассировка не найдена")
    return found


@app.get("/api/v1/dialogs")
def dialogs():
    return storage.list_dialogs()


@app.get("/api/v1/dialogs/{dialog_id}")
def dialog(dialog_id: str):
    found = storage.get_dialog(dialog_id)
    if found is None:
        raise HTTPException(404, "Диалог не найден")
    return {"dialog_id": dialog_id, **found}


@app.get("/api/v1/documents")
def documents(http_request: Request):
    return call("GET", f"{SETTINGS['retrieval_url']}/documents", http_request.state.request_id)


@app.get("/api/v1/documents/{doc_id}")
def document(doc_id: str, http_request: Request):
    return call("GET", f"{SETTINGS['retrieval_url']}/documents/{doc_id}", http_request.state.request_id)


@app.get("/api/v1/chunks/{chunk_id}")
def chunk(chunk_id: str, http_request: Request):
    return call("GET", f"{SETTINGS['retrieval_url']}/chunks/{chunk_id}", http_request.state.request_id)


@app.post("/api/v1/feedback")
def feedback(payload: dict):
    storage.save_feedback(payload)
    return {"ok": True}


@app.get("/")
def index_page():
    return FileResponse(STATIC / "index.html")
