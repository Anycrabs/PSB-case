"""Однократная загрузка модели эмбеддингов в общий кэш /models (том hf-cache) — сервис model-init.

Раньше модель при первом запуске одновременно скачивали indexer (от root) и embedder (от пользователя app):
файлы в томе получали владельца root, и embedder падал с PermissionError на lock-файлах кэша Hugging Face.
Теперь model-init от root только выставляет владельца тома, а скачивает модель уже от app, один раз;
indexer и embedder стартуют после него и читают готовый кэш.

  python scripts/prepare_models.py           # EMBEDDER_MODE=local: скачать EMB_MODEL, иначе ничего не делать
"""

import os
import sys
import time
from pathlib import Path


def main():
    mode = os.environ.get("EMBEDDER_MODE", "remote").strip()
    model = (os.environ.get("EMB_MODEL") or os.environ.get("EMBEDDING_MODEL") or "BAAI/bge-m3").strip()
    cache = Path(os.environ.get("HF_HOME", "/models"))
    if mode != "local":
        print(f"EMBEDDER_MODE={mode}: модель в контейнер не нужна, загрузка пропущена")
        return 0
    probe = cache / ".write-test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        print(f"Нет доступа на запись в {cache} ({exc}). Пересоздайте том: docker compose down && "
              f"docker volume rm legal-assistant_hf-cache", file=sys.stderr)
        return 1

    from huggingface_hub import snapshot_download

    for attempt in range(1, 4):
        try:
            started = time.time()
            print(f"Загрузка {model} в {cache} (попытка {attempt}); первый раз ~4.5 ГБ, дальше — из кэша", flush=True)
            # те же исключения, что у FlagEmbedding: иначе в офлайн-режиме он считает снимок неполным
            # (IncompleteSnapshotError при запуске indexer/embedder)
            path = snapshot_download(model, ignore_patterns=["flax_model.msgpack", "rust_model.ot", "tf_model.h5"])
            print(f"Модель готова: {path} ({time.time() - started:.0f} с)")
            return 0
        except Exception as exc:  # noqa: BLE001 — сеть до huggingface.co бывает нестабильной
            print(f"Не удалось скачать {model}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            time.sleep(10 * attempt)
    print("Проверьте доступ к huggingface.co (или задайте HF_ENDPOINT зеркала в .env)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
