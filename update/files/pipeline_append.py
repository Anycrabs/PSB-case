

from legal_core.verify import labels_in  # noqa: E402 — для простого режима сабмита

def clean_text_answer(raw):
    """Простой текст модели -> одна строка: без рассуждений <think>, markdown и префикса «Ответ:»."""
    text = re.sub(r"<think>.*?(</think>|$)", "", raw or "", flags=re.S)
    text = re.sub(r"\*\*|__|^#+\s*|^\s*[-•*]\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*(?:ответ|answer)\s*:\s*", "", text.strip(), flags=re.I)
    return " ".join(text.split())


def answer_question_lean(question, history, deps, settings):
    """Режим сабмита: поиск как в прототипе (гибридный + реранкер + редакции по дате), затем ОДИН вызов LLM
    простым текстом в стиле эталонов. Без JSON, без второго вызова и без удаления предложений проверкой:
    эти шаги на платформе давали больше потерь, чем пользы. deps: retrieve(queries, filters), llm_text(messages)."""
    started = time.time()
    request_id = uuid.uuid4().hex[:12]
    if not history:
        question, history = parse_dialog(question)
    history = sanitize_history(history)
    if detect_injection(question):
        return response("refusal", prompts.ATTACK_ANSWER, request_id, started, trace={"guard": "attack"})
    period = extract_event_period(question) or extract_event_period(
        " ".join(t["content"] for t in history if t["role"] == "user"))
    trace = {"mode": "lean", "event_period": period}
    context = deps["retrieve"](heuristic_queries(question, history), {"event_period": period} if period else {})
    trace["context"] = [{"label": c["label"], "doc": c.get("doc_display") or c["doc_title"], "citation": c["citation"]}
                        for c in context]
    if not context:
        return response("refusal", prompts.NO_CONTEXT_ANSWER, request_id, started, trace=trace)

    best = sorted(context, key=lambda c: -c.get("score", 0.0))
    try:
        raw = deps["llm_text"](prompts.lean_messages(question, history, context, period))
    except Exception as exc:  # noqa: BLE001 — LLM недоступна: текст лучших пунктов лучше заглушки
        print(f"Генерация не удалась ({type(exc).__name__}: {str(exc)[:120]}): ответ из текста фрагментов", flush=True)
        raw = ""
    draft = clean_text_answer(raw)
    if len(draft) < 15:
        trace["extractive"] = True
        return response("partial", extractive_answer(best, question), request_id, started, trace=trace,
                        sources=used_sources(context, [c["label"] for c in best[:2]]))
    if not draft.startswith("В базе знаний нет"):
        draft = add_missing_caveats(draft, context)
    labels = labels_in(draft)
    status = "refusal" if draft.startswith("В базе знаний нет") else "answer"
    return response(status, strip_markers(draft), request_id, started, trace=trace,
                    sources=used_sources(context, labels), answer_marked=marked_text(draft))
