"""The component library.

Grouped by what a component is *for* rather than by what it renders:

* :mod:`primitives` — surfaces, statistics, and the three states every panel
  must be able to reach (empty, loading, failed).
* :mod:`evidence` — confidence, evidence tiers, and the qualifications the API
  attaches to its numbers. The rule the whole library exists to enforce lives
  here: caveats render inline, never behind a click.
* :mod:`cards` — the three objects a reader acts on: proposed actions, alerts,
  and machine-written narrative.
* :mod:`investigation` — decomposing a movement into where it landed and why
  it might have happened, keeping those two categories visually distinct.

Nothing in this package computes a business figure. Every number arrives from
the API already derived and already graded; the library's job is to render
both, and never the first without the second.
"""

from retailmind_ui.components.cards import (
    action_card,
    ai_summary,
    alert_card,
    headline_card,
)
from retailmind_ui.components.evidence import (
    basis_chip,
    caveats,
    checked_and_not,
    confidence_legend,
    confidence_meter,
    disqualifier,
    does_not_establish,
    evidence_panel,
    meter_row,
    provenance,
    statements,
    tier_chip,
)
from retailmind_ui.components.investigation import (
    breadcrumb,
    coverage,
    decision_tree,
    finding_row,
    findings_group,
    timeline,
    window_comparison,
)
from retailmind_ui.components.primitives import (
    analyst_grid,
    bar_column,
    chip,
    divider,
    empty,
    failure,
    frame,
    meter,
    money,
    panel_close,
    panel_open,
    section,
    skeleton,
    stat,
    stat_row,
    table,
    working,
    workspace_header,
)

__all__ = [
    "action_card",
    "ai_summary",
    "alert_card",
    "analyst_grid",
    "bar_column",
    "basis_chip",
    "breadcrumb",
    "caveats",
    "checked_and_not",
    "chip",
    "confidence_legend",
    "confidence_meter",
    "coverage",
    "decision_tree",
    "disqualifier",
    "divider",
    "does_not_establish",
    "empty",
    "evidence_panel",
    "failure",
    "finding_row",
    "findings_group",
    "frame",
    "headline_card",
    "meter",
    "meter_row",
    "money",
    "panel_close",
    "panel_open",
    "provenance",
    "section",
    "skeleton",
    "stat",
    "stat_row",
    "statements",
    "table",
    "tier_chip",
    "timeline",
    "window_comparison",
    "working",
    "workspace_header",
]
