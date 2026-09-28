from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from .db import ReminderStore
from .device_hub import DeviceHub
from .models import Reminder, Surface


logger = logging.getLogger(__name__)


class ReminderScheduler:
    def __init__(
        self,
        store: ReminderStore,
        hub: DeviceHub,
        poll_seconds: float = 1.0,
    ) -> None:
        self.store = store
        self.hub = hub
        self.poll_seconds = max(0.1, poll_seconds)

    @staticmethod
    def _surface(reminder: Reminder) -> Surface:
        return Surface(
            kind="reminder",
            title="Reminder",
            subtitle=reminder.text,
            items=[{"time": reminder.due_at.strftime("%-I:%M %p")}],
        )

    async def deliver_due_once(self, now: datetime | None = None) -> int:
        delivered = 0
        check_time = now or datetime.now(timezone.utc)
        for reminder in self.store.due(check_time):
            if not self.hub.has_capability(reminder.device_id, "reminder_ack"):
                continue
            if not self.store.claim_delivery(reminder.id, check_time):
                continue
            recipients = await self.hub.broadcast(
                reminder.device_id,
                {
                    "type": "reminder.alert",
                    "reminder_id": reminder.id,
                    "text": f"Reminder: {reminder.text}",
                    "surface": self._surface(reminder).model_dump(mode="json"),
                },
                required_capability="reminder_ack",
            )
            if recipients > 0:
                delivered += 1
                logger.info(
                    "Sent reminder %s to %s; awaiting acknowledgement",
                    reminder.id,
                    reminder.device_id,
                )
        return delivered

    async def run(self) -> None:
        while True:
            try:
                await self.deliver_due_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Reminder scheduler iteration failed")
            await asyncio.sleep(self.poll_seconds)
