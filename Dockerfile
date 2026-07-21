# syntax=docker/dockerfile:1.7

FROM node:22-alpine AS web-builder
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS python-builder
ARG PIP_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cpu
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_EXTRA_INDEX_URL=${PIP_EXTRA_INDEX_URL}
WORKDIR /build
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN python -m pip wheel --wheel-dir /wheels .

FROM python:3.12-slim-bookworm AS runtime
ARG PIP_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cpu
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_EXTRA_INDEX_URL=${PIP_EXTRA_INDEX_URL} \
    GEOCHEM_WEB_DIST=/app/web/dist \
    GEOCHEM_CONFIG=/data/config/settings.local.yaml

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        libmagic1 \
        poppler-utils \
        tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=python-builder /wheels /wheels
RUN python -m pip install /wheels/* \
    && rm -rf /wheels
COPY alembic.ini ./
COPY migrations/ ./migrations/
COPY config/settings.example.yaml ./config/settings.example.yaml
COPY config/prompts/ ./config/prompts/
COPY config/skills/ ./config/skills/
COPY --from=web-builder /build/web/dist ./web/dist

RUN groupadd --gid 10001 geochem \
    && useradd --uid 10001 --gid geochem --create-home --shell /usr/sbin/nologin geochem \
    && mkdir -p /data/config /data/workspaces /data/exports \
    && chown -R geochem:geochem /data

USER geochem
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD curl --fail --silent http://127.0.0.1:8765/api/v1/health >/dev/null || exit 1

CMD ["geochem-web", "--no-browser"]
