"""Заглушка OpenAI-совместимой LLM для разработки без ключа и для тестов.

Не «понимает» вопрос: выбирает фрагмент контекста с наибольшим пересечением слов и
возвращает JSON в нужном формате. Нужна, чтобы фронтенд и бэкенд работали, пока нет доступа к модели.

  uvicorn devtools.fake_llm:app --port 9000
  LLM_BASE_URL=http://localhost:9000/v1 LLM_MODEL=fake
"""

import re

from fastapi import FastAPI

from legal_core.lexical import tokenize

CONTEXT_ITEM_RE = re.compile(r"\[(S\d+)\] [^\n]*\n(.*?)(?=\n\n\[S\d+\] |\n</context>)", re.S)


def block(text, name):
    match = re.search(rf"<{name}>\n?(.*?)\n?</{name}>", text, re.S)
    return match.group(1) if match else ""


def analyze(user):
    question = block(user, "user_input").strip()
    kind = "offtopic" if re.search(r"рецепт|погод|анекдот|футбол|стих", question.lower()) else "legal"
    return {"type": kind, "standalone_question": question, "subquestions": [question], "event_date": None}


def first_sentence(text):
    lines = [ln for ln in text.strip().split("\n") if len(re.sub(r"^\d+(?:\.\d+)*\.?\s*", "", ln)) > 40]
    body = lines[0] if lines else text.strip()
    return re.split(r"(?<=[.;])\s", body, maxsplit=1)[0][:400]


def best_sentence(question, text):
    """Строка фрагмента с наибольшим пересечением слов с вопросом."""
    lines = [ln for ln in text.split("\n") if len(ln) > 30]
    if not lines:
        return first_sentence(text)
    return max(lines, key=lambda ln: len(question & set(tokenize(ln))))[:400]


def generate(user):
    question = set(tokenize(block(user, "user_input")))
    if re.search(r"рецепт|погод|анекдот|футбол|стих", block(user, "user_input").lower()):
        return {"status": "refusal", "answer": "Я отвечаю только по документам базы знаний.", "missing_info": []}
    best, best_score = None, 0
    for label, text in CONTEXT_ITEM_RE.findall(user):
        score = len(question & set(tokenize(text)))
        if score > best_score:
            best, best_score = (label, text), score
    if best is None or best_score < 2:
        return {"status": "refusal", "answer": "В базе знаний нет сведений, чтобы ответить на этот вопрос.",
                "missing_info": ["положения по теме вопроса"]}
    label, text = best
    return {"status": "answer", "answer": f"{best_sentence(question, text)} [{label}]", "missing_info": []}


def fake_answer(messages):
    system, user = messages[0]["content"], messages[-1]["content"]
    if "модуль разбора" in system:
        return analyze(user)
    if "строгий проверяющий" in system:
        return {"verdict": "ok", "problems": []}
    if "строгий судья" in system:  # eval/judge.py: заглушка всегда ставит «сопоставимо», чтобы проверить цепочку
        return {"verdict": "equal", "a_fabricated": False, "b_fabricated": False, "reason": "заглушка судьи"}
    return generate(user)


app = FastAPI(title="fake-llm")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/v1/chat/completions")
def completions(body: dict):
    import json

    content = json.dumps(fake_answer(body["messages"]), ensure_ascii=False)
    return {"id": "fake", "object": "chat.completion", "model": body.get("model", "fake"),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]}


@app.post("/v1/embeddings")
def embeddings(body: dict):
    from legal_core.embeddings import embed_fake

    texts = body["input"] if isinstance(body["input"], list) else [body["input"]]
    dense = embed_fake(texts)["dense"]
    return {"object": "list", "model": body.get("model"),
            "data": [{"object": "embedding", "index": i, "embedding": v.tolist()} for i, v in enumerate(dense)]}


@app.post("/v1/rerank")
def rerank(body: dict):
    query = set(tokenize(body["query"]))
    scores = [len(query & set(tokenize(doc))) / (1 + len(set(tokenize(doc))) ** 0.5) for doc in body["documents"]]
    order = sorted(range(len(scores)), key=lambda i: -scores[i])[: body.get("top_n") or len(scores)]
    return {"results": [{"index": i, "relevance_score": scores[i]} for i in order]}
