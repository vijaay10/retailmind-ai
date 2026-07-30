"""Synthetic inventory position files.

Positions are *state*, not events: one row per SKU × store × day describing
what was on hand at close. The generator reflects two realities that make
inventory analytics worth doing at all:

* stock is uneven — a handful of SKU/store pairs sit at or near zero while
  others carry months of cover, and that spread is the entire signal;
* the SW region runs deliberately thin on Outerwear, which is the planted
  stockout the demo's alert and root-cause story depends on.

Deterministic for a given seed, like every generator here.
"""

import csv
import random
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

HEADER = [
    "sku",
    "store_id",
    "snapshot_date",
    "snapshot_ts",
    "on_hand_qty",
    "on_order_qty",
    "in_transit_qty",
    "unit_cost",
    "currency",
    "store_timezone",
]

# Mirrors the product master the warehouse dimensions are built from.
CATALOG_SKUS = (
    [f"OW-{1000 + i}" for i in range(10)]
    + [f"AC-{1010 + i}" for i in range(10)]
    + [f"FW-{1020 + i}" for i in range(10)]
    + [f"BS-{1030 + i}" for i in range(10)]
)

#: Stores whose Outerwear runs dry — the planted stockout event.
STARVED_STORES = frozenset({f"S{2000 + i}" for i in range(1, 13)})


@dataclass(frozen=True, slots=True)
class GeneratedPositions:
    files: list[Path]
    rows: int
    planted_stockouts: int


def generate_day(
    inbox: Path,
    business_day: date,
    *,
    stores: int = 120,
    skus_per_store: int = 40,
    seed: int = 13,
) -> GeneratedPositions:
    """Write one positions file per store for ``business_day``."""
    rng = random.Random(seed)  # noqa: S311 — determinism is the requirement
    inbox.mkdir(parents=True, exist_ok=True)
    stamp = business_day.strftime("%Y%m%d")

    written: list[Path] = []
    total = stockouts = 0

    for index in range(1, stores + 1):
        store_id = f"S{2000 + index}"
        rows = []

        for sku in CATALOG_SKUS[:skus_per_store]:
            starved = store_id in STARVED_STORES and sku.startswith("OW-")

            if starved and rng.random() < 0.4:
                on_hand = 0  # the planted stockout
                stockouts += 1
            elif starved:
                on_hand = rng.randint(1, 4)  # nearly out
            else:
                on_hand = rng.randint(8, 260)

            rows.append(
                {
                    "sku": sku,
                    "store_id": store_id,
                    "snapshot_date": business_day.isoformat(),
                    "snapshot_ts": f"{business_day.isoformat()} 23:00:00",
                    "on_hand_qty": str(on_hand),
                    "on_order_qty": str(rng.choice([0, 0, 12, 24, 48])),
                    "in_transit_qty": str(rng.choice([0, 0, 0, 6, 12])),
                    "unit_cost": f"{rng.uniform(4.0, 95.0):.2f}",
                    "currency": "USD",
                    "store_timezone": "America/Chicago",
                }
            )

        path = inbox / f"inventory_{store_id}_{stamp}.csv"
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=HEADER)
            writer.writeheader()
            writer.writerows(rows)

        written.append(path)
        total += len(rows)

    log.info(
        "etl.generator.wrote_positions",
        business_day=business_day.isoformat(),
        files=len(written),
        rows=total,
        planted_stockouts=stockouts,
    )
    return GeneratedPositions(written, total, stockouts)
