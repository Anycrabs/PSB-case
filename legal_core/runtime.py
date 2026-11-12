"""Сборка зависимостей конвейера внутри одного процесса (solution.py, CLI, тесты)."""

from legal_core.embeddings import embed_texts
from legal_core.llm import chat_json
from legal_core.retrieval import retrieve_context


def make_local_deps(settings, index, embed_fn=None, llm_json=None, qdrant=None):
    embed_fn = embed_fn or (lambda texts: embed_texts(texts, settings))
    llm_json = llm_json or (lambda messages, max_tokens, default: chat_json(messages, settings, max_tokens, default))
    return {
        "llm_json": llm_json,
        "retrieve": lambda queries, filters: retrieve_context(index, queries, embed_fn, settings, filters, qdrant),
    }
