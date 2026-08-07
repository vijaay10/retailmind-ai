"""Impact arithmetic, risk sizing, and the rules that stop a number lying.

The estimators are where this feature is most able to mislead: every function
here produces a pound figure that somebody will put in a business case. These
tests pin the properties that keep those figures defensible — that capital is
never counted as profit, that an assumption cannot be dressed as a
measurement, and that confidence cannot outrun the weakest input.
"""

import pytest

from app.services.recommendations import estimators, generators
from app.services.recommendations.contracts import (
    BASIS_CEILING,
    Assumption,
    Category,
    EstimateBasis,
    ImpactEstimate,
    Portfolio,
    Recommendation,
    Reversibility,
    RiskProfile,
)
from app.services.recommendations.service import _estate_margin_rate, _median


def _impact(
    profit: float = 1000.0,
    basis: EstimateBasis = EstimateBasis.MEASURED,
    capital: float = 0.0,
) -> ImpactEstimate:
    return ImpactEstimate(
        revenue=profit * 2,
        profit=profit,
        basis=basis,
        horizon_days=30,
        method="test",
        capital_freed=capital,
    )


def _risk(
    downside: float = -100.0,
    reversibility: Reversibility = Reversibility.REVERSIBLE,
) -> RiskProfile:
    return RiskProfile(
        reversibility=reversibility,
        downside_profit=downside,
        blast_radius="test",
        principal_risk="test",
    )


def _rec(
    *,
    profit: float = 1000.0,
    basis: EstimateBasis = EstimateBasis.MEASURED,
    confidence: float = 0.5,
    downside: float = -100.0,
    scope: frozenset[str] = frozenset(),
    reversibility: Reversibility = Reversibility.REVERSIBLE,
    capital: float = 0.0,
) -> Recommendation:
    return Recommendation(
        category=Category.INVENTORY,
        subject="test",
        action="test",
        rationale="test",
        impact=_impact(profit, basis, capital),
        risk=_risk(downside, reversibility),
        confidence=confidence,
        scope=scope,
    )


# ── Confidence is capped by how the estimate was derived ─────────────


def test_confidence_cannot_exceed_the_basis_ceiling() -> None:
    """An estimate cannot be more certain than its weakest input.

    An elasticity-based figure is a guess with arithmetic around it, and no
    amount of supporting detail promotes it into a measurement.
    """
    with pytest.raises(ValueError, match="ceiling"):
        _rec(basis=EstimateBasis.ASSUMED, confidence=0.8)


def test_measured_estimates_may_be_more_confident_than_assumed_ones() -> None:
    assert BASIS_CEILING[EstimateBasis.MEASURED] > BASIS_CEILING[EstimateBasis.MODELLED]
    assert BASIS_CEILING[EstimateBasis.MODELLED] > BASIS_CEILING[EstimateBasis.ASSUMED]


def test_nothing_reaches_certainty() -> None:
    """Even exact arithmetic assumes the recommendation is executed at all."""
    assert all(ceiling < 1.0 for ceiling in BASIS_CEILING.values())


# ── Capital is not profit ────────────────────────────────────────────


def test_liquidation_reports_capital_and_loss_separately() -> None:
    """Clearing dead stock frees cash *and* books a loss.

    Netting them into one positive number is how a clearance programme gets
    approved on the strength of the thing that makes it expensive.
    """
    impact = estimators.liquidation(excess_units=100, unit_cost=10.0, markdown_depth=0.5)

    assert impact.capital_freed == pytest.approx(500.0)
    assert impact.profit < 0, "a 50% markdown cannot be profitable"


def test_portfolio_keeps_capital_out_of_the_profit_total() -> None:
    portfolio = Portfolio(
        recommendations=(_rec(profit=-200.0, capital=5000.0),),
        categories_requested=(Category.PRICING,),
    )
    assert portfolio.capital_freed == pytest.approx(5000.0)
    assert portfolio.gross_profit_opportunity == pytest.approx(-200.0)


# ── Downside and reversibility ───────────────────────────────────────


def test_ranking_accounts_for_the_downside() -> None:
    """Two identical upsides are not equally attractive."""
    safe = _rec(profit=1000.0, downside=-10.0, confidence=0.5)
    dangerous = _rec(profit=1000.0, downside=-5000.0, confidence=0.5)
    assert safe.risk_adjusted_profit > dangerous.risk_adjusted_profit


def test_an_irreversible_action_is_high_risk_regardless_of_upside() -> None:
    """A large prize does not make a permanent margin give-away safe."""
    markdown = _rec(profit=1_000_000.0, downside=-50.0, reversibility=Reversibility.IRREVERSIBLE)
    assert markdown.risk.band == "high"


def test_a_reversible_action_is_low_risk() -> None:
    """Ordering too much leaves stock that eventually sells."""
    assert _rec(reversibility=Reversibility.REVERSIBLE).risk.band == "low"


def test_low_confidence_lets_the_downside_dominate() -> None:
    """A coin-flip recommendation with a big downside should rank negative."""
    gamble = _rec(profit=100.0, downside=-10_000.0, confidence=0.2)
    assert gamble.risk_adjusted_profit < 0


# ── Assumptions are labelled, not laundered ──────────────────────────


def test_placeholder_parameters_are_not_presented_as_measurements() -> None:
    impact = estimators.price_change(current_revenue=100_000.0, margin_rate=0.4, price_delta=0.02)
    elasticity = next(a for a in impact.assumptions if a.name == "price_elasticity")

    assert elasticity.source == "placeholder"
    assert not elasticity.is_evidenced
    assert impact.rests_on_unmeasured_assumptions


def test_measured_inputs_are_marked_as_such() -> None:
    assert Assumption("margin_rate", 0.4, "measured").is_evidenced


def test_assumed_estimates_carry_a_sensitivity_range() -> None:
    """The only question that matters is whether the assumption changes the call."""
    impact = estimators.price_change(current_revenue=100_000.0, margin_rate=0.4, price_delta=0.02)
    assert impact.pessimistic_profit is not None
    assert impact.optimistic_profit is not None
    assert impact.pessimistic_profit < impact.optimistic_profit
    assert impact.spread is not None and impact.spread > 0


def test_elasticity_drives_the_sign_of_a_price_rise() -> None:
    """At an elastic enough response, a price rise destroys profit.

    This is why the recommendation built on it is a *test*, not a rollout.
    """
    inelastic = estimators.price_change(
        current_revenue=100_000.0, margin_rate=0.4, price_delta=0.02, elasticity=-0.5
    )
    elastic = estimators.price_change(
        current_revenue=100_000.0, margin_rate=0.4, price_delta=0.02, elasticity=-4.0
    )
    assert inelastic.profit > 0
    assert elastic.profit < inelastic.profit


# ── The retention estimate does not spend money already spent ────────


def test_retention_prizes_forward_spend_not_lifetime_value() -> None:
    """Lifetime value is money the customer has *already* spent.

    Applying a save rate to it directly overstates the prize by roughly the
    ratio of tenure to campaign horizon — fourfold here, and much more for a
    long-tenured base. It is the single most common error in a retention
    business case.
    """
    value_at_risk = 1_000_000.0
    impact = estimators.retention_campaign(value_at_risk=value_at_risk, customers=100)

    naive = value_at_risk * estimators.CAMPAIGN_SAVE_RATE
    assert impact.revenue < naive / 3
    assert impact.basis is EstimateBasis.ASSUMED


def test_retention_subtracts_the_campaign_cost() -> None:
    """The contact cost is spent whether or not anyone is retained."""
    impact = estimators.retention_campaign(value_at_risk=1000.0, customers=10_000)
    assert impact.profit < 0


# ── Availability ─────────────────────────────────────────────────────


def test_availability_recovers_only_the_share_that_does_not_substitute() -> None:
    """Counting all unmet demand as lost is how availability cases inflate."""
    impact = estimators.availability_recovery(revenue_at_risk=10_000.0, margin_rate=0.4)
    assert impact.revenue < 10_000.0
    assert impact.basis is EstimateBasis.MODELLED


def test_safety_stock_release_uses_the_square_root_relationship() -> None:
    """Halving lead time releases about 30% of the buffer, not half of it.

    A linear rule overstates the prize substantially, and it is the part a
    spreadsheet estimate almost always gets wrong.
    """
    impact = estimators.safety_stock_release(
        current_safety_stock=100.0,
        lead_time_days=20.0,
        improved_lead_time_days=10.0,
        unit_cost=1.0,
    )
    assert 25.0 < impact.capital_freed < 35.0


# ── Portfolio totals ─────────────────────────────────────────────────


def test_overlapping_recommendations_are_counted_once() -> None:
    """Reordering a line and fixing its supplier chase the same pounds."""
    portfolio = Portfolio(
        recommendations=(
            _rec(profit=1000.0, scope=frozenset({"sku:A", "store:S1"})),
            _rec(profit=800.0, scope=frozenset({"sku:A"})),
        ),
        categories_requested=(Category.INVENTORY,),
    )
    assert portfolio.gross_profit_opportunity == pytest.approx(1800.0)
    assert portfolio.net_profit_opportunity == pytest.approx(1000.0)


def test_independent_recommendations_do_add_up() -> None:
    portfolio = Portfolio(
        recommendations=(
            _rec(profit=1000.0, scope=frozenset({"sku:A"})),
            _rec(profit=800.0, scope=frozenset({"sku:B"})),
        ),
        categories_requested=(Category.INVENTORY,),
    )
    assert portfolio.net_profit_opportunity == pytest.approx(1800.0)


def test_deduplication_keeps_the_larger_of_two_overlapping_claims() -> None:
    portfolio = Portfolio(
        recommendations=(
            _rec(profit=300.0, scope=frozenset({"sku:A"})),
            _rec(profit=900.0, scope=frozenset({"sku:A"})),
        ),
        categories_requested=(Category.INVENTORY,),
    )
    assert portfolio.net_profit_opportunity == pytest.approx(900.0)


def test_net_never_exceeds_gross() -> None:
    portfolio = Portfolio(
        recommendations=tuple(
            _rec(profit=100.0 * n, scope=frozenset({f"sku:{n % 3}"})) for n in range(1, 8)
        ),
        categories_requested=(Category.INVENTORY,),
    )
    assert portfolio.net_profit_opportunity <= portfolio.gross_profit_opportunity


# ── Generators refuse to waste attention ─────────────────────────────


def test_customers_who_are_not_at_risk_are_never_targeted() -> None:
    """The bug this pins cost real budget.

    The registry dimension is `risk_band`; the column it resolves to is
    `churn_risk_band`. Reading the dimension name returned nothing, a default
    filled the gap, and the engine's top recommendation became a retention
    campaign aimed at the customers with no churn risk at all.
    """
    rows = [
        {"churn_risk_band": "none", "customers": 5000, "value_at_risk": 9_000_000.0},
        {"churn_risk_band": "low", "customers": 2000, "value_at_risk": 4_000_000.0},
        {"churn_risk_band": "unknown", "customers": 900, "value_at_risk": 600_000.0},
    ]
    assert generators.customer_recommendations(rows) == []


def test_a_genuinely_at_risk_band_is_targeted() -> None:
    rows = [{"churn_risk_band": "high", "customers": 3000, "value_at_risk": 5_000_000.0}]
    found = generators.customer_recommendations(rows)

    assert len(found) == 1
    assert "high" in found[0].subject


def test_trivial_recommendations_are_suppressed() -> None:
    """A queue that includes everything is a queue nobody works through."""
    rows = [
        {
            "sku": "X",
            "store_id": "S1",
            "suggested_order_qty": 2,
            "daily_demand": 0.1,
            "revenue_at_risk": 3.0,
        }
    ]
    assert generators.inventory_recommendations(rows) == []


def test_every_recommendation_names_its_disqualifier() -> None:
    rows = [{"churn_risk_band": "high", "customers": 3000, "value_at_risk": 5_000_000.0}]
    for item in generators.customer_recommendations(rows):
        assert item.do_not_act_if


def test_a_markdown_is_generated_as_irreversible() -> None:
    rows = [
        {
            "sku": "X",
            "store_id": "S1",
            "excess_units": 200.0,
            "excess_value": 4000.0,
            "cover_days": 400.0,
        }
    ]
    found = generators.pricing_recommendations(rows)
    assert found
    assert found[0].risk.reversibility is Reversibility.IRREVERSIBLE
    assert found[0].risk.band == "high"


def test_a_supplier_meeting_its_contract_is_left_alone() -> None:
    rows = [
        {
            "supplier_name": "Reliable Co",
            "otif_rate": 0.97,
            "closed_lines": 500,
            "avg_lead_time_days": 8.0,
            "ordered_value": 900_000.0,
        }
    ]
    assert generators.supplier_recommendations(rows) == []


def test_a_supplier_with_too_few_lines_is_not_judged() -> None:
    """A rate from nine receipts is arithmetic, not evidence."""
    rows = [
        {
            "supplier_name": "New Co",
            "otif_rate": 0.30,
            "closed_lines": 9,
            "avg_lead_time_days": 30.0,
            "ordered_value": 500_000.0,
        }
    ]
    assert generators.supplier_recommendations(rows) == []


# ── Benchmarks ───────────────────────────────────────────────────────


def test_peer_benchmark_uses_the_median_not_the_mean() -> None:
    """A mean is dragged by the flagship, making every ordinary store a laggard."""
    assert _median([10.0, 10.0, 10.0, 10.0, 1000.0]) == pytest.approx(10.0)


def test_median_handles_an_empty_peer_set() -> None:
    assert _median([]) == 0.0


def test_estate_margin_is_revenue_weighted() -> None:
    """An unweighted mean lets a tiny high-margin category set the valuation."""
    categories = [
        {"net_revenue": 1_000_000.0, "margin_rate": 0.30},
        {"net_revenue": 1_000.0, "margin_rate": 0.90},
    ]
    assert _estate_margin_rate(categories) == pytest.approx(0.3006, abs=1e-3)


# ── Decision identity ────────────────────────────────────────────────


def test_the_decision_key_survives_the_numbers_moving() -> None:
    """The engine recomputes every request, and its wording carries figures.

    Keying a decision on the sentence would detach a manager's approval the
    moment a reorder quantity moved from 122 units to 130 — the card would
    reappear as undecided, and the queue would look like nobody had ever
    worked it.
    """
    monday = Recommendation(
        category=Category.INVENTORY,
        subject="BS-1037@S2016",
        action="Order 122 units of BS-1037 for S2016",
        rationale="r",
        impact=_impact(1000.0, EstimateBasis.MEASURED, 0.0),
        risk=_risk(-100.0, Reversibility.REVERSIBLE),
        confidence=0.6,
    )
    tuesday = Recommendation(
        category=Category.INVENTORY,
        subject="BS-1037@S2016",
        action="Order 130 units of BS-1037 for S2016",
        rationale="r",
        impact=_impact(1200.0, EstimateBasis.MEASURED, 0.0),
        risk=_risk(-100.0, Reversibility.REVERSIBLE),
        confidence=0.7,
    )

    assert monday.decision_key == tuesday.decision_key


def test_different_actions_on_one_subject_are_different_decisions() -> None:
    """Reordering a line and marking it down are opposite calls. Sharing a key
    would let accepting one silently mark the other as handled."""
    reorder = _rec()
    markdown = Recommendation(
        category=Category.PRICING,
        subject="test",
        action="Mark down",
        rationale="r",
        impact=_impact(1000.0, EstimateBasis.ASSUMED, 0.0),
        risk=_risk(-100.0, Reversibility.IRREVERSIBLE),
        confidence=0.4,
    )

    assert reorder.decision_key != markdown.decision_key


def test_the_key_travels_with_the_recommendation() -> None:
    """A card the console cannot key is a card nobody can act on."""
    payload = _rec().as_dict()

    assert len(payload["decision_key"]) == 32
    assert payload["decision_key"] == _rec().decision_key
