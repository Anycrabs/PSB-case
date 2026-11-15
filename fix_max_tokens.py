"""Исправление MAX_TOKENS (вместо max-tokens.patch, если git apply не сработал, например из-за CRLF в Windows).

Запуск из корня проекта:  python fix_max_tokens.py
  * legal_core/config.py: MAX_TOKENS от платформы берётся как есть, без подъёма до 3000;
  * legal_core/llm.py: при HTTP 400/413/422 с max_tokens > 1024 запрос повторяется с 1024, значение запоминается.
Повторный запуск ничего не ломает: уже сделанные правки пропускаются.
"""

import re
import sys
from pathlib import Path

RETRY_BLOCK = '''{i}if response.status_code in (400, 413, 422) and body["max_tokens"] > 1024:
{i}    # Частая причина 400 — max_tokens больше лимита прокси: повторяем с 1024 и запоминаем для всех вызовов
{i}    print(f"LLM: HTTP {{response.status_code}} при max_tokens={{body['max_tokens']}}, повторяю с 1024: "
{i}          f"{{response.text[:200]}}", flush=True)
{i}    body["max_tokens"] = STATE["max_tokens_cap"] = 1024
{i}    continue
'''

LIMIT_FUNC = '''def _limit(max_tokens, settings):
    """max_tokens не больше MAX_TOKENS платформы и не больше лимита, на который уже ругался прокси."""
    return min(v for v in (max_tokens, settings.get("llm_max_tokens"), STATE.get("max_tokens_cap")) if v)


'''


def load(path):
    raw = path.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in raw else "\n"
    return raw.replace("\r\n", "\n"), newline


def save(path, text, newline):
    path.write_bytes(text.replace("\n", newline).encode("utf-8"))


def main():
    root = Path.cwd()
    config, llm = root / "legal_core" / "config.py", root / "legal_core" / "llm.py"
    if not config.exists() or not llm.exists():
        sys.exit("Запустите из корня проекта (там, где папка legal_core): python fix_max_tokens.py")

    text, nl = load(config)
    new, n = re.subn(r'max\(\s*_int\(\s*\["MAX_TOKENS"\]\s*,\s*(\d+)\s*\)\s*,\s*\d+\s*\)', r'_int(["MAX_TOKENS"], \1)', text)
    if n:
        save(config, new, nl)
        print("config.py: MAX_TOKENS больше не поднимается до 3000")
    elif re.search(r'"llm_max_tokens":\s*_int\(\s*\["MAX_TOKENS"\]', text):
        print("config.py: уже исправлен")
    else:
        sys.exit('config.py: не нашёл строку "llm_max_tokens" с MAX_TOKENS — пришлите её мне')

    text, nl = load(llm)
    changed = []
    if '"max_tokens_cap"' not in text:
        text, n = re.subn(r'STATE\s*=\s*\{\s*"json_mode"\s*:\s*True\s*\}', 'STATE = {"json_mode": True, "max_tokens_cap": None}', text)
        if not n:
            sys.exit("llm.py: не нашёл строку STATE = {\"json_mode\": True} — пришлите файл мне")
        changed.append("STATE")
    if "def _limit(" not in text:
        text = text.replace("def chat(", LIMIT_FUNC + "def chat(", 1)
        text, n = re.subn(r'"max_tokens":\s*max_tokens,', '"max_tokens": _limit(max_tokens, settings),', text, count=1)
        if not n:
            sys.exit('llm.py: не нашёл "max_tokens": max_tokens в теле запроса — пришлите файл мне')
        changed.append("лимит max_tokens")
    if 'STATE["max_tokens_cap"] = 1024' not in text:
        match = re.search(r'^([ \t]*)if response\.status_code == 400 and use_json:', text, re.M)
        if not match:
            sys.exit("llm.py: не нашёл обработку HTTP 400 — пришлите файл мне")
        text = text[:match.start()] + RETRY_BLOCK.format(i=match.group(1)) + text[match.start():]
        changed.append("повтор при 400")
    if changed:
        save(llm, text, nl)
        print("llm.py: " + ", ".join(changed))
    else:
        print("llm.py: уже исправлен")
    print("Готово. Проверка: python -m pytest -q tests")


if __name__ == "__main__":
    main()
