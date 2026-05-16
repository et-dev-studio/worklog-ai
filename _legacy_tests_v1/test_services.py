import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from db.models import Base, Event, EventType, Summary, Workstream, WorkstreamStatus
from services.event_service import EventService
from services.export_service import ExportFormat, ExportService
from services.reflection_service import ReflectionService
from services.summary_service import SummaryService
from services.workstream_service import WorkstreamService


def make_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session)()


def test_create_workstream_and_capture_event() -> None:
    session = make_session()
    ws = WorkstreamService(session).create("AUTH-1 OAuth fix")
    event = EventService(session).add_capture("Investigating oauth redirect", ws.id)
    assert event.workstream_id == ws.id


def test_reflection_priority_with_no_status_signal() -> None:
    session = make_session()
    ws = WorkstreamService(session).create("PLATFORM-1 Redis")
    event = Event(
        id="e1",
        timestamp=datetime.now(UTC),
        type=EventType.CAPTURE,
        content="Investigating timeout",
        workstream_id=ws.id,
        metadata_json=None,
    )
    session.add(event)
    session.commit()
    score = ReflectionService(session).reflection_priority(event)
    assert score >= 2


def test_summary_group_and_save() -> None:
    session = make_session()
    ws = Workstream(
        id="w1",
        title="PLATFORM-1 Redis",
        summary=None,
        status=WorkstreamStatus.ACTIVE,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        last_activity_at=datetime.now(UTC),
    )
    session.add(ws)
    session.add(
        Event(
            id="e1",
            timestamp=datetime.now(UTC),
            type=EventType.CAPTURE,
            content="Added instrumentation",
            workstream_id=ws.id,
            metadata_json=None,
        )
    )
    session.commit()
    summary_service = SummaryService(session)
    grouped = summary_service.grouped_context(datetime.now(UTC).date())
    assert "PLATFORM-1 Redis" in grouped
    saved = summary_service.save_summary("ok", datetime.now(UTC).date(), decision="edit", edited=True)
    assert saved.content == "ok"


def test_matching_prefers_related_workstream() -> None:
    session = make_session()
    ws1 = WorkstreamService(session).create("PLATFORM-88 Redis stability investigation")
    ws2 = WorkstreamService(session).create("AUTH-1 OAuth callback")
    EventService(session).add_capture("Investigating redis timeout", ws1.id)
    suggestions = EventService(session).suggest_workstreams("redis timeout stability", limit=1)
    assert suggestions[0].id == ws1.id


def test_suggestions_match_real_db_enum_storage() -> None:
    session = make_session()
    active_ws = WorkstreamService(session).create("PLATFORM-1 redis pool")
    paused_ws = WorkstreamService(session).create("AUTH-1 oauth")
    WorkstreamService(session).set_status(paused_ws.id, WorkstreamStatus.PAUSED)
    archived_ws = WorkstreamService(session).create("LEGACY-1 retired")
    WorkstreamService(session).set_status(archived_ws.id, WorkstreamStatus.ARCHIVED)
    suggestions = EventService(session).suggest_workstreams("redis pool", limit=5)
    suggestion_ids = {ws.id for ws in suggestions}
    assert active_ws.id in suggestion_ids
    assert paused_ws.id in suggestion_ids
    assert archived_ws.id not in suggestion_ids


def test_human_governance_title_rename_guardrail() -> None:
    session = make_session()
    ws = WorkstreamService(session).create("AUTH-9 immutable title")
    try:
        WorkstreamService(session).rename(ws.id, "AI renamed")
        assert False, "rename should be forbidden"
    except PermissionError:
        assert True


def test_export_format_formalized_api(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("WORKLOG_HOME", str(tmp_path))
    service = ExportService()
    out = service.export(datetime.now(UTC).date(), "hello", ExportFormat.MARKDOWN)
    assert out is not None and out.exists()
    out_json = service.export(datetime.now(UTC).date(), "hello", ExportFormat.JSON)
    assert out_json is not None and out_json.exists()
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["content"] == "hello"


def test_export_all_writes_both_formats(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("WORKLOG_HOME", str(tmp_path))
    service = ExportService()
    day = datetime.now(UTC).date()
    service.export(day, "ALL content", ExportFormat.ALL)
    files = list((tmp_path / "exports").rglob("*"))
    suffixes = {f.suffix for f in files if f.is_file()}
    assert ".md" in suffixes and ".json" in suffixes


def test_archived_workstreams_never_suggested() -> None:
    session = make_session()
    archived = WorkstreamService(session).create("PLATFORM-9 redis legacy")
    WorkstreamService(session).set_status(archived.id, WorkstreamStatus.ARCHIVED)
    suggestions = EventService(session).suggest_workstreams("redis legacy", limit=5)
    assert all(ws.id != archived.id for ws in suggestions)


def test_completed_workstreams_never_suggested() -> None:
    session = make_session()
    done = WorkstreamService(session).create("DONE-1 wrapped up")
    WorkstreamService(session).set_status(done.id, WorkstreamStatus.COMPLETED)
    suggestions = EventService(session).suggest_workstreams("wrapped up", limit=5)
    assert all(ws.id != done.id for ws in suggestions)


def test_reflection_priority_fixed_keyword_subtracts() -> None:
    session = make_session()
    ws = WorkstreamService(session).create("PLATFORM-2 caches")
    fixed_event = Event(
        id="e-fixed",
        timestamp=datetime.now(UTC),
        type=EventType.CAPTURE,
        content="cache invalidation fixed and verified",
        workstream_id=ws.id,
        metadata_json=None,
    )
    session.add(fixed_event)
    session.commit()
    score = ReflectionService(session).reflection_priority(fixed_event)
    assert score < 2


def test_unresolved_capture_excludes_already_reflected() -> None:
    session = make_session()
    ws = WorkstreamService(session).create("WS-A")
    captured = EventService(session).add_capture("investigating something", ws.id)
    service = ReflectionService(session)
    assert any(e.id == captured.id for e in service.unresolved_capture_events())
    service.add_reflection(captured.id, "what?", "answered")
    assert all(e.id != captured.id for e in service.unresolved_capture_events())


def test_summary_emits_summary_generated_audit_event() -> None:
    session = make_session()
    day = datetime.now(UTC).date()
    SummaryService(session).save_summary("body", day, decision="edit", edited=True)
    audit = (
        session.query(Event)
        .filter(Event.type == EventType.SUMMARY_GENERATED)
        .one()
    )
    metadata = json.loads(audit.metadata_json)
    assert metadata["decision"] == "edit"
    assert metadata["edited"] is True
    assert metadata["date"] == day.isoformat()


def test_event_connected_emits_audit_event_with_json_metadata() -> None:
    session = make_session()
    ws = WorkstreamService(session).create("WS-X")
    event = EventService(session).add_capture("loose capture")
    EventService(session).connect_event(event.id, ws.id)
    audit = (
        session.query(Event)
        .filter(Event.type == EventType.EVENT_CONNECTED)
        .one()
    )
    metadata = json.loads(audit.metadata_json)
    assert metadata["event_id"] == event.id
    assert metadata["workstream_id"] == ws.id


def test_daily_events_includes_late_evening_excludes_next_day() -> None:
    session = make_session()
    ws = WorkstreamService(session).create("WS-Y")
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    late = today.replace(hour=23, minute=59, second=59, microsecond=999000)
    next_day = today + timedelta(days=1)
    session.add_all(
        [
            Event(id="late", timestamp=late, type=EventType.CAPTURE, content="late", workstream_id=ws.id, metadata_json=None),
            Event(id="next", timestamp=next_day, type=EventType.CAPTURE, content="next", workstream_id=ws.id, metadata_json=None),
        ]
    )
    session.commit()
    events = SummaryService(session).daily_events(today.date())
    ids = {e.id for e in events}
    assert "late" in ids
    assert "next" not in ids


def test_metadata_json_constraint_rejects_invalid_json() -> None:
    session = make_session()
    ws = WorkstreamService(session).create("WS-Z")
    bad = Event(
        id="bad",
        timestamp=datetime.now(UTC),
        type=EventType.CAPTURE,
        content="x",
        workstream_id=ws.id,
        metadata_json="not-json",
    )
    session.add(bad)
    with pytest.raises(Exception):
        session.commit()


def test_title_mutation_blocked_at_model_level() -> None:
    session = make_session()
    ws = WorkstreamService(session).create("AUTH-20 immutable")
    try:
        ws.title = "changed"
        session.commit()
        assert False, "title mutation should fail"
    except PermissionError:
        assert True
