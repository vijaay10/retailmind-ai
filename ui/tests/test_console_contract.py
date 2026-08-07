"""Invariants that hold across the whole console.

Two kinds. Behavioural — sign-in, navigation, what a role sees. And source
level, because some properties are about what the code must *never* contain,
and a test that only exercises today's workspaces would not catch tomorrow's
reintroducing the thing.
"""

import ast
import inspect
import re
from pathlib import Path
from typing import Any

import pytest

from retailmind_ui import design, session
from retailmind_ui.api import ApiError
from retailmind_ui.components import evidence, primitives

UI = Path(__file__).resolve().parents[1]
WORKSPACES = UI / "workspaces"
APP = UI / "app.py"

FILES = sorted(WORKSPACES.glob("*.py"))


# ── The registry matches the filesystem ──────────────────────────────


def test_every_declared_workspace_has_a_file() -> None:
    """A named workspace with no file is a dead entry in the navigation."""
    for item in session.WORKSPACES:
        assert (UI / item.file).exists(), item.name


def test_every_file_is_declared() -> None:
    """A workspace missing from the registry is one nothing gates: it would be
    published to every role, and only the API would push back."""
    declared = {item.file.rsplit("/", 1)[-1] for item in session.WORKSPACES}
    for path in FILES:
        assert path.name in declared, path.name


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_every_workspace_gates_itself(path: Path) -> None:
    assert "session.require(" in path.read_text(), path.name


def test_each_workspace_requires_what_the_registry_says(monkeypatch: Any) -> None:
    """A workspace whose own `require` call disagrees with the registry is one
    the navigation shows to people the page then refuses."""
    for item in session.WORKSPACES:
        source = (UI / item.file).read_text()
        match = re.search(r"session\.require\(\s*\"([^\"]+)\"", source)
        assert match, f"{item.name} has no literal permission"
        assert match.group(1) == item.permission, item.name


# ── Things the source must never contain ─────────────────────────────


@pytest.mark.parametrize("path", [*FILES, APP], ids=lambda p: p.name)
def test_no_workspace_writes_a_token_to_the_browser(path: Path) -> None:
    source = path.read_text().lower()
    assert "localstorage" not in source
    assert "sessionstorage" not in source


#: A query, rather than a word that happens to appear in prose.
SQL = re.compile(r"\bselect\b.{0,200}?\bfrom\b|\bgroup\s+by\b|\bwhere\b\s+\w+\s*=", re.I | re.S)


@pytest.mark.parametrize("path", [*FILES, APP], ids=lambda p: p.name)
def test_no_workspace_composes_sql(path: Path) -> None:
    """The console sends metric *names*; the API owns the query. A workspace
    assembling even a fragment of a WHERE clause would put a string the user
    typed onto the path to the warehouse."""
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert not SQL.search(node.value), f"{path.name}: {node.value[:80]}"


def test_caveats_are_not_rendered_behind_a_disclosure_triangle() -> None:
    """The property the whole component library exists for.

    A behavioural test catches today's workspaces; this catches the well-meaning
    refactor that tidies caveats into a collapsed block to shorten a busy screen.
    """
    body = inspect.getsource(evidence.caveats)
    for hider in ("st.expander", "st.popover", "st.status"):
        assert hider not in body


def test_the_absence_helper_always_asks_for_a_reason() -> None:
    assert "reason" in primitives.empty.__code__.co_varnames


# ── The design system ────────────────────────────────────────────────


def test_generated_markup_is_flattened_before_rendering() -> None:
    """Streamlit parses this as Markdown, and Markdown treats a line indented
    four spaces as a code block — so an indented fragment renders as literal
    source. Flattening in one place is what stops every component needing to
    remember."""
    assert "line.strip()" in inspect.getsource(design.html)


def test_values_from_the_api_are_escaped_before_interpolation() -> None:
    """Supplier names, SKU labels and analyst prose are untrusted text. A
    supplier called `<script>` must render as a supplier called `<script>`."""
    assert design.escape("<script>alert(1)</script>") == ("&lt;script&gt;alert(1)&lt;/script&gt;")
    assert design.escape('" onload="') == "&quot; onload=&quot;"


def test_every_evidence_tier_the_api_can_return_has_a_colour() -> None:
    """An unstyled tier renders grey and reads as "unknown" — which is a claim
    about the evidence, not a gap in the palette."""
    for tier in (
        "arithmetic",
        "measured",
        "mechanical",
        "modelled",
        "statistical",
        "assumed",
        "associative",
        "inferred",
        "derived",
        "unknown",
    ):
        assert design.tier_colour(tier) != design.INK["faint"] or tier == "unknown"
        assert design.tier_meaning(tier) != tier


def test_confidence_is_banded_rather_than_a_gradient() -> None:
    """A continuous ramp would imply the platform can tell 61% from 64%
    confidence. It cannot — these are graded estimates with hard ceilings."""
    assert design.confidence_colour(0.9) == design.SEMANTIC["positive"]
    assert design.confidence_colour(0.5) == design.SEMANTIC["warning"]
    assert design.confidence_colour(0.2) == design.SEMANTIC["critical"]


# ── Navigation ───────────────────────────────────────────────────────


def test_a_signed_out_visitor_is_not_shown_the_map_of_the_product(console: Any) -> None:
    """Streamlit publishes every file in a pages directory to everyone by
    default. Declaring navigation is what stops an anonymous visitor seeing
    twelve workspaces they cannot open."""
    app = console()
    assert app.text_input
    assert not app.sidebar.button


def test_navigation_carries_only_what_a_role_can_open(console: Any, ceo: dict[str, Any]) -> None:
    limited = console(user={**ceo, "permissions": ["analytics.revenue.read"]})
    names = [page for page in session.visible_pages()]
    assert "Sales Intelligence" in names or not names
    assert not limited.exception


def test_a_failed_sign_in_shows_the_apis_own_message(console: Any, texts: Any) -> None:
    """ "Invalid credentials" is wrong for a locked account and wrong again for
    a stopped backend, and those need different responses."""

    class Refusing:
        tokens = None

        def login(self, email: str, password: str) -> None:
            raise ApiError(status=423, title="Account locked", detail="Account is locked.")

    app = console(client=Refusing())
    app.button[0].click().run()
    assert "locked" in texts(app.error)


def test_every_workspace_can_be_signed_out_of(workspace: Any, manager: dict[str, Any]) -> None:
    """Streamlit runs only the current page's script, so a sidebar built in the
    entry point exists there and nowhere else."""
    for name in ("9_Forecast_Intelligence.py", "10_Risk_Center.py"):
        app = workspace(name, user=manager)
        assert any(button.label == "Sign out" for button in app.sidebar.button), name


def test_date_controls_default_to_the_warehouse_date_not_today(workspace: Any) -> None:
    """A period ending "today" covers days the warehouse does not have, and the
    platform then reports a 100% collapse in revenue."""
    from datetime import date

    app = workspace(
        "5_Sales_Intelligence.py",
        responses={
            "/api/v1/analytics/revenue/summary": {
                "totals": {"net_revenue": 1000.0},
                "meta": {"freshness": "2026-07-21"},
            },
            "/api/v1/analytics/revenue/breakdown": {"data": [], "meta": {}},
            "/api/v1/analytics/revenue/trend": {"series": []},
            "/api/v1/dashboard/profit": {"cards": []},
        },
    )
    assert app.date_input[0].value == date(2026, 7, 21)
