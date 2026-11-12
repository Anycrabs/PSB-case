FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models

WORKDIR /app
COPY requirements.txt requirements-embeddings.txt ./
RUN pip install -r requirements.txt

# Только для embedder и indexer: torch (CPU) + FlagEmbedding для BAAI/bge-m3
ARG WITH_EMBEDDINGS=0
RUN if [ "$WITH_EMBEDDINGS" = "1" ]; then \
      pip install torch --index-url https://download.pytorch.org/whl/cpu && \
      pip install -r requirements-embeddings.txt ; \
    fi

COPY legal_core ./legal_core
COPY services ./services
COPY scripts ./scripts
COPY devtools ./devtools

RUN useradd --create-home app && mkdir -p /models /index && chown -R app /app /models /index
USER app

ARG SERVICE=gateway
ENV SERVICE=${SERVICE} PORT=8000
CMD ["sh", "-c", "uvicorn services.${SERVICE}.main:app --host 0.0.0.0 --port ${PORT}"]
