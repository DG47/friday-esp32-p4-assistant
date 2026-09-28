from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .config import Settings
from .db import ReminderStore
from .models import AgentReply, CalendarEvent, Surface
from .providers.base import TextProvider
from .providers.router import ProviderRouter
from .tools.calendar import MockCalendar


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_calendar",
            "description": "Read the user's mock calendar for today or get the next event.",
            "parameters": {
                "type": "object",
                "properties": {
                    "range": {
                        "type": "string",
                        "enum": ["today", "next"],
                        "description": "Whether to return today's events or only the next event.",
                    }
                },
                "required": ["range"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_reminder",
            "description": "Save a reminder after resolving its due time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "What to remember."},
                    "due_at": {
                        "type": "string",
                        "description": "ISO 8601 date and time, including UTC offset.",
                    },
                },
                "required": ["text", "due_at"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_reminders",
            "description": "List all pending reminders.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
]


@dataclass
class SessionContext:
    last_event: CalendarEvent | None = None
    last_topic: str = ""
    messages: list[dict[str, str]] = field(default_factory=list)


@dataclass
class ToolOutcome:
    content: str
    surface: Surface | None = None


class FridayAgent:
    def __init__(self, settings: Settings, store: ReminderStore):
        self.settings = settings
        self.store = store
        self.calendar = MockCalendar(settings.assistant_timezone)
        self.providers = ProviderRouter(settings)
        self.sessions: dict[str, SessionContext] = {}

    def _context(self, session_id: str) -> SessionContext:
        return self.sessions.setdefault(session_id, SessionContext())

    async def handle(self, session_id: str, text: str) -> AgentReply:
        provider = self.providers.select(text)
        if provider is not None:
            return await self._handle_with_model(session_id, text, provider)
        return await self._handle_mock(session_id, text)

    def _instructions(self) -> str:
        timezone = ZoneInfo(self.settings.assistant_timezone)
        now = datetime.now(timezone).isoformat(timespec="minutes")
        user_name = self.settings.assistant_user_name
        return f"""You are Friday, {user_name}'s private executive assistant.
Be concise, polished, observant, and competent first. Use dry sarcasm only when
there is a natural opening. Never flatter, announce tool mechanics, repeat the
request, or make jokes during urgent situations. Spoken replies should usually
be one or two short sentences.

The current local date and time is {now}. Use the supplied tools whenever the
request depends on calendar or reminder data. Never claim that a tool action
succeeded until its result confirms success. Use prior conversation messages
to resolve follow-up questions and references."""

    async def _handle_with_model(
        self, session_id: str, text: str, provider: TextProvider
    ) -> AgentReply:
        context = self._context(session_id)
        user_message = {"role": "user", "content": text}
        messages: list[dict[str, Any]] = [*context.messages[-10:], user_message]
        last_surface: Surface | None = None
        last_tool: str | None = None

        for _ in range(4):
            turn = await provider.complete(messages, self._instructions(), TOOL_DEFINITIONS)
            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": turn.text,
            }
            if turn.tool_calls:
                assistant_message["tool_calls"] = [
                    {
                        "id": call.id,
                        "name": call.name,
                        "arguments": call.arguments,
                    }
                    for call in turn.tool_calls
                ]
            messages.append(assistant_message)

            if not turn.tool_calls:
                answer = turn.text.strip() or "Done."
                context.messages.extend(
                    [user_message, {"role": "assistant", "content": answer}]
                )
                context.messages = context.messages[-12:]
                return AgentReply(
                    text=answer,
                    surface=last_surface
                    or Surface(
                        kind="message",
                        title="Friday",
                        subtitle=answer,
                        expires_ms=12000,
                    ),
                    provider=provider.name,
                    tool=last_tool,
                )

            for call in turn.tool_calls:
                outcome = self._execute_tool(
                    call.name, call.arguments, device_id=session_id
                )
                last_surface = outcome.surface or last_surface
                last_tool = call.name
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": outcome.content,
                    }
                )

        answer = "I couldn't complete that request safely. Please try again."
        context.messages.extend(
            [user_message, {"role": "assistant", "content": answer}]
        )
        context.messages = context.messages[-12:]
        return AgentReply(
            text=answer,
            surface=last_surface
            or Surface(kind="message", title="Friday", subtitle=answer, expires_ms=12000),
            provider=provider.name,
            tool=last_tool,
        )

    def _execute_tool(
        self, name: str, arguments: dict[str, Any], device_id: str
    ) -> ToolOutcome:
        if name == "get_calendar":
            events = self.calendar.today()
            selected = [self.calendar.next_event()] if arguments.get("range") == "next" else events
            result = {
                "ok": True,
                "events": [event.model_dump(mode="json") for event in selected],
            }
            return ToolOutcome(
                content=json.dumps(result),
                surface=Surface(
                    kind="timeline",
                    title="Today",
                    subtitle=f"{len(events)} events",
                    items=self.calendar.timeline_items(),
                    expires_ms=12000,
                ),
            )

        if name == "create_reminder":
            reminder_text = str(arguments.get("text", "")).strip()
            due_value = str(arguments.get("due_at", "")).strip()
            try:
                due_at = datetime.fromisoformat(due_value.replace("Z", "+00:00"))
                timezone = ZoneInfo(self.settings.assistant_timezone)
                due_at = (
                    due_at.replace(tzinfo=timezone)
                    if due_at.tzinfo is None
                    else due_at.astimezone(timezone)
                )
                if not reminder_text:
                    raise ValueError("Reminder text is required")
                if due_at <= datetime.now(timezone):
                    raise ValueError("Reminder due time must be in the future")
            except ValueError as error:
                return ToolOutcome(content=json.dumps({"ok": False, "error": str(error)}))
            saved = self.store.create(reminder_text, due_at, device_id=device_id)
            return ToolOutcome(
                content=json.dumps({"ok": True, "reminder": saved.model_dump(mode="json")}),
                surface=Surface(
                    kind="reminder",
                    title=saved.due_at.strftime("%-I:%M %p"),
                    subtitle=saved.text,
                    expires_ms=10000,
                ),
            )

        if name == "list_reminders":
            pending = self.store.pending(device_id=device_id)
            result = {
                "ok": True,
                "reminders": [item.model_dump(mode="json") for item in pending],
            }
            subtitle = (
                f"{len(pending)} pending reminder{'s' if len(pending) != 1 else ''}"
                if pending
                else "No pending reminders"
            )
            return ToolOutcome(
                content=json.dumps(result),
                surface=Surface(
                    kind="reminder",
                    title="Reminders",
                    subtitle=subtitle,
                    items=[
                        {"time": item.due_at.strftime("%-I:%M %p"), "label": item.text}
                        for item in pending
                    ],
                    expires_ms=12000,
                ),
            )

        return ToolOutcome(
            content=json.dumps({"ok": False, "error": f"Unknown tool: {name}"})
        )

    async def _handle_mock(self, session_id: str, text: str) -> AgentReply:
        normalized = " ".join(text.lower().strip().split())
        context = self._context(session_id)

        if self._is_calendar_request(normalized):
            event = self.calendar.next_event()
            context.last_event = event
            context.last_topic = "calendar"
            return AgentReply(
                text=f"Your next event is {event.title} at {event.starts_at.strftime('%-I %p').lower()}.",
                surface=Surface(
                    kind="timeline",
                    title="Today",
                    subtitle=f"Next · {event.starts_at.strftime('%-I:%M %p')}",
                    items=self.calendar.timeline_items(),
                    expires_ms=12000,
                ),
                tool="calendar",
            )

        if self._is_event_follow_up(normalized) and context.last_event:
            event = context.last_event
            people = [person for person in event.people if person != "Upstart"]
            answer = ", ".join(people) if people else "No one else is listed"
            return AgentReply(
                text=f"With {answer}. {event.notes}" if event.notes else f"With {answer}.",
                surface=Surface(
                    kind="message",
                    title=event.title,
                    subtitle=event.starts_at.strftime("%-I:%M %p"),
                    items=[{"label": person} for person in event.people]
                    + ([{"label": event.notes}] if event.notes else []),
                    expires_ms=12000,
                ),
                tool="calendar",
            )

        reminder = self._parse_reminder(text)
        if reminder:
            reminder_text, due_at = reminder
            saved = self.store.create(reminder_text, due_at, device_id=session_id)
            context.last_topic = "reminder"
            return AgentReply(
                text=f"Done. I’ll remind you at {saved.due_at.strftime('%-I:%M %p').lower()}.",
                surface=Surface(
                    kind="reminder",
                    title=saved.due_at.strftime("%-I:%M %p"),
                    subtitle=saved.text,
                    expires_ms=10000,
                ),
                tool="reminders",
            )

        if "reminder" in normalized or "reminders" in normalized:
            pending = self.store.pending(device_id=session_id)
            if not pending:
                text_out = "No pending reminders. Against all odds, future-you is currently safe."
            else:
                text_out = f"You have {len(pending)} pending reminder{'s' if len(pending) != 1 else ''}."
            return AgentReply(
                text=text_out,
                surface=Surface(
                    kind="reminder",
                    title="Reminders",
                    subtitle=text_out,
                    items=[
                        {"time": item.due_at.strftime("%-I:%M %p"), "label": item.text}
                        for item in pending
                    ],
                    expires_ms=12000,
                ),
                tool="reminders",
            )

        return AgentReply(
            text="I can handle the calendar and reminders in mock mode. The broader intellect arrives when you add the API keys.",
            surface=Surface(
                kind="message",
                title="Mock mode",
                subtitle="Calendar and reminders are ready",
                expires_ms=10000,
            ),
        )

    @staticmethod
    def _is_calendar_request(text: str) -> bool:
        return any(
            phrase in text
            for phrase in (
                "what's next",
                "what is next",
                "calendar",
                "my day",
                "schedule",
                "next meeting",
            )
        )

    @staticmethod
    def _is_event_follow_up(text: str) -> bool:
        return any(
            phrase in text
            for phrase in ("with who", "with whom", "who is it with", "tell me about it", "what did")
        )

    def _parse_reminder(self, text: str) -> tuple[str, datetime] | None:
        lowered = text.lower()
        if "remind me" not in lowered:
            return None

        timezone = ZoneInfo(self.settings.assistant_timezone)
        now = datetime.now(timezone)
        due_at: datetime | None = None

        relative = re.search(r"in\s+(\d+)\s+(minute|minutes|hour|hours)", lowered)
        if relative:
            amount = int(relative.group(1))
            unit = relative.group(2)
            due_at = now + (timedelta(hours=amount) if "hour" in unit else timedelta(minutes=amount))

        if not due_at:
            clock = re.search(r"(?:at|for)\s+(noon|midnight|\d{1,2}(?::\d{2})?\s*(?:am|pm)?)", lowered)
            if clock:
                value = clock.group(1).replace(" ", "")
                if value == "noon":
                    hour, minute = 12, 0
                elif value == "midnight":
                    hour, minute = 0, 0
                else:
                    match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(am|pm)?", value)
                    if not match:
                        return None
                    hour = int(match.group(1))
                    minute = int(match.group(2) or 0)
                    meridiem = match.group(3)
                    if meridiem == "pm" and hour < 12:
                        hour += 12
                    if meridiem == "am" and hour == 12:
                        hour = 0
                due_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if due_at <= now:
                    due_at += timedelta(days=1)

        if not due_at:
            return None

        content = re.sub(r"^.*?remind me(?:\s+to)?\s+", "", text, flags=re.IGNORECASE)
        content = re.sub(
            r"^(?:at|for)\s+(?:noon|midnight|\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s+(?:to\s+)?",
            "",
            content,
            flags=re.IGNORECASE,
        )
        content = re.sub(
            r"\s+(?:at|for)\s+(?:noon|midnight|\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*$",
            "",
            content,
            flags=re.IGNORECASE,
        )
        content = re.sub(
            r"\s+in\s+\d+\s+(?:minute|minutes|hour|hours)\s*$",
            "",
            content,
            flags=re.IGNORECASE,
        )
        return (content.strip(" .") or "that", due_at)
