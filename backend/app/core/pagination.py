"""Pagination for analytics results (Backend design §22).

Two mechanisms, chosen per surface rather than picked once and applied badly:

**Offset pagination** for aggregate analytics. Grouped results are small and
bounded — hundreds of rows, not millions — and a caller paging through
categories expects stable page numbers. The usual objection to OFFSET is deep
pagination on large tables, which cannot arise here because the group-by
result is the whole population.

**Keyset pagination** for OLTP lists (alert inbox, recommendations), where the
table *is* large and page 400 must cost the same as page 1. That machinery
lives with those endpoints; this module provides the shared page envelope both
use, so clients see one shape.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, Field

MAX_PAGE_SIZE = 500
DEFAULT_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class PageParams:
    """Validated paging window."""

    limit: int = DEFAULT_PAGE_SIZE
    offset: int = 0

    @property
    def next_offset(self) -> int:
        return self.offset + self.limit


def page_params(
    limit: Annotated[
        int,
        Query(ge=1, le=MAX_PAGE_SIZE, description="Rows per page (max 500)."),
    ] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0, description="Rows to skip.")] = 0,
) -> PageParams:
    """FastAPI dependency supplying paging to any endpoint that needs it."""
    return PageParams(limit=limit, offset=offset)


class Page[T](BaseModel):
    """The page envelope every list endpoint returns.

    ``has_more`` is derived by asking for one row beyond the page and checking
    whether it arrived — cheaper and more honest than a COUNT(*), which on a
    grouped query means running the whole aggregation twice.
    """

    items: list[T]
    limit: int
    offset: int
    has_more: bool = Field(description="True when further rows exist beyond this page.")
    next_offset: int | None = Field(
        default=None, description="Offset for the next page, or null at the end."
    )

    @classmethod
    def build(cls, items: list[T], params: PageParams, *, has_more: bool) -> "Page[T]":
        return cls(
            items=items,
            limit=params.limit,
            offset=params.offset,
            has_more=has_more,
            next_offset=params.next_offset if has_more else None,
        )
