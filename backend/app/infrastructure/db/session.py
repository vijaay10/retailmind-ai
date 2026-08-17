"""Async engine and session factories.

One engine per process (DI container singleton,); sessions are
request-scoped and enlisted in the Unit of Work. ``raiseload("*")`` is applied
at query time by repositories — relationship loading is always explicit
(Backend: no lazy loading in async API code).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import DatabaseSettings


def create_engine(settings: DatabaseSettings | None = None) -> AsyncEngine:
    """Build the process-wide async engine from RM_DB_* settings."""
    settings = settings or DatabaseSettings()
    return create_async_engine(
        settings.async_dsn,
        pool_size=settings.pool_size,
        max_overflow=settings.pool_size,  # burst headroom = 2× sustained
        pool_pre_ping=True,  # recycle dead connections after failovers
        pool_timeout=10,
        echo=False,  # SQL visibility comes from pg logs, not stdout noise
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        engine,
        expire_on_commit=False,  # entities remain readable after UoW commit (mapper layer copies)
        autoflush=False,  # flushes happen at UoW boundaries, not mid-read
    )


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """One atomic unit: commit on success, rollback on any error (UoW primitive)."""
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
