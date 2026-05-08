from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from services.daemon_service import DaemonService

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _run(args: list[str], home: Path, timeout: int = 15) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["WORKLOG_HOME"] = str(home)
    env.pop("BITNET_CMD", None)
    env.pop("WORKLOG_INFERENCE_URL", None)
    return subprocess.run(
        [sys.executable, "-m", "cli.main", *args],
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _wait_until(predicate, timeout: float = 5.0, interval: float = 0.1) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_daemon_status_stopped_when_no_pid_file(tmp_path: Path) -> None:
    pid = tmp_path / "daemon.pid"
    hb = tmp_path / "daemon.hb"
    last = tmp_path / "daemon.last"
    service = DaemonService(str(pid), str(hb), str(last))
    assert service.status() == "stopped"
    assert service.is_running() is False


def test_heartbeat_writes_file_and_age(tmp_path: Path) -> None:
    pid = tmp_path / "daemon.pid"
    hb = tmp_path / "daemon.hb"
    service = DaemonService(str(pid), str(hb))
    service.heartbeat()
    assert hb.exists()
    age = service.heartbeat_age_seconds()
    assert age is not None and age >= 0


def test_mark_job_run_writes_last_job(tmp_path: Path) -> None:
    pid = tmp_path / "daemon.pid"
    hb = tmp_path / "daemon.hb"
    last = tmp_path / "daemon.last"
    service = DaemonService(str(pid), str(hb), str(last))
    service.mark_job_run()
    assert last.exists()
    assert service.last_job_age_seconds() == 0


def test_stop_returns_not_running_with_stale_pid(tmp_path: Path) -> None:
    pid = tmp_path / "daemon.pid"
    hb = tmp_path / "daemon.hb"
    pid.write_text("999999999", encoding="utf-8")
    service = DaemonService(str(pid), str(hb))
    result = service.stop()
    assert result == "stopped"
    assert not pid.exists()


def test_full_daemon_lifecycle_via_cli(tmp_path: Path) -> None:
    home = tmp_path / "wl"
    init = _run(["init-db"], home)
    assert init.returncode == 0, init.stderr

    started = _run(["daemon-start"], home)
    assert started.returncode == 0
    assert "started" in started.stdout

    pid_path = home / "data" / "worklog-daemon.pid"
    assert _wait_until(lambda: pid_path.exists(), timeout=3.0)

    status = _run(["daemon-status"], home)
    assert status.returncode == 0
    assert "running" in status.stdout

    stop = _run(["daemon-stop"], home)
    assert stop.returncode == 0
    assert "stopped" in stop.stdout

    after = _run(["daemon-status"], home)
    assert "stopped" in after.stdout
    assert not pid_path.exists()


def test_double_start_returns_already_running(tmp_path: Path) -> None:
    home = tmp_path / "wl"
    _run(["init-db"], home)
    try:
        first = _run(["daemon-start"], home)
        assert first.returncode == 0
        assert _wait_until(lambda: (home / "data" / "worklog-daemon.pid").exists(), timeout=3.0)
        second = _run(["daemon-start"], home)
        assert second.returncode == 0
        assert "already_running" in second.stdout
    finally:
        _run(["daemon-stop"], home)


def test_sigkill_fallback_when_process_ignores_sigterm(tmp_path: Path) -> None:
    pid_path = tmp_path / "daemon.pid"
    hb_path = tmp_path / "daemon.hb"

    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "[time.sleep(1) for _ in range(120)]",
        ],
    )
    pid_path.write_text(str(proc.pid), encoding="utf-8")
    hb_path.write_text(str(int(time.time())), encoding="utf-8")

    service = DaemonService(str(pid_path), str(hb_path))
    try:
        result = service.stop()
        assert result == "stopped"
        proc.wait(timeout=5)
        assert proc.returncode is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
