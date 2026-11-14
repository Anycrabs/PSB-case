"""Строит снимок индекса из папки с документами базы знаний.

Пример:
  python scripts/build_index.py --data data/knowledge-base.zip --out index
  python scripts/build_index.py --data data/knowledge-base.zip --out index --qdrant http://localhost:6333
--data принимает ZIP-архив базы знаний или папку с документами.
"""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from legal_core.chunking import chunk_documents  # noqa: E402
from legal_core.config import get_settings  # noqa: E402
from legal_core.embeddings import embed_texts_batched  # noqa: E402
from legal_core.index import build_index, qdrant_client, qdrant_upload, save_index  # noqa: E402
from legal_core.parsing import read_knowledge_base  # noqa: E402
from legal_core.chunking import CHUNKER_VERSION  # noqa: E402


def data_fingerprint(folder):
    digest = hashlib.sha1()
    if Path(folder).is_file():
        digest.update(Path(folder).read_bytes())
        return digest.hexdigest()
    for path in sorted(Path(folder).rglob("*")):
        if path.is_file():
            stat = path.stat()
            digest.update(f"{path.relative_to(folder)}:{stat.st_size}:{int(stat.st_mtime)}".encode())
    return digest.hexdigest()


def is_up_to_date(out, fingerprint, settings):
    meta_path = Path(out) / "meta.json"
    if not meta_path.exists():
        return False
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    return (
        meta.get("data_fingerprint") == fingerprint
        and meta.get("embedding_model") == settings["embedding_model"]
        and meta.get("embedder_mode") == settings["embedder_mode"]
        and meta.get("chunker_version") == CHUNKER_VERSION
    )


def progress(done, total):
    print(f"\r  эмбеддинги: {done}/{total}", end="", flush=True)


def main():
    settings = get_settings()
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data/knowledge-base.zip")
    parser.add_argument("--out", default=settings["index_dir"])
    parser.add_argument("--qdrant", default=None, help="URL Qdrant, чтобы сразу загрузить индекс")
    parser.add_argument("--skip-existing", action="store_true", help="не пересобирать, если данные не менялись")
    args = parser.parse_args()

    fingerprint = data_fingerprint(args.data)
    if args.skip_existing and is_up_to_date(args.out, fingerprint, settings):
        print(f"Индекс в {args.out} актуален, пересборка не нужна")
        return

    started = time.time()
    docs, errors = read_knowledge_base(args.data)
    print(f"Документов: {len(docs)}, ошибок чтения: {len(errors)}")
    for err in errors:
        print("  !", err["file"], "—", err["error"])
    chunks = chunk_documents(docs)
    print(f"Фрагментов: {len(chunks)} (режим эмбеддингов: {settings['embedder_mode']}, модель: {settings['embedding_model']})")

    embeddings = embed_texts_batched([c["embed_text"] for c in chunks], settings, progress=progress)
    print()
    index = build_index(chunks, embeddings, settings)
    index["meta"]["data_fingerprint"] = fingerprint
    save_index(index, args.out)

    out = Path(args.out)
    with open(out / "docs.jsonl", "w", encoding="utf-8") as fh:
        for doc in docs:
            fh.write(json.dumps(doc, ensure_ascii=False) + "\n")
    (out / "errors.json").write_text(json.dumps(errors, ensure_ascii=False, indent=1), encoding="utf-8")

    if args.qdrant:
        qdrant_upload(qdrant_client({**settings, "qdrant_url": args.qdrant}), index, settings["qdrant_collection"])
        print(f"Загружено в Qdrant: {args.qdrant}, коллекция {settings['qdrant_collection']}")
    print(f"Готово за {time.time() - started:.1f} с → {out.resolve()}")


if __name__ == "__main__":
    main()
