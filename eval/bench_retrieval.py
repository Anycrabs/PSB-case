"""Проверка поиска на расширенном наборе data/eval/bench.csv: попал ли нужный фрагмент в контекст LLM.

  EMBEDDER_MODE=fake python eval/bench_retrieval.py     # без модели (нижняя граница)
  EMBEDDER_MODE=local python eval/bench_retrieval.py    # с bge-m3
Попадание: во фрагменте из файла expected_file есть фраза expected_phrase.
"""

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from legal_core.chunking import chunk_documents  # noqa: E402
from legal_core.config import get_settings  # noqa: E402
from legal_core.embeddings import embed_texts, embed_texts_batched  # noqa: E402
from legal_core.index import build_index  # noqa: E402
from legal_core.parsing import read_knowledge_base  # noqa: E402
from legal_core.pipeline import heuristic_queries, parse_dialog  # noqa: E402
from legal_core.retrieval import retrieve_context  # noqa: E402


def norm(text):
    return " ".join(text.replace(" ", " ").split()).lower()


def lexical_only(_texts):
    raise RuntimeError("векторный канал выключен")


EMBED = None


def main():
    global EMBED
    settings = get_settings()
    # --lexical: без векторного канала (только BM25 и точные ссылки). В режиме fake «векторы» — хеш-заглушка,
    # которая только шумит, поэтому для оценки без bge-m3 честнее этот режим.
    EMBED = lexical_only if "--lexical" in sys.argv else (lambda t: embed_texts(t, settings))
    docs, _ = read_knowledge_base(str(ROOT / "data" / "knowledge-base.zip"))
    chunks = chunk_documents(docs)
    index = build_index(chunks, embed_texts_batched([c["embed_text"] for c in chunks], settings), settings)
    with open(ROOT / "data" / "eval" / "bench.csv", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    hits = total = 0
    for row in rows:
        question, history = parse_dialog(row["question"])
        context = retrieve_context(index, heuristic_queries(question, history), EMBED, settings)
        size = sum(len(c["text"]) for c in context)
        if not row["expected_file"]:
            print(f"{row['id']:>4}  (ответа в базе нет)  контекст: {len(context)} фрагм., {size} симв.")
            continue
        total += 1
        rank = next((i for i, c in enumerate(context, 1)
                     if row["expected_file"] in c["source_file"] and norm(row["expected_phrase"]) in norm(c["text"])), None)
        hits += rank is not None
        where = f"позиция {rank}/{len(context)}" if rank else "НЕ НАЙДЕНО"
        docs_in = sorted({c["source_file"].split("__")[-1][:28] for c in context})
        print(f"{row['id']:>4}  {row['expected_file']:<18} {where:<16} {size:>6} симв.  " + ("" if rank else f"в контексте: {docs_in}"))
    mode = "только BM25" if "--lexical" in sys.argv else settings["embedder_mode"]
    print(f"\nНужный фрагмент в контексте: {hits}/{total} (поиск: {mode})")


if __name__ == "__main__":
    main()
