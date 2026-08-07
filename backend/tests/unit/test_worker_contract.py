"""The beat schedule and the task registry must agree.

This suite exists because they did not, and nothing said so.

`autodiscover_tasks(["app.workers.tasks"])` searches for a `tasks` submodule
*inside* each package listed, so it looked for `app.workers.tasks.tasks`, found
nothing, and registered nothing. The worker started cleanly, logged "ready",
and connected to the broker with an empty registry. Beat published on schedule.
Every message came back `Received unregistered task`, once every ten minutes,
into a log that nobody reads while things appear to be working.

The failure is characteristic of scheduled work: there is no caller waiting on
a response, so a task that never runs looks exactly like a quiet system. The
detection sweep is the platform's alerting pipeline — the one thing whose
silence is indistinguishable from good news.

So the agreement is asserted here, where it costs a millisecond, rather than
discovered in a broker log.
"""

import pytest

from app.workers.celery_app import celery_app


@pytest.fixture(scope="module", autouse=True)
def _import_tasks() -> None:
    """Register tasks exactly the way a worker does.

    Via `conf.imports`, not a hand-written import list: a test that imports the
    task modules itself would pass no matter what the application is configured
    to load, which is precisely the bug being guarded against.
    """
    celery_app.loader.import_default_modules()


def scheduled_task_names() -> set[str]:
    return {entry["task"] for entry in celery_app.conf.beat_schedule.values()}


def test_every_scheduled_task_is_registered() -> None:
    """The bug, stated directly."""
    missing = sorted(scheduled_task_names() - set(celery_app.tasks))
    assert not missing, (
        f"beat publishes {missing}, which no worker can execute. The schedule "
        "will fire forever and every message will be rejected as unregistered."
    )


def test_the_registry_is_not_empty() -> None:
    """A weaker guard that catches the same class of failure earlier.

    If imports break again, the assertion above still fails — but so does this
    one, and this one says *why* in one line.
    """
    registered = {name for name in celery_app.tasks if not name.startswith("celery.")}
    assert registered, (
        "no application tasks are registered. `conf.imports` points at a module "
        "that does not exist, or the task module raised on import."
    )


def test_the_sweep_is_scheduled() -> None:
    """The alerting pipeline specifically.

    Every other task failing loudly would eventually be noticed. This one
    failing produces no alerts, which reads as a healthy estate.
    """
    assert "notifications.sweep" in scheduled_task_names()


def test_scheduled_entries_expire() -> None:
    """A queued task must not outlive the window it describes.

    Without `expires`, a broker outage means the backlog is delivered all at
    once on recovery: twelve hourly sweeps run together, each re-detecting the
    same conditions, and the recipients get twelve copies of one alert.
    """
    for name, entry in celery_app.conf.beat_schedule.items():
        expires = entry.get("options", {}).get("expires")
        assert expires, f"{name} has no expiry — a broker outage replays it as a burst"


def test_tasks_are_acknowledged_late() -> None:
    """Redelivery has to be safe, and the config has to say so.

    Every task here is idempotent by construction so that a worker dying
    mid-flight redelivers rather than loses. That property is only worth
    anything if acks are late.
    """
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
