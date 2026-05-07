from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _run(args: list[str], home: Path, stdin: str | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["WORKLOG_HOME"] = str(home)
    env.pop("BITNET_CMD", None)
    return subprocess.run(
        [sys.executable, "-m", "cli.main", *args],
        cwd=str(PROJECT_ROOT),
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=20,
    )


def _init(home: Path) -> str:
    assert _run(["init-db"], home).returncode == 0
    return home.as_posix()


def _create_ws(home: Path, title: str) -> str:
    out = _run(["workstream", "create", title], home).stdout
    return out.strip().split(": ", 1)[1]


def test_status_json_output(tmp_path: Path) -> None:
    home = tmp_path / "wl"
    _init(home)
    _create_ws(home, "demo")
    result = _run(["status", "--json"], home)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["pending_reflections"] == 0
    assert payload["active_workstreams"][0]["title"] == "demo"


def test_workstream_list_json(tmp_path: Path) -> None:
    home = tmp_path / "wl"
    _init(home)
    _create_ws(home, "ws-a")
    _create_ws(home, "ws-b")
    result = _run(["workstream", "list", "--json"], home)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    titles = {row["title"] for row in payload}
    assert {"ws-a", "ws-b"}.issubset(titles)


def test_event_list_filters_by_workstream_and_tag(tmp_path: Path) -> None:
    home = tmp_path / "wl"
    _init(home)
    ws_a = _create_ws(home, "ws-a")
    ws_b = _create_ws(home, "ws-b")
    _run(["add", "alpha event", "--workstream-id", ws_a, "--tag", "bug", "--quiet"], home)
    _run(["add", "beta event", "--workstream-id", ws_b, "--tag", "feature", "--quiet"], home)

    by_ws = _run(["event", "list", "--ws", ws_a, "--json"], home)
    assert by_ws.returncode == 0
    rows = json.loads(by_ws.stdout)
    assert len(rows) == 1 and rows[0]["content"] == "alpha event"

    by_tag = _run(["event", "list", "--tag", "feature", "--json"], home)
    rows = json.loads(by_tag.stdout)
    assert len(rows) == 1 and rows[0]["content"] == "beta event"


def test_event_search_uses_fts(tmp_path: Path) -> None:
    home = tmp_path / "wl"
    _init(home)
    _run(["add", "redis pool drops", "--quiet"], home)
    _run(["add", "auth oauth flow", "--quiet"], home)
    result = _run(["event", "search", "redis"], home)
    assert result.returncode == 0
    assert "redis pool drops" in result.stdout
    assert "auth oauth" not in result.stdout


def test_undo_marks_event_voided(tmp_path: Path) -> None:
    home = tmp_path / "wl"
    _init(home)
    add = _run(["add", "typo", "--quiet"], home)
    event_id = add.stdout.strip().split(": ", 1)[1]

    voided = _run(["undo", "--reason", "wrong-content"], home)
    assert voided.returncode == 0
    assert event_id in voided.stdout

    listing = _run(["event", "list", "--json"], home)
    rows = json.loads(listing.stdout)
    assert all(r["id"] != event_id for r in rows)

    with_voided = _run(["event", "list", "--type", "capture", "--type", "voided", "--json"], home)
    rows = json.loads(with_voided.stdout)
    assert any(r["type"] == "voided" for r in rows)


def test_doctor_json_reports_components(tmp_path: Path) -> None:
    home = tmp_path / "wl"
    _init(home)
    result = _run(["doctor", "--json"], home)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["worklog_home"] == str(home.resolve())
    assert payload["alembic_revision"] == "0005_events_fts"
    assert payload["bitnet"]["configured"] is False
    assert payload["missing_prompts"] == []


def test_daemon_dashboard_json(tmp_path: Path) -> None:
    home = tmp_path / "wl"
    _init(home)
    _run(["add", "dashed event", "--quiet"], home)
    result = _run(["daemon-dashboard", "--json"], home)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert "schedules" in payload and "lunch" in payload["schedules"]
    assert payload["recent_events"][0]["content"] == "dashed event"


def test_add_stdin_dash(tmp_path: Path) -> None:
    home = tmp_path / "wl"
    _init(home)
    result = _run(["add", "-", "--quiet"], home, stdin="piped capture")
    assert result.returncode == 0
    listing = _run(["event", "list", "--json"], home)
    rows = json.loads(listing.stdout)
    assert any(r["content"] == "piped capture" for r in rows)


def test_dateparse_today_yesterday(tmp_path: Path) -> None:
    from services.dateparse_service import parse_day, parse_range
    from datetime import date, timedelta

    today = date(2026, 5, 7)
    assert parse_day("today", today=today) == today
    assert parse_day("yesterday", today=today) == today - timedelta(days=1)
    assert parse_day("-3d", today=today) == today - timedelta(days=3)
    assert parse_day("2026-01-15") == date(2026, 1, 15)
    start, end = parse_range("this-week", today=today)
    assert start == today - timedelta(days=today.weekday())
    assert end == today
