from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..models import CalendarEvent


class MockCalendar:
    def __init__(self, timezone: str):
        self.timezone = ZoneInfo(timezone)

    def today(self, now: datetime | None = None) -> list[CalendarEvent]:
        current = now.astimezone(self.timezone) if now else datetime.now(self.timezone)
        day = current.replace(hour=0, minute=0, second=0, microsecond=0)
        return [
            CalendarEvent(
                id="standup",
                title="Team standup",
                starts_at=day.replace(hour=10, minute=30),
                ends_at=day.replace(hour=11),
                people=["Engineering"],
            ),
            CalendarEvent(
                id="upstart",
                title="Upstart interview",
                starts_at=day.replace(hour=14),
                ends_at=day.replace(hour=15),
                people=["David", "Upstart"],
                notes="Verbal system-design exercise. Discuss a system owned end-to-end.",
                priority="high",
            ),
            CalendarEvent(
                id="gym",
                title="Gym",
                starts_at=day.replace(hour=17, minute=30),
                ends_at=day.replace(hour=18, minute=30),
            ),
        ]

    def next_event(self, now: datetime | None = None) -> CalendarEvent:
        current = now.astimezone(self.timezone) if now else datetime.now(self.timezone)
        events = self.today(current)
        upcoming = [event for event in events if event.ends_at > current]
        return upcoming[0] if upcoming else events[-1]

    def timeline_items(self, now: datetime | None = None) -> list[dict[str, str]]:
        return [
            {
                "time": event.starts_at.strftime("%-I:%M %p"),
                "label": event.title,
                "priority": event.priority,
            }
            for event in self.today(now)
        ]

