# RetailMind API image — one image, four entrypoints (api|migrate|worker|beat).
# DevOps design §1: multi-stage, non-root, slim, SHA-tagged in CI.
#
# Path discipline: the venv is BUILT at the same absolute path it RUNS at
# (/srv/.venv). Console scripts bake an absolute shebang, so a venv built in
# one directory and copied elsewhere fails with a misleading
# "exec: uvicorn: not found". Keep UV_PROJECT_ENVIRONMENT and the runtime
# layout in sync.

# ── Builder ───────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/srv/.venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /srv
# Workspace resolution needs every member's manifest, even ones we don't install.
COPY pyproject.toml uv.lock* ./
COPY data_platform/pyproject.toml data_platform/
COPY backend/pyproject.toml backend/
# Dependencies first (cached layer), then the source that changes every commit.
RUN uv sync --no-dev --package retailmind-backend --no-install-workspace
COPY backend/ backend/
RUN uv sync --no-dev --package retailmind-backend

# ── Runtime ───────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime
LABEL org.opencontainers.image.source="https://github.com/OWNER/retailmind-ai" \
      org.opencontainers.image.title="retailmind/api"

# The base image ships with known-fixable OS vulnerabilities (9 HIGH at the
# time of writing, e.g. bsdutils CVE-2026-53615). Upgrading here picks up the
# distro's fixed versions; without it the image scan gate fails on packages
# this project neither chose nor uses directly.
RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -g 10001 app && useradd -u 10001 -g app -m app

COPY --from=builder --chown=app:app /srv/.venv /srv/.venv
COPY --chown=app:app backend/ /srv/backend/
COPY --chown=app:app infra/docker/api-entrypoint.sh /srv/entrypoint.sh
RUN chmod +x /srv/entrypoint.sh

# cwd holds app/ (import root) and alembic.ini — both entrypoint commands
# resolve relative to it.
WORKDIR /srv/backend

ENV PATH="/srv/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER app
EXPOSE 8000
ENTRYPOINT ["/srv/entrypoint.sh"]
CMD ["api"]
