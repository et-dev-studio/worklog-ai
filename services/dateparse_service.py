from __future__ import annotations

import re
from datetime import date, timedelta


_RELATIVE_DAYS = re.compile(r"^-(\d+)d$")


class DateParseError(ValueError):
    pass


def parse_day(value: str | None, today: date | None = None) -> date:
    if value is None:
        return today or date.today()
    today = today or date.today()
    s = value.strip().lower()
    if s in ("today", "t"):
        return today
    if s in ("yesterday", "y"):
        return today - timedelta(days=1)
    rel = _RELATIVE_DAYS.match(s)
    if rel:
        return today - timedelta(days=int(rel.group(1)))
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DateParseError(
            f"Invalid date: {value!r}. Use YYYY-MM-DD, today, yesterday, or -Nd."
        ) from exc


def parse_range(value: str | None, today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    if value is None:
        return today, today
    s = value.strip().lower()
    if s == "this-week":
        start = today - timedelta(days=today.weekday())
        return start, today
    if s == "last-week":
        end = today - timedelta(days=today.weekday() + 1)
        start = end - timedelta(days=6)
        return start, end
    if s in ("week", "7d"):
        return today - timedelta(days=6), today
    day = parse_day(value, today=today)
    return day, day
