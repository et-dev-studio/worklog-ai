from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from cli.main import app
from services.daemon_service import DaemonService

PROJECT_ROOT = Path(__file__).resolve().parent.parent
runner = CliRunner()


def _env(home: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["WORKLOG_HOME"] = str(home)
    env.pop("BITNET_CMD", None)
    env.pop("WORKLOG_INFERENCE_URL", None)
    return env


def _shell(args: list[str], home: Path, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "cli.main", *args],
        cwd=str(PROJECT_ROOT),
        env=_env(home),
        input=stdin,
        capture_output=True,
        text=True,
        timeout=15,
    )


def _init(home: Path) -> None:
    result = _shell(["init-db"], home)
    assert result.returncode == 0, result.stderr


def test_init_db_idempotent(tmp_path: Path) -> None:
    home = tmp_path / "wl"
    first = _shell(["init-db"], home)
    second = _shell(["init-db"], home)
    assert first.returncode == 0
    assert second.returncode == 0


def test_workstream_create_list_set_status(tmp_path: Path) -> None:
    home = tmp_path / "wl"
    _init(home)

    create = _shell(["workstream", "create", "AUTH-1 oauth"], home)
    assert create.returncode == 0
    ws_id = create.stdout.strip().split(": ", 1)[1]

    listed = _shell(["workstream", "list"], home)
    assert listed.returncode == 0
    assert "AUTH-1 oauth" in listed.stdout
    assert "[active]" in listed.stdout

    paused = _shell(["workstream", "set-status", ws_id, "paused"], home)
    assert paused.returncode == 0
    assert "paused" in paused.stdout


def test_status_command(tmp_path: Path) -> None:
    home = tmp_path / "wl"
    _init(home)
    _shell(["workstream", "create", "demo ws"], home)
    result = _shell(["status"], home)
    assert result.returncode == 0
    assert "Active workstreams: 1" in result.stdout
    assert "Pending reflections: 0" in result.stdout


def test_resume_validates_workstream_id(tmp_path: Path) -> None:
    home = tmp_path / "wl"
    _init(home)
    bad = _shell(["resume", "00000000-0000-0000-0000-000000000000"], home)
    assert bad.returncode != 0
    assert "Workstream not found" in bad.stderr or "Workstream not found" in bad.stdout


def test_add_with_explicit_workstream_id(tmp_path: Path) -> None:
    home = tmp_path / "wl"
    _init(home)
    create = _shell(["workstream", "create", "PLATFORM-1"], home)
    ws_id = create.stdout.strip().split(": ", 1)[1]
    add = _shell(["add", "captured", "--workstream-id", ws_id], home)
    assert add.returncode == 0
    assert "Captured event:" in add.stdout


def test_add_non_tty_yes_returns_zero(tmp_path: Path) -> None:
    home = tmp_path / "wl"
    _init(home)
    result = _shell(["add", "stdin event", "--yes"], home, stdin="")
    assert result.returncode == 0
    assert "Captured event:" in result.stdout


def test_connect_no_data_flow_exits_clean() -> None:
    result = runner.invoke(app, ["connect"])
    assert result.exit_code == 0
    assert "No events available." in result.stdout


def test_connect_non_tty_requires_explicit_ids(tmp_path: Path) -> None:
    home = tmp_path / "wl"
    _init(home)
    create = _shell(["workstream", "create", "WS"], home)
    ws_id = create.stdout.strip().split(": ", 1)[1]
    _shell(["add", "an event", "--workstream-id", ws_id], home)
    result = _shell(["connect"], home, stdin="")
    assert result.returncode == 2
    assert "--event-id" in result.stderr


def test_summary_invalid_day_format(tmp_path: Path) -> None:
    home = tmp_path / "wl"
    _init(home)
    result = _shell(["summary", "--day", "not-a-date", "--yes"], home)
    assert result.returncode != 0


def test_notify_test_writes_log_when_notify_send_missing(tmp_path: Path) -> None:
    home = tmp_path / "wl"
    _init(home)
    env = _env(home)
    env["PATH"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "cli.main", "notify-test", "hello"],
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    log = home / "data" / "notifications.log"
    assert log.exists()
    assert "hello" in log.read_text(encoding="utf-8")


def test_daemon_status_stopped_with_temp_files(tmp_path: Path) -> None:
    pid = tmp_path / "daemon.pid"
    hb = tmp_path / "daemon.hb"
    service = DaemonService(str(pid), str(hb))
    assert service.status() == "stopped"
    service.heartbeat()
    assert hb.exists()
