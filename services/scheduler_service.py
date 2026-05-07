from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler

from db.session import get_session
from services.config_service import ConfigService
from services.notification_service import NotificationService
from services.reflection_service import ReflectionService


class SchedulerService:
    def __init__(self, notifier: NotificationService, config: ConfigService | None = None):
        self.notifier = notifier
        self.config = config or ConfigService()
        self.scheduler = BackgroundScheduler()

    def _parse_hm(self, value: str) -> tuple[int, int]:
        hour, minute = value.split(":")
        return int(hour), int(minute)

    def _pending_reflection_count(self) -> int:
        with get_session() as session:
            return len(ReflectionService(session).unresolved_capture_events(limit=1000))

    def start_default_reminders(self) -> None:
        cfg = self.config.load()
        lunch_hour, lunch_minute = self._parse_hm(cfg.get("lunch", {}).get("time", "13:00"))
        evening_hour, evening_minute = self._parse_hm(cfg.get("evening", {}).get("time", "18:00"))
        self.scheduler.add_job(self._lunch_reminder, "cron", hour=lunch_hour, minute=lunch_minute)
        self.scheduler.add_job(self._evening_reminder, "cron", hour=evening_hour, minute=evening_minute)
        self.scheduler.start()

    def _lunch_reminder(self) -> None:
        pending = self._pending_reflection_count()
        self.notifier.send(f"{pending} work items need clarification. Run: wl reflect")

    def _evening_reminder(self) -> None:
        pending = self._pending_reflection_count()
        self.notifier.send(f"{pending} work items need clarification. Run: wl reflect")
