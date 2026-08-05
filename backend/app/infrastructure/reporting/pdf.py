"""PDF export.

reportlab rather than an HTML-to-PDF converter. WeasyPrint and wkhtmltopdf
produce prettier output and drag in cairo, pango, and a browser engine
respectively — turning a document export into a system-dependency problem that
surfaces as a container that builds on a laptop and fails in CI. A pure-Python
renderer is the one that still works in two years.

The layout is deliberately plain. A report that a board reads is judged on
whether its numbers are legible and its caveats visible, and every hour spent
on typography here is an hour not spent on whether the numbers are right.
"""

from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.services.reporting.contracts import (
    Block,
    BlockKind,
    ExportFormat,
    Renderer,
    Report,
    Section,
)

BRAND = colors.HexColor("#1F3864")
MUTED = colors.HexColor("#595959")
RULE = colors.HexColor("#D9D9D9")

#: Table columns past this many stop fitting a portrait page legibly.
MAX_COLUMNS = 6
MAX_ROWS = 12


class PdfRenderer(Renderer):
    format = ExportFormat.PDF
    media_type = "application/pdf"
    extension = "pdf"

    def __init__(self) -> None:
        base = getSampleStyleSheet()
        self.styles = {
            "title": ParagraphStyle(
                "title", parent=base["Title"], textColor=BRAND, fontSize=22, spaceAfter=6
            ),
            "subtitle": ParagraphStyle(
                "subtitle", parent=base["Normal"], textColor=MUTED, fontSize=11, spaceAfter=18
            ),
            "heading": ParagraphStyle(
                "heading", parent=base["Heading1"], textColor=BRAND, fontSize=15, spaceBefore=14
            ),
            "sub": ParagraphStyle(
                "sub", parent=base["Normal"], textColor=MUTED, fontSize=9.5, spaceAfter=8
            ),
            "body": ParagraphStyle(
                "body", parent=base["Normal"], fontSize=10, leading=15, alignment=TA_LEFT
            ),
            "bullet": ParagraphStyle(
                "bullet", parent=base["Normal"], fontSize=10, leading=14, leftIndent=10
            ),
            "note": ParagraphStyle(
                "note", parent=base["Normal"], fontSize=8, textColor=MUTED, leading=11
            ),
        }

    def render(self, report: Report) -> bytes:
        buffer = BytesIO()
        document = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            title=report.title,
            author="RetailMind AI",
            leftMargin=18 * mm,
            rightMargin=18 * mm,
            topMargin=18 * mm,
            bottomMargin=18 * mm,
        )

        story: list[Any] = [
            Paragraph(_escape(report.title), self.styles["title"]),
            Paragraph(_escape(report.subtitle), self.styles["subtitle"]),
            Paragraph(
                f"{_escape(report.period_label)} &nbsp;·&nbsp; generated "
                f"{report.generated_at:%d %b %Y %H:%M} UTC",
                self.styles["note"],
            ),
            Spacer(1, 8 * mm),
        ]

        for index, section in enumerate(report.sections):
            if index:
                story.append(Spacer(1, 6 * mm))
            story.extend(self._section(section))

        story.append(PageBreak())
        story.append(Paragraph("How to read this report", self.styles["heading"]))
        for caveat in report.caveats:
            story.append(Paragraph(f"• {_escape(caveat)}", self.styles["bullet"]))
            story.append(Spacer(1, 2 * mm))

        document.build(story)
        return buffer.getvalue()

    # ── Sections ─────────────────────────────────────────────────────

    def _section(self, section: Section) -> list[Any]:
        story: list[Any] = [Paragraph(_escape(section.title), self.styles["heading"])]
        if section.subtitle:
            story.append(Paragraph(_escape(section.subtitle), self.styles["sub"]))

        if section.unavailable_reason:
            story.append(Paragraph(_escape(section.unavailable_reason), self.styles["note"]))
            return story

        for block in section.blocks:
            story.extend(self._block(block))
        return story

    def _block(self, block: Block) -> list[Any]:
        story: list[Any] = []
        if block.title:
            story.append(Paragraph(f"<b>{_escape(block.title)}</b>", self.styles["body"]))
            story.append(Spacer(1, 2 * mm))

        if block.kind is BlockKind.NARRATIVE and block.text:
            story.append(Paragraph(_escape(block.text), self.styles["body"]))
            story.append(Spacer(1, 3 * mm))

        elif block.kind is BlockKind.CALLOUT:
            for bullet in block.bullets:
                story.append(Paragraph(f"• {_escape(bullet)}", self.styles["bullet"]))
                story.append(Spacer(1, 1.5 * mm))
            story.append(Spacer(1, 2 * mm))

        elif block.kind is BlockKind.KPI_GRID and block.kpis:
            story.append(self._kpi_table(block))
            story.append(Spacer(1, 3 * mm))

        elif block.rows:
            story.append(self._table(block))
            story.append(Spacer(1, 3 * mm))

        if block.note:
            story.append(Paragraph(_escape(block.note), self.styles["note"]))
            story.append(Spacer(1, 3 * mm))

        # Keep a block and its note on one page: a caveat orphaned onto the
        # next page is a caveat the reader never connects to its number.
        return [KeepTogether(story)] if story else []

    def _kpi_table(self, block: Block) -> Table:
        data = [["Measure", "Value", "Prior", "Change"]]
        for kpi in block.kpis:
            data.append(
                [
                    kpi.label,
                    kpi.formatted(),
                    f"{kpi.comparison:,.0f}" if kpi.comparison is not None else "—",
                    f"{kpi.change:+.1%}" if kpi.change is not None else "—",
                ]
            )
        table = Table(data, hAlign="LEFT", colWidths=[55 * mm, 35 * mm, 35 * mm, 30 * mm])
        table.setStyle(self._style(len(data)))
        return table

    def _table(self, block: Block) -> Table:
        columns = list(block.columns[:MAX_COLUMNS])
        header = [name.replace("_", " ").title() for name in columns]
        data = [header]

        for record in block.rows[:MAX_ROWS]:
            data.append([_cell(value) for value in record[:MAX_COLUMNS]])

        table = Table(data, hAlign="LEFT", repeatRows=1)
        table.setStyle(self._style(len(data)))
        return table

    def _style(self, rows: int) -> TableStyle:
        return TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), BRAND),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("GRID", (0, 0), (-1, -1), 0.25, RULE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F7FB")]),
            ]
            if rows > 1
            else []
        )


def _cell(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:,.0f}" if abs(value) >= 1000 else f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    return _escape(str(value))[:60]


def _escape(text: str) -> str:
    """Escape reportlab's inline markup.

    reportlab parses a subset of HTML in paragraph text, so an ampersand or an
    angle bracket arriving from a product name would raise mid-render or
    silently swallow the rest of the sentence.
    """
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
