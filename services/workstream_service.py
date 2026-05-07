from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Event, EventType, Workstream, WorkstreamStatus


class WorkstreamService:
    def __init__(self, session: Session):
        self.session = session

    def create(self, title: str, summary: str | None = None) -> Workstream:
        now = datetime.now(UTC)
        ws = Workstream(
            id=str(uuid4()),
            title=title,
            summary=summary,
            status=WorkstreamStatus.ACTIVE,
            created_at=now,
            updated_at=now,
            last_activity_at=now,
        )
        self.session.add(ws)
        self.session.commit()
        self.session.refresh(ws)
        return ws

    def list_all(self) -> list[Workstream]:
        return list(self.session.scalars(select(Workstream).order_by(Workstream.last_activity_at.desc())))

    def set_status(self, workstream_id: str, status: WorkstreamStatus) -> Workstream | None:
        workstream = self.session.get(Workstream, workstream_id)
        if not workstream:
            return None
        workstream.status = status
        workstream.updated_at = datetime.now(UTC)
        self.session.add(
            Event(
                id=str(uuid4()),
                timestamp=datetime.now(UTC),
                type=EventType.STATUS_UPDATE,
                content=f"Workstream {workstream.title} status set to {status.value}",
                workstream_id=workstream_id,
                metadata_json=None,
            )
        )
        self.session.commit()
        self.session.refresh(workstream)
        return workstream

    def rename(self, workstream_id: str, new_title: str) -> None:
        raise PermissionError(
            "Workstream titles are human-defined and immutable by system/AI operations. "
            "Create a new workstream instead."
        )
