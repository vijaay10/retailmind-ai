"""Configuration and the permission map."""

import pytest

from retailmind_ui import session
from retailmind_ui.api import DEFAULT_BASE_URL


def test_a_checkout_with_no_secrets_file_still_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    """`st.secrets` raises when no secrets.toml exists rather than returning
    the default — so reading it unguarded breaks the deployment least likely
    to have one, on the first click."""
    monkeypatch.delenv("RM_API_BASE_URL", raising=False)
    assert session.api_base_url() == DEFAULT_BASE_URL


def test_the_api_url_is_configurable_without_editing_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RM_API_BASE_URL", "https://api.internal.example")
    assert session.api_base_url() == "https://api.internal.example"


def test_no_area_is_visible_without_a_permission() -> None:
    """An area with no permission would be listed for every role, including
    roles the API will refuse — an invitation to a 403."""
    assert all(session.PAGE_PERMISSIONS.values())
    assert len(set(session.PAGE_PERMISSIONS)) == len(session.PAGE_PERMISSIONS)
