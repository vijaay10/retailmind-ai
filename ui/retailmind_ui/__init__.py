"""RetailMind operator console.

A thin rendering layer over the API. It holds no business logic: every figure
arrives already computed and already qualified, and the console's one
responsibility is to draw both — the number *and* what the platform said about
trusting it.
"""

import pandas as pd

# pandas 3.0 turned Arrow-backed string storage on by default
# (`future.infer_string`). Under repeated DataFrame construction across many
# Streamlit script re-runs in the same process — exactly what happens across
# workspaces during a test run, and across page navigations in a live
# session — that path segfaults inside
# `pandas.core.arrays.string_arrow._from_sequence` on this pandas/pyarrow
# pairing (reproduced: 12/12 workspace re-runs crash on the pattern with this
# on, 12/12 pass with it off — same data, same code, same run order).
# Turning it off reverts string columns/indexes to the numpy `object` dtype
# pandas used for years before 3.0 — slower for very large frames, irrelevant
# at this console's row counts, and it avoids a native crash entirely rather
# than trying to catch one. Module-level so it's set once, before any
# DataFrame gets built anywhere in this package.
pd.options.future.infer_string = False
