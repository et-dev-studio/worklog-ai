from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler

from db.session import get_session
from services.config_service import ConfigService
from services.daemon_service import DaemonService
from services.notification_service import NotificationService
from services.reflection_service import ReflectionService


class SchedulerService:
    def __init__(
        self,
        notifier: NotificationService,
        config: ConfigService | None = None,
        daemon: DaemonService | None = None,
    ):
        self.notifier = notifier
        self.config = config or ConfigService()
        self.daemon = daemon or DaemonService()
        self.scheduler = BackgroundScheduler()

    def _parse_hm(self, value: str) -> tuple[int, int]:
        hour, minute = value.split(":")
        return int(hour), int(minute)

    def _pending_reflections(self) -> list:
        with get_session() as session:
            return ReflectionService(session).unresolved_capture_events(limit=1000)

    def start_default_reminders(self) -> None:
        cfg = self.config.load()
        lunch_hour, lunch_minute = self._parse_hm(cfg.get("lunch", {}).get("time", "13:00"))
        evening_hour, evening_minute = self._parse_hm(cfg.get("evening", {}).get("time", "18:00"))
        self.scheduler.add_job(self._lunch_reminder, "cron", hour=lunch_hour, minute=lunch_minute)
        self.scheduler.add_job(self._evening_reminder, "cron", hour=evening_hour, minute=evening_minute)
        self.scheduler.start()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def _lunch_reminder(self) -> None:
        pending = self._pending_reflections()
        if not pending:
            self.notifier.send("Lunch check-in: no pending items. Worklog clean.")
        else:
            top = "; ".join(e.content[:60] for e in pending[:3])
            self.notifier.send(
                f"Lunch check-in: {len(pending)} items pending. Top: {top}. Run `wl reflect`."
            )
        self.daemon.mark_job_run()

    def _evening_reminder(self) -> None:
        pending = self._pending_reflections()
        msg = (
            f"Evening: {len(pending)} unresolved item(s). "
            "Run `wl reflect` then `wl summary` to wrap the day."
        )
        self.notifier.send(msg)
        self.daemon.mark_job_run()
