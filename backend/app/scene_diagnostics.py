"""Privacy-safe scene diagnostics: enums/counts only, never labels or payloads."""
import logging
from datetime import datetime, timezone

logger = logging.getLogger("uvicorn.error")
ACTIONS = {"status", "reminders", "calendar", "dismiss", "confirm", "decline", "reminder", "snooze", "complete", "cancel"}


def log_scene_action(device_id, action, surface):
    category = action.split(":", 1)[0]
    if category not in ACTIONS:
        category = "invalid"
    kind = surface.kind if surface else "pending"
    logger.info("scene_event utc=%s device=%s action=%s kind=%s cards=%d buttons=%d",
        datetime.now(timezone.utc).isoformat(), device_id, category, kind,
        min(4, len(surface.items)) if surface else 0,
        min(4, len(surface.actions)) if surface else 0)
