"""Semantic-layer client — the only path from application code to the warehouse.

Two rules make this module the architectural keystone (ARCH ADR-3):

1. **Nothing else may query the warehouse.** Import-linter enforces it. That is
   what guarantees a metric means one thing everywhere, because there is only
   one place metrics are computed.
2. **Application code never writes SQL.** Callers describe *what* they want
   (metric, dimensions, filters, period) and this layer compiles it. Values are
   always bound as parameters; identifiers are validated against the registry
   before they can reach a query string.

Results carry provenance — the snapshot they were computed from, freshness,
and whether the answer came from cache — because a number without provenance
is a number nobody can defend.
"""

# ruff: noqa: S608 — identifiers reaching the compiler come only from the
# metric registry (validated in AnalyticsRequest.validate); every value
# originating with a caller binds as a query parameter.

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import duckdb
import structlog

from app.domain.shared.errors import DependencyError, ValidationDomainError

log = structlog.get_logger(__name__)

#: Hard cap on rows returned to an interactive caller. Anything larger is an
#: export, which is a job, not a request (Backend).
MAX_ROWS = 5_000


def _coerce_row(row: dict[str, Any]) -> dict[str, Any]:
    """Convert warehouse types into JSON-native ones.

    DuckDB returns DECIMAL as ``Decimal``, which serializes to a JSON *string*
    — leaving every client to parse numbers before it can chart them. The
    warehouse has already rounded these values to four places, and no
    arithmetic happens past this boundary, so converting to float here costs
    nothing and makes the API contract honest: numbers are numbers.

    Dates become ISO strings for the same reason.
    """
    coerced: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, Decimal):
            coerced[key] = float(value)
        elif isinstance(value, datetime | date):
            coerced[key] = value.isoformat()
        else:
            coerced[key] = value
    return coerced


@dataclass(frozen=True, slots=True)
class QueryResult:
    """Rows plus everything needed to explain where they came from."""

    rows: list[dict[str, Any]]
    columns: list[str]
    row_count: int
    compiled_sql: str
    snapshot_id: str | None
    freshness: date | None
    cache: str = "miss"
    elapsed_ms: float = 0.0
    truncated: bool = False

    @property
    def meta(self) -> dict[str, Any]:
        """The provenance block every API response carries (ARCH.2)."""
        return {
            "row_count": self.row_count,
            "data_snapshot_id": self.snapshot_id,
            "freshness": self.freshness.isoformat() if self.freshness else None,
            "cache": self.cache,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "truncated": self.truncated,
        }


@dataclass(slots=True)
class SemanticQuery:
    """A governed query request. Compiled, never concatenated."""

    relation: str
    """Semantic view to read. Validated against the registry's allow-list."""

    select: list[str] = field(default_factory=list)
    aggregates: dict[str, str] = field(default_factory=dict)
    """``alias → expression``, where expressions come only from metric
    definitions in the registry — never from a caller."""

    filters: list[tuple[str, str, Any]] = field(default_factory=list)
    """``(column, operator, value)``. Values bind as parameters."""

    group_by: list[str] = field(default_factory=list)
    order_by: list[tuple[str, bool]] = field(default_factory=list)
    limit: int = 100
    offset: int = 0

    def fingerprint(self) -> str:
        """Stable digest for cache keying.

        Ordering is normalized so two logically identical queries built in
        different orders share a cache entry.
        """
        payload = {
            "relation": self.relation,
            "select": sorted(self.select),
            "aggregates": dict(sorted(self.aggregates.items())),
            "filters": sorted((c, o, str(v)) for c, o, v in self.filters),
            "group_by": sorted(self.group_by),
            "order_by": self.order_by,
            "limit": self.limit,
            "offset": self.offset,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()[:20]


_ALLOWED_OPERATORS: dict[str, str] = {
    "eq": "=",
    "ne": "<>",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
    "in": "IN",
    "like": "ILIKE",
}


class SemanticLayerClient:
    """Compiles and executes governed queries against the warehouse."""

    def __init__(
        self,
        warehouse_path: str,
        *,
        semantic_schema: str = "analytics_semantic",
        core_schema: str = "analytics_analytics",
    ) -> None:
        self._path = warehouse_path
        self._semantic_schema = semantic_schema
        self._core_schema = core_schema
        self._snapshot_cache: tuple[str | None, date | None] | None = None

    # ── Execution ────────────────────────────────────────────────────

    def execute(self, query: SemanticQuery) -> QueryResult:
        """Compile, run, and wrap a governed query.

        Opens a read-only connection per call. DuckDB is embedded and
        single-writer: a long-lived shared handle would serialize every
        request behind whichever one is slowest. Connection setup is
        microseconds; the isolation is worth it.
        """
        sql, params = self._compile(query)
        started = time.perf_counter()

        try:
            with self._connect() as conn:
                cursor = conn.execute(sql, params)
                columns = [d[0] for d in cursor.description or []]
                raw = cursor.fetchmany(MAX_ROWS + 1)
        except duckdb.Error as exc:
            log.warning("semantic.query_failed", relation=query.relation, error=str(exc))
            raise DependencyError("warehouse", retryable=True) from exc

        # One row beyond the page was requested so truncation can be detected
        # without a second COUNT query; it must be trimmed before returning,
        # or every page silently contains one extra row.
        page_size = min(query.limit, MAX_ROWS)
        truncated = len(raw) > page_size
        rows = [_coerce_row(dict(zip(columns, row, strict=True))) for row in raw[:page_size]]
        snapshot_id, freshness = self.snapshot()

        return QueryResult(
            rows=rows,
            columns=columns,
            row_count=len(rows),
            compiled_sql=sql,
            snapshot_id=snapshot_id,
            freshness=freshness,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            truncated=truncated,
        )

    def snapshot(self) -> tuple[str | None, date | None]:
        """Current warehouse snapshot id and freshness date.

        Derived from the latest business date present in the KPI mart: a
        cheap, honest proxy that changes exactly when new data publishes,
        which is what makes it usable as a cache-invalidation key.
        """
        if self._snapshot_cache is not None:
            return self._snapshot_cache

        try:
            with self._connect() as conn:
                row = conn.execute(
                    f"SELECT max(business_date) FROM {self._core_schema}.mart_kpi_daily"
                ).fetchone()
        except duckdb.Error:
            return None, None

        freshness = row[0] if row else None
        snapshot_id = f"snap_{freshness.isoformat()}" if freshness else None
        self._snapshot_cache = (snapshot_id, freshness)
        return self._snapshot_cache

    def invalidate_snapshot(self) -> None:
        """Drop the memoized snapshot after a warehouse publish."""
        self._snapshot_cache = None

    # ── Compilation ──────────────────────────────────────────────────

    def _compile(self, query: SemanticQuery) -> tuple[str, list[Any]]:
        """Build parameterized SQL from a validated query object.

        Identifiers are interpolated only after passing registry validation
        (``app.services.analytics.registry``); every literal binds as a
        parameter. There is no path here for caller-supplied text to become
        SQL syntax.
        """
        if not query.relation.replace("_", "").isalnum():
            raise ValidationDomainError(f"invalid relation name: {query.relation!r}")

        projections = list(query.select)
        projections += [
            f"{expression} AS {alias}" for alias, expression in query.aggregates.items()
        ]
        if not projections:
            projections = ["*"]

        sql = [f"SELECT {', '.join(projections)}"]
        sql.append(f"FROM {self._semantic_schema}.{query.relation}")

        params: list[Any] = []
        if query.filters:
            clauses = []
            for column, operator, value in query.filters:
                op = _ALLOWED_OPERATORS.get(operator)
                if op is None:
                    raise ValidationDomainError(f"unsupported operator: {operator!r}")
                if op == "IN":
                    values = list(value)
                    if not values:
                        # An empty IN list is a caller bug; matching nothing is
                        # a friendlier answer than a SQL syntax error.
                        clauses.append("FALSE")
                        continue
                    placeholders = ", ".join("?" for _ in values)
                    clauses.append(f"{column} IN ({placeholders})")
                    params.extend(values)
                else:
                    clauses.append(f"{column} {op} ?")
                    params.append(value)
            sql.append("WHERE " + " AND ".join(clauses))

        if query.group_by:
            sql.append("GROUP BY " + ", ".join(query.group_by))

        if query.order_by:
            ordering = ", ".join(
                f"{column} {'DESC' if descending else 'ASC'} NULLS LAST"
                for column, descending in query.order_by
            )
            sql.append(f"ORDER BY {ordering}")

        # Always bounded. An unbounded interactive query is a self-inflicted
        # outage waiting for the day the table gets big (DB design).
        sql.append(f"LIMIT {min(query.limit, MAX_ROWS) + 1} OFFSET {max(query.offset, 0)}")

        return "\n".join(sql), params

    def _connect(self) -> duckdb.DuckDBPyConnection:
        try:
            conn = duckdb.connect(self._path, read_only=True)
        except duckdb.Error as exc:
            raise DependencyError("warehouse", retryable=True) from exc
        conn.execute("SET TimeZone = 'UTC'")
        return conn
