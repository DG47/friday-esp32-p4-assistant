import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from app.agent import FridayAgent
from app.config import Settings
from app.db import ReminderStore


class FridayAgentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        settings = Settings(
            assistant_provider="mock",
            assistant_database_path=Path(self.tempdir.name) / "test.db",
        )
        self.store = ReminderStore(settings.assistant_database_path)
        self.agent = FridayAgent(settings, self.store)

    async def asyncTearDown(self):
        self.tempdir.cleanup()

    async def test_calendar_and_follow_up_share_context(self):
        # Keep this context test independent of the wall clock. After the
        # interview ends, the real mock calendar correctly returns the gym as
        # the next event, which has no attendees or notes.
        interview = self.agent.calendar.today()[1]
        self.agent.calendar.next_event = lambda: interview

        first = await self.agent.handle("device-1", "What's next?")
        second = await self.agent.handle("device-1", "With who?")

        self.assertEqual(first.surface.kind, "timeline")
        self.assertIn("David", second.text)
        self.assertIn("system-design", second.text)

    async def test_reminder_persists(self):
        reply = await self.agent.handle("device-1", "Remind me at noon to prepare")
        pending = self.store.pending()

        self.assertEqual(reply.surface.kind, "reminder")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].text, "prepare")
        self.assertEqual(pending[0].due_at.hour, 12)

    async def test_relative_reminder(self):
        before = datetime.now().astimezone()
        await self.agent.handle("device-1", "Remind me to stretch in 10 minutes")
        reminder = self.store.pending()[0]

        self.assertEqual(reminder.text, "stretch")
        self.assertGreater(reminder.due_at.timestamp(), before.timestamp() + 9 * 60)


if __name__ == "__main__":
    unittest.main()
