from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path


from services.paths_service import (
    daemon_heartbeat_path,
    daemon_last_job_path,
    daemon_pid_path,
    project_root,
)


class DaemonService:
    def __init__(
        self,
        pid_file: str | None = None,
        heartbeat_file: str | None = None,
        last_job_file: str | None = None,
    ):
        self.pid_path = Path(pid_file) if pid_file else daemon_pid_path()
        self.heartbeat_path = Path(heartbeat_file) if heartbeat_file else daemon_heartbeat_path()
        self.last_job_path = Path(last_job_file) if last_job_file else daemon_last_job_path()

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
        for p in (self.pid_path, self.heartbeat_path, self.last_job_path):
            if p.exists():
                p.unlink()

    def mark_job_run(self) -> None:
        self.last_job_path.parent.mkdir(parents=True, exist_ok=True)
        self.last_job_path.write_text(str(int(time.time())), encoding="utf-8")

    def last_job_age_seconds(self) -> int | None:
        if not self.last_job_path.exists():
            return None
        try:
            last = int(self.last_job_path.read_text(encoding="utf-8").strip())
            return max(0, int(time.time()) - last)
        except ValueError:
            return None

    def start(self) -> str:
        if self.is_running():
            return "already_running"
        self.pid_path.parent.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        proc = subprocess.Popen(
            [sys.executable, "-m", "cli.main", "scheduler-run"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            cwd=str(project_root()),
            env=env,
        )
        self.pid_path.write_text(str(proc.pid), encoding="utf-8")
        self.heartbeat()
        return "started"

    def _wait_for_exit(self, pid: int, timeout: float = 5.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                return True
            time.sleep(0.1)
        return False

    def stop(self) -> str:
        pid = self._read_pid()
        if not pid:
            self._cleanup_files()
            return "not_running"
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            self._cleanup_files()
            return "stopped"
        if not self._wait_for_exit(pid, timeout=5.0):
            try:
                os.kill(pid, signal.SIGKILL)
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
        if self._job_health_stale():
            return "degraded"
        return "running"

    def _job_health_stale(self) -> bool:
        last_job_age = self.last_job_age_seconds()
        if last_job_age is None:
            uptime = self.heartbeat_age_seconds() or 0
            try:
                pid_age = int(time.time()) - int(self.pid_path.stat().st_mtime)
            except (OSError, ValueError):
                pid_age = 0
            return max(uptime, pid_age) > self._max_expected_gap_seconds() * 2
        return last_job_age > self._max_expected_gap_seconds() * 2

    def _max_expected_gap_seconds(self) -> int:
        try:
            from services.config_service import ConfigService

            cfg = ConfigService().load()
            times = []
            for key in ("lunch", "evening"):
                hm = cfg.get(key, {}).get("time")
                if hm:
                    h, m = hm.split(":")
                    times.append(int(h) * 60 + int(m))
            if len(times) < 2:
                return 24 * 3600
            times.sort()
            gaps = [times[i + 1] - times[i] for i in range(len(times) - 1)]
            gaps.append((24 * 60) - times[-1] + times[0])
            return max(gaps) * 60
        except Exception:
            return 24 * 3600
