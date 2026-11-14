"""Server-Sent Events и логирование с request_id — общее для gateway и orchestrator.

Поток /chat/stream состоит из событий:
  meta   {"request_id", "dialog_id"}             — только из шлюза, первым событием
  stage  {"stage": analyze|retrieve|generate|review|verify}
  result {ChatResponse}                          — готовый проверенный ответ, последним событием
  error  {"message"}                              — если ответ получить не удалось
Текст ответа не печатается по словам: модель отдаёт JSON, который затем проверяется и правится,
поэтому клиенту уходят реальные этапы конвейера и уже проверенный ответ.
"""

import json
import logging
import uuid

REQUEST_ID_HEADER = "X-Request-ID"
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def new_request_id(value=None):
    """Берёт id из заголовка, если он похож на настоящий, иначе создаёт новый."""
    value = (value or "").strip()
    if value and len(value) <= 64 and all(ch.isalnum() or ch in "-_" for ch in value):
        return value
    return uuid.uuid4().hex[:12]


def sse_event(event, data):
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def parse_sse(lines):
    """Разбирает поток строк SSE в пары (event, data)."""
    event, data = "message", []
    for line in lines:
        if line == "":
            if data:
                yield event, json.loads("\n".join(data))
            event, data = "message", []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        yield event, json.loads("\n".join(data))


def get_logger(name):
    """Логи в одну строку: время, сервис, уровень, сообщение (request_id пишется в самом сообщении)."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger
