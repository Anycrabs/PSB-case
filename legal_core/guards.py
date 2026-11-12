"""Дешёвые правила до вызова LLM: попытки сменить роль и поддельные системные сообщения."""

import re

INJECTION_PATTERNS = [
    r"игнорир\w*\s+(?:все\s+)?(?:предыдущ|прошл|сво)\w*\s+(?:инструкц|правил|указани)",
    r"забудь\s+(?:все\s+)?(?:правила|инструкции|указания|что\s+тебе)",
    r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions",
    r"(?:покажи|выведи|раскрой|напечатай)\s+(?:свой\s+|твой\s+)?(?:системн\w+\s+)?(?:промпт|prompt|инструкци)",
    r"system\s*prompt|системн\w+\s+промпт",
    r"ты\s+(?:теперь|больше\s+не)\s+(?!юрист)",
    r"you\s+are\s+now\b",
    r"режим\w*\s+разработчика|developer\s+mode|\bjailbreak\b|\bDAN\b",
    r"^\s*(?:\[|<|#+\s*)?(?:system|система|developer|системное сообщение)\s*(?:\]|>|:)",
]
INJECTION_RE = [re.compile(p, re.I | re.M) for p in INJECTION_PATTERNS]


def detect_injection(text):
    for pattern in INJECTION_RE:
        match = pattern.search(text or "")
        if match:
            return match.group(0)
    return None


def sanitize_history(history):
    """Оставляет только роли user/assistant и помечает реплики с признаками атаки."""
    clean = []
    for message in history or []:
        role = message.get("role")
        if role not in ("user", "assistant"):
            role = "user"  # «системные» сообщения из истории считаем словами пользователя
        content = str(message.get("content", ""))
        if detect_injection(content):
            content = "[реплика содержала попытку изменить правила работы ассистента и проигнорирована]"
        clean.append({"role": role, "content": content})
    return clean
