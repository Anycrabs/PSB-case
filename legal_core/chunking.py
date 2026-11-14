"""Структурное разбиение документа на фрагменты: раздел > глава > статья > пункт.

Каждый фрагмент — словарь с текстом и метаданными, по которым потом строится ссылка
вида «Название документа, ст. 5 п. 2». Шаблоны нумерации подстраиваются под базу знаний.
"""

import re

# Меняйте при любом изменении правил нарезки: индекс с другой версией считается устаревшим и пересобирается.
CHUNKER_VERSION = "2026-11-13.1"  # служебные строки Гаранта вне поиска, голые заголовки приклеены к тексту

TARGET_CHARS = 900   # соседние пункты одной статьи (или одного документа без статей) склеиваем до этого размера
MAX_CHARS = 1500     # длиннее — режем по абзацам, а слишком длинные абзацы — по предложениям
OVERLAP_CHARS = 200  # перекрытие при разрезании длинного блока
HEADING_CHARS = 120  # блок короче и без статьи/пункта — это голый заголовок
MIN_PIECE_CHARS = 200  # при разрезании длинного блока кусок короче не отделяем
MIN_SEARCHABLE_CHARS = 20  # меньше содержательного текста («4.», только пометки Гаранта) — не отдельный фрагмент

SECTION_RE = re.compile(r"^(раздел|часть)\s+([IVXLC\d]+)\.?\s*(.*)$", re.I)
CHAPTER_RE = re.compile(r"^глава\s+([\dIVXLC]+(?:\.\d+)?)\.?\s*(.*)$", re.I)
ARTICLE_RE = re.compile(r"^статья\s+(\d+(?:\.\d+)*)\.?\s*(.*)$", re.I)
POINT_RE = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){0,3})\.\s+(?=[«\"(A-Za-zА-Яа-яЁё])(.*)$")  # «1.», «3.2.»; «1)» — подпункт, не режем
SKIP_LINE_RE = re.compile(r"^(Источник:|URL:|Дата выгрузки:)", re.I)
DEFINITION_RE = re.compile(r"основные понятия|термины и определения|понятия и термины|определения", re.I)
# Служебные пометки выгрузки Гаранта: «Статья 70 изменена с 1 сентября 2026 г. - Федеральный закон…»,
# «Федеральным законом от … в статью 112 внесены изменения…», «Действие положений пункта 4.5 …».
# Для модели они полезны (даты редакций) и остаются в тексте фрагмента, но в поиске это шум:
# одинаковые слова «изменен», «Федеральный закон», «2026» есть в десятках фрагментов.
SERVICE_LINE_RE = re.compile(
    r"^\s*(?:[\w.]+\s+){0,4}"
    r"(?:изменен[аоы]?|дополнен[аоы]?|утратил[аои]? силу|изложен[аоы]? в новой редакции|внесены изменения)\b"
    r"[^\n]*Федеральн\w* закон"
    r"|^\s*Федеральным законом от [^\n]*(?:изложен[аоы]? в новой редакции|внесены изменения)"
    r"|^\s*Действие положений\b",
    re.I,
)


def strip_service(text):
    """Текст без служебных строк Гаранта — для поискового представления фрагмента (только у законов)."""
    kept = [line for line in text.split("\n") if not SERVICE_LINE_RE.match(line)]
    return "\n".join(kept).strip()


def classify_line(line):
    if m := SECTION_RE.match(line):
        return "section", f"{m.group(1).capitalize()} {m.group(2)}", m.group(3)
    if m := CHAPTER_RE.match(line):
        return "chapter", f"Глава {m.group(1)}", m.group(2)
    if m := ARTICLE_RE.match(line):
        return "article", m.group(1), m.group(2)
    if m := POINT_RE.match(line):
        return "point", m.group(1), m.group(2)
    return None, None, None


def line_offsets(text):
    offsets, pos = [], 0
    for line in text.split("\n"):
        offsets.append(pos)
        pos += len(line) + 1
    return offsets


def split_blocks(text):
    """Режет текст на блоки по заголовкам структуры. Каждый блок помнит свой путь."""
    lines = text.split("\n")
    offsets = line_offsets(text)
    state = {"section": None, "chapter": None, "article": None, "article_title": ""}
    blocks, current = [], None

    def start_block(kind, label, idx):
        return {
            "kind": kind,
            "section": state["section"],
            "chapter": state["chapter"],
            "article": state["article"],
            "article_title": state["article_title"],
            "point": label if kind == "point" else None,
            "start": offsets[idx],
            "lines": [],
        }

    for idx, line in enumerate(lines):
        if SKIP_LINE_RE.match(line):
            continue
        kind, label, rest = classify_line(line)
        if kind == "section":
            state.update(section=label, chapter=None, article=None, article_title="")
        elif kind == "chapter":
            state.update(chapter=label, article=None, article_title="")
        elif kind == "article":
            state.update(article=label, article_title=rest.strip()[:100])
        if kind is not None or current is None:
            if current and current["lines"]:
                blocks.append(current)
            current = start_block(kind or "preamble", label, idx)
        current["lines"].append(line)
        current["end"] = offsets[idx] + len(line)
    if current and current["lines"]:
        blocks.append(current)
    return blocks


def citation_for(block):
    parts = []
    if block["article"]:
        parts.append(f"ст. {block['article']}")
    if block["point"]:
        parts.append(f"п. {block['point']}")
    if parts:
        return " ".join(parts)
    if block["chapter"]:
        return block["chapter"]
    if block["section"]:
        return block["section"]
    return "вводная часть"


def struct_path_for(block):
    path = [block["section"], block["chapter"]]
    if block["article"]:
        title = f" {block['article_title']}" if block["article_title"] else ""
        path.append(f"Статья {block['article']}.{title}")
    if block["point"]:
        path.append(f"п. {block['point']}")
    return [p for p in path if p]


def parent_key(block):
    if block["article"]:
        return f"art{block['article']}"
    if block["point"]:
        return f"p{block['point'].split('.')[0]}"
    return block["chapter"] or block["section"] or "root"


def merge_small(blocks, is_law=False):
    """Склеивает короткие соседние блоки одной статьи; голые заголовки приклеивает к следующему блоку."""
    merged, carry = [], None
    for block in blocks:
        text = "\n".join(block["lines"]).strip()
        if not text:
            continue
        if carry is not None:
            text = carry["text"] + "\n" + text
            block = {**block, "start": carry["start"]}
            carry = None
        # голый заголовок (раздел, глава, статья) или блок только из служебных пометок — приклеиваем к следующему:
        # сам по себе он ничего не отвечает, но даёт следующему фрагменту контекст
        searchable = strip_service(text) if is_law else text
        is_heading = len(searchable) < HEADING_CHARS and block["kind"] in ("section", "chapter", "preamble", "article")
        if is_heading or len(searchable) < MIN_SEARCHABLE_CHARS:
            carry = {"text": text, "start": block["start"]}
            continue
        prev = merged[-1] if merged else None
        same_scope = prev is not None and prev["article"] == block["article"] and prev["kind"] != "preamble"
        if same_scope and len(prev["text"]) + len(text) <= TARGET_CHARS:
            prev["text"] += "\n" + text
            prev["end"] = block["end"]
            prev["points"].append(block["point"])
            continue
        merged.append({**block, "text": text, "points": [block["point"]]})
    if carry is not None:
        if merged:
            merged[-1]["text"] += "\n" + carry["text"]
        else:
            merged.append({"kind": "preamble", "section": None, "chapter": None, "article": None,
                           "article_title": "", "point": None, "start": carry["start"],
                           "end": carry["start"] + len(carry["text"]), "text": carry["text"], "points": [None]})
    return merged


def split_units(text):
    """Абзацы; слишком длинный абзац — по предложениям. Возвращает куски, склеиваемые без потерь."""
    units = []
    for para in re.split(r"(?<=\n)", text):
        if len(para) <= MAX_CHARS:
            units.append(para)
        else:
            units.extend(re.split(r"(?<=[.;:])(?=\s)", para))
    return units


def split_large(text, start):
    """Делит длинный текст на куски до MAX_CHARS с перекрытием по границе слова. Возвращает (кусок, смещение)."""
    if len(text) <= MAX_CHARS:
        return [(text, start)]
    pieces, buf, buf_start, pos = [], "", start, start
    for unit in split_units(text):
        # короткий хвост («4.» перед длинным абзацем) не режем в отдельный кусок — он ничего не значит без продолжения
        if len(buf) + len(unit) > MAX_CHARS and len(buf.strip()) >= MIN_PIECE_CHARS:
            pieces.append((buf.strip(), buf_start))
            tail = buf[-OVERLAP_CHARS:]
            cut = tail.find(" ")
            tail = tail[cut + 1:] if cut != -1 else ""
            buf_start = pos - len(tail)
            buf = tail
        buf += unit
        pos += len(unit)
    if buf.strip():
        pieces.append((buf.strip(), buf_start))
    return pieces


def point_range(points):
    points = [p for p in points if p]
    if not points:
        return None
    return points[0] if len(points) == 1 else f"{points[0]}–{points[-1]}"


def chunk_document(doc):
    chunks = []
    display = doc.get("display_title") or doc["title"]
    is_law = doc["doc_type"] == "law"
    for block in merge_small(split_blocks(doc["text"]), is_law):
        parent = parent_key(block)
        block = {**block, "point": point_range(block["points"])}
        citation = citation_for(block)
        path = struct_path_for(block)
        is_definition = bool(DEFINITION_RE.search(block["article_title"] or "")) or "понимается" in block["text"]
        for piece, offset in split_large(block["text"], block["start"]):
            idx = len(chunks)
            header = " > ".join([display, *path])
            chunks.append({
                "chunk_id": f"{doc['doc_id']}:{idx:04d}",
                "doc_id": doc["doc_id"],
                "doc_title": doc["title"],
                "doc_display": display,
                "doc_label": doc.get("label", ""),
                "doc_type": doc["doc_type"],
                "source_file": doc["source_file"],
                "citation": citation,
                "struct_path": path,
                "parent_id": f"{doc['doc_id']}:{parent}",
                "is_definition": is_definition,
                "edition": doc.get("edition", ""),
                "is_current": doc.get("is_current", True),
                "valid_from": doc.get("valid_from"),
                "valid_to": doc.get("valid_to"),
                "contract_no": doc.get("contract_no"),
                "amends": doc.get("amends"),
                "char_span": [offset, offset + len(piece)],
                "text": piece,
                "embed_text": f"{header} | {doc.get('label', '')} {search_topic(doc)}\n{(strip_service(piece) if is_law else piece) or piece}",
            })
    return chunks


# Главы НК РФ по номерам статей: заголовки статей одинаковые («Налоговые ставки» в ст. 164, 224, 284),
# поэтому без названия налога вопрос «ставка НДС» не находит нужную статью. Только для поиска:
# в текст фрагмента и в ответ это не попадает.
NK_CHAPTERS = [
    (106, 142, "налоговые правонарушения ответственность налоговые санкции штраф"),
    (143, 178, "НДС налог на добавленную стоимость"),
    (179, 206, "акцизы"),
    (207, 233, "НДФЛ налог на доходы физических лиц зарплата"),
    (234, 245, "страховые взносы"),
    (246, 333, "налог на прибыль организаций"),
]
LAW_TOPICS = [
    ("ТК РФ", "трудовой кодекс работник работодатель"),
    ("КоАП", "административное правонарушение административный штраф"),
    ("152-ФЗ", "персональные данные ПДн"),
    ("оплаты труда", "МРОТ минимальная зарплата"),
]


def search_topic(doc):
    """Ключевые слова темы закона для поискового заголовка фрагмента."""
    title = doc.get("title", "")
    words = [topic for marker, topic in LAW_TOPICS if marker in title]
    article = str(doc.get("article") or "")
    if title.startswith("НК РФ") and article.split(".")[0].isdigit():
        number = int(article.split(".")[0])
        words += [topic for low, high, topic in NK_CHAPTERS if low <= number <= high]
    return " ".join(words)


def chunk_documents(docs):
    return [chunk for doc in docs for chunk in chunk_document(doc)]
