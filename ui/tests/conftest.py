"""Page-level test harness.

Pages are run through Streamlit's own ``AppTest``, which executes the real
script against a fake API client. That is the only way to test what this
console is actually for: not that a function returns a value, but that a
qualification the API attached still appears on the screen the user reads.
"""

from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from retailmind_ui.api import ApiError, Tokens

PAGES = Path(__file__).resolve().parents[1] / "pages"

CEO = {
    "email": "priya@northwind.example",
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


class FakeApi:
    """Stands in for ``ApiClient``, returning canned bodies per path.

    Raising ``ApiError`` for a path is as important as returning a body: a
    console that renders a blank dashboard when the backend is down is telling
    the reader the business has no revenue.
    """

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.responses = responses or {}
        self.tokens = Tokens(access="a", refresh="r")
        self.calls: list[str] = []

    def _resolve(self, path: str) -> dict[str, Any]:
        self.calls.append(path)
        body = self.responses.get(path, {})
        if isinstance(body, ApiError):
            raise body
        return dict(body)

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        return self._resolve(path)

    def post(self, path: str, **json: Any) -> dict[str, Any]:
        return self._resolve(path)

    def download(self, path: str, **params: Any) -> tuple[bytes, str]:
        self._resolve(path)
        return b"%PDF-1.7", "application/pdf"

    def me(self) -> dict[str, Any]:
        return CEO

    def logout(self) -> None:
        self.tokens = None  # type: ignore[assignment]


@pytest.fixture
def ceo() -> dict[str, Any]:
    return dict(CEO)


@pytest.fixture
def texts():  # type: ignore[no-untyped-def]
    """Everything a group of elements says, as one searchable string."""

    def collect(elements: Any) -> str:
        return "\n".join(str(getattr(item, "value", "")) for item in elements)

    return collect


def _script(
    path: Path,
    *,
    responses: dict[str, Any] | None = None,
    user: dict[str, Any] | None = CEO,
    client: Any = None,
    run: bool = True,
) -> AppTest:
    app = AppTest.from_file(str(path), default_timeout=30)
    app.session_state["rm_client"] = client if client is not None else FakeApi(responses)
    if user is not None:
        app.session_state["rm_user"] = user
    return app.run() if run else app


@pytest.fixture
def page():  # type: ignore[no-untyped-def]
    """Run a page as a given user with a given set of API responses."""

    def run(name: str, **kwargs: Any) -> AppTest:
        return _script(PAGES / name, **kwargs)

    return run


@pytest.fixture
def console():  # type: ignore[no-untyped-def]
    """Run the entry point — sign-in form, sidebar, and landing."""

    def run(**kwargs: Any) -> AppTest:
        kwargs.setdefault("user", None)
        return _script(PAGES.parent / "app.py", **kwargs)

    return run
