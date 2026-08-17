"""Synthetic outbound deliveries — the shipping feed (RCA).

Only ecommerce orders ship. Store purchases leave with the customer and never
appear here, which means an absent shipment is not a fast one: the fact has to
carry the fulfilment channel or "on-time rate" quietly becomes "on-time rate
among orders we happened to ship", and the two diverge exactly when the mix
shifts.

Carriers differ, consistently and by design, and one of them degrades during
the window declared in the shared incident calendar. That degradation is the
harder root-cause case in the estate: the visible symptom is in the sales feed
and the cause is in this one, so an engine that only decomposes revenue by
region will find *where* and never *why*.
"""

import csv
import random
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import structlog

from ingestion.generators.shocks import Shock, region_for_store, shocks_for

log = structlog.get_logger(__name__)

HEADER = [
    "shipment_id",
    "order_id",
    "store_id",
    "carrier",
    "shipped_at",
    "updated_at",
    "promised_date",
    "delivered_date",
    "delivery_status",
]

#: Carrier behaviour: (promised transit days, baseline late rate).
#: Northbound is the one the incident calendar degrades; the others exist so
#: that "this carrier is late" is a comparison rather than an assertion.
CARRIERS: dict[str, tuple[int, float]] = {
    "NORTHBOUND": (3, 0.08),
    "CROSSLINE": (4, 0.06),
    "PARCELWAY": (2, 0.11),
}

#: Share of a store's daily orders fulfilled by shipping rather than carried
#: out. Ecommerce penetration, in effect.
SHIPPED_SHARE = 0.28


@dataclass(frozen=True, slots=True)
class GeneratedDeliveries:
    files: list[Path]
    rows: int
    late_rows: int
    in_transit_rows: int


def generate_day(
    inbox: Path,
    business_day: date,
    *,
    stores: int = 120,
    orders_per_store: int = 12,
    seed: int = 55,
    history_end: date | None = None,
) -> GeneratedDeliveries:
    """Write one estate-wide delivery file for shipments raised on ``business_day``."""
    rng = random.Random(seed)  # noqa: S311 — determinism is the requirement
    inbox.mkdir(parents=True, exist_ok=True)

    horizon = history_end or business_day
    incidents = shocks_for(horizon)
    rows: list[dict[str, str]] = []
    late_rows = in_transit_rows = 0

    for index in range(1, stores + 1):
        store_id = f"S{2000 + index}"
        region = region_for_store(index)
        shipped = max(1, round(orders_per_store * SHIPPED_SHARE))

        for sequence in range(shipped):
            carrier = rng.choice(list(CARRIERS))
            transit_days, base_late_rate = CARRIERS[carrier]
            promised = business_day + timedelta(days=transit_days)

            late_rate = _late_rate(incidents, business_day, region, carrier, base_late_rate)
            is_late = rng.random() < late_rate
            actual_days = transit_days + (rng.randint(2, 6) if is_late else rng.randint(-1, 0))
            delivered = business_day + timedelta(days=max(1, actual_days))

            if delivered > horizon:
                # Still moving. Not late — it has not yet had the chance to
                # be, and counting it as a miss would make a carrier's score
                # worsen simply because orders were placed recently.
                status, delivered_value = "in_transit", ""
                in_transit_rows += 1
            else:
                status = "delivered" if delivered <= promised else "delayed"
                delivered_value = delivered.isoformat()
                if status == "delayed":
                    late_rows += 1

            rows.append(
                {
                    "shipment_id": f"SHP-{business_day:%Y%m%d}-{store_id}-{sequence:03d}",
                    "order_id": f"POS-{store_id}-{business_day:%Y%m%d}-{sequence:04d}",
                    "store_id": store_id,
                    "carrier": carrier,
                    "shipped_at": f"{business_day.isoformat()} 16:00:00",
                    "updated_at": f"{min(delivered, horizon).isoformat()} 20:00:00",
                    "promised_date": promised.isoformat(),
                    "delivered_date": delivered_value,
                    "delivery_status": status,
                }
            )

    path = inbox / f"fulfilment_ALL_{business_day:%Y%m%d}.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADER)
        writer.writeheader()
        writer.writerows(rows)

    log.info(
        "etl.generator.wrote_deliveries",
        business_day=business_day.isoformat(),
        rows=len(rows),
        late_rows=late_rows,
        in_transit_rows=in_transit_rows,
    )
    return GeneratedDeliveries([path], len(rows), late_rows, in_transit_rows)


def _late_rate(
    shocks: tuple[Shock, ...], day: date, region: str, carrier: str, baseline: float
) -> float:
    for shock in shocks:
        if shock.kind == "carrier" and shock.carrier == carrier and shock.covers(day, region):
            return shock.late_rate
    return baseline
