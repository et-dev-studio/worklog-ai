"""SQLAlchemy 2.x ORM models for worklog v2 (Postgres + pgvector).

Phase 5 lands the foundational tables: identity (users, teams, team_members),
workstreams, raw_events, and agent_traces. Memory tables (memory_objects,
memory_object_versions, memory_relationships) land in phase 6.

All models target Postgres exclusively. SQLite is not a supported backend
in v2; see v2 architecture.md §5 and §11 for the rationale.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Enums — modelled as str subclasses so service code stays typed, but
# persisted as TEXT + CHECK so widening is a one-line migration.
# ---------------------------------------------------------------------------


class WorkstreamStatus(str, enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class Visibility(str, enum.Enum):
    PRIVATE = "private"
    TEAM = "team"
    ORG = "org"


class TeamRole(str, enum.Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class RawEventKind(str, enum.Enum):
    # v1-origin kinds (semantics preserved)
    CAPTURE = "capture"
    REFLECTION = "reflection"
    EVENT_CONNECTED = "event_connected"
    STATUS_UPDATE = "status_update"
    SUMMARY_GENERATED = "summary_generated"
    VOIDED = "voided"
    # v2-new integration source kinds
    GIT_COMMIT = "git_commit"
    SLACK_MESSAGE = "slack_message"
    MEETING_SEGMENT = "meeting_segment"
    TERMINAL_HISTORY = "terminal_history"
    GITHUB_EVENT = "github_event"


class TraceStatus(str, enum.Enum):
    RUNNING = "running"
    OK = "ok"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    pg_role: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String, unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    memberships: Mapped[list["TeamMember"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    members: Mapped[list["TeamMember"]] = relationship(
        back_populates="team", cascade="all, delete-orphan"
    )


class TeamMember(Base):
    __tablename__ = "team_members"

    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[TeamRole] = mapped_column(
        String, nullable=False, default=TeamRole.MEMBER.value
    )

    team: Mapped[Team] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")

    __table_args__ = (
        CheckConstraint(
            "role IN ('owner','admin','member')", name="ck_team_members_role"
        ),
    )


# ---------------------------------------------------------------------------
# Workstreams
# ---------------------------------------------------------------------------


class Workstream(Base):
    __tablename__ = "workstreams"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default=WorkstreamStatus.ACTIVE.value
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    team_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id"), nullable=True
    )
    visibility: Mapped[str] = mapped_column(
        String, nullable=False, default=Visibility.PRIVATE.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_activity_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    raw_events: Mapped[list["RawEvent"]] = relationship(back_populates="workstream")

    __table_args__ = (
        CheckConstraint(
            "status IN ('active','paused','completed','archived')",
            name="ck_workstreams_status",
        ),
        CheckConstraint(
            "visibility IN ('private','team')",
            name="ck_workstreams_visibility",
        ),
    )


# ---------------------------------------------------------------------------
# Raw events — immutable archive, FK target only
# ---------------------------------------------------------------------------


class RawEvent(Base):
    __tablename__ = "raw_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    kind: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    workstream_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("workstreams.id"), nullable=True
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    source_uri: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    event_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="{}"
    )
    content_tsv: Mapped[Optional[str]] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', content)", persisted=True),
        nullable=True,
    )

    workstream: Mapped[Optional[Workstream]] = relationship(back_populates="raw_events")

    __table_args__ = (
        CheckConstraint(
            "kind IN ('capture','reflection','event_connected','status_update',"
            "'summary_generated','voided','git_commit','slack_message',"
            "'meeting_segment','terminal_history','github_event')",
            name="ck_raw_events_kind",
        ),
        CheckConstraint(
            "jsonb_typeof(metadata) = 'object'",
            name="ck_raw_events_metadata_object",
        ),
        Index("ix_raw_events_ts", "ts"),
        Index("ix_raw_events_ws_kind", "workstream_id", "kind"),
        Index("ix_raw_events_kind", "kind"),
        Index("ix_raw_events_owner_ts", "owner_id", "ts"),
        Index("ix_raw_events_content_tsv", "content_tsv", postgresql_using="gin"),
    )


# ---------------------------------------------------------------------------
# Agent traces — observability for the MAS layer (rows arrive from phase 8,
# table provisioned in phase 5 so doctor/migrations stay stable).
# ---------------------------------------------------------------------------


class AgentTrace(Base):
    __tablename__ = "agent_traces"

    trace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    agent_name: Mapped[str] = mapped_column(String, nullable=False)
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String, nullable=False)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    events: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")

    __table_args__ = (
        CheckConstraint(
            "status IN ('running','ok','error')", name="ck_agent_traces_status"
        ),
        Index("ix_traces_agent_started", "agent_name", "started_at"),
    )


# ---------------------------------------------------------------------------
# Cross-cutting invariants
# ---------------------------------------------------------------------------


@event.listens_for(Workstream.title, "set", retval=True)
def _prevent_workstream_title_mutation(target, value, oldvalue, initiator):
    """Ported from v1 (db/models.py:100). Workstream titles are AI-immutable.

    Handles SQLAlchemy NEVER_SET / NO_VALUE sentinels so construction works;
    bulk-repair scripts must disable this listener explicitly.
    """
    from sqlalchemy.orm.attributes import NEVER_SET, NO_VALUE

    if oldvalue not in (None, NEVER_SET, NO_VALUE) and oldvalue != value:
        raise PermissionError("Workstream titles are immutable after creation.")
    return value
