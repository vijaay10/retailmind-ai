"""Suppression, detection thresholds, and email rendering.

The suppression tests carry the weight. Detection is comparatively easy; what
determines whether an alerting system is read or filtered is the discipline
about *not* sending things, and every rule here exists because of a specific
way these systems fail in production.
"""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.infrastructure.notifications.email import (
    Message,
    NullEmailSender,
    RecordingEmailSender,
    render,
)
from app.services.notifications import detectors, suppression
from app.services.notifications.contracts import (
    AlertCandidate,
    AlertKind,
    Severity,
    event_type_for,
)
from app.services.notifications.suppression import SuppressionState

NOW = datetime(2026, 7, 21, 9, 0, tzinfo=UTC)
TODAY = date(2026, 7, 21)


def candidate(
    *,
    kind: AlertKind = AlertKind.LOW_INVENTORY,
    subject: str = "AC-1010@S2001",
    severity: Severity = Severity.WARN,
    observed: float = 3.0,
) -> AlertCandidate:
    return AlertCandidate(
        kind=kind,
        subject=subject,
        title="t",
        body="b",
        severity=severity,
        observed=observed,
        detected_for=TODAY,
    )


# ── Identity ─────────────────────────────────────────────────────────


def test_the_same_condition_has_the_same_fingerprint() -> None:
    """Two sweeps over an unchanged world must agree, or nothing dedupes."""
    assert candidate().fingerprint == candidate().fingerprint


def test_a_worsening_value_is_still_the_same_alert() -> None:
    """A stockout deepening from 3 units to 2 is not a new stockout.

    Including the observed value would make every re-detection look novel,
    which is exactly how an alerting system starts shouting.
    """
    assert candidate(observed=3.0).fingerprint == candidate(observed=2.0).fingerprint


def test_escalation_is_a_new_identity() -> None:
    """Warn becoming critical is the one change worth interrupting for."""
    warn = candidate(severity=Severity.WARN)
    critical = candidate(severity=Severity.CRITICAL)
    assert warn.fingerprint != critical.fingerprint


def test_different_subjects_are_different_alerts() -> None:
    assert candidate(subject="A").fingerprint != candidate(subject="B").fingerprint


# ── The re-notification window ───────────────────────────────────────


def test_a_first_sighting_is_sent() -> None:
    decision = suppression.decide(candidate(), SuppressionState({}), now=NOW)
    assert decision.send


def test_an_unchanged_condition_is_not_resent() -> None:
    """Without this, an hourly sweep sends the same stockout 24 times a day."""
    item = candidate()
    state = SuppressionState({item.fingerprint: NOW - timedelta(hours=1)})

    decision = suppression.decide(item, state, now=NOW)
    assert not decision.send
    assert "quiet for another" in decision.reason


def test_the_window_reopens_once_it_has_elapsed() -> None:
    item = candidate(severity=Severity.WARN)
    state = SuppressionState({item.fingerprint: NOW - timedelta(hours=13)})
    assert suppression.decide(item, state, now=NOW).send


def test_critical_alerts_reopen_sooner_than_informational_ones() -> None:
    """The cost of a delayed info alert is small; the cost of a noisy one is
    that nobody reads any of them."""
    assert (
        suppression.RENOTIFY_WINDOW[Severity.CRITICAL]
        < suppression.RENOTIFY_WINDOW[Severity.WARN]
        < suppression.RENOTIFY_WINDOW[Severity.INFO]
    )


def test_an_escalation_bypasses_the_window() -> None:
    """The fingerprint includes severity precisely so this works."""
    warn = candidate(severity=Severity.WARN)
    state = SuppressionState({warn.fingerprint: NOW - timedelta(minutes=5)})

    assert not suppression.decide(warn, state, now=NOW).send
    assert suppression.decide(candidate(severity=Severity.CRITICAL), state, now=NOW).send


# ── Mutes ────────────────────────────────────────────────────────────


def test_a_muted_subject_is_never_sent() -> None:
    """Overriding a mute teaches people the mute button does not work."""
    state = SuppressionState({}, muted_subjects=frozenset({"AC-1010@S2001"}))
    assert not suppression.decide(candidate(), state, now=NOW).send


def test_a_muted_kind_is_never_sent() -> None:
    state = SuppressionState({}, muted_kinds=frozenset({AlertKind.LOW_INVENTORY}))
    assert not suppression.decide(candidate(), state, now=NOW).send


def test_a_mute_outranks_an_escalation() -> None:
    state = SuppressionState({}, muted_subjects=frozenset({"AC-1010@S2001"}))
    assert not suppression.decide(candidate(severity=Severity.CRITICAL), state, now=NOW).send


# ── Volume caps ──────────────────────────────────────────────────────


def test_a_flood_of_one_kind_is_capped() -> None:
    """A supplier outage can stock out hundreds of lines at once.

    Four hundred alerts is not four hundred times the information; it is one
    piece of information rendered unreadable.
    """
    flood = [candidate(subject=f"SKU-{n}") for n in range(50)]
    sending, withheld = suppression.apply(flood, SuppressionState({}), now=NOW)

    assert len(sending) == suppression.MAX_PER_KIND
    assert len(withheld) == 50 - suppression.MAX_PER_KIND


def test_the_cap_keeps_the_most_severe() -> None:
    """When a cap bites it must drop the least urgent, not the last-emitted."""
    items = [candidate(subject=f"warn-{n}") for n in range(10)]
    items.append(candidate(subject="critical-1", severity=Severity.CRITICAL))

    sending, _ = suppression.apply(items, SuppressionState({}), now=NOW)
    assert any(item.subject == "critical-1" for item in sending)


def test_withheld_candidates_carry_their_reason() -> None:
    """ "Why didn't I hear about this?" deserves better than a shrug."""
    flood = [candidate(subject=f"SKU-{n}") for n in range(20)]
    _, withheld = suppression.apply(flood, SuppressionState({}), now=NOW)

    assert withheld
    assert all(reason for _, reason in withheld)


def test_capped_alerts_produce_a_digest() -> None:
    """Silence would leave the recipient believing it was a quiet day."""
    flood = [candidate(subject=f"SKU-{n}") for n in range(30)]
    _, withheld = suppression.apply(flood, SuppressionState({}), now=NOW)

    digest = suppression.summarise_withheld(withheld)
    assert digest is not None
    assert "withheld" in digest.title
    assert digest.severity is Severity.INFO


def test_no_digest_when_nothing_was_capped() -> None:
    """A digest saying nothing was withheld is noise about silence."""
    item = candidate()
    state = SuppressionState({item.fingerprint: NOW})
    _, withheld = suppression.apply([item], state, now=NOW)

    assert withheld
    assert suppression.summarise_withheld(withheld) is None


# ── Detection thresholds ─────────────────────────────────────────────


def test_healthy_stock_raises_nothing() -> None:
    rows = [
        {
            "sku": "A",
            "store_id": "S1",
            "soonest_stockout_days": 40.0,
            "revenue_at_risk": 900.0,
        }
    ]
    assert detectors.low_inventory(rows, as_of=TODAY) == []


def test_imminent_stockouts_escalate_to_critical() -> None:
    rows = [{"sku": "A", "store_id": "S1", "soonest_stockout_days": 1.0, "revenue_at_risk": 900.0}]
    found = detectors.low_inventory(rows, as_of=TODAY)
    assert found and found[0].severity is Severity.CRITICAL


def test_low_inventory_ranks_by_cost_not_by_urgency_of_the_number() -> None:
    """A staple two days out loses more than a slow mover at zero."""
    rows = [
        {"sku": "SLOW", "store_id": "S1", "soonest_stockout_days": 0.5, "revenue_at_risk": 50.0},
        {
            "sku": "STAPLE",
            "store_id": "S1",
            "soonest_stockout_days": 2.0,
            "revenue_at_risk": 9000.0,
        },
    ]
    found = detectors.low_inventory(rows, as_of=TODAY)
    assert found[0].subject.startswith("STAPLE")


def test_a_small_sales_move_is_not_an_alert() -> None:
    assert detectors.sales_drop(current=99.0, prior=100.0, region_rows=[], as_of=TODAY) == []


def test_a_material_sales_drop_alerts_once_not_per_region() -> None:
    """Five regional alerts saying the same national thing is what people mute."""
    regions = [{"region": f"R{n}", "net_revenue": 10.0 * n} for n in range(5)]
    found = detectors.sales_drop(current=70.0, prior=100.0, region_rows=regions, as_of=TODAY)

    assert len(found) == 1
    assert found[0].severity is Severity.CRITICAL


def test_a_forecast_at_parity_with_naive_is_flagged() -> None:
    """Replenishment is planned on it, and the weakness is invisible downstream."""
    found = detectors.forecast_risk(
        [{"target": "revenue", "model_mase": 1.2, "model_wape": 0.1}], as_of=TODAY
    )
    assert found and "naive baseline" in found[0].title


def test_a_skilful_forecast_is_not_flagged() -> None:
    assert detectors.forecast_risk([{"target": "revenue", "model_mase": 0.6}], as_of=TODAY) == []


def test_fraud_detection_needs_enough_peers_to_know_what_normal_is() -> None:
    """An outlier among three is arithmetic, not evidence."""
    rows = [{"slice_value": f"S{n}", "return_rate": 0.05 * n} for n in range(3)]
    assert detectors.fraud_risk(rows, as_of=TODAY) == []


def test_fraud_alerts_never_accuse() -> None:
    """This is the one detector whose output touches people.

    A z-score is equally consistent with a damaged delivery, a mis-picked
    planogram, or one large return. Naming it as fraud would be both wrong and,
    aimed at a named employee, defamatory.
    """
    rows = [{"slice_value": f"S{n}", "return_rate": 0.05} for n in range(10)]
    rows.append({"slice_value": "S99", "return_rate": 0.60})

    found = detectors.fraud_risk(rows, as_of=TODAY)
    assert found

    alert = found[0]
    assert "review" in alert.title.lower()
    assert "anomaly, not a finding" in alert.body
    text = f"{alert.title} {alert.body}".lower()
    assert "fraud" not in text
    assert "theft" not in text
    assert alert.severity is not Severity.CRITICAL


def test_a_reliable_supplier_raises_nothing() -> None:
    rows = [{"supplier_name": "Good", "otif_rate": 0.97, "closed_lines": 400.0}]
    assert detectors.inventory_risk(rows, as_of=TODAY) == []


def test_a_supplier_with_too_few_lines_is_not_judged() -> None:
    rows = [{"supplier_name": "New", "otif_rate": 0.2, "closed_lines": 5.0}]
    assert detectors.inventory_risk(rows, as_of=TODAY) == []


def test_recommendations_alert_only_when_materially_valuable() -> None:
    """Notifying every run trains people that the notification means nothing."""
    assert (
        detectors.recommendation_ready({"count": 3, "net_profit_opportunity": 100.0}, as_of=TODAY)
        == []
    )

    found = detectors.recommendation_ready(
        {
            "count": 3,
            "net_profit_opportunity": 90_000.0,
            "gross_profit_opportunity": 150_000.0,
            "recommendations": [{"action": "Reorder AC-1010"}],
        },
        as_of=TODAY,
    )
    assert found and "90,000" in found[0].title


def test_recommendation_alerts_quote_the_deduplicated_figure() -> None:
    found = detectors.recommendation_ready(
        {
            "count": 2,
            "net_profit_opportunity": 90_000.0,
            "gross_profit_opportunity": 400_000.0,
            "recommendations": [{"action": "Do the thing"}],
        },
        as_of=TODAY,
    )
    assert "overlapping" in found[0].body


# ── Every alert is actionable ────────────────────────────────────────


def test_every_detector_produces_a_deep_link() -> None:
    """An alert with nowhere to go is one the recipient must translate
    into an action themselves, and most of them will not."""
    produced = [
        *detectors.low_inventory(
            [
                {
                    "sku": "A",
                    "store_id": "S1",
                    "soonest_stockout_days": 1.0,
                    "revenue_at_risk": 900.0,
                }
            ],
            as_of=TODAY,
        ),
        *detectors.sales_drop(current=70.0, prior=100.0, region_rows=[], as_of=TODAY),
        *detectors.forecast_risk([{"target": "revenue", "model_mase": 1.4}], as_of=TODAY),
        *detectors.inventory_risk(
            [{"supplier_name": "Bad", "otif_rate": 0.4, "closed_lines": 300.0}], as_of=TODAY
        ),
    ]
    assert produced
    for alert in produced:
        assert alert.deep_link, f"{alert.kind} has nowhere to go"
        assert alert.body


def test_event_types_follow_the_ledger_convention() -> None:
    assert event_type_for(AlertKind.LOW_INVENTORY, Severity.CRITICAL) == "alert.critical"
    assert event_type_for(AlertKind.RECOMMENDATION_READY, Severity.INFO) == "rec.proposed"


# ── Email ────────────────────────────────────────────────────────────


def test_email_renders_plain_text_carrying_the_whole_message() -> None:
    """A recipient on a locked-down client gets the same information."""
    message = render(candidate(severity=Severity.CRITICAL).as_payload())

    assert "[CRITICAL]" in message.subject
    assert message.body.strip()
    assert message.html


def test_email_escapes_markup_from_data() -> None:
    """A product name with an ampersand must not break the HTML part."""
    payload = candidate().as_payload()
    payload["title"] = "Ben & Jerry's <b>Half Baked</b>"

    message = render(payload)
    assert "&amp;" in message.html
    assert "<b>" not in message.html


def test_the_recording_sender_captures_instead_of_sending() -> None:
    sender = RecordingEmailSender()
    sender.send(Message(to="a@b.c", subject="s", body="b"))
    assert len(sender.sent) == 1


def test_a_failing_address_raises_so_the_caller_can_record_it() -> None:
    """A delivery system that swallows errors reports perfect health while
    nobody receives anything."""
    sender = RecordingEmailSender(fail_on="bad@b.c")
    with pytest.raises(Exception, match="simulated failure"):
        sender.send(Message(to="bad@b.c", subject="s", body="b"))


def test_the_null_sender_delivers_nothing() -> None:
    NullEmailSender().send(Message(to="a@b.c", subject="s", body="b"))
