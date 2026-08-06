"""Explaining a KPI from the registry that defines it.

The definitions here are not written down twice. They are read from the metric
registry — the same declaration the compiler turns into SQL — so an
explanation cannot drift from the number it explains. A glossary maintained
separately is one that describes last year's definition with total confidence.

What makes an explanation useful is not the definition. It is the **failure
mode**: how this particular metric gets misread. Additivity and ratio
recomposition are where retail reporting goes wrong most often, and the
registry already records both, so the warning can be generated rather than
remembered.
"""

from dataclasses import dataclass
from typing import Any

from app.services.analytics.registry import DOMAINS, Additivity, Domain, Metric

#: What each additivity class means for someone reading a number, and the
#: mistake it invites. Written from the reader's side rather than the
#: modeller's: "do not sum across dates" is actionable, "semi-additive" is not.
ADDITIVITY_GUIDANCE: dict[Additivity, str] = {
    Additivity.FULL: (
        "Safe to add up across any cut — days, stores, categories. A total is a total."
    ),
    Additivity.SEMI: (
        "Add across stores and products, never across dates. Stock on hand on "
        "Monday plus stock on Tuesday is not two days of inventory; it is the "
        "same units counted twice."
    ),
    Additivity.NON: (
        "Never add this up. It is a rate, a ratio, or a distinct count, and "
        "summing it produces a number with no meaning. Recompute it from its "
        "components at whatever grain you need."
    ),
}


@dataclass(frozen=True, slots=True)
class KpiExplanation:
    """Everything worth saying about one metric."""

    key: str
    label: str
    domain: str
    definition: str
    unit: str
    additivity: str
    how_to_read: str
    computed_as: str
    misreadings: tuple[str, ...] = ()
    related: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "domain": self.domain,
            "definition": self.definition,
            "unit": self.unit,
            "additivity": self.additivity,
            "how_to_read": self.how_to_read,
            "computed_as": self.computed_as,
            "misreadings": list(self.misreadings),
            "related": list(self.related),
        }


def find(term: str) -> tuple[str, Metric, Domain] | None:
    """Locate a metric by key or label, across every domain.

    Exact key first, then a case-insensitive label match, then a substring —
    in that order, so "revenue" resolves to `net_revenue` rather than to
    whichever domain happens to be iterated first.
    """
    needle = term.strip().lower().replace(" ", "_")

    for domain_key, domain in DOMAINS.items():
        if needle in domain.metrics:
            return domain_key, domain.metrics[needle], domain

    for domain_key, domain in DOMAINS.items():
        for metric in domain.metrics.values():
            if metric.label.lower() == term.strip().lower():
                return domain_key, metric, domain

    for domain_key, domain in DOMAINS.items():
        for key, metric in domain.metrics.items():
            if needle in key or needle in metric.label.lower():
                return domain_key, metric, domain

    return None


def explain(term: str) -> KpiExplanation | None:
    """Explain a metric, including how it is commonly misread."""
    found = find(term)
    if found is None:
        return None
    domain_key, metric, domain = found

    misreadings: list[str] = []

    if metric.additivity is Additivity.NON:
        misreadings.append(
            f"Averaging {metric.label.lower()} across groups. It is a ratio, "
            "so the average of several is not the figure for all of them "
            "together — a quiet Sunday would weigh the same as a peak Friday."
        )
    if metric.additivity is Additivity.SEMI:
        misreadings.append(
            f"Summing {metric.label.lower()} over a date range. Stock is a "
            "position, not a flow: adding two days invents inventory that was "
            "never there."
        )
    if metric.ratio_of:
        numerator, denominator = metric.ratio_of
        misreadings.append(
            f"Recomputing it wrongly. This is {numerator} over {denominator}, "
            "and the platform recomputes it from those components at whatever "
            "grain you ask for. Taking a mean of the per-row values instead "
            "gives a different — and wrong — answer."
        )
    if metric.unit == "currency":
        misreadings.append(
            "Comparing periods of different lengths. A 28-day figure against a "
            "31-day one is a 10% difference before anything happened."
        )

    computed = (
        f"{metric.ratio_of[0]} ÷ {metric.ratio_of[1]}, recomputed at the grain you ask for"
        if metric.ratio_of
        else metric.expression
    )

    related = tuple(
        key
        for key in domain.metrics
        if key != metric.key
        and (
            metric.ratio_of is not None
            and key in metric.ratio_of
            or any(
                other.ratio_of is not None and metric.key in other.ratio_of
                for other in [domain.metrics[key]]
            )
        )
    )

    return KpiExplanation(
        key=metric.key,
        label=metric.label,
        domain=domain_key,
        definition=metric.description,
        unit=metric.unit,
        additivity=metric.additivity.value,
        how_to_read=ADDITIVITY_GUIDANCE[metric.additivity],
        computed_as=computed,
        misreadings=tuple(misreadings),
        related=related[:4],
    )
