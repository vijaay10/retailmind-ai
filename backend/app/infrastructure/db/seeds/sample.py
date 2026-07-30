"""Sample data seed — the Northwind Threads demo tenant (dev/demo/staging ONLY).

Mirrors the Database design §29 sample records so every layer of the demo tells
one coherent story: the SW Outerwear stockout anomaly, its RCA, and the reorder
recommendation it produced.

Idempotency: everything keys off the tenant slug — re-running finds the tenant
and stops. Refusal to run in prod is enforced, not advised.

Run with:  ``python -m app.infrastructure.db.seeds.sample``
"""

import asyncio
import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

import structlog
from argon2 import PasswordHasher
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.infrastructure.db.models import (
    Alert,
    AlertEvent,
    AlertRule,
    AppUser,
    ConnectorConfig,
    DataSnapshot,
    DqResult,
    Insight,
    MetricConfig,
    Notification,
    PipelineRun,
    RcaResult,
    Recommendation,
    Role,
    Tenant,
    UserRole,
)
from app.infrastructure.db.session import create_engine, create_session_factory, session_scope

log = structlog.get_logger(__name__)

TENANT_SLUG = "northwind-threads"
DEMO_PASSWORD = "ChangeMe-Demo1!"  # noqa: S105 — demo tenant only; rotated by `make demo` banner
SNAPSHOT_ID = "snap_2026-07-21"

# Registry keys the demo tracks (subset of the Analytics doc catalog).
METRICS: list[dict[str, Any]] = [
    {"metric_key": "net_revenue", "display_name": "Net Revenue", "sensitivity": "high"},
    {"metric_key": "units_sold", "display_name": "Units Sold", "sensitivity": "med"},
    {"metric_key": "margin_pct", "display_name": "Gross Margin %", "sensitivity": "med"},
    {"metric_key": "aov", "display_name": "Average Order Value", "sensitivity": "low"},
    {"metric_key": "stockout_rate_pct", "display_name": "Stockout Rate", "sensitivity": "high"},
]

USERS: list[dict[str, Any]] = [
    {"email": "sam@northwind.example", "display_name": "Sam Okafor", "role_key": "admin"},
    {"email": "marcus@northwind.example", "display_name": "Marcus Webb", "role_key": "analyst"},
    {"email": "aisha@northwind.example", "display_name": "Aisha Rahman", "role_key": "analyst"},
    {"email": "priya@northwind.example", "display_name": "Priya Sharma", "role_key": "viewer"},
]


def _series_key(metric: str, **dims: str) -> dict[str, str]:
    return {"metric": metric, **dims}


def _dedup_key(subject: dict[str, Any]) -> str:
    canonical = json.dumps(subject, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def seed_sample(session: AsyncSession) -> None:
    existing = await session.scalar(select(Tenant).where(Tenant.slug == TENANT_SLUG))
    if existing is not None:
        log.info("seed.sample.skipped", reason="tenant already present", slug=TENANT_SLUG)
        return

    now = datetime.now(tz=UTC)
    detected = now.replace(hour=5, minute=52, second=0, microsecond=0)

    tenant = Tenant(slug=TENANT_SLUG, name="Northwind Threads", base_currency="USD")
    session.add(tenant)
    await session.flush()  # need tenant.id for children

    # ── Users + roles ────────────────────────────────────────────────
    hasher = PasswordHasher()
    roles = {r.key: r for r in (await session.scalars(select(Role))).all()}
    for spec in USERS:
        user = AppUser(
            tenant_id=tenant.id,
            email=spec["email"],
            display_name=spec["display_name"],
            password_hash=hasher.hash(DEMO_PASSWORD),
        )
        session.add(user)
        await session.flush()
        session.add(UserRole(user_id=user.id, role_id=roles[spec["role_key"]].id))
    marcus = await session.scalar(
        select(AppUser).where(AppUser.email == "marcus@northwind.example")
    )
    assert marcus is not None  # just created above

    # ── Metric configs + one detector rule each ──────────────────────
    rules_by_metric: dict[str, AlertRule] = {}
    for spec in METRICS:
        config = MetricConfig(tenant_id=tenant.id, **spec)
        session.add(config)
        await session.flush()
        rule = AlertRule(
            metric_config_id=config.id,
            detector="stl_resid",
            params={"z_threshold": 3.0, "seasonal_periods": [7, 365]},
        )
        session.add(rule)
        rules_by_metric[str(spec["metric_key"])] = rule
    await session.flush()

    # ── Snapshot pin (everything below references it) ────────────────
    session.add(
        DataSnapshot(
            id=SNAPSHOT_ID,
            tenant_id=tenant.id,
            dag_run_id="transform_daily__2026-07-21",
            manifest_digest="d41d8cd98f00b204demo",
            mart_row_counts={"mart_kpi_daily": 730, "mart_sales_daily": 96_000},
            published_at=detected - timedelta(minutes=11),
        )
    )
    # No ORM relationship links snapshot→alert, so flush explicitly before
    # anything that carries the FK — insert ordering is ours to guarantee here.
    await session.flush()

    # ── The MVP demo anomaly (DB §29 sample records) ────────────────
    alert = Alert(
        tenant_id=tenant.id,
        rule_id=rules_by_metric["net_revenue"].id,
        series_key=_series_key("net_revenue", region="SW"),
        observed=2_910_400,
        expected_low=3_280_000,
        expected_high=3_460_000,
        severity="critical",
        status="acked",
        detector_scores={"stl_resid": {"z": 3.4}, "iforest": {"score": 0.81}},
        narration=(
            "Net revenue in Southwest came in 12.4% below its expected range ($3.28–3.46M) for W30."
        ),
        detected_at=detected,
        acked_at=detected + timedelta(hours=3, minutes=13),
        acked_by=marcus.id,
        data_snapshot_id=SNAPSHOT_ID,
    )
    session.add(alert)
    await session.flush()
    session.add_all(
        [
            AlertEvent(alert_id=alert.id, action="opened", at=detected),
            AlertEvent(
                alert_id=alert.id,
                action="acked",
                actor_id=marcus.id,
                note="Confirmed — stockout event, RCA attached. Posting to channel.",
                at=alert.acked_at or detected,
            ),
        ]
    )

    rca = RcaResult(
        tenant_id=tenant.id,
        alert_id=alert.id,
        metric_key="net_revenue",
        period_a=Range(date(2026, 7, 6), date(2026, 7, 13)),
        period_b=Range(date(2026, 7, 13), date(2026, 7, 20)),
        decomposition={
            "schema_version": 1,
            "total_delta_usd": -684_000,
            "components": {"volume": -598_000, "rate": -41_000, "mix": -45_000},
        },
        top_contributors={
            "schema_version": 1,
            "ranked": [
                {"segment": {"region": "SW"}, "delta_usd": -412_000, "share": 0.60},
                {
                    "segment": {"region": "SW", "category": "Outerwear"},
                    "delta_usd": -389_000,
                    "share": 0.57,
                },
                {"segment": {"region": "NE"}, "delta_usd": -88_000, "share": 0.13},
            ],
        },
        driver_correlations=[
            {
                "driver": "stockout_event",
                "detail": "8 SKUs, SW DCs, from 2026-07-14",
                "relation": "associated",
            }
        ],
        narrative=(
            "Net revenue fell 12.4% (−$684K) W29→W30; Southwest Outerwear explains 60% "
            "of it. The decline is volume, not price: a stockout event on 8 core SKUs "
            "beginning Jul 14 coincides with the drop. No price or promotion changes "
            "were found in the window."
        ),
        compiled_queries={"query_ids": ["q_7f3a", "q_8b1f"]},
        confidence="high",
        confidence_rubric={"concentration": 0.60, "significance_survivals": 5, "freshness": "ok"},
        prompt_version="rca_narrate_v3",
        model_id="claude-sonnet-5",
        runtime_ms=9_140,
        data_snapshot_id=SNAPSHOT_ID,
    )
    session.add(rca)
    await session.flush()  # ids are DB-generated; feed cards below reference rca.id

    # ── The reorder recommendation it produced (DB §29) ─────────────
    subject = {
        "sku": "OW-1042",
        "store_id": "S2117",
        "suggested_qty": 36,
        "order_by_date": "2026-08-02",
    }
    session.add(
        Recommendation(
            tenant_id=tenant.id,
            type="reorder",
            subject=subject,
            dedup_key=_dedup_key(subject),
            expected_impact={
                "metric": "protected_revenue_usd",
                "value_usd": 310_000,
                "method": "newsvendor@95pct_service",
                "confidence": "high",
            },
            rationale=(
                "Projected cover is 4 days against a 10-day supplier lead time; stockout "
                "probability 71% before next receipt. Quantity is the order-up-to level "
                "at 95% service from forecast mr_2026w28 (backtest WAPE 14%)."
            ),
            rule_id="reorder_cover_below_leadtime",
            rule_version="1.0",
            model_run_id="mr_2026w28_lgbm_v7",
            score=310_000 * 0.9,
            confidence="high",
            evidence={"query_ids": ["q_8b1f"], "snapshot": SNAPSHOT_ID},
            expires_at=now + timedelta(days=5),
            data_snapshot_id=SNAPSHOT_ID,
        )
    )

    # ── Feed cards + a delivered notification ───────────────────────
    session.add_all(
        [
            Insight(
                tenant_id=tenant.id,
                kind="alert",
                resource_type="alert",
                resource_id=alert.id,
                headline="Net revenue in Southwest fell 12.4% below expected — cause attached.",
                severity="critical",
                salience_score=684_000,
                ai_generated=True,
                occurred_at=detected,
            ),
            Insight(
                tenant_id=tenant.id,
                kind="rca",
                resource_type="rca_result",
                resource_id=rca.id,
                headline="SW Outerwear stockout explains 60% of the revenue decline.",
                severity="critical",
                salience_score=684_000,
                ai_generated=True,
                occurred_at=detected + timedelta(minutes=9),
            ),
        ]
    )
    session.add(
        Notification(
            tenant_id=tenant.id,
            user_id=marcus.id,
            channel="email",
            event_type="alert.critical",
            payload={"alert_id": str(alert.id), "deep_link": f"/alerts/{alert.id}"},
            severity="critical",
            delivery_status="sent",
            delivery_attempts=[{"at": detected.isoformat(), "outcome": "sent"}],
        )
    )

    # ── Pipeline context: a healthy run + its DQ results ────────────
    connector = ConnectorConfig(
        tenant_id=tenant.id,
        source_key="pos",
        display_name="POS Transactions",
        connector_class="ingestion.connectors.pos_files.PosFileConnector",
        schedule="0 2 * * *",
        params={"completeness_threshold": 0.98},
    )
    session.add(connector)
    await session.flush()
    run = PipelineRun(
        tenant_id=tenant.id,
        connector_id=connector.id,
        dag_run_id="ingest_daily__2026-07-21",
        window=Range(date(2026, 7, 21), date(2026, 7, 22)),
        rows_read=9_814_223,
        rows_rejected=412,
        rows_written=9_813_811,
        watermark_before="2026-07-20",
        watermark_after="2026-07-21",
        status="succeeded",
        started_at=detected - timedelta(hours=3, minutes=52),
        ended_at=detected - timedelta(hours=3, minutes=12),
    )
    session.add(run)
    await session.flush()
    session.add_all(
        [
            DqResult(
                pipeline_run_id=run.id,
                suite="pos_sales_boundary",
                rule_id="QR-VOL-002",
                expectation="row count within weekday-adaptive band",
                passed=True,
                blocking=True,
                observed={"rows": 9_814_223, "band": [9_100_000, 10_400_000]},
            ),
            DqResult(
                pipeline_run_id=run.id,
                suite="pos_sales_boundary",
                rule_id="QR-RNG-011",
                expectation="unit_price in (0, category cap]",
                passed=True,
                blocking=False,
                observed={"reject_rate": 0.000042},
            ),
        ]
    )

    log.info(
        "seed.sample.done",
        tenant=TENANT_SLUG,
        users=len(USERS),
        metrics=len(METRICS),
        note="all demo users share the module-level demo credential — dev only",
    )


async def main() -> None:
    if Settings().env == "prod":
        raise SystemExit("sample seed refuses to run in prod (DB §28 environment matrix)")
    engine = create_engine()
    factory = create_session_factory(engine)
    async with session_scope(factory) as session:
        await seed_sample(session)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
