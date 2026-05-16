"""Async SQLAlchemy engine + session helpers for Supabase Cloud Postgres.

Env vars (see v2 architecture.md §11.1):

    WORKLOG_DB_URL
        Per-user connection string.
        Example: postgresql+asyncpg://wl_user:pwd@db.<project>.supabase.co:5432/postgres

    WORKLOG_DB_SERVICE_ROLE_KEY
        Admin connection string used only by Alembic migrations and
        `wl team add|remove` provisioning. Never opened during ordinary
        CLI sessions.

    WORKLOG_DB_POOL_SIZE   (default 5)
    WORKLOG_DB_POOL_OVERFLOW (default 5)

Sessions opened via ``get_session(user_role)`` wrap a transaction
that issues ``SET LOCAL ROLE`` so Postgres RLS policies (see v2
architecture.md §11.2) evaluate against the acting human user. The
bootstrap engine connects under a service role; per-transaction
``SET LOCAL ROLE`` is the row-security switch.

This module exposes a small surface — engines are process-global and
explicit init/shutdown keeps test fixtures honest.
"""

from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.sql import text

# .env is loaded by services/__init__.py at package import time. Don't
# duplicate the load here — it would re-walk the filesystem on every
# session open.


class StorageNotInitialized(RuntimeError):
    """Raised when get_session() is called before init_engine()."""


class StorageMisconfigured(RuntimeError):
    """Raised when WORKLOG_DB_URL is missing or malformed."""


_DRIVER_PREFIX = "postgresql+asyncpg://"
_VALID_ROLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _normalize_db_url(raw: str) -> str:
    """Coerce a plain `postgres(ql)://` URL onto the asyncpg driver."""
    if raw.startswith(_DRIVER_PREFIX):
        return raw
    if raw.startswith("postgresql://"):
        return _DRIVER_PREFIX + raw[len("postgresql://") :]
    if raw.startswith("postgres://"):
        return _DRIVER_PREFIX + raw[len("postgres://") :]
    raise StorageMisconfigured(
        f"WORKLOG_DB_URL must be a postgres URL, got: {raw!r}"
    )


def _resolve_db_url() -> str:
    raw = os.environ.get("WORKLOG_DB_URL")
    if not raw:
        raise StorageMisconfigured(
            "WORKLOG_DB_URL is not set. Point it at your Supabase Postgres "
            "(format: postgresql+asyncpg://user:pwd@host:5432/postgres)."
        )
    return _normalize_db_url(raw)


def _pool_size() -> int:
    try:
        return max(1, int(os.environ.get("WORKLOG_DB_POOL_SIZE", "5")))
    except ValueError as exc:
        raise StorageMisconfigured(f"WORKLOG_DB_POOL_SIZE invalid: {exc}") from exc


def _pool_overflow() -> int:
    try:
        return max(0, int(os.environ.get("WORKLOG_DB_POOL_OVERFLOW", "5")))
    except ValueError as exc:
        raise StorageMisconfigured(f"WORKLOG_DB_POOL_OVERFLOW invalid: {exc}") from exc


async def init_engine(url: str | None = None) -> AsyncEngine:
    """Create the process-wide async engine. Idempotent: re-entry returns the cached engine."""
    global _engine, _sessionmaker
    if _engine is not None:
        return _engine
    resolved = _normalize_db_url(url) if url else _resolve_db_url()
    _engine = create_async_engine(
        resolved,
        pool_size=_pool_size(),
        max_overflow=_pool_overflow(),
        pool_pre_ping=True,
        future=True,
    )
    _sessionmaker = async_sessionmaker(
        _engine, expire_on_commit=False, class_=AsyncSession
    )
    return _engine


async def shutdown_engine() -> None:
    """Dispose the engine on process exit. No-op if init_engine() was never called."""
    global _engine, _sessionmaker
    if _engine is None:
        return
    await _engine.dispose()
    _engine = None
    _sessionmaker = None


def _validate_role(role: str) -> str:
    """Postgres identifiers cannot be parameter-bound; validate the shape instead."""
    if not _VALID_ROLE.match(role):
        raise StorageMisconfigured(
            f"Refusing to SET ROLE {role!r}: must match {_VALID_ROLE.pattern}"
        )
    return role


@asynccontextmanager
async def get_session(user_role: str | None = None) -> AsyncIterator[AsyncSession]:
    """Open an AsyncSession in a transaction.

    If ``user_role`` is provided, the transaction starts with
    ``SET LOCAL ROLE <role>`` so RLS policies evaluate against that
    role. The role-switch is scoped to the transaction; the underlying
    pooled connection returns to the service-role default on commit
    or rollback.

    Commits on clean exit, rolls back on any exception.
    """
    if _sessionmaker is None:
        raise StorageNotInitialized(
            "Call init_engine() before opening a session."
        )

    async with _sessionmaker() as session:
        async with session.begin():
            if user_role is not None:
                role = _validate_role(user_role)
                await session.execute(text(f"SET LOCAL ROLE {role}"))
            yield session
