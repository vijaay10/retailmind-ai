"""Reference seed — ships with the product, safe in every environment (DB class 1).

Idempotent by construction: keyed upserts on business keys, re-runnable at
every deploy. The role catalog is derived from the domain matrix
(``app.domain.auth.permissions``) so the database can never disagree with the
authorization code about which roles exist.

Run with:  ``python -m app.infrastructure.db.seeds.reference``
"""

import asyncio

import structlog
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.auth.permissions import ROLE_DESCRIPTIONS, RoleKey
from app.infrastructure.db.models import Role
from app.infrastructure.db.session import create_engine, create_session_factory, session_scope

log = structlog.get_logger(__name__)

# Stable ids — they are a contract: user_role rows and the 0002 migration both
# depend on this ordering, so append new roles, never renumber existing ones.
ROLE_IDS: dict[RoleKey, int] = {
    RoleKey.ADMIN: 1,
    RoleKey.REGIONAL_MANAGER: 2,
    RoleKey.CEO: 3,
    RoleKey.STORE_MANAGER: 4,
    RoleKey.MARKETING: 5,
    RoleKey.INVENTORY: 6,
    RoleKey.FINANCE: 7,
}


async def seed_reference(session: AsyncSession) -> None:
    """Upsert the role catalog (on conflict → refresh key and description)."""
    rows = [
        {"id": role_id, "key": role.value, "description": ROLE_DESCRIPTIONS[role]}
        for role, role_id in ROLE_IDS.items()
    ]
    stmt = insert(Role).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Role.id],
        set_={"key": stmt.excluded.key, "description": stmt.excluded.description},
    )
    await session.execute(stmt)
    log.info("seed.reference.done", roles=len(rows))


async def main() -> None:
    engine = create_engine()
    factory = create_session_factory(engine)
    async with session_scope(factory) as session:
        await seed_reference(session)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
