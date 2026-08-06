"""Comparing one period against another.

Two properties make a comparison honest, and both are routinely skipped.

**Windows are normalised.** A 28-day period against a 31-day one differs by
10% before anything happened in the business. Comparing them raw and reporting
the difference as performance is the most common error in a period review, and
it is invisible because both numbers are individually correct.

**The change is split into volume and rate.** Revenue falling because fewer
people bought and revenue falling because they each spent less are different
problems with different owners, and "revenue is down 8%" distinguishes
neither. The split reuses the decomposition the root cause engine already
uses, so the two surfaces cannot disagree about the same movement.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from app.services.rca.decomposition import volume_rate_split

#: Movement below this is ordinary variation. Describing a 0.4% move as a
#: decline trains a reader to discount the language entirely.
MATERIAL_MOVE = 0.02


@dataclass(frozen=True, slots=True)
class Window:
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def as_dict(self) -> dict[str, Any]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat(), "days": self.days}


@dataclass(frozen=True, slots=True)
class PeriodComparison:
    """One metric, two windows, and what actually changed."""

    metric: str
    current: Window
    baseline: Window
    current_value: float
    baseline_value: float
    scale: float
    """What the baseline was multiplied by to match the current window's
    length. Reported so a reader can see the normalisation happened."""

    volume_effect: float = 0.0
    rate_effect: float = 0.0
    interaction: float = 0.0
    dominant: str = ""

    @property
    def baseline_scaled(self) -> float:
        return self.baseline_value * self.scale

    @property
    def change(self) -> float:
        return self.current_value - self.baseline_scaled

    @property
    def relative_change(self) -> float | None:
        if not self.baseline_scaled:
            return None
        return self.change / abs(self.baseline_scaled)

    @property
    def is_material(self) -> bool:
        relative = self.relative_change
        return relative is not None and abs(relative) >= MATERIAL_MOVE

    def describe(self) -> str:
        relative = self.relative_change
        label = self.metric.replace("_", " ")
        if relative is None:
            return (
                f"{label.capitalize()} was {self.current_value:,.0f}; no comparable prior period."
            )
        if not self.is_material:
            return (
                f"{label.capitalize()} was {self.current_value:,.0f}, broadly flat "
                f"({relative:+.1%}) against the comparable period."
            )
        direction = "up" if relative > 0 else "down"
        text = (
            f"{label.capitalize()} was {self.current_value:,.0f}, {direction} "
            f"{abs(relative):.1%} against the comparable period."
        )
        if self.dominant:
            text += f" The move is driven by {self.dominant}: " + (
                "the number of transactions changed rather than their value."
                if self.dominant == "volume"
                else "each transaction was worth more or less, not fewer of them."
            )
        return text

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "current": self.current.as_dict(),
            "baseline": self.baseline.as_dict(),
            "current_value": round(self.current_value, 2),
            "baseline_value": round(self.baseline_value, 2),
            "baseline_scaled": round(self.baseline_scaled, 2),
            "scale": round(self.scale, 4),
            "change": round(self.change, 2),
            "relative_change": (
                round(self.relative_change, 4) if self.relative_change is not None else None
            ),
            "is_material": self.is_material,
            "volume_effect": round(self.volume_effect, 2),
            "rate_effect": round(self.rate_effect, 2),
            "interaction": round(self.interaction, 2),
            "dominant": self.dominant,
        }


def windows(period_end: date, period_days: int) -> tuple[Window, Window]:
    """The period under review and the one immediately before it."""
    current = Window(period_end - timedelta(days=period_days - 1), period_end)
    baseline_end = current.start - timedelta(days=1)
    return current, Window(baseline_end - timedelta(days=period_days - 1), baseline_end)


def compare(
    *,
    metric: str,
    current: Window,
    baseline: Window,
    current_value: float,
    baseline_value: float,
    current_count: float = 0.0,
    baseline_count: float = 0.0,
) -> PeriodComparison:
    """Compare two windows, normalising for length and splitting the change."""
    scale = current.days / baseline.days if baseline.days else 1.0

    volume = rate = interaction = 0.0
    dominant = ""
    if current_count and baseline_count:
        split = volume_rate_split(
            current_total=current_value,
            baseline_total=baseline_value * scale,
            current_count=current_count,
            baseline_count=baseline_count * scale,
        )
        volume, rate, interaction = split.volume_effect, split.rate_effect, split.interaction
        dominant = split.dominant

    return PeriodComparison(
        metric=metric,
        current=current,
        baseline=baseline,
        current_value=current_value,
        baseline_value=baseline_value,
        scale=scale,
        volume_effect=volume,
        rate_effect=rate,
        interaction=interaction,
        dominant=dominant,
    )
