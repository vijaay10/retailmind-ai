"""Planted incidents — the ground truth a root-cause engine is graded against.

An RCA engine evaluated on data with no causal structure proves nothing. It
will happily rank the largest region first and be congratulated for it, and
nobody finds out it is a ranking of *size* until it is asked about a real
incident. So the synthetic estate contains incidents with known causes,
defined here, and the test suite asserts the engine recovers them.

The incidents are shared deliberately. The same declaration drives the POS
feed, the weather feed, and the carrier feed, so the correlation the engine
detects is one that was genuinely put there rather than one that emerged from
three generators drifting in the same direction by luck.

Two properties make these worth detecting:

**They are regional, not global.** A shock applied everywhere is invisible to
a decomposition — every slice moves together and nothing stands out. Real
incidents are local, and finding *where* is most of the work.

**They move volume, not rate.** Weather keeps people at home; it does not make
the ones who come spend less. That distinction is diagnosable — a volume
effect and a rate effect need different responses — and an engine that reports
"revenue fell" without separating them has not said anything actionable.
"""

from dataclasses import dataclass
from datetime import date, timedelta

#: Region assignment follows the store seed: stores round-robin across the
#: five regions in this order, so store index N belongs to REGIONS[(N-1) % 5].
REGIONS: tuple[str, ...] = ("Southwest", "Northeast", "Midwest", "West", "Southeast")


def region_for_store(store_index: int) -> str:
    """Region of ``S{2000 + store_index}``, matching seeds/store_master.csv."""
    return REGIONS[(store_index - 1) % len(REGIONS)]


@dataclass(frozen=True, slots=True)
class Shock:
    """One planted incident, and everything the feeds need to express it."""

    kind: str
    """``weather`` or ``carrier`` — which feed carries the visible cause."""

    region: str
    start: date
    end: date

    traffic_multiplier: float = 1.0
    """Share of transactions that still happen. Applied to *line count*, not
    to line value: a storm stops people leaving the house, it does not make
    the ones who came spend less."""

    severe_flag: str = "none"
    carrier: str = ""
    late_rate: float = 0.0
    """Share of the region's shipments that miss their promise date."""

    def covers(self, day: date, region: str) -> bool:
        return region == self.region and self.start <= day <= self.end


def shocks_for(history_end: date) -> tuple[Shock, ...]:
    """The incident calendar, positioned relative to the last day of history.

    Anchored to the end rather than to absolute dates so the same incidents
    land inside the recent window whatever period is generated — a shock
    eleven months back is real history nobody is investigating.
    """
    return (
        # A severe-weather week in the Northeast. Large, short, and confined
        # to one region: the shape a decomposition should isolate cleanly.
        Shock(
            kind="weather",
            region="Northeast",
            start=history_end - timedelta(days=6),
            end=history_end - timedelta(days=3),
            traffic_multiplier=0.55,
            severe_flag="snow",
        ),
        # A carrier degrading in the West. Slower, milder, and visible in the
        # shipping feed rather than the sales feed — the engine has to reach
        # across sources to find it, which is the harder case.
        Shock(
            kind="carrier",
            region="West",
            start=history_end - timedelta(days=13),
            end=history_end - timedelta(days=1),
            traffic_multiplier=0.88,
            carrier="NORTHBOUND",
            late_rate=0.55,
        ),
    )


def traffic_multiplier(shocks: tuple[Shock, ...], day: date, region: str) -> float:
    """Combined traffic effect on one region-day.

    Multiplicative rather than additive so two overlapping incidents compound
    the way real ones do, and so the result can never go negative.
    """
    factor = 1.0
    for shock in shocks:
        if shock.covers(day, region):
            factor *= shock.traffic_multiplier
    return factor
