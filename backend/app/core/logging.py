"""Structured logging setup (Backend design §13, ARCH §17).

JSON to stdout — shipping is the platform's job (DevOps §14). Request context
(request_id, tenant, user) is bound by middleware into contextvars so every
downstream log line carries it without threading arguments through call stacks.

The PII scrubber is a processor, not a convention: it runs on every event
regardless of who wrote the log line.
"""

import logging
import re
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

# Keys whose values are redacted wholesale, wherever they appear.
_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "api_key",
        "secret",
        "cookie",
        "set-cookie",
    }
)

# Emails are the PII most likely to slip into a log line by accident.
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _scrub(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Redact secrets and pseudonymize emails (ARCH §17 PII discipline)."""
    for key, value in list(event_dict.items()):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = "***"
        elif isinstance(value, str) and "@" in value:
            event_dict[key] = _EMAIL_RE.sub("<email>", value)
    return event_dict


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    """Idempotent logging configuration; safe to call from app and workers."""
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,  # request_id, tenant_id, user
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _scrub,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        cache_logger_on_first_use=True,
    )
