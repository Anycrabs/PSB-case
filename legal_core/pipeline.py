"""Конвейер ответа.

Режимы (PIPELINE_MODE):
  fast — поиск + 1 вызов LLM (генерация) + детерминированная проверка;
  auto — то же + 1 вызов LLM-проверяющего, который исправляет ответ по фрагментам (по умолчанию);
  full — auto + предварительный разбор вопроса LLM (самостоятельная формулировка, подзапросы, дата события).

Зависимости передаются словарём deps, чтобы один код работал в сервисе orchestrator, в solution.py и в тестах:
  deps["llm_json"](messages, max_tokens, default) -> dict
  deps["retrieve"](queries, filters) -> list[context item]
"""

import re
import time
import uuid

from legal_core import prompts
from legal_core.guards import detect_injection, sanitize_history
from legal_core.verify import check_answer, drop_sentences, issue_hints, strip_markers

ROLE_RE = re.compile(r"^\s*(Пользователь|Юрист|Клиент|User|Ассистент|Assistant|Бот)\s*:\s*", re.I | re.M)
MAX_ANSWER_CHARS = 9500


def parse_dialog(text):
    """«Пользователь: …\\nАссистент: …\\nПользователь: …» -> (последняя реплика пользователя, история)."""
    marks = list(ROLE_RE.finditer(text or ""))
    if not marks:
        return (text or "").strip(), []
    turns = []
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        role = "assistant" if mark.group(1).lower() in ("ассистент", "assistant", "бот") else "user"
        turns.append({"role": role, "content": text[mark.end():end].strip()})
    last_user = max((i for i, t in enumerate(turns) if t["role"] == "user"), default=len(turns) - 1)
    return turns[last_user]["content"], turns[:last_user]


ABBREVIATIONS = {
    "НДС": "налог на добавленную стоимость",
    "НДФЛ": "налог на доходы физических лиц",
    "МРОТ": "минимальный размер оплаты труда",
    "УСН": "упрощенная система налогообложения",
    "УПД": "универсальный передаточный документ",
    "ПДн": "персональные данные",
    "СЭД": "система электронного документооборота",
    "ГД": "генеральный директор",
}


def expand_abbreviations(text):
    """«ставка НДС» -> «ставка НДС (налог на добавленную стоимость)»: в текстах законов сокращений нет."""
    for short, full in ABBREVIATIONS.items():
        text = re.sub(rf"(?<![\wА-Яа-яЁё]){short}(?![\wА-Яа-яЁё])", f"{short} ({full})", text)
    return text


def heuristic_queries(question, history):
    queries = [expand_abbreviations(question)]
    if history:
        joined = " ".join(t["content"] for t in history[-4:]) + " " + question
        queries.append(expand_abbreviations(joined[-700:]))
    return queries


def response(status, answer, request_id, started, **extra):
    return {
        "request_id": request_id,
        "status": status,
        "answer_text": answer,
        "answer_marked": extra.get("answer_marked", answer),
        "sources": extra.get("sources", []),
        "missing_info": extra.get("missing_info", []),
        "verification": extra.get("verification", {"passed": True, "issues": []}),
        "trace": extra.get("trace", {}),
        "latency_ms": int((time.time() - started) * 1000),
    }


def used_sources(context, labels):
    by_label = {c["label"]: c for c in context}
    return [{
        "id": label, "chunk_id": c["chunk_id"], "doc_id": c["doc_id"], "doc_title": c.get("doc_display") or c["doc_title"],
        "citation": c["citation"], "edition": c.get("edition", ""), "quote": c["text"][:700], "char_span": c["char_span"],
    } for label in labels if (c := by_label.get(label))]


def marked_text(draft):
    """Текст ответа с метками [S#] для интерфейса: номер ссылки ставится сразу после слова, без пробела."""
    text = re.sub(r"\s+(\[\s*S\d)", r"\1", (draft or "").strip())
    return re.sub(r"[ \t]+", " ", text)[:MAX_ANSWER_CHARS + 200]


def finalize(draft, context, question_text, trace):
    """Убирает предложения, которые так и не подтвердились, и метки [S#]."""
    report = check_answer(draft, context, question_text)
    if report["issues"]:
        trimmed = drop_sentences(draft, [item["index"] for item in report["issues"]])
        trace["dropped_sentences"] = [item["sentence"] for item in report["issues"]]
        if trimmed:
            draft = trimmed
            report = check_answer(draft, context, question_text)
    return draft, report


def answer_question(question, history, deps, settings):
    started = time.time()
    request_id = uuid.uuid4().hex[:12]
    trace = {}
    if not history:
        question, history = parse_dialog(question)
    history = sanitize_history(history)
    question_text = question + " " + " ".join(t["content"] for t in history if t["role"] == "user")
    mode = settings.get("pipeline_mode", "auto")
    trace["mode"] = mode

    if detect_injection(question):
        return response("refusal", prompts.ATTACK_ANSWER, request_id, started, trace={"guard": "attack"})

    queries, event_date = heuristic_queries(question, history), None
    if mode == "full":
        t = time.time()
        analysis = deps["llm_json"](prompts.analyze_messages(question, history), 600, {"type": "legal"}) or {}
        trace["analyze_ms"] = int((time.time() - t) * 1000)
        trace["analysis"] = analysis
        if analysis.get("type") == "offtopic":
            return response("refusal", prompts.OFFTOPIC_ANSWER, request_id, started, trace=trace)
        if analysis.get("type") == "attack":
            return response("refusal", prompts.ATTACK_ANSWER, request_id, started, trace=trace)
        extra = [analysis.get("standalone_question"), *(analysis.get("subquestions") or [])]
        queries = list(dict.fromkeys(q for q in [*queries, *extra] if isinstance(q, str) and q.strip()))[:5]
        event_date = analysis.get("event_date")

    t = time.time()
    context = deps["retrieve"](queries, {})
    trace["retrieve_ms"] = int((time.time() - t) * 1000)
    trace["queries"] = queries
    trace["context"] = [{"label": c["label"], "chunk_id": c["chunk_id"], "citation": c["citation"],
                         "doc": (c.get("doc_display") or c["doc_title"])[:80], "score": round(c["score"], 4)}
                        for c in context]
    if not context:
        return response("refusal", prompts.NO_CONTEXT_ANSWER, request_id, started, trace=trace)

    t = time.time()
    fallback = {"status": "refusal", "answer": prompts.NO_CONTEXT_ANSWER, "missing_info": []}
    generation = deps["llm_json"](prompts.generate_messages(question, history, context, event_date),
                                  settings.get("llm_max_tokens", 3000), fallback) or fallback
    trace["generate_ms"] = int((time.time() - t) * 1000)
    draft = str(generation.get("answer") or "").strip() or prompts.NO_CONTEXT_ANSWER
    status = str(generation.get("status") or "answer")
    report = check_answer(draft, context, question_text)
    trace["draft_issues"] = issue_hints(report)

    if mode in ("auto", "full"):
        t = time.time()
        review = deps["llm_json"](prompts.review_messages(question, history, context, draft, issue_hints(report)),
                                  settings.get("llm_max_tokens", 3000), {"verdict": "ok"}) or {}
        trace["review_ms"] = int((time.time() - t) * 1000)
        trace["review"] = {"verdict": review.get("verdict"), "problems": review.get("problems")}
        candidate = str(review.get("answer") or "").strip()
        if candidate and review.get("verdict") == "fixed":
            candidate_report = check_answer(candidate, context, question_text)
            if len(candidate_report["issues"]) <= len(report["issues"]):
                draft, report = candidate, candidate_report
                status = str(review.get("status") or status)

    draft, report = finalize(draft, context, question_text, trace)
    if status not in ("answer", "partial", "refusal", "clarification"):
        status = "answer"
    text = strip_markers(draft)[:MAX_ANSWER_CHARS] or prompts.NO_CONTEXT_ANSWER
    return response(
        status, text, request_id, started,
        answer_marked=marked_text(draft) if text != prompts.NO_CONTEXT_ANSWER else text,
        sources=used_sources(context, report["labels"]),
        missing_info=[str(m) for m in generation.get("missing_info") or [] if str(m).strip()],
        verification={"passed": not report["issues"], "issues": issue_hints(report)},
        trace=trace,
    )
