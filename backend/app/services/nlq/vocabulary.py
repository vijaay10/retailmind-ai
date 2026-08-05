"""The closed vocabulary a planner may draw from.

Built from the metric registry at import time rather than maintained by hand,
so a domain added to the registry becomes askable and a domain removed becomes
unaskable without anyone remembering to update a list here. A hand-kept
synonym table drifts, and the way it drifts is that a question keeps resolving
to a metric that no longer means what it did.

The synonyms below are the only hand-written part, and they map *English* onto
registry keys — never onto SQL. "Sales" resolves to the metric key
``net_revenue``, which the compiler then turns into an expression it owns.
There is no path by which a synonym could introduce a fragment of a statement,
because a synonym's value is looked up in the registry and discarded if absent.
"""

from dataclasses import dataclass

from app.services.analytics.registry import DOMAINS, Domain

#: English → domain key. Ordered longest-first at match time so "top customers"
#: prefers the customer domain over a bare "top".
DOMAIN_SYNONYMS: dict[str, str] = {
    "revenue": "revenue",
    "sales": "revenue",
    "turnover": "revenue",
    "income": "revenue",
    "store": "store",
    "stores": "store",
    "shop": "store",
    "branch": "store",
    "customer": "customer",
    "customers": "customer",
    "shopper": "customer",
    "segment": "customer",
    "segments": "customer",
    "product": "product",
    "products": "product",
    "sku": "product",
    "item": "product",
    "category": "revenue",
    "inventory": "inventory_health",
    "stock": "inventory_health",
    "availability": "inventory_health",
    "stockout": "inventory_health",
    "stockouts": "inventory_health",
    "supplier": "supplier",
    "suppliers": "supplier",
    "vendor": "supplier",
    "promotion": "marketing",
    "promotions": "marketing",
    "promo": "marketing",
    "campaign": "marketing",
    "campaigns": "marketing",
    "margin": "profitability",
    "profit": "profitability",
    "profitability": "profitability",
    "churn": "churn",
    "vip": "vip",
    "retention": "cohorts",
    "cohort": "cohorts",
    "cohorts": "cohorts",
    "lifecycle": "lifecycle",
    "reorder": "reorder",
    "replenishment": "reorder",
    "delivery": "rca_factor",
    "shipping": "rca_factor",
}

#: English → metric key, checked against the chosen domain before use.
METRIC_SYNONYMS: dict[str, tuple[str, ...]] = {
    "revenue": ("net_revenue", "revenue", "segment_value", "vip_value"),
    "sales": ("net_revenue", "revenue", "units_sold"),
    "units": ("units_sold", "units"),
    "volume": ("units_sold", "units"),
    "orders": ("orders", "po_lines"),
    "transactions": ("orders",),
    "margin": ("margin_amount", "margin", "promo_margin"),
    "profit": ("margin_amount", "margin"),
    "customers": ("customers", "vip_customers", "identified_customers"),
    "value": ("net_revenue", "segment_value", "vip_value", "value_at_risk"),
    "spend": ("net_revenue", "ordered_value"),
    "basket": ("aov",),
    "aov": ("aov",),
    "discount": ("discount_amount", "discount_rate"),
    "stockouts": ("stockout_positions", "stockout_rate"),
    "availability": ("stockout_rate",),
    "otif": ("otif_rate",),
    "lead time": ("avg_lead_time_days", "lead_time_days"),
}

#: Words that indicate a grouping, mapped to dimension keys.
DIMENSION_SYNONYMS: dict[str, tuple[str, ...]] = {
    "region": ("region",),
    "regions": ("region",),
    "store": ("store", "store_id"),
    "stores": ("store", "store_id"),
    "category": ("category",),
    "categories": ("category",),
    "department": ("department",),
    "channel": ("channel", "channel_group"),
    "segment": ("segment", "rfm_segment"),
    "segments": ("segment", "rfm_segment"),
    "product": ("product_name", "sku"),
    "products": ("product_name", "sku"),
    "sku": ("sku",),
    "supplier": ("supplier_name", "supplier_id"),
    "suppliers": ("supplier_name", "supplier_id"),
    "day": ("business_date",),
    "daily": ("business_date",),
    "date": ("business_date",),
    "time": ("business_date",),
    "week": ("business_date",),
    "month": ("business_date",),
    "promotion": ("promo", "promo_name"),
    "campaign": ("promo", "promo_name"),
    "band": ("risk_band",),
    "risk": ("risk_band",),
    "stage": ("stage",),
    "class": ("abc_class",),
}

#: Domains a plain question should never be routed to, because answering from
#: them without the surrounding service loses the guardrails that service
#: applies — customer privacy suppression, forecast accuracy, RCA grading.
#: They remain reachable through their own endpoints.
RESTRICTED_DOMAINS: frozenset[str] = frozenset(
    {"rca_slice", "rca_weather", "forecast_explanation", "forecast_accuracy"}
)


@dataclass(frozen=True, slots=True)
class Resolution:
    """A term resolved against the registry, or not."""

    term: str
    key: str | None

    @property
    def resolved(self) -> bool:
        return self.key is not None


class Vocabulary:
    """Everything a planner is permitted to name.

    The single guarantee this class provides: **every method returns either a
    key that exists in the registry, or nothing.** No method returns caller
    input, and none constructs a string that a database would parse. That is
    what makes the planner's output safe regardless of who or what produced it.
    """

    def __init__(self, domains: dict[str, Domain] | None = None) -> None:
        self._domains = {
            key: domain
            for key, domain in (domains or DOMAINS).items()
            if key not in RESTRICTED_DOMAINS
        }

    # ── Introspection ────────────────────────────────────────────────

    @property
    def domain_keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._domains))

    def domain(self, key: str) -> Domain | None:
        return self._domains.get(key)

    def metrics_for(self, domain_key: str) -> tuple[str, ...]:
        domain = self._domains.get(domain_key)
        return tuple(sorted(domain.metrics)) if domain else ()

    def dimensions_for(self, domain_key: str) -> tuple[str, ...]:
        domain = self._domains.get(domain_key)
        return tuple(sorted(domain.dimensions)) if domain else ()

    # ── Resolution ───────────────────────────────────────────────────

    def resolve_domain(self, term: str) -> Resolution:
        """Map a word onto a domain key, or refuse it."""
        key = term.strip().lower()
        if key in self._domains:
            return Resolution(term, key)
        candidate = DOMAIN_SYNONYMS.get(key)
        if candidate and candidate in self._domains:
            return Resolution(term, candidate)
        return Resolution(term, None)

    def resolve_metric(self, term: str, *, domain_key: str) -> Resolution:
        """Map a word onto a metric of the chosen domain, or refuse it.

        Checked against *that domain's* metrics rather than against a global
        list: "margin" means something on profitability and nothing on
        supplier, and resolving it anyway would produce a query that fails in
        the compiler with a far less useful message.
        """
        domain = self._domains.get(domain_key)
        if domain is None:
            return Resolution(term, None)

        key = term.strip().lower()
        if key in domain.metrics:
            return Resolution(term, key)
        for candidate in METRIC_SYNONYMS.get(key, ()):
            if candidate in domain.metrics:
                return Resolution(term, candidate)
        return Resolution(term, None)

    def resolve_dimension(self, term: str, *, domain_key: str) -> Resolution:
        domain = self._domains.get(domain_key)
        if domain is None:
            return Resolution(term, None)

        key = term.strip().lower()
        if key in domain.dimensions:
            return Resolution(term, key)
        for candidate in DIMENSION_SYNONYMS.get(key, ()):
            if candidate in domain.dimensions:
                return Resolution(term, candidate)
        return Resolution(term, None)

    # ── Defaults ─────────────────────────────────────────────────────

    def default_metrics(self, domain_key: str, count: int = 3) -> tuple[str, ...]:
        """A sensible answer when the question names no measure.

        "Show top customers" specifies a population and no metric. Refusing
        would be pedantic; inventing one silently would be worse, so the
        defaults are the domain's own declaration order — which is how the
        registry author ranked them — and the interpretation string says which
        were chosen.
        """
        domain = self._domains.get(domain_key)
        if domain is None:
            return ()
        return tuple(list(domain.metrics)[:count])

    def default_dimension(self, domain_key: str) -> str | None:
        """The cut a domain is most naturally listed by.

        The first non-date dimension: a date grouping answers "over time",
        which is a different question from the "which ones" that an unqualified
        listing asks.
        """
        domain = self._domains.get(domain_key)
        if domain is None:
            return None
        for key in domain.dimensions:
            if key not in {"business_date", "position_date", "origin_date"}:
                return key
        return None

    def catalogue(self) -> list[dict[str, object]]:
        """What can be asked about — the answer to "what do you know?"."""
        return [
            {
                "domain": key,
                "label": domain.label,
                "metrics": sorted(domain.metrics),
                "dimensions": sorted(domain.dimensions),
            }
            for key, domain in sorted(self._domains.items())
        ]
