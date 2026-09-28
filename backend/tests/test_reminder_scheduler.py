import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.db import ReminderStore
from app.device_hub import DeviceHub
from app.reminder_scheduler import ReminderScheduler


class FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def accept(self):
        pass

    async def send_json(self, message):
        self.messages.append(message)


class ReminderSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "reminders.db"
        self.store = ReminderStore(self.path)
        self.hub = DeviceHub()
        self.scheduler = ReminderScheduler(self.store, self.hub, poll_seconds=0.1)

    async def asyncTearDown(self):
        self.tempdir.cleanup()

    async def test_delivers_due_reminder_once_and_survives_restart(self):
        now = datetime.now(timezone.utc)
        self.store.create(
            "join the interview", now - timedelta(seconds=1), device_id="device-1"
        )
        self.store.create(
            "future item", now + timedelta(hours=1), device_id="device-1"
        )
        socket = FakeWebSocket()
        await self.hub.connect("device-1", socket)
        self.hub.set_capabilities("device-1", socket, {"reminder_ack"})

        delivered = await self.scheduler.deliver_due_once(now)

        self.assertEqual(delivered, 1)
        self.assertEqual([message["type"] for message in socket.messages], [
            "reminder.alert",
        ])
        self.assertEqual(socket.messages[0]["surface"]["subtitle"], "join the interview")
        self.assertEqual(socket.messages[0]["surface"]["expires_ms"], 60000)
        reminder_id = socket.messages[0]["reminder_id"]
        self.store.mark_delivered(reminder_id, "device-1", now)
        self.assertEqual([item.text for item in self.store.pending()], ["future item"])

        restarted = ReminderScheduler(ReminderStore(self.path), self.hub)
        self.assertEqual(await restarted.deliver_due_once(now), 0)

    async def test_keeps_due_reminder_pending_until_a_device_connects(self):
        now = datetime.now(timezone.utc)
        self.store.create(
            "offline reminder", now - timedelta(seconds=1), device_id="offline"
        )

        self.assertEqual(await self.scheduler.deliver_due_once(now), 0)
        self.assertEqual(self.store.pending()[0].text, "offline reminder")

        socket = FakeWebSocket()
        await self.hub.connect("offline", socket)
        self.hub.set_capabilities("offline", socket, {"reminder_ack"})
        self.assertEqual(await self.scheduler.deliver_due_once(now), 1)
        self.store.mark_delivered(socket.messages[0]["reminder_id"], "offline", now)
        self.assertEqual(self.store.pending(), [])

    async def test_acknowledgement_is_scoped_to_the_originating_device(self):
        now = datetime.now(timezone.utc)
        reminder = self.store.create("private", now, device_id="device-1")

        self.assertFalse(self.store.mark_delivered(reminder.id, "device-2", now))
        self.assertTrue(self.store.mark_delivered(reminder.id, "device-1", now))

    async def test_unacknowledged_delivery_stops_after_attempt_limit(self):
        now = datetime.now(timezone.utc)
        self.store.create("bounded retry", now, device_id="device-1")
        socket = FakeWebSocket()
        await self.hub.connect("device-1", socket)
        self.hub.set_capabilities("device-1", socket, {"reminder_ack"})
        scheduler = ReminderScheduler(
            self.store,
            self.hub,
            poll_seconds=0.1,
            retry_seconds=10,
            max_attempts=2,
        )

        self.assertEqual(await scheduler.deliver_due_once(now), 1)
        self.assertEqual(await scheduler.deliver_due_once(now + timedelta(seconds=11)), 1)
        self.assertEqual(await scheduler.deliver_due_once(now + timedelta(seconds=22)), 0)
        self.assertEqual(len(socket.messages), 2)

    async def test_cancelled_and_completed_reminders_are_never_delivered(self):
        now = datetime.now(timezone.utc)
        cancelled = self.store.create("cancelled", now, device_id="device-1")
        completed = self.store.create("completed", now, device_id="device-1")
        self.store.mutate_reminder(
            cancelled.id, "device-1", "cancel", "cancel-action"
        )
        self.store.mutate_reminder(
            completed.id, "device-1", "complete", "complete-action"
        )
        socket = FakeWebSocket()
        await self.hub.connect("device-1", socket)
        self.hub.set_capabilities("device-1", socket, {"reminder_ack"})

        self.assertEqual(await self.scheduler.deliver_due_once(now), 0)
        self.assertEqual(socket.messages, [])

    async def test_snoozed_reminder_is_delivered_only_at_its_new_time(self):
        now = datetime.now(timezone.utc)
        reminder = self.store.create("snooze me", now, device_id="device-1")
        self.store.mark_delivered(reminder.id, "device-1", now)
        snoozed_until = now + timedelta(minutes=20)
        self.store.mutate_reminder(
            reminder.id,
            "device-1",
            "snooze",
            "snooze-action",
            due_at=snoozed_until,
        )
        socket = FakeWebSocket()
        await self.hub.connect("device-1", socket)
        self.hub.set_capabilities("device-1", socket, {"reminder_ack"})

        self.assertEqual(
            await self.scheduler.deliver_due_once(now + timedelta(minutes=19)), 0
        )
        self.assertEqual(await self.scheduler.deliver_due_once(snoozed_until), 1)
        self.assertEqual(socket.messages[0]["surface"]["subtitle"], "snooze me")

    async def test_legacy_naive_utc_due_at_does_not_stop_scheduler(self):
        now = datetime.now(timezone.utc)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(
                "INSERT INTO reminders (text, due_at, created_at, device_id) "
                "VALUES (?, ?, ?, ?)",
                (
                    "legacy naive timestamp",
                    (now - timedelta(seconds=1)).replace(tzinfo=None).isoformat(),
                    now.replace(tzinfo=None).isoformat(),
                    "device-1",
                ),
            )
            connection.commit()
        socket = FakeWebSocket()
        await self.hub.connect("device-1", socket)
        self.hub.set_capabilities("device-1", socket, {"reminder_ack"})

        self.assertEqual(await self.scheduler.deliver_due_once(now), 1)
        self.assertEqual(socket.messages[0]["surface"]["subtitle"], "legacy naive timestamp")

    async def test_migrates_existing_reminder_database(self):
        legacy_path = Path(self.tempdir.name) / "legacy.db"
        with closing(sqlite3.connect(legacy_path)) as connection:
            connection.execute(
                """
                CREATE TABLE reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT NOT NULL,
                    due_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.commit()

        migrated = ReminderStore(legacy_path)
        reminder = migrated.create(
            "migration works", datetime.now(timezone.utc) + timedelta(minutes=5)
        )

        self.assertEqual(reminder.device_id, "friday-p3a")
        self.assertIsNone(reminder.delivered_at)
        with closing(sqlite3.connect(legacy_path)) as connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(reminders)")
            }
        self.assertIn("cancelled_at", columns)
        self.assertIn("completed_at", columns)


if __name__ == "__main__":
    unittest.main()
