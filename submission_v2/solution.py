"""Сабмит v2 (кейс ПСБ, Hack&Change 2026): тот же поиск и та же база, вдвое меньше обращений к API.

Почему переделано. Платформа даёт только итоговый балл, без логов. По сабмитам видно одно: чем больше
запросов к прокси платформы, тем ниже балл (база 32; +реранкер 27; +потоки и параллельные эмбеддинги 21.7).
Значит, прокси ограничивает нагрузку, и часть наших вызовов не доходит до модели.

Что делает на вопрос — 2 запроса, как у примера организаторов (у них 3):
  1. POST /embeddings — один вектор вопроса (база векторизуется один раз при старте);
  2. поиск локально: BM25 + векторы + точные ссылки на статьи и пункты, сборка контекста с редакцией по дате;
  3. POST /chat/completions — один ответ простым текстом (без JSON и без второго вызова на проверку).
Потоков — ровно MAX_WORKERS платформы (по умолчанию 2, как в примере). Вопросы, где модель не ответила,
переспрашиваются вторым проходом в конце, по одному; заглушек нет: в крайнем случае — текст нужного пункта.
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

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from lc_chunking import chunk_documents  # noqa: E402
from lc_config import get_settings  # noqa: E402
from lc_dates import extract_event_period  # noqa: E402
from lc_embeddings import embed_texts, embed_texts_batched  # noqa: E402
from lc_guards import detect_injection, sanitize_history  # noqa: E402
from lc_index import build_index  # noqa: E402
from lc_parsing import read_knowledge_base  # noqa: E402
from lc_pipeline import extractive_answer, heuristic_queries, parse_dialog  # noqa: E402
from lc_prompts import ATTACK_ANSWER, NO_CONTEXT_ANSWER, text_messages  # noqa: E402
from lc_retrieval import hybrid_search, retrieve_context  # noqa: E402
from lc_verify import add_missing_caveats, strip_markers  # noqa: E402

STARTED = time.time()
FALLBACK = NO_CONTEXT_ANSWER
MAX_CHARS = 9500
RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


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


def build_or_load_index(kb_path, settings):
    digest = hashlib.sha1(Path(kb_path).read_bytes()).hexdigest()[:16]
    cache = Path("/tmp") / f"psb_v2_{digest}_{settings['embedder_mode']}_{settings['embedding_model'].replace('/', '_')}.pkl"
    if cache.exists():
        return pickle.loads(cache.read_bytes())
    docs, errors = read_knowledge_base(kb_path)
    for err in errors:
        log(f"Пропущен документ {err['file']}: {err['error']}")
    chunks = chunk_documents(docs)
    log(f"Документов: {len(docs)}, фрагментов: {len(chunks)}")
    # последовательно, пачками: на старте ~10 запросов, без всплеска параллельной нагрузки
    index = build_index(chunks, embed_texts_batched([c["embed_text"] for c in chunks], settings), settings)
    try:
        cache.write_bytes(pickle.dumps(index))
    except OSError:
        pass
    return index


def llm_text(messages, settings, deadline):
    """Один вызов модели простым текстом. 429/5xx — пауза (учитывая Retry-After) и до 3 попыток; без JSON."""
    url = f"{settings['llm_base_url']}/chat/completions"
    headers = {"Authorization": f"Bearer {settings['llm_api_key']}"} if settings["llm_api_key"] else {}
    body = {"model": settings["llm_model"], "messages": messages,
            "temperature": settings["llm_temperature"], "max_tokens": settings["llm_max_tokens"]}
    for attempt in range(3):
        if time.time() > deadline:
            return ""
        try:
            response = httpx.post(url, json=body, headers=headers, timeout=90.0)
            if response.status_code in RETRY_STATUS:
                wait = response.headers.get("retry-after", "")
                time.sleep(min(float(wait) if wait.replace(".", "", 1).isdigit() else 3.0 * (attempt + 1) ** 2, 30))
                continue
            if response.status_code in (400, 413, 422) and body["max_tokens"] > 1024:
                body["max_tokens"] = 1024  # прокси может не принимать большой max_tokens
                continue
            response.raise_for_status()
            content = response.json()["choices"][0]["message"].get("content") or ""
            content = re.sub(r"<think>.*?(</think>|$)", "", content, flags=re.S)
            content = re.sub(r"\*\*|__|^#+\s*", "", content, flags=re.M)
            content = " ".join(content.split())
            if len(content) >= 15:
                return content
        except (httpx.HTTPError, KeyError, IndexError, ValueError):
            time.sleep(2.0 * (attempt + 1))
    return ""


def main():
    settings = get_settings()
    # ровно столько потоков, сколько разрешает платформа (как в примере организаторов: 1–8, по умолчанию 2)
    workers = (os.environ.get("MAX_WORKERS") or "").strip()
    settings["max_workers"] = max(1, min(8, int(workers))) if workers.isdigit() else 2
    settings["reranker_model"] = ""  # реранкер: +1 запрос на поиск, на платформе балл с ним ниже (27 против 32)
    budget = settings["time_budget_s"]
    data_dir = settings["data_dir"]
    questions = load_questions(find_file(data_dir, "questions.csv"))
    kb_path = find_file(data_dir, "knowledge-base.zip", "kb.zip", "knowledge_base.zip")
    log(f"Вопросов: {len(questions)}; потоков: {settings['max_workers']}; max_tokens: {settings['llm_max_tokens']}")

    try:
        index = build_or_load_index(kb_path, settings)
    except Exception as exc:  # noqa: BLE001 — без эмбеддингов работаем на лексическом поиске
        log(f"Эмбеддинги базы недоступны ({type(exc).__name__}); поиск по BM25")
        settings["embedder_mode"] = "fake"
        index = build_or_load_index(kb_path, settings)
    log(f"Индекс готов: {len(index['chunks'])} фрагментов")

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
        context = retrieve_context(index, [queries[-1]], embed_query, settings, filters)
        return text, history, period, context, queries[-1]

    def answer(qid, question, deadline):
        if detect_injection(parse_dialog(question)[0]):
            return ATTACK_ANSWER, True
        if qid not in prepared:
            prepared[qid] = prepare(question)
        text, history, period, context, query = prepared[qid]
        if not context:
            return NO_CONTEXT_ANSWER, True
        raw = llm_text(text_messages(text, history, context, period), settings, deadline)
        if not raw:
            return extractive_answer(sorted(context, key=lambda c: -c.get("score", 0.0)), query), False
        if not raw.startswith("В базе знаний н"):
            raw = add_missing_caveats(raw, context)
        return strip_markers(raw), True

    answers, failed, lock = {}, [], threading.Lock()

    def watchdog():
        time.sleep(max(5, budget + 60 - (time.time() - STARTED)))
        with lock:
            for qid, question in questions:
                if qid not in answers:
                    try:
                        answers[qid] = extractive_answer([index["chunks"][c["idx"]] for c in hybrid_search(
                            index, question, None, settings)[:4]], question)
                    except Exception:  # noqa: BLE001
                        answers[qid] = FALLBACK
            write_submission(questions, answers)
        log("Время на исходе: submission.csv записан")
        os._exit(0)

    threading.Thread(target=watchdog, daemon=True).start()

    def solve(item):
        qid, question = item
        try:
            text, ok = answer(qid, question, STARTED + budget - 30)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            text, ok = FALLBACK, False
        with lock:
            answers[qid] = text
            if not ok:
                failed.append((qid, question))
            n = len(answers)
        if n % 10 == 0 or n == len(questions):
            log(f"Готово {n}/{len(questions)}; без ответа модели: {len(failed)}")

    with ThreadPoolExecutor(max_workers=settings["max_workers"]) as pool:
        list(pool.map(solve, questions))

    # второй проход: переспрашиваем по одному, пока есть время (прокси мог отказать из-за нагрузки)
    retry = list(failed)
    log(f"Второй проход: {len(retry)} вопросов")
    for qid, question in retry:
        if time.time() > STARTED + budget - 40:
            break
        time.sleep(1.0)
        try:
            text, ok = answer(qid, question, STARTED + budget - 30)
        except Exception:  # noqa: BLE001
            continue
        if ok:
            with lock:
                answers[qid] = text
    with lock:
        write_submission(questions, answers)
    log(f"Создан submission.csv: {len(questions)} ответов; {time.time() - STARTED:.0f} с")


if __name__ == "__main__":
    main()
