"""How confidence is composed, and what the number is allowed to mean.

**Confidence here is not a probability of causation.** Nobody has measured
P(the storm caused the drop), and a system that prints one is inventing a
calibration that was never established. What this number expresses is:

    how strongly this evidence supports a claim *of this type*

which is a weaker statement, and the only one the data licenses. A finding at
0.8 is not "80% likely to be the cause"; it is "strong evidence for the claim
being made", where the claim itself might only be "this slice accounts for a
large share of the change".

Three components multiply, and they multiply rather than average on purpose.
An arithmetic mean lets one strong component carry a finding with nothing
behind it: a factor that moved four standard deviations but affects 0.3% of
the business would score respectably on a mean and is not worth anyone's
morning. Under a geometric mean, a near-zero in any component collapses the
whole score, which is the correct behaviour — a cause needs to be *both*
unusual and material, not one or the other.

The result is then capped by the evidence tier. That cap is the load-bearing
part of the design: correlation strength cannot promote weather into a
mechanism, however loud the signal, because the limit is a property of what
the evidence *is* rather than how large the number came out.
"""

from app.services.rca.contracts import TIER_CEILING, EvidenceTier

#: Below this share of the total change, a finding is not worth surfacing
#: however certain it is. A queue that includes everything is a queue nobody
#: works through.
MIN_MATERIAL_SHARE = 0.05

#: Floor on the components that can legitimately be *unknown*. A missing
#: significance estimate should not zero an otherwise well-evidenced finding:
#: absence of evidence is not evidence of absence.
#:
#: Impact is deliberately excluded from the floor. It is never unknown — it is
#: computed from the decomposition every time — so flooring it would let a
#: finding worth 1% of the change score higher than a balanced one worth 50%,
#: which is exactly backwards.
COMPONENT_FLOOR = 0.15


def compose(
    tier: EvidenceTier,
    *,
    impact: float,
    significance: float,
    consistency: float = 1.0,
) -> float:
    """Combine the three components and cap by evidence tier.

    ``impact``
        Share of the measured change this finding accounts for, 0–1.
    ``significance``
        How unusual the movement is, 0–1 (see ``normalise_significance``).
    ``consistency``
        How much of the window the factor was actually present for. A carrier
        that missed its promises on one day of fourteen explains less than one
        that missed them throughout, even at identical severity.
    """
    components = [
        min(1.0, max(0.0, impact)),
        max(COMPONENT_FLOOR, min(1.0, significance)),
        max(COMPONENT_FLOOR, min(1.0, consistency)),
    ]

    product = 1.0
    for component in components:
        product *= component
    strength = float(product ** (1.0 / len(components)))

    return round(TIER_CEILING[tier] * strength, 4)


def describe(confidence: float) -> str:
    """A word for the number, so a reader is not left calibrating alone.

    Bands rather than a continuum: the difference between 0.61 and 0.64 is not
    meaningful and presenting it as though it were invites false precision.
    """
    if confidence >= 0.70:
        return "strong"
    if confidence >= 0.45:
        return "moderate"
    if confidence >= 0.25:
        return "weak"
    return "tentative"


def is_material(impact_share: float) -> bool:
    """Whether a finding clears the reporting floor."""
    return abs(impact_share) >= MIN_MATERIAL_SHARE
