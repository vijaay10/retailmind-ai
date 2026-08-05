"""Report renderers, one per export format.

Every renderer reads the same :class:`Report` and none of them queries a
service. That is what stops a PDF and a workbook of the same report
disagreeing — and they would, quickly and invisibly, if each fetched its own
numbers. A recipient who spots the difference cannot tell which is right, and
from then on neither is trusted.
"""

from app.infrastructure.reporting.pdf import PdfRenderer
from app.infrastructure.reporting.pptx import PptxRenderer
from app.infrastructure.reporting.xlsx import XlsxRenderer
from app.services.reporting.contracts import ExportFormat, Renderer

RENDERERS: dict[ExportFormat, Renderer] = {
    ExportFormat.PDF: PdfRenderer(),
    ExportFormat.PPTX: PptxRenderer(),
    ExportFormat.XLSX: XlsxRenderer(),
}


def renderer_for(export_format: ExportFormat) -> Renderer:
    renderer = RENDERERS.get(export_format)
    if renderer is None:  # pragma: no cover - the enum is closed
        raise ValueError(f"no renderer for {export_format}")
    return renderer


__all__ = ["RENDERERS", "PdfRenderer", "PptxRenderer", "XlsxRenderer", "renderer_for"]
