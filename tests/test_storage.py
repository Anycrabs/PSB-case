"""Хранилище диалогов шлюза: в памяти всегда, в PostgreSQL — если задан TEST_DATABASE_URL.

  docker run -d --rm --name pg-test -e POSTGRES_PASSWORD=test -p 55432:5432 postgres:16-alpine
  TEST_DATABASE_URL=postgresql://postgres:test@localhost:55432/postgres pytest tests/test_storage.py
"""

import os
import uuid

import pytest

from services.gateway import storage

RESULT = {
    "request_id": "req-storage", "status": "answer", "answer_text": "Арендная плата — 450 000 рублей.",
    "missing_info": [], "latency_ms": 12, "verification": {"passed": True, "issues": []},
    "trace": {"queries": ["аренда"], "context": [{"label": "S1"}]},
    "sources": [{"id": "S1", "chunk_id": "c1", "doc_id": "d1", "doc_title": "Договор аренды", "citation": "п. 3.1",
                 "edition": "", "quote": "3.1. …", "char_span": [0, 5]}],
}

BACKENDS = ["memory"] + (["postgres"] if os.environ.get("TEST_DATABASE_URL") else [])


@pytest.fixture(params=BACKENDS)
def store(request):
    saved = dict(storage.STATE)
    storage.STATE["memory"] = {"dialogs": {}, "traces": {}, "feedback": []}
    dsn = os.environ["TEST_DATABASE_URL"] if request.param == "postgres" else ""
    try:
        assert storage.init_storage(dsn, attempts=5) == request.param
        yield storage
    finally:  # даже если база недоступна, остальные тесты должны работать с хранилищем в памяти
        storage.STATE.clear()
        storage.STATE.update(saved)


def test_turn_roundtrip(store):
    dialog_id = uuid.uuid4().hex[:10]
    request_id = uuid.uuid4().hex[:12]
    result = {**RESULT, "request_id": request_id, "dialog_id": dialog_id}
    store.save_turn(dialog_id, "Какая арендная плата?", result)
    store.save_turn(dialog_id, "А до какого числа?", {**result, "request_id": request_id + "b"})

    assert store.get_history(dialog_id) == [
        {"role": "user", "content": "Какая арендная плата?"},
        {"role": "assistant", "content": RESULT["answer_text"]},
        {"role": "user", "content": "А до какого числа?"},
        {"role": "assistant", "content": RESULT["answer_text"]},
    ]
    dialog = store.get_dialog(dialog_id)
    assert dialog["messages"][1]["response"]["sources"][0]["citation"] == "п. 3.1"
    listed = {d["dialog_id"]: d for d in store.list_dialogs()}
    assert listed[dialog_id] == {"dialog_id": dialog_id, "title": "Какая арендная плата?", "messages": 4}

    trace = store.get_trace(request_id)
    assert trace["question"] == "Какая арендная плата?" and trace["dialog_id"] == dialog_id
    assert trace["trace"]["queries"] == ["аренда"]
    assert store.get_trace("missing") is None
    assert store.get_dialog("missing") is None
    store.save_feedback({"request_id": request_id, "rating": "up"})
