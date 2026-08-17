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


# ── Revenue (Analytics) ───────────────────────────────────────────

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


# ── Store (Analytics) ─────────────────────────────────────────────

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


# ── Customer (Analytics) ─────────────────────────────────────────
#
# Every customer domain reads a *segment-level* relation. Individual rows
# exist in dim_customer for joins, but nothing here can project a person:
# the product analyses cohorts, and making that structural beats trusting a
# policy nobody enforces.

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


RFM_GRID = Domain(
    key="rfm",
    label="RFM Grid",
    relation="v_mart_customer_rfm_grid",
    date_column="",
    metrics={
        "customers": Metric(
            "customers",
            "Customers",
            "sum(customers)",
            Additivity.FULL,
            "count",
            "Customers in the cell.",
        ),
        "segment_value": Metric(
            "segment_value",
            "Cell Value",
            "sum(segment_value)",
            Additivity.FULL,
            "currency",
            "Lifetime value in the cell.",
        ),
        "avg_lifetime_value": Metric(
            "avg_lifetime_value",
            "Avg Lifetime Value",
            "sum(segment_value) / nullif(sum(customers), 0)",
            Additivity.NON,
            "currency",
            "Recomputed at grain.",
            ratio_of=("segment_value", "customers"),
        ),
        "at_risk_customers": Metric(
            "at_risk_customers",
            "At Risk",
            "sum(at_risk_customers)",
            Additivity.FULL,
            "count",
            "Customers overdue by two or more purchase cycles.",
        ),
        "vip_customers": Metric(
            "vip_customers",
            "VIPs",
            "sum(vip_customers)",
            Additivity.FULL,
            "count",
            "Top-decile repeat customers.",
        ),
    },
    dimensions=_dims(
        ("recency_score", "Recency Score", "recency_score"),
        ("frequency_score", "Frequency Score", "frequency_score"),
    ),
)


COHORTS = Domain(
    key="cohorts",
    label="Retention Cohorts",
    relation="v_mart_customer_cohorts",
    date_column="",  # the axis is weeks-since-acquisition, not a calendar date
    metrics={
        "cohort_customers": Metric(
            "cohort_customers",
            "Cohort Size",
            "max(cohort_customers)",
            Additivity.NON,
            "count",
            "Customers acquired in the cohort week; constant across the row, so max not sum.",
        ),
        "active_customers": Metric(
            "active_customers",
            "Active",
            "sum(active_customers)",
            Additivity.FULL,
            "count",
            "Customers who purchased.",
        ),
        "retention_rate": Metric(
            "retention_rate",
            "Retention Rate",
            "sum(active_customers) / nullif(max(cohort_customers), 0)",
            Additivity.NON,
            "rate",
            "Share of the cohort still active, recomputed at grain.",
            ratio_of=("active_customers", "cohort_customers"),
        ),
        "revenue": Metric(
            "revenue",
            "Revenue",
            "sum(revenue)",
            Additivity.FULL,
            "currency",
            "Revenue from the cohort.",
        ),
        "cumulative_value_per_customer": Metric(
            "cumulative_value_per_customer",
            "Cumulative Value per Customer",
            "max(cumulative_value_per_customer)",
            Additivity.NON,
            "currency",
            "Running value per acquired customer — the payback curve.",
        ),
    },
    dimensions=_dims(
        ("cohort_week", "Cohort Week", "cohort_week"),
        ("weeks_since", "Weeks Since Acquisition", "weeks_since_acquisition"),
    ),
)


LIFECYCLE = Domain(
    key="lifecycle",
    label="Customer Journey",
    relation="v_mart_customer_lifecycle",
    date_column="",
    metrics={
        "customers": Metric(
            "customers",
            "Customers",
            "sum(customers)",
            Additivity.FULL,
            "count",
            "Customers at this stage.",
        ),
        "reached_stage": Metric(
            "reached_stage",
            "Reached Stage",
            "max(reached_stage)",
            Additivity.NON,
            "count",
            "Customers who reached this stage or beyond — cumulative, so max not sum.",
        ),
        "conversion_from_previous": Metric(
            "conversion_from_previous",
            "Conversion",
            "max(conversion_from_previous)",
            Additivity.NON,
            "rate",
            "Share of the previous stage that progressed.",
        ),
        "stage_value": Metric(
            "stage_value",
            "Stage Value",
            "sum(stage_value)",
            Additivity.FULL,
            "currency",
            "Lifetime value at this stage.",
        ),
        "at_risk_rate": Metric(
            "at_risk_rate",
            "At-Risk Rate",
            "sum(at_risk_customers) / nullif(sum(customers), 0)",
            Additivity.NON,
            "rate",
            "Share of the stage drifting away.",
            ratio_of=("at_risk_customers", "customers"),
        ),
        "at_risk_customers": Metric(
            "at_risk_customers",
            "At Risk",
            "sum(at_risk_customers)",
            Additivity.FULL,
            "count",
            "Customers overdue to purchase.",
        ),
        "avg_days_between_orders": Metric(
            "avg_days_between_orders",
            "Purchase Cadence",
            "avg(avg_days_between_orders)",
            Additivity.NON,
            "days",
            "Average days between orders at this stage.",
        ),
    },
    dimensions=_dims(
        ("stage", "Lifecycle Stage", "lifecycle_stage"),
        # Exposed so the funnel has a defined sequence; a client should not
        # have to hardcode New → Repeat → Established → Loyal.
        ("stage_order", "Stage Order", "stage_order"),
    ),
)


CHURN = Domain(
    key="churn",
    label="Churn Risk",
    relation="v_mart_customer_churn_risk",
    date_column="",
    metrics={
        "customers": Metric(
            "customers",
            "Customers",
            "sum(customers)",
            Additivity.FULL,
            "count",
            "Customers in the risk band.",
        ),
        "value_at_risk": Metric(
            "value_at_risk",
            "Value at Risk",
            "sum(value_at_risk)",
            Additivity.FULL,
            "currency",
            "Lifetime value carried by these customers — the number "
            "that earns a retention meeting.",
        ),
        "vip_value_at_risk": Metric(
            "vip_value_at_risk",
            "VIP Value at Risk",
            "sum(vip_value_at_risk)",
            Additivity.FULL,
            "currency",
            "Value held by at-risk VIPs: expensive to replace and still reachable.",
        ),
        "avg_cycles_overdue": Metric(
            "avg_cycles_overdue",
            "Avg Cycles Overdue",
            "avg(avg_cycles_overdue)",
            Additivity.NON,
            "count",
            "How many purchase cycles have elapsed unfulfilled.",
        ),
        "vip_customers": Metric(
            "vip_customers",
            "VIPs",
            "sum(vip_customers)",
            Additivity.FULL,
            "count",
            "VIPs in this band.",
        ),
    },
    dimensions=_dims(
        ("risk_band", "Risk Band", "churn_risk_band"),
        ("segment", "RFM Segment", "rfm_segment"),
        ("stage", "Lifecycle Stage", "lifecycle_stage"),
    ),
)


VIP = Domain(
    key="vip",
    label="VIP Customers",
    relation="v_mart_customer_vip",
    date_column="",
    metrics={
        "vip_customers": Metric(
            "vip_customers",
            "VIPs",
            "sum(vip_customers)",
            Additivity.FULL,
            "count",
            "Top-decile repeat customers.",
        ),
        "vip_value": Metric(
            "vip_value",
            "VIP Value",
            "sum(vip_value)",
            Additivity.FULL,
            "currency",
            "Lifetime value held by VIPs.",
        ),
        "avg_lifetime_value": Metric(
            "avg_lifetime_value",
            "Avg Lifetime Value",
            "sum(vip_value) / nullif(sum(vip_customers), 0)",
            Additivity.NON,
            "currency",
            "Recomputed at grain.",
            ratio_of=("vip_value", "vip_customers"),
        ),
        "avg_predicted_clv_12m": Metric(
            "avg_predicted_clv_12m",
            "Avg Predicted 12m CLV",
            "avg(avg_predicted_clv_12m)",
            Additivity.NON,
            "currency",
            "Extrapolation from observed behaviour, not a fitted "
            "model — read with its confidence grade.",
        ),
        "share_of_total_value": Metric(
            "share_of_total_value",
            "Share of Total Value",
            "sum(share_of_total_value)",
            Additivity.FULL,
            "rate",
            "Portion of all customer value this slice holds.",
        ),
    },
    dimensions=_dims(
        ("segment", "RFM Segment", "rfm_segment"),
        ("risk_band", "Risk Band", "churn_risk_band"),
    ),
)


# ── Inventory (Analytics) ─────────────────────────────────────────

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


# ── Marketing (Analytics) ─────────────────────────────────────────

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


# ── Profitability (Analytics) ─────────────────────────────────────

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


# ── Product (Analytics) ───────────────────────────────────────────

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


# ── Inventory intelligence (Analytics) ────────────────────────────
#
# The five domains below read *point-in-time* marts: one row per position (or
# per supplier) as of the latest snapshot, not a time series. They therefore
# declare no date column — a caller cannot ask for "reorder suggestions last
# Tuesday", because the warehouse keeps the current picture, not a replayable
# history of every recommendation it has ever made.
#
# Where a mart already stores a rate or a score, the registry still recomputes
# it from its components. At the mart's own grain the two agree exactly; at any
# coarser grain only the recomputation is correct, and having one expression
# that is right at every grain is worth more than a column read.


PRODUCT_ABC = Domain(
    key="product_abc",
    label="ABC Classification",
    relation="v_mart_product_abc",
    date_column="",
    metrics={
        "skus": Metric(
            "skus",
            "SKUs",
            "count(*)",
            Additivity.FULL,
            "count",
            "Number of products in the class.",
        ),
        "revenue": Metric(
            "revenue",
            "Revenue",
            "sum(revenue)",
            Additivity.FULL,
            "currency",
            "Net revenue over the classification window.",
        ),
        "units": Metric(
            "units", "Units", "sum(units)", Additivity.FULL, "units", "Net units sold."
        ),
        "margin": Metric(
            "margin", "Margin", "sum(margin)", Additivity.FULL, "currency", "Gross margin."
        ),
        "orders": Metric(
            "orders",
            "Orders",
            "sum(orders)",
            Additivity.NON,
            "count",
            "Orders containing the product. Not additive: one basket holding "
            "two classes belongs to both.",
        ),
        "margin_rate": Metric(
            "margin_rate",
            "Margin Rate",
            "sum(margin) / nullif(sum(revenue), 0)",
            Additivity.NON,
            "rate",
            "Margin share of revenue, recomputed at grain.",
            ratio_of=("margin", "revenue"),
        ),
        "units_per_selling_day": Metric(
            "units_per_selling_day",
            "Units per Selling Day",
            "sum(units) / nullif(sum(selling_days), 0)",
            Additivity.NON,
            "units",
            "Velocity, normalised for products that were not on sale every day.",
        ),
        "avg_service_level": Metric(
            "avg_service_level",
            "Target Service Level",
            "avg(target_service_level)",
            Additivity.NON,
            "rate",
            "Service level the class is planned to, which drives safety stock.",
        ),
    },
    dimensions=_dims(
        ("abc_class", "ABC Class", "abc_class"),
        ("sku", "SKU", "sku"),
        ("product_name", "Product", "product_name"),
        ("category", "Category", "category"),
        ("department", "Department", "department"),
    ),
)


INVENTORY_HEALTH = Domain(
    key="inventory_health",
    label="Inventory Health",
    relation="v_mart_inventory_health",
    date_column="",
    metrics={
        "positions": Metric(
            "positions",
            "Positions",
            "count(*)",
            Additivity.FULL,
            "count",
            "SKU × store positions in scope.",
        ),
        "on_hand_units": Metric(
            "on_hand_units",
            "On Hand Units",
            "sum(on_hand_qty)",
            Additivity.SEMI,
            "units",
            "Units in stock. Semi-additive: valid across stores and SKUs, never across dates.",
        ),
        "on_order_units": Metric(
            "on_order_units",
            "On Order Units",
            "sum(on_order_total)",
            Additivity.SEMI,
            "units",
            "Units already inbound, reconciled to one source. The position "
            "feed and the purchasing feed both report in-transit stock; "
            "adding them double-counts it.",
        ),
        "inventory_value": Metric(
            "inventory_value",
            "Inventory Value",
            "sum(inventory_value_cost)",
            Additivity.SEMI,
            "currency",
            "Stock at cost.",
        ),
        "stockout_positions": Metric(
            "stockout_positions",
            "Stockouts",
            "count(*) filter (where is_stockout)",
            Additivity.FULL,
            "count",
            "Positions at zero on hand.",
        ),
        "at_risk_positions": Metric(
            "at_risk_positions",
            "At Risk",
            "count(*) filter (where stockout_before_lead_time)",
            Additivity.FULL,
            "count",
            "Positions projected to hit zero before a replenishment could "
            "arrive — the ones where ordering today is already late.",
        ),
        "overstocked_positions": Metric(
            "overstocked_positions",
            "Overstocked",
            "count(*) filter (where is_overstocked)",
            Additivity.FULL,
            "count",
            "Positions carrying more than twelve weeks of cover.",
        ),
        "dead_stock_positions": Metric(
            "dead_stock_positions",
            "Dead Stock",
            "count(*) filter (where is_dead_stock)",
            Additivity.FULL,
            "count",
            "Stock on hand with no demand at all in the trailing window.",
        ),
        "excess_units": Metric(
            "excess_units",
            "Excess Units",
            "sum(excess_units)",
            Additivity.SEMI,
            "units",
            "Units held above the overstock threshold.",
        ),
        "excess_value": Metric(
            "excess_value",
            "Excess Value",
            "sum(excess_value)",
            Additivity.SEMI,
            "currency",
            "Working capital tied up in excess stock.",
        ),
        "stockout_rate": Metric(
            "stockout_rate",
            "Stockout Rate",
            "count(*) filter (where is_stockout)::double / nullif(count(*), 0)",
            Additivity.NON,
            "rate",
            "Share of positions at zero, recomputed from counts.",
            ratio_of=("stockout_positions", "positions"),
        ),
        "daily_demand": Metric(
            "daily_demand",
            "Daily Demand",
            "sum(avg_daily_demand)",
            Additivity.FULL,
            "units",
            "Average units sold per day across the positions in scope.",
        ),
        "cover_days": Metric(
            "cover_days",
            "Cover Days",
            "sum(on_hand_qty) / nullif(sum(avg_daily_demand), 0)",
            Additivity.NON,
            "days",
            "Days of supply at current demand, recomputed at grain rather "
            "than averaged — a stockout has infinite cover and would otherwise "
            "drag the mean the wrong way.",
        ),
        "avg_days_since_receipt": Metric(
            "avg_days_since_receipt",
            "Stock Age",
            "avg(days_since_receipt)",
            Additivity.NON,
            "days",
            "Mean days since the last delivery — the aging measure.",
        ),
        "soonest_stockout_days": Metric(
            "soonest_stockout_days",
            "Soonest Stockout",
            "min(days_until_stockout)",
            Additivity.NON,
            "days",
            "Days until the first position in the group runs out.",
        ),
    },
    dimensions=_dims(
        ("sku", "SKU", "sku"),
        ("product_name", "Product", "product_name"),
        ("category", "Category", "category"),
        ("department", "Department", "department"),
        ("store_id", "Store", "store_id"),
        ("store_name", "Store Name", "store_name"),
        ("region", "Region", "region"),
        ("store_cluster", "Store Cluster", "store_cluster"),
        ("supplier_id", "Supplier", "supplier_id"),
        ("supplier_name", "Supplier Name", "supplier_name"),
        ("abc_class", "ABC Class", "abc_class"),
        ("aging_bucket", "Aging Bucket", "aging_bucket"),
        ("position_date", "As Of", "position_date"),
    ),
)


REORDER = Domain(
    key="reorder",
    label="Reorder Suggestions",
    relation="v_mart_reorder_suggestions",
    date_column="",
    metrics={
        "positions": Metric(
            "positions", "Positions", "count(*)", Additivity.FULL, "count", "Positions in scope."
        ),
        "below_reorder_point": Metric(
            "below_reorder_point",
            "Below Reorder Point",
            "count(*) filter (where below_reorder_point)",
            Additivity.FULL,
            "count",
            "Positions where inventory position has fallen through the "
            "reorder point and an order is due.",
        ),
        "suggested_order_qty": Metric(
            "suggested_order_qty",
            "Suggested Order",
            "sum(suggested_order_qty)",
            Additivity.FULL,
            "units",
            "Units to order to reach the order-up-to level.",
        ),
        "revenue_at_risk": Metric(
            "revenue_at_risk",
            "Revenue at Risk",
            "sum(revenue_at_risk)",
            Additivity.FULL,
            "currency",
            "Sales expected to be lost before replenishment lands, if nothing "
            "is ordered. This is the ranking number: it puts a fast-moving "
            "staple above a slow one with a worse cover ratio.",
        ),
        "on_hand_units": Metric(
            "on_hand_units",
            "On Hand",
            "sum(on_hand_qty)",
            Additivity.SEMI,
            "units",
            "Units in stock.",
        ),
        "on_order_units": Metric(
            "on_order_units",
            "On Order",
            "sum(on_order_total)",
            Additivity.SEMI,
            "units",
            "Units already inbound, reconciled to one source. The position "
            "feed and the purchasing feed both report in-transit stock; "
            "adding them double-counts it and under-orders.",
        ),
        "safety_stock": Metric(
            "safety_stock",
            "Safety Stock",
            "sum(safety_stock)",
            Additivity.FULL,
            "units",
            "Buffer covering demand and lead-time variability at the target service level.",
        ),
        "reorder_point": Metric(
            "reorder_point",
            "Reorder Point",
            "sum(reorder_point)",
            Additivity.FULL,
            "units",
            "Lead-time demand plus safety stock.",
        ),
        "order_up_to_level": Metric(
            "order_up_to_level",
            "Order Up To",
            "sum(order_up_to_level)",
            Additivity.FULL,
            "units",
            "Target position after ordering.",
        ),
        "daily_demand": Metric(
            "daily_demand",
            "Daily Demand",
            "sum(avg_daily_demand)",
            Additivity.FULL,
            "units",
            "Average units per day.",
        ),
        "lead_time_days": Metric(
            "lead_time_days",
            "Lead Time",
            "max(effective_lead_time_days)",
            Additivity.NON,
            "days",
            "Longest lead time in the group — the one that governs when an order must be placed.",
        ),
        "soonest_stockout_days": Metric(
            "soonest_stockout_days",
            "Soonest Stockout",
            "min(days_until_stockout)",
            Additivity.NON,
            "days",
            "Days until the first position runs out.",
        ),
    },
    dimensions=_dims(
        ("sku", "SKU", "sku"),
        ("product_name", "Product", "product_name"),
        ("category", "Category", "category"),
        ("store_id", "Store", "store_id"),
        ("store_name", "Store Name", "store_name"),
        ("region", "Region", "region"),
        ("supplier_id", "Supplier", "supplier_id"),
        ("supplier_name", "Supplier Name", "supplier_name"),
        ("abc_class", "ABC Class", "abc_class"),
        ("on_order_source", "On Order Source", "on_order_source"),
    ),
)


SUPPLIER = Domain(
    key="supplier",
    label="Supplier Performance",
    relation="v_mart_supplier_performance",
    date_column="",
    metrics={
        "suppliers": Metric(
            "suppliers", "Suppliers", "count(*)", Additivity.FULL, "count", "Vendors in scope."
        ),
        "po_lines": Metric(
            "po_lines",
            "PO Lines",
            "sum(po_lines)",
            Additivity.FULL,
            "count",
            "Purchase-order lines raised.",
        ),
        "open_lines": Metric(
            "open_lines",
            "Open Lines",
            "sum(open_lines)",
            Additivity.FULL,
            "count",
            "Lines still in transit. Excluded from every performance rate: a "
            "line that has not arrived yet has not failed.",
        ),
        "closed_lines": Metric(
            "closed_lines",
            "Closed Lines",
            "sum(closed_lines)",
            Additivity.FULL,
            "count",
            "Received lines — the denominator for OTIF.",
        ),
        "ordered_value": Metric(
            "ordered_value",
            "Ordered Value",
            "sum(ordered_value)",
            Additivity.FULL,
            "currency",
            "Value placed with the supplier. The exposure behind the risk band.",
        ),
        "otif_rate": Metric(
            "otif_rate",
            "OTIF",
            "sum(otif_lines)::double / nullif(sum(closed_lines), 0)",
            Additivity.NON,
            "rate",
            "On time *and* in full. Recomputed from line counts, so a vendor "
            "with 2,000 lines does not weigh the same as one with 20.",
            ratio_of=("otif_lines", "closed_lines"),
        ),
        "on_time_rate": Metric(
            "on_time_rate",
            "On Time",
            "sum(on_time_lines)::double / nullif(sum(closed_lines), 0)",
            Additivity.NON,
            "rate",
            "Received on or before the promise date.",
            ratio_of=("on_time_lines", "closed_lines"),
        ),
        "in_full_rate": Metric(
            "in_full_rate",
            "In Full",
            "sum(in_full_lines)::double / nullif(sum(closed_lines), 0)",
            Additivity.NON,
            "rate",
            "Received complete. Split from on-time deliberately: late and "
            "short are different failures needing different conversations.",
            ratio_of=("in_full_lines", "closed_lines"),
        ),
        "fill_rate": Metric(
            "fill_rate",
            "Fill Rate",
            "sum(received_qty) / nullif(sum(closed_ordered_qty), 0)",
            Additivity.NON,
            "rate",
            "Units received against units ordered — how short a short shipment actually was.",
        ),
        "avg_lead_time_days": Metric(
            "avg_lead_time_days",
            "Lead Time",
            "sum(avg_lead_time_days * closed_lines) / nullif(sum(closed_lines), 0)",
            Additivity.NON,
            "days",
            "Line-weighted mean lead time.",
        ),
        "avg_days_late": Metric(
            "avg_days_late",
            "Days Late",
            "sum(avg_days_late * closed_lines) / nullif(sum(closed_lines), 0)",
            Additivity.NON,
            "days",
            "Line-weighted mean variance against the promise date. Negative means early.",
        ),
        "worst_lead_time_stddev": Metric(
            "worst_lead_time_stddev",
            "Lead Time Variability",
            "max(lead_time_stddev)",
            Additivity.NON,
            "days",
            "Worst spread in the group. Variability drives safety stock far "
            "harder than average lateness does — a consistently slow supplier "
            "can be planned around, an erratic one cannot.",
        ),
        "worst_lead_time_cov": Metric(
            "worst_lead_time_cov",
            "Lead Time CoV",
            "max(lead_time_cov)",
            Additivity.NON,
            "ratio",
            "Spread relative to the mean — comparable across suppliers whose "
            "lead times differ by weeks.",
        ),
        "p90_lead_time_days": Metric(
            "p90_lead_time_days",
            "P90 Lead Time",
            "max(p90_lead_time_days)",
            Additivity.NON,
            "days",
            "The lead time to plan to. Planning to the mean is planning to be "
            "out of stock half the time.",
        ),
    },
    dimensions=_dims(
        ("supplier_id", "Supplier", "supplier_id"),
        ("supplier_name", "Supplier Name", "supplier_name"),
        ("country", "Country", "country"),
        ("risk_band", "Risk Band", "risk_band"),
    ),
)


WAREHOUSE_HEALTH = Domain(
    key="warehouse_health",
    label="Warehouse Health",
    relation="v_mart_warehouse_health",
    date_column="",
    metrics={
        "positions": Metric(
            "positions",
            "Positions",
            "sum(sku_store_positions)",
            Additivity.FULL,
            "count",
            "SKU × store positions covered.",
        ),
        "stores": Metric(
            "stores", "Stores", "sum(stores)", Additivity.FULL, "count", "Stores in scope."
        ),
        "inventory_value": Metric(
            "inventory_value",
            "Inventory Value",
            "sum(inventory_value)",
            Additivity.SEMI,
            "currency",
            "Stock at cost.",
        ),
        "excess_value": Metric(
            "excess_value",
            "Excess Value",
            "sum(excess_value)",
            Additivity.SEMI,
            "currency",
            "Capital tied up above the overstock threshold.",
        ),
        "stockout_positions": Metric(
            "stockout_positions",
            "Stockouts",
            "sum(stockout_positions)",
            Additivity.FULL,
            "count",
            "Positions at zero.",
        ),
        "at_risk_positions": Metric(
            "at_risk_positions",
            "At Risk",
            "sum(at_risk_positions)",
            Additivity.FULL,
            "count",
            "Positions that will run out before a replenishment can land.",
        ),
        "overstocked_positions": Metric(
            "overstocked_positions",
            "Overstocked",
            "sum(overstocked_positions)",
            Additivity.FULL,
            "count",
            "Positions above twelve weeks of cover.",
        ),
        "open_po_lines": Metric(
            "open_po_lines",
            "Open PO Lines",
            "sum(open_po_lines)",
            Additivity.FULL,
            "count",
            "Replenishment in flight.",
        ),
        "stockout_rate": Metric(
            "stockout_rate",
            "Stockout Rate",
            "sum(stockout_positions)::double / nullif(sum(sku_store_positions), 0)",
            Additivity.NON,
            "rate",
            "Recomputed from counts, so a large region does not get the same vote as a small one.",
            ratio_of=("stockout_positions", "positions"),
        ),
        "excess_value_share": Metric(
            "excess_value_share",
            "Excess Share",
            "sum(excess_value) / nullif(sum(inventory_value), 0)",
            Additivity.NON,
            "rate",
            "Share of inventory value that is excess.",
        ),
        # The five component scores and the composite are position-weighted
        # rather than averaged. At the mart's own grain (one row per region)
        # the weighting is a no-op and the value is read exactly as computed;
        # roll two regions together and only the weighted form is defensible.
        "availability_score": Metric(
            "availability_score",
            "Availability",
            "sum(availability_score * sku_store_positions) / nullif(sum(sku_store_positions), 0)",
            Additivity.NON,
            "score",
            "Can a customer buy it? Driven by stockout rate.",
        ),
        "replenishment_score": Metric(
            "replenishment_score",
            "Replenishment",
            "sum(replenishment_score * sku_store_positions) / nullif(sum(sku_store_positions), 0)",
            Additivity.NON,
            "score",
            "Is supply keeping up? Driven by positions running out inside their lead time.",
        ),
        "capital_efficiency_score": Metric(
            "capital_efficiency_score",
            "Capital Efficiency",
            "sum(capital_efficiency_score * sku_store_positions)"
            " / nullif(sum(sku_store_positions), 0)",
            Additivity.NON,
            "score",
            "How much stock is dead weight.",
        ),
        "assortment_score": Metric(
            "assortment_score",
            "Assortment",
            "sum(assortment_score * sku_store_positions) / nullif(sum(sku_store_positions), 0)",
            Additivity.NON,
            "score",
            "Breadth actually carried against breadth ranged.",
        ),
        "freshness_score": Metric(
            "freshness_score",
            "Freshness",
            "sum(freshness_score * sku_store_positions) / nullif(sum(sku_store_positions), 0)",
            Additivity.NON,
            "score",
            "How long stock has been sitting.",
        ),
        "health_score": Metric(
            "health_score",
            "Health Score",
            "sum(health_score * sku_store_positions) / nullif(sum(sku_store_positions), 0)",
            Additivity.NON,
            "score",
            "The weighted composite of the five components, 0–100. A single "
            "number is a summary, not a diagnosis: it exists to rank regions "
            "for attention, and the components say what to actually fix.",
        ),
        "avg_cover_days": Metric(
            "avg_cover_days",
            "Cover Days",
            "sum(avg_cover_days * sku_store_positions) / nullif(sum(sku_store_positions), 0)",
            Additivity.NON,
            "days",
            "Position-weighted days of supply.",
        ),
    },
    dimensions=_dims(
        ("region", "Region", "region"),
        ("health_band", "Health Band", "health_band"),
        ("position_date", "As Of", "position_date"),
    ),
)


# ── Forecasting (Analytics M7,) ─────────────────────────────
#
# Forecast rows are *published output*, not a recomputation surface. The
# registry therefore exposes them at their stored grain and aggregates only
# where aggregation is meaningful: summing a point forecast across days is
# legitimate (a fortnight's expected revenue), while averaging a WAPE across
# models is not, and the additivity flags say which is which.


FORECAST = Domain(
    key="forecast",
    label="Forecasts",
    relation="v_forecast_predictions",
    # No implicit date window. The default lookback exists to stop an
    # unbounded scan of history; applied here it would do the opposite and
    # hide every forecast beyond today — which is all of them. The table is
    # bounded by the horizon, so there is nothing to guard against.
    date_column="",
    metrics={
        "forecast": Metric(
            "forecast",
            "Forecast",
            "sum(yhat)",
            Additivity.FULL,
            "mixed",
            "Point forecast. Additive across days and series within one "
            "target — a fortnight's expected revenue is the sum of its days.",
        ),
        "forecast_lower": Metric(
            "forecast_lower",
            "Lower Bound",
            "sum(yhat_lower)",
            Additivity.FULL,
            "mixed",
            "Lower edge of the prediction interval. Summing bounds across "
            "days is conservative rather than exact: independent errors "
            "partially cancel, so the summed band is wider than the true one.",
        ),
        "forecast_upper": Metric(
            "forecast_upper",
            "Upper Bound",
            "sum(yhat_upper)",
            Additivity.FULL,
            "mixed",
            "Upper edge of the prediction interval.",
        ),
        "horizons": Metric(
            "horizons",
            "Horizons",
            "count(*)",
            Additivity.FULL,
            "count",
            "Forecast rows in scope.",
        ),
        "series": Metric(
            "series",
            "Series",
            "count(distinct series_key)",
            Additivity.NON,
            "count",
            "Distinct series forecast.",
        ),
        "relative_interval_width": Metric(
            "relative_interval_width",
            "Interval Width",
            "sum(yhat_upper - yhat_lower) / nullif(sum(abs(yhat)), 0)",
            Additivity.NON,
            "ratio",
            "Band width relative to the forecast. The number that says how "
            "much to trust one: 100 ± 8 and 100 ± 90 are not the same claim.",
        ),
        "model_wape": Metric(
            "model_wape",
            "Model WAPE",
            "max(model_wape)",
            Additivity.NON,
            "rate",
            "Out-of-sample error of the model that produced these rows. Read "
            "at model grain, never averaged across models — a mean of two "
            "models' WAPE describes neither.",
        ),
        "model_mase": Metric(
            "model_mase",
            "Model MASE",
            "max(model_mase)",
            Additivity.NON,
            "ratio",
            "Error scaled against seasonal naive. Below 1.0 the model beats "
            "assuming next week looks like last week; at or above it, the "
            "model has earned nothing.",
        ),
    },
    dimensions=_dims(
        ("target", "Target", "target"),
        ("series_key", "Series", "series_key"),
        ("model_name", "Model", "model_name"),
        ("model_class", "Model Class", "model_class"),
        ("horizon", "Horizon", "horizon"),
        ("business_date", "Date", "business_date"),
        ("origin_date", "Forecast Origin", "origin_date"),
    ),
)


FORECAST_ACCURACY = Domain(
    key="forecast_accuracy",
    label="Forecast Accuracy",
    relation="v_mart_forecast_accuracy",
    date_column="",
    metrics={
        "wape": Metric(
            "wape",
            "WAPE",
            "max(wape)",
            Additivity.NON,
            "rate",
            "Weighted absolute percentage error — the headline. Read at model "
            "grain: pooling two models' errors produces a number describing a "
            "model nobody is running.",
        ),
        "mape": Metric(
            "mape",
            "MAPE",
            "max(mape)",
            Additivity.NON,
            "rate",
            "The familiar number, and the least reliable: it divides by the "
            "actual, so one quiet day can dominate the headline.",
        ),
        "bias": Metric(
            "bias",
            "Bias",
            "max(bias)",
            Additivity.NON,
            "rate",
            "Signed error. A model with good WAPE and strong bias is wrong in "
            "a consistent direction, which for replenishment compounds into "
            "working capital instead of averaging out.",
        ),
        "interval_coverage": Metric(
            "interval_coverage",
            "Interval Coverage",
            "max(interval_coverage)",
            Additivity.NON,
            "rate",
            "Share of actuals that fell inside the band. Compared against the "
            "band's nominal level: one claiming 80% and delivering 50% is "
            "miscalibrated, and planning against it is worse than planning "
            "against a point estimate known to be uncertain.",
        ),
        "mean_absolute_error": Metric(
            "mean_absolute_error",
            "MAE",
            "max(mean_absolute_error)",
            Additivity.NON,
            "currency",
            "Mean absolute error in currency.",
        ),
        "forecast_days": Metric(
            "forecast_days",
            "Scored Days",
            "sum(forecast_days)",
            Additivity.FULL,
            "count",
            "Days with an actual to score against — the evidence behind every rate above.",
        ),
        "pending_days": Metric(
            "pending_days",
            "Pending Days",
            "sum(pending_days)",
            Additivity.FULL,
            "count",
            "Forecasts for days that have not happened yet. Counted apart so "
            "they cannot dilute the accuracy sample.",
        ),
    },
    dimensions=_dims(
        ("model_name", "Model", "model_name"),
        ("model_class", "Model Class", "model_class"),
        ("produced_by", "Produced By", "produced_by"),
    ),
)


FORECAST_EXPLANATION = Domain(
    key="forecast_explanation",
    label="Forecast Explanations",
    relation="v_forecast_explanations",
    date_column="",  # forward-looking, and bounded by the horizon — see FORECAST
    metrics={
        "effect": Metric(
            "effect",
            "Effect",
            "sum(effect)",
            Additivity.FULL,
            "mixed",
            "Signed contribution to the forecast, in the series' own units. "
            "Additive by construction: baseline plus the effects reconstructs "
            "the point forecast exactly.",
        ),
        "effect_magnitude": Metric(
            "effect_magnitude",
            "Effect Size",
            "sum(effect_magnitude)",
            Additivity.FULL,
            "mixed",
            "Absolute contribution — how much a feature moved the number, regardless of direction.",
        ),
        "baseline": Metric(
            "baseline",
            "Baseline",
            "max(baseline)",
            Additivity.NON,
            "mixed",
            "What the model predicts with every feature at its training mean "
            "— the reference the contributions move away from.",
        ),
        "features": Metric(
            "features",
            "Features",
            "count(*)",
            Additivity.FULL,
            "count",
            "Contributions in scope.",
        ),
    },
    dimensions=_dims(
        ("target", "Target", "target"),
        ("series_key", "Series", "series_key"),
        ("feature", "Feature", "feature"),
        ("direction", "Direction", "direction"),
        ("horizon", "Horizon", "horizon"),
        ("business_date", "Date", "business_date"),
    ),
)


# ── Root cause analysis (Analytics) ───────────────────────────────
#
# Two relations and one shape. The slice domain is *unpivoted*: one row per
# (slice_type, slice_value, day), so a single query answers "how did every cut
# of the business move" and the decomposition code never learns the difference
# between a region and a category. The factor domain carries the operational
# signals that might explain a move, at region-day grain.


RCA_SLICE = Domain(
    key="rca_slice",
    label="RCA Slices",
    relation="v_mart_rca_slice_daily",
    date_column="business_date",
    metrics={
        "net_revenue": Metric(
            "net_revenue",
            "Net Revenue",
            "sum(net_revenue)",
            Additivity.FULL,
            "currency",
            "Revenue net of discounts and returns — the metric decompositions run on.",
        ),
        "gross_revenue": Metric(
            "gross_revenue",
            "Gross Revenue",
            "sum(gross_revenue)",
            Additivity.FULL,
            "currency",
            "Revenue before discounts.",
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
            "units_sold",
            "Units",
            "sum(units_sold)",
            Additivity.FULL,
            "units",
            "Net units.",
        ),
        "orders": Metric(
            "orders",
            "Orders",
            "sum(orders)",
            Additivity.NON,
            "count",
            "Transaction count — the volume half of the volume-versus-rate "
            "split. Summed across days within a slice, never across slices: a "
            "basket spanning two categories belongs to both.",
        ),
        "return_amount": Metric(
            "return_amount",
            "Returned Value",
            "sum(return_amount)",
            Additivity.FULL,
            "currency",
            "Value of goods returned, carried separately from net revenue "
            "so that 'sales fell' and 'returns rose' stay distinguishable.",
        ),
        "return_units": Metric(
            "return_units",
            "Returned Units",
            "sum(return_units)",
            Additivity.FULL,
            "units",
            "Units returned.",
        ),
        "return_rate": Metric(
            "return_rate",
            "Return Rate",
            "sum(return_amount) / nullif(sum(net_revenue) + sum(return_amount), 0)",
            Additivity.NON,
            "rate",
            "Returned value against gross sales, recomputed at grain.",
            ratio_of=("return_amount", "net_revenue"),
        ),
        "aov": Metric(
            "aov",
            "Average Order Value",
            "sum(net_revenue) / nullif(sum(orders), 0)",
            Additivity.NON,
            "currency",
            "The rate half of the volume-versus-rate split.",
            ratio_of=("net_revenue", "orders"),
        ),
    },
    dimensions=_dims(
        ("slice_type", "Cut", "slice_type"),
        ("slice_value", "Slice", "slice_value"),
        ("business_date", "Date", "business_date"),
    ),
)


RCA_FACTOR = Domain(
    key="rca_factor",
    label="RCA Factors",
    relation="v_mart_rca_factor_daily",
    date_column="business_date",
    metrics={
        "stockout_rate": Metric(
            "stockout_rate",
            "Stockout Rate",
            "sum(stockout_positions)::double / nullif(sum(sku_store_positions), 0)",
            Additivity.NON,
            "rate",
            "Share of positions at zero, recomputed from counts.",
            ratio_of=("stockout_positions", "sku_store_positions"),
        ),
        "stockout_positions": Metric(
            "stockout_positions",
            "Stockouts",
            "sum(stockout_positions)",
            Additivity.FULL,
            "count",
            "Positions at zero.",
        ),
        "sku_store_positions": Metric(
            "sku_store_positions",
            "Positions",
            "sum(sku_store_positions)",
            Additivity.FULL,
            "count",
            "Positions in scope.",
        ),
        "on_time_rate": Metric(
            "on_time_rate",
            "On Time Delivery",
            "sum(shipments_on_time)::double / nullif(sum(shipments_closed), 0)",
            Additivity.NON,
            "rate",
            "Delivered by the promise date, over *closed* shipments. Averaging "
            "daily rates would weigh a quiet Sunday like a peak Friday.",
            ratio_of=("shipments_on_time", "shipments_closed"),
        ),
        "shipments": Metric(
            "shipments",
            "Shipments",
            "sum(shipments)",
            Additivity.FULL,
            "count",
            "Shipments raised.",
        ),
        "shipments_closed": Metric(
            "shipments_closed",
            "Closed Shipments",
            "sum(shipments_closed)",
            Additivity.FULL,
            "count",
            "Shipments that landed — the denominator for on-time rate.",
        ),
        "shipments_on_time": Metric(
            "shipments_on_time",
            "On Time Shipments",
            "sum(shipments_on_time)",
            Additivity.FULL,
            "count",
            "Shipments that met their promise date.",
        ),
        "avg_days_late": Metric(
            "avg_days_late",
            "Days Late",
            "avg(avg_days_late)",
            Additivity.NON,
            "days",
            "Mean lateness among shipments that missed.",
        ),
        "carriers_missing_promise": Metric(
            "carriers_missing_promise",
            "Carriers Missing",
            "max(carriers_missing_promise)",
            Additivity.NON,
            "count",
            "Distinct carriers that missed a promise.",
        ),
        "severe_days": Metric(
            "severe_days",
            "Severe Weather Days",
            "count(*) filter (where is_severe)",
            Additivity.FULL,
            "days",
            "Days carrying a provider severe-weather flag.",
        ),
        "max_precipitation_z": Metric(
            "max_precipitation_z",
            "Peak Rainfall",
            "max(precipitation_z)",
            Additivity.NON,
            "z",
            "Wettest day in scope, standardised against the region's own norm.",
        ),
        "max_wind_kph": Metric(
            "max_wind_kph",
            "Peak Wind",
            "max(wind_kph_max)",
            Additivity.NON,
            "kph",
            "Strongest wind in scope.",
        ),
        "promo_revenue": Metric(
            "promo_revenue",
            "Promotional Revenue",
            "avg(promo_revenue)",
            Additivity.NON,
            "currency",
            "National promotional revenue. Averaged, not summed: the same "
            "national figure is attached to every region, so summing multiplies it "
            "by the number of regions.",
        ),
        "active_promotions": Metric(
            "active_promotions",
            "Active Campaigns",
            "max(active_promotions)",
            Additivity.NON,
            "count",
            "Campaigns live in the window.",
        ),
        "avg_promo_depth": Metric(
            "avg_promo_depth",
            "Promotional Depth",
            "avg(avg_promo_depth)",
            Additivity.NON,
            "rate",
            "Mean effective discount depth.",
        ),
    },
    dimensions=_dims(
        ("region", "Region", "region"),
        ("severe_flag", "Severe Flag", "severe_flag"),
        ("business_date", "Date", "business_date"),
    ),
)


RCA_WEATHER = Domain(
    key="rca_weather",
    label="Weather Effect",
    relation="v_mart_rca_weather_effect",
    date_column="",
    metrics={
        "severe_day_gap": Metric(
            "severe_day_gap",
            "Severe Day Revenue Gap",
            "max(severe_day_gap)",
            Additivity.NON,
            "currency",
            "Observed daily revenue difference between severe and ordinary days "
            "in the same region. An association, not an effect: severe days "
            "differ from ordinary ones in more ways than the weather.",
        ),
        "severe_day_gap_pct": Metric(
            "severe_day_gap_pct",
            "Severe Day Gap %",
            "max(severe_day_gap_pct)",
            Additivity.NON,
            "rate",
            "The same gap, relative to an ordinary day.",
        ),
        "severe_days": Metric(
            "severe_days",
            "Severe Days Observed",
            "max(severe_days)",
            Additivity.NON,
            "days",
            "Severe days behind the estimate — its entire evidence base.",
        ),
        "ordinary_days": Metric(
            "ordinary_days",
            "Ordinary Days",
            "max(ordinary_days)",
            Additivity.NON,
            "days",
            "Comparison days.",
        ),
    },
    dimensions=_dims(("region", "Region", "region")),
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
        RFM_GRID,
        COHORTS,
        LIFECYCLE,
        CHURN,
        VIP,
        PRODUCT_ABC,
        INVENTORY_HEALTH,
        REORDER,
        SUPPLIER,
        WAREHOUSE_HEALTH,
        FORECAST,
        FORECAST_ACCURACY,
        FORECAST_EXPLANATION,
        RCA_SLICE,
        RCA_FACTOR,
        RCA_WEATHER,
    )
}


def get_domain(key: str) -> Domain | None:
    return DOMAINS.get(key)
