# RetailMind console image — Streamlit over the API.
#
# Path discipline matches the API image: the venv is BUILT at the absolute
# path it RUNS at (/srv/.venv), because console scripts bake an absolute
# shebang and a relocated venv fails with "exec: streamlit: not found".

# ── Builder ───────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/srv/.venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /srv
# Workspace resolution needs every member's manifest, even ones we do not install.
COPY pyproject.toml uv.lock* ./
COPY data_platform/pyproject.toml data_platform/
COPY backend/pyproject.toml backend/
COPY ml/pyproject.toml ml/
COPY ui/pyproject.toml ui/
# Dependencies first (cached layer), then the source that changes every commit.
RUN uv sync --no-dev --package retailmind-ui --no-install-workspace
COPY ui/ ui/
RUN uv sync --no-dev --package retailmind-ui

# ── Runtime ───────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime
LABEL org.opencontainers.image.source="https://github.com/OWNER/retailmind-ai" \
      org.opencontainers.image.title="retailmind/ui" \
      org.opencontainers.image.description="Operator console"

# The base image ships with known-fixable OS vulnerabilities (9 HIGH at the
# time of writing, e.g. bsdutils CVE-2026-53615). Upgrading here picks up the
# distro's fixed versions; without it the image scan gate fails on packages
# this project neither chose nor uses directly.
RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -g 10001 app && useradd -u 10001 -g app -m app

COPY --from=builder --chown=app:app /srv/.venv /srv/.venv
COPY --chown=app:app ui/ /srv/ui/

WORKDIR /srv/ui

ENV PATH="/srv/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Streamlit writes usage stats and a config dir; give it a writable home
    # rather than letting it fail on a read-only root filesystem.
    HOME=/home/app \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

USER app
EXPOSE 8501

# Streamlit publishes its own health endpoint; using it means the container is
# unhealthy exactly when the script server is, not when the port happens to
# accept a connection.
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8501/_stcore/health')"

ENTRYPOINT ["streamlit", "run", "app.py", \
            "--server.port=8501", "--server.address=0.0.0.0"]
