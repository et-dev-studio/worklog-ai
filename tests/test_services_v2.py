"""Live-Postgres integration tests for the v2 service layer.

One representative flow per service. Each test uses the ``pg_session``
fixture; mutations roll back at teardown so nothing persists.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from db.models import RawEvent, RawEventKind, User, WorkstreamStatus
from services import event_service, reflection_service, summary_service
from services import user_service, workstream_service

pytestmark = pytest.mark.asyncio


async def _make_user(pg_session, label: str = "test") -> User:
    """Insert a User row directly (no Postgres role) — fast for tests
    that don't need RLS isolation."""
    user = User(
        pg_role=f"wl_test_{label}_{uuid.uuid4().hex[:8]}",
        display_name=f"Test {label}",
    )
    pg_session.add(user)
    await pg_session.flush()
    return user


# ---------------------------------------------------------------------------
# workstream_service
# ---------------------------------------------------------------------------


async def test_workstream_create_list_set_status(pg_session) -> None:
    owner = await _make_user(pg_session, "wsowner")

    ws = await workstream_service.create(
        pg_session,
        title=f"AUTH-{uuid.uuid4().hex[:6]} oauth retries",
        owner_id=owner.id,
        summary="redirect token expiry edge case",
    )
    assert ws.status == WorkstreamStatus.ACTIVE.value
    assert ws.visibility == "private"

    listed = await workstream_service.list_all(pg_session)
    assert any(w.id == ws.id for w in listed)

    updated = await workstream_service.set_status(
        pg_session, ws.id, WorkstreamStatus.PAUSED, actor_id=owner.id
    )
    assert updated is not None
    assert updated.status == WorkstreamStatus.PAUSED.value

    # Audit raw_event written by set_status.
    audit_rows = (
        await pg_session.execute(
            select(RawEvent)
            .where(RawEvent.workstream_id == ws.id)
            .where(RawEvent.kind == RawEventKind.STATUS_UPDATE.value)
        )
    ).scalars().all()
    assert len(audit_rows) == 1


# ---------------------------------------------------------------------------
# event_service
# ---------------------------------------------------------------------------


async def test_event_add_capture_updates_workstream_activity(pg_session) -> None:
    owner = await _make_user(pg_session, "capowner")
    ws = await workstream_service.create(
        pg_session,
        title=f"PLATFORM-{uuid.uuid4().hex[:6]} redis",
        owner_id=owner.id,
    )
    before = ws.last_activity_at

    captured = await event_service.add_capture(
        pg_session,
        content="Investigating Redis connection pool exhaustion",
        owner_id=owner.id,
        workstream_id=ws.id,
    )
    assert captured.kind == RawEventKind.CAPTURE.value
    assert captured.workstream_id == ws.id

    refreshed = await workstream_service.get(pg_session, ws.id)
    assert refreshed is not None
    assert refreshed.last_activity_at is not None
    assert refreshed.last_activity_at >= before


async def test_event_void_writes_voided_audit_row(pg_session) -> None:
    owner = await _make_user(pg_session, "voidowner")
    captured = await event_service.add_capture(
        pg_session, content="ephemeral note", owner_id=owner.id
    )
    voided = await event_service.void_event(
        pg_session, captured.id, actor_id=owner.id, reason="duplicate"
    )
    assert voided is not None

    targets = await event_service.voided_target_ids(pg_session)
    assert captured.id in targets

    # list_recent excludes voided captures.
    recent = await event_service.list_recent(pg_session, limit=20)
    assert all(e.id != captured.id for e in recent)


async def test_event_void_rejects_non_capture(pg_session) -> None:
    owner = await _make_user(pg_session, "rejectowner")
    ws = await workstream_service.create(
        pg_session,
        title=f"WS-{uuid.uuid4().hex[:6]}",
        owner_id=owner.id,
    )
    await workstream_service.set_status(
        pg_session, ws.id, WorkstreamStatus.PAUSED, actor_id=owner.id
    )
    status_event = (
        await pg_session.execute(
            select(RawEvent)
            .where(RawEvent.workstream_id == ws.id)
            .where(RawEvent.kind == RawEventKind.STATUS_UPDATE.value)
        )
    ).scalar_one()

    with pytest.raises(ValueError):
        await event_service.void_event(
            pg_session, status_event.id, actor_id=owner.id
        )


async def test_event_search_uses_tsvector(pg_session) -> None:
    owner = await _make_user(pg_session, "searchowner")
    needle = f"unique_marker_{uuid.uuid4().hex[:8]}"
    await event_service.add_capture(
        pg_session,
        content=f"Investigating {needle} in OAuth redirect handler",
        owner_id=owner.id,
    )
    await pg_session.flush()

    hits = await event_service.search(pg_session, needle, limit=5)
    assert any(needle in e.content for e in hits)


async def test_suggest_workstreams_keyword_overlap_wins(pg_session) -> None:
    owner = await _make_user(pg_session, "suggestowner")
    redis_ws = await workstream_service.create(
        pg_session,
        title=f"PLATFORM-{uuid.uuid4().hex[:6]} redis reliability",
        owner_id=owner.id,
    )
    other_ws = await workstream_service.create(
        pg_session,
        title=f"BILLING-{uuid.uuid4().hex[:6]} invoice export",
        owner_id=owner.id,
    )
    # Both workstreams created same instant; ranking must come from keyword
    # overlap, not recency.
    suggestions = await event_service.suggest_workstreams(
        pg_session, "redis timeout investigation", limit=2
    )
    ids = [w.id for w in suggestions]
    assert redis_ws.id in ids
    assert ids.index(redis_ws.id) <= ids.index(other_ws.id) if other_ws.id in ids else True


# ---------------------------------------------------------------------------
# reflection_service
# ---------------------------------------------------------------------------


async def test_reflection_unresolved_captures_ranks_unresolved_first(pg_session) -> None:
    owner = await _make_user(pg_session, "reflowner")
    # Two captures: one short + unassigned + "investigating" (high priority);
    # one long + workstream + "fixed" (low/negative priority).
    high = await event_service.add_capture(
        pg_session, content="investigating bug", owner_id=owner.id
    )
    ws = await workstream_service.create(
        pg_session,
        title=f"WS-{uuid.uuid4().hex[:6]} long",
        owner_id=owner.id,
    )
    # Trigger STATUS_UPDATE so the second capture's workstream has recent activity.
    await workstream_service.set_status(
        pg_session, ws.id, WorkstreamStatus.PAUSED, actor_id=owner.id
    )
    low = await event_service.add_capture(
        pg_session,
        content="Wrote integration test for the fixed migration runner today",
        owner_id=owner.id,
        workstream_id=ws.id,
    )

    ranked = await reflection_service.unresolved_captures(pg_session, limit=5)
    ids = [e.id for e in ranked]
    if high.id in ids and low.id in ids:
        assert ids.index(high.id) < ids.index(low.id)
    else:
        assert high.id in ids


# ---------------------------------------------------------------------------
# summary_service
# ---------------------------------------------------------------------------


async def test_summary_daily_events_respects_day_boundary(pg_session) -> None:
    owner = await _make_user(pg_session, "sumowner")
    today = datetime.now(UTC).date()
    # Insert an event "today" via add_capture.
    cap = await event_service.add_capture(
        pg_session, content=f"summary-day marker {uuid.uuid4().hex}", owner_id=owner.id
    )

    same_day = await summary_service.daily_events(pg_session, today)
    assert any(e.id == cap.id for e in same_day)

    next_day = await summary_service.daily_events(pg_session, today + timedelta(days=1))
    assert all(e.id != cap.id for e in next_day)


async def test_summary_save_writes_summary_generated_audit(pg_session) -> None:
    owner = await _make_user(pg_session, "saveowner")
    today = datetime.now(UTC).date()
    saved = await summary_service.save_summary(
        pg_session,
        content="### Today\n- one event",
        day=today,
        actor_id=owner.id,
        decision="yes",
        edited=False,
    )
    assert saved.kind == RawEventKind.SUMMARY_GENERATED.value
    assert saved.event_metadata["date"] == today.isoformat()
    assert saved.event_metadata["decision"] == "yes"
    assert saved.event_metadata["edited"] is False


# ---------------------------------------------------------------------------
# user_service
# ---------------------------------------------------------------------------


async def test_user_create_then_lookup(pg_session) -> None:
    role = f"wl_test_lookup_{uuid.uuid4().hex[:8]}"
    user, password = await user_service.create_user(
        pg_session,
        display_name="Lookup Tester",
        pg_role=role,
        create_pg_role=False,  # don't touch CREATE ROLE — needs admin
    )
    assert password is None  # not generated when create_pg_role=False
    found = await user_service.get_by_pg_role(pg_session, role)
    assert found is not None
    assert found.id == user.id


async def test_team_create_owner_appears_in_members(pg_session) -> None:
    from db.models import TeamMember

    owner = await _make_user(pg_session, "teamowner")
    team = await user_service.create_team(
        pg_session, name=f"team-{uuid.uuid4().hex[:6]}", owner=owner
    )
    # team.members is an async-lazy relationship — query directly.
    members = (
        await pg_session.execute(
            select(TeamMember).where(TeamMember.team_id == team.id)
        )
    ).scalars().all()
    assert len(members) == 1
    assert members[0].user_id == owner.id
