"""Error taxonomy (ETL design §21–22).

Retry behaviour is decided by *class*, never by string-matching a message.
That distinction matters: a timeout should be retried, a parse failure never
should (same input, same failure), and retrying an auth failure is lockout
roulette. Unknown errors are deliberately not retryable — automatically
retrying something we do not understand is how corruption compounds.
"""

from enum import StrEnum


class ErrorClass(StrEnum):
    SOURCE_UNAVAILABLE = "source_unavailable"
    AUTH_FAILURE = "auth_failure"
    PARSE_ERROR = "parse_error"
    VALIDATION_FAILED = "validation_failed"
    VALIDATION_ENGINE_ERROR = "validation_engine_error"
    WAREHOUSE_ERROR = "warehouse_error"
    TIMEOUT = "timeout"
    CONFIG_ERROR = "config_error"
    UNKNOWN = "unknown"


#: Only these classes are safe to retry unattended.
RETRYABLE: frozenset[ErrorClass] = frozenset(
    {
        ErrorClass.SOURCE_UNAVAILABLE,
        ErrorClass.TIMEOUT,
        ErrorClass.WAREHOUSE_ERROR,
    }
)


class EtlError(Exception):
    """Base for every deliberate pipeline failure."""

    error_class: ErrorClass = ErrorClass.UNKNOWN

    def __init__(self, message: str, **context: object) -> None:
        self.context = context
        super().__init__(message)

    @property
    def retryable(self) -> bool:
        return self.error_class in RETRYABLE


class SourceUnavailableError(EtlError):
    error_class = ErrorClass.SOURCE_UNAVAILABLE


class AuthFailureError(EtlError):
    error_class = ErrorClass.AUTH_FAILURE


class ParseError(EtlError):
    """Malformed input that no amount of retrying will fix."""

    error_class = ErrorClass.PARSE_ERROR


class SchemaDriftError(EtlError):
    """Source broke its declared contract in a blocking way (ETL §12)."""

    error_class = ErrorClass.VALIDATION_FAILED


class QuarantineError(EtlError):
    """A blocking quality rule failed; the batch must not reach the warehouse.

    Carries the failing rule ids so the alert and the UI can name them without
    re-running the gate.
    """

    error_class = ErrorClass.VALIDATION_FAILED

    def __init__(self, message: str, failed_rules: list[str], **context: object) -> None:
        self.failed_rules = failed_rules
        super().__init__(message, failed_rules=failed_rules, **context)


class WarehouseError(EtlError):
    error_class = ErrorClass.WAREHOUSE_ERROR


class ConfigError(EtlError):
    error_class = ErrorClass.CONFIG_ERROR
