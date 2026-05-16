"""Reflection scoring — v2 async + Postgres.

Reflections in v2 are raw events of ``kind='reflection'`` rather than a
separate table. This module just picks unresolved CAPTURE events worth
asking follow-up questions about; the agent layer (phase 8) handles the
actual Q&A and writes the reflection RawEvent rows.

Priority signals carried over from v1 (services/reflection_service.py):
- short text (<= 4 words):  +2
- no workstream attached:   +2
- no STATUS_UPDATE in 14d for this workstream: +2
- contains "investigating": +2
- contains "fixed":         -2
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import RawEvent, RawEventKind


def _priority(event: RawEvent, recent_status_update_workstreams: set[int]) -> int:
    score = 0
    if len(event.content.split()) <= 4:
        score += 2
    if event.workstream_id is None:
        score += 2
    elif event.workstream_id not in recent_status_update_workstreams:
        score += 2
    lower = event.content.lower()
    if "investigating" in lower:
        score += 2
    if "fixed" in lower:
        score -= 2
    return score


async def unresolved_captures(
    session: AsyncSession, limit: int = 5
) -> list[RawEvent]:
    """Top-N CAPTURE events the user probably hasn't closed out yet."""
    captures = (
        await session.execute(
            select(RawEvent)
            .where(RawEvent.kind == RawEventKind.CAPTURE.value)
            .order_by(RawEvent.ts.desc())
            .limit(50)
        )
    ).scalars().all()

    cutoff = datetime.now(UTC) - timedelta(days=14)
    recent_status_update_ws_ids = set(
        (
            await session.execute(
                select(RawEvent.workstream_id)
                .where(RawEvent.kind == RawEventKind.STATUS_UPDATE.value)
                .where(RawEvent.ts >= cutoff)
                .where(RawEvent.workstream_id.isnot(None))
            )
        ).scalars()
    )

    ranked = sorted(
        captures,
        key=lambda e: _priority(e, recent_status_update_ws_ids),
        reverse=True,
    )
    return [
        e for e in ranked if _priority(e, recent_status_update_ws_ids) > 0
    ][:limit]
