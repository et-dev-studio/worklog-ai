from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path


class DaemonService:
    def __init__(self, pid_file: str = "data/worklog-daemon.pid", heartbeat_file: str = "data/worklog-daemon.heartbeat"):
        self.pid_path = Path(pid_file)
        self.heartbeat_path = Path(heartbeat_file)

    def _read_pid(self) -> int | None:
        if not self.pid_path.exists():
            return None
        try:
            return int(self.pid_path.read_text(encoding="utf-8").strip())
        except ValueError:
            return None

    def heartbeat(self) -> None:
        self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        self.heartbeat_path.write_text(str(int(time.time())), encoding="utf-8")

    def heartbeat_age_seconds(self) -> int | None:
        if not self.heartbeat_path.exists():
            return None
        try:
            last = int(self.heartbeat_path.read_text(encoding="utf-8").strip())
            return max(0, int(time.time()) - last)
        except ValueError:
            return None

    def is_running(self) -> bool:
        pid = self._read_pid()
        if not pid:
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            self._cleanup_files()
            return False

    def _cleanup_files(self) -> None:
        if self.pid_path.exists():
            self.pid_path.unlink()
        if self.heartbeat_path.exists():
            self.heartbeat_path.unlink()

    def start(self) -> str:
        if self.is_running():
            return "already_running"
        self.pid_path.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(
            [sys.executable, "-m", "cli.main", "scheduler-run"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.pid_path.write_text(str(proc.pid), encoding="utf-8")
        self.heartbeat()
        return "started"

    def stop(self) -> str:
        pid = self._read_pid()
        if not pid:
            self._cleanup_files()
            return "not_running"
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        self._cleanup_files()
        return "stopped"

    def restart(self) -> str:
        self.stop()
        return self.start()

    def status(self) -> str:
        if not self.is_running():
            return "stopped"
        age = self.heartbeat_age_seconds()
        if age is not None and age > 180:
            return "degraded"
        return "running"
