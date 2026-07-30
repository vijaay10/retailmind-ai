"""Metric registry — the governed vocabulary of the platform (ARCH ADR-3).

Every metric the API can compute is declared here once, with its SQL
expression, its grain rules, and how it may be aggregated. Dashboards, the
analytics endpoints, and (later) natural-language queries all compile against
this same registry, which is why the same question cannot produce two answers.

The registry is also the **security boundary**. Callers send metric *names*,
never expressions; anything not declared here cannot be computed, so a
malicious or mistaken parameter fails validation instead of reaching SQL.

Two properties are encoded per metric because getting them wrong is how
warehouses lie:

``additive``
    Whether the measure may be summed across every dimension. Order counts are
    not (summing distinct orders across categories double-counts mixed
    baskets); inventory positions are not additive across *time* specifically.

``ratio_of``
    Ratios must be recomputed from their components at whatever grain the
    caller asked for. Averaging an average is the single most common wrong
    number in retail reporting, and declaring the components makes it
    impossible here.
"""

from dataclasses import dataclass
from enum import StrEnum


class Additivity(StrEnum):
    FULL = "full"
    """Sums correctly across every dimension."""

    SEMI = "semi"
    """Sums across dimensions but NOT across time — inventory positions."""

    NON = "non"
    """Never summed: ratios, distinct counts, and derived rates."""


@dataclass(frozen=True, slots=True)
class Metric:
    key: str
    label: str
    expression: str
    """SQL aggregate over the relation's columns. Never caller-supplied."""
    additivity: Additivity
    unit: str
    description: str
    ratio_of: tuple[str, str] | None = None
    """``(numerator_metric, denominator_metric)`` — recomputed, never averaged."""


@dataclass(frozen=True, slots=True)
class Dimension:
    key: str
    label: str
    column: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class Domain:
    """One analytics module: its relation, metrics, and legal dimensions."""

    key: str
    label: str
    relation: str
    metrics: dict[str, Metric]
    dimensions: dict[str, Dimension]
    date_column: str = "business_date"

    def metric(self, key: str) -> Metric | None:
        return self.metrics.get(key)

    def dimension(self, key: str) -> Dimension | None:
        return self.dimensions.get(key)


def _dims(*pairs: tuple[str, str, str]) -> dict[str, Dimension]:
    return {key: Dimension(key=key, label=label, column=column) for key, label, column in pairs}


# ── Revenue (Analytics §1) ───────────────────────────────────────────

REVENUE = Domain(
    key="revenue",
    label="Revenue Analytics",
    relation="v_mart_sales_daily",
    metrics={
        "net_revenue": Metric(
            key="net_revenue",
            label="Net Revenue",
            expression="sum(net_revenue)",
            additivity=Additivity.FULL,
            unit="currency",
            description="Gross revenue less discounts, with returns netted.",
        ),
        "gross_revenue": Metric(
            key="gross_revenue",
            label="Gross Revenue",
            expression="sum(gross_revenue)",
            additivity=Additivity.FULL,
            unit="currency",
            description="Revenue before discounts.",
        ),
        "discount_amount": Metric(
            key="discount_amount",
            label="Discount",
            expression="sum(discount_amount)",
            additivity=Additivity.FULL,
            unit="currency",
            description="Total discount given.",
        ),
        "units_sold": Metric(
            key="units_sold",
            label="Units Sold",
            expression="sum(units_sold)",
            additivity=Additivity.FULL,
            unit="units",
            description="Net units, returns included as negatives.",
        ),
        "orders": Metric(
            key="orders",
            label="Orders",
            expression="sum(orders)",
            additivity=Additivity.NON,
            unit="count",
            description=(
                "Distinct orders. Pre-aggregated per slice, so summing across "
                "dimensions double-counts mixed-category baskets — the API "
                "reports it only at the grain it was computed."
            ),
        ),
        "aov": Metric(
            key="aov",
            label="Average Order Value",
            expression="sum(net_revenue) / nullif(sum(orders), 0)",
            additivity=Additivity.NON,
            unit="currency",
            description="Recomputed at the requested grain, never averaged.",
            ratio_of=("net_revenue", "orders"),
        ),
        "asp": Metric(
            key="asp",
            label="Average Selling Price",
            expression="sum(net_revenue) / nullif(sum(units_sold), 0)",
            additivity=Additivity.NON,
            unit="currency",
            description="Price realization per unit.",
            ratio_of=("net_revenue", "units_sold"),
        ),
        "discount_rate": Metric(
            key="discount_rate",
            label="Discount Rate",
            expression="sum(discount_amount) / nullif(sum(gross_revenue), 0)",
            additivity=Additivity.NON,
            unit="rate",
            description="Discount as a share of gross — a margin early warning.",
            ratio_of=("discount_amount", "gross_revenue"),
        ),
        "return_rate": Metric(
            key="return_rate",
            label="Return Rate",
            expression="sum(return_amount) / nullif(sum(gross_revenue), 0)",
            additivity=Additivity.NON,
            unit="rate",
            description="Returned value as a share of gross.",
            ratio_of=("return_amount", "gross_revenue"),
        ),
    },
    dimensions=_dims(
        ("category", "Category", "category"),
        ("department", "Department", "department"),
        ("region", "Region", "region"),
        ("channel", "Channel", "channel_code"),
        ("channel_group", "Channel Group", "channel_group"),
        ("store_cluster", "Store Cluster", "store_cluster"),
        ("business_date", "Date", "business_date"),
    ),
)


# ── Store (Analytics §3) ─────────────────────────────────────────────

STORE = Domain(
    key="store",
    label="Store Analytics",
    relation="v_mart_store_daily",
    metrics={
        "net_revenue": Metric(
            "net_revenue",
            "Net Revenue",
            "sum(net_revenue)",
            Additivity.FULL,
            "currency",
            "Store net revenue.",
        ),
        "margin_amount": Metric(
            "margin_amount",
            "Margin",
            "sum(margin_amount)",
            Additivity.FULL,
            "currency",
            "Gross margin.",
        ),
        "units_sold": Metric(
            "units_sold", "Units Sold", "sum(units_sold)", Additivity.FULL, "units", "Units sold."
        ),
        "orders": Metric(
            "orders",
            "Orders",
            "sum(orders)",
            Additivity.NON,
            "count",
            "Distinct orders at the computed grain.",
        ),
        "aov": Metric(
            "aov",
            "Average Order Value",
            "sum(net_revenue) / nullif(sum(orders), 0)",
            Additivity.NON,
            "currency",
            "Recomputed at grain.",
            ratio_of=("net_revenue", "orders"),
        ),
        "margin_rate": Metric(
            "margin_rate",
            "Margin Rate",
            "sum(margin_amount) / nullif(sum(net_revenue), 0)",
            Additivity.NON,
            "rate",
            "Margin as a share of net revenue.",
            ratio_of=("margin_amount", "net_revenue"),
        ),
        "identified_customers": Metric(
            "identified_customers",
            "Identified Customers",
            "sum(identified_customers)",
            Additivity.NON,
            "count",
            "Distinct loyalty customers at the computed grain.",
        ),
    },
    dimensions=_dims(
        ("store", "Store", "store_id"),
        ("store_name", "Store Name", "store_name"),
        ("city", "City", "city"),
        ("district", "District", "district"),
        ("region", "Region", "region"),
        ("store_format", "Format", "store_format"),
        ("store_cluster", "Cluster", "store_cluster"),
        ("business_date", "Date", "business_date"),
    ),
)


# ── Customer (Analytics §2) ──────────────────────────────────────────

CUSTOMER = Domain(
    key="customer",
    label="Customer Analytics",
    relation="v_mart_customer_rfm",
    date_column="",  # segment aggregates carry no date grain
    metrics={
        "customers": Metric(
            "customers",
            "Customers",
            "sum(customers)",
            Additivity.FULL,
            "count",
            "Customers in the segment.",
        ),
        "segment_value": Metric(
            "segment_value",
            "Segment Value",
            "sum(segment_value)",
            Additivity.FULL,
            "currency",
            "Lifetime value in the segment.",
        ),
        "avg_lifetime_value": Metric(
            "avg_lifetime_value",
            "Avg Lifetime Value",
            "sum(segment_value) / nullif(sum(customers), 0)",
            Additivity.NON,
            "currency",
            "Recomputed from totals, not averaged.",
            ratio_of=("segment_value", "customers"),
        ),
        "repeat_customers": Metric(
            "repeat_customers",
            "Repeat Customers",
            "sum(repeat_customers)",
            Additivity.FULL,
            "count",
            "Customers with more than one order.",
        ),
        "repeat_rate": Metric(
            "repeat_rate",
            "Repeat Rate",
            "sum(repeat_customers) / nullif(sum(customers), 0)",
            Additivity.NON,
            "rate",
            "Share of customers who returned.",
            ratio_of=("repeat_customers", "customers"),
        ),
    },
    dimensions=_dims(("segment", "RFM Segment", "rfm_segment")),
)


# ── Inventory (Analytics §4) ─────────────────────────────────────────

INVENTORY = Domain(
    key="inventory",
    label="Inventory Analytics",
    relation="v_mart_inventory_daily",
    metrics={
        "on_hand_units": Metric(
            "on_hand_units",
            "On Hand Units",
            "sum(on_hand_units)",
            Additivity.SEMI,
            "units",
            "Units in stock. Semi-additive: valid across stores and SKUs, "
            "never across dates — adding two days invents inventory.",
        ),
        "inventory_value_cost": Metric(
            "inventory_value_cost",
            "Inventory Value",
            "sum(inventory_value_cost)",
            Additivity.SEMI,
            "currency",
            "Stock at cost. Semi-additive, as above.",
        ),
        "stockout_rate": Metric(
            "stockout_rate",
            "Stockout Rate",
            "sum(stockout_positions) / nullif(sum(sku_store_positions), 0)",
            Additivity.NON,
            "rate",
            "Share of SKU-store positions at zero, recomputed from counts so "
            "large and small stores weigh correctly.",
            ratio_of=("stockout_positions", "sku_store_positions"),
        ),
        "stockout_positions": Metric(
            "stockout_positions",
            "Stockout Positions",
            "sum(stockout_positions)",
            Additivity.FULL,
            "count",
            "SKU-store pairs at zero stock.",
        ),
        "cover_days": Metric(
            "cover_days",
            "Cover Days",
            "sum(on_hand_units) / nullif(sum(sku_store_positions), 0)",
            Additivity.NON,
            "days",
            "Days of supply, recomputed at grain — never an average of averages.",
        ),
        "overstocked_positions": Metric(
            "overstocked_positions",
            "Overstocked Positions",
            "sum(overstocked_positions)",
            Additivity.FULL,
            "count",
            "Positions carrying more than 12 weeks of cover.",
        ),
    },
    dimensions=_dims(
        ("category", "Category", "category"),
        ("department", "Department", "department"),
        ("region", "Region", "region"),
        ("store_cluster", "Store Cluster", "store_cluster"),
        ("business_date", "Date", "business_date"),
    ),
)


# ── Marketing (Analytics §6) ─────────────────────────────────────────

MARKETING = Domain(
    key="marketing",
    label="Marketing & Promotion Analytics",
    relation="v_mart_promo_daily",
    metrics={
        "promo_revenue": Metric(
            "promo_revenue",
            "Promo Revenue",
            "sum(promo_revenue)",
            Additivity.FULL,
            "currency",
            "Revenue on promoted lines.",
        ),
        "subsidy_amount": Metric(
            "subsidy_amount",
            "Subsidy",
            "sum(subsidy_amount)",
            Additivity.FULL,
            "currency",
            "Discount funded by the promotion.",
        ),
        "promo_margin": Metric(
            "promo_margin",
            "Promo Margin",
            "sum(promo_margin)",
            Additivity.FULL,
            "currency",
            "Margin earned on promoted lines.",
        ),
        "promo_units": Metric(
            "promo_units",
            "Promo Units",
            "sum(promo_units)",
            Additivity.FULL,
            "units",
            "Units sold on promotion.",
        ),
        "promo_orders": Metric(
            "promo_orders",
            "Promo Orders",
            "sum(promo_orders)",
            Additivity.NON,
            "count",
            "Orders containing a promoted line.",
        ),
        "effective_depth": Metric(
            "effective_depth",
            "Effective Depth",
            "sum(subsidy_amount) / nullif(sum(promo_revenue) + sum(subsidy_amount), 0)",
            Additivity.NON,
            "rate",
            "Realised discount depth — often below the planned depth.",
            ratio_of=("subsidy_amount", "promo_revenue"),
        ),
        "promo_margin_rate": Metric(
            "promo_margin_rate",
            "Promo Margin Rate",
            "sum(promo_margin) / nullif(sum(promo_revenue), 0)",
            Additivity.NON,
            "rate",
            "Margin share on promoted revenue.",
            ratio_of=("promo_margin", "promo_revenue"),
        ),
    },
    dimensions=_dims(
        ("promo", "Promotion", "promo_code"),
        ("promo_name", "Promotion Name", "promo_name"),
        ("mechanic", "Mechanic", "mechanic"),
        ("funding", "Funding", "funding"),
        ("business_date", "Date", "business_date"),
    ),
)


# ── Profitability (Analytics §8) ─────────────────────────────────────

PROFITABILITY = Domain(
    key="profitability",
    label="Profitability Analytics",
    relation="v_mart_sales_daily",
    metrics={
        "margin_amount": Metric(
            "margin_amount",
            "Gross Margin",
            "sum(margin_amount)",
            Additivity.FULL,
            "currency",
            "Net revenue less COGS.",
        ),
        "cogs_amount": Metric(
            "cogs_amount",
            "COGS",
            "sum(cogs_amount)",
            Additivity.FULL,
            "currency",
            "Cost of goods sold at as-was cost.",
        ),
        "net_revenue": Metric(
            "net_revenue",
            "Net Revenue",
            "sum(net_revenue)",
            Additivity.FULL,
            "currency",
            "Revenue net of discounts.",
        ),
        "margin_rate": Metric(
            "margin_rate",
            "Margin Rate",
            "sum(margin_amount) / nullif(sum(net_revenue), 0)",
            Additivity.NON,
            "rate",
            "Margin as a share of net revenue.",
            ratio_of=("margin_amount", "net_revenue"),
        ),
        "markdown_impact": Metric(
            "markdown_impact",
            "Markdown Impact",
            "sum(discount_amount)",
            Additivity.FULL,
            "currency",
            "Margin foregone to discounting.",
        ),
        "gross_margin_per_unit": Metric(
            "gross_margin_per_unit",
            "Margin per Unit",
            "sum(margin_amount) / nullif(sum(units_sold), 0)",
            Additivity.NON,
            "currency",
            "Unit economics at the requested grain.",
            ratio_of=("margin_amount", "units_sold"),
        ),
    },
    dimensions=_dims(
        ("category", "Category", "category"),
        ("department", "Department", "department"),
        ("region", "Region", "region"),
        ("channel", "Channel", "channel_code"),
        ("store_cluster", "Store Cluster", "store_cluster"),
        ("business_date", "Date", "business_date"),
    ),
)


# ── Product (Analytics §5) ───────────────────────────────────────────

PRODUCT = Domain(
    key="product",
    label="Product Analytics",
    relation="v_fct_sales",
    metrics={
        "net_revenue": Metric(
            "net_revenue",
            "Net Revenue",
            "sum(net_amount)",
            Additivity.FULL,
            "currency",
            "Revenue attributed to the product.",
        ),
        "units_sold": Metric(
            "units_sold", "Units Sold", "sum(quantity)", Additivity.FULL, "units", "Net units sold."
        ),
        "margin_amount": Metric(
            "margin_amount",
            "Margin",
            "sum(margin_amount)",
            Additivity.FULL,
            "currency",
            "Gross margin earned.",
        ),
        "orders": Metric(
            "orders",
            "Orders",
            "count(distinct order_id)",
            Additivity.NON,
            "count",
            "Distinct orders containing the product.",
        ),
        "margin_rate": Metric(
            "margin_rate",
            "Margin Rate",
            "sum(margin_amount) / nullif(sum(net_amount), 0)",
            Additivity.NON,
            "rate",
            "Margin share of revenue.",
            ratio_of=("margin_amount", "net_revenue"),
        ),
        "asp": Metric(
            "asp",
            "Average Selling Price",
            "sum(net_amount) / nullif(sum(quantity), 0)",
            Additivity.NON,
            "currency",
            "Realised price per unit.",
            ratio_of=("net_revenue", "units_sold"),
        ),
    },
    dimensions=_dims(
        ("sku", "SKU", "sku"),
        ("product_name", "Product", "product_name"),
        ("category", "Category", "category"),
        ("subcategory", "Subcategory", "subcategory"),
        ("department", "Department", "department"),
        ("brand", "Brand", "brand"),
        ("region", "Region", "region"),
        ("channel", "Channel", "channel_code"),
        ("business_date", "Date", "business_date"),
    ),
)


DOMAINS: dict[str, Domain] = {
    domain.key: domain
    for domain in (
        REVENUE,
        STORE,
        CUSTOMER,
        INVENTORY,
        MARKETING,
        PROFITABILITY,
        PRODUCT,
    )
}


def get_domain(key: str) -> Domain | None:
    return DOMAINS.get(key)
