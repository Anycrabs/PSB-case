"""Настройки из переменных окружения. Один словарь, без классов.

Имена переменных совпадают с теми, что передаёт платформа хакатона (LLM_BASE_URL, LLM_API_KEY,
LLM_MODEL, EMB_MODEL, RERANKER_MODEL, TEMPERATURE, MAX_TOKENS, MAX_WORKERS, DATA_DIR),
с запасными именами из регламента (OPENAI_*, GEN_MODEL, BASE_URL).
"""

import os


def _env(*names, default=""):
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value and value != name:
            return value
    return default


def _int(names, default):
    try:
        return int(_env(*names, default=str(default)))
    except ValueError:
        return default


def _float(names, default):
    try:
        return float(_env(*names, default=str(default)))
    except ValueError:
        return default


def get_settings():
    llm_base = _env("LLM_BASE_URL", "OPENAI_BASE_URL", "BASE_URL", default="https://api.deepseek.com").rstrip("/")
    llm_key = _env("LLM_API_KEY", "OPENAI_API_KEY")
    return {
        # Языковая модель: прокси платформы или любой OpenAI-совместимый API
        "llm_base_url": llm_base,
        "llm_api_key": llm_key,
        "llm_model": _env("LLM_MODEL", "OPENAI_MODEL", "GEN_MODEL", default="deepseek/deepseek-v4.1-flash"),
        "llm_timeout": _float(["LLM_TIMEOUT"], 90.0),
        "llm_temperature": _float(["TEMPERATURE", "LLM_TEMPERATURE"], 0.0),
        "llm_max_tokens": max(_int(["MAX_TOKENS"], 3000), 3000),
        "llm_json_mode": _env("LLM_JSON_MODE", default="1") == "1",
        # Эмбеддинги: remote — /embeddings прокси (по умолчанию тот же адрес и ключ, что у LLM);
        # local — FlagEmbedding в процессе; fake — для тестов
        "embedder_mode": _env("EMBEDDER_MODE", default="remote"),
        "embedding_model": _env("EMB_MODEL", "EMBEDDING_MODEL", default="baai/bge-m3"),
        "embedding_base_url": _env("EMBEDDING_BASE_URL", default=llm_base).rstrip("/"),
        "embedding_api_key": _env("EMBEDDER_API_KEY", "EMBEDDING_API_KEY", default=llm_key),
        "embedding_batch": _int(["EMBEDDING_BATCH"], 32),
        "embedding_max_length": _int(["EMBEDDING_MAX_LENGTH"], 2048),
        "embedding_fp16": _env("EMBEDDING_FP16", default="0") == "1",
        # Реранкер: включён, если задана модель (на платформе — RERANKER_MODEL)
        "reranker_model": _env("RERANKER_MODEL"),
        "reranker_url": _env("RERANKER_URL", default=llm_base).rstrip("/"),
        "reranker_api_key": _env("RERANKER_API_KEY", default=llm_key),
        # Индекс и поиск (свои имена: TOP_K / RERANKER_TOP_N платформы рассчитаны на учебный пример)
        "index_dir": _env("INDEX_DIR", default="index"),
        "data_dir": _env("DATA_DIR", default="data"),
        "retrieval_backend": _env("RETRIEVAL_BACKEND", default="local"),  # local | qdrant
        "qdrant_url": _env("QDRANT_URL", default="http://localhost:6333"),
        "qdrant_collection": _env("QDRANT_COLLECTION", default="legal_chunks"),
        "top_k": _int(["SEARCH_K"], 40),
        "rerank_k": _int(["RERANK_K"], 30),
        "context_chunks": _int(["CONTEXT_CHUNKS"], 8),
        "context_chars": _int(["CONTEXT_CHARS"], 14000),
        "rrf_k": _int(["RRF_K"], 60),
        # Конвейер: fast — 1 вызов LLM; auto — + проверка ответа; full — + разбор вопроса
        "pipeline_mode": _env("PIPELINE_MODE", default="auto"),
        "max_workers": _int(["MAX_WORKERS"], 4),
        "time_budget_s": _int(["TIME_BUDGET_S"], 1080),  # лимит платформы 20 мин, оставляем запас
        # Адреса сервисов внутри сети Docker
        "embedder_url": _env("EMBEDDER_URL", default="http://localhost:8003").rstrip("/"),
        "retrieval_url": _env("RETRIEVAL_URL", default="http://localhost:8002").rstrip("/"),
        "orchestrator_url": _env("ORCHESTRATOR_URL", default="http://localhost:8001").rstrip("/"),
        "service_timeout": _float(["SERVICE_TIMEOUT"], 120.0),
    }
