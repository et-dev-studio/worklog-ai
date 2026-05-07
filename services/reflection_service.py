from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Event, EventType, Reflection


class ReflectionService:
    WEIGHTS = {
        "short_text": 2,
        "no_workstream": 2,
        "no_status_update": 2,
        "investigating": 2,
        "fixed": -2,
    }

    def __init__(self, session: Session):
        self.session = session

    def _has_recent_status_update(self, event: Event) -> bool:
        if not event.workstream_id:
            return False
        cutoff = event.timestamp - timedelta(days=14)
        status_event = self.session.scalar(
            select(Event.id)
            .where(Event.workstream_id == event.workstream_id)
            .where(Event.type == EventType.STATUS_UPDATE)
            .where(Event.timestamp >= cutoff)
            .limit(1)
        )
        return bool(status_event)

    def reflection_priority(self, event: Event) -> int:
        score = 0
        if len(event.content.split()) <= 4:
            score += self.WEIGHTS["short_text"]
        if not event.workstream_id:
            score += self.WEIGHTS["no_workstream"]
        if not self._has_recent_status_update(event):
            score += self.WEIGHTS["no_status_update"]
        text = event.content.lower()
        if "investigating" in text:
            score += self.WEIGHTS["investigating"]
        if "fixed" in text:
            score += self.WEIGHTS["fixed"]
        return score

    def unresolved_capture_events(self, limit: int = 20) -> list[Event]:
        stmt = (
            select(Event)
            .where(Event.type == EventType.CAPTURE)
            .where(~Event.reflections.any())
            .order_by(Event.timestamp.desc())
        )
        ranked = sorted(list(self.session.scalars(stmt)), key=self.reflection_priority, reverse=True)
        return ranked[:limit]

    def add_reflection(self, event_id: str, question: str, answer: str) -> Reflection:
        reflection = Reflection(
            id=str(uuid4()),
            event_id=event_id,
            question=question,
            answer=answer,
            created_at=datetime.now(UTC),
        )
        self.session.add(reflection)
        self.session.add(
            Event(
                id=str(uuid4()),
                timestamp=datetime.now(UTC),
                type=EventType.REFLECTION,
                content=f"Reflection added for event {event_id}",
                workstream_id=None,
                metadata_json=json.dumps({"event_id": event_id}),
            )
        )
        self.session.commit()
        self.session.refresh(reflection)
        return reflection
