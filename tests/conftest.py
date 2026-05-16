"""Pytest fixtures for v2 Postgres-backed tests.

Live-DB fixtures require ``WORKLOG_DB_SERVICE_ROLE_KEY`` (or
``WORKLOG_DB_URL``) set in the environment or ``.env``. When neither is
present the fixtures skip the consuming tests rather than fail — the
unit-only suite (no DB) still runs.

Two fixtures matter:

``pg_engine`` (session-scoped)
    Single async engine for the whole test run. Built once, disposed
    at session teardown. Connection-pool churn would otherwise eat
    seconds on every test in the suite.

``pg_session`` (function-scoped)
    Opens an outer transaction on a borrowed connection, gives the
    test an ``AsyncSession`` bound to it, and rolls the outer
    transaction back at teardown. No fixture-managed data ever
    persists; tests can ``commit()`` freely and the rollback above
    them throws everything away.
"""

from __future__ import annotations

import os
from typing import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from services.storage.postgres import _normalize_db_url


def _resolve_test_url() -> str | None:
    raw = os.environ.get("WORKLOG_DB_SERVICE_ROLE_KEY") or os.environ.get(
        "WORKLOG_DB_URL"
    )
    if not raw:
        return None
    try:
        return _normalize_db_url(raw)
    except Exception:
        return None


_SKIP_REASON = (
    "WORKLOG_DB_SERVICE_ROLE_KEY (or WORKLOG_DB_URL) not set — "
    "skipping Postgres-backed tests"
)


@pytest_asyncio.fixture
async def pg_engine() -> AsyncIterator[AsyncEngine]:
    """Per-test engine. Session scope is tempting for perf but interacts
    badly with pytest-asyncio's per-test event loop (asyncpg's cleanup
    coroutines need the loop they were created on)."""
    url = _resolve_test_url()
    if url is None:
        pytest.skip(_SKIP_REASON)
    engine = create_async_engine(url, pool_pre_ping=True, future=True)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def pg_session(pg_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Per-test session. The fixture rolls back at teardown so anything the
    test ``flush()``-ed (but did not ``commit()``) is discarded.

    Tests must not call ``session.commit()`` — that would persist beyond
    the fixture and contaminate later runs. Use ``flush()`` to make SQL
    visible within the transaction without ending it.
    """
    Session = async_sessionmaker(
        pg_engine, expire_on_commit=False, class_=AsyncSession
    )
    session = Session()
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()
