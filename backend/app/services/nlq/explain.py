"""The business reading of a result.

Composed from the plan and the numbers rather than generated as free text.
That is deliberate: an explanation produced by a language model can drift from
the data it describes — asserting a trend the rows do not show — and a reader
has no way to tell. Everything here is derived arithmetically from the result
set, so it cannot say anything the numbers do not.

The caveats matter as much as the summary. A natural-language interface hides
the assumptions a SQL author would have had to make explicit — which period,
which grouping, which metric — and stating them back is what lets a user
notice they were asked a different question from the one they meant.
"""

from typing import Any

from app.services.nlq.contracts import Explanation, QueryPlan


def build(plan: QueryPlan, rows: list[dict[str, Any]]) -> Explanation:
    """Describe a result set in business terms."""
    if not rows:
        return Explanation(
            summary="No data matched the question over the period requested.",
            caveats=(
                "An empty result is not the same as a zero. It usually means "
                "the period, the filter, or the grouping excluded everything.",
            ),
        )

    metric = plan.metrics[0] if plan.metrics else ""
    label = metric.replace("_", " ")
    details: list[str] = []

    values = [value for value in (_number(row.get(metric)) for row in rows) if value is not None]

    if values and plan.dimensions:
        dimension = plan.dimensions[0]
        total = sum(values)
        leader = rows[0]
        leader_value = _number(leader.get(metric))

        summary = (
            f"{len(rows)} {dimension.replace('_', ' ')} values returned, "
            f"totalling {total:,.0f} {label}."
        )
        if leader_value is not None and total:
            share = leader_value / total if total else 0.0
            details.append(
                f"{leader.get(dimension)} leads with {leader_value:,.0f} "
                f"({share:.0%} of the total shown)."
            )
            # Concentration is the reading a merchant takes from a ranking, and
            # it is arithmetic rather than interpretation.
            top_three = sum(values[:3])
            if len(values) > 3 and total:
                details.append(f"The top three account for {top_three / total:.0%} of the total.")
    elif values:
        summary = f"{label.capitalize()} came to {values[0]:,.0f} over the period."
    else:
        summary = f"{len(rows)} row(s) returned."

    caveats = [
        f"Period: {plan.start_date} to {plan.end_date}."
        if plan.start_date
        else "No period filter was applied.",
        "The total shown covers only the rows returned, which are capped at "
        f"{plan.limit}. It is not the estate total unless every group fits.",
    ]
    if plan.confidence < 0.75:
        caveats.append(
            f"The question was interpreted with {plan.confidence:.0%} confidence. "
            f"{plan.interpretation}"
        )
    if plan.unresolved:
        caveats.append(
            "These terms were not understood and had no effect on the answer: "
            f"{', '.join(plan.unresolved)}."
        )

    return Explanation(summary=summary, details=tuple(details), caveats=tuple(caveats))


def _number(value: Any) -> float | None:
    if not isinstance(value, int | float | str):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
