from __future__ import annotations

from collections import defaultdict

from db.models import Event


class GroupingService:
    def group_by_workstream(self, events: list[Event]) -> dict[str, list[Event]]:
        groups: dict[str, list[Event]] = defaultdict(list)
        for event in events:
            key = event.workstream_id or "unassigned"
            groups[key].append(event)
        return dict(groups)
