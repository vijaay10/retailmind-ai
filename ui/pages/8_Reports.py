"""Reports — compose a review and export it."""

import streamlit as st

from retailmind_ui import components as ui
from retailmind_ui import session, theme
from retailmind_ui.api import ApiError

theme.configure("Reports")
client = session.require("reports.read")

ui.page_header(
    "Reports",
    "One document, three formats. The preview below is the same structure the exports render.",
)

controls = st.columns([2, 2, 3])
end = controls[0].date_input("Period end", value=session.data_date())
days = controls[1].slider("Period length (days)", 7, 180, 28)
title = controls[2].text_input("Title", value="Retail Performance Review")

try:
    report = client.get(
        "/api/v1/reports",
        period_end=end.isoformat(),
        period_days=days,
        title=title,
    )
except ApiError as error:
    ui.error(str(error))
    st.stop()

st.markdown(f"### {report.get('title', '')}")
st.caption(f"{report.get('period_label', '')} · generated {report.get('generated_at', '')[:16]}")

for block in report.get("sections") or []:
    ui.section(block.get("title", ""), block.get("subtitle", ""))

    if not block.get("blocks"):
        # An omitted section and an empty one mean opposite things.
        ui.empty(block.get("unavailable_reason", ""), what="Not included")
        continue

    for item in block["blocks"]:
        kind = item.get("kind")
        if item.get("title"):
            st.markdown(f"**{item['title']}**")

        if kind == "narrative" and item.get("text"):
            st.write(item["text"])
        elif kind == "callout":
            for bullet in item.get("bullets") or []:
                st.caption(f"· {bullet}")
        elif kind == "kpi_grid":
            ui.kpi_row(ui.kpis_from_api(item.get("kpis") or []))
        elif kind == "chart" and item.get("rows"):
            columns = item.get("columns") or []
            rows = [dict(zip(columns, row, strict=False)) for row in item["rows"]]
            ui.chart(
                rows,
                x=item.get("chart_category_column") or (columns[0] if columns else ""),
                y=item.get("chart_value_columns") or [],
                kind=item.get("chart_kind") or "bar",
                rationale=item.get("note", ""),
            )
        elif item.get("rows"):
            columns = item.get("columns") or []
            ui.table([dict(zip(columns, row, strict=False)) for row in item["rows"]])

        if item.get("note") and kind != "chart":
            st.caption(item["note"])

ui.caveats(report.get("caveats") or [], title="How to read this report")

ui.section("Export")
st.caption(
    "Charts are written as native objects — a workbook can be re-sorted and a "
    "deck restyled. An embedded image cannot be interrogated."
)

for slot, (fmt, mime, name) in zip(
    st.columns(3),
    (
        ("pdf", "application/pdf", "PDF"),
        (
            "pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "PowerPoint",
        ),
        ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "Excel"),
    ),
    strict=True,
):
    with slot:
        if st.button(f"Build {name}", width="stretch", key=f"build_{fmt}"):
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
                    file_name=f"retailmind-report-{end:%Y%m%d}.{fmt}",
                    mime=content_type or mime,
                    width="stretch",
                    key=f"dl_{fmt}",
                )
            except ApiError as error:
                ui.error(str(error))
