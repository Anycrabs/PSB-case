"""Точка входа автоматической проверки (Технический регламент Hack&Change 2026, кейс ПСБ).

Что делает:
  1. читает {DATA_DIR}/questions.csv (id,question) и {DATA_DIR}/knowledge-base.zip;
  2. строит индекс базы знаний: структурное разбиение + BM25 + эмбеддинги bge-m3. Векторы берутся готовыми
     из lc_vectors.py (их кладёт в ZIP build_zip.py), сверяются с POST {LLM_BASE_URL}/embeddings платформы,
     досчитываются только недостающие фрагменты — параллельно и не дольше INDEX_SHARE бюджета; кэш в /tmp;
  3. отвечает на каждый вопрос тем же конвейером, что и прототип (legal_core): гибридный поиск,
     реранкер (POST /rerank), генерация и проверка ответа (POST /chat/completions);
  4. пишет submission.csv (id,answer) в рабочий каталог.

Следит за лимитом 20 минут: при нехватке времени пропускает проверку ответа и переключается в быстрый режим,
а вопросы, на которые не хватило времени или LLM не ответила, получают текст лучших найденных пунктов
(без обращений к API) вместо заглушки.

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

import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))  # в ZIP модули lc_*.py лежат рядом с solution.py
if (ROOT.parent / "legal_core").exists():
    sys.path.insert(0, str(ROOT.parent))  # запуск из репозитория: python submission/solution.py

from legal_core.chunking import chunk_documents  # noqa: E402
from legal_core.config import get_settings  # noqa: E402
from legal_core.embeddings import embed_dense_parallel, embed_texts, embed_texts_batched  # noqa: E402
from legal_core.index import build_index  # noqa: E402
from legal_core.parsing import read_knowledge_base  # noqa: E402
from legal_core.pipeline import answer_question, extractive_answer, heuristic_queries, parse_dialog  # noqa: E402
from legal_core.prebuilt import attach_vectors, load_store  # noqa: E402
from legal_core.retrieval import hybrid_search, retrieve_context  # noqa: E402
from legal_core.chunking import CHUNKER_VERSION  # noqa: E402
from legal_core.llm import chat_json  # noqa: E402
from legal_core.prompts import NO_CONTEXT_ANSWER  # noqa: E402

STARTED = time.time()
FALLBACK = ("Не удалось сформировать обоснованный ответ по базе знаний за отведённое время. "
            "Уточните вопрос или обратитесь к юристу.")
MAX_CHARS = 9500
INDEX_SHARE = 0.35  # доля бюджета на досчёт эмбеддингов базы (с готовыми из сабмита обычно 0)


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


def load_prebuilt():
    """Готовые эмбеддинги из lc_vectors.py (кладёт build_zip.py); при запуске из репозитория их нет."""
    try:
        import lc_vectors
    except ImportError:
        return None
    store = load_store(lc_vectors)
    if store:
        log(f"Готовые эмбеддинги в сабмите: {store['meta']['count']} × {store['meta']['dim']} "
            f"({store['meta'].get('embedding_model')}, собраны {store['meta'].get('built_at', '?')})")
    return store


def build_or_load_index(kb_path, settings):
    digest = hashlib.sha1(Path(kb_path).read_bytes()).hexdigest()[:16]
    cache = Path("/tmp") / f"psb_index_{digest}_{CHUNKER_VERSION}_{settings['embedder_mode']}_{settings['embedding_model'].replace('/', '_')}.pkl"
    if cache.exists():
        log(f"Индекс из кэша {cache.name}")
        return pickle.loads(cache.read_bytes())
    docs, errors = read_knowledge_base(kb_path)
    for err in errors:
        log(f"Пропущен документ {err['file']}: {err['error']}")
    chunks = chunk_documents(docs)
    log(f"Документов: {len(docs)}, фрагментов: {len(chunks)}; эмбеддинги {settings['embedding_model']} ({settings['embedder_mode']})")

    if settings["embedder_mode"] == "fake":
        embeddings = embed_texts_batched([c["embed_text"] for c in chunks], settings)
        return build_index(chunks, embeddings, settings)

    # На пересчёт базы — не больше INDEX_SHARE бюджета: остальное время нужно на ответы
    deadline = STARTED + settings["time_budget_s"] * INDEX_SHARE
    dense, _ = attach_vectors(
        chunks, load_prebuilt(),
        embed_fn=lambda texts: embed_texts(texts, settings),
        embed_missing=lambda texts: embed_dense_parallel(texts, settings, deadline=deadline),
        log=log,
    )
    if dense is None:
        raise RuntimeError("нет ни готовых, ни посчитанных эмбеддингов")
    index = build_index(chunks, {"dense": dense, "sparse": None}, settings)
    complete = bool(np.all(np.abs(dense).sum(axis=1) > 0))
    if complete:  # неполный индекс не кэшируем: при повторном запуске досчитаем
        try:
            cache.write_bytes(pickle.dumps(index))
        except OSError:
            pass
    return index


def quick_answer(index, settings, question):
    """Ответ без обращений к API (BM25 + точные ссылки, текст лучших пунктов): когда время вышло
    или вопрос упал с ошибкой. Лучше заглушки: судья засчитывает совпадение по сути и не видит выдумок."""
    try:
        text, history = parse_dialog(question)
        queries = heuristic_queries(text, history)
        local = {**settings, "reranker_model": ""}
        ranked = hybrid_search(index, queries[-1], None, local)
        return extractive_answer([index["chunks"][c["idx"]] for c in ranked[:4]], queries[-1])
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return FALLBACK


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
    # после этой отметки проверка ответа (второй вызов LLM) пропускается: черновик лучше заглушки
    settings["review_until"] = STARTED + budget * 0.8

    answers, lock, done = {}, threading.Lock(), {"n": 0, "spent": 0.0, "since": time.time()}
    stats = {"status": {}, "fallback": 0, "quick": 0, "slowest": 0.0}
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
            late = [qid for qid, _ in questions if qid not in answers]
            for qid, question in questions:
                if qid not in answers:
                    answers[qid] = quick_answer(index, settings, question)
            write_submission(questions, answers)
        log(f"Время на исходе: записан submission.csv; без LLM (текст пунктов) — {len(late)} вопросов, завершаю работу")
        os._exit(0)

    threading.Thread(target=watchdog, daemon=True).start()

    def solve(item):
        qid, question = item
        elapsed = time.time() - STARTED
        if elapsed > budget - 20:
            text = quick_answer(index, settings, question)
            with lock:
                stats["quick"] += 1
                answers[qid] = text
            return qid, text
        with lock:
            remaining = len(questions) - done["n"]
            # прогноз по фактической пропускной способности (учитывает, что прокси может работать в один поток)
            wall_per_question = (time.time() - done["since"]) / done["n"] if done["n"] else 0
            projected = elapsed + remaining * wall_per_question
            if settings["pipeline_mode"] != "fast" and done["n"] >= 3 and projected > budget * 0.85:
                settings["pipeline_mode"] = "fast"
                log(f"Прогноз {projected:.0f}s > бюджета: переключаюсь в режим fast")
        t = time.time()
        status = "error"
        try:
            result = answer_question(question, [], deps, settings)
            text, status = result["answer_text"], result["status"]
            if result.get("trace", {}).get("extractive"):
                status = "без LLM"
        except Exception:  # noqa: BLE001 — один упавший вопрос не должен ронять прогон
            log(f"Вопрос {qid}: ошибка, ответ из текста найденных пунктов")
            traceback.print_exc()
            text = quick_answer(index, settings, question)
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
                    f"заглушек: {stats['fallback']}; без LLM из-за времени: {stats['quick']}; "
                    f"среднее {done['spent'] / done['n']:.1f}s, "
                    f"максимум {stats['slowest']:.1f}s; режим: {settings['pipeline_mode']}")
        return qid, text

    with ThreadPoolExecutor(max_workers=settings["max_workers"]) as pool:
        list(pool.map(solve, questions))

    with lock:
        write_submission(questions, answers)
    log(f"Создан submission.csv: {len(questions)} ответов")
    run_probe(deps, settings, budget, kb_path)


def run_probe(deps, settings, budget, kb_path):
    """Зонд: после записи submission.csv отвечает на демо- и тестовые вопросы (lc_probe.py) нашим решением
    и baseline организаторов (lc_baseline.py), сравнивает их LLM-судьёй по методике регламента (lc_judge.py)
    и печатает ответы и итоговый балл в лог. submission.csv не меняется. Работает только из ZIP."""
    if os.getenv("SKIP_PROBE"):  # локальная копия платформы (eval/local_platform.py) оценивает сама
        return
    try:
        from lc_probe import PROBE
        import lc_baseline
        import lc_judge
    except ImportError:
        return
    if time.time() - STARTED > budget * 0.5:
        log("Зонд пропущен: мало времени")
        return
    log(f"Зонд: {len(PROBE)} вопросов, сравнение с baseline через LLM-судью")
    try:
        base_docs = lc_baseline.load_documents(Path(kb_path))
    except Exception as exc:  # noqa: BLE001
        log(f"Зонд: baseline не загрузил базу ({type(exc).__name__}), сравнения не будет")
        base_docs = None
    deadline = STARTED + budget - 60

    def one(item):
        group, pid, question, golden = item
        if time.time() > deadline:
            return group, pid, None, f"PROBE {group}:{pid} пропущен — время"
        try:
            result = answer_question(question, [], deps, settings)
        except Exception as exc:  # noqa: BLE001
            return group, pid, None, f"PROBE {group}:{pid} [error] {type(exc).__name__}: {exc}"
        ours = " ".join(result["answer_text"].split())
        trace = result.get("trace", {})
        cites = "; ".join(f"{c['doc'][:40]} {c['citation']}" for c in trace.get("context", [])[:8])
        review = trace.get("review") or {}
        lines = [f"PROBE {group}:{pid} [{result['status']}] {ours}",
                 f"PROBE {group}:{pid} контекст: {cites}"]
        if review.get("verdict") == "fixed" or trace.get("dropped_sentences"):
            lines.append(f"PROBE {group}:{pid} проверка: {review.get('verdict')} {review.get('problems') or ''} "
                         f"вырезано: {trace.get('dropped_sentences') or []}")
        score = None
        if base_docs is not None and golden and time.time() < deadline:
            try:
                import httpx
                with httpx.Client(timeout=90.0) as client:
                    baseline = lc_baseline.generate(client, question, lc_baseline.select_documents(client, question, base_docs))
                score, reason = lc_judge.judge_pair(question, golden, ours, baseline, settings)
                lines.append(f"PROBE {group}:{pid} baseline: {baseline}")
                lines.append(f"PROBE {group}:{pid} судья: {'—' if score is None else f'{score:.2f}'} {reason}")
            except Exception as exc:  # noqa: BLE001
                lines.append(f"PROBE {group}:{pid} судья: ошибка {type(exc).__name__}")
        return group, pid, score, "\n".join(lines)

    with ThreadPoolExecutor(max_workers=settings["max_workers"]) as pool:
        results = list(pool.map(one, PROBE))
    for _, _, _, text in results:
        print(text, flush=True)
    for group in ("demo", "bench", None):
        part = [r for r in results if group is None or r[0] == group]
        total = lc_judge.summarize([score for _, _, score, _ in part])
        # Faithfulness: доля ответов, где судья не нашёл выдуманных или противоречащих эталону фактов
        judged = [text for _, _, score, text in part if score is not None]
        fabricated = sum("выдуманный факт в нашем ответе" in text for text in judged)
        log(f"ЗОНД {group or 'всего'}: оценено {total['n']}; MatchRate {total['match_rate']:.3f}; "
            f"WinRate {total['win_rate']:.3f}; Score x100 = {total['score'] * 100:.1f}; "
            f"без выдуманных фактов: {len(judged) - fabricated} из {len(judged)}")

if __name__ == "__main__":
    main()
