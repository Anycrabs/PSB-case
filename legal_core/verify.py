"""Детерминированная проверка ответа: метки фрагментов, номера пунктов и статей, числа, названия законов.

Всё, что модель могла «придумать», сверяется с текстом фрагментов контекста и вопроса.
"""

import re

MARKER_RE = re.compile(r"\s*\[\s*(S\d+(?:\s*[,;]\s*S\d+)*)\s*\]")
POINT_REF_RE = re.compile(r"(?:\bп\.|\bпп\.|\bпункт\w*)\s*(\d+(?:\.\d+)*)", re.I)
ARTICLE_REF_RE = re.compile(r"(?:\bст\.|\bстать\w*)\s*(\d+(?:\.\d+)*)", re.I)
NUMBER_RE = re.compile(r"(?<![\w.])\d{1,3}(?:[  ]\d{3})+(?![\d])|(?<![\w.])\d+(?:[.,]\d+)?(?:\s?%)?")
LEGAL_NAME_RE = re.compile(
    r"(федеральн\w+\s+закон\w*[^.;,()]{0,60}|\w+\s+кодекс\w*(?:\s+российской\s+федерации|\s+рф)?|№\s*\d+-ФЗ|\b[А-Я]{2,4}\s+РФ\b)",
    re.I,
)
DATE_IN_TEXT_RE = re.compile(r"\d{2}\.\d{2}\.\d{4}")


def labels_in(text):
    return [label for group in MARKER_RE.findall(text or "") for label in re.split(r"\s*[,;]\s*", group)]


def strip_markers(text):
    text = MARKER_RE.sub("", text or "")
    text = re.sub(r"\s+([.,;:!?)])", r"\1", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def split_sentences(text):
    parts = re.split(r"(?<=[.!?])\s+(?=[А-ЯЁA-Z0-9«(\d])", (text or "").strip())
    return [p for p in parts if p.strip()]


def squeeze_digits(text):
    return re.sub(r"(?<=\d)[  ](?=\d{3}\b)", "", text)


def corpus_of(context, extra=""):
    parts = [f"{c.get('doc_display') or c['doc_title']} {c['citation']} {c['text']}" for c in context]
    return squeeze_digits(" ".join(parts) + " " + extra).lower()


def numbers_in(sentence):
    """Числа, на которых держится утверждение: от двух цифр или с %, без номеров пунктов/статей и дат."""
    cleaned = POINT_REF_RE.sub(" ", ARTICLE_REF_RE.sub(" ", DATE_IN_TEXT_RE.sub(" ", sentence)))
    cleaned = re.sub(r"№\s*\S+", " ", cleaned)
    found = []
    for match in NUMBER_RE.finditer(cleaned):
        raw = match.group(0)
        digits = squeeze_digits(raw).replace(" ", "").replace(" ", "")
        if "%" in raw or len(re.sub(r"\D", "", digits)) >= 2:
            found.append(digits.replace("%", "").rstrip(".,"))
    return found


def unknown_legal_names(text, corpus):
    unknown = []
    for match in LEGAL_NAME_RE.finditer(text or ""):
        words = re.findall(r"[a-zа-яё0-9-]+", match.group(0).lower())
        stems = [w[:5] for w in words if len(w) >= 4 or any(ch.isdigit() for ch in w)]
        if stems and not all(stem in corpus for stem in stems):
            unknown.append(match.group(0).strip())
    return unknown


def point_exists(num, corpus):
    return re.search(rf"(?<![\d.]){re.escape(num)}(?![\d])", corpus) is not None


def article_exists(num, corpus):
    return re.search(rf"(?:ст\.|стать\w*)\s*{re.escape(num)}(?![\d.])", corpus) is not None


def check_answer(answer, context, question_text=""):
    """Возвращает проблемы по предложениям: [{'index', 'sentence', 'problems'}] и использованные метки."""
    valid = {c["label"] for c in context}
    corpus = corpus_of(context)
    allowed_numbers = corpus + " " + squeeze_digits(question_text).lower()
    report = []
    for i, sentence in enumerate(split_sentences(answer)):
        problems = []
        for label in labels_in(sentence):
            if label not in valid:
                problems.append(f"метка {label} не существует")
        plain = strip_markers(sentence)
        for num in POINT_REF_RE.findall(plain):
            if not point_exists(num, corpus):
                problems.append(f"пункт {num} не найден во фрагментах")
        for num in ARTICLE_REF_RE.findall(plain):
            if not article_exists(num, corpus):
                problems.append(f"статья {num} не найдена во фрагментах")
        for num in numbers_in(plain):
            if num not in allowed_numbers and num.replace(",", ".") not in allowed_numbers:
                problems.append(f"число {num} не найдено во фрагментах")
        for name in unknown_legal_names(plain, corpus):
            problems.append(f"документ «{name}» отсутствует во фрагментах")
        if problems:
            report.append({"index": i, "sentence": plain, "problems": problems})
    return {"issues": report, "labels": [l for l in dict.fromkeys(labels_in(answer)) if l in valid]}


def drop_sentences(answer, indices):
    keep = [s for i, s in enumerate(split_sentences(answer)) if i not in set(indices)]
    return " ".join(keep).strip()


def issue_hints(report):
    return [f"«{item['sentence'][:160]}»: {'; '.join(item['problems'])}" for item in report["issues"]]
