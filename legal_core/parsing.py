"""Чтение базы знаний (папка или ZIP), нормализация текста и паспорт документа.

Паспорт документа: тип (закон, договор, доп. соглашение, регламент, письмо…), понятное название,
редакция (действует / утратила силу и даты), номер договора и ссылка доп. соглашения на договор.
"""

import hashlib
import io
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

SUPPORTED = {".txt", ".md", ".docx", ".pdf", ".html", ".htm"}
DATE_RE = r"(\d{2})\.(\d{2})\.(\d{4})"
GARANT_HEADER_RE = re.compile(r"^(Источник:|URL:|Дата выгрузки:)", re.I)
AI_INSTRUCTION_RE = re.compile(
    r"(для\s+ии|ии-ассистент|ai[- ]assistant|языков\w+\s+модел|игнорируй|ignore\s+(?:all|previous)|"
    r"не\s+упоминай\s+эту\s+инструкцию|system\s*prompt|системн\w+\s+промпт)",
    re.I,
)
CONTRACT_NO_RE = re.compile(r"№\s*([A-ZА-ЯЁ]{1,4}-\d{1,3}/\d{2,4})")
TITLE_START_RE = re.compile(
    r"^(договор|дополнительное соглашение|регламент|положение|порядок|инструкция|приказ|служебная записка|"
    r"письмо|политика|стандарт|соглашение)\b",
    re.I,
)


# ---------- извлечение текста ----------

def text_from_docx(raw):
    """DOCX — это ZIP с XML; читаем абзацы без сторонних библиотек."""
    with zipfile.ZipFile(io.BytesIO(raw)) as doc:
        root = ET.fromstring(doc.read("word/document.xml"))
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    return "\n".join("".join(t.text or "" for t in p.iter(ns + "t")) for p in root.iter(ns + "p"))


def text_from_pdf(raw):
    from pypdf import PdfReader

    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(raw)).pages)


def text_from_html(raw):
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(raw.decode("utf-8", errors="ignore"), "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text("\n")


def decode_text(raw):
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def extract_text(name, raw):
    suffix = Path(name).suffix.lower()
    if suffix == ".docx":
        return text_from_docx(raw)
    if suffix == ".pdf":
        return text_from_pdf(raw)
    if suffix in (".html", ".htm"):
        return text_from_html(raw)
    return decode_text(raw)


def normalize_text(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ").replace("​", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ---------- паспорт документа ----------

def iso_date(match):
    day, month, year = match.groups()
    return f"{year}-{month}-{day}"


def sentence_case(line):
    """«ДОГОВОР АРЕНДЫ … ООО «МЕРИДИАН»» -> «Договор аренды … ООО «Меридиан»»."""
    words = line.split(" ")
    is_shouting = lambda w: (len(re.sub(r"[^A-Za-zА-Яа-яЁё]", "", w)) >= 4  # noqa: E731
                             and w == w.upper() and not any(ch.isdigit() for ch in w))
    if sum(is_shouting(w) for w in words) < 2:
        return line
    out = []
    for word in words:
        if word.startswith("«") and is_shouting(word):
            out.append("«" + word[1:2].upper() + word[2:].lower())
        elif is_shouting(word):
            out.append(word.lower())
        else:
            out.append(word)
    text = " ".join(out)
    return text[:1].upper() + text[1:]


def file_label(name):
    stem = Path(name).stem
    return stem.split("__", 1)[1] if "__" in stem else stem


def file_group(name):
    head = Path(name).stem.split("__", 1)[0].lower() if "__" in Path(name).stem else ""
    if "закон" in head:
        return "law"
    if "документ" in head or "компан" in head:
        return "company"
    return ""


def law_title(text, name):
    source = next((ln for ln in text.split("\n")[:3] if ln.startswith("Источник:")), "")
    law = re.search(r"(Федеральный закон от [^\"«]+[\"«][^\"»]+[\"»])", source)
    if law:
        law_name = law.group(1).replace(" N ", " № ").replace('"', "«", 1).replace('"', "»", 1)
    else:
        code = re.search(r"\b([А-Я][А-Яа-я]{1,4} РФ)\b", source) or re.search(r"([А-Я][А-Яа-я]{1,4})_", Path(name).stem)
        law_name = code.group(1) if code else file_label(name)
        if not law_name.endswith("РФ") and len(law_name) <= 5:
            law_name += " РФ"
    article = re.search(r"^Статья (\d+(?:\.\d+)*)\.\s*(.*)$", text, re.M)
    if not article:
        return law_name, None
    caption = article.group(2).strip()
    caption = f" «{caption}»" if caption and len(caption) <= 110 and not caption.lower().startswith("установить") else ""
    return f"{law_name}, ст. {article.group(1)}{caption}", article.group(1)


def company_title(text, name):
    lines = [ln for ln in text.split("\n")[:12] if ln.strip()]
    for i, line in enumerate(lines):
        if line.lower().startswith("редакция утратила") or not TITLE_START_RE.match(line):
            continue
        title = sentence_case(line)
        following = lines[i + 1] if i + 1 < len(lines) else ""
        if re.match(r"^к\s", following):
            title += " " + following
        elif re.match(r"^(О|Об)\s", following) and len(following) < 120:
            title += f" «{following}»"
        return title
    return file_label(name)


def guess_doc_type(title, group):
    low = title.lower()
    if group == "law":
        return "law"
    rules = [
        ("supplement", r"дополнительн\w* соглашени"),
        ("contract", r"^договор"),
        ("regulation", r"регламент|положени[ея]|порядок|инструкци|политик|стандарт|правила"),
        ("letter", r"письмо|служебн\w+ записк|приказ|уведомлени"),
        ("code", r"кодекс"),
        ("law", r"закон"),
    ]
    for doc_type, pattern in rules:
        if re.search(pattern, low):
            return doc_type
    return "other"


def edition_info(text):
    head = text[:800]
    valid_to = re.search(r"утратил[аио]?\s+силу\s+с\s+" + DATE_RE, head, re.I)
    valid_from = re.search(r"действует\s+с\s+" + DATE_RE, head, re.I) or re.search(r"редакция\s+от\s+" + DATE_RE, head, re.I)
    return (iso_date(valid_from) if valid_from else None, iso_date(valid_to) if valid_to else None)


def edition_label(valid_from, valid_to):
    show = lambda d: f"{d[8:10]}.{d[5:7]}.{d[0:4]}"  # noqa: E731
    if valid_to:
        start = f"ред. от {show(valid_from)}, " if valid_from else ""
        return f"{start}утратила силу с {show(valid_to)}"
    if valid_from:
        return f"ред. от {show(valid_from)}, действующая"
    return ""


def counterparty(text):
    parties = re.findall(r"ООО «([^»]+)»", text[:1500])
    others = [p for p in dict.fromkeys(parties) if p.lower() != "меридиан"]
    return others[0] if others else None


def strip_ai_instructions(text):
    """Строки-инструкции для ИИ внутри документов — это данные, а не нормы: в поиск они не попадают."""
    kept, removed = [], []
    for line in text.split("\n"):
        (removed if AI_INSTRUCTION_RE.search(line) else kept).append(line)
    return "\n".join(kept), removed


def make_doc_id(name):
    stem = re.sub(r"[^\w\-]+", "_", file_label(name), flags=re.U).strip("_").lower()[:40]
    return f"{stem}-{hashlib.sha1(name.encode('utf-8')).hexdigest()[:6]}"


def build_document(name, raw):
    text = normalize_text(extract_text(name, raw))
    group = file_group(name)
    article = None
    if group == "law" or text.startswith("Источник:"):
        group = "law"
        title, article = law_title(text, name)
    else:
        title = company_title(text, name)
    doc_type = guess_doc_type(title, group)
    valid_from, valid_to = edition_info(text) if doc_type != "law" else (None, None)
    edition = edition_label(valid_from, valid_to)
    party = counterparty(text) if doc_type in ("contract", "supplement") else None
    if party and party not in title:
        title += f" с ООО «{party}»"
    numbers = CONTRACT_NO_RE.findall(title)
    clean_text, removed = strip_ai_instructions(text)
    return {
        "doc_id": make_doc_id(name),
        "title": title,
        "display_title": f"{title} ({edition})" if edition else title,
        "label": file_label(name),
        "doc_type": doc_type,
        "source_file": Path(name).name,
        "article": article,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "edition": edition,
        "is_current": valid_to is None,
        "contract_no": numbers[0] if doc_type == "contract" and numbers else None,
        "amends": numbers[0] if doc_type == "supplement" and numbers else None,
        "removed_lines": removed,
        "text": clean_text,
    }


# ---------- чтение папки или архива ----------

def read_entries(entries):
    docs, errors = [], []
    for name, raw in entries:
        if Path(name).suffix.lower() not in SUPPORTED or "__MACOSX" in name:
            continue
        try:
            doc = build_document(name, raw)
            if doc["text"].strip():
                docs.append(doc)
        except Exception as exc:  # noqa: BLE001 — собираем все ошибки, а не падаем на первой
            errors.append({"file": name, "error": f"{type(exc).__name__}: {exc}"})
    return docs, errors


def zip_name(info):
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("utf-8")
    except UnicodeError:
        return info.filename.encode("cp437").decode("cp866", errors="ignore")


def read_zip(path):
    with zipfile.ZipFile(path) as archive:
        entries = [(zip_name(i), archive.read(i)) for i in sorted(archive.infolist(), key=lambda i: i.filename)
                   if not i.is_dir()]
    return read_entries(entries)


def read_folder(folder):
    folder = Path(folder)
    entries = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        if path.suffix.lower() == ".zip":
            docs, errors = read_zip(path)
            entries.append(("__zip__", (docs, errors)))
        elif path.suffix.lower() in SUPPORTED:
            entries.append((str(path.relative_to(folder)), path.read_bytes()))
    docs, errors = read_entries([e for e in entries if e[0] != "__zip__"])
    for _, (zdocs, zerrors) in (e for e in entries if e[0] == "__zip__"):
        docs += zdocs
        errors += zerrors
    return docs, errors


def read_knowledge_base(path):
    """Папка с документами, ZIP-архив или папка с ZIP-архивом."""
    path = Path(path)
    return read_zip(path) if path.suffix.lower() == ".zip" else read_folder(path)
