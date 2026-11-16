"""Сабмит v3 (кейс ПСБ, Hack&Change 2026). Основа — v2: тот же поиск, 1 эмбеддинг и 1 вызов модели на вопрос.

Что изменено относительно v2 (все пункты проверены на мок-прокси с отказами):
  1. Вызов модели подстраивается под прокси и запоминает рабочий вариант на весь прогон:
     - 4xx на большой max_tokens -> значение платформы (MAX_TOKENS), запоминается после первого же случая
       (в v2 это стоило лишнего вызова на КАЖДЫЙ вопрос; 403/404 не обрабатывались вовсе);
     - пустой или оборванный ответ (DeepSeek V4.1 Flash рассуждает по умолчанию, и рассуждения расходуют
       max_tokens) -> тот же вопрос с меньшим объёмом рассуждений (reasoning.effort low -> none), а если
       прокси эти поля не принимает -> с удвоенным max_tokens. В v2 повторялся тот же самый запрос, 6 раз;
     - если по темпу первых ответов прогон не укладывается во время -> рассуждения уменьшаются для всех.
     Пока всё работает, запрос тот же, что в v2: никаких новых полей и пробных вызовов.
  2. submission.csv пишется сразу после построения индекса (текст найденных пунктов, без API) и обновляется
     каждые 5 ответов: при любом сбое или убийстве по времени файл уже есть.
  3. Запас по времени: новые вызовы модели прекращаются на 930-й секунде, выход — не позже 1020-й (лимит 1200).
  4. Эмбеддинги: тайм-аут 45 с на пачку и 15 с на вопрос, 2 попытки, на всю базу не дольше 150 с;
     при сбое поиск идёт по BM25 и точным ссылкам (в v2 включались хеш-векторы, с ними поиск хуже: 45/48 против 48/48).
  5. Промпт под формат эталона: 1–3 предложения, только то, о чём спросили, документ и пункт; без меток [S#],
     без встречных вопросов. Автоматическое дописывание оговорок кодом убрано (оговорки просит сам промпт).
"""

import csv
import hashlib
import os
import pickle
import re
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from lc_chunking import chunk_documents  # noqa: E402
from lc_config import get_settings  # noqa: E402
from lc_dates import extract_event_period  # noqa: E402
from lc_embeddings import embed_texts  # noqa: E402
from lc_guards import detect_injection, sanitize_history  # noqa: E402
from lc_index import build_index  # noqa: E402
from lc_parsing import read_knowledge_base  # noqa: E402
from lc_pipeline import extractive_answer, heuristic_queries, parse_dialog  # noqa: E402
from lc_prompts import ATTACK_ANSWER, NO_CONTEXT_ANSWER, text_messages  # noqa: E402
from lc_retrieval import hybrid_search, retrieve_context  # noqa: E402
from lc_verify import strip_markers  # noqa: E402

STARTED = time.time()
FALLBACK = NO_CONTEXT_ANSWER
MAX_CHARS = 9500
RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
INDEX_EMBED_LIMIT_S = 150  # дольше этого базу не векторизуем: BM25 на этой базе находит нужный пункт и без векторов


def _env_int(name, default):
    value = (os.environ.get(name) or "").strip()
    return int(value) if value.isdigit() else default


SOFT_AT = STARTED + _env_int("TIME_BUDGET_S", 930)   # после этого новые вызовы модели не начинаем
HARD_AT = SOFT_AT + 90                               # сторож: дописать файл и выйти

# Режимы рассуждений (формат OpenRouter, через него работает прокси платформы: модели названы его идентификаторами).
MODES = {"plain": {}, "low": {"reasoning": {"effort": "low"}}, "off": {"reasoning": {"effort": "none"}}}
ORDER = ["plain", "low", "off"]
LLM = {"mode": "plain", "dead": set(), "max_tokens": 3000, "floor": 1024, "votes": {}, "exhausted": {},
       "ok": 0, "lat": [], "calls": 0, "dry": 0, "lock": threading.Lock()}


def log(message):
    print(f"[{time.time() - STARTED:6.1f}s] {message}", flush=True)


def find_file(data_dir, *names):
    for root in (Path(data_dir), ROOT / data_dir, Path.cwd() / data_dir, Path.cwd(), ROOT, Path.cwd() / "data"):
        for name in names:
            path = root / name
            if path.is_file():
                return path
    raise FileNotFoundError(f"Не найден ни один из файлов: {', '.join(names)}")


def load_questions(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames or not {"id", "question"}.issubset(reader.fieldnames):
            raise ValueError("В questions.csv нужны колонки id и question")
        rows = [(str(r["id"]).strip(), str(r["question"] or "").strip()) for r in reader]
    return [(qid, q) for qid, q in rows if qid]


def write_submission(questions, answers, path="submission.csv"):
    tmp = Path(path).with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, quoting=csv.QUOTE_ALL, lineterminator="\n")
        writer.writerow(["id", "answer"])
        seen = set()
        for qid, _ in questions:
            if qid in seen:
                continue
            seen.add(qid)
            text = " ".join(str(answers.get(qid) or FALLBACK).split())[:MAX_CHARS] or FALLBACK
            writer.writerow([qid, text])
    os.replace(tmp, path)


# ---------- индекс ----------

def embed_chunks(texts, settings):
    """Векторы базы последовательно, пачками; общий предел времени — INDEX_EMBED_LIMIT_S."""
    parts, begun = [], time.time()
    for start in range(0, len(texts), 64):
        if time.time() - begun > INDEX_EMBED_LIMIT_S:
            raise TimeoutError("векторизация базы идёт слишком долго")
        parts.append(embed_texts(texts[start:start + 64], settings)["dense"])
    return {"dense": np.vstack(parts), "sparse": None}


def build_or_load_index(kb_path, settings):
    """Возвращает (индекс, есть ли настоящие векторы)."""
    digest = hashlib.sha1(Path(kb_path).read_bytes()).hexdigest()[:16]
    cache = Path("/tmp") / f"psb_v3_{digest}_{settings['embedding_model'].replace('/', '_')}.pkl"
    if cache.exists():
        try:
            return pickle.loads(cache.read_bytes()), True
        except Exception:  # noqa: BLE001 — битый кэш просто пересобираем
            pass
    docs, errors = read_knowledge_base(kb_path)
    for err in errors:
        log(f"Пропущен документ {err['file']}: {err['error']}")
    chunks = chunk_documents(docs)
    log(f"Документов: {len(docs)}, фрагментов: {len(chunks)}")
    texts = [c["embed_text"] for c in chunks]
    try:
        index = build_index(chunks, embed_chunks(texts, settings), settings)
    except Exception as exc:  # noqa: BLE001 — без векторов работаем на BM25 и точных ссылках
        log(f"Эмбеддинги базы недоступны ({type(exc).__name__}: {str(exc)[:120]}); поиск по BM25")
        fake = {**settings, "embedder_mode": "fake"}  # матрица-заглушка нужна только структуре индекса
        return build_index(chunks, embed_texts(texts, fake), fake), False
    try:
        cache.write_bytes(pickle.dumps(index))
    except OSError:
        pass
    return index, True


# ---------- вызов модели ----------

def lower_mode(mode):
    """Следующий режим с меньшим объёмом рассуждений, который прокси ещё не отклонял."""
    for name in ORDER[ORDER.index(mode) + 1:]:
        if name not in LLM["dead"]:
            return name
    return None


def clean_answer(content):
    content = re.sub(r"<think>.*?(</think>|$)", "", content or "", flags=re.S)
    content = re.sub(r"\*\*|__|^#+\s*", "", content, flags=re.M)
    content = re.sub(r"^\s*(?:Ответ|Справка)\s*:\s*", "", content.strip())
    return " ".join(content.split())


def trim_to_sentence(text):
    """Оборванный по лимиту ответ — до последнего законченного предложения."""
    cut = max(text.rfind(". "), text.rfind(".) "), text.rfind("» "))
    if text.endswith((".", "»", ")")):
        return text
    return text[:cut + 1].strip() if cut > 40 else ""


def remember(mode, max_tokens, forced=False):
    """Запоминает на весь прогон вариант запроса, который сработал после сбоя исходного."""
    with LLM["lock"]:
        key = (mode, max_tokens)
        LLM["votes"][key] = LLM["votes"].get(key, 0) + 1
        if (forced or LLM["votes"][key] >= 2) and (LLM["mode"], LLM["max_tokens"]) != key:
            LLM["mode"], LLM["max_tokens"], LLM["lat"], LLM["prev"] = mode, max_tokens, [], None
            log(f"Модель: дальше режим «{mode}», max_tokens={max_tokens}")


def llm_text(messages, settings, deadline):
    """Ответ модели простым текстом или "" (тогда вызывающий подставит текст найденного пункта)."""
    url = f"{settings['llm_base_url']}/chat/completions"
    headers = {"Authorization": f"Bearer {settings['llm_api_key']}"} if settings["llm_api_key"] else {}
    with LLM["lock"]:
        mode, max_tokens = LLM["mode"], LLM["max_tokens"]
    start_cfg, partial, lowered, exhausted, waits = (mode, max_tokens), "", False, False, 0
    # если модель не дала ни одного ответа на трёх вопросах подряд, перебор вариантов не помогает:
    # дальше по одной попытке на вопрос (вдруг заработает), остальное время не тратим
    tries = 1 if (LLM["ok"] == 0 and LLM["dry"] >= 3) else 7
    for _ in range(tries):
        now = time.time()
        if now > deadline:
            break
        body = {"model": settings["llm_model"], "messages": messages, "temperature": settings["llm_temperature"],
                "max_tokens": max_tokens, **MODES[mode]}
        with LLM["lock"]:
            LLM["calls"] += 1
        began = time.time()
        try:
            response = httpx.post(url, json=body, headers=headers, timeout=max(10.0, min(75.0, HARD_AT - now - 5)))
        except httpx.TimeoutException:
            nxt = lower_mode(mode)  # не успела за 75 с: просим рассуждать меньше
            if nxt:
                mode, exhausted = nxt, True
            continue
        except httpx.HTTPError:
            waits += 1
            time.sleep(min(2.0 * waits, 8))
            continue
        code = response.status_code
        if code in RETRY_STATUS:
            waits += 1
            if waits > 3:
                break
            wait = response.headers.get("retry-after", "")
            time.sleep(min(float(wait) if wait.replace(".", "", 1).isdigit() else 3.0 * waits ** 2, 30))
            continue
        if 400 <= code < 500:
            if mode != "plain":  # прокси не принимает поле reasoning: больше его не шлём
                with LLM["lock"]:
                    LLM["dead"].add(mode)
                    if LLM["mode"] == mode:
                        LLM["mode"], LLM["lat"] = "plain", []
                nxt = lower_mode(mode)
                if nxt:
                    mode = nxt
                else:
                    mode = "plain"
                    if exhausted:
                        max_tokens = min(8000, max_tokens * 2)
                continue
            if max_tokens > LLM["floor"]:  # прокси не принимает max_tokens выше заданного платформой
                max_tokens, lowered = LLM["floor"], True
                continue
            break
        try:
            choice = response.json()["choices"][0]
            content = clean_answer(choice["message"].get("content") or "")
            finish = choice.get("finish_reason")
        except (KeyError, IndexError, ValueError, TypeError, AttributeError):
            waits += 1
            time.sleep(1.0)
            continue
        if len(content) >= 15 and finish != "length":
            with LLM["lock"]:
                LLM["ok"] += 1
                if (mode, max_tokens) == (LLM["mode"], LLM["max_tokens"]):
                    LLM["lat"].append(time.time() - began)
            if (mode, max_tokens) != start_cfg:
                remember(mode, max_tokens, forced=lowered)
            return content
        # пустой или оборванный ответ: лимит токенов ушёл на рассуждения
        if len(content) >= 15:
            partial = trim_to_sentence(content) or partial
        exhausted = True
        with LLM["lock"]:
            LLM["exhausted"][mode] = LLM["exhausted"].get(mode, 0) + 1
        nxt = lower_mode(mode)
        if nxt:
            mode = nxt
        elif max_tokens < 8000 and not lowered:
            max_tokens = min(8000, max_tokens * 2)
        else:
            break
    if not partial:
        with LLM["lock"]:
            LLM["dry"] += 1
    return partial


def main():
    settings = get_settings()
    workers = (os.environ.get("MAX_WORKERS") or "").strip()
    settings["max_workers"] = max(1, min(8, int(workers))) if workers.isdigit() else 2
    settings["reranker_model"] = ""  # реранкер: +1 запрос на поиск, на платформе балл с ним ниже (27 против 32)
    settings["embedding_timeout"], settings["embedding_attempts"] = 45.0, 2
    platform_tokens = _env_int("MAX_TOKENS", 0)
    LLM["floor"] = max(256, min(8192, platform_tokens)) if platform_tokens else 1024
    LLM["max_tokens"] = max(LLM["floor"], 3000)
    data_dir = settings["data_dir"]
    questions = load_questions(find_file(data_dir, "questions.csv"))
    kb_path = find_file(data_dir, "knowledge-base.zip", "kb.zip", "knowledge_base.zip")
    log(f"Вопросов: {len(questions)}; потоков: {settings['max_workers']}; max_tokens: {LLM['max_tokens']} "
        f"(платформа: {platform_tokens or 'не задано'})")

    index, has_dense = build_or_load_index(kb_path, settings)
    settings["embedding_timeout"] = 15.0
    log(f"Индекс готов: {len(index['chunks'])} фрагментов; векторы: {'да' if has_dense else 'нет, BM25'}")

    answers, drafts, failed, lock = {}, {}, [], threading.Lock()

    def save():
        write_submission(questions, {**drafts, **answers})

    # черновики без API: текст найденных пунктов. Файл есть с первой минуты, что бы ни случилось дальше.
    for qid, question in questions:
        try:
            text, history = parse_dialog(question)
            if detect_injection(text):
                drafts[qid] = ATTACK_ANSWER
                continue
            query = heuristic_queries(text, sanitize_history(history))[-1]
            hits = hybrid_search(index, query, None, settings)[:4]
            drafts[qid] = extractive_answer([index["chunks"][c["idx"]] for c in hits], query)
        except Exception:  # noqa: BLE001
            drafts[qid] = FALLBACK
    save()
    log("Черновой submission.csv записан")

    def watchdog():
        time.sleep(max(5, HARD_AT - time.time()))
        with lock:
            save()
        log("Время на исходе: submission.csv записан")
        os._exit(0)

    threading.Thread(target=watchdog, daemon=True).start()

    emb_state = {"fails": 0}

    def embed_query(texts):
        if emb_state["fails"] >= 3:
            raise RuntimeError("эмбеддинг вопросов отключён после 3 сбоев")
        try:
            result = embed_texts(texts, settings)
        except Exception:
            emb_state["fails"] += 1
            raise
        emb_state["fails"] = 0
        return result

    prepared = {}

    def prepare(question):
        """Контекст для вопроса: 1 запрос эмбеддинга (только для одной, самой полной формулировки)."""
        text, history = parse_dialog(question)
        history = sanitize_history(history)
        queries = heuristic_queries(text, history)
        period = extract_event_period(text) or extract_event_period(
            " ".join(t["content"] for t in history if t["role"] == "user"))
        filters = {"event_period": period} if period else {}
        context = retrieve_context(index, [queries[-1]], embed_query if has_dense else None, settings, filters)
        return text, history, period, context, queries[-1]

    def answer(qid, question):
        if detect_injection(parse_dialog(question)[0]):
            return ATTACK_ANSWER, True
        if qid not in prepared:
            prepared[qid] = prepare(question)
        text, history, period, context, query = prepared[qid]
        if not context:
            return NO_CONTEXT_ANSWER, True
        raw = llm_text(text_messages(text, history, context, period), settings, SOFT_AT)
        if not raw:
            return extractive_answer(sorted(context, key=lambda c: -c.get("score", 0.0)), query), False
        return strip_markers(raw), True

    def keep_pace():
        """Если по темпу не успеваем к SOFT_AT — просим модель рассуждать меньше (ответ короткий, время уходит на рассуждения)."""
        with LLM["lock"]:
            lat, mode, prev = list(LLM["lat"]), LLM["mode"], LLM.get("prev")
        if len(lat) < 4 or LLM.get("pace_off"):
            return
        pace = sum(lat) / len(lat)
        if prev and prev[0] != mode and pace > 0.8 * prev[1]:
            # меньше рассуждений не дало выигрыша во времени — возвращаем прежний режим: точность важнее
            with LLM["lock"]:
                if LLM["mode"] == mode:
                    LLM["mode"], LLM["lat"], LLM["pace_off"] = prev[0], [], True
                    log(f"Режим «{mode}» не быстрее ({pace:.1f} с против {prev[1]:.1f} с): возвращаю «{prev[0]}»")
            return
        remaining = len(questions) - len(answers)
        projected = time.time() + remaining * pace / settings["max_workers"]
        nxt = lower_mode(mode)
        if projected > SOFT_AT - 20 and nxt:
            with LLM["lock"]:
                if LLM["mode"] == mode:
                    LLM["mode"], LLM["lat"], LLM["prev"] = nxt, [], (mode, pace)
                    log(f"Темп {pace:.1f} с на вопрос, осталось {remaining}: режим рассуждений «{nxt}»")

    def solve(item):
        qid, question = item
        try:
            text, ok = answer(qid, question)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            text, ok = drafts.get(qid) or FALLBACK, False
        with lock:
            answers[qid] = text
            if not ok:
                failed.append((qid, question))
            n = len(answers)
            if n % 5 == 0 or n == len(questions):
                save()
        keep_pace()
        if n % 10 == 0 or n == len(questions):
            log(f"Готово {n}/{len(questions)}; без ответа модели: {len(failed)}; режим «{LLM['mode']}», "
                f"max_tokens {LLM['max_tokens']}, вызовов {LLM['calls']}")

    with ThreadPoolExecutor(max_workers=settings["max_workers"]) as pool:
        list(pool.map(solve, questions))

    # второй проход — только если модель вообще отвечала: иначе это те же отказы ещё раз
    retry = list(failed) if LLM["ok"] else []
    log(f"Второй проход: {len(retry)} вопросов")
    for qid, question in retry:
        if time.time() > SOFT_AT - 10:
            break
        time.sleep(1.0)
        try:
            text, ok = answer(qid, question)
        except Exception:  # noqa: BLE001
            continue
        if ok:
            with lock:
                answers[qid] = text
    with lock:
        save()
    log(f"Создан submission.csv: {len(questions)} ответов; от модели {LLM['ok']}; вызовов {LLM['calls']}; "
        f"{time.time() - STARTED:.0f} с")


if __name__ == "__main__":
    main()
