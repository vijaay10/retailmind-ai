# RetailMind API image — one image, four entrypoints (api|migrate|worker|beat).
# DevOps design §1: multi-stage, non-root, slim, SHA-tagged in CI.

# ── Builder ───────────────────────────────────────────
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /build
COPY pyproject.toml uv.lock* ./
COPY backend/pyproject.toml backend/
COPY data_platform/pyproject.toml data_platform/
RUN uv sync --frozen --no-dev --package retailmind-backend --no-install-workspace || \
    uv sync --no-dev --package retailmind-backend --no-install-workspace
COPY backend/app backend/app
RUN uv sync --no-dev --package retailmind-backend

# ── Runtime ───────────────────────────────────────────
FROM python:3.12-slim AS runtime
LABEL org.opencontainers.image.source="https://github.com/OWNER/retailmind-ai" \
      org.opencontainers.image.title="retailmind/api"

RUN groupadd -g 10001 app && useradd -u 10001 -g app -m app
WORKDIR /srv
COPY --from=builder --chown=app:app /build/.venv /srv/.venv
COPY --chown=app:app backend/app /srv/app
COPY --chown=app:app infra/docker/api-entrypoint.sh /srv/entrypoint.sh
RUN chmod +x /srv/entrypoint.sh

ENV PATH="/srv/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER app
EXPOSE 8000
ENTRYPOINT ["/srv/entrypoint.sh"]
CMD ["api"]
