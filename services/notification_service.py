from __future__ import annotations

import subprocess


class NotificationService:
    def send(self, message: str) -> None:
        subprocess.run(["notify-send", "Worklog AI", message], check=False)
