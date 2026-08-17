"""Unit of Work — explicit transaction control for services.

The request-scoped session commits once, at the end of a successful request
(``app.api.deps.get_session``). That is the right default: a handler that
fails leaves no partial writes.

Security-ledger writes are the documented exception. A failed login records
``login.failed`` and *then* raises; a detected token replay revokes the family
and *then* raises. If those writes rolled back with the exception, lockout
would never count a failure and a stolen session would survive its own
detection. :meth:`commit_now` is how a service says "this fact is true
regardless of how the request ends".

Use it sparingly and only for that reason — reaching for it to work around
awkward ordering is how partial writes get normalized.
"""

from sqlalchemy.ext.asyncio import AsyncSession


class UnitOfWork:
    """Thin, explicit wrapper over the request session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        return self._session

    async def flush(self) -> None:
        """Push pending changes to the database without ending the transaction.

        Used when a subsequent statement needs a database-generated id.
        """
        await self._session.flush()

    async def commit_now(self) -> None:
        """Durably commit work in progress, mid-request.

        See the module docstring: this exists for audit and revocation writes
        that must outlive the exception being raised immediately after them.
        """
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
