"""Workspace test harness.

Workspaces run through Streamlit's own ``AppTest`` against a fake API. That is
the only way to test what this console is for: not that a function returns a
value, but that a qualification the API attached still reaches the screen a
person reads.
"""

from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from retailmind_ui.api import ApiError, Tokens

UI = Path(__file__).resolve().parents[1]
WORKSPACES = UI / "workspaces"

CEO = {
    "email": "priya@northwind.example",
    "display_name": "Priya Sharma",
    "roles": ["ceo"],
    "permissions": [
        "analytics.revenue.read",
        "analytics.customer.read",
        "analytics.inventory.read",
        "forecasts.read",
        "recommendations.read",
        "insights.read",
        "reports.read",
        "alerts.read",
    ],
}

MANAGER = {
    **CEO,
    "email": "marcus@northwind.example",
    "display_name": "Marcus Webb",
    "roles": ["regional_manager"],
    "permissions": [*CEO["permissions"], "recommendations.act"],
}


class FakeApi:
    """Stands in for ``ApiClient``, returning canned bodies per path.

    Raising ``ApiError`` for a path matters as much as returning one: a console
    that draws a blank dashboard when the backend is down is telling the reader
    the business has no revenue.
    """

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.responses = responses or {}
        self.tokens = Tokens(access="a", refresh="r")
        self.calls: list[tuple[str, str]] = []
        self.posted: list[dict[str, Any]] = []

    def _resolve(self, verb: str, path: str) -> dict[str, Any]:
        self.calls.append((verb, path))
        body = self.responses.get(path, {})
        if isinstance(body, ApiError):
            raise body
        return dict(body)

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        return self._resolve("GET", path)

    def post(self, path: str, **json: Any) -> dict[str, Any]:
        self.posted.append({"path": path, **json})
        return self._resolve("POST", path)

    def download(self, path: str, **params: Any) -> tuple[bytes, str]:
        self._resolve("GET", path)
        return b"%PDF-1.7", "application/pdf"

    def me(self) -> dict[str, Any]:
        return CEO

    def logout(self) -> None:
        self.tokens = None  # type: ignore[assignment]


def _script(
    path: Path,
    *,
    responses: dict[str, Any] | None = None,
    user: dict[str, Any] | None = CEO,
    client: Any = None,
    state: dict[str, Any] | None = None,
) -> AppTest:
    app = AppTest.from_file(str(path), default_timeout=60)
    app.session_state["rm_client"] = client if client is not None else FakeApi(responses)
    if user is not None:
        app.session_state["rm_user"] = user
    for key, value in (state or {}).items():
        app.session_state[key] = value
    return app.run()


@pytest.fixture
def ceo() -> dict[str, Any]:
    return dict(CEO)


@pytest.fixture
def manager() -> dict[str, Any]:
    return dict(MANAGER)


@pytest.fixture
def texts():  # type: ignore[no-untyped-def]
    """Everything a group of elements says, as one searchable string."""

    def collect(elements: Any) -> str:
        return "\n".join(str(getattr(item, "value", "")) for item in elements)

    return collect


@pytest.fixture
def markup():  # type: ignore[no-untyped-def]
    """All rendered markdown, including the design system's own fragments."""

    def collect(app: AppTest) -> str:
        return "\n".join(str(item.value) for item in app.markdown)

    return collect


@pytest.fixture
def api():  # type: ignore[no-untyped-def]
    """Build a fake client whose calls a test can inspect."""

    def build(responses: dict[str, Any] | None = None) -> FakeApi:
        return FakeApi(responses)

    return build


@pytest.fixture
def workspace():  # type: ignore[no-untyped-def]
    """Run one workspace."""

    def run(name: str, **kwargs: Any) -> AppTest:
        return _script(WORKSPACES / name, **kwargs)

    return run


@pytest.fixture
def console():  # type: ignore[no-untyped-def]
    """Run the entry point."""

    def run(**kwargs: Any) -> AppTest:
        kwargs.setdefault("user", None)
        return _script(UI / "app.py", **kwargs)

    return run
