"""Собирает ZIP-сабмит по Техническому регламенту: все файлы в корне архива, без папок.

Платформа переносит только корневые .py и requirements.txt, поэтому модули legal_core
кладутся в корень как lc_<имя>.py, а импорты в них переписываются.

  python submission/build_zip.py            # -> dist/submission.zip

Готовые эмбеддинги (lc_vectors.py). Пересчёт ~300 фрагментов bge-m3 на платформе съедал больше половины
20 минут, поэтому векторы считаются заранее и кладутся в ZIP. Откуда берутся:
  1) index/ (снимок, который строит сервис indexer в режиме local) — по ключу текста фрагмента;
  2) недостающие — POST {--embed-url}/embeddings: по умолчанию локальная копия прокси платформы
     (docker compose --profile eval up -d → http://localhost:9100/v1, тот же bge-m3, что в embedder).
  python submission/build_zip.py --no-vectors           # без готовых векторов (посчитаются на платформе)
  python submission/build_zip.py --require-vectors      # ошибка, если векторы получить не удалось
"""

import argparse
import json
import os
import re
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
MODULES = ["config", "parsing", "chunking", "lexical", "embeddings", "index", "retrieval",
           "llm", "prompts", "guards", "verify", "pipeline", "prebuilt", "dates"]


def flatten_imports(source):
    source = re.sub(r"from legal_core\.(\w+) import", r"from lc_\1 import", source)
    return re.sub(r"from legal_core import (\w+)", r"import lc_\1 as \1", source)


def probe_module():
    """Вопросы зонда как .py-модуль (платформа переносит только корневые .py): демо-вопросы организаторов
    с эталоном из demo_output.csv и расширенный набор data/eval/bench.csv. solution.py после записи
    submission.csv отвечает на них, сравнивает с baseline организаторов через LLM-судью и печатает итог в лог."""
    import csv

    def rows(path):
        with open(path, encoding="utf-8-sig") as fh:
            return list(csv.DictReader(fh))

    golden = {r["id"]: r["answer"] for r in rows(ROOT / "data" / "demo" / "demo_output.csv")}
    probe = [("demo", r["id"], r["question"], golden.get(r["id"], "")) for r in rows(ROOT / "data" / "demo" / "demo_questions.csv")]
    # в зонд — только вопросы с эталоном; остальные в bench.csv нужны для проверки поиска (eval/bench_retrieval.py)
    probe += [("bench", r["id"], r["question"], r["golden"]) for r in rows(ROOT / "data" / "eval" / "bench.csv") if r["golden"]]
    return "# Сгенерировано build_zip.py (data/demo, data/eval/bench.csv)\nPROBE = " + repr(probe) + "\n"


def vectors_from_index(folder):
    """{ключ текста: вектор} из снимка index/, если он построен настоящей моделью (не fake)."""
    import numpy as np
    from legal_core.prebuilt import text_key

    folder = Path(folder)
    try:
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        if meta.get("embedder_mode") == "fake" or int(meta.get("dim", 0)) < 512:
            print(f"  index/: векторы режима {meta.get('embedder_mode')} ({meta.get('dim')}) — не подходят")
            return {}, None
        dense = np.load(folder / "dense.npy")
        with open(folder / "chunks.jsonl", encoding="utf-8") as fh:
            chunks = [json.loads(line) for line in fh if line.strip()]
    except (OSError, ValueError) as exc:
        print(f"  index/: нет снимка ({type(exc).__name__})")
        return {}, None
    return {text_key(c["embed_text"]): dense[i] for i, c in enumerate(chunks)}, meta.get("embedding_model")


def vectors_from_api(texts, url, model, batch=16):
    import httpx
    import numpy as np

    out = []
    headers = {"Authorization": f"Bearer {os.environ.get('PREBUILT_EMBED_KEY', 'local')}"}
    with httpx.Client(timeout=600.0) as client:
        for start in range(0, len(texts), batch):
            response = client.post(f"{url.rstrip('/')}/embeddings", headers=headers,
                                   json={"model": model, "input": texts[start:start + batch], "encoding_format": "float"})
            response.raise_for_status()
            data = sorted(response.json()["data"], key=lambda item: item.get("index", 0))
            out.extend(item["embedding"] for item in data)
            print(f"\r  эмбеддинги через API: {len(out)}/{len(texts)}", end="", flush=True)
    print()
    return np.asarray(out, dtype=np.float32)


def vectors_module(kb, index_dir, embed_url):
    """Исходник lc_vectors.py для текущей базы знаний и текущего разбиения на фрагменты, или None."""
    import numpy as np
    from legal_core.chunking import CHUNKER_VERSION, chunk_documents
    from legal_core.config import get_settings
    from legal_core.parsing import read_knowledge_base
    from legal_core.prebuilt import encode_module, text_key

    docs, _ = read_knowledge_base(kb)
    texts = list(dict.fromkeys(c["embed_text"] for c in chunk_documents(docs)))
    keys = [text_key(t) for t in texts]
    known, model = vectors_from_index(index_dir)
    vectors = {k: known[k] for k in keys if k in known}
    print(f"Готовые эмбеддинги: фрагментов {len(texts)}, из index/ — {len(vectors)}")
    missing = [(k, t) for k, t in zip(keys, texts) if k not in vectors]
    model = model or get_settings()["embedding_model"]
    if missing:
        try:
            fresh = vectors_from_api([t for _, t in missing], embed_url, model)
            vectors.update({k: fresh[i] for i, (k, _) in enumerate(missing)})
        except Exception as exc:  # noqa: BLE001
            print(f"  {embed_url}: не ответил ({type(exc).__name__}: {str(exc)[:120]})")
    if len(vectors) < len(keys):
        print(f"ВНИМАНИЕ: готовых векторов {len(vectors)} из {len(keys)}. Остальные будут считаться на платформе "
              f"и тратить её 20 минут. Поднимите прокси (docker compose --profile eval up -d) или пересоберите index/.")
    if not vectors:
        return None
    ordered = [k for k in keys if k in vectors]
    matrix = np.stack([np.asarray(vectors[k], dtype=np.float32) for k in ordered])
    matrix /= np.clip(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-12, None)
    meta = {"embedding_model": model, "chunker_version": CHUNKER_VERSION, "kb": Path(kb).name,
            "built_at": time.strftime("%Y-%m-%d %H:%M"), "complete": len(ordered) == len(keys)}
    return encode_module(ordered, matrix, meta)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "dist" / "submission.zip"))
    parser.add_argument("--kb", default=str(ROOT / "data" / "knowledge-base.zip"))
    parser.add_argument("--index", default=str(ROOT / "index"), help="снимок индекса с векторами bge-m3")
    parser.add_argument("--embed-url", default=os.environ.get("PREBUILT_EMBED_URL", "http://localhost:9100/v1"))
    parser.add_argument("--no-vectors", action="store_true", help="не класть готовые эмбеддинги")
    parser.add_argument("--require-vectors", action="store_true", help="ошибка, если векторы не все")
    args = parser.parse_args()
    vectors = None if args.no_vectors else vectors_module(args.kb, args.index, args.embed_url)
    if args.require_vectors and (vectors is None or "'complete': True" not in vectors[:600]):
        raise SystemExit("Готовые эмбеддинги получены не для всех фрагментов (--require-vectors)")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("solution.py", flatten_imports((ROOT / "submission" / "solution.py").read_text(encoding="utf-8")))
        zf.write(ROOT / "submission" / "requirements.txt", "requirements.txt")
        zf.writestr("lc_probe.py", probe_module())
        zf.writestr("lc_judge.py", flatten_imports((ROOT / "eval" / "judge.py").read_text(encoding="utf-8")))
        zf.write(ROOT / "eval" / "baseline_solution.py", "lc_baseline.py")
        if vectors:
            zf.writestr("lc_vectors.py", vectors)
        for name in MODULES:
            source = (ROOT / "legal_core" / f"{name}.py").read_text(encoding="utf-8")
            zf.writestr(f"lc_{name}.py", flatten_imports(source))
    leftovers = [n for n in zipfile.ZipFile(out).namelist() if n not in ("solution.py", "lc_vectors.py")
                 and re.search(r"from legal_core|import legal_core", zipfile.ZipFile(out).read(n).decode("utf-8"))]
    if leftovers:
        raise SystemExit(f"Остались импорты legal_core в: {leftovers}")
    print(f"Архив: {out} ({out.stat().st_size / 1024:.0f} КБ), файлов: {len(zipfile.ZipFile(out).namelist())}")


if __name__ == "__main__":
    main()
