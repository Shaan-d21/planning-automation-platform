# syntax=docker/dockerfile:1.7


FROM node:22.13.1-alpine AS frontend-build

WORKDIR /build/frontend

COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN corepack enable \
    && corepack prepare pnpm@11.16.0 --activate \
    && pnpm install --frozen-lockfile

COPY frontend/ ./
RUN pnpm build


FROM python:3.13-slim AS application

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system epm \
    && useradd --system --gid epm --home-dir /app --shell /usr/sbin/nologin epm

COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt

COPY --chown=epm:epm . ./
COPY --from=frontend-build --chown=epm:epm \
    /build/frontend/dist ./frontend/dist

RUN mkdir -p /app/var /app/reports \
    && chown -R epm:epm /app/var /app/reports

USER epm

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/ready', timeout=4).read()"]

CMD ["python", "-m", "uvicorn", "web_main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
