"""Column-name matching — the one place alias logic and fuzzy scoring live.

`detection.py` (which dataset is this?) and `mapping.py` (which of my columns
is which canonical field?) are the same question asked at two grains: the
first asks it once per *schema*, the second once per *column*. Both need
identical scoring, so it lives here rather than being copy-pasted twice and
drifting.

Scoring, in order, cheapest and most certain first:

1. **Exact match** — column names equal after normalizing case, whitespace,
   and punctuation. Score ``1.0``.
2. **Synonym match** — the uploaded name is a known alias of the canonical
   field (``sku`` for ``product_code``, ``qty`` for ``quantity``, …), drawn
   from a small hand-maintained table of common retail naming conventions.
   Score ``0.9`` — high confidence, but a notch below an exact match so an
   exact match always wins a conflict.
3. **Fuzzy match** — `difflib.SequenceMatcher` ratio on the normalized
   strings, only attempted when both names are long enough (``>= 4`` chars
   after normalizing) that a coincidental high ratio is unlikely. Score is
   the ratio itself, only kept when it clears ``FUZZY_THRESHOLD``.
4. Otherwise: no match, score ``0.0``.

Every score is inspectable — call `match_score` directly to see why two names
did or did not match.
"""

import re
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

from ingestion.domain.schema import SourceSchema

#: A score at or above this is treated as "confident enough to use" by both
#: detection and mapping. Below it, we would rather say "no match" than guess.
CONFIDENT_THRESHOLD = 0.5

#: Fuzzy matches below this ratio are discarded outright — SequenceMatcher
#: ratio degrades gracefully, but low ratios between short strings are noise
#: more often than signal (e.g. "id" against half the alphabet).
FUZZY_THRESHOLD = 0.72

#: Fuzzy matching is skipped for names shorter than this (after normalizing).
#: Short strings produce spuriously high ratios ("id" vs "id_no" vs "bid").
_MIN_FUZZY_LEN = 4

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_name(name: str) -> str:
    """Case- and punctuation-insensitive form used for every comparison.

    ``"Sale Timestamp"``, ``"sale_timestamp"``, and ``"SALE-TIMESTAMP"`` all
    normalize to ``"saletimestamp"``.
    """
    return _NON_ALNUM.sub("", name.strip().lower())


#: Canonical column name -> known real-world aliases for it, drawn from the
#: naming conventions of the retail files this platform actually ingests
#: (POS extracts, ERP exports, WMS feeds). Deliberately small and reviewed —
#: an alias table that grows without bound turns into an unreviewable pile of
#: guesses, which is exactly the failure mode a synonym table exists to avoid.
SYNONYMS: dict[str, tuple[str, ...]] = {
    # pos.sales business keys / event time
    "order_id": (
        "transaction_id",
        "order_no",
        "order_number",
        "invoice_id",
        "invoice_no",
        "transaction_no",
        "txn_id",
    ),
    "sku": (
        "product_code",
        "item_code",
        "product_id",
        "sku_id",
        "item_id",
        "item_number",
    ),
    "store_id": (
        "store",
        "location_id",
        "location",
        "branch_id",
        "branch",
        "site_id",
        "outlet_id",
    ),
    "transaction_ts": (
        "date",
        "business_date",
        "sale_timestamp",
        "sale_date",
        "transaction_date",
        "invoice_date",
        "order_date",
    ),
    "quantity": (
        "qty",
        "units",
        "units_sold",
        "quantity_sold",
        "unit_sold",
        "sold_qty",
    ),
    "gross_amount": (
        "sales",
        "revenue",
        "net_value",
        "net_sales",
        "sale_amount",
        "total_amount",
        "amount",
    ),
    "unit_price": ("price", "selling_price", "list_price", "unit_cost_price"),
    # product.master
    "product_id": ("sku", "product_code", "item_code", "item_id", "sku_id"),
    "product_name": ("item_name", "product_title", "description", "item_description"),
    "unit_cost": ("cost", "purchase_price", "landed_cost", "cost_price"),
    "selling_price": ("price", "list_price", "retail_price", "unit_price"),
    # store.master
    "store_name": ("location_name", "branch_name", "outlet_name"),
    "region": ("area", "zone", "territory"),
    "country": ("nation", "country_code"),
    # inventory.positions / purchasing.orders
    "on_hand_qty": ("stock_on_hand", "on_hand", "inventory_qty", "stock_qty"),
    "supplier_id": ("vendor_id", "supplier_code", "vendor_code"),
    "po_number": ("purchase_order_no", "po_no", "purchase_order_id"),
    # fulfilment.deliveries
    "shipment_id": ("tracking_id", "shipment_no", "tracking_number"),
    "carrier": ("shipping_carrier", "courier"),
}

#: Pre-normalized for fast lookup: canonical column -> set of normalized aliases.
_NORMALIZED_SYNONYMS: dict[str, frozenset[str]] = {
    canonical: frozenset(normalize_name(alias) for alias in aliases)
    for canonical, aliases in SYNONYMS.items()
}


def match_score(source_column: str, canonical_column: str) -> tuple[float, str]:
    """Score how well an uploaded column name matches a canonical field name.

    Returns ``(score, reason)``. ``score`` is in ``[0.0, 1.0]``; ``reason`` is
    a short human-readable explanation suitable for display in an onboarding
    review screen ("exact match", "synonym match", "fuzzy match (0.82)", or
    "no confident match"). See the module docstring for the scoring order.
    """
    norm_source = normalize_name(source_column)
    norm_canonical = normalize_name(canonical_column)
    if not norm_source or not norm_canonical:
        return 0.0, "no confident match"

    if norm_source == norm_canonical:
        return 1.0, "exact match"

    aliases = _NORMALIZED_SYNONYMS.get(canonical_column, frozenset())
    if norm_source in aliases:
        return 0.9, "synonym match"

    if len(norm_source) >= _MIN_FUZZY_LEN and len(norm_canonical) >= _MIN_FUZZY_LEN:
        ratio = SequenceMatcher(None, norm_source, norm_canonical).ratio()
        if ratio >= FUZZY_THRESHOLD:
            return ratio, f"fuzzy match ({ratio:.2f})"

    return 0.0, "no confident match"


#: All declared source schemas ship as YAML under this directory — the same
#: files `CsvFileConnector` and the quality gate load from.
SCHEMAS_DIR = Path(__file__).resolve().parents[1] / "ingestion" / "schemas"


@lru_cache(maxsize=1)
def load_known_schemas() -> tuple[SourceSchema, ...]:
    """Every declared `SourceSchema`, loaded once and cached.

    This is the registry `detect_dataset_type` scores an upload against. It
    reads real, versioned contracts from disk — it does not hardcode a list
    of dataset types, so a new `{source}/{table}.yml` is picked up for free.
    """
    return tuple(SourceSchema.from_yaml(path) for path in sorted(SCHEMAS_DIR.glob("*/*.yml")))
