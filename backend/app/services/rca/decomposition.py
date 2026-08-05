"""The arithmetic underneath every finding.

Pure functions over numbers: no database, no configuration, no state. That is
deliberate — this is the part that must be provably right, and the way to make
it provable is to let it be tested with numbers a reader can do in their head.

Three decompositions live here, and the second is the one that separates a
useful engine from a confident useless one.

**Contribution.** How much of the total change each slice accounts for. Exact:
the shares sum to one, and the test suite asserts it.

**Excess contribution.** How much each slice moved *relative to how the whole
estate moved*. This matters more than it sounds. A naive engine ranks slices
by raw contribution and therefore ranks them, in practice, by size — the
largest region "explains" most of every decline, every time, and reports it as
an insight. The finding a merchant needs is which slice moved *differently*,
and that requires comparing each slice against what it would have done had it
simply tracked the network.

**Volume versus rate.** Revenue is orders times basket value, so a fall is
either fewer transactions, smaller ones, or both. The distinction survives
into completely different responses — a footfall problem is a marketing or
availability question, a basket problem is a pricing or mix question — and an
engine reporting only "revenue fell 12%" has not narrowed anything down.
"""

from dataclasses import dataclass
from math import sqrt

#: A change smaller than this is treated as no change. Guards the division in
#: every share calculation: when the network is flat, slice contributions are
#: ratios of noise to noise and can reach thousands of percent.
NEGLIGIBLE = 1e-9

#: Minimum peers before a peer-relative z-score is meaningful. With three
#: regions the standard deviation is an estimate from two degrees of freedom
#: and any of them can look like an outlier.
MIN_PEERS_FOR_DEVIATION = 4


@dataclass(frozen=True, slots=True)
class SliceChange:
    """One slice's movement, decomposed every way that is exact."""

    label: str
    current: float
    baseline: float
    current_orders: float = 0.0
    baseline_orders: float = 0.0

    @property
    def change(self) -> float:
        return self.current - self.baseline

    @property
    def relative_change(self) -> float | None:
        if abs(self.baseline) < NEGLIGIBLE:
            return None
        return self.change / abs(self.baseline)


@dataclass(frozen=True, slots=True)
class Contribution:
    """A slice's share of the total change, gross and excess."""

    label: str
    change: float
    share: float
    """Share of the total change. Sums to 1.0 across slices, by construction."""

    expected_change: float
    """What this slice would have done had it moved with the network."""

    excess_change: float
    """Actual minus expected. The part that is *this slice's* behaviour rather
    than the estate's."""

    excess_share: float
    """Excess as a share of the total change — the ranking key."""

    relative_change: float | None
    current: float
    baseline: float
    peer_z: float | None = None
    """How unusual this slice's relative movement is among its peers."""


def contributions(
    slices: list[SliceChange], *, network_current: float, network_baseline: float
) -> list[Contribution]:
    """Decompose a total change across slices, gross and excess.

    ``network_current`` and ``network_baseline`` are passed rather than summed
    from the slices on purpose. Slices need not partition the estate — a sale
    to an unidentified customer belongs to no RFM segment — and summing the
    parts would produce a denominator that quietly excludes whatever the cut
    does not cover, making every share too large.
    """
    total_change = network_current - network_baseline
    growth = network_current / network_baseline if abs(network_baseline) > NEGLIGIBLE else 1.0

    results: list[Contribution] = []
    for item in slices:
        expected = item.baseline * growth
        excess = item.current - expected
        results.append(
            Contribution(
                label=item.label,
                change=item.change,
                share=_safe_share(item.change, total_change),
                expected_change=expected - item.baseline,
                excess_change=excess,
                excess_share=_safe_share(excess, total_change),
                relative_change=item.relative_change,
                current=item.current,
                baseline=item.baseline,
            )
        )

    return _with_peer_deviation(results)


def _with_peer_deviation(items: list[Contribution]) -> list[Contribution]:
    """Attach a peer-relative z-score to each slice's relative movement.

    Compares a slice against the other slices of the same cut rather than
    against its own history. Both are defensible; this one answers the
    question a merchant is actually asking — "is this region behaving
    differently from the others?" — and needs only the aggregates already in
    hand, where a temporal z-score needs the full daily series for every slice.

    The limitation is stated in the response: a shock that hit every region
    equally has no peer deviation anywhere, and this statistic will report all
    of them as unremarkable. That is why it grades a finding rather than
    creating one.
    """
    usable = [item.relative_change for item in items if item.relative_change is not None]
    if len(usable) < MIN_PEERS_FOR_DEVIATION:
        return items

    mean = sum(usable) / len(usable)
    variance = sum((value - mean) ** 2 for value in usable) / (len(usable) - 1)
    spread = sqrt(variance)
    if spread < NEGLIGIBLE:
        return items

    return [
        Contribution(
            **{
                **{
                    field: getattr(item, field)
                    for field in (
                        "label",
                        "change",
                        "share",
                        "expected_change",
                        "excess_change",
                        "excess_share",
                        "relative_change",
                        "current",
                        "baseline",
                    )
                },
                "peer_z": (
                    (item.relative_change - mean) / spread
                    if item.relative_change is not None
                    else None
                ),
            }
        )
        for item in items
    ]


@dataclass(frozen=True, slots=True)
class VolumeRateSplit:
    """A change split into transaction count, basket value, and interaction."""

    total_change: float
    volume_effect: float
    """Change attributable to doing more or fewer transactions."""

    rate_effect: float
    """Change attributable to each transaction being worth more or less."""

    interaction: float
    """The cross term. Small when either change is small; reported rather than
    folded into one of the others, because silently assigning it is how the
    two effects stop summing to the total."""

    @property
    def dominant(self) -> str:
        """Which effect drives the change — the diagnosis, in one word."""
        if abs(self.volume_effect) >= abs(self.rate_effect):
            return "volume"
        return "rate"

    @property
    def reconciles(self) -> bool:
        parts = self.volume_effect + self.rate_effect + self.interaction
        return abs(parts - self.total_change) <= 1e-6 * max(1.0, abs(self.total_change))


def volume_rate_split(
    *,
    current_total: float,
    baseline_total: float,
    current_count: float,
    baseline_count: float,
) -> VolumeRateSplit:
    """Split a change into volume and rate effects, exactly.

    With ``total = count × rate``::

        Δtotal = Δcount·rate_b  +  count_b·Δrate  +  Δcount·Δrate

    All three terms are kept. The common shortcut is to drop the interaction
    or bury it in one of the main effects, which makes the parts stop summing
    to the whole — and a decomposition that does not reconcile is a set of
    numbers rather than a decomposition.
    """
    baseline_rate = baseline_total / baseline_count if abs(baseline_count) > NEGLIGIBLE else 0.0
    current_rate = current_total / current_count if abs(current_count) > NEGLIGIBLE else 0.0

    delta_count = current_count - baseline_count
    delta_rate = current_rate - baseline_rate

    return VolumeRateSplit(
        total_change=current_total - baseline_total,
        volume_effect=delta_count * baseline_rate,
        rate_effect=baseline_count * delta_rate,
        interaction=delta_count * delta_rate,
    )


def _safe_share(part: float, whole: float) -> float:
    """Share of a whole, guarding the case where the whole is ~zero.

    A flat network with slices moving in opposite directions produces shares
    of ±40,000%, which are arithmetically correct and completely useless.
    Returning zero says "this cut explains nothing here", which is the honest
    reading when there is no net change to explain.
    """
    if abs(whole) < NEGLIGIBLE:
        return 0.0
    return part / whole


def normalise_significance(z_score: float | None, *, saturate_at: float = 3.0) -> float:
    """Map a z-score onto 0–1 for confidence composition.

    Saturating at three standard deviations rather than scaling without limit:
    beyond that the difference between "very unusual" and "extraordinarily
    unusual" does not change what anyone does, and letting it keep growing
    would let one wild statistic dominate a composite score.
    """
    if z_score is None:
        return 0.5  # unknown, not zero: absence of evidence is not evidence
    return min(1.0, abs(z_score) / saturate_at)
