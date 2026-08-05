"""Deciding what *not* to send.

This is the part that determines whether an alerting system is used or muted.
Detection is comparatively easy; the discipline is in refusing to send things.
Four rules apply, in order, and each exists because of a specific way these
systems fail:

**Re-notification window.** A condition that is still true is not news. Without
a window, an hourly sweep sends the same stockout twenty-four times a day and
the recipient builds a mail rule — after which the one alert that mattered is
filed with the rest. The window is longer for lower severities, because the
cost of a delayed info alert is small and the cost of a noisy one is that
nobody reads any of them.

**Volume cap.** A supplier outage can stock out four hundred lines at once. Four
hundred separate alerts is not four hundred times the information; it is one
piece of information rendered unreadable. Past a cap the tail is summarised
rather than sent, and the summary says how many were withheld.

**Escalation always passes.** A condition moving from warn to critical is the
one change worth interrupting someone for, so it bypasses the window — the
fingerprint includes severity precisely to make that a new identity.

**Explicit mutes are absolute.** When somebody has muted a subject they have
made a decision, and overriding it teaches them the mute button does not work.

Every suppressed candidate is *reported* with its reason rather than dropped
silently. An operator asking "why didn't I hear about this?" deserves an
answer better than a shrug.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.services.notifications.contracts import AlertCandidate, AlertKind, Severity

#: How long the same condition stays quiet after being notified.
#:
#: Scaled by severity rather than fixed. A critical stockout re-raised after
#: four hours is a reasonable nudge; an informational one re-raised that often
#: is the reason nobody reads the informational ones.
RENOTIFY_WINDOW: dict[Severity, timedelta] = {
    Severity.CRITICAL: timedelta(hours=4),
    Severity.WARN: timedelta(hours=12),
    Severity.INFO: timedelta(hours=48),
}

#: Alerts of one kind sent per sweep before the rest are summarised.
#:
#: A supplier failure can stock out hundreds of lines simultaneously, and
#: sending each one destroys the channel for everything else that day.
MAX_PER_KIND = 5

#: Total alerts per sweep across all kinds.
MAX_PER_SWEEP = 20


@dataclass(frozen=True, slots=True)
class Decision:
    """Whether a candidate is sent, and why not when it is not."""

    send: bool
    reason: str = ""


@dataclass(frozen=True, slots=True)
class SuppressionState:
    """What the system has already said, and what has been silenced.

    Passed in rather than queried here so this module stays pure and its rules
    can be tested against an exact history rather than a database fixture.
    """

    last_notified: dict[str, datetime]
    """fingerprint → when it last went out."""

    muted_subjects: frozenset[str] = frozenset()
    muted_kinds: frozenset[AlertKind] = frozenset()

    def since(self, fingerprint: str, *, now: datetime) -> timedelta | None:
        previous = self.last_notified.get(fingerprint)
        return None if previous is None else now - previous


def decide(
    candidate: AlertCandidate, state: SuppressionState, *, now: datetime | None = None
) -> Decision:
    """Apply the suppression rules to one candidate."""
    moment = now or datetime.now(UTC)

    if candidate.kind in state.muted_kinds:
        return Decision(False, f"{candidate.kind.value} alerts are muted")
    if candidate.subject in state.muted_subjects:
        return Decision(False, f"{candidate.subject} is muted")

    elapsed = state.since(candidate.fingerprint, now=moment)
    if elapsed is None:
        return Decision(True)

    window = RENOTIFY_WINDOW[candidate.severity]
    if elapsed < window:
        remaining = window - elapsed
        hours = remaining.total_seconds() / 3600
        return Decision(
            False,
            f"unchanged since it was last sent; quiet for another {hours:.0f}h",
        )

    return Decision(True)


def apply(
    candidates: Iterable[AlertCandidate],
    state: SuppressionState,
    *,
    now: datetime | None = None,
    max_per_kind: int = MAX_PER_KIND,
    max_per_sweep: int = MAX_PER_SWEEP,
) -> tuple[list[AlertCandidate], list[tuple[AlertCandidate, str]]]:
    """Split candidates into what to send and what to withhold, with reasons.

    Ordered by severity first so that when a cap bites it removes the least
    urgent items rather than whatever the detectors happened to emit last.
    """
    moment = now or datetime.now(UTC)
    order = {Severity.CRITICAL: 0, Severity.WARN: 1, Severity.INFO: 2}
    ranked = sorted(
        candidates,
        key=lambda item: (order[item.severity], -abs(item.observed)),
    )

    sending: list[AlertCandidate] = []
    withheld: list[tuple[AlertCandidate, str]] = []
    per_kind: dict[AlertKind, int] = {}

    for candidate in ranked:
        decision = decide(candidate, state, now=moment)
        if not decision.send:
            withheld.append((candidate, decision.reason))
            continue

        count = per_kind.get(candidate.kind, 0)
        if count >= max_per_kind:
            withheld.append(
                (
                    candidate,
                    f"more than {max_per_kind} {candidate.kind.value} alerts this "
                    "sweep; the rest are summarised rather than sent individually",
                )
            )
            continue
        if len(sending) >= max_per_sweep:
            withheld.append((candidate, f"sweep cap of {max_per_sweep} alerts reached"))
            continue

        per_kind[candidate.kind] = count + 1
        sending.append(candidate)

    return sending, withheld


def summarise_withheld(
    withheld: list[tuple[AlertCandidate, str]],
) -> AlertCandidate | None:
    """One digest alert standing in for what the volume cap held back.

    Returned rather than folded into the send list by the caller's choice: the
    point of a cap is that the recipient learns *something happened at scale*
    without receiving it item by item. Silence would leave them believing the
    quiet run meant a quiet day.
    """
    capped = [
        (candidate, reason)
        for candidate, reason in withheld
        if "summarised rather than sent" in reason or "sweep cap" in reason
    ]
    if not capped:
        return None

    kinds: dict[str, int] = {}
    for candidate, _ in capped:
        kinds[candidate.kind.value] = kinds.get(candidate.kind.value, 0) + 1
    breakdown = ", ".join(f"{count} × {kind}" for kind, count in sorted(kinds.items()))

    return AlertCandidate(
        kind=AlertKind.RECOMMENDATION_READY,
        subject="digest",
        title=f"{len(capped)} further alerts withheld this sweep",
        body=(
            f"{breakdown}. Individual alerts were capped to keep the channel "
            "readable. Open the alerts view for the full list."
        ),
        severity=Severity.INFO,
        observed=float(len(capped)),
        evidence={"withheld_by_kind": kinds},
        deep_link="/alerts",
    )
