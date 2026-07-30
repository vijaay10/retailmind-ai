"""Synthetic POS file generator (PRD F-20, first cut).

Produces per-store CSV drops shaped exactly like the real feed, so the demo
exercises the *same* pipeline as production — there is no shortcut path.

Two properties matter more than realism:

* **Deterministic.** A given seed always produces identical files, so an
  idempotency test that re-runs a window can assert byte-identical output.
* **Deliberately imperfect.** Real feeds carry unkeyed rows, uncastable
  values, and duplicates. Planting them means the reject and dedup paths are
  exercised every single run instead of only when something breaks.

Trading hours are generated inside one local business day on purpose: a file
labelled ``20260721`` whose rows span local midnight is a legitimate
*failure*, and the freshness rule should catch it. Producing that by accident
in a demo teaches the wrong lesson.

The full planted-ground-truth generator (known anomalies with known causes,
elasticities, seasonality — the evaluation harness for the alert and RCA
engines) builds on this in Phase 1.
"""

import csv
import random
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

HEADER = [
    "order_id",
    "line_no",
    "sku",
    "store_id",
    "transaction_ts",
    "updated_at",
    "quantity",
    "gross_amount",
    "discount_amount",
    "unit_price",
    "currency",
    "channel",
    "store_timezone",
    "promo_code",
    "cashier_id",
    "customer_email",
]

CATEGORIES = ["OW", "AC", "FW", "BS"]  # outerwear, accessories, footwear, basics
CHANNELS = ["store", "ecom_web"]


@dataclass(frozen=True, slots=True)
class GeneratedBatch:
    """What a generation run produced — the caller's receipt."""

    files: list[Path]
    rows: int
    planted_rejects: int
    planted_duplicates: int


def generate_day(
    inbox: Path,
    business_day: date,
    *,
    stores: int = 120,
    lines_per_store: int = 40,
    timezone: str = "America/Chicago",
    seed: int = 7,
    plant_bad_rows: bool = True,
) -> GeneratedBatch:
    """Write one CSV per store for ``business_day``.

    Filenames follow the connector's contract
    (``pos_{store}_{yyyymmdd}.csv``), and every row's ``transaction_ts`` falls
    inside 13:00–23:00 UTC — 08:00–18:00 in Chicago — so the derived business
    date matches the label.
    """
    # Seeded PRNG on purpose: determinism is the requirement here, and a
    # cryptographic generator would defeat it.
    rng = random.Random(seed)  # noqa: S311
    inbox.mkdir(parents=True, exist_ok=True)
    stamp = business_day.strftime("%Y%m%d")

    written: list[Path] = []
    total = planted_rejects = planted_duplicates = 0

    for index in range(1, stores + 1):
        store_id = f"S{2000 + index}"
        rows = [_sale_row(rng, store_id, business_day, line) for line in range(lines_per_store)]

        if plant_bad_rows and index == 1:
            # One unkeyed row and one uncastable measure — the reject path.
            rows.append({**rows[0], "order_id": ""})
            rows.append({**rows[1], "order_id": "POS-BAD-001", "quantity": "abc"})
            planted_rejects += 2

        if plant_bad_rows and index == 2:
            # A re-sent line with a later updated_at — the dedup path.
            duplicate = {
                **rows[0],
                "quantity": "9",
                "updated_at": f"{business_day.isoformat()} 23:59:00",
            }
            rows.append(duplicate)
            planted_duplicates += 1

        path = inbox / f"pos_{store_id}_{stamp}.csv"
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=HEADER)
            writer.writeheader()
            writer.writerows(rows)

        written.append(path)
        total += len(rows)

    log.info(
        "etl.generator.wrote_day",
        business_day=business_day.isoformat(),
        files=len(written),
        rows=total,
        planted_rejects=planted_rejects,
        planted_duplicates=planted_duplicates,
    )
    return GeneratedBatch(written, total, planted_rejects, planted_duplicates)


def _sale_row(rng: random.Random, store_id: str, day: date, line: int) -> dict[str, str]:
    hour = 13 + (line % 11)  # 13:00–23:00 UTC = trading hours in Chicago
    timestamp = f"{day.isoformat()} {hour:02d}:{(line * 7) % 60:02d}:00"
    unit_price = round(rng.uniform(12.0, 180.0), 2)
    quantity = rng.randint(1, 4)

    return {
        "order_id": f"POS-{store_id}-{line:04d}",
        "line_no": "1",
        "sku": f"{rng.choice(CATEGORIES)}-{1000 + line % 40}",
        "store_id": store_id,
        "transaction_ts": timestamp,
        "updated_at": timestamp,
        "quantity": str(quantity),
        "gross_amount": f"{unit_price * quantity:.2f}",
        "discount_amount": "0.00",
        "unit_price": f"{unit_price:.2f}",
        "currency": "USD",
        "channel": rng.choice(CHANNELS),
        "store_timezone": "America/Chicago",
        "promo_code": "",
        "cashier_id": f"C-{rng.randint(1, 9)}",
        "customer_email": "shopper@example.test",
    }
