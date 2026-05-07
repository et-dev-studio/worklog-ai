from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.models import Event, EventTag, Tag


class TagService:
    def __init__(self, session: Session):
        self.session = session

    def get_or_create(self, name: str) -> Tag:
        normalized = name.strip().lower()
        if not normalized:
            raise ValueError("Tag name cannot be empty.")
        tag = self.session.scalar(select(Tag).where(Tag.name == normalized))
        if tag:
            return tag
        tag = Tag(id=str(uuid4()), name=normalized, created_at=datetime.now(UTC))
        self.session.add(tag)
        self.session.commit()
        self.session.refresh(tag)
        return tag

    def attach(self, event_id: str, names: list[str]) -> list[Tag]:
        event = self.session.get(Event, event_id)
        if not event:
            raise ValueError(f"Event not found: {event_id}")
        tags = [self.get_or_create(n) for n in names]
        existing = {
            row[0]
            for row in self.session.execute(
                select(EventTag.tag_id).where(EventTag.event_id == event_id)
            ).all()
        }
        for tag in tags:
            if tag.id not in existing:
                self.session.add(EventTag(event_id=event_id, tag_id=tag.id))
        self.session.commit()
        return tags

    def list_tags(self) -> list[tuple[Tag, int]]:
        rows = self.session.execute(
            select(Tag, func.count(EventTag.event_id))
            .outerjoin(EventTag, EventTag.tag_id == Tag.id)
            .group_by(Tag.id)
            .order_by(Tag.name)
        ).all()
        return [(row[0], int(row[1])) for row in rows]

    def tags_for_event(self, event_id: str) -> list[Tag]:
        return list(
            self.session.scalars(
                select(Tag)
                .join(EventTag, EventTag.tag_id == Tag.id)
                .where(EventTag.event_id == event_id)
                .order_by(Tag.name)
            )
        )

    def events_for_tag(self, name: str) -> list[Event]:
        return list(
            self.session.scalars(
                select(Event)
                .join(EventTag, EventTag.event_id == Event.id)
                .join(Tag, Tag.id == EventTag.tag_id)
                .where(Tag.name == name.strip().lower())
                .order_by(Event.timestamp.desc())
            )
        )
