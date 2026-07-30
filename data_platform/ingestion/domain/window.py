"""Processing windows — the unit of work, of idempotency, and of replay.

Everything in the pipeline is scoped to ``(source, table, window)``. A window
is a half-open range of **business dates**, never a wall-clock offset: that is
what makes a re-run of yesterday produce byte-identical output whether it runs
tonight or in six months (ETL §5, FR-D03).
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True, slots=True)
class Window:
    """Half-open business-date range ``[start, end)``."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError(f"window end {self.end} must be after start {self.start}")

    @classmethod
    def for_day(cls, day: date) -> "Window":
        return cls(day, day + timedelta(days=1))

    @classmethod
    def trailing(cls, end_exclusive: date, days: int) -> "Window":
        """The rolling reprocess window that absorbs late data (ETL §6)."""
        return cls(end_exclusive - timedelta(days=days), end_exclusive)

    @property
    def days(self) -> int:
        return (self.end - self.start).days

    @property
    def partitions(self) -> tuple[str, ...]:
        """``dt=`` partition labels covered, one per day."""
        return tuple(day.isoformat() for day in self)

    def __iter__(self) -> Iterator[date]:
        current = self.start
        while current < self.end:
            yield current
            current += timedelta(days=1)

    def contains(self, day: date) -> bool:
        return self.start <= day < self.end

    def __str__(self) -> str:
        return f"[{self.start.isoformat()},{self.end.isoformat()})"
