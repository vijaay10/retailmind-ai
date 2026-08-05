"""Render a report to all three formats against the local demo warehouse.

An operator entrypoint, not a test. It exists so the export path can be
exercised end to end without standing up the API — which is how a rendering
regression gets noticed before somebody downloads a broken deck.
"""

import argparse
import asyncio
import logging
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

import structlog  # noqa: E402

structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))

from app.domain.auth.entities import Principal  # noqa: E402
from app.domain.auth.permissions import Permission  # noqa: E402
from app.infrastructure.reporting import renderer_for  # noqa: E402
from app.infrastructure.semantic.client import SemanticLayerClient  # noqa: E402
from app.infrastructure.semantic.repository import AnalyticsRepository  # noqa: E402
from app.services.analytics.service import AnalyticsService  # noqa: E402
from app.services.forecasting.service import ForecastingService  # noqa: E402
from app.services.rca.service import RootCauseService  # noqa: E402
from app.services.recommendations.service import RecommendationService  # noqa: E402
from app.services.reporting.composer import ReportComposer, ReportRequest  # noqa: E402
from app.services.reporting.contracts import ExportFormat  # noqa: E402

DEFAULT_WAREHOUSE = REPO / ".local" / "retailmind.duckdb"
DEFAULT_PERIOD_END = date(2026, 7, 21)


class _NoCache:
    """The analytics cache port, satisfied without Redis."""

    def key(self, **_: object) -> str:
        return "render-report"

    async def get(self, _: str) -> None:
        return None

    async def set(self, _: str, __: object) -> None:
        return None


def main(destination: str = "build", warehouse: Path = DEFAULT_WAREHOUSE) -> None:
    if not warehouse.exists():
        raise SystemExit(
            f"no warehouse at {warehouse}. Run `make etl-demo && make warehouse` first."
        )

    analytics = AnalyticsService(
        AnalyticsRepository(SemanticLayerClient(str(warehouse)), _NoCache())  # type: ignore[arg-type]
    )
    composer = ReportComposer(
        analytics,
        rca=RootCauseService(analytics),
        forecasts=ForecastingService(analytics),
        recommendations=RecommendationService(analytics),
    )
    principal = Principal(
        user_id="cli",
        tenant_id="demo",
        email="cli@retailmind.local",
        roles=("ceo",),
        token_version=1,
        permissions=frozenset(Permission),
    )

    report = asyncio.run(composer.compose(principal, ReportRequest(period_end=DEFAULT_PERIOD_END)))

    out = Path(destination)
    out.mkdir(parents=True, exist_ok=True)
    print(f"{report.title} — {report.period_label}")
    for section in report.sections:
        state = f"{len(section.blocks)} block(s)" if section.blocks else "empty"
        print(f"  {section.title:<28} {state}")

    print()
    for export_format in ExportFormat:
        renderer = renderer_for(export_format)
        path = out / f"retailmind-report.{renderer.extension}"
        path.write_bytes(renderer.render(report))
        print(f"  {export_format.value:<5} {path.stat().st_size:>9,} bytes  {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", nargs="?", default="build")
    parser.add_argument("--warehouse", type=Path, default=DEFAULT_WAREHOUSE)
    arguments = parser.parse_args()
    main(arguments.destination, arguments.warehouse)
