"""Async SQLAlchemy 2.0 engine / session helpers.

The engine factory transparently supports both production PostgreSQL
(``postgresql+asyncpg://``) and the zero-config SQLite fallback
(``sqlite+aiosqlite://``) used in demo mode and tests. For in-memory SQLite we
pin a :class:`~sqlalchemy.pool.StaticPool` so every connection shares the same
database, which is what makes an ``:memory:`` engine usable across a request.

Each *service* defines its own declarative ``Base`` (its own schema); this
module deliberately does not, so services stay independently migratable.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool


def _is_memory_sqlite(url: str) -> bool:
    return url.startswith("sqlite") and (":memory:" in url or url.endswith("://"))


def make_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    """Create an :class:`AsyncEngine` for the given URL with sane defaults."""
    connect_args: dict = {}
    kwargs: dict = {"echo": echo, "pool_pre_ping": True}

    if database_url.startswith("sqlite"):
        # aiosqlite drives a single background thread; disable the same-thread
        # guard so the async session can hand connections across tasks.
        connect_args["check_same_thread"] = False
        if _is_memory_sqlite(database_url):
            # A shared in-memory DB requires a single, permanently-held
            # connection; StaticPool provides exactly that.
            kwargs["poolclass"] = StaticPool
            kwargs.pop("pool_pre_ping", None)

    return create_async_engine(database_url, connect_args=connect_args, **kwargs)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory. ``expire_on_commit=False`` keeps ORM objects usable
    after commit (handy when returning them straight into a response model)."""
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
