"""Структурное разбиение документа на фрагменты: раздел > глава > статья > пункт.

Каждый фрагмент — словарь с текстом и метаданными, по которым потом строится ссылка
вида «Название документа, ст. 5 п. 2». Шаблоны нумерации подстраиваются под базу знаний.
"""

import re

TARGET_CHARS = 900   # соседние пункты одной статьи (или одного документа без статей) склеиваем до этого размера
MAX_CHARS = 1500     # длиннее — режем по абзацам, а слишком длинные абзацы — по предложениям
OVERLAP_CHARS = 200  # перекрытие при разрезании длинного блока
HEADING_CHARS = 120  # блок короче и без статьи/пункта — это голый заголовок

SECTION_RE = re.compile(r"^(раздел|часть)\s+([IVXLC\d]+)\.?\s*(.*)$", re.I)
CHAPTER_RE = re.compile(r"^глава\s+([\dIVXLC]+(?:\.\d+)?)\.?\s*(.*)$", re.I)
ARTICLE_RE = re.compile(r"^статья\s+(\d+(?:\.\d+)*)\.?\s*(.*)$", re.I)
POINT_RE = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){0,3})\.\s+(?=[«\"(A-Za-zА-Яа-яЁё])(.*)$")  # «1.», «3.2.»; «1)» — подпункт, не режем
SKIP_LINE_RE = re.compile(r"^(Источник:|URL:|Дата выгрузки:)", re.I)
DEFINITION_RE = re.compile(r"основные понятия|термины и определения|понятия и термины|определения", re.I)


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


def merge_small(blocks):
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
        if len(text) < HEADING_CHARS and block["kind"] in ("section", "chapter", "preamble"):
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
        if len(buf) + len(unit) > MAX_CHARS and buf.strip():
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
    for block in merge_small(split_blocks(doc["text"])):
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
                "embed_text": f"{header} | {doc.get('label', '')}\n{piece}",
            })
    return chunks


def chunk_documents(docs):
    return [chunk for doc in docs for chunk in chunk_document(doc)]
