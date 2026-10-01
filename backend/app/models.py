from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


AssistantState = Literal["idle", "listening", "thinking", "speaking", "alert"]


class CalendarEvent(BaseModel):
    id: str
    title: str
    starts_at: datetime
    ends_at: datetime
    people: list[str] = Field(default_factory=list)
    notes: str = ""
    priority: Literal["normal", "high"] = "normal"
    all_day: bool = False
    all_day_start: date | None = None
    all_day_end: date | None = None
    recurring: bool = False
    location: str = ""


class Reminder(BaseModel):
    id: int
    text: str
    due_at: datetime
    created_at: datetime
    device_id: str = "friday-p3a"
    delivered_at: datetime | None = None
    completed: bool = False
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None


class SurfaceAction(BaseModel):
    id: str = Field(max_length=64, pattern=r"^(status|reminders(?::[0-9]+)?|calendar(?::[0-9]+)?|calendar_refresh|calendar_next|event:[0-9a-f]{16}|dismiss|confirm|decline|reminder:[1-9][0-9]*|snooze:[1-9][0-9]*|complete:[1-9][0-9]*|cancel:[1-9][0-9]*)$")
    label: str = Field(min_length=1, max_length=32)


class Surface(BaseModel):
    kind: Literal["ambient", "timeline", "reminder", "message", "list", "metric", "comparison", "media", "person", "email", "map", "progress", "timer", "graph", "camera", "artifact"]
    title: str = ""
    subtitle: str = ""
    items: list[dict[str, Any]] = Field(default_factory=list)
    expires_ms: int | None = None
    countdown_ms: int | None = Field(default=None, ge=1000, le=86400000)
    deadline_at: datetime | None = None
    actions: list[SurfaceAction] = Field(default_factory=list, max_length=4)


class AgentReply(BaseModel):
    text: str
    surface: Surface
    provider: str = "mock"
    tool: str | None = None
