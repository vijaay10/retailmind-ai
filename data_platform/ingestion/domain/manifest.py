"""Partition manifests — the commit marker and the flight recorder.

A bronze partition without ``manifest.json`` is invisible to everything
downstream. Writing the manifest *last*, after the data files are in place, is
what makes landing atomic without a transactional filesystem: a crash
mid-write leaves an unreferenced directory, not a half-visible partition
(ETL §3, §19).
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

MANIFEST_NAME = "manifest.json"


def file_checksum(path: Path, *, chunk_size: int = 1 << 20) -> str:
    """SHA-256 of a file, read in chunks so large drops stay memory-bounded.

    Drives file-level duplicate detection: a re-drop with an identical
    checksum is skipped, a changed one is treated as a correction (ETL §7).
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class SourceFile:
    name: str
    bytes: int
    checksum: str


@dataclass(slots=True)
class PartitionManifest:
    """Everything needed to explain, verify, or replay one landed partition."""

    source: str
    table: str
    partition: str
    schema_version: str
    schema_fingerprint: str
    connector_version: str
    rows_read: int
    rows_rejected: int
    rows_landed: int
    source_files: list[SourceFile] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    watermark: str | None = None
    landed_at: str = field(default_factory=lambda: datetime.now(tz=UTC).isoformat())

    @property
    def reject_rate(self) -> float:
        return self.rows_rejected / self.rows_read if self.rows_read else 0.0

    @property
    def checksums(self) -> set[str]:
        return {f.checksum for f in self.source_files}

    def write(self, directory: Path) -> Path:
        """Commit the partition. Call this only after data files are durable."""
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / MANIFEST_NAME
        path.write_text(json.dumps(asdict(self), indent=2, sort_keys=True))
        return path

    @classmethod
    def read(cls, directory: Path) -> Self | None:
        """Load a committed manifest, or None when the partition is uncommitted."""
        path = directory / MANIFEST_NAME
        if not path.exists():
            return None
        raw: dict[str, Any] = json.loads(path.read_text())
        raw["source_files"] = [SourceFile(**f) for f in raw.get("source_files", [])]
        return cls(**raw)
