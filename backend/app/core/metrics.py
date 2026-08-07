"""Prometheus metrics.

**Route templates, never raw paths.** `/api/v1/forecasts/{target}` is one
series; `/api/v1/forecasts/revenue` and `/api/v1/forecasts/profit` as separate
series is how a metrics store gets a million labels and falls over. The access
log already made this choice for the same reason, and the two agree.

**Only the metrics somebody would act on.** Every series costs memory in
Prometheus forever, and a dashboard with forty panels is a dashboard nobody
reads. Four families:

* request rate and status — is the platform serving?
* request duration — is it serving *fast enough*?
* the last completed detection sweep — because silence from the alerting
  pipeline looks exactly like good news, and is the one failure that hides
  itself.
* build info — so a graph can be tied to a deployment.

Deliberately absent: per-tenant labels (an identifier in a metric name is a
privacy leak that outlives the request), and anything derived from a response
body. Business figures live in the warehouse, which is queryable and
auditable; a business number scraped into a time series is a number with no
provenance.
"""

import os
import time
from collections.abc import Awaitable, Callable

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    multiprocess,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

RequestHandler = Callable[[Request], Awaitable[Response]]

REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests by method, route template, and status.",
    ["method", "path", "status"],
)

DURATION = Histogram(
    "http_request_duration_seconds",
    "Request duration in seconds.",
    ["method", "path"],
    # Buckets chosen for what this platform actually does. The default set
    # tops out at 10s, which puts a root-cause sweep and a report render in
    # the same overflow bucket as a hung request.
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
)

LAST_SWEEP = Gauge(
    "retailmind_last_sweep_timestamp_seconds",
    "Unix time of the last completed detection sweep.",
    # Under multi-process collection a gauge defaults to one series per
    # process id, which would turn "when did the last sweep finish" into four
    # answers and a pid label that changes on every restart. `max` is the
    # question actually being asked: the most recent sweep, whoever ran it.
    multiprocess_mode="max",
)

SWEEP_ALERTS = Counter(
    "retailmind_sweep_alerts_total",
    "Alerts a sweep produced, by outcome.",
    ["outcome"],
)

BUILD = Gauge(
    "retailmind_build_info",
    "Always 1; the labels carry the build.",
    ["version", "env"],
    # Every worker sets the same value, so collapsing them is right; the
    # default would publish the identical build four times under four pids.
    multiprocess_mode="max",
)


def record_build(version: str, env: str) -> None:
    BUILD.labels(version=version, env=env).set(1)


def record_sweep(*, notified: int, suppressed: int) -> None:
    """Called when a detection sweep finishes.

    The timestamp is the point. A sweep that stops running produces no alerts,
    which is indistinguishable from a quiet estate until somebody notices the
    silence — so the alert rule watches this gauge going stale rather than
    watching for errors that will never arrive.
    """
    LAST_SWEEP.set(time.time())
    SWEEP_ALERTS.labels(outcome="notified").inc(notified)
    SWEEP_ALERTS.labels(outcome="suppressed").inc(suppressed)


#: What an unrouted request is labelled. 404s and scanner traffic collapse
#: here on purpose: labelling them by raw path lets anyone with a URL bar add
#: unbounded cardinality to the metrics store.
UNMATCHED = "<unmatched>"


def _route_label(request: Request) -> str:
    """The route template a request matched, or `<unmatched>`.

    Two cases, because Starlette only reports one of them. FastAPI's
    `APIRoute` puts itself in the scope, so `route.path` gives the template
    (`/api/v1/forecasts/{target}`) directly. A plain `starlette.routing.Route`
    — which is what FastAPI itself uses for `/api/openapi.json` and the docs —
    does not, so those requests looked unrouted and were counted next to the
    scanner traffic despite returning 200.

    The fallback uses the raw path, but only when the request demonstrably
    matched something (`endpoint` is in the scope) *and* that match bound no
    path parameters. Both conditions together bound the label set to the
    literal routes the application registers, which is the property the
    template rule exists to protect.
    """
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    if template:
        return str(template)
    if request.scope.get("endpoint") and not request.scope.get("path_params"):
        return str(request.scope.get("path", UNMATCHED))
    return UNMATCHED


class MetricsMiddleware(BaseHTTPMiddleware):
    """Count and time every request, labelled by route template."""

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        started = time.perf_counter()
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:
            # An unhandled exception still became a 500 for the caller, and a
            # metric that only counts successful failures is worse than none.
            status = 500
            raise
        finally:
            label = _route_label(request)
            elapsed = time.perf_counter() - started

            REQUESTS.labels(method=request.method, path=label, status=str(status)).inc()
            DURATION.labels(method=request.method, path=label).observe(elapsed)

        return response


def metrics_response() -> Response:
    """Render the exposition format.

    Handles the multi-process case: uvicorn runs several workers in
    production, and each has its own counters. Without the collector below,
    Prometheus scrapes whichever worker answered and the numbers jump around
    by a factor of the worker count.
    """
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
        payload = generate_latest(registry)
    else:
        payload = generate_latest()

    return Response(content=payload, media_type=CONTENT_TYPE_LATEST)
