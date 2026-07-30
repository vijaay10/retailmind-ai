#!/bin/sh
# One image, four commands (DevOps design §1) — build once, drift never.
# Invoked via `python -m` rather than console scripts: module invocation is
# independent of the absolute shebangs baked into venv bin/ wrappers.
set -e

case "$1" in
  api)
    exec python -m uvicorn app.main:create_app --factory \
      --host 0.0.0.0 --port 8000 --proxy-headers
    ;;
  migrate)
    exec python -m alembic upgrade head
    ;;
  worker)
    echo "TODO(S1): exec python -m celery -A app.workers.celery_app worker"
    exit 1
    ;;
  beat)
    echo "TODO(S1): exec python -m celery -A app.workers.celery_app beat"
    exit 1
    ;;
  *)
    exec "$@"
    ;;
esac
