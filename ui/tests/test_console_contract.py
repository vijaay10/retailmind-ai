"""Invariants that hold across the whole console.

Two kinds. The first are behavioural — sign-in, navigation, what a role sees.
The second read the source, because some properties are about what the code
must *never* contain, and a test that only exercises today's pages would not
catch tomorrow's page reintroducing the thing.
"""

import ast
import inspect
import re
from pathlib import Path
from typing import Any

import pytest
from streamlit import config

from retailmind_ui import components, session, theme
from retailmind_ui.api import ApiError

UI = Path(__file__).resolve().parents[1]
PAGES = UI / "pages"
APP = UI / "app.py"

PAGE_FILES = sorted(PAGES.glob("*.py"))


# ── Navigation matches the permission map ────────────────────────────


def test_every_declared_area_has_a_page() -> None:
    """A named area with no file is a dead link in the sidebar."""
    slugs = {re.sub(r"^\d+_", "", path.stem).replace("_", " ").lower() for path in PAGE_FILES}
    for name in session.PAGE_PERMISSIONS:
        assert name.lower() in slugs, name


def test_every_page_has_a_declared_permission() -> None:
    """A page missing from the map is a page nothing hides — it would be
    listed for every role, and only the API would push back."""
    declared = {name.lower() for name in session.PAGE_PERMISSIONS}
    for path in PAGE_FILES:
        slug = re.sub(r"^\d+_", "", path.stem).replace("_", " ").lower()
        assert slug in declared, path.name


@pytest.mark.parametrize("path", PAGE_FILES, ids=lambda p: p.name)
def test_every_page_gates_itself(path: Path) -> None:
    """Streamlit publishes every file in ``pages/`` as a URL. A page that
    forgets the gate is reachable by typing its name."""
    assert "session.require(" in path.read_text(), path.name


# ── Things the source must never contain ─────────────────────────────


@pytest.mark.parametrize("path", [*PAGE_FILES, APP], ids=lambda p: p.name)
def test_no_page_writes_a_token_to_the_browser(path: Path) -> None:
    """Session state is server-side; browser storage is not. The whole reason
    a refresh signs you out is that nothing token-shaped leaves the server."""
    source = path.read_text().lower()
    assert "localstorage" not in source
    assert "sessionstorage" not in source


#: A query, rather than any word that happens to appear in prose.
SQL = re.compile(r"\bselect\b.{0,200}?\bfrom\b|\bgroup\s+by\b|\bwhere\b\s+\w+\s*=", re.I | re.S)


@pytest.mark.parametrize("path", [*PAGE_FILES, APP], ids=lambda p: p.name)
def test_no_page_composes_sql(path: Path) -> None:
    """The console asks for metric names; the API owns the query.

    A page that assembled even a fragment of a WHERE clause would put a string
    the user typed onto the path to the warehouse — the exact thing the metric
    registry exists to make impossible.
    """
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert not SQL.search(node.value), f"{path.name}: {node.value[:80]}"


def test_caveats_are_not_rendered_behind_a_disclosure_triangle() -> None:
    """The property the whole package exists for, pinned at the source.

    A behavioural test catches today's pages; this catches the well-meaning
    refactor that tidies caveats into a collapsed block to shorten a busy
    screen, which is where a qualification goes to be ignored.
    """
    body = inspect.getsource(components.caveats)
    for hider in ("st.expander", "st.popover", "st.status"):
        assert hider not in body


def test_the_absence_helper_always_asks_for_a_reason() -> None:
    """An empty state with no reason is the bug it exists to prevent."""
    assert "reason" in components.empty.__code__.co_varnames


# ── Sign-in ──────────────────────────────────────────────────────────


def test_a_signed_out_visitor_gets_the_form_and_nothing_else(console: Any) -> None:
    app = console()
    assert app.text_input
    assert not app.metric


def test_a_failed_sign_in_shows_the_apis_own_message(console: Any, texts: Any) -> None:
    """ "Invalid credentials" is wrong for a locked account and wrong again for
    a stopped backend, and those need different responses from the person
    reading it."""

    class Refusing:
        tokens = None

        def login(self, email: str, password: str) -> None:
            raise ApiError(status=423, title="Account locked", detail="Account is locked.")

    app = console(client=Refusing())
    app.button[0].click().run()

    assert "locked" in texts(app.error)


# ── The chrome is on every page ──────────────────────────────────────


@pytest.mark.parametrize("name", ["5_Forecast.py", "9_Alerts.py", "10_Admin.py"])
def test_every_page_can_be_signed_out_of(page: Any, name: str) -> None:
    """Streamlit runs only the current page's script, so a sidebar built in
    `app.py` exists on `app.py` and nowhere else. A user who lands on a deep
    page would have no way out but closing the browser."""
    app = page(name)
    assert any(button.label == "Sign out" for button in app.sidebar.button)


def test_the_palette_never_disagrees_with_the_widgets() -> None:
    """There is no in-app theme switch, and that is deliberate.

    Swapping CSS variables cannot restyle a dataframe — the grid paints itself
    onto a canvas from JavaScript using Streamlit's compiled theme — so a
    "light mode" built that way yields a white page with grey-on-white metrics
    and a dark table. The palette is chosen from the session's actual theme
    instead, which cannot drift from what Streamlit has drawn.
    """
    assert not hasattr(theme, "toggle")
    assert set(theme.PALETTES) == {"dark", "light"}
    assert theme.current() in theme.PALETTES


def test_the_palette_follows_the_configured_base(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deliberately *not* `st.context.theme`: that reports the viewer's browser
    preference, and says "dark" for a light-configured app opened in a dark
    browser — which would paint a dark page around light widgets."""
    monkeypatch.setattr(
        config, "get_option", lambda name: "light" if name == "theme.base" else None
    )
    assert theme.current() == "light"


# ── Navigation ───────────────────────────────────────────────────────


def test_a_signed_out_visitor_is_not_shown_the_map_of_the_product(console: Any) -> None:
    """Streamlit publishes every file in `pages/` to everyone by default.
    Declaring the navigation is what stops an anonymous visitor seeing ten
    areas they cannot open — and stops a store manager seeing six."""
    app = console()
    assert not app.sidebar.button
    assert not app.sidebar.toggle


def test_navigation_carries_only_the_areas_a_role_can_open(
    console: Any, ceo: dict[str, Any], texts: Any
) -> None:
    app = console(user={**ceo, "permissions": ["analytics.revenue.read"]})
    body = texts(app.markdown)
    assert "Sales" in body
    assert "Inventory" not in body


def test_walking_into_a_page_through_the_navigation_works(
    console: Any, ceo: dict[str, Any]
) -> None:
    """The entry point and the page both call `set_page_config`, and under
    `st.navigation` they run inside the same script execution. Worth pinning:
    running a page file directly — which every other test here does — would
    never exercise that."""
    app = console(
        user=ceo,
        responses={"/api/v1/forecasts/revenue": {"data": [], "caveats": []}},
    )
    app.switch_page("pages/5_Forecast.py").run()
    assert not app.exception
