"""The report document model.

**One structured document, three renderers.** Every export format reads this
same object; none of them queries a service. That is the property that keeps a
PDF and a spreadsheet of the same report from disagreeing — and they will
disagree, quickly and invisibly, if each renderer is allowed to fetch its own
numbers. A recipient who spots the difference has no way to tell which is
right, and from then on neither is trusted.

The model is also deliberately renderer-agnostic. There is no page size here,
no font, no slide layout: a block says *what it is* and the renderer decides
how that looks in its medium. A table is a table whether it becomes a
reportlab flowable, a PowerPoint shape, or a worksheet range.

Blocks carry provenance. Every number in a report came from a service that
already attached its own caveats — a forecast with its accuracy record, a
recommendation with its estimate basis — and dropping those on the way into a
document would strip exactly the context that makes the number safe to act on.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any


class ExportFormat(StrEnum):
    PDF = "pdf"
    PPTX = "pptx"
    XLSX = "xlsx"


class BlockKind(StrEnum):
    NARRATIVE = "narrative"
    KPI_GRID = "kpi_grid"
    TABLE = "table"
    CHART = "chart"
    CALLOUT = "callout"


class ChartKind(StrEnum):
    """Chart shapes every renderer can produce natively.

    Kept small on purpose. A shape supported by only one format would render
    in the PowerPoint and vanish from the PDF, and a section that is present
    in one export and missing from another is worse than one that is plainer
    everywhere.
    """

    LINE = "line"
    BAR = "bar"
    HORIZONTAL_BAR = "horizontal_bar"


@dataclass(frozen=True, slots=True)
class Kpi:
    """A headline figure with its comparison."""

    label: str
    value: float
    unit: str = ""
    comparison: float | None = None
    comparison_label: str = "vs prior period"

    @property
    def change(self) -> float | None:
        if self.comparison is None or self.comparison == 0:
            return None
        return (self.value - self.comparison) / abs(self.comparison)

    def formatted(self) -> str:
        if self.unit == "currency":
            return f"{self.value:,.0f}"
        if self.unit == "rate":
            return f"{self.value:.1%}"
        if self.unit == "count":
            return f"{self.value:,.0f}"
        return f"{self.value:,.2f}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "value": round(self.value, 4),
            "formatted": self.formatted(),
            "unit": self.unit,
            "comparison": round(self.comparison, 4) if self.comparison is not None else None,
            "comparison_label": self.comparison_label,
            "change": round(self.change, 4) if self.change is not None else None,
        }


@dataclass(frozen=True, slots=True)
class Block:
    """One piece of a report section."""

    kind: BlockKind
    title: str = ""
    text: str = ""
    bullets: tuple[str, ...] = ()
    kpis: tuple[Kpi, ...] = ()
    columns: tuple[str, ...] = ()
    rows: tuple[tuple[Any, ...], ...] = ()
    chart_kind: ChartKind | None = None
    chart_category_column: str = ""
    chart_value_columns: tuple[str, ...] = ()
    note: str = ""
    """Provenance or caveat travelling with this block. Rendered beneath it in
    every format, because a number lifted out of the service that qualified it
    is a number somebody will act on unqualified."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "title": self.title,
            "text": self.text,
            "bullets": list(self.bullets),
            "kpis": [item.as_dict() for item in self.kpis],
            "columns": list(self.columns),
            "rows": [list(row) for row in self.rows],
            "chart_kind": self.chart_kind.value if self.chart_kind else None,
            "chart_category_column": self.chart_category_column,
            "chart_value_columns": list(self.chart_value_columns),
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class Section:
    """A titled group of blocks — a page, a slide, or a worksheet."""

    key: str
    title: str
    blocks: tuple[Block, ...] = ()
    subtitle: str = ""
    unavailable_reason: str = ""
    """Why this section is empty, when it is.

    An omitted section and a section that found nothing look identical to a
    reader, and the two mean opposite things: one is a gap in the platform,
    the other is good news. Empty sections are rendered with their reason
    rather than dropped.
    """

    @property
    def is_empty(self) -> bool:
        return not self.blocks

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "subtitle": self.subtitle,
            "blocks": [block.as_dict() for block in self.blocks],
            "unavailable_reason": self.unavailable_reason,
        }


@dataclass(frozen=True, slots=True)
class Report:
    """A complete report, ready for any renderer."""

    title: str
    subtitle: str
    period_start: date
    period_end: date
    generated_at: datetime
    sections: tuple[Section, ...] = ()
    caveats: tuple[str, ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def period_label(self) -> str:
        return f"{self.period_start:%d %b %Y} – {self.period_end:%d %b %Y}"

    def section(self, key: str) -> Section | None:
        return next((item for item in self.sections if item.key == key), None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "subtitle": self.subtitle,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "period_label": self.period_label,
            "generated_at": self.generated_at.isoformat(),
            "sections": [section.as_dict() for section in self.sections],
            "caveats": list(self.caveats),
            "meta": self.meta,
        }


class Renderer:
    """What every export format implements."""

    format: ExportFormat
    media_type: str
    extension: str

    def render(self, report: Report) -> bytes:  # pragma: no cover - interface
        raise NotImplementedError
