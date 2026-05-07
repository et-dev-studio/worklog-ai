from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import UTC, datetime

from services.paths_service import notifications_log_path


class NotificationService:
    TITLE = "Worklog AI"

    def __init__(self) -> None:
        self._notify_send_available: bool | None = None

    def notify_send_available(self) -> bool:
        if self._notify_send_available is None:
            self._notify_send_available = shutil.which("notify-send") is not None
        return self._notify_send_available

    def _log(self, message: str) -> None:
        log = notifications_log_path()
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now(UTC).isoformat()}\t{message}\n")

    def send(self, message: str) -> None:
        if self.notify_send_available():
            try:
                subprocess.run(["notify-send", self.TITLE, message], check=True)
                return
            except (FileNotFoundError, subprocess.CalledProcessError):
                pass
        self._log(message)
        if sys.stderr.isatty():
            print(f"[{self.TITLE}] {message}", file=sys.stderr)
