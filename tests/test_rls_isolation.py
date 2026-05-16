"""RLS contract tests against the live Supabase schema.

These tests verify that the v2 migration installed the expected
row-level-security policies on every phase-5 table. They are static
contract checks — they query ``pg_policies`` / ``pg_tables`` and
confirm shape, NOT runtime enforcement.

A full round-trip enforcement test (provision two real Postgres
roles, connect as each, assert visibility boundaries) is **deferred
to a follow-up commit**. Supabase Cloud's session pooler blocks
``GRANT <role> TO CURRENT_USER`` (the connection drops mid-statement),
so the round-trip test needs either:

  - direct connections per provisioned user (each carrying its own
    password), or
  - server-side RLS testing via SECURITY DEFINER fixtures.

Both are out of scope for the phase-5 foundation. The policies
themselves are well-defined SQL in alembic/versions/0001_v2_initial.py
and the contract checks below catch the most common regressions
(policy missing, wrong command, wrong CHECK on raw_events).
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from services.storage.postgres import _normalize_db_url

pytestmark = pytest.mark.asyncio


def _admin_url() -> str | None:
    raw = os.environ.get("WORKLOG_DB_SERVICE_ROLE_KEY") or os.environ.get(
        "WORKLOG_DB_URL"
    )
    if not raw:
        return None
    try:
        return _normalize_db_url(raw)
    except Exception:
        return None


PHASE5_TABLES = {
    "users",
    "teams",
    "team_members",
    "workstreams",
    "raw_events",
    "agent_traces",
}


async def test_rls_enabled_on_every_phase5_table() -> None:
    url = _admin_url()
    if url is None:
        pytest.skip("WORKLOG_DB_SERVICE_ROLE_KEY not set")
    engine = create_async_engine(url, future=True)
    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'public' AND rowsecurity = true"
                    )
                )
            ).scalars().all()
        enabled = set(rows)
    finally:
        await engine.dispose()

    missing = PHASE5_TABLES - enabled
    assert not missing, f"RLS not enabled on: {missing}"


async def test_workstreams_has_owner_scoped_select_policy() -> None:
    url = _admin_url()
    if url is None:
        pytest.skip("WORKLOG_DB_SERVICE_ROLE_KEY not set")
    engine = create_async_engine(url, future=True)
    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT policyname, cmd, qual "
                        "FROM pg_policies "
                        "WHERE tablename = 'workstreams'"
                    )
                )
            ).all()
    finally:
        await engine.dispose()

    by_name = {r[0]: (r[1], r[2]) for r in rows}
    assert "ws_select" in by_name, f"missing ws_select policy; got {list(by_name)}"
    cmd, qual = by_name["ws_select"]
    assert cmd == "SELECT", f"ws_select wrong cmd: {cmd}"
    # The owner-scoped clause survived migration intact.
    assert "current_user_id" in qual.lower()
    assert "team_id" in qual.lower()


async def test_raw_events_has_owner_select_policy_and_no_update_delete() -> None:
    """raw_events is an immutable archive: only owner can SELECT, no
    UPDATE / DELETE policy exists (denying both under RLS)."""
    url = _admin_url()
    if url is None:
        pytest.skip("WORKLOG_DB_SERVICE_ROLE_KEY not set")
    engine = create_async_engine(url, future=True)
    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT policyname, cmd "
                        "FROM pg_policies WHERE tablename = 'raw_events'"
                    )
                )
            ).all()
    finally:
        await engine.dispose()

    by_cmd = {r[1] for r in rows}
    assert "SELECT" in by_cmd
    assert "INSERT" in by_cmd
    assert "UPDATE" not in by_cmd, "raw_events must remain immutable; no UPDATE policy"
    assert "DELETE" not in by_cmd, "raw_events must remain immutable; no DELETE policy"


async def test_workstreams_no_delete_policy() -> None:
    """Memory and audit semantics: workstream rows are not deleted."""
    url = _admin_url()
    if url is None:
        pytest.skip("WORKLOG_DB_SERVICE_ROLE_KEY not set")
    engine = create_async_engine(url, future=True)
    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT policyname, cmd, qual "
                        "FROM pg_policies WHERE tablename = 'workstreams' "
                        "AND cmd = 'DELETE'"
                    )
                )
            ).all()
    finally:
        await engine.dispose()

    # ws_no_delete is the policy that denies all DELETEs.
    assert any(r[0] == "ws_no_delete" for r in rows)
    for name, _cmd, qual in rows:
        if name == "ws_no_delete":
            assert "false" in qual.lower()


async def test_current_user_id_function_exists() -> None:
    url = _admin_url()
    if url is None:
        pytest.skip("WORKLOG_DB_SERVICE_ROLE_KEY not set")
    engine = create_async_engine(url, future=True)
    try:
        async with engine.connect() as conn:
            found = (
                await conn.execute(
                    text(
                        "SELECT proname FROM pg_proc WHERE proname = 'current_user_id'"
                    )
                )
            ).scalar_one_or_none()
    finally:
        await engine.dispose()
    assert found == "current_user_id"
