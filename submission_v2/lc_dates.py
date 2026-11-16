"""Дата события из вопроса и выбор редакции документа.

В базе есть документы в нескольких редакциях (регламент согласования ред. 2023 — утратил силу с 01.03.2025,
ред. 2025 — действует). Вопрос «договор заключали в августе 2024 года» требует редакцию, действовавшую тогда,
а модель без подсказки берёт действующую. Здесь без LLM:
  extract_event_period(text) -> (начало, конец, подпись) | None — «в августе 2024», «15.08.2024», «в 2024 году»;
  applicability(chunk, period) -> "yes" | "no" | "partly" | None — действовала ли редакция фрагмента в этот период.
"""

import calendar
import re

MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}
MONTH_NAMES = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь",
               "октябрь", "ноябрь", "декабрь"]
MONTH_RE = (r"(январ[ья]|январе|феврал[ья]|феврале|марта?|марте|апрел[ья]|апреле|ма[йяе]|июн[ья]|июне|"
            r"июл[ья]|июле|августа?|августе|сентябр[ья]|сентябре|октябр[ья]|октябре|ноябр[ья]|ноябре|"
            r"декабр[ья]|декабре)")
YEAR = r"((?:19|20)\d{2})"
NUMERIC_RE = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.((?:19|20)\d{2})(?!\d)")
DAY_MONTH_RE = re.compile(rf"(?<!\d)(\d{{1,2}})\s+{MONTH_RE}\s+{YEAR}", re.I)
MONTH_YEAR_RE = re.compile(rf"{MONTH_RE}\s+{YEAR}", re.I)
YEAR_RE = re.compile(rf"(?:\bв|\bза|\bс|\bдо|\bна)\s+{YEAR}\s*(?:год[ауе]?\b|г\.)", re.I)
# даты реквизитов документов («письмо от 15.04.2025», «ред. от 01.03.2025») — не дата события
DOC_DATE_PREFIX_RE = re.compile(r"(?:\bот|ред\.?|редакци\w+|приказ\w*|№\s*\S+)\s*$", re.I)


def _month(word):
    word = word.lower()
    for stem, number in sorted(MONTHS.items(), key=lambda item: -len(item[0])):
        if word.startswith(stem):
            return number
    return None


def _show(iso):
    return f"{iso[8:10]}.{iso[5:7]}.{iso[0:4]}"


def _is_doc_date(text, start):
    return bool(DOC_DATE_PREFIX_RE.search(text[max(0, start - 25):start]))


def extract_event_period(text):
    """Первая дата события в тексте: (YYYY-MM-DD начала, YYYY-MM-DD конца, подпись) или None."""
    text = text or ""
    found = []
    for match in NUMERIC_RE.finditer(text):
        day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31 and not _is_doc_date(text, match.start()):
            iso = f"{year:04d}-{month:02d}-{day:02d}"
            found.append((match.start(), iso, iso, _show(iso)))
    for match in DAY_MONTH_RE.finditer(text):
        month = _month(match.group(2))
        if month and not _is_doc_date(text, match.start()):
            iso = f"{int(match.group(3)):04d}-{month:02d}-{int(match.group(1)):02d}"
            found.append((match.start(), iso, iso, _show(iso)))
    covered = {m.start(2) for m in DAY_MONTH_RE.finditer(text)}
    for match in MONTH_YEAR_RE.finditer(text):
        month = _month(match.group(1))
        if not month or any(abs(match.start() - pos) < 3 for pos in covered) or _is_doc_date(text, match.start()):
            continue
        year = int(match.group(2))
        last = calendar.monthrange(year, month)[1]
        found.append((match.start(), f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last:02d}",
                      f"{MONTH_NAMES[month - 1]} {year} г."))
    for match in YEAR_RE.finditer(text):
        year = int(match.group(1))
        found.append((match.start(), f"{year:04d}-01-01", f"{year:04d}-12-31", f"{year} г."))
    if not found:
        return None
    _, start, end, label = min(found, key=lambda item: item[0])
    return {"start": start, "end": end, "label": label}


def applicability(item, period):
    """Действовала ли редакция документа (valid_from/valid_to фрагмента) в период события.
    None — у документа нет сведений о редакциях (законы, договоры) или период не задан."""
    if not period:
        return None
    start, end = item.get("valid_from"), item.get("valid_to")
    if not start and not end:
        return None
    begins_ok = start is None or start <= period["start"]
    ends_ok = end is None or period["end"] < end
    if begins_ok and ends_ok:
        return "yes"
    overlaps = (start is None or start <= period["end"]) and (end is None or period["start"] < end)
    return "partly" if overlaps else "no"


def applicability_note(item, period):
    """Пометка для заголовка фрагмента в контексте LLM."""
    status = applicability(item, period)
    if status == "yes":
        return f"ПРИМЕНЯЕТСЯ к событию ({period['label']}): эта редакция действовала на дату события"
    if status == "partly":
        return f"действовала только в части периода события ({period['label']}): уточни дату"
    if status == "no":
        start, end = item.get("valid_from"), item.get("valid_to")
        if start and start > period["end"]:
            return f"НЕ ПРИМЕНЯЕТСЯ к событию ({period['label']}): эта редакция действует только с {_show(start)}"
        return f"НЕ ПРИМЕНЯЕТСЯ к событию ({period['label']}): эта редакция утратила силу с {_show(end)}"
    return ""
