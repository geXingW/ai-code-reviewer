FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PIP_TRUSTED_HOST=mirrors.aliyun.com \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=5 \
    PYTHONPATH=/app

WORKDIR /app

RUN addgroup --gid 1000 appuser \
    && adduser --disabled-password --gecos "" --uid 1000 --gid 1000 appuser

# Pre-install pip upgrade (no build tooling needed for requirements.txt install).
RUN pip install --upgrade pip

# Install dependencies first (better cache)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY main.py __main__.py ./
COPY api ./api
COPY core ./core
COPY engines ./engines
COPY integrations ./integrations
COPY llm ./llm
COPY models ./models
COPY repositories ./repositories
COPY schemas ./schemas
COPY services ./services
COPY static ./static

COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY scripts ./scripts

# Generate release SQL artifacts (full schema + incremental migrations)
RUN python scripts/generate_release_sql.py --output-dir /app/sql

# app.py as entrypoint
COPY app.py ./app.py

RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["python", "app.py", "--host", "0.0.0.0", "--port", "8000", "--migrate"]
