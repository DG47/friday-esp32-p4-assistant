"""Deterministic local scene director; no external integrations or model calls."""
from zoneinfo import ZoneInfo

from .models import Surface, SurfaceAction


def local_scene(action, device_id, settings, store, calendar) -> Surface | None:
    def button(id, label):
        return SurfaceAction(id=id, label=label)

    if action == "dismiss":
        return Surface(kind="ambient", title="Friday", subtitle="Ready when needed.")
    if action == "status":
        return Surface(kind="metric", title="At your service", subtitle="Local backend status",
            items=[{"label": "Provider", "time": settings.assistant_provider},
                   {"label": "Calendar", "time": "Sample only"},
                   {"label": "Music / smart home / computer", "time": "Not connected"},
                   {"label": "Device speech playback", "time": "Enabled; hardware unverified" if settings.tts_provider != "none" else "Disabled (opt-in)"}],
            actions=[button("reminders", "Reminders"), button("dismiss", "Dismiss")], expires_ms=30000)
    if action == "calendar":
        return Surface(kind="timeline", title="Calendar preview", subtitle="Sample data — no calendar connected",
            items=calendar.timeline_items()[:4], actions=[button("dismiss", "Dismiss")], expires_ms=30000)
    reminders = store.active(device_id=device_id)
    if action == "reminders" or action.startswith("reminders:"):
        page = int(action.split(":", 1)[1]) if ":" in action else 0
        if page > max(0, (len(reminders) - 1) // 4):
            raise ValueError("Reminder page is no longer available")
        selected = reminders[page * 4:page * 4 + 4]
        navigation = []
        if page:
            navigation.append(button(f"reminders:{page - 1}", "Previous"))
        if (page + 1) * 4 < len(reminders):
            navigation.append(button(f"reminders:{page + 1}", "Next"))
        navigation.append(button("dismiss", "Dismiss"))
        return Surface(kind="list", title="Your reminders", subtitle=f"{len(reminders)} active · page {page + 1}" if reminders else "No active reminders. Ask Friday to create one.",
            items=[{"time": item.due_at.astimezone(ZoneInfo(settings.assistant_timezone)).strftime("%b %d %H:%M"),
                    "label": f"#{item.id} {item.text}", "action": f"reminder:{item.id}"} for item in selected],
            actions=navigation, expires_ms=30000)
    if action.startswith("reminder:"):
        reminder_id = int(action.split(":", 1)[1])
        item = next((item for item in reminders if item.id == reminder_id), None)
        if item is None:
            raise ValueError("That reminder is no longer active for this device")
        return Surface(kind="reminder", title=f"Reminder #{item.id}", subtitle=item.text,
            items=[{"time": "Due", "label": item.due_at.astimezone(ZoneInfo(settings.assistant_timezone)).strftime("%b %d %H:%M")}],
            actions=[button(f"snooze:{item.id}", "Snooze 10 min"), button(f"complete:{item.id}", "Complete"),
                     button(f"cancel:{item.id}", "Cancel"), button("dismiss", "Dismiss")], expires_ms=30000)
    return None
