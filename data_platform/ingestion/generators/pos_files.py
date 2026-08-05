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

from ingestion.generators.customers import build_population, buyers_for_day
from ingestion.generators.shocks import region_for_store, shocks_for, traffic_multiplier

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
    "customer_id",
    "cashier_id",
    "customer_email",
]

# SKUs must exist in the product master the warehouse dimensions are built
# from — inventing them would resolve every sale to the UNKNOWN member and
# make the orphan test (correctly) fail.
CATALOG_SKUS = (
    [f"OW-{1000 + i}" for i in range(10)]
    + [f"AC-{1010 + i}" for i in range(10)]
    + [f"FW-{1020 + i}" for i in range(10)]
    + [f"BS-{1030 + i}" for i in range(10)]
)
CHANNELS = ["store", "ecom_web"]

# Active promotions, matching the promotion master the warehouse seeds from.
# Roughly a third of lines carry one — enough to measure lift against, without
# making "promoted" the baseline.
PROMOS = ["", "", "SUMMER25", "BOGO-OW", "CLEAR-FW"]

#: How many customers exist per daily order line.
#:
#: The base must scale with volume, not sit at a fixed constant: only a
#: fraction of a loyalty file shops on any given day (roughly 4–5% at these
#: purchase probabilities), so a base too small for the line count leaves most
#: lines unattributable and drives the identification rate toward zero. At this
#: ratio the daily shopper pool covers a realistic share of baskets.
CUSTOMERS_PER_DAILY_LINE = 11

#: Share of lines that carry no loyalty id at all. Guest checkout is left in
#: deliberately: the identification rate is itself a reported KPI (Analytics §2).
IDENTIFIED_SHARE = 0.72


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
    history_start: date | None = None,
    history_end: date | None = None,
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

    # The population spans the whole history window, not just this day, so a
    # customer's tier and acquisition date stay fixed across the run. Without
    # the window the population would be rebuilt per day and every customer
    # would silently change behaviour.
    population = build_population(
        stores * lines_per_store * CUSTOMERS_PER_DAILY_LINE,
        history_start or business_day,
        history_end or business_day,
    )
    shoppers = buyers_for_day(
        population, business_day, seed=seed, max_buyers=stores * lines_per_store
    )
    # Assign each shopper a realistic basket (1–3 lines) and lay the
    # assignments out as a flat queue. Picking a random shopper *per line*
    # would hand a small daily pool dozens of orders each, which is how every
    # customer ends up looking Loyal and the lifecycle funnel collapses to one
    # stage. Lines beyond the queue become guest checkout.
    line_owners: list[str] = []
    for shopper in shoppers:
        line_owners.extend([shopper] * rng.randint(1, 3))
    rng.shuffle(line_owners)
    stamp = business_day.strftime("%Y%m%d")

    written: list[Path] = []
    total = planted_rejects = planted_duplicates = 0

    incidents = shocks_for(history_end or business_day)
    suppressed = 0

    for index in range(1, stores + 1):
        store_id = f"S{2000 + index}"
        region = region_for_store(index)

        # A planted incident suppresses *transactions*, not transaction value.
        # Weather keeps people at home; it does not make the ones who came
        # spend less. Expressing it as fewer lines rather than smaller lines
        # is what makes the drop show up as a volume effect in the
        # mix-versus-rate decomposition, which is the diagnostic that matters.
        keep = traffic_multiplier(incidents, business_day, region)
        lines_today = round(lines_per_store * keep)
        suppressed += lines_per_store - lines_today

        rows = []
        for line in range(lines_today):
            owner = line_owners.pop() if line_owners else ""
            rows.append(_sale_row(rng, store_id, business_day, line, owner))

        if plant_bad_rows and index == 1 and rows:
            # One unkeyed row and one uncastable measure — the reject path.
            rows.append({**rows[0], "order_id": ""})
            rows.append({**rows[1], "order_id": "POS-BAD-001", "quantity": "abc"})
            planted_rejects += 2

        if plant_bad_rows and index == 2 and len(rows) > 1:
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
        suppressed_by_incidents=suppressed,
        planted_rejects=planted_rejects,
        planted_duplicates=planted_duplicates,
    )
    return GeneratedBatch(written, total, planted_rejects, planted_duplicates)


def _sale_row(
    rng: random.Random, store_id: str, day: date, line: int, customer_id: str
) -> dict[str, str]:
    hour = 13 + (line % 11)  # 13:00–23:00 UTC = trading hours in Chicago
    timestamp = f"{day.isoformat()} {hour:02d}:{(line * 7) % 60:02d}:00"
    unit_price = round(rng.uniform(12.0, 180.0), 2)
    quantity = rng.randint(1, 4)

    return {
        # Date-qualified: order ids must be unique over time, or a
        # multi-day load collides on the fact grain (one row per order
        # line) and every measure inflates.
        "order_id": f"POS-{store_id}-{day:%Y%m%d}-{line:04d}",
        "line_no": "1",
        "sku": rng.choice(CATALOG_SKUS),
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
        "promo_code": rng.choice(PROMOS),
        # Guest checkout when no shopper owns this line, or by design for a
        # share of identified ones — the identification rate is a KPI.
        "customer_id": customer_id if rng.random() < IDENTIFIED_SHARE else "",
        "cashier_id": f"C-{rng.randint(1, 9)}",
        "customer_email": "shopper@example.test",
    }
