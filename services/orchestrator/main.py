"""Сервис-оркестратор: полный конвейер ответа. Порт 8001."""

import httpx
from fastapi import FastAPI

from legal_core.config import get_settings
from legal_core.llm import chat_json
from legal_core.pipeline import answer_question
from legal_core.schemas import AnswerRequest

SETTINGS = get_settings()
app = FastAPI(title="orchestrator")


def retrieve_via_service(queries, filters):
    response = httpx.post(f"{SETTINGS['retrieval_url']}/search", json={"queries": queries, "filters": filters},
                          timeout=SETTINGS["service_timeout"])
    response.raise_for_status()
    return response.json()["context"]


DEPS = {
    "llm_json": lambda messages, max_tokens, default: chat_json(messages, SETTINGS, max_tokens, default),
    "retrieve": retrieve_via_service,
}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    try:
        ok = httpx.get(f"{SETTINGS['retrieval_url']}/ready", timeout=5).json().get("ready", False)
    except httpx.HTTPError:
        ok = False
    return {"ready": ok, "llm_model": SETTINGS["llm_model"], "llm_key_set": bool(SETTINGS["llm_api_key"])}


@app.post("/answer")
def answer(request: AnswerRequest):
    history = [m.model_dump() for m in request.history]
    return answer_question(request.question, history, DEPS, SETTINGS)
