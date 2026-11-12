"""Шлюз: единая точка входа /api/v1, хранение диалогов, простая страница чата. Порт 8000.

Диалоги пока хранятся в памяти процесса — перенос в PostgreSQL отмечен в README как следующий шаг.
"""

import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from legal_core.config import get_settings
from legal_core.schemas import ChatRequest, ChatResponse

SETTINGS = get_settings()
STATIC = Path(__file__).parent / "static"
DIALOGS = {}  # dialog_id -> {"messages": [...]}
FEEDBACK = []

app = FastAPI(title="Юридический ассистент — gateway", version="0.1.0")


def call(method, url, **kwargs):
    try:
        response = httpx.request(method, url, timeout=SETTINGS["service_timeout"], **kwargs)
    except httpx.HTTPError as exc:
        raise HTTPException(503, f"Сервис недоступен: {exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(response.status_code, response.text[:500])
    return response.json()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    try:
        return call("GET", f"{SETTINGS['orchestrator_url']}/ready")
    except HTTPException:
        return {"ready": False}


@app.post("/api/v1/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    dialog_id = request.dialog_id or uuid.uuid4().hex[:10]
    dialog = DIALOGS.setdefault(dialog_id, {"messages": []})
    history = [m.model_dump() for m in request.history] or [
        {"role": m["role"], "content": m["content"]} for m in dialog["messages"]
    ]
    result = call("POST", f"{SETTINGS['orchestrator_url']}/answer",
                  json={"question": request.question, "history": history})
    dialog["messages"].append({"role": "user", "content": request.question})
    dialog["messages"].append({"role": "assistant", "content": result["answer_text"], "response": result})
    return {**result, "dialog_id": dialog_id}


@app.get("/api/v1/dialogs")
def dialogs():
    return [{"dialog_id": d, "title": v["messages"][0]["content"][:80] if v["messages"] else "", "messages": len(v["messages"])}
            for d, v in DIALOGS.items()]


@app.get("/api/v1/dialogs/{dialog_id}")
def dialog(dialog_id: str):
    if dialog_id not in DIALOGS:
        raise HTTPException(404, "Диалог не найден")
    return {"dialog_id": dialog_id, **DIALOGS[dialog_id]}


@app.get("/api/v1/documents")
def documents():
    return call("GET", f"{SETTINGS['retrieval_url']}/documents")


@app.get("/api/v1/documents/{doc_id}")
def document(doc_id: str):
    return call("GET", f"{SETTINGS['retrieval_url']}/documents/{doc_id}")


@app.get("/api/v1/chunks/{chunk_id}")
def chunk(chunk_id: str):
    return call("GET", f"{SETTINGS['retrieval_url']}/chunks/{chunk_id}")


@app.post("/api/v1/feedback")
def feedback(payload: dict):
    FEEDBACK.append(payload)
    return {"ok": True}


@app.get("/")
def index_page():
    return FileResponse(STATIC / "index.html")
