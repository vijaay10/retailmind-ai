"""Number and date formatting.

The only computation in this package, and deliberately so: everything else is
rendered exactly as the API returned it. Formatting a number is presentation;
deriving one would be a second implementation of a metric that already has one.
"""

from datetime import UTC, date, datetime
from typing import Any


def number(value: Any, unit: str = "", places: int = 0) -> str:
    """Format a value for display, honouring the API's declared unit."""
    if value is None:
        return "—"
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return str(value)

    if unit == "rate":
        return f"{amount:.1%}"
    if unit in {"ratio", "z"}:
        return f"{amount:,.2f}"
    if unit == "days":
        return f"{amount:,.1f}"
    if unit == "currency":
        return f"{amount:,.0f}"
    return f"{amount:,.{places}f}"


def delta(value: Any) -> str | None:
    """A signed percentage for a metric tile, or nothing.

    Returns ``None`` rather than "0.0%" when there is no comparison. A tile
    showing a change of zero and a tile showing no comparison at all mean
    different things, and Streamlit renders the absence correctly.
    """
    if value is None:
        return None
    try:
        return f"{float(value):+.1%}"
    except (TypeError, ValueError):
        return None


def label(key: str) -> str:
    """Turn a registry key into something a person reads."""
    return key.replace("_", " ").strip().title()


def day(value: Any) -> str:
    if isinstance(value, date | datetime):
        return value.strftime("%d %b %Y")
    text = str(value or "")
    try:
        return date.fromisoformat(text[:10]).strftime("%d %b %Y")
    except ValueError:
        return text


def truncate(text: str, limit: int = 90) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def relative_time(value: Any) -> str:
    """How long ago, in the units a person would say it in.

    Decisions and alerts are read in terms of recency — "accepted 2 hours ago"
    tells a manager whether the queue is being worked; an ISO timestamp makes
    them do the subtraction.
    """
    if not value:
        return ""
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    seconds = (datetime.now(tz=UTC) - moment).total_seconds()

    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86_400:
        return f"{int(seconds // 3600)} h ago"
    if seconds < 7 * 86_400:
        return f"{int(seconds // 86_400)} d ago"
    return moment.strftime("%d %b")


def signed_rate(value: Any) -> str:
    """A signed percentage, or an em dash when there is no comparison."""
    if value is None:
        return "—"
    try:
        return f"{float(value):+.1%}"
    except (TypeError, ValueError):
        return "—"


def compact(value: Any) -> str:
    """Large figures at a glance: 1.2M, 340k.

    Used only where a headline must fit one line — never in a table, where
    scanning columns needs a consistent number of digits.
    """
    if value is None:
        return "—"
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return str(value)

    for threshold, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(amount) >= threshold:
            return f"{amount / threshold:,.1f}{suffix}"
    return f"{amount:,.0f}"
