#!/bin/sh
# One image, five commands (DevOps design §1) — build once, drift never.
#
# Invoked via `python -m` rather than console scripts: module invocation is
# independent of the absolute shebangs baked into venv bin/ wrappers, so the
# image survives being built at one path and run at another.
set -e

# Workers and the API are the same image on purpose. A worker built from a
# different image is a worker running different code from the API that
# enqueued the task, and the symptom is a task that fails only in production.

case "$1" in
  api)
    # Multi-process metrics. Each uvicorn worker keeps its own counters, so a
    # scrape without this is answered by whichever worker the kernel happened
    # to hand the connection to: request rates read low by a factor of the
    # worker count and jump between scrapes. prometheus_client aggregates
    # across processes only when this directory is set.
    #
    # It must be empty at boot. Files left by a previous container describe
    # dead processes, and their counters would be added to the live ones —
    # every restart inflating the totals a little more.
    export PROMETHEUS_MULTIPROC_DIR="${PROMETHEUS_MULTIPROC_DIR:-/tmp/prometheus}"
    rm -rf "$PROMETHEUS_MULTIPROC_DIR"
    mkdir -p "$PROMETHEUS_MULTIPROC_DIR"

    # Workers default to CPU count, which on a large host means dozens of
    # processes each holding a database pool. Sized explicitly instead.
    exec python -m uvicorn app.main:create_app --factory \
      --host 0.0.0.0 --port 8000 \
      --workers "${RM_API_WORKERS:-2}" \
      --proxy-headers --forwarded-allow-ips="*" \
      --timeout-keep-alive 30 \
      --access-log --no-server-header
    ;;

  api-reload)
    # Development only: one process, watching the mounted source.
    exec python -m uvicorn app.main:create_app --factory \
      --host 0.0.0.0 --port 8000 --reload --reload-dir /srv/backend/app \
      --proxy-headers
    ;;

  migrate)
    exec python -m alembic upgrade head
    ;;

  worker)
    # `--without-gossip/mingle/heartbeat` cuts broker chatter that buys
    # nothing for a single-queue deployment and costs Redis round trips.
    exec python -m celery -A app.workers.celery_app worker \
      --loglevel "${RM_APP_LOG_LEVEL:-INFO}" \
      --concurrency "${RM_WORKER_CONCURRENCY:-2}" \
      --max-tasks-per-child 200 \
      --without-gossip --without-mingle
    ;;

  beat)
    # One beat, ever. Two schedulers against one broker double every
    # scheduled sweep, and the second copy is invisible until someone reads
    # the notification volume.
    exec python -m celery -A app.workers.celery_app beat \
      --loglevel "${RM_APP_LOG_LEVEL:-INFO}"
    ;;

  *)
    exec "$@"
    ;;
esac
