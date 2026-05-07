from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Event, EventType, Summary, Workstream
from services.grouping_service import GroupingService


def _voided_target_ids(session) -> set[str]:
    import json as _json

    rows = session.scalars(select(Event.metadata_json).where(Event.type == EventType.VOIDED))
    out: set[str] = set()
    for raw in rows:
        if not raw:
            continue
        try:
            parsed = _json.loads(raw)
            if isinstance(parsed, dict) and parsed.get("event_id"):
                out.add(parsed["event_id"])
        except (TypeError, ValueError):
            continue
    return out


class SummaryService:
    def __init__(self, session: Session):
        self.session = session
        self.grouping = GroupingService()

    def daily_events(self, day: date) -> list[Event]:
        start = datetime(day.year, day.month, day.day, tzinfo=UTC)
        end = start + timedelta(days=1)
        events = list(
            self.session.scalars(
                select(Event)
                .where(Event.timestamp >= start)
                .where(Event.timestamp < end)
                .where(Event.type != EventType.VOIDED)
            )
        )
        voided = _voided_target_ids(self.session)
        return [e for e in events if e.id not in voided]

    def grouped_context(self, day: date) -> str:
        events = self.daily_events(day)
        groups = self.grouping.group_by_workstream(events)
        blocks = []
        for workstream_id, ws_events in groups.items():
            title = "Unassigned"
            if workstream_id != "unassigned":
                ws = self.session.get(Workstream, workstream_id)
                title = ws.title if ws else workstream_id
            blocks.append(f"### {title}")
            blocks.extend(f"- {e.content}" for e in ws_events)
        return "\n".join(blocks) if blocks else "No events"

    def save_summary(self, content: str, day: date, decision: str = "yes", edited: bool = False) -> Summary:
        summary = Summary(id=str(uuid4()), date=day.isoformat(), content=content, created_at=datetime.now(UTC))
        self.session.add(summary)
        self.session.add(
            Event(
                id=str(uuid4()),
                timestamp=datetime.now(UTC),
                type=EventType.SUMMARY_GENERATED,
                content=f"Summary generated for {day.isoformat()}",
                workstream_id=None,
                metadata_json=json.dumps({"date": day.isoformat(), "decision": decision, "edited": edited}),
            )
        )
        self.session.commit()
        self.session.refresh(summary)
        return summary
