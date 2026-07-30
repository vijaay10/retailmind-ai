"""Reference seed — ships with the product, safe in every environment (DB §28 class 1).

Idempotent by construction: keyed upserts on business keys, re-runnable at
every deploy. Run with:  ``python -m app.infrastructure.db.seeds.reference``
"""

import asyncio

import structlog
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import Role
from app.infrastructure.db.session import create_engine, create_session_factory, session_scope

log = structlog.get_logger(__name__)

# Fixed ids: the role catalog is a closed set (DB §34); ids are stable contract.
ROLES: list[dict[str, object]] = [
    {"id": 1, "key": "admin", "description": "Workspace administration, config, budgets, audit"},
    {"id": 2, "key": "analyst", "description": "Query, investigate, act on recommendations"},
    {"id": 3, "key": "viewer", "description": "Read dashboards and reports; no export"},
]


async def seed_reference(session: AsyncSession) -> None:
    """Upsert the role catalog (ON CONFLICT key → refresh description only)."""
    stmt = insert(Role).values(ROLES)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Role.key],
        set_={"description": stmt.excluded.description},
    )
    await session.execute(stmt)
    log.info("seed.reference.done", roles=len(ROLES))


async def main() -> None:
    engine = create_engine()
    factory = create_session_factory(engine)
    async with session_scope(factory) as session:
        await seed_reference(session)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
