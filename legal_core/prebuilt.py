"""Готовые эмбеддинги базы знаний внутри сабмита.

Регламент разрешает положить индекс в архив, но в среду проверки переносятся только корневые .py-файлы,
поэтому векторы лежат в модуле lc_vectors.py: ключ фрагмента (sha1 его embed_text) и матрица float16,
сжатая zlib и закодированная base64. Модуль собирает submission/build_zip.py.

При запуске solution.py:
  1. разбивает базу на фрагменты как обычно (это быстро) и находит для каждого готовый вектор по ключу;
  2. сверяет 3 случайных готовых вектора с ответом /embeddings платформы: если модель та же (косинус ≥ 0.95),
     векторы используются, иначе (другая модель или размерность) пересчитываются;
  3. досчитывает только фрагменты, которых нет в готовом наборе (если база на платформе отличается),
     параллельно и с крайним сроком: не успели — фрагмент ищется только по BM25.
"""

import base64
import hashlib
import zlib

import numpy as np

COMPAT_MIN_COSINE = 0.95
LINE = 4000


def text_key(text):
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:20]


def encode_module(keys, matrix, meta):
    """Исходник модуля lc_vectors.py: META, KEYS (через пробел), VECTORS (base64 от zlib(float16))."""
    matrix = np.asarray(matrix, dtype=np.float16)
    blob = base64.b64encode(zlib.compress(matrix.tobytes(), 9)).decode("ascii")
    lines = "\n".join(f"    {blob[i:i + LINE]!r}" for i in range(0, len(blob), LINE))
    meta = {**meta, "count": int(matrix.shape[0]), "dim": int(matrix.shape[1])}
    return (
        "# Сгенерировано submission/build_zip.py: готовые эмбеддинги фрагментов базы знаний (читает legal_core/prebuilt.py)\n"
        f"META = {meta!r}\n"
        f"KEYS = {' '.join(keys)!r}\n"
        f"VECTORS = (\n{lines}\n)\n"
    )


def load_store(module):
    """{'meta', 'rows': {key: номер строки}, 'matrix': float32 n × dim (нормирована)} или None."""
    try:
        meta = dict(module.META)
        keys = module.KEYS.split()
        raw = zlib.decompress(base64.b64decode(module.VECTORS))
        matrix = np.frombuffer(raw, dtype=np.float16).astype(np.float32).reshape(meta["count"], meta["dim"])
    except Exception as exc:  # noqa: BLE001 — испорченный модуль не должен ронять прогон
        print(f"Готовые эмбеддинги не прочитаны: {type(exc).__name__}: {exc}", flush=True)
        return None
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = matrix / np.clip(norms, 1e-12, None)
    return {"meta": meta, "rows": {k: i for i, k in enumerate(keys)}, "matrix": matrix}


def compatibility(store, texts_and_rows, embed_fn):
    """Средний косинус готовых векторов и свежих от API. None — API не ответил (проверить нельзя)."""
    texts = [text for text, _ in texts_and_rows]
    try:
        fresh = np.asarray(embed_fn(texts)["dense"], dtype=np.float32)
    except Exception as exc:  # noqa: BLE001
        print(f"Проверка готовых эмбеддингов: API не ответил ({type(exc).__name__})", flush=True)
        return None, None
    if fresh.ndim != 2 or fresh.shape[1] != store["matrix"].shape[1]:
        return 0.0, fresh.shape[-1] if fresh.ndim == 2 else None
    stored = store["matrix"][[row for _, row in texts_and_rows]]
    fresh = fresh / np.clip(np.linalg.norm(fresh, axis=1, keepdims=True), 1e-12, None)
    return float(np.mean(np.sum(fresh * stored, axis=1))), fresh.shape[1]


def attach_vectors(chunks, store, embed_fn, embed_missing, log):
    """Матрица dense-векторов для chunks из готового набора + досчёт недостающих.
    embed_fn(texts) -> {'dense': ...} — один запрос к API (для проверки совместимости);
    embed_missing(texts) -> (матрица или None, сколько посчитано) — с крайним сроком.
    Возвращает (матрица или None, описание для лога)."""
    texts = [c["embed_text"] for c in chunks]
    rows = [store["rows"].get(text_key(t)) if store else None for t in texts]
    have = [i for i, row in enumerate(rows) if row is not None]
    use_store = bool(have)
    note = "готовых векторов нет"
    if have:
        sample = [have[int(j)] for j in np.linspace(0, len(have) - 1, num=min(3, len(have)))]
        cosine, _ = compatibility(store, [(texts[i], rows[i]) for i in sample], embed_fn)
        if cosine is None:
            note = f"готовые: {len(have)}/{len(chunks)}, API не ответил — используем готовые без проверки"
        elif cosine >= COMPAT_MIN_COSINE:
            note = f"готовые: {len(have)}/{len(chunks)}, совпадают с API (косинус {cosine:.3f})"
        else:
            use_store = False
            note = f"готовые векторы от другой модели (косинус {cosine:.3f}) — пересчитываем все"
    log(f"Эмбеддинги: {note}")

    if use_store:
        matrix = np.zeros((len(chunks), store["matrix"].shape[1]), dtype=np.float32)
        for i in have:
            matrix[i] = store["matrix"][rows[i]]
        missing = [i for i, row in enumerate(rows) if row is None]
    else:
        matrix, missing = None, list(range(len(chunks)))
    if not missing:
        return matrix, note

    log(f"Досчитываю эмбеддинги: {len(missing)} фрагментов")
    fresh, done = embed_missing([texts[i] for i in missing])
    if fresh is None:
        log("Эмбеддинги для недостающих фрагментов не получены: они ищутся только по BM25")
        return matrix, note
    if matrix is None:
        matrix = np.zeros((len(chunks), fresh.shape[1]), dtype=np.float32)
    if fresh.shape[1] != matrix.shape[1]:
        log(f"Размерность API ({fresh.shape[1]}) не совпала с готовыми векторами: недостающие — только BM25")
        return matrix, note
    for j, i in enumerate(missing):
        matrix[i] = fresh[j]
    log(f"Досчитано {done}/{len(missing)}" + ("" if done == len(missing) else "; остальные — только BM25"))
    return matrix, note
