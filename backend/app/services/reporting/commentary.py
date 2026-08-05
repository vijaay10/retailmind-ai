"""Narrative composed from the report's own content.

Every sentence here is assembled from a figure that already appears elsewhere
in the document. That constraint is the whole design: commentary generated
freely — by a person in a hurry or by a language model — drifts from the data
it describes, asserting a recovery the numbers do not show or a cause the
evidence does not support, and a reader has no way to tell which sentences are
arithmetic and which are atmosphere.

So the generator can only say things it can point at. If root cause analysis
found nothing, the commentary says nothing about causes rather than reaching
for a plausible one. If the forecast is at parity with its baseline, the
commentary says so instead of describing the outlook as though it were
informative.

**On adding a language model here.** The extension point is a rewrite pass
over *these* sentences — improving how they read without changing what they
claim — with the figures injected as fixed facts rather than generated. A
model asked to write the commentary from raw data instead would produce better
prose and occasionally invent a quarter. That trade is not worth taking in a
document somebody forwards to a board, so it is not taken here.
"""

from typing import Any

from app.services.reporting.contracts import Block, BlockKind, Kpi, Section

#: Movement below this is ordinary variation and is described as flat. Calling
#: a 0.4% move a "decline" trains readers to discount the language entirely.
MATERIAL_MOVE = 0.02


def executive_summary(
    *,
    kpis: list[Kpi],
    rca: dict[str, Any],
    recommendations: dict[str, Any],
    forecast: dict[str, Any],
    period_label: str,
) -> Section:
    """The opening page: what happened, why, and what to do about it."""
    headline = _headline(kpis, period_label)
    bullets: list[str] = []

    driver = _leading_driver(rca)
    if driver:
        bullets.append(driver)

    if forecast.get("rows"):
        bullets.append(
            f"The next {len(forecast['rows'])} days are forecast at "
            f"{forecast['total']:,.0f} in net revenue."
        )

    net = recommendations.get("net_profit_opportunity")
    count = recommendations.get("count")
    if net and count:
        bullets.append(
            f"{count} recommended actions carry a combined profit opportunity "
            f"of {net:,.0f} once overlapping actions are counted once."
        )

    if not bullets:
        bullets.append(
            "No driver, forecast, or action cleared its materiality threshold "
            "this period. That is a finding, not a gap."
        )

    return Section(
        key="summary",
        title="Executive Summary",
        subtitle=period_label,
        blocks=(
            Block(kind=BlockKind.NARRATIVE, text=headline),
            Block(kind=BlockKind.CALLOUT, title="In short", bullets=tuple(bullets)),
            Block(
                kind=BlockKind.KPI_GRID,
                kpis=tuple(kpis[:4]),
                note="Full detail follows in the sections below.",
            ),
        ),
    )


def build(
    *,
    kpis: list[Kpi],
    rca: dict[str, Any],
    recommendations: dict[str, Any],
    forecast: dict[str, Any],
) -> Section:
    """The closing commentary."""
    paragraphs: list[str] = []

    revenue = next((kpi for kpi in kpis if kpi.label == "Net Revenue"), None)
    margin_rate = next((kpi for kpi in kpis if kpi.label == "Margin Rate"), None)

    if revenue and revenue.change is not None:
        paragraphs.append(_revenue_paragraph(revenue, margin_rate))

    findings = rca.get("findings") or []
    if findings:
        paragraphs.append(_evidence_paragraph(findings))

    if recommendations.get("recommendations"):
        paragraphs.append(_action_paragraph(recommendations))

    if forecast.get("caveats"):
        paragraphs.append(
            "The outlook should be read with its own caveats attached: "
            + " ".join(forecast["caveats"][:2])
        )

    if not paragraphs:
        paragraphs.append(
            "There is little to say about this period beyond the figures "
            "themselves: nothing moved far enough to explain, and no action "
            "cleared the threshold where it would repay the attention."
        )

    return Section(
        key="commentary",
        title="Commentary",
        blocks=tuple(Block(kind=BlockKind.NARRATIVE, text=paragraph) for paragraph in paragraphs),
    )


# ── Sentence construction ────────────────────────────────────────────


def _headline(kpis: list[Kpi], period_label: str) -> str:
    revenue = next((kpi for kpi in kpis if kpi.label == "Net Revenue"), None)
    if revenue is None:
        return f"No sales were recorded in {period_label}."

    if revenue.change is None:
        return (
            f"Net revenue for {period_label} was {revenue.formatted()}. "
            "No comparable prior period was available."
        )

    direction = _direction(revenue.change)
    return (
        f"Net revenue for {period_label} was {revenue.formatted()}, "
        f"{direction} against the preceding period of equal length."
    )


def _direction(change: float) -> str:
    if abs(change) < MATERIAL_MOVE:
        return f"broadly flat ({change:+.1%})"
    return f"{'up' if change > 0 else 'down'} {abs(change):.1%}"


def _revenue_paragraph(revenue: Kpi, margin_rate: Kpi | None) -> str:
    text = (
        f"Net revenue came to {revenue.formatted()}, "
        f"{_direction(revenue.change or 0.0)} on the prior period."
    )
    if margin_rate is not None and margin_rate.change is not None:
        # Volume and rate moving in opposite directions is the case worth
        # naming: it is invisible in either figure alone.
        if (revenue.change or 0) < 0 <= margin_rate.change:
            text += (
                " Margin rate held or improved while revenue fell, so the "
                "shortfall is a volume problem rather than a pricing one."
            )
        elif (revenue.change or 0) >= 0 > margin_rate.change:
            text += (
                " Revenue held while margin rate slipped, which points at "
                "discount depth or mix rather than demand."
            )
        else:
            text += f" Margin rate was {margin_rate.formatted()}."
    return text


def _evidence_paragraph(findings: list[dict[str, Any]]) -> str:
    leading = findings[0]
    arithmetic = [item for item in findings if item["evidence_tier"] == "arithmetic"]
    weaker = [item for item in findings if item["evidence_tier"] != "arithmetic"]

    text = f"The largest single driver was {leading['subject']}: {leading['headline']}."
    if arithmetic:
        text += (
            f" {len(arithmetic)} finding(s) are exact decompositions — they say "
            "where the change landed, not why it started."
        )
    if weaker:
        tiers = sorted({item["evidence_tier"] for item in weaker})
        text += (
            f" A further {len(weaker)} are {', '.join(tiers)} in nature: "
            "candidate explanations that coincide with the movement, and are "
            "capped in confidence accordingly."
        )
    return text


def _action_paragraph(recommendations: dict[str, Any]) -> str:
    items = recommendations["recommendations"]
    leading = items[0]
    gross = recommendations.get("gross_profit_opportunity", 0.0)
    net = recommendations.get("net_profit_opportunity", 0.0)

    text = (
        f"The highest-ranked action is to {leading['action'][0].lower()}"
        f"{leading['action'][1:]}, owned by {leading['owner'] or 'the relevant team'}."
    )
    if gross and net and gross > net * 1.05:
        text += (
            f" Note the gap between {gross:,.0f} if every recommendation were "
            f"counted separately and {net:,.0f} once overlapping actions are "
            "counted once — several of these chase the same pounds."
        )

    assumed = [item for item in items if item["impact"]["rests_on_unmeasured_assumptions"]]
    if assumed:
        text += (
            f" {len(assumed)} of {len(items)} estimates rest on parameters this "
            "platform has not measured, and carry a sensitivity range rather "
            "than a single figure."
        )
    return text


def _leading_driver(rca: dict[str, Any]) -> str:
    findings = rca.get("findings") or []
    if not findings:
        return ""
    leading = findings[0]
    return (
        f"{leading['headline']} "
        f"({leading['evidence_tier']} evidence, {leading['confidence']:.0%} confidence)."
    )
