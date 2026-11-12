"""Проверки на базе знаний организаторов (data/knowledge-base.zip)."""

import csv

from legal_core.pipeline import heuristic_queries, parse_dialog
from legal_core.retrieval import retrieve_context
from legal_core.embeddings import embed_texts
from tests.conftest import ROOT


def test_passports(kb):
    docs, _ = kb
    by_label = {d["label"]: d for d in docs}
    old = by_label["Регламент согласования договоров ред 2023 (утратил силу)"]
    new = by_label["Регламент согласования договоров ред 2025"]
    assert (old["valid_from"], old["valid_to"], old["is_current"]) == ("2023-01-10", "2025-03-01", False)
    assert (new["valid_from"], new["is_current"]) == ("2025-03-01", True)
    assert by_label["Доп соглашение 1 к договору П-17-2024"]["amends"] == "П-17/2024"
    assert by_label["Договор аренды А-08-2024 Северный Склад"]["title"].startswith("Договор аренды нежилого помещения № А-08/2024")
    assert by_label["ТК_ст70_действующая"]["title"].startswith("ТК РФ, ст. 70")
    memo = by_label["Служебная записка об архиве договоров"]
    assert memo["removed_lines"] and "ИИ" not in memo["text"].split("Начальник")[0][-400:]


def test_demo_questions_retrieval(kb, settings):
    """Нужный документ попадает в контекст для всех демо-вопросов, где ответ есть в базе."""
    _, index = kb
    expected = {"1": "А-08", "2": "ТК_ст70", "5": "ред 2023", "6": "П-17", "7": "А-08", "8": "П-17",
                "9": "ПТ-05", "10": "ред 2025", "11": "Служебная записка", "13": "152ФЗ", "14": "ТК_ст59",
                "15": "А-08", "16": "П-17", "17": "ПТ-05", "18": "ред 2025"}
    with open(ROOT / "data" / "demo" / "demo_questions.csv", encoding="utf-8-sig") as fh:
        rows = {r["id"]: r["question"] for r in csv.DictReader(fh)}
    embed = lambda texts: embed_texts(texts, settings)  # noqa: E731
    for qid, label in expected.items():
        question, history = parse_dialog(rows[qid])
        context = retrieve_context(index, heuristic_queries(question, history), embed, settings)
        assert any(label in c["source_file"] for c in context), qid


def test_supplement_follows_contract(kb, settings):
    _, index = kb
    context = retrieve_context(index, ["По какому адресу доставляется товар по договору П-17/2024?"],
                               lambda t: embed_texts(t, settings), settings)
    assert any(c["doc_type"] == "supplement" for c in context)
