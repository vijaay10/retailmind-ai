"""Model persistence, versioning, and the promotion gate.

**Models are stored as JSON, never pickle.** This is a security boundary, not
a style preference. `pickle.load` executes arbitrary code by design, so a
pickle-based model registry turns "anyone who can write to the model directory"
into "anyone who can run code as the service account" — and the model
directory is exactly the thing a training job, a CI runner, and an artefact
sync all write to. Every model in this package is a coefficient vector and a
feature list, which serialise to plain data, so the safe option costs nothing.
It also means a model artefact can be read, diffed, and reviewed.

**Promotion is gated on beating the baseline, not on being new.** A trained
challenger does not replace the incumbent because it is more sophisticated; it
replaces it by clearing a stated margin on out-of-sample WAPE (PRD G4). When
it does not, the baseline stays champion and the decision is recorded with its
numbers. That record is what stops the same rejected model being re-proposed
every quarter with a different name.

Each version carries a **model card**: the training window, the data snapshot
it read, the full scorecard, and the promotion decision. A forecast whose
provenance cannot be reconstructed is not auditable, and a planner asked to
trust it has no way to check.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import structlog

from forecasting.metrics import ForecastScore

log = structlog.get_logger(__name__)

#: Relative WAPE improvement a challenger must show over the incumbent
#: baseline to be promoted (PRD G4). Fifteen percent is deliberately a wide
#: margin: a model that beats the baseline by two percent on forty evaluation
#: points has not demonstrated anything a different week would not reverse.
MIN_IMPROVEMENT = 0.15

#: Model artefacts are JSON. Loading one must never be able to run code.
ARTEFACT_SUFFIX = ".json"


@dataclass(frozen=True, slots=True)
class ModelCard:
    """Everything needed to judge a stored model without rerunning it."""

    target: str
    series_key: str
    model_name: str
    model_class: str
    version: str
    created_at: str
    training_start: str
    training_end: str
    training_days: int
    horizon: int
    data_snapshot_id: str
    feature_names: list[str]
    metrics: dict[str, Any]
    challenger_accepted: bool
    """Whether the challenger cleared the gate. **Not** the same as being
    live: when it fails, the baseline ships and is the champion."""
    promotion_reason: str
    baseline_name: str = ""
    baseline_wape: float | None = None
    improvement_over_baseline: float | None = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    """Whether a challenger earned production, and the arithmetic behind it."""

    promoted: bool
    reason: str
    champion_name: str
    challenger_name: str
    champion_wape: float
    challenger_wape: float
    improvement: float

    @property
    def summary(self) -> str:
        direction = "beats" if self.improvement > 0 else "trails"
        return (
            f"{self.challenger_name} {direction} {self.champion_name} by "
            f"{abs(self.improvement):.1%} WAPE "
            f"({self.challenger_wape:.4f} vs {self.champion_wape:.4f}); "
            f"gate requires {MIN_IMPROVEMENT:.0%}"
        )


def decide_promotion(
    *,
    champion_name: str,
    champion: ForecastScore,
    challenger_name: str,
    challenger: ForecastScore,
    min_improvement: float = MIN_IMPROVEMENT,
) -> PromotionDecision:
    """Apply the adoption gate.

    Three ways to fail, and each is a real failure mode rather than a
    formality:

    * **The challenger does not beat seasonal naive.** MASE ≥ 1 means it has
      learned nothing a calendar could not tell you.
    * **It beats the champion by too little.** Inside the margin, the
      difference is indistinguishable from which weeks happened to land in the
      evaluation window.
    * **It was scored on too few points.** A brilliant number from six
      observations is a coin flip with good presentation.
    """
    improvement = (champion.wape - challenger.wape) / champion.wape if champion.wape > 0 else 0.0

    if not challenger.is_representative:
        reason = (
            f"rejected: scored on {challenger.points} points, below the "
            f"evidence floor — the number is not yet measurable"
        )
        promoted = False
    elif not challenger.beats_seasonal_naive:
        reason = (
            f"rejected: MASE {challenger.mase:.3f} ≥ 1.0 — no better than "
            "assuming next week looks like last week"
        )
        promoted = False
    elif improvement < min_improvement:
        reason = (
            f"rejected: {improvement:.1%} WAPE improvement is inside the "
            f"{min_improvement:.0%} gate — indistinguishable from which weeks "
            "landed in the evaluation window"
        )
        promoted = False
    else:
        reason = (
            f"promoted: {improvement:.1%} WAPE improvement clears the {min_improvement:.0%} gate"
        )
        promoted = True

    return PromotionDecision(
        promoted=promoted,
        reason=reason,
        champion_name=champion_name,
        challenger_name=challenger_name,
        champion_wape=champion.wape,
        challenger_wape=challenger.wape,
        improvement=improvement,
    )


class ModelRegistry:
    """Versioned, JSON-backed model storage on the local filesystem.

    Layout::

        <root>/<target>/<series>/<version>/model.json
        <root>/<target>/<series>/<version>/card.json
        <root>/<target>/<series>/champion.json   → pointer to the live version

    **The series is part of the path, not just the target.** Demand trains one
    model per SKU × store, and keying only on the target would have every
    series overwrite the last one's artefact and champion pointer — leaving a
    registry that looks populated while holding exactly one model with the
    wrong name on it.

    The champion pointer is a separate small file rather than a copy, so
    promoting is atomic-ish and the history stays intact. Rolling back is
    rewriting one pointer, not restoring a backup — which matters at 3am.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    # ── Writing ──────────────────────────────────────────────────────

    def save(self, *, target: str, model: Any, card: ModelCard) -> Path:
        """Persist a model version and its card. Returns the version directory."""
        directory = self.root / target / _slug(card.series_key) / card.version
        directory.mkdir(parents=True, exist_ok=True)

        payload = model.to_dict()
        if not _is_json_safe(payload):
            raise TypeError(
                f"{card.model_name}: to_dict() returned values that are not JSON "
                "primitives. Models persist as JSON so that loading one can never "
                "execute code; a model that needs pickle cannot be stored here."
            )

        (directory / f"model{ARTEFACT_SUFFIX}").write_text(json.dumps(payload, indent=2))
        (directory / f"card{ARTEFACT_SUFFIX}").write_text(json.dumps(card.as_dict(), indent=2))

        log.info(
            "forecast.model_saved",
            target=target,
            series=card.series_key,
            model=card.model_name,
            version=card.version,
            challenger_accepted=card.challenger_accepted,
        )
        return directory

    def promote(self, target: str, series_key: str, version: str, *, reason: str) -> None:
        """Point the champion for one series at a version.

        Called for every trained series, including those where the challenger
        was rejected — in that case the version being pointed at *is* the
        baseline, which is the model that ships. "Promoted" here means "this
        is live", not "the new model won".
        """
        directory = self.root / target / _slug(series_key) / version
        if not (directory / f"model{ARTEFACT_SUFFIX}").exists():
            raise FileNotFoundError(f"no stored model at {directory}")

        pointer = self.root / target / _slug(series_key) / f"champion{ARTEFACT_SUFFIX}"
        pointer.parent.mkdir(parents=True, exist_ok=True)
        pointer.write_text(
            json.dumps(
                {
                    "version": version,
                    "reason": reason,
                    "promoted_at": datetime.now(UTC).isoformat(),
                },
                indent=2,
            )
        )
        log.info(
            "forecast.champion_set",
            target=target,
            series=series_key,
            version=version,
            reason=reason,
        )

    # ── Reading ──────────────────────────────────────────────────────

    def champion_version(self, target: str, series_key: str) -> str | None:
        pointer = self.root / target / _slug(series_key) / f"champion{ARTEFACT_SUFFIX}"
        if not pointer.exists():
            return None
        return str(json.loads(pointer.read_text())["version"])

    def load_model_payload(self, target: str, series_key: str, version: str) -> dict[str, Any]:
        """Read a stored artefact as plain data.

        Returns the payload rather than a live model: reconstruction belongs
        to the model classes, and keeping the registry ignorant of them means
        it never has to import — or execute — anything to read a file.
        """
        path = self.root / target / _slug(series_key) / version / f"model{ARTEFACT_SUFFIX}"
        if not path.exists():
            raise FileNotFoundError(f"no stored model at {path}")
        payload: dict[str, Any] = json.loads(path.read_text())
        return payload

    def load_card(self, target: str, series_key: str, version: str) -> dict[str, Any]:
        path = self.root / target / _slug(series_key) / version / f"card{ARTEFACT_SUFFIX}"
        if not path.exists():
            raise FileNotFoundError(f"no card at {path}")
        card: dict[str, Any] = json.loads(path.read_text())
        return card

    def versions(self, target: str, series_key: str) -> list[str]:
        directory = self.root / target / _slug(series_key)
        if not directory.exists():
            return []
        return sorted(
            child.name
            for child in directory.iterdir()
            if child.is_dir() and (child / f"model{ARTEFACT_SUFFIX}").exists()
        )

    def targets(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(child.name for child in self.root.iterdir() if child.is_dir())


def new_version(reference: date | None = None) -> str:
    """A sortable version stamp.

    Timestamped rather than a content hash: two trainings on the same data
    genuinely are different events, and the audit question is almost always
    "what was live on the 14th", which a chronological identifier answers
    directly.
    """
    moment = (
        datetime.now(UTC)
        if reference is None
        else datetime.combine(reference, datetime.min.time(), UTC)
    )
    return moment.strftime("%Y%m%dT%H%M%SZ")


def _slug(series_key: str) -> str:
    """Filesystem-safe directory name for a series key.

    Demand keys look like ``AC-1010|S2001``. The pipe is legal on POSIX and
    not on Windows, and neither is worth discovering during an incident.
    """
    return "".join(char if char.isalnum() or char in "-_." else "_" for char in series_key)


def _is_json_safe(value: Any) -> bool:
    """Reject anything that would need a code-executing deserialiser."""
    if value is None or isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, list | tuple):
        return all(_is_json_safe(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _is_json_safe(v) for k, v in value.items())
    return False
