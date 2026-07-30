"""Registry and query-compilation invariants — no warehouse required.

These guard the properties that make analytics *correct* rather than merely
functional: that ratios are recomputed rather than averaged, that semi-additive
measures are labelled as such, and that nothing outside the registry can reach
SQL.
"""

from datetime import date

import pytest

from app.domain.shared.errors import ValidationDomainError
from app.infrastructure.semantic.client import SemanticQuery
from app.infrastructure.semantic.repository import AnalyticsRequest
from app.services.analytics.registry import (
    DOMAINS,
    Additivity,
    get_domain,
)
from app.services.analytics.service import DOMAIN_PERMISSIONS

# ── Registry integrity ───────────────────────────────────────────────


def test_every_domain_declares_metrics_and_dimensions() -> None:
    for key, domain in DOMAINS.items():
        assert domain.metrics, f"{key} has no metrics"
        assert domain.dimensions, f"{key} has no dimensions"
        assert domain.relation.startswith("v_"), (
            f"{key} must read a semantic view, not a mart directly"
        )


def test_every_domain_is_permission_gated() -> None:
    """An ungated domain would be readable by any authenticated user."""
    assert set(DOMAINS) == set(DOMAIN_PERMISSIONS)


def test_every_metric_documents_itself() -> None:
    for domain in DOMAINS.values():
        for metric in domain.metrics.values():
            assert metric.description, f"{domain.key}.{metric.key} is undocumented"
            assert metric.unit
            assert metric.key == metric.expression or metric.expression


def test_metric_keys_match_their_registry_key() -> None:
    """A mismatch would make the API accept a name it then cannot resolve."""
    for domain in DOMAINS.values():
        for key, metric in domain.metrics.items():
            assert key == metric.key


def test_ratio_metrics_are_marked_non_additive() -> None:
    """Summing a ratio is the most common wrong number in retail reporting."""
    for domain in DOMAINS.values():
        for metric in domain.metrics.values():
            if metric.ratio_of is not None:
                assert metric.additivity is Additivity.NON, (
                    f"{domain.key}.{metric.key} is a ratio but not marked non-additive"
                )


def test_ratio_metrics_divide_rather_than_average() -> None:
    """The expression itself must recompute from components, not use avg()."""
    for domain in DOMAINS.values():
        for metric in domain.metrics.values():
            if metric.ratio_of is not None:
                assert "/" in metric.expression
                assert "avg(" not in metric.expression.lower()


def test_ratio_metrics_guard_against_division_by_zero() -> None:
    for domain in DOMAINS.values():
        for metric in domain.metrics.values():
            if metric.ratio_of is not None:
                assert "nullif" in metric.expression.lower(), (
                    f"{domain.key}.{metric.key} can divide by zero"
                )


def test_inventory_positions_are_semi_additive() -> None:
    """Stock sums across stores but never across dates — adding Monday's and
    Tuesday's on-hand invents inventory that never existed."""
    inventory = DOMAINS["inventory"]
    assert inventory.metrics["on_hand_units"].additivity is Additivity.SEMI
    assert inventory.metrics["inventory_value_cost"].additivity is Additivity.SEMI


def test_distinct_counts_are_not_additive() -> None:
    """Summing distinct orders across categories double-counts mixed baskets."""
    assert DOMAINS["revenue"].metrics["orders"].additivity is Additivity.NON


def test_customer_domain_has_no_time_grain() -> None:
    """RFM segments are point-in-time aggregates; a date axis would be fiction."""
    assert DOMAINS["customer"].date_column == ""


def test_unknown_domain_resolves_to_none() -> None:
    assert get_domain("suppliers") is None


# ── Request validation (the security boundary) ───────────────────────


def _request(**overrides: object) -> AnalyticsRequest:
    base = {
        "domain": DOMAINS["revenue"],
        "metrics": ["net_revenue"],
        "dimensions": ["category"],
    }
    return AnalyticsRequest(**{**base, **overrides})  # type: ignore[arg-type]


def test_valid_request_passes() -> None:
    _request().validate()


def test_unknown_metric_is_rejected_with_alternatives() -> None:
    """Caller input never becomes SQL: it is matched against the registry."""
    with pytest.raises(ValidationDomainError) as excinfo:
        _request(metrics=["revenu"]).validate()
    assert "unknown metric" in str(excinfo.value)
    assert "net_revenue" in (excinfo.value.hint or "")


def test_unknown_dimension_is_rejected() -> None:
    with pytest.raises(ValidationDomainError, match="unknown dimension"):
        _request(dimensions=["store_climate"]).validate()


def test_sql_injection_attempt_is_rejected_as_an_unknown_metric() -> None:
    """The registry is the reason injection cannot reach the compiler."""
    with pytest.raises(ValidationDomainError, match="unknown metric"):
        _request(metrics=["net_revenue); DROP TABLE fct_sales;--"]).validate()


def test_filtering_on_an_undeclared_dimension_is_rejected() -> None:
    with pytest.raises(ValidationDomainError, match="cannot filter"):
        _request(filters={"secret_column": "x"}).validate()


def test_sorting_by_something_not_requested_is_rejected() -> None:
    """Sorting by an unselected column would produce SQL referencing a name
    that is not in the projection."""
    with pytest.raises(ValidationDomainError, match="cannot sort"):
        _request(sort_by="margin_amount").validate()


def test_empty_metric_list_is_rejected() -> None:
    with pytest.raises(ValidationDomainError, match="at least one metric"):
        _request(metrics=[]).validate()


def test_inverted_period_is_rejected() -> None:
    with pytest.raises(ValidationDomainError, match="must not be after"):
        _request(start_date=date(2026, 7, 21), end_date=date(2026, 7, 1)).validate()


# ── Query fingerprinting (cache correctness) ─────────────────────────


def test_fingerprint_is_stable_across_equivalent_orderings() -> None:
    """Two logically identical queries must share a cache entry."""
    first = SemanticQuery(
        relation="v_mart_sales_daily",
        select=["category", "region"],
        aggregates={"net_revenue": "sum(net_revenue)", "units_sold": "sum(units_sold)"},
    )
    second = SemanticQuery(
        relation="v_mart_sales_daily",
        select=["region", "category"],
        aggregates={"units_sold": "sum(units_sold)", "net_revenue": "sum(net_revenue)"},
    )
    assert first.fingerprint() == second.fingerprint()


def test_fingerprint_changes_with_filters() -> None:
    """Different questions must not share an answer."""
    base = SemanticQuery(relation="v_mart_sales_daily", aggregates={"r": "sum(net_revenue)"})
    filtered = SemanticQuery(
        relation="v_mart_sales_daily",
        aggregates={"r": "sum(net_revenue)"},
        filters=[("region", "eq", "West")],
    )
    assert base.fingerprint() != filtered.fingerprint()


def test_fingerprint_changes_with_paging() -> None:
    page_one = SemanticQuery(relation="v", aggregates={"r": "sum(x)"}, limit=10, offset=0)
    page_two = SemanticQuery(relation="v", aggregates={"r": "sum(x)"}, limit=10, offset=10)
    assert page_one.fingerprint() != page_two.fingerprint()
