FROM python:3.12-slim-bookworm

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=pypi.org
ARG PIP_FALLBACK_INDEX_URL=https://pypi.org/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=60 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST} \
    HF_HUB_DISABLE_TELEMETRY=1 \
    TOKENIZERS_PARALLELISM=false

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates git git-lfs gnupg unixodbc-dev build-essential \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
       | gpg --dearmor -o /etc/apt/keyrings/microsoft-prod.gpg \
    && curl -fsSL https://packages.microsoft.com/config/debian/12/prod.list \
       | sed 's#signed-by=/usr/share/keyrings/microsoft-prod.gpg#signed-by=/etc/apt/keyrings/microsoft-prod.gpg#' \
       > /etc/apt/sources.list.d/microsoft-prod.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && git lfs install --system \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/app
COPY pyproject.toml README.md alembic.ini docker-entrypoint.sh ./
COPY app ./app
COPY alembic ./alembic
RUN (python -m pip install --upgrade pip \
     || python -m pip install \
        --index-url "${PIP_FALLBACK_INDEX_URL}" \
        --upgrade pip) \
    && (python -m pip install \
        'huggingface-hub>=0.27,<1' \
        'onnxruntime>=1.20,<2' \
        'transformers>=4.41,<5' \
        || python -m pip install \
        --index-url "${PIP_FALLBACK_INDEX_URL}" \
        'huggingface-hub>=0.27,<1' \
        'onnxruntime>=1.20,<2' \
        'transformers>=4.41,<5') \
    && python -m pip install \
       --index-url "${PIP_FALLBACK_INDEX_URL}" \
       '.[postgres,reranker-onnx]'

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data/index /data/docs /data/audit /model-cache \
    && chmod +x /srv/app/docker-entrypoint.sh \
    && chown -R appuser:appuser /data /model-cache /srv/app
USER appuser

EXPOSE 8000
ENTRYPOINT ["/srv/app/docker-entrypoint.sh"]
CMD ["uvicorn", "nl2sql.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-server-header", "--limit-concurrency", "100", "--timeout-keep-alive", "5"]
