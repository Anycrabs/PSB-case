"""Проверка поиска на демо-вопросах организаторов: попал ли нужный пункт в контекст для LLM.

  python eval/demo_eval.py            # база data/knowledge-base.zip, вопросы data/demo/demo_questions.csv
Режим эмбеддингов берётся из EMBEDDER_MODE (fake — без модели, lower bound качества).
"""

import argparse
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from legal_core.chunking import chunk_documents  # noqa: E402
from legal_core.config import get_settings  # noqa: E402
from legal_core.embeddings import embed_texts, embed_texts_batched  # noqa: E402
from legal_core.index import build_index  # noqa: E402
from legal_core.parsing import read_knowledge_base  # noqa: E402
from legal_core.pipeline import heuristic_queries, parse_dialog  # noqa: E402
from legal_core.retrieval import retrieve_context  # noqa: E402

# id -> (подстрока имени файла, номер пункта или None); None целиком — ответа в базе нет
EXPECTED = {
    "1": ("А-08", "3.1"), "2": ("ТК_ст70", None), "3": None, "4": ("А-08", "3.1"),
    "5": ("ред 2023", "2.4"), "6": ("П-17", "6.1"), "7": ("А-08", "4.3"), "8": ("П-17", "6.2"),
    "9": ("ПТ-05", "4.2"), "10": ("ред 2025", "1.3"), "11": ("Служебная записка", None), "12": ("ТК_ст70", None),
    "13": ("152ФЗ", None), "14": ("ТК_ст59", None), "15": ("А-08", "6.3"), "16": ("П-17", "2.1"),
    "17": ("ПТ-05", "6.1"), "18": ("ред 2025", "3.2"),
}


def hit(context, expected):
    label, point = expected
    for rank, item in enumerate(context, 1):
        if label in item["source_file"] and (point is None or re.search(rf"(?m)^{re.escape(point)}\.\s", item["text"])):
            return rank
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kb", default="data/knowledge-base.zip")
    parser.add_argument("--questions", default="data/demo/demo_questions.csv")
    args = parser.parse_args()
    settings = get_settings()
    docs, _ = read_knowledge_base(args.kb)
    chunks = chunk_documents(docs)
    index = build_index(chunks, embed_texts_batched([c["embed_text"] for c in chunks], settings), settings)
    embed = lambda texts: embed_texts(texts, settings)  # noqa: E731
    with open(args.questions, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    found = total = 0
    for row in rows:
        expected = EXPECTED.get(row["id"])
        question, history = parse_dialog(row["question"])
        context = retrieve_context(index, heuristic_queries(question, history), embed, settings)
        if expected is None:
            print(f"{row['id']:>3}  (ответа в базе нет)  контекст: {len(context)} фрагм.")
            continue
        total += 1
        rank = hit(context, expected)
        found += rank is not None
        mark = f"в контексте, позиция {rank}/{len(context)}" if rank else "НЕ НАЙДЕНО"
        print(f"{row['id']:>3}  {expected[0]} п.{expected[1]}: {mark}")
    print(f"\nНужный фрагмент в контексте: {found}/{total} (эмбеддинги: {settings['embedder_mode']})")


if __name__ == "__main__":
    main()
