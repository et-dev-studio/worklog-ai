"""Async SQLAlchemy engine + session helpers for Supabase Cloud Postgres.

Real implementation lands in phase-5 task #4. This skeleton exposes the
public surface so dependent modules can import from `services.storage`
during the transition without raising ImportError at collection time.

Env vars (see v2 architecture.md §11.1):
    WORKLOG_DB_URL                postgresql+asyncpg://user:pwd@host:5432/postgres
    WORKLOG_DB_SERVICE_ROLE_KEY   admin role connection string used for migrations
                                  and team provisioning only

Sessions opened via ``get_session(user_role)`` run ``SET LOCAL ROLE`` so
RLS policies evaluate against the acting human user.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator


class StorageNotInitialized(RuntimeError):
    """Raised when get_session() is called before init_engine()."""


async def init_engine() -> None:
    """Create the process-wide async engine. No-op until phase-5 task #4."""
    raise NotImplementedError("phase-5 task #4 — async engine wiring")


async def shutdown_engine() -> None:
    """Dispose the engine on process exit."""
    raise NotImplementedError("phase-5 task #4 — async engine wiring")


@asynccontextmanager
async def get_session(user_role: str | None = None) -> AsyncIterator[object]:
    """Yield an AsyncSession scoped to a transaction with SET LOCAL ROLE."""
    raise NotImplementedError("phase-5 task #4 — async session helper")
    yield  # pragma: no cover  (required for asynccontextmanager type)
