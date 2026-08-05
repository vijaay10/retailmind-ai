"""Synthetic weather observations — the external covariate feed (RCA §9).

Weather is the one dimension in this platform the retailer neither controls
nor produces. That shapes what the feed is for: it can *explain* a footfall
drop and it can never be the thing anyone fixes, so its role in root-cause
analysis is to rule a region-day in or out, not to generate an action.

Severe days are drawn from the shared incident calendar rather than at random,
so the weather that appears in this feed is the same weather that suppressed
transactions in the POS feed. A correlation the test suite asserts is one that
was deliberately put there.
"""

import csv
import random
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import structlog

from ingestion.generators.shocks import REGIONS, Shock, shocks_for

log = structlog.get_logger(__name__)

HEADER = [
    "region",
    "observation_date",
    "observed_at",
    "ingested_at",
    "temp_c_mean",
    "precipitation_mm",
    "wind_kph_max",
    "severe_flag",
]

#: Baseline climate per region: (mean temperature, typical daily rainfall).
#: Different enough that a naive model cannot treat the regions as one place,
#: which is what makes "compared to normal for this region" a real question.
CLIMATE: dict[str, tuple[float, float]] = {
    "Southwest": (26.0, 0.6),
    "Northeast": (11.0, 2.8),
    "Midwest": (14.0, 2.2),
    "West": (18.0, 1.4),
    "Southeast": (23.0, 3.4),
}


@dataclass(frozen=True, slots=True)
class GeneratedWeather:
    files: list[Path]
    rows: int
    severe_rows: int


def generate_day(
    inbox: Path,
    business_day: date,
    *,
    seed: int = 41,
    history_end: date | None = None,
) -> GeneratedWeather:
    """Write one estate-wide observation file for ``business_day``."""
    rng = random.Random(seed)  # noqa: S311 — determinism is the requirement
    inbox.mkdir(parents=True, exist_ok=True)

    incidents = shocks_for(history_end or business_day)
    rows: list[dict[str, str]] = []
    severe_rows = 0

    for region in REGIONS:
        base_temp, base_rain = CLIMATE[region]
        shock = _weather_shock(incidents, business_day, region)

        if shock is not None:
            # A severe day is not merely a wetter one. The flag and the
            # measurements move together, because a provider that issued a
            # warning on a mild, dry day would be describing a different
            # event from the one that emptied the stores.
            temp = base_temp - rng.uniform(6.0, 11.0)
            rain = base_rain + rng.uniform(14.0, 28.0)
            wind = rng.uniform(58.0, 92.0)
            flag = shock.severe_flag
            severe_rows += 1
        else:
            temp = rng.gauss(base_temp, 3.5)
            rain = max(0.0, rng.gauss(base_rain, base_rain))
            wind = rng.uniform(6.0, 34.0)
            flag = "none"

        rows.append(
            {
                "region": region.upper(),
                "observation_date": business_day.isoformat(),
                "observed_at": f"{business_day.isoformat()} 12:00:00",
                "ingested_at": f"{business_day.isoformat()} 23:30:00",
                "temp_c_mean": f"{temp:.1f}",
                "precipitation_mm": f"{rain:.1f}",
                "wind_kph_max": f"{wind:.1f}",
                "severe_flag": flag,
            }
        )

    path = inbox / f"weather_ALL_{business_day:%Y%m%d}.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADER)
        writer.writeheader()
        writer.writerows(rows)

    log.info(
        "etl.generator.wrote_weather",
        business_day=business_day.isoformat(),
        rows=len(rows),
        severe_rows=severe_rows,
    )
    return GeneratedWeather([path], len(rows), severe_rows)


def _weather_shock(shocks: tuple[Shock, ...], day: date, region: str) -> Shock | None:
    for shock in shocks:
        if shock.kind == "weather" and shock.covers(day, region):
            return shock
    return None
