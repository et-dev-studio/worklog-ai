from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from db.models import Event, EventTag, EventType, Tag, Workstream, WorkstreamStatus


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class EventService:
    def __init__(self, session: Session):
        self.session = session
        self.active_workstream_id: str | None = None

    def set_active_workstream(self, workstream_id: str) -> None:
        self.active_workstream_id = workstream_id

    def add_capture(self, content: str, workstream_id: str | None = None) -> Event:
        resolved_workstream_id = workstream_id or self.active_workstream_id
        event = Event(
            id=str(uuid4()),
            timestamp=datetime.now(UTC),
            type=EventType.CAPTURE,
            content=content,
            workstream_id=resolved_workstream_id,
            metadata_json=None,
        )
        self.session.add(event)
        self.session.commit()
        self.session.refresh(event)
        return event

    def _score_workstream(self, ws: Workstream, words: set[str], technical_terms: set[str], now: datetime) -> float:
        title_words = {w.lower() for w in ws.title.split()}

        keyword_overlap = len(words.intersection(title_words)) * 3.0
        exact_technical_terms = len(technical_terms.intersection(title_words)) * 4.0

        recency_days = max((now - _as_utc(ws.last_activity_at)).days, 0)
        recent_activity = max(0.0, 14 - recency_days)

        prior_attach_count = self.session.scalar(select(func.count()).select_from(Event).where(Event.workstream_id == ws.id)) or 0
        prior_attachments = min(prior_attach_count, 20) * 0.4

        recent_ws_events = list(
            self.session.scalars(
                select(Event)
                .where(Event.workstream_id == ws.id)
                .where(Event.type == EventType.CAPTURE)
                .order_by(Event.timestamp.desc())
                .limit(20)
            )
        )

        semantic_similarity = 0.0
        temporal_proximity = 0.0
        for e in recent_ws_events:
            e_words = {w.lower() for w in e.content.split() if len(w) > 2}
            semantic_similarity += len(words.intersection(e_words)) * 0.7
            hours = max((now - _as_utc(e.timestamp)).total_seconds() / 3600, 0)
            temporal_proximity += max(0.0, 72 - hours) / 72

        return keyword_overlap + exact_technical_terms + recent_activity + prior_attachments + semantic_similarity + temporal_proximity

    def suggest_workstreams(self, content: str, limit: int = 3) -> list[Workstream]:
        candidates = list(
            self.session.scalars(
                select(Workstream).where(
                    Workstream.status.in_([WorkstreamStatus.ACTIVE, WorkstreamStatus.PAUSED])
                )
            )
        )
        words = {w.lower() for w in content.split() if len(w) > 2}
        technical_terms = {w for w in words if any(ch.isdigit() for ch in w) or "-" in w}
        now = datetime.now(UTC)

        ranked = sorted(candidates, key=lambda ws: self._score_workstream(ws, words, technical_terms, now), reverse=True)
        return ranked[:limit]

    def list_recent(self, limit: int = 20) -> list[Event]:
        events = list(
            self.session.scalars(
                select(Event).order_by(Event.timestamp.desc()).limit(limit * 2)
            )
        )
        voided = self.voided_target_ids()
        return [e for e in events if e.id not in voided][:limit]

    def voided_target_ids(self) -> set[str]:
        rows = self.session.scalars(
            select(Event.metadata_json).where(Event.type == EventType.VOIDED)
        )
        targets: set[str] = set()
        for raw in rows:
            if not raw:
                continue
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict) and parsed.get("event_id"):
                    targets.add(parsed["event_id"])
            except (TypeError, ValueError):
                continue
        return targets

    def list_events(
        self,
        day_range: tuple[date, date] | None = None,
        workstream_id: str | None = None,
        tag: str | None = None,
        types: list[EventType] | None = None,
        include_voided: bool = False,
        limit: int = 50,
    ) -> list[Event]:
        stmt = select(Event)
        if day_range is not None:
            start = datetime(day_range[0].year, day_range[0].month, day_range[0].day, tzinfo=UTC)
            end = datetime(day_range[1].year, day_range[1].month, day_range[1].day, tzinfo=UTC) + timedelta(days=1)
            stmt = stmt.where(Event.timestamp >= start).where(Event.timestamp < end)
        if workstream_id:
            stmt = stmt.where(Event.workstream_id == workstream_id)
        if types:
            stmt = stmt.where(Event.type.in_(types))
        else:
            stmt = stmt.where(Event.type == EventType.CAPTURE)
        if tag:
            stmt = stmt.join(EventTag, EventTag.event_id == Event.id).join(
                Tag, Tag.id == EventTag.tag_id
            ).where(Tag.name == tag)
        events = list(self.session.scalars(stmt.order_by(Event.timestamp.desc()).limit(limit)))
        if include_voided:
            return events
        voided = self.voided_target_ids()
        return [e for e in events if e.id not in voided]

    def get_event(self, event_id: str) -> Event | None:
        return self.session.get(Event, event_id)

    def search(self, query: str, limit: int = 25, include_voided: bool = False) -> list[Event]:
        rows = self.session.execute(
            text(
                "SELECT event_id FROM events_fts WHERE events_fts MATCH :q "
                "ORDER BY rank LIMIT :lim"
            ),
            {"q": query, "lim": limit},
        ).fetchall()
        ids = [r[0] for r in rows]
        if not ids:
            return []
        events = list(self.session.scalars(select(Event).where(Event.id.in_(ids))))
        ordered = {e.id: e for e in events}
        result = [ordered[i] for i in ids if i in ordered]
        if include_voided:
            return result
        voided = self.voided_target_ids()
        return [e for e in result if e.id not in voided]

    def void_event(self, event_id: str, reason: str | None = None) -> Event | None:
        event = self.session.get(Event, event_id)
        if not event:
            return None
        if event.type != EventType.CAPTURE:
            raise ValueError("Only CAPTURE events can be voided.")
        existing = self.voided_target_ids()
        if event_id in existing:
            return event
        self.session.add(
            Event(
                id=str(uuid4()),
                timestamp=datetime.now(UTC),
                type=EventType.VOIDED,
                content=f"Voided event {event_id}" + (f": {reason}" if reason else ""),
                workstream_id=event.workstream_id,
                metadata_json=json.dumps({"event_id": event_id, "reason": reason}),
            )
        )
        self.session.commit()
        return event

    def connect_event(self, event_id: str, workstream_id: str) -> Event | None:
        event = self.session.get(Event, event_id)
        if not event:
            return None
        event.workstream_id = workstream_id
        self.session.add(
            Event(
                id=str(uuid4()),
                timestamp=datetime.now(UTC),
                type=EventType.EVENT_CONNECTED,
                content=f"Connected event {event_id} to workstream {workstream_id}",
                workstream_id=workstream_id,
                metadata_json=json.dumps({"event_id": event_id, "workstream_id": workstream_id}),
            )
        )
        self.session.commit()
        self.session.refresh(event)
        return event
