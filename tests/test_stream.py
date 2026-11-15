"""Потоковый ответ /chat/stream: этапы конвейера, формат SSE, сквозной request_id и трассировка."""

from fastapi.testclient import TestClient

from legal_core.pipeline import answer_question
from legal_core.sse import new_request_id, parse_sse, sse_event

QUESTION = "Какие документы нужно приложить к проекту договора на согласование?"


def test_pipeline_reports_stages_and_keeps_request_id(deps, settings):
    stages = []
    result = answer_question(QUESTION, [], {**deps, "on_stage": stages.append, "request_id": "req-1"}, settings)
    assert stages == ["retrieve", "generate", "review", "verify"]
    assert result["request_id"] == "req-1"


def test_pipeline_works_without_stage_callback(deps, settings):
    assert answer_question(QUESTION, [], deps, settings)["sources"]


def test_sse_roundtrip_and_request_id():
    text = sse_event("stage", {"stage": "retrieve"}) + sse_event("result", {"answer_text": "Строка 1\nстрока 2"})
    events = list(parse_sse(text.split("\n")))
    assert events == [("stage", {"stage": "retrieve"}), ("result", {"answer_text": "Строка 1\nстрока 2"})]
    assert new_request_id("abc-123_x") == "abc-123_x"
    assert new_request_id("bad id; drop table") != "bad id; drop table"


def _orchestrator(deps, monkeypatch):
    from services.orchestrator import main as orchestrator

    monkeypatch.setattr(orchestrator, "DEPS", deps)
    return TestClient(orchestrator.app)


def test_orchestrator_stream_sends_stages_then_result(deps, monkeypatch):
    client = _orchestrator(deps, monkeypatch)
    with client.stream("POST", "/answer/stream", json={"question": QUESTION},
                       headers={"X-Request-ID": "orch-1"}) as response:
        events = list(parse_sse(response.iter_lines()))
    names = [name for name, _ in events]
    assert names[:-1] == ["stage"] * (len(names) - 1) and names[-1] == "result"
    assert [data["stage"] for name, data in events if name == "stage"][0] == "retrieve"
    assert events[-1][1]["request_id"] == "orch-1"


def test_gateway_stream_saves_dialog_and_trace(deps, monkeypatch):
    from services.gateway import main as gateway

    orchestrator = _orchestrator(deps, monkeypatch)

    def fake_stream(method, url, json, headers, timeout):  # шлюз ходит в orchestrator через тестовый клиент
        return orchestrator.stream(method, "/answer/stream", json=json, headers=headers)

    monkeypatch.setattr(gateway.httpx, "stream", fake_stream)
    client = TestClient(gateway.app)
    with client.stream("POST", "/api/v1/chat/stream", json={"question": QUESTION}) as response:
        request_id = response.headers["X-Request-ID"]
        events = list(parse_sse(response.iter_lines()))

    assert events[0] == ("meta", {"request_id": request_id, "dialog_id": events[0][1]["dialog_id"]})
    result = events[-1][1]
    assert events[-1][0] == "result" and result["request_id"] == request_id
    assert result["dialog_id"] == events[0][1]["dialog_id"]

    trace = client.get(f"/api/v1/trace/{request_id}").json()
    assert trace["question"] == QUESTION and trace["trace"]["context"]
    saved = client.get(f"/api/v1/dialogs/{result['dialog_id']}").json()
    assert [m["role"] for m in saved["messages"]] == ["user", "assistant"]
    assert client.get("/api/v1/trace/unknown").status_code == 404


def test_gateway_stream_reports_unavailable_orchestrator(monkeypatch):
    import httpx

    from services.gateway import main as gateway

    def down(*_args, **_kwargs):
        raise httpx.ConnectError("нет связи")

    monkeypatch.setattr(gateway.httpx, "stream", down)
    with TestClient(gateway.app).stream("POST", "/api/v1/chat/stream", json={"question": "Вопрос"}) as response:
        events = list(parse_sse(response.iter_lines()))
    assert [name for name, _ in events] == ["meta", "error"]
