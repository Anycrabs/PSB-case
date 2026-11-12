"""Хранение диалогов, сообщений, ссылок и оценок.

DATABASE_URL задан (в Docker Compose) — PostgreSQL: история переживает перезапуск, таблицы создаются при старте.
DATABASE_URL пуст (тесты, локальный запуск без Docker) — словари в памяти процесса, как раньше.

Таблицы:
  dialogs   — диалог: id, заголовок (первый вопрос), время создания
  messages  — реплики: роль, текст, request_id, статус и полный ответ (jsonb, вместе с трассировкой)
  citations — источники ответа: метка S#, фрагмент, документ, пункт
  feedback  — оценки ответов
"""

import json
import os
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS dialogs (
    id          text PRIMARY KEY,
    title       text NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS messages (
    id          bigserial PRIMARY KEY,
    dialog_id   text NOT NULL REFERENCES dialogs(id) ON DELETE CASCADE,
    role        text NOT NULL,
    content     text NOT NULL,
    request_id  text,
    status      text,
    response    jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS messages_dialog_idx ON messages (dialog_id, id);
CREATE INDEX IF NOT EXISTS messages_request_idx ON messages (request_id);
CREATE TABLE IF NOT EXISTS citations (
    id          bigserial PRIMARY KEY,
    message_id  bigint NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    label       text,
    chunk_id    text,
    doc_id      text,
    doc_title   text,
    citation    text
);
CREATE TABLE IF NOT EXISTS feedback (
    id          bigserial PRIMARY KEY,
    request_id  text,
    rating      text,
    payload     jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);
"""

TITLE_CHARS = 80
MAX_MEMORY_TRACES = 500
STATE = {"dsn": "", "memory": {"dialogs": {}, "traces": {}, "feedback": []}}


# ---------- общее ----------

def init_storage(dsn=None, attempts=30):
    """Выбирает хранилище и, если это PostgreSQL, создаёт таблицы (ждёт базу до ~30 с)."""
    STATE["dsn"] = (dsn if dsn is not None else os.environ.get("DATABASE_URL", "")).strip()
    if not STATE["dsn"]:
        return "memory"
    for attempt in range(attempts):
        try:
            with _connect() as conn:
                conn.execute(SCHEMA)
            return "postgres"
        except Exception:
            if attempt == attempts - 1:
                raise
            time.sleep(1)
    return "postgres"


def backend():
    return "postgres" if STATE["dsn"] else "memory"


def _connect():
    import psycopg  # нужен только с DATABASE_URL

    return psycopg.connect(STATE["dsn"])


def _trace_record(dialog_id, question, result):
    return {
        "request_id": result.get("request_id"), "dialog_id": dialog_id, "question": question,
        "status": result.get("status"), "latency_ms": result.get("latency_ms", 0),
        "verification": result.get("verification", {}), "trace": result.get("trace", {}),
    }


# ---------- операции ----------

def get_history(dialog_id):
    """Реплики диалога для конвейера: [{"role", "content"}]."""
    dialog = get_dialog(dialog_id)
    return [{"role": m["role"], "content": m["content"]} for m in dialog["messages"]] if dialog else []


def save_turn(dialog_id, question, result):
    """Сохраняет вопрос и ответ (вместе с источниками и трассировкой)."""
    if not STATE["dsn"]:
        memory = STATE["memory"]
        dialog = memory["dialogs"].setdefault(dialog_id, {"messages": []})
        dialog["messages"].append({"role": "user", "content": question})
        dialog["messages"].append({"role": "assistant", "content": result["answer_text"], "response": result})
        memory["traces"][result.get("request_id")] = _trace_record(dialog_id, question, result)
        while len(memory["traces"]) > MAX_MEMORY_TRACES:
            memory["traces"].pop(next(iter(memory["traces"])))
        return
    from psycopg.types.json import Jsonb

    with _connect() as conn:
        conn.execute("INSERT INTO dialogs (id, title) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
                     (dialog_id, question[:TITLE_CHARS]))
        conn.execute("INSERT INTO messages (dialog_id, role, content) VALUES (%s, 'user', %s)", (dialog_id, question))
        message_id = conn.execute(
            "INSERT INTO messages (dialog_id, role, content, request_id, status, response) "
            "VALUES (%s, 'assistant', %s, %s, %s, %s) RETURNING id",
            (dialog_id, result["answer_text"], result.get("request_id"), result.get("status"), Jsonb(result)),
        ).fetchone()[0]
        for s in result.get("sources", []):
            conn.execute(
                "INSERT INTO citations (message_id, label, chunk_id, doc_id, doc_title, citation) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (message_id, s.get("id"), s.get("chunk_id"), s.get("doc_id"), s.get("doc_title"), s.get("citation")),
            )


def list_dialogs():
    """Диалоги от старых к новым: [{"dialog_id", "title", "messages"}]."""
    if not STATE["dsn"]:
        return [{"dialog_id": d, "title": v["messages"][0]["content"][:TITLE_CHARS] if v["messages"] else "",
                 "messages": len(v["messages"])} for d, v in STATE["memory"]["dialogs"].items()]
    with _connect() as conn:
        rows = conn.execute(
            "SELECT d.id, d.title, count(m.id) FROM dialogs d LEFT JOIN messages m ON m.dialog_id = d.id "
            "GROUP BY d.id ORDER BY d.created_at, d.id"
        ).fetchall()
    return [{"dialog_id": r[0], "title": r[1], "messages": r[2]} for r in rows]


def get_dialog(dialog_id):
    """{"messages": [{"role", "content", "response"?}]} или None."""
    if not STATE["dsn"]:
        return STATE["memory"]["dialogs"].get(dialog_id)
    with _connect() as conn:
        if not conn.execute("SELECT 1 FROM dialogs WHERE id = %s", (dialog_id,)).fetchone():
            return None
        rows = conn.execute("SELECT role, content, response FROM messages WHERE dialog_id = %s ORDER BY id",
                            (dialog_id,)).fetchall()
    messages = []
    for role, content, response in rows:
        message = {"role": role, "content": content}
        if response is not None:
            message["response"] = response if isinstance(response, dict) else json.loads(response)
        messages.append(message)
    return {"messages": messages}


def get_trace(request_id):
    if not STATE["dsn"]:
        return STATE["memory"]["traces"].get(request_id)
    with _connect() as conn:
        row = conn.execute(
            "SELECT m.id, m.dialog_id, m.response, "
            "(SELECT content FROM messages u WHERE u.dialog_id = m.dialog_id AND u.id < m.id AND u.role = 'user' "
            " ORDER BY u.id DESC LIMIT 1) "
            "FROM messages m WHERE m.request_id = %s AND m.role = 'assistant' ORDER BY m.id DESC LIMIT 1",
            (request_id,),
        ).fetchone()
    if not row:
        return None
    response = row[2] if isinstance(row[2], dict) else json.loads(row[2])
    return _trace_record(row[1], row[3] or "", response)


def save_feedback(payload):
    if not STATE["dsn"]:
        STATE["memory"]["feedback"].append(payload)
        return
    from psycopg.types.json import Jsonb

    with _connect() as conn:
        conn.execute("INSERT INTO feedback (request_id, rating, payload) VALUES (%s, %s, %s)",
                     (str(payload.get("request_id") or "") or None, str(payload.get("rating") or "") or None,
                      Jsonb(payload)))
