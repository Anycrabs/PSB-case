"""Гибридный поиск: dense + лексический канал + точные ссылки, слияние RRF, реранкер, сборка контекста.

Функция эмбеддинга передаётся параметром (embed_fn), поэтому один и тот же код работает
и внутри сервиса retrieval (эмбеддинги по HTTP из embedder), и в solution.py (в процессе).
"""

import re

from legal_core.index import allowed_mask, qdrant_search, search_dense, search_lexical

ARTICLE_REF_RE = re.compile(r"(?:\bст\.?|\bстать[яиеюей]+)\s*(\d+(?:\.\d+)*)", re.I)
POINT_REF_RE = re.compile(r"(?:\bп\.?|\bпункт[аеуом]*)\s*(\d+(?:\.\d+)*)", re.I)
CIT_ARTICLE_RE = re.compile(r"ст\. ([\d.]+)")
CIT_POINT_RE = re.compile(r"п\. ([\d.]+)")


def extract_references(text):
    """Номера статей и пунктов, упомянутые в вопросе: {'articles': [...], 'points': [...]}."""
    return {
        "articles": sorted(set(ARTICLE_REF_RE.findall(text))),
        "points": sorted(set(POINT_REF_RE.findall(text))),
    }


def citation_matches(citation, refs):
    article = CIT_ARTICLE_RE.search(citation)
    point = CIT_POINT_RE.search(citation)
    article = article.group(1) if article else None
    point = point.group(1) if point else None
    if refs["articles"] and article not in refs["articles"]:
        return False
    if refs["points"]:
        return point is not None and any(point == p or point.startswith(p + ".") for p in refs["points"])
    return bool(refs["articles"])


def search_exact(index, refs, allowed, k=10):
    if not refs["articles"] and not refs["points"]:
        return []
    hits = [i for i, c in enumerate(index["chunks"]) if allowed[i] and citation_matches(c["citation"], refs)]
    return [(i, 1.0) for i in hits[:k]]


def rrf_fuse(ranked_lists, k=60, weights=None):
    """Reciprocal Rank Fusion: score = Σ w / (k + rank)."""
    weights = weights or [1.0] * len(ranked_lists)
    scores = {}
    for ranked, weight in zip(ranked_lists, weights):
        for rank, (idx, _) in enumerate(ranked):
            scores[idx] = scores.get(idx, 0.0) + weight / (k + rank + 1)
    return sorted(scores.items(), key=lambda item: -item[1])


def rerank(query, candidates, index, settings):
    """POST {RERANKER_URL}/rerank (формат Cohere/Jina, как в примере организаторов).
    Включается, если задана модель RERANKER_MODEL. При любой ошибке остаётся порядок RRF."""
    if not settings["reranker_model"] or not candidates:
        return candidates
    import httpx

    head = candidates[: settings["rerank_k"]]
    documents = [index["chunks"][c["idx"]]["embed_text"][:3000] for c in head]
    headers = {"Authorization": f"Bearer {settings['reranker_api_key']}"} if settings["reranker_api_key"] else {}
    try:
        response = httpx.post(
            f"{settings['reranker_url']}/rerank",
            json={"model": settings["reranker_model"], "query": query, "documents": documents, "top_n": len(head)},
            headers=headers,
            timeout=60.0,
        )
        response.raise_for_status()
        body = response.json()
        results = body.get("results", body.get("data", []))
    except Exception as exc:  # noqa: BLE001 — реранкер необязателен
        print(f"Реранкер недоступен: {type(exc).__name__}", flush=True)
        return candidates
    rescored, seen = [], set()
    for position, item in enumerate(results):
        i = int(item.get("index", -1))
        if 0 <= i < len(head) and i not in seen:
            seen.add(i)
            score = item.get("relevance_score", item.get("score"))
            rescored.append({**head[i], "rerank_score": float(score) if score is not None else 1.0 / (position + 1)})
    rescored += [c for i, c in enumerate(head) if i not in seen]
    return rescored + candidates[settings["rerank_k"]:]


def hybrid_search(index, query, embed_fn, settings, filters=None, qdrant=None):
    """Возвращает кандидатов [{'idx', 'chunk_id', 'score', 'channels', 'rerank_score'?}]."""
    k = settings["top_k"]
    allowed = allowed_mask(index, filters)
    emb = embed_fn([query])
    query_vec = emb["dense"][0]
    query_weights = emb["sparse"][0] if emb.get("sparse") else None
    if not index["meta"].get("has_sparse"):
        query_weights = None

    if qdrant is not None:
        semantic = qdrant_search(qdrant["client"], qdrant["collection"], query_vec, query_weights, filters, k)
        lexical = search_lexical(index, query, None, allowed, k) if query_weights is None else []
    else:
        semantic = search_dense(index, query_vec, allowed, k)
        lexical = search_lexical(index, query, query_weights, allowed, k)
    exact = search_exact(index, extract_references(query), allowed)

    channels = {"semantic": semantic, "lexical": lexical, "exact": exact}
    fused = rrf_fuse([semantic, lexical, exact], k=settings["rrf_k"], weights=[1.0, 1.0, 2.0])
    found_in = {name: {i for i, _ in hits} for name, hits in channels.items()}
    candidates = [
        {
            "idx": idx,
            "chunk_id": index["chunks"][idx]["chunk_id"],
            "score": score,
            "channels": [name for name, ids in found_in.items() if idx in ids],
        }
        for idx, score in fused[:k]
    ]
    return rerank(query, candidates, index, settings)


def linked_supplements(index, chosen_idx):
    """Доп. соглашения к договорам, которые уже попали в контекст: изменения условий нельзя терять."""
    numbers = {index["chunks"][i].get("contract_no") for i in chosen_idx} - {None}
    return [i for i, c in enumerate(index["chunks"]) if c.get("amends") in numbers and i not in chosen_idx]


def context_item(index, idx, label, cand=None):
    chunk = index["chunks"][idx]
    return {
        "label": label,
        "chunk_id": chunk["chunk_id"],
        "doc_id": chunk["doc_id"],
        "doc_title": chunk["doc_title"],
        "doc_display": chunk.get("doc_display", chunk["doc_title"]),
        "doc_type": chunk["doc_type"],
        "source_file": chunk.get("source_file", ""),
        "citation": chunk["citation"],
        "struct_path": chunk["struct_path"],
        "edition": chunk.get("edition", ""),
        "is_current": chunk.get("is_current", True),
        "valid_from": chunk.get("valid_from"),
        "valid_to": chunk.get("valid_to"),
        "char_span": chunk["char_span"],
        "text": chunk["text"],
        "score": (cand or {}).get("rerank_score", (cand or {}).get("score", 0.0)),
        "channels": (cand or {}).get("channels", ["linked"]),
    }


WHOLE_DOC_CHARS = 6000  # короткие документы компании (договор, регламент, письмо) отдаём целиком
WHOLE_DOC_TOP = 3       # …если документ в первых WHOLE_DOC_TOP результатах поиска


def doc_chunks(index, doc_id):
    return [i for i, c in enumerate(index["chunks"]) if c["doc_id"] == doc_id]


def build_context(index, candidate_lists, settings):
    """Контекст для LLM: лучшие фрагменты по каждому запросу без дублей; короткие документы компании
    из верхних результатов — целиком; связанные доп. соглашения; документный порядок, метки S1..Sn."""
    per_query = settings["context_chunks"]
    budget = settings["context_chars"]
    chosen, seen = [], set()

    def add(idx, cand=None):
        if idx not in seen:
            seen.add(idx)
            chosen.append(cand or {"idx": idx, "score": 0.0, "channels": ["same_doc"]})

    # 1) лучшие результаты всех запросов вперемешку по рангу: rank 0 каждого запроса, затем rank 1…
    for rank in range(per_query):
        for candidates in candidate_lists:
            if rank < len(candidates):
                add(candidates[rank]["idx"], candidates[rank])
    # 2) короткие документы компании из верхних результатов — целиком
    for candidates in candidate_lists:
        for cand in candidates[:WHOLE_DOC_TOP]:
            chunk = index["chunks"][cand["idx"]]
            if chunk["doc_type"] == "law":
                continue
            siblings = doc_chunks(index, chunk["doc_id"])
            if sum(len(index["chunks"][i]["text"]) for i in siblings) <= WHOLE_DOC_CHARS:
                for i in siblings:
                    add(i)
    for i in linked_supplements(index, seen):
        add(i, {"idx": i, "score": 0.0, "channels": ["linked"]})

    selected, used = [], 0
    for cand in chosen:  # порядок важности: сначала лучшие по поиску
        size = len(index["chunks"][cand["idx"]]["text"])
        if used + size > budget and selected:
            continue
        used += size
        selected.append(cand)
    selected.sort(key=lambda c: (index["chunks"][c["idx"]]["doc_id"], c["idx"]))
    return [context_item(index, c["idx"], f"S{n}", c) for n, c in enumerate(selected, 1)]


def retrieve_context(index, queries, embed_fn, settings, filters=None, qdrant=None):
    """Поиск по всем подвопросам + сборка контекста. Это и есть «подгрузка в LLM» (get_context)."""
    candidate_lists = [hybrid_search(index, q, embed_fn, settings, filters, qdrant) for q in queries]
    return build_context(index, candidate_lists, settings)
