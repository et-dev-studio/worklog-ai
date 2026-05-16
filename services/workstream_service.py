"""Workstream service — v2 async + Postgres.

Stateless functions; the caller passes an ``AsyncSession`` from
``services.storage.postgres.get_session``. Status changes emit a
``status_update`` raw event so the audit trail survives.

Titles are immutable after creation, enforced by the SQLAlchemy listener
in ``db.models`` (ported verbatim from v1).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import RawEvent, RawEventKind, Visibility, Workstream, WorkstreamStatus


async def create(
    session: AsyncSession,
    *,
    title: str,
    owner_id: uuid.UUID,
    summary: str | None = None,
    team_id: uuid.UUID | None = None,
    visibility: Visibility = Visibility.PRIVATE,
) -> Workstream:
    now = datetime.now(UTC)
    ws = Workstream(
        title=title,
        summary=summary,
        status=WorkstreamStatus.ACTIVE.value,
        owner_id=owner_id,
        team_id=team_id,
        visibility=visibility.value,
        created_at=now,
        updated_at=now,
        last_activity_at=now,
    )
    session.add(ws)
    await session.flush()
    await session.refresh(ws)
    return ws


async def list_all(session: AsyncSession) -> list[Workstream]:
    result = await session.execute(
        select(Workstream).order_by(Workstream.last_activity_at.desc().nullslast())
    )
    return list(result.scalars())


async def get(session: AsyncSession, workstream_id: int) -> Workstream | None:
    return await session.get(Workstream, workstream_id)


async def set_status(
    session: AsyncSession,
    workstream_id: int,
    status: WorkstreamStatus,
    actor_id: uuid.UUID,
) -> Workstream | None:
    ws = await session.get(Workstream, workstream_id)
    if not ws:
        return None
    ws.status = status.value
    ws.updated_at = datetime.now(UTC)
    session.add(
        RawEvent(
            kind=RawEventKind.STATUS_UPDATE.value,
            content=f"Workstream {ws.title} status set to {status.value}",
            workstream_id=workstream_id,
            owner_id=actor_id,
            event_metadata={"previous_status": ws.status, "new_status": status.value},
        )
    )
    await session.flush()
    await session.refresh(ws)
    return ws
