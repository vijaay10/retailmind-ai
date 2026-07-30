#!/bin/sh
# One image, four commands (DevOps design §1) — build once, drift never.
set -e

case "$1" in
  api)
    exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 --proxy-headers
    ;;
  migrate)
    echo "TODO(S1): exec alembic upgrade head"
    exit 0
    ;;
  worker)
    echo "TODO(S1): exec celery -A app.workers.celery_app worker"
    exit 1
    ;;
  beat)
    echo "TODO(S1): exec celery -A app.workers.celery_app beat"
    exit 1
    ;;
  *)
    exec "$@"
    ;;
esac
