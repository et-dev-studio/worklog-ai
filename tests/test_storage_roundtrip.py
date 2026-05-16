"""Live-Postgres round-trip smoke tests against the v2 schema.

These tests require ``WORKLOG_DB_SERVICE_ROLE_KEY`` (or ``WORKLOG_DB_URL``)
to point at a database with the ``0001_v2_initial`` migration applied.
The ``pg_session`` fixture rolls back its outer transaction at teardown
so nothing the tests insert persists.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from db.models import RawEvent, RawEventKind, User, Workstream

pytestmark = pytest.mark.asyncio


async def test_user_round_trip(pg_session) -> None:
    role = f"wl_test_{uuid.uuid4().hex[:8]}"
    user = User(pg_role=role, display_name="Test User", email=f"{role}@example.test")
    pg_session.add(user)
    await pg_session.flush()

    fetched = (
        await pg_session.execute(select(User).where(User.pg_role == role))
    ).scalar_one()
    assert fetched.id == user.id
    assert fetched.display_name == "Test User"
    assert fetched.email == f"{role}@example.test"


async def test_workstream_round_trip(pg_session) -> None:
    role = f"wl_test_{uuid.uuid4().hex[:8]}"
    owner = User(pg_role=role, display_name="WS Owner")
    pg_session.add(owner)
    await pg_session.flush()

    ws = Workstream(
        title=f"phase-5-fixture-{uuid.uuid4().hex[:8]}",
        owner_id=owner.id,
    )
    pg_session.add(ws)
    await pg_session.flush()

    fetched = (
        await pg_session.execute(select(Workstream).where(Workstream.id == ws.id))
    ).scalar_one()
    assert fetched.title == ws.title
    assert fetched.status == "active"
    assert fetched.visibility == "private"
    assert fetched.owner_id == owner.id


async def test_raw_event_tsvector_populated(pg_session) -> None:
    """The generated tsvector column must be populated automatically."""
    role = f"wl_test_{uuid.uuid4().hex[:8]}"
    owner = User(pg_role=role, display_name="RE Owner")
    pg_session.add(owner)
    await pg_session.flush()

    event = RawEvent(
        kind=RawEventKind.CAPTURE.value,
        content="Investigating Redis timeout in OAuth redirect handler",
        owner_id=owner.id,
    )
    pg_session.add(event)
    await pg_session.flush()
    await pg_session.refresh(event)

    assert event.content_tsv is not None
    # tsvector renders as a string with token:position pairs.
    # Postgres' 'english' config stems aggressively ("redis" -> "redi",
    # "investigating" -> "investig"), so assert on tokens that are
    # stable under stemming.
    rendered = event.content_tsv.lower()
    assert "oauth" in rendered
    assert "timeout" in rendered


async def test_raw_event_kind_check_rejects_unknown(pg_session) -> None:
    """ck_raw_events_kind must block kinds outside the v1+v2 union."""
    role = f"wl_test_{uuid.uuid4().hex[:8]}"
    owner = User(pg_role=role, display_name="Kind Test")
    pg_session.add(owner)
    await pg_session.flush()

    bad = RawEvent(kind="not_a_real_kind", content="x", owner_id=owner.id)
    pg_session.add(bad)
    with pytest.raises(Exception):  # IntegrityError under asyncpg
        await pg_session.flush()
