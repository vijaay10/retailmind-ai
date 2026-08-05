"""Report endpoints (Analytics §12).

Composes the platform's own surfaces into a document and renders it to PDF,
PowerPoint, or Excel.

**One document, three renderers.** The JSON returned by `/reports` is the same
structure every export renders, so a preview in the browser and a downloaded
deck cannot disagree. Letting each format fetch its own numbers is how a PDF
and a spreadsheet of "the same" report end up differing by a rounding rule
nobody can find — and once a recipient spots that, neither is trusted again.

**Charts are native, not pictures.** The Excel export writes real chart objects
anchored to real cell ranges, and the PowerPoint export writes real chart
shapes. A recipient can re-sort the workbook or restyle the deck. An embedded
image cannot be interrogated, so it gets rebuilt by hand — and the hand-built
version is the one that disagrees with this report.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, Response

from app.api.deps import PrincipalDep, ReportServiceDep
from app.infrastructure.reporting import renderer_for
from app.schemas.reports import ReportResponse
from app.services.reporting.composer import ReportRequest
from app.services.reporting.contracts import ExportFormat

router = APIRouter(prefix="/reports", tags=["reports"])

_FORBIDDEN = {
    "description": "Requires report access.",
    "content": {
        "application/problem+json": {
            "example": {
                "type": "https://retailmind.ai/errors/forbidden",
                "title": "Permission denied",
                "status": 403,
                "detail": "You do not have permission to perform this action.",
                "hint": "Requires the 'reports.read' permission.",
            }
        }
    },
}

_PERIOD = Query(ge=7, le=180, description="Length of the reporting period, in days.")


@router.get(
    "",
    response_model=ReportResponse,
    summary="Compose a report",
    responses={403: _FORBIDDEN},
)
async def report(
    principal: PrincipalDep,
    service: ReportServiceDep,
    period_end: Annotated[date | None, Query(description="Last day of the period.")] = None,
    period_days: Annotated[int, _PERIOD] = 28,
    title: Annotated[str, Query(max_length=120)] = "Retail Performance Review",
) -> ReportResponse:
    """The report as structured JSON — the same document the exports render.

    Sections cover the executive summary, KPIs against the prior period,
    performance charts, ranked business insights, the forecast outlook,
    recommended actions, and closing commentary.

    **Nothing here is recomputed for the report.** Every figure comes from the
    surface that owns it — the analytics registry for KPIs, root cause analysis
    for insights, the forecast service for the outlook — and arrives with that
    surface's caveats attached. A number in a slide is the number the dashboard
    shows, structurally rather than by convention.

    **Commentary is composed from the report's own content**, not generated as
    free prose. Every sentence is assembled from a figure that appears
    elsewhere in the document, so it cannot assert a recovery the numbers do
    not show. Where nothing cleared a materiality threshold, the commentary
    says so instead of reaching for a plausible story.

    **Empty sections are rendered with their reason.** "No recommendations
    because the business is healthy" and "no recommendations because the engine
    failed" look identical if the section is simply dropped, and they are
    opposite conclusions.
    """
    return ReportResponse(
        **(
            await service.compose(
                principal,
                ReportRequest(
                    period_end=period_end or date.today(),
                    period_days=period_days,
                    title=title,
                ),
            )
        ).as_dict()
    )


@router.get(
    "/export",
    summary="Download the report as PDF, PowerPoint, or Excel",
    responses={403: _FORBIDDEN},
    response_class=Response,
)
async def export(
    principal: PrincipalDep,
    service: ReportServiceDep,
    export_format: Annotated[
        ExportFormat, Query(alias="format", description="pdf | pptx | xlsx")
    ] = ExportFormat.PDF,
    period_end: Annotated[date | None, Query(description="Last day of the period.")] = None,
    period_days: Annotated[int, _PERIOD] = 28,
    title: Annotated[str, Query(max_length=120)] = "Retail Performance Review",
) -> Response:
    """Render the report to a document and return the bytes.

    Each format is built for how it actually gets used:

    * **PDF** — the version that gets read and filed. Plain layout, legible
      tables, and every caveat printed with the number it qualifies rather
      than gathered into a footnote nobody reaches.
    * **PowerPoint** — the version that gets forwarded and re-presented. One
      section per slide, content capped to what is readable from the back of a
      room, and charts as native shapes so a recipient can restyle rather than
      rebuild them.
    * **Excel** — the version that gets worked in. Typed cells in real
      worksheet ranges with native charts anchored to them, so the numbers can
      be re-sorted, pivoted, and totalled. Values are written as numbers: a
      workbook of numeric-looking text is one nobody can sum, and the person
      who finds out is the one who needed the total.

    Generation is synchronous. For very long periods or scheduled delivery,
    the composition step is deliberately separable so a worker can call it and
    write the result to object storage — the composer takes no request context
    and renders nothing itself.
    """
    document = await service.compose(
        principal,
        ReportRequest(period_end=period_end or date.today(), period_days=period_days, title=title),
    )
    renderer = renderer_for(export_format)
    payload = renderer.render(document)

    filename = f"retailmind-report-{document.period_end:%Y%m%d}.{renderer.extension}"
    return Response(
        content=payload,
        media_type=renderer.media_type,
        headers={
            # `attachment` rather than `inline`: a document rendered in the
            # browser from an API origin is a content-injection surface, and
            # nobody previews a spreadsheet anyway.
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(payload)),
            "X-Content-Type-Options": "nosniff",
        },
    )
