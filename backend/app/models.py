from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    id: str = Field(max_length=64, pattern=r"^(reportstop:[0-9a-f]{24}|context:[0-9a-f]{24}|recover|reqcancel:[0-9a-f]{24}|music|musicctl:[0-9a-f]{24}|memories(?::[0-9]+)?|memory:[1-9][0-9]*(?::[0-9]+)?|memreview:[0-9a-f]{24}:[0-9]+|memforget:[1-9][0-9]*|memyes:[0-9a-f]{24}|memno:[0-9a-f]{24}|status|weather|weather_context|reminders(?::[0-9]+)?|calendar(?::[0-9]+)?|calendar_refresh|calendar_next|event:[0-9a-f]{16}|dismiss|confirm|decline|reminder:[1-9][0-9]*|snooze:[1-9][0-9]*|complete:[1-9][0-9]*|cancel:[1-9][0-9]*)$")
    label: str = Field(min_length=1, max_length=32)


class ContextLinks(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_ref: str = Field(default="", pattern=r"^(?:[0-9a-f]{24})?$")
    weather_event_ref: str = Field(default="", pattern=r"^(?:[0-9a-f]{24})?$")
    weather_ref: str = Field(default="", pattern=r"^(?:[0-9a-f]{24})?$")
    weather_summary: str = Field(default="", max_length=200)
    weather_location: str = Field(default="", pattern=r"^(?:[0-9]{5})?$")
    weather_temperature: str = Field(default="", max_length=48)
    weather_condition: str = Field(default="", max_length=96)
    weather_outlook: str = Field(default="", max_length=192)
    revision: int = Field(ge=0)
    view: Literal["both", "event", "weather"]
    event_valid_ms: int = Field(ge=0, le=300000)
    weather_valid_ms: int = Field(ge=0, le=300000)
    event_deadline_at: datetime | None = None
    weather_deadline_at: datetime | None = None


class Surface(BaseModel):
    kind: Literal["ambient", "timeline", "reminder", "message", "list", "metric", "comparison", "media", "person", "email", "map", "progress", "timer", "graph", "camera", "artifact"]
    title: str = ""
    subtitle: str = ""
    items: list[dict[str, Any]] = Field(default_factory=list)
    context: dict[str, Any] | None = None
    clear_weather_context: bool = False
    expires_ms: int | None = None
    weather_valid_ms: int | None = Field(default=None, ge=1, le=300000)
    countdown_ms: int | None = Field(default=None, ge=1000, le=86400000)
    deadline_at: datetime | None = None
    actions: list[SurfaceAction] = Field(default_factory=list, max_length=5)

    @field_validator("context")
    @classmethod
    def bound_context(cls, value):
        return ContextLinks.model_validate(value).model_dump(mode="json") if value is not None else None

    @model_validator(mode="after")
    def validate_action_capacity(self):
        if len(self.actions) > 4 and (self.kind != "metric" or
            [action.id for action in self.actions] != ["memories", "reminders", "calendar", "music", "weather"]):
            raise ValueError("Five actions are reserved for the local launcher")
        return self


class AgentReply(BaseModel):
    text: str
    surface: Surface
    provider: str = "mock"
    tool: str | None = None
    retain_context: bool = False
