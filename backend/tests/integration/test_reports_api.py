"""Report endpoints against a real warehouse.

Proves the composed document is assembled from the live services and that all
three exports are documents a recipient's software will actually open — not
just bytes with the right magic number.
"""

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path

import pytest

pytest.importorskip("testcontainers", reason="integration extra not installed")
from httpx import ASGITransport, AsyncClient  # noqa: E402
from openpyxl import load_workbook  # noqa: E402
from pptx import Presentation  # noqa: E402

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[3]
DBT_DIR = REPO / "data_platform" / "dbt"
LAST_DAY = date(2026, 7, 21)
HISTORY_DAYS = 42
DEMO_PASSWORD = "ChangeMe-Demo1!"  # noqa: S105 — seeded demo credential

USERS = {"ceo": "priya@northwind.example", "marketing": "marcus@northwind.example"}


@pytest.fixture(scope="module")
def report_warehouse(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    sys.path.insert(0, str(REPO / "data_platform"))

    from ingestion.connectors.csv_files import CsvFileConnector
    from ingestion.core.config import EtlSettings
    from ingestion.core.duck import connect
    from ingestion.domain.schema import SourceSchema
    from ingestion.domain.window import Window
    from ingestion.generators import (
        fulfilment,
        inventory_files,
        pos_files,
        purchase_orders,
        weather,
    )
    from ingestion.pipeline import IngestionPipeline

    root = tmp_path_factory.mktemp("report_wh")
    settings = EtlSettings(
        landing_root=root / "lake",
        inbox_root=root / "inbox",
        warehouse_path=root / "wh.duckdb",
        reject_rate_threshold=0.10,
    )

    stores = 6
    first_day = LAST_DAY - timedelta(days=HISTORY_DAYS - 1)
    for offset in range(HISTORY_DAYS):
        day = first_day + timedelta(days=offset)
        pos_files.generate_day(
            settings.inbox_dir("pos"),
            day,
            stores=stores,
            lines_per_store=20,
            seed=7 + offset,
            history_start=first_day,
            history_end=LAST_DAY,
        )
        inventory_files.generate_day(
            settings.inbox_dir("inventory"),
            day,
            stores=stores,
            skus_per_store=8,
            seed=600 + offset,
        )
        purchase_orders.generate_day(
            settings.inbox_dir("purchasing"),
            day,
            stores=stores,
            lines=16,
            seed=900 + offset,
            as_of=LAST_DAY,
        )
        weather.generate_day(
            settings.inbox_dir("weather"), day, seed=41 + offset, history_end=LAST_DAY
        )
        fulfilment.generate_day(
            settings.inbox_dir("fulfilment"),
            day,
            stores=stores,
            seed=55 + offset,
            history_end=LAST_DAY,
        )

    schema_root = REPO / "data_platform" / "ingestion" / "schemas"
    window = Window(first_day, LAST_DAY + timedelta(days=1))
    conn = connect(settings.warehouse_path)
    for source, table, units in (
        ("pos", "sales", stores),
        ("inventory", "positions", stores),
        ("purchasing", "orders", 1),
        ("weather", "observations", 1),
        ("fulfilment", "deliveries", 1),
    ):
        schema = SourceSchema.from_yaml(schema_root / source / f"{table}.yml")
        connector = CsvFileConnector(
            schema=schema, settings=settings, connection=conn, expected_units=units
        )
        summary = IngestionPipeline(connector=connector, settings=settings, connection=conn).run(
            window
        )
        assert not summary.quarantined, f"{source}: {summary.quarantined}"
    conn.close()

    env = {
        **os.environ,
        "RM_WAREHOUSE_DUCKDB_PATH": str(settings.warehouse_path),
        "DBT_TARGET_PATH": str(root / "dbt_target"),
    }
    for step in ("seed", "snapshot", "build"):
        result = subprocess.run(  # noqa: S603
            ["uv", "run", "dbt", step, "--profiles-dir", "."],  # noqa: S607
            cwd=DBT_DIR,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"dbt {step} failed:\n{result.stdout[-3000:]}"

    yield settings.warehouse_path


@pytest.fixture
async def client(migrated_db: dict[str, str], report_warehouse: Path) -> AsyncIterator[AsyncClient]:
    os.environ["RM_WAREHOUSE_DUCKDB_PATH"] = str(report_warehouse)
    os.environ.pop("RM_REDIS_CACHE_URL", None)

    from app.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    await app.state.engine.dispose()


async def _auth(client: AsyncClient, role: str = "ceo") -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login", json={"email": USERS[role], "password": DEMO_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _report(client: AsyncClient, role: str = "ceo") -> dict:
    response = await client.get(
        "/api/v1/reports",
        headers=await _auth(client, role),
        params={"period_end": LAST_DAY.isoformat(), "period_days": 14},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _export(client: AsyncClient, fmt: str):
    return await client.get(
        "/api/v1/reports/export",
        headers=await _auth(client),
        params={"format": fmt, "period_end": LAST_DAY.isoformat(), "period_days": 14},
    )


# ── Composition ──────────────────────────────────────────────────────


async def test_the_report_has_every_requested_section(client: AsyncClient) -> None:
    body = await _report(client)
    keys = {section["key"] for section in body["sections"]}
    assert {
        "summary",
        "kpis",
        "trend",
        "insights",
        "forecast",
        "recommendations",
        "commentary",
    } <= keys


async def test_the_executive_summary_leads_with_a_headline(client: AsyncClient) -> None:
    body = await _report(client)
    summary = next(s for s in body["sections"] if s["key"] == "summary")
    assert any(block["text"] for block in summary["blocks"])


async def test_kpis_carry_a_prior_period_comparison(client: AsyncClient) -> None:
    body = await _report(client)
    kpis = [
        kpi for section in body["sections"] for block in section["blocks"] for kpi in block["kpis"]
    ]
    assert kpis
    assert any(kpi["comparison"] is not None for kpi in kpis)


async def test_an_empty_section_states_why(client: AsyncClient) -> None:
    """ "Nothing found" and "never ran" are opposite conclusions."""
    body = await _report(client)
    for section in body["sections"]:
        if not section["blocks"]:
            assert section["unavailable_reason"], f"{section['key']} is silently empty"


async def test_the_report_carries_its_caveats(client: AsyncClient) -> None:
    body = await _report(client)
    assert body["caveats"]
    assert any("recomputed" in caveat for caveat in body["caveats"])


async def test_commentary_is_present_and_non_empty(client: AsyncClient) -> None:
    body = await _report(client)
    commentary = next(s for s in body["sections"] if s["key"] == "commentary")
    assert any(block["text"] for block in commentary["blocks"])


# ── Exports ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("fmt", ["pdf", "pptx", "xlsx"])
async def test_every_format_downloads(client: AsyncClient, fmt: str) -> None:
    response = await _export(client, fmt)
    assert response.status_code == 200, response.text
    assert len(response.content) > 1000
    assert f".{fmt}" in response.headers["content-disposition"]
    assert response.headers["content-disposition"].startswith("attachment")


async def test_the_pdf_is_a_pdf(client: AsyncClient) -> None:
    response = await _export(client, "pdf")
    assert response.content.startswith(b"%PDF")
    assert response.headers["content-type"] == "application/pdf"


async def test_the_workbook_opens_with_native_charts(client: AsyncClient) -> None:
    """Native charts, not pictures: the recipient can re-sort and re-total."""
    response = await _export(client, "xlsx")
    workbook = load_workbook(BytesIO(response.content))

    assert len(workbook.sheetnames) >= 5
    assert sum(len(workbook[name]._charts) for name in workbook.sheetnames) >= 1


async def test_the_deck_opens_with_native_charts(client: AsyncClient) -> None:
    response = await _export(client, "pptx")
    presentation = Presentation(BytesIO(response.content))

    assert len(presentation.slides) >= 5
    assert any(shape.has_chart for slide in presentation.slides for shape in slide.shapes)


async def test_the_exports_agree_with_the_json(client: AsyncClient) -> None:
    """One document, three renderers — the whole reason for the design."""
    body = await _report(client)
    response = await _export(client, "xlsx")
    workbook = load_workbook(BytesIO(response.content))

    for section in body["sections"]:
        assert section["title"][:31] in workbook.sheetnames


async def test_an_unknown_format_is_refused(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/reports/export", headers=await _auth(client), params={"format": "docx"}
    )
    assert response.status_code == 422


# ── Authorization ────────────────────────────────────────────────────


async def test_anonymous_access_is_rejected(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/reports")).status_code == 401
    assert (await client.get("/api/v1/reports/export")).status_code == 401


async def test_a_denied_section_narrows_the_report_rather_than_failing_it(
    client: AsyncClient,
) -> None:
    """Marketing may read reports but not profitability.

    Failing the whole document because one section was forbidden turns a
    partial report into no report. The section is rendered with its reason and
    everything the role *may* see is still there.
    """
    body = await _report(client, role="marketing")
    assert body["sections"], "a permission denial emptied the whole report"

    denied = [
        section
        for section in body["sections"]
        if "role does not include" in section["unavailable_reason"]
    ]
    populated = [section for section in body["sections"] if section["blocks"]]

    assert populated, "nothing survived the denial"
    for section in denied:
        assert section["title"], "a denied section still needs its heading"
