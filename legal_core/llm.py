"""Клиент языковой модели через OpenAI-совместимый API (/chat/completions).

Подходит и для API организаторов, и для api.deepseek.com (модель deepseek-flash),
и для локального сервера (Ollama, vLLM) при разработке без ключа.
"""

import json
import re
import time

import httpx

RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504}
STATE = {"json_mode": True}  # выключается навсегда, если сервер не понимает response_format


def chat(messages, settings, json_mode=False, max_tokens=2000, temperature=None):
    """Возвращает текст ответа модели. Повторяет запрос при сетевых сбоях и 429/5xx."""
    url = f"{settings['llm_base_url']}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if settings["llm_api_key"]:
        headers["Authorization"] = f"Bearer {settings['llm_api_key']}"
    body = {
        "model": settings["llm_model"],
        "messages": messages,
        "temperature": settings["llm_temperature"] if temperature is None else temperature,
        "max_tokens": max_tokens,
    }
    use_json = json_mode and settings["llm_json_mode"] and STATE["json_mode"]
    if use_json:
        body["response_format"] = {"type": "json_object"}

    last_error = None
    for attempt in range(4):
        try:
            response = httpx.post(url, json=body, headers=headers, timeout=settings["llm_timeout"])
            if response.status_code == 400 and use_json:
                # Сервер не понял response_format — повторяем без него, JSON разберём сами
                body.pop("response_format", None)
                use_json = False
                STATE["json_mode"] = False
                continue
            if response.status_code in RETRY_STATUS:
                last_error = f"HTTP {response.status_code}: {response.text[:300]}"
                time.sleep(1.5 * (attempt + 1))
                continue
            response.raise_for_status()
            message = response.json()["choices"][0]["message"]
            return message.get("content") or ""
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            last_error = str(exc)
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"LLM недоступна: {last_error}")


def parse_json_loose(text):
    """Достаёт JSON из ответа модели: снимает ```json-обёртку и рассуждения <think>…</think>."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def chat_json(messages, settings, max_tokens=2000, default=None):
    """Вызов LLM с разбором JSON; при неудачном разборе — одна повторная попытка, затем default."""
    for attempt in range(2):
        raw = chat(messages, settings, json_mode=True, max_tokens=max_tokens)
        try:
            return parse_json_loose(raw)
        except (json.JSONDecodeError, ValueError):
            messages = messages + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": "Ответ не является корректным JSON. Верни только JSON по схеме."},
            ]
    if default is not None:
        return default
    raise ValueError("Модель не вернула корректный JSON")
