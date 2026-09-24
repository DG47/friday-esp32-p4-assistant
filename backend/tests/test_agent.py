import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.agent import FridayAgent
from app.config import Settings
from app.db import ReminderStore
from app.providers.ollama_provider import OllamaProvider
from app.providers.router import ProviderRouter


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

    async def test_router_selects_ollama_without_api_key(self):
        settings = Settings(
            assistant_provider="ollama",
            ollama_model="local-test-model",
        )

        provider = ProviderRouter(settings).select("Good morning")

        self.assertIsInstance(provider, OllamaProvider)
        self.assertEqual(provider.model, "local-test-model")

    async def test_ollama_provider_sends_personality_and_prompt(self):
        response = MagicMock()
        response.json.return_value = {
            "message": {"role": "assistant", "content": "At your service."}
        }
        client = MagicMock()
        client.post = AsyncMock(return_value=response)
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=client)
        context.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.providers.ollama_provider.httpx.AsyncClient",
            return_value=context,
        ):
            provider = OllamaProvider("http://127.0.0.1:11434/", "local-test-model")
            answer = await provider.respond("Good morning", "Be concise.")

        self.assertEqual(answer, "At your service.")
        response.raise_for_status.assert_called_once()
        payload = client.post.await_args.kwargs["json"]
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["messages"][1]["content"], "Good morning")
        self.assertFalse(payload["stream"])


if __name__ == "__main__":
    unittest.main()
