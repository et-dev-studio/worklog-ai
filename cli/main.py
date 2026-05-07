from __future__ import annotations

from datetime import date
import asyncio
from pathlib import Path

import questionary
import typer

from agents.reflection_agent import ReflectionAgent
from agents.summary_agent import SummaryAgent
from db.models import Base, WorkstreamStatus
from db.session import engine, get_session
from services.event_service import EventService
from services.daemon_service import DaemonService
from services.export_service import ExportFormat, ExportService
from services.inference_service import InferenceService
from services.notification_service import NotificationService
from services.prompt_service import PromptService
from services.reflection_service import ReflectionService
from services.scheduler_service import SchedulerService
from services.summary_service import SummaryService
from services.workstream_service import WorkstreamService

app = typer.Typer(help="Worklog AI CLI")
workstream_app = typer.Typer(help="Workstream operations")
app.add_typer(workstream_app, name="workstream")
ACTIVE_WS_FILE = Path("data/.active_workstream")


def _get_active_workstream_id() -> str | None:
    if ACTIVE_WS_FILE.exists():
        return ACTIVE_WS_FILE.read_text(encoding="utf-8").strip() or None
    return None


@app.command()
def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    typer.echo("Database initialized.")


@app.command()
def add(content: str, workstream_id: str | None = None) -> None:
    with get_session() as session:
        event_service = EventService(session)
        selected_workstream_id = workstream_id
        if not selected_workstream_id:
            suggestions = event_service.suggest_workstreams(content)
            if suggestions:
                choices = [questionary.Choice(title=f"{ws.title} ({ws.id})", value=ws.id) for ws in suggestions]
                choices.append(questionary.Choice(title="No attachment", value=""))
                selected_workstream_id = questionary.select("Attach to a suggested workstream?", choices=choices).ask()
                selected_workstream_id = selected_workstream_id or None
            else:
                active = _get_active_workstream_id()
                if active:
                    event_service.set_active_workstream(active)

        event = event_service.add_capture(content=content, workstream_id=selected_workstream_id)
        typer.echo(f"Captured event: {event.id}")


@app.command()
def connect(event_id: str | None = None, workstream_id: str | None = None) -> None:
    with get_session() as session:
        event_service = EventService(session)
        if not event_id:
            events = event_service.list_recent(limit=20)
            if not events:
                typer.echo("No events available.")
                return
            event_id = questionary.select(
                "Select event:",
                choices=[questionary.Choice(title=f"{e.timestamp.isoformat()} | {e.content} ({e.id})", value=e.id) for e in events],
            ).ask()

        if not workstream_id:
            workstreams = WorkstreamService(session).list_all()
            if not workstreams:
                typer.echo("No workstreams available.")
                return
            workstream_id = questionary.select(
                "Select workstream:",
                choices=[questionary.Choice(title=f"[{w.status.value}] {w.title} ({w.id})", value=w.id) for w in workstreams],
            ).ask()

        event = event_service.connect_event(event_id=event_id, workstream_id=workstream_id)
        if not event:
            raise typer.BadParameter("Event not found")
        typer.echo(f"Connected {event_id} -> {workstream_id}")


@app.command()
def status() -> None:
    with get_session() as session:
        ws_service = WorkstreamService(session)
        ref_service = ReflectionService(session)
        active = [ws for ws in ws_service.list_all() if ws.status == WorkstreamStatus.ACTIVE]
        unresolved = ref_service.unresolved_capture_events(limit=10)
        typer.echo(f"Active workstreams: {len(active)}")
        for ws in active:
            typer.echo(f"- {ws.title} ({ws.id})")
        typer.echo(f"Pending reflections: {len(unresolved)}")


@app.command()
def resume(workstream_id: str) -> None:
    ACTIVE_WS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE_WS_FILE.write_text(workstream_id, encoding="utf-8")
    typer.echo(f"Active workstream set: {workstream_id}")


@app.command()
def reflect() -> None:
    inference = InferenceService()
    prompts = PromptService()
    agent = ReflectionAgent(inference, prompts)
    with get_session() as session:
        reflection_service = ReflectionService(session)
        unresolved = reflection_service.unresolved_capture_events(limit=5)
        if not unresolved:
            typer.echo("No unresolved events.")
            return

        for event in unresolved:
            question = asyncio.run(agent.generate_questions(event.content))
            answer = questionary.text(f"{event.content}\n{question}\nAnswer:").ask()
            if answer:
                reflection_service.add_reflection(event_id=event.id, question=question, answer=answer)
        typer.echo("Reflection session complete.")


@app.command()
def summary(day: str = date.today().isoformat(), export_format: ExportFormat = ExportFormat.MARKDOWN) -> None:
    target_day = date.fromisoformat(day)
    inference = InferenceService()
    prompts = PromptService()
    agent = SummaryAgent(inference, prompts)
    with get_session() as session:
        summary_service = SummaryService(session)
        context = summary_service.grouped_context(target_day)
        text = asyncio.run(agent.summarize(context))
        typer.echo(text)

        decision = questionary.select("Accept summary?", choices=["yes", "edit", "no"]).ask()
        if decision == "no":
            typer.echo("Summary discarded.")
            return
        if decision == "edit":
            edited = questionary.text("Edit summary:", default=text).ask()
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
    import time
    daemon = DaemonService()
    SchedulerService(NotificationService()).start_default_reminders()
    while True:
        daemon.heartbeat()
        time.sleep(60)


@app.command("daemon-start")
def daemon_start() -> None:
    result = DaemonService().start()
    typer.echo(f"Daemon {result}.")


@app.command("daemon-stop")
def daemon_stop() -> None:
    result = DaemonService().stop()
    typer.echo(f"Daemon {result}.")




@app.command("daemon-restart")
def daemon_restart() -> None:
    result = DaemonService().restart()
    typer.echo(f"Daemon {result}.")

@app.command("daemon-status")
def daemon_status() -> None:
    typer.echo(f"Daemon {DaemonService().status()}.")


@workstream_app.command("create")
def workstream_create(title: str, summary: str | None = None) -> None:
    with get_session() as session:
        ws = WorkstreamService(session).create(title=title, summary=summary)
        typer.echo(f"Created workstream: {ws.id}")


@workstream_app.command("list")
def workstream_list() -> None:
    with get_session() as session:
        workstreams = WorkstreamService(session).list_all()
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
