"""Celery application and the beat schedule.

**Schedules are declared here, in code, rather than configured in a UI.** A
cron entry someone added by hand is invisible to review, absent from the diff
that changed the job it triggers, and gone when the box is rebuilt. Declaring
them beside the tasks means a change to when something runs goes through the
same review as a change to what it does.

The cadences are chosen against how fast each condition can actually change:

* **Alert sweep, hourly.** Stock runs out over hours; suppression stops the
  hourly cadence from becoming hourly noise. Running it every few minutes
  would multiply cost without shortening the time to a *useful* alert, because
  the underlying marts only rebuild nightly.
* **Daily digest, early morning.** Delivered before the trading day so it is
  read as a plan rather than as history.
* **Delivery retry, every ten minutes.** Bounded and idempotent — the ledger
  records what was owed, so a retry cannot double-send.

Every task is idempotent by construction. Retries, overlapping schedules, and
a nervous operator hitting the button twice all happen, and none of them may
double-notify.
"""

import os

from celery import Celery
from celery.schedules import crontab

BROKER_URL = os.environ.get("RM_CELERY_BROKER_URL", "redis://localhost:6379/1")
RESULT_BACKEND = os.environ.get("RM_CELERY_RESULT_BACKEND", BROKER_URL)

celery_app = Celery("retailmind", broker=BROKER_URL, backend=RESULT_BACKEND)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # A task that vanishes mid-flight must be redelivered, and every task here
    # is idempotent precisely so that redelivery is safe.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # One at a time: these tasks are IO-bound against the warehouse, and
    # prefetching batches them into one worker while others idle.
    worker_prefetch_multiplier=1,
    # A sweep that hangs must not hold the schedule. Soft first, so the task
    # can log what it was doing before it is killed.
    task_soft_time_limit=600,
    task_time_limit=900,
    beat_schedule={
        "alert-sweep-hourly": {
            "task": "notifications.sweep",
            "schedule": crontab(minute=5),
            "options": {"expires": 3000},
        },
        "daily-digest": {
            "task": "notifications.digest",
            "schedule": crontab(hour=6, minute=30),
            "options": {"expires": 3600},
        },
        "delivery-retry": {
            "task": "notifications.retry_failed",
            "schedule": crontab(minute="*/10"),
            "options": {"expires": 540},
        },
    },
)

#: Imported explicitly rather than autodiscovered.
#:
#: `autodiscover_tasks(["app.workers.tasks"])` looks for a `tasks` submodule
#: *inside* each package it is given — so it searched for
#: `app.workers.tasks.tasks`, found nothing, and registered nothing. The worker
#: still started, still reported ready, and still connected to the broker; it
#: simply had no tasks. Every scheduled job beat published came back
#: "Received unregistered task", once every ten minutes, in a log nobody was
#: reading. An explicit list cannot fail this way: a wrong module name is an
#: ImportError at startup instead of silence.
celery_app.conf.imports = ("app.workers.tasks.notifications",)
