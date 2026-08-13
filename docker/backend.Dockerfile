# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/pyproject.toml backend/README.md /app/backend/
COPY backend/tradeloom/__init__.py /app/backend/tradeloom/__init__.py
RUN pip install --upgrade pip && pip install -e "/app/backend[worker]"

COPY backend /app/backend
COPY worker /app/worker
COPY shared /app/shared
COPY data /app/data
COPY docker/entrypoint-api.sh docker/entrypoint-worker.sh /app/docker/
RUN chmod +x /app/docker/entrypoint-api.sh /app/docker/entrypoint-worker.sh

ENV PYTHONPATH=/app:/app/backend

RUN useradd --create-home --uid 10001 tradeloom && chown -R tradeloom:tradeloom /app
USER tradeloom

EXPOSE 8000
CMD ["/app/docker/entrypoint-api.sh"]
