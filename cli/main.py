from __future__ import annotations

from datetime import date
import asyncio
from pathlib import Path

import questionary
import typer

from agents.reflection_agent import ReflectionAgent
from agents.summary_agent import SummaryAgent
from cli.output import emit_json
from cli.prompts import is_tty, require_tty, select_from, text_input, confirm_choice
from db.models import Base, EventType, WorkstreamStatus
from db.session import engine, get_session
from services.dateparse_service import DateParseError, parse_day, parse_range
from services.event_service import EventService
from services.daemon_service import DaemonService
from services.export_service import ExportFormat, ExportService
from services.inference_service import InferenceService, InferenceUnavailable
from services.notification_service import NotificationService
from services.paths_service import (
    active_workstream_path,
    daemon_heartbeat_path,
    daemon_last_job_path,
    daemon_pid_path,
    db_path,
    home,
    notifications_log_path,
    project_root,
    prompts_dir,
)
from services.prompt_service import PromptService
from services.reflection_service import ReflectionService
from services.config_service import ConfigService
from services.scheduler_service import SchedulerService
from services.summary_service import SummaryService
from services.tag_service import TagService
from services.workstream_service import WorkstreamService

app = typer.Typer(help="Worklog AI CLI")
workstream_app = typer.Typer(help="Workstream operations")
event_app = typer.Typer(help="Event operations: list, show, search, tag")
tag_app = typer.Typer(help="Tag operations")
app.add_typer(workstream_app, name="workstream")
app.add_typer(event_app, name="event")
app.add_typer(tag_app, name="tag")


def _active_ws_file() -> Path:
    return active_workstream_path()


def _get_active_workstream_id() -> str | None:
    f = _active_ws_file()
    if f.exists():
        return f.read_text(encoding="utf-8").strip() or None
    return None


@app.command()
def init_db() -> None:
    from alembic import command as alembic_command
    from alembic.config import Config as AlembicConfig

    from services.paths_service import db_url, project_root

    cfg = AlembicConfig(str(project_root() / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root() / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url())
    alembic_command.upgrade(cfg, "head")
    typer.echo("Database initialized.")


@app.command()
def add(
    content: str = typer.Argument(..., help="Event text. Use '-' to read from stdin."),
    workstream_id: str | None = typer.Option(None, "--workstream-id"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip interactive prompts; use active workstream if set."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Skip prompts even on a TTY (alias of --yes)."),
    tags: list[str] = typer.Option(None, "--tag", "-t", help="Tag(s) to attach to the new event."),
) -> None:
    import sys as _sys

    if content == "-":
        content = _sys.stdin.read().strip()
        if not content:
            raise typer.BadParameter("stdin was empty")
    skip_prompts = yes or quiet
    with get_session() as session:
        event_service = EventService(session)
        selected_workstream_id = workstream_id
        if not selected_workstream_id:
            if skip_prompts or not is_tty():
                active = _get_active_workstream_id()
                selected_workstream_id = active
            else:
                suggestions = event_service.suggest_workstreams(content)
                if suggestions:
                    choices = [questionary.Choice(title=f"{ws.title} ({ws.id})", value=ws.id) for ws in suggestions]
                    choices.append(questionary.Choice(title="No attachment", value=""))
                    answer = select_from("Attach to a suggested workstream?", choices, default="")
                    selected_workstream_id = answer or None
                else:
                    active = _get_active_workstream_id()
                    if active:
                        event_service.set_active_workstream(active)

        event = event_service.add_capture(content=content, workstream_id=selected_workstream_id)
        if tags:
            TagService(session).attach(event.id, tags)
        typer.echo(f"Captured event: {event.id}")


app.command(name="a", hidden=True)(add)


@app.command()
def connect(
    event_id: str | None = None,
    workstream_id: str | None = None,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip interactive prompts; require explicit --event-id and --workstream-id."),
) -> None:
    with get_session() as session:
        event_service = EventService(session)
        if not event_id:
            events = event_service.list_recent(limit=20)
            if not events:
                typer.echo("No events available.")
                return
            if yes or not is_tty():
                typer.echo("Error: --event-id required in non-interactive mode.", err=True)
                raise typer.Exit(code=2)
            event_id = questionary.select(
                "Select event:",
                choices=[questionary.Choice(title=f"{e.timestamp.isoformat()} | {e.content} ({e.id})", value=e.id) for e in events],
            ).ask()

        if not workstream_id:
            workstreams = WorkstreamService(session).list_all()
            if not workstreams:
                typer.echo("No workstreams available.")
                return
            if yes or not is_tty():
                typer.echo("Error: --workstream-id required in non-interactive mode.", err=True)
                raise typer.Exit(code=2)
            workstream_id = questionary.select(
                "Select workstream:",
                choices=[questionary.Choice(title=f"[{w.status.value}] {w.title} ({w.id})", value=w.id) for w in workstreams],
            ).ask()

        event = event_service.connect_event(event_id=event_id, workstream_id=workstream_id)
        if not event:
            raise typer.BadParameter("Event not found")
        typer.echo(f"Connected {event_id} -> {workstream_id}")


def _alembic_head() -> str | None:
    try:
        import sqlite3

        conn = sqlite3.connect(str(db_path()))
        try:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except Exception as exc:
        return f"unknown ({exc})"


@app.command()
def doctor(json_output: bool = typer.Option(False, "--json")) -> None:
    inference = InferenceService()
    health = asyncio.run(inference.healthcheck())
    daemon = DaemonService()
    notifier = NotificationService()
    prompts = prompts_dir()
    required_prompts = ["reflection.txt", "summarize.txt", "grouping.txt", "categorize.txt"]
    missing_prompts = [name for name in required_prompts if not (prompts / name).exists()]

    report = {
        "worklog_home": str(home()),
        "project_root": str(project_root()),
        "database": str(db_path()),
        "database_exists": db_path().exists(),
        "alembic_revision": _alembic_head() if db_path().exists() else None,
        "inference": {
            "url": health.url or inference.url,
            "reachable": health.runnable,
            "detail": health.detail,
            "model": health.model,
            "latency_ms": (
                round(health.latency_ms, 1) if health.latency_ms is not None else None
            ),
        },
        "daemon": {
            "status": daemon.status(),
            "pid_file": str(daemon_pid_path()),
            "heartbeat_age_seconds": daemon.heartbeat_age_seconds(),
            "last_job_age_seconds": daemon.last_job_age_seconds(),
        },
        "notify_send": notifier.notify_send_available(),
        "notifications_log": str(notifications_log_path()),
        "prompts_dir": str(prompts),
        "missing_prompts": missing_prompts,
        "active_workstream_file": str(active_workstream_path()),
        "active_workstream_id": _get_active_workstream_id(),
        "shell_completion_hint": "wl --install-completion bash | zsh | fish",
    }
    if json_output:
        emit_json(report)
        return

    def fmt(label: str, value) -> str:
        return f"{label:<28} {value}"

    typer.echo(fmt("WORKLOG_HOME:", report["worklog_home"]))
    typer.echo(fmt("Project root:", report["project_root"]))
    typer.echo(fmt("Database:", report["database"]))
    typer.echo(fmt("DB exists:", report["database_exists"]))
    typer.echo(fmt("Alembic revision:", report["alembic_revision"] or "(no DB)"))
    typer.echo(fmt("Inference URL:", report["inference"]["url"]))
    typer.echo(
        fmt(
            "Inference reachable:",
            f"{report['inference']['reachable']} ({report['inference']['detail']})",
        )
    )
    typer.echo(fmt("Inference model:", report["inference"]["model"] or "(none detected)"))
    typer.echo(
        fmt(
            "Inference latency:",
            f"{report['inference']['latency_ms']} ms" if report["inference"]["latency_ms"] is not None else "(n/a)",
        )
    )
    typer.echo(fmt("Daemon status:", report["daemon"]["status"]))
    typer.echo(fmt("Daemon heartbeat age:", report["daemon"]["heartbeat_age_seconds"]))
    typer.echo(fmt("Daemon last-job age:", report["daemon"]["last_job_age_seconds"]))
    typer.echo(fmt("notify-send available:", report["notify_send"]))
    typer.echo(fmt("Notifications log:", report["notifications_log"]))
    typer.echo(fmt("Prompts dir:", report["prompts_dir"]))
    typer.echo(fmt("Missing prompts:", report["missing_prompts"] or "(none)"))
    typer.echo(fmt("Active workstream:", report["active_workstream_id"] or "(none)"))
    typer.echo(fmt("Shell completion:", report["shell_completion_hint"]))


@app.command()
def undo(
    event_id: str | None = typer.Argument(None, help="Event id to void; defaults to the most recent capture."),
    reason: str | None = typer.Option(None, "--reason", help="Optional explanation stored on the void event."),
) -> None:
    with get_session() as session:
        event_service = EventService(session)
        if event_id is None:
            recent = event_service.list_recent(limit=1)
            if not recent:
                typer.echo("Nothing to undo.")
                return
            event_id = recent[0].id
        try:
            target = event_service.void_event(event_id, reason=reason)
        except ValueError as exc:
            raise typer.BadParameter(str(exc))
        if not target:
            raise typer.BadParameter(f"Event not found: {event_id}")
        typer.echo(f"Voided event: {event_id}")


@app.command()
def status(json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON.")) -> None:
    with get_session() as session:
        ws_service = WorkstreamService(session)
        ref_service = ReflectionService(session)
        active = [ws for ws in ws_service.list_all() if ws.status == WorkstreamStatus.ACTIVE]
        unresolved = ref_service.unresolved_capture_events(limit=10)
        if json_output:
            emit_json(
                {
                    "active_workstreams": [{"id": ws.id, "title": ws.title} for ws in active],
                    "pending_reflections": len(unresolved),
                }
            )
            return
        typer.echo(f"Active workstreams: {len(active)}")
        for ws in active:
            typer.echo(f"- {ws.title} ({ws.id})")
        typer.echo(f"Pending reflections: {len(unresolved)}")


@app.command()
def resume(workstream_id: str) -> None:
    with get_session() as session:
        ws = WorkstreamService(session).list_all()
        if not any(w.id == workstream_id for w in ws):
            raise typer.BadParameter(f"Workstream not found: {workstream_id}")
    f = _active_ws_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(workstream_id, encoding="utf-8")
    typer.echo(f"Active workstream set: {workstream_id}")


@app.command()
def reflect() -> None:
    require_tty("reflect")
    inference = InferenceService()
    health = asyncio.run(inference.healthcheck())
    if not health.runnable:
        typer.echo(f"Error: inference unavailable ({health.detail}). Run `wl doctor`.", err=True)
        raise typer.Exit(code=3)
    prompts = PromptService()
    agent = ReflectionAgent(inference, prompts)
    with get_session() as session:
        reflection_service = ReflectionService(session)
        unresolved = reflection_service.unresolved_capture_events(limit=5)
        if not unresolved:
            typer.echo("No unresolved events.")
            return

        for event in unresolved:
            try:
                question = asyncio.run(agent.generate_questions(event.content))
            except InferenceUnavailable as exc:
                typer.echo(f"Error: {exc}", err=True)
                raise typer.Exit(code=3) from exc
            answer = text_input(f"{event.content}\n{question}\nAnswer:")
            if answer:
                reflection_service.add_reflection(event_id=event.id, question=question, answer=answer)
        typer.echo("Reflection session complete.")


@app.command()
def summary(
    day: str | None = typer.Option(None, "--day", help="ISO date (default: today)."),
    export_format: ExportFormat = typer.Option(ExportFormat.MARKDOWN, "--export-format"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-accept the generated summary."),
) -> None:
    target_day = date.fromisoformat(day) if day else date.today()
    inference = InferenceService()
    health = asyncio.run(inference.healthcheck())
    if not health.runnable:
        typer.echo(f"Error: inference unavailable ({health.detail}). Run `wl doctor`.", err=True)
        raise typer.Exit(code=3)
    prompts = PromptService()
    agent = SummaryAgent(inference, prompts)
    with get_session() as session:
        summary_service = SummaryService(session)
        context = summary_service.grouped_context(target_day)
        try:
            text = asyncio.run(agent.summarize(context))
        except InferenceUnavailable as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=3) from exc
        typer.echo(text)

        if yes or not is_tty():
            decision = "yes"
        else:
            decision = confirm_choice("Accept summary?", ["yes", "edit", "no"], default="yes")
        if decision == "no":
            typer.echo("Summary discarded.")
            return
        if decision == "edit":
            edited = text_input("Edit summary:", default=text)
            if edited:
                text = edited

        summary_service.save_summary(text, target_day, decision=decision, edited=(decision == "edit"))
        export_service = ExportService()
        if export_format == ExportFormat.TERMINAL:
            export_service.export(target_day, text, ExportFormat.TERMINAL)
            typer.echo("Summary output to terminal.")
        elif export_format == ExportFormat.JSON:
            path = export_service.export(target_day, text, ExportFormat.JSON)
            typer.echo(f"JSON exported: {path}")
        elif export_format == ExportFormat.MARKDOWN:
            path = export_service.export(target_day, text, ExportFormat.MARKDOWN)
            typer.echo(f"Summary exported: {path}")
        else:
            export_service.export(target_day, text, ExportFormat.ALL)
            typer.echo("Exported markdown, json, and terminal output.")


@app.command("scheduler-run", hidden=True)
def scheduler_run() -> None:
    import signal
    import threading
    import time

    daemon = DaemonService()
    scheduler = SchedulerService(NotificationService(), daemon=daemon)
    scheduler.start_default_reminders()

    stop = threading.Event()

    def _shutdown(_signo, _frame) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        while not stop.is_set():
            daemon.heartbeat()
            stop.wait(timeout=60)
    finally:
        scheduler.shutdown()


@app.command("daemon-start")
def daemon_start() -> None:
    if not NotificationService().notify_send_available():
        typer.echo(
            "WARN: notify-send not found on PATH. Reminders will fall back to "
            f"{active_workstream_path().parent / 'notifications.log'} and stderr.",
            err=True,
        )
    result = DaemonService().start()
    typer.echo(f"Daemon {result}.")


@app.command("notify-test")
def notify_test(message: str = typer.Argument("Worklog test notification")) -> None:
    NotificationService().send(message)
    typer.echo("Notification dispatched (check desktop or notifications.log).")


@app.command("daemon-stop")
def daemon_stop() -> None:
    result = DaemonService().stop()
    typer.echo(f"Daemon {result}.")




@app.command("daemon-restart")
def daemon_restart() -> None:
    result = DaemonService().restart()
    typer.echo(f"Daemon {result}.")

@app.command("daemon-dashboard")
def daemon_dashboard(
    watch: int = typer.Option(0, "--watch", "-w", help="Refresh every N seconds (0 = run once)."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    import time as _time

    daemon = DaemonService()
    config = ConfigService()

    def _next_run_for(hm: str) -> str:
        from datetime import datetime as _dt, timedelta as _td

        now = _dt.now()
        try:
            h, m = hm.split(":")
            hh, mm = int(h), int(m)
        except ValueError:
            return f"(invalid: {hm})"
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if target <= now:
            target = target + _td(days=1)
        return target.strftime("%Y-%m-%d %H:%M")

    def _build_report() -> dict:
        cfg = config.load()
        with get_session() as session:
            ref_service = ReflectionService(session)
            event_service = EventService(session)
            unresolved = ref_service.unresolved_capture_events(limit=10)
            recent = event_service.list_recent(limit=10)
            return {
                "daemon_status": daemon.status(),
                "heartbeat_age_seconds": daemon.heartbeat_age_seconds(),
                "last_job_age_seconds": daemon.last_job_age_seconds(),
                "schedules": {
                    "lunch": {
                        "time": cfg.get("lunch", {}).get("time", "13:00"),
                        "next_run": _next_run_for(cfg.get("lunch", {}).get("time", "13:00")),
                    },
                    "evening": {
                        "time": cfg.get("evening", {}).get("time", "18:00"),
                        "next_run": _next_run_for(cfg.get("evening", {}).get("time", "18:00")),
                    },
                },
                "pending_reflections": [
                    {"id": e.id, "content": e.content, "timestamp": e.timestamp}
                    for e in unresolved
                ],
                "recent_events": [
                    {
                        "id": e.id,
                        "type": e.type.value,
                        "content": e.content,
                        "timestamp": e.timestamp,
                        "workstream_id": e.workstream_id,
                    }
                    for e in recent
                ],
            }

    def _print(report: dict) -> None:
        typer.echo(f"Daemon: {report['daemon_status']}")
        typer.echo(f"  heartbeat_age={report['heartbeat_age_seconds']}s last_job_age={report['last_job_age_seconds']}s")
        typer.echo("Schedules:")
        for name, info in report["schedules"].items():
            typer.echo(f"  {name:<8} time={info['time']} next_run={info['next_run']}")
        typer.echo(f"Pending reflections ({len(report['pending_reflections'])}):")
        for e in report["pending_reflections"][:5]:
            typer.echo(f"  - {e['content'][:80]} ({e['id']})")
        typer.echo(f"Recent events ({len(report['recent_events'])}):")
        for e in report["recent_events"][:10]:
            ws = f" -> {e['workstream_id']}" if e["workstream_id"] else ""
            typer.echo(f"  {e['timestamp']} [{e['type']}] {e['content'][:60]}{ws}")

    while True:
        report = _build_report()
        if json_output:
            emit_json(report)
        else:
            if watch:
                typer.echo("\033[2J\033[H", nl=False)
            _print(report)
        if not watch:
            return
        try:
            _time.sleep(watch)
        except KeyboardInterrupt:
            return


@app.command("daemon-status")
def daemon_status(json_output: bool = typer.Option(False, "--json", help="Emit JSON.")) -> None:
    svc = DaemonService()
    state = svc.status()
    if json_output:
        emit_json(
            {
                "status": state,
                "heartbeat_age_seconds": svc.heartbeat_age_seconds(),
                "last_job_age_seconds": svc.last_job_age_seconds(),
            }
        )
        return
    typer.echo(f"Daemon {state}.")


def _format_event_text(e) -> str:
    ws = f" -> {e.workstream_id}" if e.workstream_id else ""
    return f"{e.timestamp.isoformat()} [{e.type.value}] {e.content}{ws} ({e.id})"


def _event_to_dict(e, tags: list[str] | None = None) -> dict:
    payload = {
        "id": e.id,
        "timestamp": e.timestamp,
        "type": e.type.value,
        "content": e.content,
        "workstream_id": e.workstream_id,
        "metadata_json": e.metadata_json,
    }
    if tags is not None:
        payload["tags"] = tags
    return payload


@event_app.command("list")
def event_list(
    day: str | None = typer.Option(None, "--day", help="today, yesterday, -3d, this-week, last-week, or YYYY-MM-DD"),
    workstream_id: str | None = typer.Option(None, "--ws", help="Filter by workstream id"),
    tag: str | None = typer.Option(None, "--tag", help="Filter by tag name"),
    types: list[str] = typer.Option(None, "--type", help="Event types to include (default: capture)"),
    limit: int = typer.Option(50, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    try:
        day_range = parse_range(day) if day else None
    except DateParseError as exc:
        raise typer.BadParameter(str(exc))

    type_filter: list[EventType] | None
    if types:
        try:
            type_filter = [EventType(t.lower()) for t in types]
        except ValueError as exc:
            raise typer.BadParameter(str(exc))
    else:
        type_filter = None

    with get_session() as session:
        events = EventService(session).list_events(
            day_range=day_range,
            workstream_id=workstream_id,
            tag=tag,
            types=type_filter,
            limit=limit,
        )
        if json_output:
            emit_json([_event_to_dict(e) for e in events])
            return
        if not events:
            typer.echo("(no events)")
            return
        for e in events:
            typer.echo(_format_event_text(e))


@event_app.command("show")
def event_show(
    event_id: str,
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    with get_session() as session:
        e = EventService(session).get_event(event_id)
        if not e:
            raise typer.BadParameter(f"Event not found: {event_id}")
        tag_names = [t.name for t in TagService(session).tags_for_event(event_id)]
        if json_output:
            emit_json(_event_to_dict(e, tags=tag_names))
            return
        typer.echo(_format_event_text(e))
        if tag_names:
            typer.echo(f"Tags: {', '.join(tag_names)}")
        if e.metadata_json:
            typer.echo(f"Metadata: {e.metadata_json}")


@event_app.command("search")
def event_search(
    query: str,
    limit: int = typer.Option(25, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    with get_session() as session:
        events = EventService(session).search(query, limit=limit)
        if json_output:
            emit_json([_event_to_dict(e) for e in events])
            return
        if not events:
            typer.echo("(no matches)")
            return
        for e in events:
            typer.echo(_format_event_text(e))


@event_app.command("tag")
def event_tag(
    event_id: str,
    tags: list[str] = typer.Argument(..., help="One or more tag names."),
) -> None:
    with get_session() as session:
        try:
            attached = TagService(session).attach(event_id, tags)
        except ValueError as exc:
            raise typer.BadParameter(str(exc))
        typer.echo(f"Tagged {event_id}: {', '.join(t.name for t in attached)}")


@tag_app.command("list")
def tag_list(json_output: bool = typer.Option(False, "--json")) -> None:
    with get_session() as session:
        rows = TagService(session).list_tags()
        if json_output:
            emit_json([{"name": tag.name, "event_count": count} for tag, count in rows])
            return
        if not rows:
            typer.echo("(no tags)")
            return
        for tag, count in rows:
            typer.echo(f"{tag.name} ({count})")


@tag_app.command("show")
def tag_show(
    name: str,
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    with get_session() as session:
        events = TagService(session).events_for_tag(name)
        if json_output:
            emit_json([_event_to_dict(e) for e in events])
            return
        if not events:
            typer.echo(f"(no events tagged '{name}')")
            return
        for e in events:
            typer.echo(_format_event_text(e))


@workstream_app.command("create")
def workstream_create(title: str, summary: str | None = None) -> None:
    with get_session() as session:
        ws = WorkstreamService(session).create(title=title, summary=summary)
        typer.echo(f"Created workstream: {ws.id}")


@workstream_app.command("list")
def workstream_list(json_output: bool = typer.Option(False, "--json", help="Emit JSON.")) -> None:
    with get_session() as session:
        workstreams = WorkstreamService(session).list_all()
        if json_output:
            emit_json(
                [
                    {
                        "id": ws.id,
                        "title": ws.title,
                        "status": ws.status.value,
                        "summary": ws.summary,
                        "last_activity_at": ws.last_activity_at,
                    }
                    for ws in workstreams
                ]
            )
            return
        for ws in workstreams:
            typer.echo(f"[{ws.status.value}] {ws.title} ({ws.id})")


@workstream_app.command("set-status")
def workstream_set_status(workstream_id: str, status: WorkstreamStatus) -> None:
    with get_session() as session:
        ws = WorkstreamService(session).set_status(workstream_id, status)
        if not ws:
            raise typer.BadParameter("Workstream not found")
        typer.echo(f"Workstream {ws.id} set to {ws.status.value}")


if __name__ == "__main__":
    app()
