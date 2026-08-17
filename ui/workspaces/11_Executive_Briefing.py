"""Executive Briefing — the morning read, and the document it exports to.

The same composition the report renderers use, drawn on screen first. What is
on this page *is* what lands in the PDF — not an approximation of it — so a
figure quoted in a board pack cannot disagree with the console it came from.

**Sections that could not be built say why.** A briefing missing its inventory
section because the reader's role excludes inventory analytics is a different
document from one missing it because there was nothing to report, and the two
must never look alike.
"""

from typing import Any

import streamlit as st

from retailmind_ui import charts, design, session
from retailmind_ui import components as ui
from retailmind_ui.api import ApiError
from retailmind_ui.design import SEMANTIC

design.configure("Executive Briefing", icon="▤")
client = session.require("reports.read")

ui.workspace_header(
    "Executive Briefing",
    eyebrow="Morning read",
    summary="Summary, KPIs, performance, outlook and actions — as one document.",
)

controls = st.columns([2, 2, 3])
end = controls[0].date_input("Period end", value=session.data_date())
days = controls[1].slider("Period length (days)", 7, 180, 28)
title = controls[2].text_input("Title", value="Retail Performance Review")

try:
    report: dict[str, Any] = client.get(
        "/api/v1/reports", period_end=end.isoformat(), period_days=days, title=title
    )
except ApiError as error:
    ui.workspace_error(error, what="The briefing did not compose")
    st.stop()

st.markdown(f"### {report.get('title', '')}")
st.caption(
    f"{report.get('period_label', '')} · generated {str(report.get('generated_at', ''))[:16]}"
)

for block in report.get("sections") or []:
    ui.section(str(block.get("title", "")), str(block.get("subtitle", "")))

    if not block.get("blocks"):
        ui.empty(
            str(block.get("unavailable_reason") or "This section produced no content."),
            what="Not included",
        )
        continue

    for item in block["blocks"]:
        kind = str(item.get("kind", ""))
        columns = item.get("columns") or []
        rows = item.get("rows") or []

        if kind == "narrative" and item.get("text"):
            ui.ai_summary(str(item["text"]), title=str(item.get("title") or "Summary"))
        elif kind == "callout":
            ui.caveats(
                [str(bullet) for bullet in (item.get("bullets") or [])],
                title=str(item.get("title") or "Note"),
                tone=SEMANTIC["ai"],
            )
        elif kind == "kpi_grid":
            ui.stat_row(
                [
                    {
                        "label": str(kpi.get("label", "")),
                        "value": str(kpi.get("formatted") or kpi.get("value") or "—"),
                        "delta": (
                            f"{float(kpi['change']):+.1%}"
                            if kpi.get("change") is not None
                            else None
                        ),
                        "direction": ("up" if (kpi.get("change") or 0) >= 0 else "down"),
                        "note": str(kpi.get("comparison_label") or ""),
                    }
                    for kpi in (item.get("kpis") or [])
                ]
            )
        elif kind == "chart" and rows:
            records = [dict(zip(columns, row, strict=False)) for row in rows]
            category = str(item.get("chart_category_column") or (columns[0] if columns else ""))
            measures = [str(name) for name in (item.get("chart_value_columns") or [])]
            figure = None
            if measures and "forecast" in measures[0]:
                figure = charts.forecast_band(records, x=category)
            elif str(item.get("chart_kind")) == "line" and measures:
                figure = charts.trend(records, x=category, y=measures[0])
            elif measures:
                figure = charts.ranked_bars(records, label=category, value=measures[0])

            if figure is not None:
                if item.get("title"):
                    st.markdown(f"**{item['title']}**")
                st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
            else:
                ui.table([dict(zip(columns, row, strict=False)) for row in rows])
            if item.get("note"):
                st.caption(str(item["note"]))
        elif rows:
            if item.get("title"):
                st.markdown(f"**{item['title']}**")
            ui.table([dict(zip(columns, row, strict=False)) for row in rows], height=280)
            if item.get("note"):
                st.caption(str(item["note"]))

ui.caveats(report.get("caveats") or [], title="How to read this briefing")

# ── Export ───────────────────────────────────────────────────────────

ui.section(
    "Export",
    "Charts are written as native objects — a workbook can be re-sorted, a deck "
    "restyled. An embedded image cannot be interrogated.",
)

formats = (
    ("pdf", "PDF", "application/pdf"),
    (
        "pptx",
        "PowerPoint",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ),
    ("xlsx", "Excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
)

for slot, (fmt, name, mime) in zip(st.columns(3), formats, strict=True):
    with slot:
        if st.button(f"Build {name}", key=f"build_{fmt}", width="stretch"):
            try:
                payload, content_type = client.download(
                    "/api/v1/reports/export",
                    format=fmt,
                    period_end=end.isoformat(),
                    period_days=days,
                    title=title,
                )
                st.download_button(
                    f"Download {name}",
                    data=payload,
                    file_name=f"retailmind-briefing-{end:%Y%m%d}.{fmt}",
                    mime=content_type or mime,
                    width="stretch",
                    key=f"dl_{fmt}",
                )
            except ApiError as error:
                ui.failure(str(error), what=f"The {name} export failed")

ui.provenance(report.get("meta") or {})
