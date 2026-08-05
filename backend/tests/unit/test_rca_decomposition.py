"""The arithmetic behind every root-cause finding, checked against hand sums.

These are the tests that matter most in the RCA engine. Everything above this
layer is presentation; if the decomposition is wrong, the confident prose
around it makes the situation worse rather than better.
"""

from datetime import date

import pytest

from app.services.rca.confidence import compose, describe, is_material
from app.services.rca.contracts import (
    TIER_CEILING,
    ClaimType,
    Dimension,
    Evidence,
    EvidenceTier,
    Finding,
    Investigation,
    Window,
)
from app.services.rca.decomposition import (
    SliceChange,
    contributions,
    normalise_significance,
    volume_rate_split,
)

# ── Contribution decomposition ───────────────────────────────────────


def test_shares_sum_to_the_whole() -> None:
    """The defining property. If these do not sum, nothing above is trustworthy."""
    slices = [
        SliceChange("A", current=80.0, baseline=100.0),
        SliceChange("B", current=95.0, baseline=100.0),
        SliceChange("C", current=105.0, baseline=100.0),
    ]
    results = contributions(slices, network_current=280.0, network_baseline=300.0)

    assert sum(item.share for item in results) == pytest.approx(1.0)


def test_a_slice_moving_exactly_with_the_network_has_no_excess() -> None:
    """The heart of the design.

    Every slice falls 10%; the network falls 10%. Nothing behaved unusually,
    so nothing should be flagged — even though each slice contributes a large
    gross share of the decline. An engine ranking on gross contribution would
    report three findings here and all three would be noise.
    """
    slices = [
        SliceChange("A", current=90.0, baseline=100.0),
        SliceChange("B", current=180.0, baseline=200.0),
        SliceChange("C", current=270.0, baseline=300.0),
    ]
    results = contributions(slices, network_current=540.0, network_baseline=600.0)

    for item in results:
        assert item.excess_change == pytest.approx(0.0, abs=1e-9)
        assert item.excess_share == pytest.approx(0.0, abs=1e-9)
        assert abs(item.share) > 0.1  # gross contribution is large for all three


def test_excess_isolates_the_slice_that_behaved_differently() -> None:
    """One region collapses while the rest hold flat.

    Note the sign convention, which is the whole reason the engine ranks the
    right things. Excess shares sum to *zero*, not one: the network's own
    movement is already subtracted, so a slice that fell harder than the
    estate is positive and one that held up is negative. Ranking on this and
    keeping only the positives is what stops a region that grew during a
    decline being reported as a cause of it.
    """
    slices = [
        SliceChange("Northeast", current=50.0, baseline=100.0),
        SliceChange("Midwest", current=100.0, baseline=100.0),
        SliceChange("West", current=100.0, baseline=100.0),
    ]
    decomposed = contributions(slices, network_current=250.0, network_baseline=300.0)
    results = {item.label: item for item in decomposed}

    assert results["Northeast"].excess_share > 0
    assert results["Midwest"].excess_share < 0
    assert results["West"].excess_share < 0
    assert sum(item.excess_share for item in decomposed) == pytest.approx(0.0, abs=1e-9)
    # Northeast is the only slice that moved against the grain, so it owns the
    # entire excess: the other two split its mirror image between them.
    assert results["Northeast"].excess_share == pytest.approx(2 / 3)


def test_a_big_slice_moving_normally_does_not_outrank_a_small_one_collapsing() -> None:
    """The failure mode excess contribution exists to prevent.

    A huge region drifting with the market contributes more raw pounds than a
    small one halving. Gross contribution ranks the giant first, every time,
    for every decline — which is a description of the estate's shape, not a
    finding.
    """
    slices = [
        SliceChange("Giant", current=9000.0, baseline=10000.0),
        SliceChange("Tiny", current=250.0, baseline=1000.0),
    ]
    results = {
        item.label: item
        for item in contributions(slices, network_current=9250.0, network_baseline=11000.0)
    }

    assert abs(results["Giant"].change) > abs(results["Tiny"].change)
    assert results["Tiny"].excess_share > results["Giant"].excess_share


def test_shares_are_zero_rather_than_absurd_when_the_network_is_flat() -> None:
    """Dividing by a near-zero total produces mathematically correct nonsense."""
    slices = [
        SliceChange("A", current=150.0, baseline=100.0),
        SliceChange("B", current=50.0, baseline=100.0),
    ]
    results = contributions(slices, network_current=200.0, network_baseline=200.0)

    assert all(item.share == 0.0 for item in results)


def test_peer_deviation_needs_enough_peers_to_mean_anything() -> None:
    two = contributions(
        [SliceChange("A", 90.0, 100.0), SliceChange("B", 80.0, 100.0)],
        network_current=170.0,
        network_baseline=200.0,
    )
    assert all(item.peer_z is None for item in two)


def test_peer_deviation_flags_the_outlier_among_many() -> None:
    slices = [SliceChange(f"S{n}", current=99.0, baseline=100.0) for n in range(6)]
    slices.append(SliceChange("Outlier", current=40.0, baseline=100.0))
    results = {
        item.label: item
        for item in contributions(slices, network_current=634.0, network_baseline=700.0)
    }

    assert results["Outlier"].peer_z is not None
    assert results["Outlier"].peer_z < -1.5


def test_slices_need_not_partition_the_estate() -> None:
    """Segments miss guest checkout, so the parts do not sum to the network.

    The denominator comes from the network figure rather than the slices; if
    it were summed from the parts, every share would be inflated by whatever
    the cut fails to cover.
    """
    slices = [SliceChange("Known", current=60.0, baseline=100.0)]
    results = contributions(slices, network_current=160.0, network_baseline=200.0)

    assert results[0].share == pytest.approx(1.0)


# ── Volume versus rate ───────────────────────────────────────────────


def test_volume_and_rate_effects_reconcile_to_the_total() -> None:
    """A decomposition whose parts do not sum is a set of numbers."""
    split = volume_rate_split(
        current_total=8000.0, baseline_total=10000.0, current_count=80.0, baseline_count=100.0
    )
    assert split.reconciles


def test_pure_footfall_loss_is_reported_as_volume() -> None:
    """Same basket, fewer baskets."""
    split = volume_rate_split(
        current_total=5000.0, baseline_total=10000.0, current_count=50.0, baseline_count=100.0
    )
    assert split.dominant == "volume"
    assert split.rate_effect == pytest.approx(0.0)


def test_pure_basket_shrinkage_is_reported_as_rate() -> None:
    """Same baskets, worth less."""
    split = volume_rate_split(
        current_total=5000.0, baseline_total=10000.0, current_count=100.0, baseline_count=100.0
    )
    assert split.dominant == "rate"
    assert split.volume_effect == pytest.approx(0.0)


def test_volume_rate_survives_a_zero_baseline_count() -> None:
    split = volume_rate_split(
        current_total=100.0, baseline_total=0.0, current_count=10.0, baseline_count=0.0
    )
    assert split.reconciles is False or split.total_change == 100.0


# ── Confidence ───────────────────────────────────────────────────────


def test_every_tier_is_capped_at_its_ceiling() -> None:
    """The load-bearing rule: evidence type limits confidence, not signal strength."""
    for tier in EvidenceTier:
        maxed = compose(tier, impact=1.0, significance=1.0, consistency=1.0)
        assert maxed == pytest.approx(TIER_CEILING[tier])


def test_an_overwhelming_correlation_cannot_outrank_a_mechanism() -> None:
    """A perfect weather signal must still sit below a mediocre stockout."""
    perfect_weather = compose(
        EvidenceTier.ASSOCIATIVE, impact=1.0, significance=1.0, consistency=1.0
    )
    modest_mechanism = compose(
        EvidenceTier.MECHANICAL, impact=0.6, significance=0.6, consistency=0.6
    )
    assert perfect_weather < modest_mechanism


def test_a_weak_component_collapses_the_score() -> None:
    """Geometric, not arithmetic: a cause must be both unusual and material."""
    unusual_but_trivial = compose(
        EvidenceTier.MECHANICAL, impact=0.01, significance=1.0, consistency=1.0
    )
    balanced = compose(EvidenceTier.MECHANICAL, impact=0.5, significance=0.5, consistency=0.5)
    assert unusual_but_trivial < balanced


def test_confidence_never_reaches_certainty() -> None:
    """Observational data cannot establish a cause outright."""
    assert all(ceiling < 1.0 for ceiling in TIER_CEILING.values())


def test_unknown_significance_is_neutral_rather_than_damning() -> None:
    """Absence of evidence is not evidence of absence."""
    assert normalise_significance(None) == 0.5
    assert normalise_significance(0.0) == 0.0


def test_significance_saturates_rather_than_growing_without_limit() -> None:
    assert normalise_significance(3.0) == normalise_significance(40.0) == 1.0


def test_materiality_floor_filters_trivia() -> None:
    assert not is_material(0.01)
    assert is_material(0.30)


def test_confidence_bands_are_words_not_false_precision() -> None:
    assert describe(0.8) == "strong"
    assert describe(0.5) == "moderate"
    assert describe(0.1) == "tentative"


# ── Findings enforce their own limits ────────────────────────────────


def _finding(tier: EvidenceTier, confidence: float, share: float = 0.5) -> Finding:
    return Finding(
        dimension=Dimension.REGION,
        subject="Northeast",
        headline="test",
        claim_type=ClaimType.ACCOUNTS_FOR,
        tier=tier,
        confidence=confidence,
        impact_share=share,
    )


def test_a_finding_cannot_be_constructed_above_its_ceiling() -> None:
    """Enforced in the type, not left to the caller to remember."""
    with pytest.raises(ValueError, match="ceiling"):
        _finding(EvidenceTier.ASSOCIATIVE, 0.9)


def test_ranking_weighs_confidence_by_what_is_at_stake() -> None:
    """Certain and trivial must lose to uncertain and expensive."""
    certain_trivial = _finding(EvidenceTier.ARITHMETIC, 0.95, share=0.02)
    unsure_large = _finding(EvidenceTier.STATISTICAL, 0.40, share=0.60)
    assert unsure_large.rank_score > certain_trivial.rank_score


# ── Investigation-level accounting ───────────────────────────────────


def _investigation(findings: tuple[Finding, ...]) -> Investigation:
    return Investigation(
        metric="net_revenue",
        current=Window(date(2026, 7, 15), date(2026, 7, 21)),
        baseline=Window(date(2026, 6, 24), date(2026, 7, 14)),
        current_value=900.0,
        baseline_value=1000.0,
        findings=findings,
        dimensions_investigated=(Dimension.REGION,),
    )


def test_cuts_do_not_add_together() -> None:
    """Region and segment describe the same pounds twice.

    Summing them counts every pound once per cut and produces headline figures
    like 298% explained, with every individual number still correct.
    """
    region = Finding(
        dimension=Dimension.REGION,
        subject="NE",
        headline="",
        claim_type=ClaimType.ACCOUNTS_FOR,
        tier=EvidenceTier.ARITHMETIC,
        confidence=0.7,
        impact_share=0.8,
    )
    segment = Finding(
        dimension=Dimension.SEGMENT,
        subject="Loyal",
        headline="",
        claim_type=ClaimType.ACCOUNTS_FOR,
        tier=EvidenceTier.ARITHMETIC,
        confidence=0.7,
        impact_share=0.9,
    )

    investigation = _investigation((region, segment))
    assert investigation.explained_share == pytest.approx(0.9)
    assert investigation.coverage_by_dimension == {"region": 0.8, "segment": 0.9}


def test_explanations_do_not_count_toward_explained_share() -> None:
    """A stockout explains a regional shortfall already counted, not a new one."""
    region = Finding(
        dimension=Dimension.REGION,
        subject="NE",
        headline="",
        claim_type=ClaimType.ACCOUNTS_FOR,
        tier=EvidenceTier.ARITHMETIC,
        confidence=0.7,
        impact_share=0.8,
    )
    weather = Finding(
        dimension=Dimension.WEATHER,
        subject="NE",
        headline="",
        claim_type=ClaimType.COINCIDES_WITH,
        tier=EvidenceTier.ASSOCIATIVE,
        confidence=0.4,
        impact_share=0.5,
    )

    assert _investigation((region, weather)).explained_share == pytest.approx(0.8)


def test_evidence_reports_its_own_comparison() -> None:
    item = Evidence("Stockout rate", value=0.12, baseline=0.04, unit="rate")
    assert item.change == pytest.approx(0.08)
    assert item.relative_change == pytest.approx(2.0)
