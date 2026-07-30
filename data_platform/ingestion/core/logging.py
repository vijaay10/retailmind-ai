"""Run-scoped structured logging (ETL design §33).

One logging standard across the platform: the same structlog JSON the backend
emits, with pipeline context (run id, source, table, window) bound so every
downstream line carries it without threading arguments through call stacks.
"""

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import structlog


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=True)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        cache_logger_on_first_use=True,
    )


@contextmanager
def run_context(**fields: Any) -> Iterator[None]:
    """Bind pipeline context for the duration of a stage.

    Data *values* are never logged — only keys, counts, and identifiers
    (ETL §33 PII discipline). The scrubber in the backend is the backstop;
    this convention is the primary control.
    """
    tokens = structlog.contextvars.bind_contextvars(**fields)
    try:
        yield
    finally:
        structlog.contextvars.reset_contextvars(**tokens)
