"""Daily summary service — v2 async + Postgres.

Replaces the v1 ``Summary`` table with a RawEvent of
``kind='summary_generated'`` whose content holds the summary body and
whose metadata captures the day, decision, and edited flag. This keeps
summaries lexically searchable (tsvector hits them automatically) and
preserves the audit trail without a parallel table to keep in sync.

Per-day events are grouped by workstream for the agent layer to
summarise; voided events are filtered. The day-boundary fix from v1
(exclusive ``< start_of_next_day``) is preserved.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import RawEvent, RawEventKind, Workstream
from services.event_service import voided_target_ids


async def daily_events(session: AsyncSession, day: date) -> list[RawEvent]:
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    end = start + timedelta(days=1)
    events = (
        await session.execute(
            select(RawEvent)
            .where(RawEvent.ts >= start)
            .where(RawEvent.ts < end)
            .where(RawEvent.kind != RawEventKind.VOIDED.value)
            .order_by(RawEvent.ts.asc())
        )
    ).scalars().all()
    voided = await voided_target_ids(session)
    return [e for e in events if e.id not in voided]


def _group_by_workstream(events: list[RawEvent]) -> dict[int | None, list[RawEvent]]:
    groups: dict[int | None, list[RawEvent]] = defaultdict(list)
    for event in events:
        groups[event.workstream_id].append(event)
    return dict(groups)


async def grouped_context(session: AsyncSession, day: date) -> str:
    events = await daily_events(session, day)
    if not events:
        return "No events"
    blocks: list[str] = []
    for workstream_id, ws_events in _group_by_workstream(events).items():
        if workstream_id is None:
            title = "Unassigned"
        else:
            ws = await session.get(Workstream, workstream_id)
            title = ws.title if ws else f"<workstream {workstream_id}>"
        blocks.append(f"### {title}")
        blocks.extend(f"- {e.content}" for e in ws_events)
    return "\n".join(blocks)


async def save_summary(
    session: AsyncSession,
    *,
    content: str,
    day: date,
    actor_id: uuid.UUID,
    decision: str = "yes",
    edited: bool = False,
) -> RawEvent:
    summary_event = RawEvent(
        kind=RawEventKind.SUMMARY_GENERATED.value,
        content=content,
        owner_id=actor_id,
        event_metadata={
            "date": day.isoformat(),
            "decision": decision,
            "edited": edited,
        },
    )
    session.add(summary_event)
    await session.flush()
    await session.refresh(summary_event)
    return summary_event
