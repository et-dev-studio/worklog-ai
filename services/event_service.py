"""Event service — v2 async + Postgres.

Captures raw events, ranks workstream suggestions, lists / searches
events, and writes the VOIDED audit row for soft-deletes. The v1
continuity-score ranking algorithm is preserved verbatim (it survives
as one signal in the future v2 retrieval fusion stage — see
v2 architecture.md §7.4).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import RawEvent, RawEventKind, Workstream, WorkstreamStatus


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


async def add_capture(
    session: AsyncSession,
    *,
    content: str,
    owner_id: uuid.UUID,
    workstream_id: int | None = None,
) -> RawEvent:
    event = RawEvent(
        kind=RawEventKind.CAPTURE.value,
        content=content,
        owner_id=owner_id,
        workstream_id=workstream_id,
    )
    session.add(event)
    if workstream_id is not None:
        ws = await session.get(Workstream, workstream_id)
        if ws is not None:
            ws.last_activity_at = datetime.now(UTC)
    await session.flush()
    await session.refresh(event)
    return event


# ---------------------------------------------------------------------------
# Suggestion ranker (v1 continuity score preserved)
# ---------------------------------------------------------------------------


async def _score_workstream(
    session: AsyncSession,
    ws: Workstream,
    words: set[str],
    technical_terms: set[str],
    now: datetime,
) -> float:
    title_words = {w.lower() for w in ws.title.split()}

    keyword_overlap = len(words & title_words) * 3.0
    exact_technical_terms = len(technical_terms & title_words) * 4.0

    if ws.last_activity_at is None:
        recent_activity = 0.0
    else:
        recency_days = max((now - _as_utc(ws.last_activity_at)).days, 0)
        recent_activity = max(0.0, 14 - recency_days)

    prior_count = await session.scalar(
        select(func.count()).select_from(RawEvent).where(RawEvent.workstream_id == ws.id)
    ) or 0
    prior_attachments = min(prior_count, 20) * 0.4

    recent = (
        await session.execute(
            select(RawEvent)
            .where(RawEvent.workstream_id == ws.id)
            .where(RawEvent.kind == RawEventKind.CAPTURE.value)
            .order_by(RawEvent.ts.desc())
            .limit(20)
        )
    ).scalars().all()

    semantic_similarity = 0.0
    temporal_proximity = 0.0
    for e in recent:
        e_words = {w.lower() for w in e.content.split() if len(w) > 2}
        semantic_similarity += len(words & e_words) * 0.7
        hours = max((now - _as_utc(e.ts)).total_seconds() / 3600, 0)
        temporal_proximity += max(0.0, 72 - hours) / 72

    return (
        keyword_overlap
        + exact_technical_terms
        + recent_activity
        + prior_attachments
        + semantic_similarity
        + temporal_proximity
    )


async def suggest_workstreams(
    session: AsyncSession, content: str, limit: int = 3
) -> list[Workstream]:
    candidates = (
        await session.execute(
            select(Workstream).where(
                Workstream.status.in_(
                    [WorkstreamStatus.ACTIVE.value, WorkstreamStatus.PAUSED.value]
                )
            )
        )
    ).scalars().all()

    words = {w.lower() for w in content.split() if len(w) > 2}
    technical_terms = {w for w in words if any(ch.isdigit() for ch in w) or "-" in w}
    now = datetime.now(UTC)

    scored = [
        (await _score_workstream(session, ws, words, technical_terms, now), ws)
        for ws in candidates
    ]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [ws for _, ws in scored[:limit]]


# ---------------------------------------------------------------------------
# Listing / lookup / search
# ---------------------------------------------------------------------------


async def voided_target_ids(session: AsyncSession) -> set[int]:
    rows = (
        await session.execute(
            select(RawEvent.event_metadata).where(
                RawEvent.kind == RawEventKind.VOIDED.value
            )
        )
    ).scalars().all()
    out: set[int] = set()
    for meta in rows:
        if isinstance(meta, dict):
            eid = meta.get("event_id")
            if isinstance(eid, int):
                out.add(eid)
    return out


async def list_recent(session: AsyncSession, limit: int = 20) -> list[RawEvent]:
    events = (
        await session.execute(
            select(RawEvent).order_by(RawEvent.ts.desc()).limit(limit * 2)
        )
    ).scalars().all()
    voided = await voided_target_ids(session)
    return [e for e in events if e.id not in voided][:limit]


async def list_events(
    session: AsyncSession,
    *,
    day_range: tuple[date, date] | None = None,
    workstream_id: int | None = None,
    kinds: Iterable[RawEventKind] | None = None,
    include_voided: bool = False,
    limit: int = 50,
) -> list[RawEvent]:
    stmt = select(RawEvent)
    if day_range is not None:
        start = datetime(
            day_range[0].year, day_range[0].month, day_range[0].day, tzinfo=UTC
        )
        end = (
            datetime(
                day_range[1].year, day_range[1].month, day_range[1].day, tzinfo=UTC
            )
            + timedelta(days=1)
        )
        stmt = stmt.where(RawEvent.ts >= start).where(RawEvent.ts < end)
    if workstream_id is not None:
        stmt = stmt.where(RawEvent.workstream_id == workstream_id)
    if kinds:
        stmt = stmt.where(RawEvent.kind.in_([k.value for k in kinds]))
    else:
        stmt = stmt.where(RawEvent.kind == RawEventKind.CAPTURE.value)
    events = (
        await session.execute(stmt.order_by(RawEvent.ts.desc()).limit(limit))
    ).scalars().all()
    if include_voided:
        return list(events)
    voided = await voided_target_ids(session)
    return [e for e in events if e.id not in voided]


async def get_event(session: AsyncSession, event_id: int) -> RawEvent | None:
    return await session.get(RawEvent, event_id)


async def search(
    session: AsyncSession, query: str, limit: int = 25, include_voided: bool = False
) -> list[RawEvent]:
    """Lexical search via the GIN-indexed tsvector column."""
    stmt = (
        select(RawEvent)
        .where(RawEvent.content_tsv.op("@@")(func.plainto_tsquery("english", query)))
        .order_by(
            func.ts_rank_cd(
                RawEvent.content_tsv, func.plainto_tsquery("english", query)
            ).desc(),
            RawEvent.ts.desc(),
        )
        .limit(limit)
    )
    events = (await session.execute(stmt)).scalars().all()
    if include_voided:
        return list(events)
    voided = await voided_target_ids(session)
    return [e for e in events if e.id not in voided]


# ---------------------------------------------------------------------------
# Audit mutations
# ---------------------------------------------------------------------------


async def void_event(
    session: AsyncSession,
    event_id: int,
    *,
    actor_id: uuid.UUID,
    reason: str | None = None,
) -> RawEvent | None:
    event = await session.get(RawEvent, event_id)
    if not event:
        return None
    if event.kind != RawEventKind.CAPTURE.value:
        raise ValueError("Only CAPTURE events can be voided.")
    existing = await voided_target_ids(session)
    if event_id in existing:
        return event
    session.add(
        RawEvent(
            kind=RawEventKind.VOIDED.value,
            content=f"Voided event {event_id}" + (f": {reason}" if reason else ""),
            workstream_id=event.workstream_id,
            owner_id=actor_id,
            event_metadata={"event_id": event_id, "reason": reason},
        )
    )
    await session.flush()
    return event


async def connect_event(
    session: AsyncSession,
    event_id: int,
    workstream_id: int,
    *,
    actor_id: uuid.UUID,
) -> RawEvent | None:
    event = await session.get(RawEvent, event_id)
    if not event:
        return None
    event.workstream_id = workstream_id
    session.add(
        RawEvent(
            kind=RawEventKind.EVENT_CONNECTED.value,
            content=f"Connected event {event_id} to workstream {workstream_id}",
            workstream_id=workstream_id,
            owner_id=actor_id,
            event_metadata={"event_id": event_id, "workstream_id": workstream_id},
        )
    )
    await session.flush()
    await session.refresh(event)
    return event
