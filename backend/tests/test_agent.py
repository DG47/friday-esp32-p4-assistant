import tempfile
import unittest
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.agent import FridayAgent
from app.config import Settings
from app.db import ReminderStore
from app.providers.base import ProviderTurn, TextProvider, ToolCall
from app.providers.ollama_provider import OllamaProvider
from app.providers.router import ProviderRouter


class FakeToolProvider(TextProvider):
    name = "test-model"

    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    async def complete(self, messages, instructions, tools):
        self.requests.append(
            {
                "messages": deepcopy(messages),
                "instructions": instructions,
                "tools": deepcopy(tools),
            }
        )
        return self.turns.pop(0)


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

    async def test_model_chooses_calendar_tool_and_receives_result(self):
        provider = FakeToolProvider(
            [
                ProviderTurn(
                    tool_calls=[
                        ToolCall(id="call-1", name="get_calendar", arguments={"range": "next"})
                    ]
                ),
                ProviderTurn(text="Your next event is the interview at 2 PM."),
            ]
        )
        with patch.object(self.agent.providers, "select", return_value=provider):
            reply = await self.agent.handle("device-model", "What's next?")

        self.assertEqual(reply.provider, "test-model")
        self.assertEqual(reply.tool, "get_calendar")
        self.assertEqual(reply.surface.kind, "timeline")
        self.assertIn("interview", reply.text)
        tool_result = provider.requests[1]["messages"][-1]
        self.assertEqual(tool_result["role"], "tool")
        self.assertIn('"events"', tool_result["content"])
        self.assertIn("Dhruv", provider.requests[0]["instructions"])

    async def test_model_history_is_available_on_the_next_turn(self):
        first_provider = FakeToolProvider([ProviderTurn(text="Your next event is the interview.")])
        second_provider = FakeToolProvider([ProviderTurn(text="It is with David.")])
        with patch.object(self.agent.providers, "select", return_value=first_provider):
            await self.agent.handle("history-device", "What's next?")
        with patch.object(self.agent.providers, "select", return_value=second_provider):
            reply = await self.agent.handle("history-device", "Who is it with?")

        history = second_provider.requests[0]["messages"]
        self.assertEqual(history[0]["content"], "What's next?")
        self.assertEqual(history[1]["content"], "Your next event is the interview.")
        self.assertEqual(history[2]["content"], "Who is it with?")
        self.assertEqual(reply.text, "It is with David.")

    async def test_model_can_create_a_reminder_tool(self):
        provider = FakeToolProvider(
            [
                ProviderTurn(
                    tool_calls=[
                        ToolCall(
                            id="call-2",
                            name="create_reminder",
                            arguments={
                                "text": "prepare",
                                "due_at": "2030-01-02T12:00:00-05:00",
                            },
                        )
                    ]
                ),
                ProviderTurn(text="I'll remind you at noon."),
            ]
        )
        with patch.object(self.agent.providers, "select", return_value=provider):
            reply = await self.agent.handle("reminder-device", "Remind me to prepare.")

        self.assertEqual(reply.surface.kind, "reminder")
        self.assertEqual(self.store.pending()[0].text, "prepare")
        self.assertEqual(self.store.pending()[0].device_id, "reminder-device")

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
