"""Сервис-оркестратор: полный конвейер ответа. Порт 8001."""

import queue
import threading
import time

import httpx
from fastapi import FastAPI, Header
from fastapi.responses import StreamingResponse

from legal_core.config import get_settings
from legal_core.llm import chat_json
from legal_core.pipeline import answer_question
from legal_core.schemas import AnswerRequest
from legal_core.sse import SSE_HEADERS, get_logger, new_request_id, sse_event

SETTINGS = get_settings()
LOG = get_logger("orchestrator")
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


def run_pipeline(request, request_id, on_stage=None):
    history = [m.model_dump() for m in request.history]
    started = time.time()

    def stage(name):
        LOG.info("request_id=%s stage=%s t=%dms", request_id, name, (time.time() - started) * 1000)
        if on_stage:
            on_stage(name)

    result = answer_question(request.question, history, {**DEPS, "request_id": request_id, "on_stage": stage}, SETTINGS)
    LOG.info("request_id=%s done status=%s sources=%d latency=%dms", request_id, result["status"],
             len(result["sources"]), result["latency_ms"])
    return result


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
def answer(request: AnswerRequest, x_request_id: str | None = Header(default=None)):
    return run_pipeline(request, new_request_id(x_request_id))


@app.post("/answer/stream")
def answer_stream(request: AnswerRequest, x_request_id: str | None = Header(default=None)):
    """То же, что /answer, но этапы конвейера уходят клиенту по мере выполнения (SSE)."""
    request_id = new_request_id(x_request_id)
    events = queue.Queue()

    def worker():
        try:
            result = run_pipeline(request, request_id, lambda name: events.put(("stage", {"stage": name})))
            events.put(("result", result))
        except Exception:  # конвейер упал — клиенту понятная ошибка, в лог подробности
            LOG.exception("request_id=%s failed", request_id)
            events.put(("error", {"message": "Не удалось сформировать ответ. Попробуйте ещё раз.",
                                  "request_id": request_id}))
        finally:
            events.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def stream():
        while (item := events.get()) is not None:
            yield sse_event(*item)

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)
