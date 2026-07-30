"""Scaffold smoke test for the retailmind-etl package."""

import ingestion
import quality


def test_packages_import() -> None:
    assert ingestion is not None
    assert quality is not None
