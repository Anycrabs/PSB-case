"""Точка входа автоматической проверки (Технический регламент Hack&Change 2026, кейс ПСБ).

Что делает:
  1. читает {DATA_DIR}/questions.csv (id,question) и {DATA_DIR}/knowledge-base.zip;
  2. строит индекс базы знаний при запуске: структурное разбиение + эмбеддинги bge-m3 через прокси платформы
     (POST {LLM_BASE_URL}/embeddings) + BM25; кэш в /tmp;
  3. отвечает на каждый вопрос тем же конвейером, что и прототип (legal_core): гибридный поиск,
     реранкер (POST /rerank), генерация и проверка ответа (POST /chat/completions);
  4. пишет submission.csv (id,answer) в рабочий каталог.

Следит за лимитом 20 минут: при нехватке времени переключается в быстрый режим, а перед тайм-аутом
записывает файл с тем, что успело посчитаться (пустых ответов не бывает).

В ZIP модули legal_core лежат в корне как lc_*.py — их кладёт туда submission/build_zip.py.
"""

import csv
import hashlib
import os
import pickle
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))  # в ZIP модули lc_*.py лежат рядом с solution.py
if (ROOT.parent / "legal_core").exists():
    sys.path.insert(0, str(ROOT.parent))  # запуск из репозитория: python submission/solution.py

from legal_core.chunking import chunk_documents  # noqa: E402
from legal_core.config import get_settings  # noqa: E402
from legal_core.embeddings import embed_texts, embed_texts_batched  # noqa: E402
from legal_core.index import build_index  # noqa: E402
from legal_core.parsing import read_knowledge_base  # noqa: E402
from legal_core.pipeline import answer_question  # noqa: E402
from legal_core.retrieval import retrieve_context  # noqa: E402
from legal_core.llm import chat_json  # noqa: E402
from legal_core.prompts import NO_CONTEXT_ANSWER  # noqa: E402

STARTED = time.time()
FALLBACK = ("Не удалось сформировать обоснованный ответ по базе знаний за отведённое время. "
            "Уточните вопрос или обратитесь к юристу.")
MAX_CHARS = 9500


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
    cache = Path("/tmp") / f"psb_index_{digest}_{settings['embedder_mode']}_{settings['embedding_model'].replace('/', '_')}.pkl"
    if cache.exists():
        log(f"Индекс из кэша {cache.name}")
        return pickle.loads(cache.read_bytes())
    docs, errors = read_knowledge_base(kb_path)
    for err in errors:
        log(f"Пропущен документ {err['file']}: {err['error']}")
    chunks = chunk_documents(docs)
    log(f"Документов: {len(docs)}, фрагментов: {len(chunks)}; эмбеддинги {settings['embedding_model']} ({settings['embedder_mode']})")
    embeddings = embed_texts_batched([c["embed_text"] for c in chunks], settings)
    index = build_index(chunks, embeddings, settings)
    try:
        cache.write_bytes(pickle.dumps(index))
    except OSError:
        pass
    return index


def main():
    settings = get_settings()
    settings["max_workers"] = max(1, min(16, settings["max_workers"]))
    budget = settings["time_budget_s"]
    data_dir = settings["data_dir"]

    questions = load_questions(find_file(data_dir, "questions.csv"))
    kb_path = find_file(data_dir, "knowledge-base.zip", "kb.zip", "knowledge_base.zip")
    log(f"Вопросов: {len(questions)}; база: {kb_path.name}; модель: {settings['llm_model']}; "
        f"потоков: {settings['max_workers']}; режим: {settings['pipeline_mode']}")

    try:
        index = build_or_load_index(kb_path, settings)
    except Exception as exc:  # noqa: BLE001 — без эмбеддингов работаем на лексическом поиске
        log(f"Эмбеддинги недоступны ({type(exc).__name__}: {exc}); индекс без dense-векторов")
        settings["embedder_mode"] = "fake"
        index = build_or_load_index(kb_path, settings)
    log(f"Индекс готов: {len(index['chunks'])} фрагментов")

    answers, lock, done = {}, threading.Lock(), {"n": 0, "spent": 0.0}
    stats = {"status": {}, "fallback": 0, "slowest": 0.0}
    emb_state = {"fails": 0}

    def embed_query(texts):
        # после 3 сбоев подряд перестаём ждать эмбеддер: каждый сбой — до 15 с повторов
        if emb_state["fails"] >= 3:
            raise RuntimeError("эмбеддинги отключены после повторных ошибок")
        try:
            result = embed_texts(texts, settings)
        except Exception:
            with lock:
                emb_state["fails"] += 1
                if emb_state["fails"] == 3:
                    log("Эмбеддер 3 раза подряд не ответил: дальше поиск только по BM25")
            raise
        emb_state["fails"] = 0
        return result

    deps = {
        "llm_json": lambda messages, max_tokens, default: chat_json(messages, settings, max_tokens, default),
        "retrieve": lambda queries, filters: retrieve_context(index, queries, embed_query, settings, filters),
    }

    def watchdog():
        time.sleep(max(5, budget + 60 - (time.time() - STARTED)))
        with lock:
            write_submission(questions, answers)
        log("Время на исходе: записан submission.csv с готовыми ответами, завершаю работу")
        os._exit(0)

    threading.Thread(target=watchdog, daemon=True).start()

    def solve(item):
        qid, question = item
        elapsed = time.time() - STARTED
        if elapsed > budget - 20:
            return qid, FALLBACK
        with lock:
            remaining = len(questions) - done["n"]
            per_question = done["spent"] / done["n"] if done["n"] else 0
            projected = elapsed + remaining * per_question / settings["max_workers"]
            if settings["pipeline_mode"] != "fast" and done["n"] >= 3 and projected > budget * 0.85:
                settings["pipeline_mode"] = "fast"
                log(f"Прогноз {projected:.0f}s > бюджета: переключаюсь в режим fast")
        t = time.time()
        status = "error"
        try:
            result = answer_question(question, [], deps, settings)
            text, status = result["answer_text"], result["status"]
        except Exception:  # noqa: BLE001 — один упавший вопрос не должен ронять прогон
            log(f"Вопрос {qid}: ошибка")
            traceback.print_exc()
            text = FALLBACK
        with lock:
            spent = time.time() - t
            stats["status"][status] = stats["status"].get(status, 0) + 1
            stats["fallback"] += text in (FALLBACK, NO_CONTEXT_ANSWER)
            stats["slowest"] = max(stats["slowest"], spent)
            answers[qid] = text
            done["n"] += 1
            done["spent"] += spent
            if done["n"] % 10 == 0 or done["n"] == len(questions):
                log(f"Готово {done['n']}/{len(questions)}; статусы: {stats['status']}; "
                    f"заглушек: {stats['fallback']}; среднее {done['spent'] / done['n']:.1f}s, "
                    f"максимум {stats['slowest']:.1f}s; режим: {settings['pipeline_mode']}")
        return qid, text

    with ThreadPoolExecutor(max_workers=settings["max_workers"]) as pool:
        list(pool.map(solve, questions))

    with lock:
        write_submission(questions, answers)
    log(f"Создан submission.csv: {len(questions)} ответов")


if __name__ == "__main__":
    main()
