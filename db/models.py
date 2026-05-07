from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, Enum as SAEnum, ForeignKey, String, Text, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class WorkstreamStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class EventType(str, Enum):
    CAPTURE = "capture"
    REFLECTION = "reflection"
    EVENT_CONNECTED = "event_connected"
    STATUS_UPDATE = "status_update"
    SUMMARY_GENERATED = "summary_generated"
    VOIDED = "voided"


class Workstream(Base):
    __tablename__ = "workstreams"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[WorkstreamStatus] = mapped_column(
        SAEnum(WorkstreamStatus), default=WorkstreamStatus.ACTIVE, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    events: Mapped[list["Event"]] = relationship(back_populates="workstream")


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint("metadata IS NULL OR json_valid(metadata)", name="ck_events_metadata_json"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    type: Mapped[EventType] = mapped_column(SAEnum(EventType), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    workstream_id: Mapped[Optional[str]] = mapped_column(ForeignKey("workstreams.id"), nullable=True)
    metadata_json: Mapped[Optional[str]] = mapped_column("metadata", Text, nullable=True)

    workstream: Mapped[Optional[Workstream]] = relationship(back_populates="events")
    reflections: Mapped[list["Reflection"]] = relationship(back_populates="event")


class Reflection(Base):
    __tablename__ = "reflections"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    event: Mapped[Event] = relationship(back_populates="reflections")


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class EventTag(Base):
    __tablename__ = "event_tags"

    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), primary_key=True)
    tag_id: Mapped[str] = mapped_column(ForeignKey("tags.id"), primary_key=True)


class Summary(Base):
    __tablename__ = "summaries"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    date: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


@event.listens_for(Workstream.title, "set", retval=True)
def prevent_title_mutation(target, value, oldvalue, initiator):
    from sqlalchemy.orm.attributes import NEVER_SET, NO_VALUE
    if oldvalue not in (None, NEVER_SET, NO_VALUE) and oldvalue != value:
        raise PermissionError("Workstream titles are immutable after creation.")
    return value
