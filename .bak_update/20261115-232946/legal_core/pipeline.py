"""Конвейер ответа.

Режимы (PIPELINE_MODE):
  fast — поиск + 1 вызов LLM (генерация) + детерминированная проверка;
  auto — то же + 1 вызов LLM-проверяющего, который исправляет ответ по фрагментам (по умолчанию);
  full — auto + предварительный разбор вопроса LLM (самостоятельная формулировка, подзапросы, дата события).

Зависимости передаются словарём deps, чтобы один код работал в сервисе orchestrator, в solution.py и в тестах:
  deps["llm_json"](messages, max_tokens, default) -> dict
  deps["retrieve"](queries, filters) -> list[context item]
  deps["on_stage"](stage) — необязательно: сообщает этап (analyze, retrieve, generate, review, verify),
                            нужен для потокового ответа /chat/stream
  deps["request_id"] — необязательно: id запроса из шлюза, чтобы он совпадал во всех логах
"""

import re
import time
import uuid

from legal_core import prompts
from legal_core.guards import detect_injection, sanitize_history
from legal_core.dates import applicability, extract_event_period
from legal_core.verify import add_missing_caveats, caveat_hints, check_answer, drop_sentences, issue_hints, strip_markers

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


QUESTION_STOP = {"какой", "какая", "какие", "каким", "можно", "нужно", "должен", "если", "договор", "договору",
                 "договора", "этого", "сколько", "почему", "через", "после", "наша", "наши", "нашему"}


def _stems(text):
    words = re.findall(r"[а-яёa-z0-9]+", (text or "").lower())
    return {w[:5] for w in words if len(w) >= 4 and w not in QUESTION_STOP}


def excerpt(text, question="", limit=420, ignore=""):
    """Строки фрагмента, больше всего совпадающие с вопросом (в исходном порядке), до limit символов.
    ignore — название документа: слова из него («Северный Склад», «поставки») есть во всех его строках
    и не помогают выбрать нужный пункт."""
    lines = [" ".join(line.split()) for line in (text or "").split("\n") if len(line.strip()) > 25]
    if not lines:
        return " ".join((text or "").split())[:limit], 0
    wanted = _stems(question) - _stems(ignore)
    scored = sorted(range(len(lines)), key=lambda i: (-len(wanted & _stems(lines[i])), i))
    best = len(wanted & _stems(lines[scored[0]]))
    picked, used = [], 0
    for i in scored:
        if picked and (len(wanted & _stems(lines[i])) == 0 or used + len(lines[i]) > limit):
            continue
        picked.append(i)
        used += len(lines[i])
        if used >= limit * 0.6 or len(picked) >= 3:
            break
    flat = " ".join(lines[i] for i in sorted(picked))
    if len(flat) > limit:
        cut = flat.rfind(". ", 120, limit)
        flat = flat[:cut + 1] if cut != -1 else flat[:limit].rsplit(" ", 1)[0] + "…"
    return flat, best


def extractive_answer(chunks, question="", limit=2):
    """Ответ без LLM: дословные строки лучших найденных пунктов с названием документа и пунктом.
    Нужен, когда модель не ответила или не хватило времени: заглушка «не удалось ответить» получает 0,
    а точный текст нужного пункта — частичное совпадение с эталоном и ничего не выдумывает.
    Если найденные документы совпадают с вопросом не больше чем одним словом — честно говорим, что ответа нет."""
    scored, seen, top = [], set(), 0
    for rank, chunk in enumerate(chunks):
        key = (chunk.get("doc_id"), chunk.get("citation"))
        if key not in seen:
            seen.add(key)
            title = f"{chunk.get('doc_display') or ''} {chunk.get('doc_title') or ''}"
            text, score = excerpt(chunk.get("text", ""), question, ignore=title)
            scored.append((-score, rank, chunk, text, score))
            top = max(top, len(_stems(question) & _stems(title + " " + chunk.get("text", ""))))
    scored.sort(key=lambda item: item[:2])  # лучшее совпадение строк с вопросом, при равенстве — ранг поиска
    parts = []
    for _, _, chunk, text, score in scored:
        if parts and score < 2:
            continue
        doc = chunk.get("doc_display") or chunk.get("doc_title") or "документ"
        parts.append(f"{doc}, {chunk.get('citation', '')}: {text}")
        if len(parts) >= limit:
            break
    if not parts or (question and top <= 1 and len(_stems(question)) >= 3):
        return prompts.NO_CONTEXT_ANSWER
    return "По базе знаний: " + " ".join(p if p.endswith((".", "…")) else p + "." for p in parts)


def edition_hints(labels, context, period):
    """Подсказка проверяющему: ответ опирается на редакцию, которая не действовала на дату события."""
    if not period:
        return []
    by_label = {c["label"]: c for c in context}
    used = [by_label[l] for l in labels if l in by_label]
    wrong = [c for c in used if applicability(c, period) == "no"]
    right = [c for c in context if applicability(c, period) == "yes"]
    if not wrong or any(applicability(c, period) == "yes" for c in used):
        return []
    hint = (f"ответ опирается на {wrong[0].get('doc_display') or wrong[0]['doc_title']} [{wrong[0]['label']}], "
            f"а она не действовала на дату события ({period['label']})")
    if right:
        hint += f"; применима {right[0].get('doc_display') or right[0]['doc_title']} [{right[0]['label']}]"
    return [hint]


def finalize(draft, context, question_text, trace):
    """Убирает предложения, которые так и не подтвердились, и метки [S#]."""
    if not draft.lstrip().startswith("В базе знаний н"):
        draft = add_missing_caveats(draft, context)
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
    request_id = deps.get("request_id") or uuid.uuid4().hex[:12]
    stage = deps.get("on_stage") or (lambda _name: None)
    trace = {}
    if not history:
        question, history = parse_dialog(question)
    history = sanitize_history(history)
    question_text = question + " " + " ".join(t["content"] for t in history if t["role"] == "user")
    mode = settings.get("pipeline_mode", "auto")
    trace["mode"] = mode

    if detect_injection(question):
        return response("refusal", prompts.ATTACK_ANSWER, request_id, started, trace={"guard": "attack"})

    queries = heuristic_queries(question, history)
    # дата события (для выбора редакции) — сначала из самой реплики, потом из прошлых реплик пользователя
    period = extract_event_period(question) or extract_event_period(
        " ".join(t["content"] for t in history if t["role"] == "user"))
    if mode == "full":
        stage("analyze")
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
        event_date = str(analysis.get("event_date") or "")
        if not period and re.fullmatch(r"\d{4}-\d{2}-\d{2}", event_date):
            period = {"start": event_date, "end": event_date, "label": event_date}

    stage("retrieve")
    t = time.time()
    trace["event_period"] = period
    context = deps["retrieve"](queries, {"event_period": period} if period else {})
    trace["retrieve_ms"] = int((time.time() - t) * 1000)
    trace["queries"] = queries
    trace["context"] = [{"label": c["label"], "chunk_id": c["chunk_id"], "citation": c["citation"],
                         "doc": (c.get("doc_display") or c["doc_title"])[:80], "score": round(c["score"], 4)}
                        for c in context]
    if not context:
        return response("refusal", prompts.NO_CONTEXT_ANSWER, request_id, started, trace=trace)

    stage("generate")
    t = time.time()
    fallback = {"status": "refusal", "answer": prompts.NO_CONTEXT_ANSWER, "missing_info": []}
    try:
        generation = deps["llm_json"](prompts.generate_messages(question, history, context, period),
                                      settings.get("llm_max_tokens", 3000), fallback) or fallback
    except Exception as exc:  # noqa: BLE001 — LLM недоступна: отвечаем текстом найденных пунктов
        print(f"Генерация не удалась ({type(exc).__name__}: {str(exc)[:120]}): ответ из текста фрагментов", flush=True)
        generation = fallback
    trace["generate_ms"] = int((time.time() - t) * 1000)
    if generation is fallback:  # модель не ответила или ответ не разобран — не превращаем это в отказ
        best = sorted(context, key=lambda c: -c.get("score", 0.0))
        text = extractive_answer(best, question)
        trace["extractive"] = True
        return response("partial", text, request_id, started, trace=trace,
                        sources=used_sources(context, [c["label"] for c in best[:2]]))
    draft = str(generation.get("answer") or "").strip() or prompts.NO_CONTEXT_ANSWER
    status = str(generation.get("status") or "answer")
    report = check_answer(draft, context, question_text)
    hints = issue_hints(report) + edition_hints(report["labels"], context, period) + caveat_hints(draft, context)
    trace["draft_issues"] = hints

    # проверку пропускаем, если solution.py сообщил, что время на исходе: черновик лучше заглушки
    late = time.time() > settings.get("review_until", float("inf"))
    wrong_edition = bool(edition_hints(report["labels"], context, period))
    # в режиме fast проверку всё равно запускаем, если ответ взял не ту редакцию: такой ответ неверен по сути
    want_review = mode in ("auto", "full") or wrong_edition
    trace["review_skipped"] = late and want_review
    if want_review and not late:
        stage("review")
        t = time.time()
        try:
            review = deps["llm_json"](prompts.review_messages(question, history, context, draft, hints, period),
                                      settings.get("llm_max_tokens", 3000), {"verdict": "ok"}) or {}
        except Exception as exc:  # noqa: BLE001 — проверка необязательна, черновик уже есть
            print(f"Проверка ответа не удалась ({type(exc).__name__}): остаётся черновик", flush=True)
            review = {"verdict": "ok"}
        trace["review_ms"] = int((time.time() - t) * 1000)
        trace["review"] = {"verdict": review.get("verdict"), "problems": review.get("problems")}
        candidate = str(review.get("answer") or "").strip()
        if candidate and review.get("verdict") == "fixed":
            candidate_report = check_answer(candidate, context, question_text)
            if len(candidate_report["issues"]) <= len(report["issues"]):
                draft, report = candidate, candidate_report
                status = str(review.get("status") or status)

    stage("verify")
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
