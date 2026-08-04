"""Synthetic purchase orders — the replenishment feed (Analytics §7, SE-1).

Purchase orders are what make lead time, supplier reliability, and inventory
aging computable. Without them those questions have no honest answer, only a
plausible-looking guess.

Two properties make the resulting supplier analytics real:

* **Suppliers differ, consistently.** Each has its own lead-time centre and
  spread, so one is reliably fast, another reliably late, and a third
  erratic — and erratic is the interesting case, because variability drives
  safety stock far more than average lateness does.
* **Some orders are still open.** A PO with no receipt is not the same as one
  that never arrived; keeping open lines in the feed is what lets the fact
  distinguish "in flight" from "failed".

One file per business day, carrying the lines *raised* that day. Because the
source is an accumulating snapshot, a receipt updates the original line rather
than creating a new one — the same natural key arrives again with a later
``updated_at``, which is what the dedup tiebreaker resolves.
"""

import csv
import random
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

HEADER = [
    "po_number",
    "line_no",
    "supplier_id",
    "sku",
    "store_id",
    "order_ts",
    "updated_at",
    "promise_date",
    "receipt_date",
    "ordered_qty",
    "received_qty",
    "unit_cost",
    "currency",
    "po_status",
]

CATALOG_SKUS = (
    [f"OW-{1000 + i}" for i in range(10)]
    + [f"AC-{1010 + i}" for i in range(10)]
    + [f"FW-{1020 + i}" for i in range(10)]
    + [f"BS-{1030 + i}" for i in range(10)]
)

#: Supplier behaviour: (contract lead time, actual mean, actual spread, fill rate).
#:
#: Meridian is the slow-and-erratic one and Harbourline the unreliable filler;
#: they exist so the supplier scorecard has something to find. A generator
#: where every vendor performs identically produces a scorecard nobody needs.
SUPPLIER_BEHAVIOUR: dict[str, tuple[int, float, float, float]] = {
    "SUP-001": (10, 8.2, 1.0, 0.99),  # reliable: comfortably inside promise
    "SUP-002": (21, 27.0, 7.5, 0.93),  # slow and erratic — the problem vendor
    "SUP-003": (28, 30.0, 3.0, 0.86),  # on time-ish, chronically short-ships
    "SUP-004": (14, 12.5, 1.8, 0.97),  # solid
    "SUP-005": (18, 19.5, 4.5, 0.95),
}

SKU_SUPPLIER = {
    **{f"OW-{1000 + i}": ("SUP-001" if i % 2 == 0 else "SUP-002") for i in range(10)},
    **{f"AC-{1010 + i}": "SUP-003" for i in range(10)},
    **{f"FW-{1020 + i}": "SUP-004" for i in range(10)},
    **{f"BS-{1030 + i}": ("SUP-005" if i % 2 == 0 else "SUP-002") for i in range(10)},
}


@dataclass(frozen=True, slots=True)
class GeneratedOrders:
    files: list[Path]
    rows: int
    open_lines: int
    late_lines: int


def generate_day(
    inbox: Path,
    business_day: date,
    *,
    stores: int = 40,
    lines: int = 60,
    seed: int = 91,
    as_of: date | None = None,
) -> GeneratedOrders:
    """Write the purchase-order lines raised on ``business_day``.

    ``as_of`` is the last day the warehouse knows about. Lines whose receipt
    would fall after it stay open — modelling the truth that on any given day
    some orders are simply still in transit.
    """
    rng = random.Random(seed)  # noqa: S311 — determinism is the requirement
    inbox.mkdir(parents=True, exist_ok=True)
    horizon = as_of or business_day

    rows: list[dict[str, str]] = []
    open_lines = late_lines = 0

    for line in range(lines):
        sku = rng.choice(CATALOG_SKUS)
        supplier = SKU_SUPPLIER[sku]
        contract_days, mean_days, spread, fill_rate = SUPPLIER_BEHAVIOUR[supplier]

        promise = business_day + timedelta(days=contract_days)
        actual_days = max(1, round(rng.gauss(mean_days, spread)))
        receipt = business_day + timedelta(days=actual_days)

        ordered = rng.choice([12, 24, 36, 48, 72])
        store_id = f"S{2000 + rng.randint(1, stores)}"

        if receipt > horizon:
            # Still in transit as far as the warehouse knows.
            status, receipt_value, received = "confirmed", "", ""
            open_lines += 1
        else:
            status = "received"
            receipt_value = receipt.isoformat()
            # Short-shipping is a supplier trait, not noise: it is what
            # separates "on time" from "on time in full".
            received = str(
                int(ordered * (1.0 if rng.random() < fill_rate else rng.uniform(0.6, 0.95)))
            )
            if receipt > promise:
                late_lines += 1

        updated = min(receipt, horizon) if status == "received" else business_day
        rows.append(
            {
                "po_number": f"PO-{business_day:%Y%m%d}-{line:04d}",
                "line_no": "1",
                "supplier_id": supplier,
                "sku": sku,
                "store_id": store_id,
                "order_ts": f"{business_day.isoformat()} 09:00:00",
                "updated_at": f"{updated.isoformat()} 18:00:00",
                "promise_date": promise.isoformat(),
                "receipt_date": receipt_value,
                "ordered_qty": str(ordered),
                "received_qty": received,
                "unit_cost": f"{rng.uniform(4.0, 95.0):.2f}",
                "currency": "USD",
                "po_status": status,
            }
        )

    path = inbox / f"purchasing_ALL_{business_day:%Y%m%d}.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADER)
        writer.writeheader()
        writer.writerows(rows)

    log.info(
        "etl.generator.wrote_purchase_orders",
        business_day=business_day.isoformat(),
        rows=len(rows),
        open_lines=open_lines,
        late_lines=late_lines,
    )
    return GeneratedOrders([path], len(rows), open_lines, late_lines)
