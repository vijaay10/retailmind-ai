"""The time series a model sees, and the rules for building one honestly.

Two decisions live here that decide whether everything downstream is
trustworthy, and both are about *absence*.

**A missing day is not the same as a zero day.** For network revenue, a date
with no rows means the pipeline did not deliver — filling it with zero teaches
the model that the business occasionally shuts down, and it will forecast
those phantom troughs forever. For SKU × store demand the opposite holds: a
day with no sales line genuinely means nobody bought it, and dropping the day
would make an item selling twice a month look like it sells every day it
appears. The caller states which regime applies; the series never guesses.

**A short series cannot support a seasonal model.** Six weeks of history gives
six observations per weekday. That is enough to notice Saturdays are busy and
nowhere near enough to fit a yearly pattern, so the minimum-history gate
refuses rather than fitting something that will look confident and be wrong.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

import numpy as np
import numpy.typing as npt

Array = npt.NDArray[np.float64]

#: A weekly cycle. Retail's dominant rhythm, and the only seasonality six
#: weeks of history can actually support.
SEASON_LENGTH = 7

#: Enough history to fit a weekly pattern with something to spare. Below four
#: complete cycles, a single unusual Saturday defines what Saturdays are.
MIN_HISTORY_DAYS = SEASON_LENGTH * 4


class GapPolicy(StrEnum):
    """What a date with no observation means for this series."""

    ZERO = "zero"
    """No row means no sales. Correct for demand at SKU × store grain."""

    ERROR = "error"
    """No row means missing data. Correct for network aggregates, where a
    silent gap is a pipeline failure and filling it fabricates a trough."""


class InsufficientHistoryError(ValueError):
    """Raised when a series is too short to fit what was asked of it."""


@dataclass(frozen=True, slots=True)
class TimeSeries:
    """A contiguous daily series, with no gaps by construction."""

    key: str
    """Series identifier — "revenue", or "AC-1010|S2001" for demand."""

    start: date
    values: Array

    def __post_init__(self) -> None:
        if self.values.ndim != 1:
            raise ValueError(f"{self.key}: expected a 1-D series, got {self.values.ndim}-D")
        if not np.all(np.isfinite(self.values)):
            raise ValueError(f"{self.key}: series contains non-finite values")

    def __len__(self) -> int:
        return int(self.values.size)

    @property
    def end(self) -> date:
        return self.start + timedelta(days=len(self) - 1)

    def dates(self) -> list[date]:
        return [self.start + timedelta(days=offset) for offset in range(len(self))]

    def date_at(self, index: int) -> date:
        return self.start + timedelta(days=index)

    def require_history(self, minimum: int = MIN_HISTORY_DAYS) -> None:
        """Refuse to proceed on a series too short to support a weekly model."""
        if len(self) < minimum:
            raise InsufficientHistoryError(
                f"{self.key}: {len(self)} days of history, need at least {minimum} "
                f"({minimum // SEASON_LENGTH} complete weekly cycles)"
            )

    @property
    def non_zero_share(self) -> float:
        """Fraction of days with any movement.

        Intermittent demand — a SKU selling on one day in ten — breaks
        regression models quietly: they fit the mean of a mostly-zero series
        and emit confident fractional forecasts for an item that sells in
        whole units or not at all. The training pipeline reads this to decide
        whether a feature model is even eligible.
        """
        if len(self) == 0:
            return 0.0
        return float(np.mean(self.values != 0))

    def split(self, at: int) -> tuple["TimeSeries", "TimeSeries"]:
        """Split into (history, future) at an index — the backtest primitive."""
        if not 0 < at < len(self):
            raise ValueError(f"{self.key}: split point {at} outside 1..{len(self) - 1}")
        return (
            TimeSeries(self.key, self.start, self.values[:at]),
            TimeSeries(self.key, self.date_at(at), self.values[at:]),
        )


def build_series(
    key: str,
    observations: dict[date, float],
    *,
    gap_policy: GapPolicy,
    start: date | None = None,
    end: date | None = None,
) -> TimeSeries:
    """Assemble a contiguous series from sparse observations.

    The gap policy is required rather than defaulted. Choosing wrong is silent
    and expensive in both directions — phantom troughs one way, a fabricated
    daily seller the other — so the caller has to have decided.
    """
    if not observations and (start is None or end is None):
        raise ValueError(f"{key}: cannot build a series from no observations")

    first = start if start is not None else min(observations)
    last = end if end is not None else max(observations)
    if first > last:
        raise ValueError(f"{key}: start {first} is after end {last}")

    span = (last - first).days + 1
    values = np.zeros(span, dtype=np.float64)
    missing: list[date] = []

    for offset in range(span):
        day = first + timedelta(days=offset)
        if day in observations:
            values[offset] = observations[day]
        else:
            missing.append(day)

    if missing and gap_policy is GapPolicy.ERROR:
        shown = ", ".join(str(day) for day in missing[:5])
        more = f" (+{len(missing) - 5} more)" if len(missing) > 5 else ""
        raise ValueError(
            f"{key}: {len(missing)} date(s) missing from an aggregate series: {shown}{more}. "
            "A gap here is a pipeline failure, not a quiet day — filling it with zero "
            "would teach the model a trough that never happened."
        )

    return TimeSeries(key=key, start=first, values=values)
