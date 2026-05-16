"""Worklog v2 CLI entry point (Typer).

Phase-5 minimum viable surface. Each command opens a fresh
``services.storage.postgres.get_session()`` and runs the async service
call via ``asyncio.run``. The connection role IS the acting user — RLS
evaluates against the role embedded in ``WORKLOG_DB_URL``.

Commands covered by this phase:
    init-db, doctor,
    status, resume, undo,
    add (alias `a`),
    workstream create|list|set-status,
    event list|show|search,
    team add|list|remove,
    notify-test.

Deferred to follow-up phases:
    reflect, summary, connect       (phase 8 — needs agent layer)
    daemon-start|stop|restart|...   (still uses scheduler_service;
                                     rewires in a follow-up commit)
    memory|recall|meeting|agent     (phase 6/7/8)
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date
from pathlib import Path
from typing import Optional

import typer
from sqlalchemy import func, select, text

from cli.output import emit, emit_json
from cli.prompts import is_tty, require_tty
from db.models import (
    RawEvent,
    RawEventKind,
    User,
    Visibility,
    Workstream,
    WorkstreamStatus,
)
from services import (
    event_service,
    paths_service,
    user_service,
    workstream_service,
)
from services.storage import init_engine, get_session
from services.storage.postgres import _resolve_db_url

app = typer.Typer(help="worklog-ai v2 — collaborative engineering memory CLI")

workstream_app = typer.Typer(help="Workstream lifecycle commands.")
event_app = typer.Typer(help="Browse the raw event log.")
team_app = typer.Typer(help="Multi-user team provisioning (admin only).")

app.add_typer(workstream_app, name="workstream")
app.add_typer(event_app, name="event")
app.add_typer(team_app, name="team")


# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------


def _run(coro):
    """Wrap an async impl in asyncio.run; ensure engine is initialised."""

    async def _runner():
        await init_engine()
        return await coro

    return asyncio.run(_runner())


async def _current_user(session) -> User | None:
    """Resolve the User row for the connected Postgres role."""
    row = (
        await session.execute(text("SELECT current_user_id()"))
    ).scalar_one_or_none()
    if row is None:
        return None
    return await session.get(User, row)


async def _require_current_user(session) -> User:
    user = await _current_user(session)
    if user is None:
        raise typer.BadParameter(
            "No users row matches the connected Postgres role. "
            "Run `wl team add` (as admin) to provision a user, then "
            "set WORKLOG_DB_URL to that user's role."
        )
    return user


def _active_workstream_path() -> Path:
    return paths_service.active_workstream_path()


def _read_active_workstream() -> int | None:
    p = _active_workstream_path()
    if not p.exists():
        return None
    raw = p.read_text().strip()
    try:
        return int(raw)
    except ValueError:
        return None


def _write_active_workstream(ws_id: int) -> None:
    _active_workstream_path().write_text(str(ws_id))


# ---------------------------------------------------------------------------
# init-db
# ---------------------------------------------------------------------------


@app.command("init-db")
def init_db() -> None:
    """Run alembic upgrade head against WORKLOG_DB_SERVICE_ROLE_KEY."""
    from alembic import command as alembic_command
    from alembic.config import Config

    cfg = Config(str(paths_service.project_root() / "alembic.ini"))
    cfg.set_main_option("script_location", str(paths_service.project_root() / "alembic"))
    # env.py resolves the URL from WORKLOG_DB_SERVICE_ROLE_KEY itself.
    alembic_command.upgrade(cfg, "head")
    typer.echo("init-db complete.")


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


@app.command()
def doctor(json_output: bool = typer.Option(False, "--json")) -> None:
    """Report Postgres + schema health."""

    async def _impl():
        report: dict = {
            "worklog_home": str(paths_service.home()),
            "project_root": str(paths_service.project_root()),
            "database_url": _resolve_db_url().split("@", 1)[-1],  # strip credentials
        }
        try:
            async with get_session() as session:
                report["alembic_head"] = (
                    await session.execute(
                        text("SELECT version_num FROM alembic_version")
                    )
                ).scalar_one_or_none()
                report["pgvector_installed"] = bool(
                    (
                        await session.execute(
                            text(
                                "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
                            )
                        )
                    ).scalar_one_or_none()
                )
                report["rls_enabled_tables"] = (
                    await session.execute(
                        text(
                            "SELECT array_agg(tablename) FROM pg_tables "
                            "WHERE schemaname = 'public' AND rowsecurity = true"
                        )
                    )
                ).scalar_one_or_none()
                user = await _current_user(session)
                report["current_user_role"] = (
                    (await session.execute(text("SELECT current_user"))).scalar_one()
                )
                report["current_user_id"] = str(user.id) if user else None
                report["users_count"] = (
                    await session.execute(select(func.count()).select_from(User))
                ).scalar_one()
                report["workstreams_count"] = (
                    await session.execute(select(func.count()).select_from(Workstream))
                ).scalar_one()
        except Exception as exc:
            report["error"] = str(exc)

        return report

    payload = _run(_impl())

    if json_output:
        emit_json(payload)
        return

    lines = [f"{k}: {v}" for k, v in payload.items()]
    typer.echo("\n".join(lines))


# ---------------------------------------------------------------------------
# status / resume / undo
# ---------------------------------------------------------------------------


@app.command()
def status(json_output: bool = typer.Option(False, "--json")) -> None:
    async def _impl():
        async with get_session() as session:
            active = (
                await session.execute(
                    select(func.count())
                    .select_from(Workstream)
                    .where(Workstream.status == WorkstreamStatus.ACTIVE.value)
                )
            ).scalar_one()
            pending = (
                await session.execute(
                    select(func.count())
                    .select_from(RawEvent)
                    .where(RawEvent.kind == RawEventKind.CAPTURE.value)
                )
            ).scalar_one()
            return {
                "active_workstreams": active,
                "captures_total": pending,
                "active_workstream_pointer": _read_active_workstream(),
            }

    payload = _run(_impl())
    emit(
        payload,
        json_output,
        f"Active workstreams: {payload['active_workstreams']}\n"
        f"Captures (total): {payload['captures_total']}\n"
        f"Active pointer: {payload['active_workstream_pointer']}",
    )


@app.command()
def resume(workstream_id: int) -> None:
    """Pin a workstream as the default target for `wl add`."""

    async def _impl():
        async with get_session() as session:
            ws = await session.get(Workstream, workstream_id)
            if ws is None:
                raise typer.BadParameter(f"Workstream {workstream_id} not found.")
            return ws.title

    title = _run(_impl())
    _write_active_workstream(workstream_id)
    typer.echo(f"Active workstream set to {workstream_id} ({title}).")


@app.command()
def undo(
    event_id: Optional[int] = typer.Argument(None),
    reason: Optional[str] = typer.Option(None, "--reason"),
) -> None:
    """Soft-delete a capture by writing a VOIDED audit row."""

    async def _impl():
        async with get_session() as session:
            actor = await _require_current_user(session)
            target_id = event_id
            if target_id is None:
                latest = (
                    await session.execute(
                        select(RawEvent)
                        .where(RawEvent.kind == RawEventKind.CAPTURE.value)
                        .where(RawEvent.owner_id == actor.id)
                        .order_by(RawEvent.ts.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if latest is None:
                    raise typer.BadParameter("No captures to void.")
                target_id = latest.id
            voided = await event_service.void_event(
                session, target_id, actor_id=actor.id, reason=reason
            )
            await session.commit()
            return target_id, voided is not None

    target_id, ok = _run(_impl())
    if ok:
        typer.echo(f"Voided event {target_id}.")
    else:
        typer.echo(f"Event {target_id} not found.", err=True)
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# add (alias: a)
# ---------------------------------------------------------------------------


@app.command()
@app.command("a", hidden=True)
def add(
    content: str = typer.Argument(..., help="Event text; use '-' to read from stdin."),
    workstream_id: Optional[int] = typer.Option(None, "--workstream-id"),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    if content == "-":
        content = sys.stdin.read().strip()

    async def _impl():
        async with get_session() as session:
            actor = await _require_current_user(session)
            ws_id = workstream_id or _read_active_workstream()
            if ws_id is None and not (quiet or yes or not is_tty()):
                suggestions = await event_service.suggest_workstreams(
                    session, content, limit=3
                )
                if suggestions:
                    typer.echo("Top workstream suggestions:")
                    for ws in suggestions:
                        typer.echo(f"  {ws.id}  [{ws.status}]  {ws.title}")
                    typer.echo(
                        "Pass --workstream-id <id> to attach, or leave unattached."
                    )
            event = await event_service.add_capture(
                session, content=content, owner_id=actor.id, workstream_id=ws_id
            )
            await session.commit()
            return event.id, ws_id

    event_id, ws_id = _run(_impl())
    typer.echo(f"Captured event: {event_id}" + (f" [ws {ws_id}]" if ws_id else ""))


# ---------------------------------------------------------------------------
# workstream subcommands
# ---------------------------------------------------------------------------


@workstream_app.command("create")
def workstream_create(
    title: str,
    summary: Optional[str] = typer.Option(None, "--summary"),
) -> None:
    async def _impl():
        async with get_session() as session:
            actor = await _require_current_user(session)
            ws = await workstream_service.create(
                session,
                title=title,
                owner_id=actor.id,
                summary=summary,
            )
            await session.commit()
            return ws.id

    ws_id = _run(_impl())
    typer.echo(f"Created workstream: {ws_id}")


@workstream_app.command("list")
def workstream_list(json_output: bool = typer.Option(False, "--json")) -> None:
    async def _impl():
        async with get_session() as session:
            workstreams = await workstream_service.list_all(session)
            return [
                {
                    "id": w.id,
                    "title": w.title,
                    "status": w.status,
                    "visibility": w.visibility,
                    "last_activity_at": w.last_activity_at,
                }
                for w in workstreams
            ]

    rows = _run(_impl())
    if json_output:
        emit_json(rows)
        return
    if not rows:
        typer.echo("No workstreams.")
        return
    for r in rows:
        typer.echo(f"{r['id']}\t[{r['status']}]\t{r['title']}")


@workstream_app.command("set-status")
def workstream_set_status(workstream_id: int, new_status: WorkstreamStatus) -> None:
    async def _impl():
        async with get_session() as session:
            actor = await _require_current_user(session)
            updated = await workstream_service.set_status(
                session, workstream_id, new_status, actor_id=actor.id
            )
            await session.commit()
            return updated

    result = _run(_impl())
    if result is None:
        typer.echo(f"Workstream {workstream_id} not found.", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"{result.id}\t[{result.status}]\t{result.title}")


# ---------------------------------------------------------------------------
# event subcommands
# ---------------------------------------------------------------------------


@event_app.command("list")
def event_list(
    workstream_id: Optional[int] = typer.Option(None, "--ws"),
    limit: int = typer.Option(50, "--limit"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    async def _impl():
        async with get_session() as session:
            events = await event_service.list_events(
                session, workstream_id=workstream_id, limit=limit
            )
            return [
                {
                    "id": e.id,
                    "ts": e.ts,
                    "kind": e.kind,
                    "workstream_id": e.workstream_id,
                    "content": e.content,
                }
                for e in events
            ]

    rows = _run(_impl())
    if json_output:
        emit_json(rows)
        return
    if not rows:
        typer.echo("No events.")
        return
    for r in rows:
        ws = f" [ws {r['workstream_id']}]" if r["workstream_id"] else ""
        typer.echo(f"{r['id']}\t{r['ts']}\t{r['kind']}{ws}\t{r['content']}")


@event_app.command("show")
def event_show(
    event_id: int,
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    async def _impl():
        async with get_session() as session:
            event = await event_service.get_event(session, event_id)
            if event is None:
                return None
            return {
                "id": event.id,
                "ts": event.ts,
                "kind": event.kind,
                "workstream_id": event.workstream_id,
                "owner_id": event.owner_id,
                "source_uri": event.source_uri,
                "metadata": event.event_metadata,
                "content": event.content,
            }

    row = _run(_impl())
    if row is None:
        typer.echo(f"Event {event_id} not found.", err=True)
        raise typer.Exit(code=1)
    emit(
        row,
        json_output,
        "\n".join(f"{k}: {v}" for k, v in row.items()),
    )


@event_app.command("search")
def event_search(
    query: str,
    limit: int = typer.Option(25, "--limit"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    async def _impl():
        async with get_session() as session:
            hits = await event_service.search(session, query, limit=limit)
            return [
                {
                    "id": e.id,
                    "ts": e.ts,
                    "kind": e.kind,
                    "content": e.content,
                }
                for e in hits
            ]

    rows = _run(_impl())
    if json_output:
        emit_json(rows)
        return
    if not rows:
        typer.echo("No hits.")
        return
    for r in rows:
        typer.echo(f"{r['id']}\t{r['ts']}\t{r['kind']}\t{r['content']}")


# ---------------------------------------------------------------------------
# team subcommands (admin)
# ---------------------------------------------------------------------------


@team_app.command("add")
def team_add(
    display_name: str,
    email: Optional[str] = typer.Option(None, "--email"),
    pg_role: Optional[str] = typer.Option(None, "--role"),
    skip_role: bool = typer.Option(
        False,
        "--skip-role",
        help="Don't run CREATE ROLE — useful if the role already exists.",
    ),
) -> None:
    """Provision a new user. Requires admin/service connection."""

    async def _impl():
        async with get_session() as session:
            user, password = await user_service.create_user(
                session,
                display_name=display_name,
                email=email,
                pg_role=pg_role,
                create_pg_role=not skip_role,
            )
            await session.commit()
            return user, password

    user, password = _run(_impl())
    typer.echo(f"Created user: {user.id} (role={user.pg_role})")
    if password is not None:
        typer.echo(
            "Generated password (hand to user out of band, won't be shown again):"
        )
        typer.echo(f"  {password}")


@team_app.command("list")
def team_list(json_output: bool = typer.Option(False, "--json")) -> None:
    async def _impl():
        async with get_session() as session:
            users = await user_service.list_users(session)
            return [
                {
                    "id": u.id,
                    "pg_role": u.pg_role,
                    "display_name": u.display_name,
                    "email": u.email,
                    "created_at": u.created_at,
                }
                for u in users
            ]

    rows = _run(_impl())
    if json_output:
        emit_json(rows)
        return
    if not rows:
        typer.echo("No users.")
        return
    for r in rows:
        typer.echo(f"{r['id']}\t{r['pg_role']}\t{r['display_name']}\t{r['email'] or ''}")


@team_app.command("remove")
def team_remove(pg_role: str) -> None:
    """Drop a user + their Postgres role. Requires admin connection."""

    async def _impl():
        async with get_session() as session:
            await user_service.remove_user(session, pg_role=pg_role)
            await session.commit()

    _run(_impl())
    typer.echo(f"Removed user with role: {pg_role}")


# ---------------------------------------------------------------------------
# notify-test (kept; v1 notification_service still works)
# ---------------------------------------------------------------------------


@app.command("notify-test")
def notify_test(
    message: str = typer.Argument("Worklog v2 test notification"),
) -> None:
    from services.notification_service import NotificationService

    NotificationService().send("Worklog", message)
    typer.echo("Notification dispatched (or logged to fallback).")


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
