from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from db.models import Base, Event, EventType, Workstream, WorkstreamStatus
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


def test_export_format_formalized_api(tmp_path) -> None:
    service = ExportService()
    out = service.export(datetime.now(UTC).date(), "hello", ExportFormat.MARKDOWN)
    assert out is not None
    out_json = service.export(datetime.now(UTC).date(), "hello", ExportFormat.JSON)
    assert out_json is not None


def test_title_mutation_blocked_at_model_level() -> None:
    session = make_session()
    ws = WorkstreamService(session).create("AUTH-20 immutable")
    try:
        ws.title = "changed"
        session.commit()
        assert False, "title mutation should fail"
    except PermissionError:
        assert True
