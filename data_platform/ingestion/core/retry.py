"""Retry policy driven by error class.

Retries are safe here *because* every task is idempotent over its window: a
re-run overwrites its partition rather than appending, so there is no cleanup
path to get wrong. Jitter is not decoration — without it, N connectors that
fail together retry together forever.
"""

import random
import time
from collections.abc import Callable

import structlog

from ingestion.core.errors import EtlError

log = structlog.get_logger(__name__)


def with_retries[T](
    operation: Callable[[], T],
    *,
    name: str,
    max_attempts: int = 3,
    base_seconds: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,  # noqa: S311 — jitter, not crypto
) -> T:
    """Run ``operation``, retrying only classes that can succeed on a retry.

    Backoff is exponential with full jitter: ``base * 3**attempt`` scaled by a
    random factor, so simultaneous failures fan out instead of synchronizing.

    ``sleep`` and ``jitter`` are injected so tests exercise the policy without
    spending real seconds.
    """
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except EtlError as exc:
            last_error = exc
            if not exc.retryable:
                log.warning(
                    "etl.retry.abandoned",
                    operation=name,
                    error_class=exc.error_class.value,
                    reason="not retryable",
                )
                raise
            if attempt == max_attempts:
                log.error(
                    "etl.retry.exhausted",
                    operation=name,
                    attempts=attempt,
                    error_class=exc.error_class.value,
                )
                raise
            delay = base_seconds * (3 ** (attempt - 1)) * (0.5 + jitter())
            log.warning(
                "etl.retry.scheduled",
                operation=name,
                attempt=attempt,
                next_attempt_in_s=round(delay, 2),
                error_class=exc.error_class.value,
            )
            sleep(delay)

    raise last_error if last_error else RuntimeError("unreachable")
