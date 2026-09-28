from __future__ import annotations

from datetime import datetime
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


class Surface(BaseModel):
    kind: Literal["ambient", "timeline", "reminder", "message"]
    title: str = ""
    subtitle: str = ""
    items: list[dict[str, Any]] = Field(default_factory=list)
    expires_ms: int | None = None


class AgentReply(BaseModel):
    text: str
    surface: Surface
    provider: str = "mock"
    tool: str | None = None
