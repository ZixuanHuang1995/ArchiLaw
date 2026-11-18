FROM python:3.12-slim-bookworm

WORKDIR /app
ENV UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    CUDA_VISIBLE_DEVICES="" \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv \
    && uv venv /app/.venv

# CPU torch. The lockfile on the dev machine resolved a CUDA wheel, which
# is too large for this demo and is not used (there is no GPU).
RUN uv pip install --python /app/.venv/bin/python \
    torch --index-url https://download.pytorch.org/whl/cpu

COPY pyproject.toml README.md ./
COPY twbc_rag ./twbc_rag
COPY config ./config
COPY alembic.ini ./
COPY alembic ./alembic

RUN uv pip install --python /app/.venv/bin/python \
    "sentence-transformers>=3" \
    "fastapi" \
    "uvicorn[standard]" \
    "streamlit" \
    "jieba" \
    "rank-bm25" \
    "qdrant-client" \
    "sqlalchemy>=2" \
    "psycopg[binary]" \
    "pyyaml" \
    "beautifulsoup4" \
    "alembic" \
    "httpx" \
    "typer" \
    && uv pip install --python /app/.venv/bin/python --no-deps .

EXPOSE 8077 8571
CMD ["uvicorn", "twbc_rag.api.app:app", "--host", "0.0.0.0", "--port", "8077"]
