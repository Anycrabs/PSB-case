"""Оценка поиска на наборе data/eval/bench.csv: метрики recall@k, MRR и попадание в контекст LLM.

  EMBEDDER_MODE=fake python eval/bench_retrieval.py --lexical   # без модели: только BM25 и точные ссылки
  EMBEDDER_MODE=local python eval/bench_retrieval.py            # с bge-m3
  ... --verbose                                                 # построчно по каждому вопросу

Правильный фрагмент: из файла expected_file и содержит фразу expected_phrase.
- recall@5 / recall@10 — доля вопросов, где правильный фрагмент в первых 5 / 10 результатах гибридного поиска;
- MRR — среднее 1/позиция первого правильного фрагмента (0, если его нет в выдаче);
- «в контексте» — правильный фрагмент попал в итоговый контекст, который видит LLM;
- «доля по теме» — какая часть контекста (по символам) взята из нужного документа: чем выше, тем меньше шума.
В режиме fake «векторы» — хеш-заглушка, которая только шумит, поэтому без bge-m3 честнее режим --lexical.
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
from legal_core.retrieval import hybrid_search, retrieve_context  # noqa: E402


def norm(text):
    return " ".join(text.replace(" ", " ").split()).lower()


def lexical_only(_texts):
    raise RuntimeError("векторный канал выключен")


def is_target(chunk, row):
    return row["expected_file"] in chunk["source_file"] and norm(row["expected_phrase"]) in norm(chunk["text"])


def main():
    settings = get_settings()
    embed = lexical_only if "--lexical" in sys.argv else (lambda t: embed_texts(t, settings))
    verbose = "--verbose" in sys.argv
    docs, _ = read_knowledge_base(str(ROOT / "data" / "knowledge-base.zip"))
    chunks = chunk_documents(docs)
    index = build_index(chunks, embed_texts_batched([c["embed_text"] for c in chunks], settings), settings)
    with open(ROOT / "data" / "eval" / "bench.csv", encoding="utf-8-sig") as fh:
        rows = [r for r in csv.DictReader(fh) if r["expected_file"]]

    r5 = r10 = in_ctx = 0
    mrr = precision = 0.0
    misses = []
    for row in rows:
        question, history = parse_dialog(row["question"])
        queries = heuristic_queries(question, history)
        ranked = hybrid_search(index, queries[0], embed, settings)
        rank = next((i for i, c in enumerate(ranked, 1) if is_target(index["chunks"][c["idx"]], row)), None)
        context = retrieve_context(index, queries, embed, settings)
        hit = any(is_target(c, row) for c in context)
        total = sum(len(c["text"]) for c in context) or 1
        on_topic = sum(len(c["text"]) for c in context if row["expected_file"] in c["source_file"]) / total

        r5 += bool(rank and rank <= 5)
        r10 += bool(rank and rank <= 10)
        mrr += 1 / rank if rank else 0.0
        in_ctx += hit
        precision += on_topic
        if not hit:
            misses.append(row["id"])
        if verbose:
            print(f"{row['id']:>4}  ранг {rank or '—':>3}  в контексте: {'да ' if hit else 'НЕТ'}  "
                  f"по теме {on_topic:4.0%}  {len(context):>2} фрагм.  {row['question'][:60]}")

    n = len(rows)
    mode = "только BM25" if "--lexical" in sys.argv else settings["embedder_mode"]
    print(f"Вопросов: {n} (поиск: {mode})")
    print(f"recall@5  {r5 / n:.3f}\nrecall@10 {r10 / n:.3f}\nMRR       {mrr / n:.3f}")
    print(f"в контексте LLM: {in_ctx}/{n}; средняя доля контекста по теме: {precision / n:.0%}")
    if misses:
        print("не попали в контекст:", ", ".join(misses))


if __name__ == "__main__":
    main()
