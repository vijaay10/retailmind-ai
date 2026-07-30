# RetailMind ETL image — the retailmind-etl package + dbt + GE.
# Consumed by Airflow tasks and the ingestion CLI (ETL design / DevOps §1).

FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /build
COPY pyproject.toml uv.lock* ./
COPY data_platform/pyproject.toml data_platform/
COPY backend/pyproject.toml backend/
COPY data_platform/ingestion data_platform/ingestion
COPY data_platform/quality data_platform/quality
RUN uv sync --no-dev --package retailmind-etl

FROM python:3.12-slim AS runtime
RUN groupadd -g 10001 app && useradd -u 10001 -g app -m app
WORKDIR /srv
COPY --from=builder --chown=app:app /build/.venv /srv/.venv
COPY --chown=app:app data_platform /srv/data_platform
ENV PATH="/srv/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
# TODO(S2): entrypoint for connector runs / dbt build / GE checkpoints
CMD ["python", "-c", "import ingestion, quality; print('retailmind-etl image ok')"]
