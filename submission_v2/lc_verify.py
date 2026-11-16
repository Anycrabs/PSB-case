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


# ---------- оговорки норм ----------
# Ответ «срок испытания главбуха — не более шести месяцев» верен, но эталон добавляет «если иное не установлено
# федеральным законом», и судья снижает оценку за неполноту. Находим оговорки в предложении фрагмента,
# на которое опирается предложение ответа, и проверяем, что они не потерялись.

CAVEAT_RE = re.compile(
    r"(если иное не (?:установлено|предусмотрено|определено|следует)[^.;:]{0,90}"
    r"|за исключением [^.;:]{3,140}|кроме случа(?:ев|я)[^.;:]{0,120})",
    re.I,
)
SAFE_CAVEAT_RE = re.compile(r"^если иное не (?:установлено|предусмотрено|определено)", re.I)
# в строгом режиме (дописывание оговорки без LLM) предложение ответа должно совпасть с нормой по числу или сроку
NUMERAL_STEMS = {"одног", "двух", "трех", "трёх", "четыр", "пяти", "шести", "семи", "восьм", "девят", "десят",
                 "двадц", "тридц", "сорок", "месяц", "недел", "рабоч", "кален", "проце", "рубле"}
STOP_STEMS = {"котор", "также", "этого", "этому", "может", "могут", "долже", "должн", "являе", "случа", "соотв"}


def _stems(text):
    words = re.findall(r"[а-яёa-z0-9]+", (text or "").lower())
    return {w[:5] for w in words if (len(w) >= 4 or w.isdigit()) and w[:5] not in STOP_STEMS}


ABBREV_END_RE = re.compile(r"(?:\b(?:ст|п|пп|ч|подп|абз|г|гг|руб|коп|т\.е|т\.к|см|им|ред|№)\.)$", re.I)


def _sentences(text):
    """Предложения без разрыва после «ст.», «п.», «ч.», «г.» и перед цифрой (в отличие от split_sentences)."""
    parts = re.split(r"(?<=[.!?])\s+(?=[А-ЯЁA-Z«(])", (text or "").strip())
    merged = []
    for part in parts:
        if merged and ABBREV_END_RE.search(merged[-1]):
            merged[-1] += " " + part
        else:
            merged.append(part)
    return [p for p in merged if p.strip()]


def _source_sentences(text):
    for line in re.split(r"\n+", text or ""):
        for sentence in _sentences(line):
            yield sentence.strip()


def _caveat_present(caveat, answer_lower):
    words = re.findall(r"[а-яёa-z]+", caveat.lower())
    head = " ".join(words[:3])
    if head.startswith("если иное не"):
        return "если иное не" in answer_lower or "иное не предусмотр" in answer_lower or "иное не установл" in answer_lower
    if head.startswith("за исключением"):
        return "исключ" in answer_lower
    return "кроме случа" in answer_lower or "исключ" in answer_lower


def missing_caveats(answer, context, strict=False):
    """[(метка, оговорка, предложение ответа)] — оговорки из пунктов, на которые опирается ответ, которых нет в ответе.
    strict — только короткие безопасные «если иное не установлено…» и при сильном совпадении с предложением."""
    by_label = {c["label"]: c for c in context}
    answer_lower = strip_markers(answer).lower()
    found, seen = [], set()
    for sentence in _sentences(answer):
        labels = [l for l in labels_in(sentence) if l in by_label]
        mine = _stems(strip_markers(sentence))
        if not labels or len(mine) < 3:
            continue
        for label in labels:
            for source in _source_sentences(by_label[label]["text"]):
                caveats = CAVEAT_RE.findall(source)
                if not caveats:
                    continue
                common = mine & _stems(source)
                need = max(4, int(0.4 * len(mine))) if strict else max(3, int(0.3 * len(mine)))
                if len(common) < need:
                    continue
                if strict and not any(stem.isdigit() or stem in NUMERAL_STEMS for stem in common):
                    continue
                for caveat in caveats:
                    caveat = caveat.strip(" ,")
                    if strict and not SAFE_CAVEAT_RE.match(caveat):
                        continue
                    key = caveat.lower()[:40]
                    if key in seen or _caveat_present(caveat, answer_lower):
                        continue
                    seen.add(key)
                    found.append((label, caveat, strip_markers(sentence)))
    return found


def caveat_hints(answer, context):
    return [f"в [{label}] норма содержит оговорку «{caveat}», а в ответе её нет (предложение «{sentence[:100]}»); "
            f"добавь, если она относится к ответу" for label, caveat, sentence in missing_caveats(answer, context)[:3]]


def add_missing_caveats(answer, context):
    """Добавляет после предложения ответа оговорку «если иное не установлено…» из той же нормы, если её потеряли."""
    additions = missing_caveats(answer, context, strict=True)
    if not additions:
        return answer
    sentences = _sentences(answer)
    for label, caveat, plain in additions[:2]:
        for i, sentence in enumerate(sentences):
            if strip_markers(sentence) == plain:
                sentences[i] = f"{sentence} Это правило действует, {caveat} [{label}]."
                break
    return " ".join(sentences)
