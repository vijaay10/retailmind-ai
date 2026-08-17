"""Regression coverage for the Prompt 11 Admin-workspace segmentation fault.

Prompt 11 found `ui/tests/test_workspaces.py`'s full parametrized run
segfaulting on `test_every_workspace_survives_an_api_that_answers_nothing[12_Admin.py]`
— reproducible 3 times, but never when that single case ran in isolation.
Root cause (confirmed by an A/B run with the option toggled): pandas 3.0
defaults `future.infer_string` on, which builds DataFrame column indexes
through `pandas.core.arrays.string_arrow._from_sequence`; on this
pandas/pyarrow pairing, that path segfaults after enough DataFrame
constructions accumulate across the many Streamlit script re-runs a full
`AppTest` sweep performs in one process — not because of anything about the
data itself (the crashing call was 12 static dict rows, same every time,
never actually empty).

Fixed in `retailmind_ui/__init__.py` by disabling Arrow-backed string
storage for the whole package. These tests exist so that fix can't silently
regress, and so the crash's original trigger — many `frame()`/`table()`
calls in one process — has a fast, direct regression check that doesn't
require a full `AppTest` sweep to catch it.
"""

import pandas as pd
import pytest

from retailmind_ui.components import primitives


def test_arrow_string_inference_is_disabled_for_the_package() -> None:
    """Guard rail: importing retailmind_ui must turn this off.

    If this ever reads True again — a pandas upgrade resetting the option,
    someone removing the `__init__.py` line, an import order change — the
    segfault this fix addresses can come back silently, since it doesn't
    reproduce reliably enough for a flaky-looking crash to get noticed and
    attributed correctly on the first occurrence.
    """
    assert pd.options.future.infer_string is False


def test_frame_handles_the_exact_admin_workspace_shape_repeatedly() -> None:
    """The exact row shape from `12_Admin.py`'s crashing `ui.table()` call,
    constructed many times in a tight loop — the accumulation pattern that
    triggered the original segfault, without needing a full AppTest sweep
    to reproduce it."""
    rows = [
        {
            "workspace": f"workspace_{i}",
            "group": "intelligence",
            "requires": "analytics.read",
            "available": "yes" if i % 2 == 0 else "no",
            "purpose": "Demonstrates the exact dict shape that crashed.",
        }
        for i in range(12)
    ]

    for _ in range(200):
        df = primitives.frame(rows)
        assert len(df) == 12
        assert list(df.columns) == ["Workspace", "Group", "Requires", "Available", "Purpose"]


def test_frame_of_empty_rows_returns_an_empty_dataframe_without_constructing_one() -> None:
    """`frame([])` takes the early-return path — never reaches the pandas
    construction that crashed — regardless of the Arrow-string setting."""
    df = primitives.frame([])
    assert df.empty


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{"a": None, "b": "x"}],
        [{"a": 1, "b": None}, {"a": None, "b": None}],
        [{"a": "x"}, {"b": "y"}],  # ragged keys across rows
    ],
)
def test_frame_survives_null_and_ragged_shapes(rows: list[dict[str, object]]) -> None:
    """Malformed/unexpected shapes an API could plausibly hand back — must
    not raise and must not crash the interpreter."""
    df = primitives.frame(rows)
    assert df is not None
