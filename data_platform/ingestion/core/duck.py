"""DuckDB connection factory with deterministic session settings.

**Every** connection the pipeline uses must come from here. DuckDB resolves
timestamp parsing and timezone arithmetic against the *session* timezone,
which defaults to the host's local zone — so the same CSV parsed on a laptop
in Chennai and a container in Virginia would produce different business dates,
and a backfill would silently disagree with the original run.

Pinning the session to UTC makes the model explicit and portable: source
timestamps are UTC instants, and the conform stage converts them to
store-local dates itself (``ingestion.transform.sql``).
"""

from pathlib import Path

import duckdb


def connect(
    database: str | Path = ":memory:", *, read_only: bool = False
) -> duckdb.DuckDBPyConnection:
    """Open a connection with deterministic, host-independent settings."""
    connection = duckdb.connect(str(database), read_only=read_only)
    configure_session(connection)
    return connection


def configure_session(connection: duckdb.DuckDBPyConnection) -> None:
    """Apply the settings the pipeline's correctness depends on.

    Exposed separately so callers holding a connection from elsewhere (tests,
    notebooks, Airflow hooks) can opt into the same guarantees.
    """
    connection.execute("SET TimeZone = 'UTC'")
