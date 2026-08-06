"""Formatting is the only computation in the console, so it is pinned."""

from datetime import date

import pytest

from retailmind_ui.formatting import day, delta, label, number, truncate


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (1234567.4, "currency", "1,234,567"),
        (0.0834, "rate", "8.3%"),
        (1.5, "ratio", "1.50"),
        (12.34, "days", "12.3"),
        (42, "", "42"),
    ],
)
def test_units_render_the_way_the_api_declared_them(value: float, unit: str, expected: str) -> None:
    assert number(value, unit) == expected


def test_a_missing_value_is_an_em_dash_not_a_zero() -> None:
    """Zero is a measurement; missing is not, and they demand different actions."""
    assert number(None, "currency") == "—"


def test_a_non_numeric_value_passes_through_rather_than_crashing_a_page() -> None:
    assert number("n/a", "currency") == "n/a"


def test_no_comparison_renders_as_absence_not_as_no_change() -> None:
    assert delta(None) is None
    assert delta(0.0) == "+0.0%"


def test_registry_keys_become_readable() -> None:
    assert label("gross_margin_rate") == "Gross Margin Rate"


def test_dates_accept_both_objects_and_iso_timestamps() -> None:
    assert day(date(2026, 7, 21)) == "21 Jul 2026"
    assert day("2026-07-21T09:30:00Z") == "21 Jul 2026"
    assert day(None) == ""


def test_an_unparseable_date_is_shown_rather_than_hidden() -> None:
    assert day("last tuesday") == "last tuesday"


def test_truncation_keeps_the_limit() -> None:
    assert len(truncate("x" * 200, 40)) == 40
    assert truncate("short", 40) == "short"
