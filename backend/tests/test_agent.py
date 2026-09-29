import tempfile
import unittest
import sqlite3
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
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
        self.settings = Settings(
            assistant_provider="mock",
            assistant_database_path=Path(self.tempdir.name) / "test.db",
        )
        self.store = ReminderStore(self.settings.assistant_database_path)
        self.agent = FridayAgent(self.settings, self.store)

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

    async def test_mock_calendar_context_survives_restart(self):
        interview = self.agent.calendar.today()[1]
        self.agent.calendar.next_event = lambda: interview
        await self.agent.handle(
            "restart-device", "What's next?", request_id="mock-calendar-turn"
        )

        restarted = FridayAgent(
            self.settings, ReminderStore(self.settings.assistant_database_path)
        )
        reply = await restarted.handle(
            "restart-device", "With who?", request_id="mock-follow-up"
        )

        self.assertIn("David", reply.text)
        self.assertIn("system-design", reply.text)

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
        restarted_agent = FridayAgent(self.settings, ReminderStore(self.settings.assistant_database_path))
        with patch.object(restarted_agent.providers, "select", return_value=second_provider):
            reply = await restarted_agent.handle("history-device", "Who is it with?")

        history = second_provider.requests[0]["messages"]
        self.assertEqual(history[0]["content"], "What's next?")
        self.assertEqual(history[1]["content"], "Your next event is the interview.")
        self.assertEqual(history[2]["content"], "Who is it with?")
        self.assertEqual(reply.text, "It is with David.")

    async def test_structured_tool_history_is_available_after_restart(self):
        first_provider = FakeToolProvider(
            [
                ProviderTurn(
                    tool_calls=[
                        ToolCall(
                            id="history-call",
                            name="get_calendar",
                            arguments={"range": "next"},
                        )
                    ]
                ),
                ProviderTurn(text="Your interview is next."),
            ]
        )
        with patch.object(self.agent.providers, "select", return_value=first_provider):
            await self.agent.handle(
                "structured-device", "What's next?", request_id="structured-first"
            )

        restarted = FridayAgent(
            self.settings, ReminderStore(self.settings.assistant_database_path)
        )
        second_provider = FakeToolProvider([ProviderTurn(text="It is with David.")])
        with patch.object(restarted.providers, "select", return_value=second_provider):
            await restarted.handle(
                "structured-device", "With who?", request_id="structured-second"
            )

        history = second_provider.requests[0]["messages"]
        tool_call = next(message for message in history if message.get("tool_calls"))
        tool_result = next(message for message in history if message["role"] == "tool")
        self.assertEqual(tool_call["tool_calls"][0]["arguments"], {"range": "next"})
        self.assertEqual(tool_result["name"], "get_calendar")
        self.assertIn('"events"', tool_result["content"])

    async def test_confirmation_survives_restart(self):
        reminder = self.store.create(
            "stretch",
            datetime.now(timezone.utc) + timedelta(minutes=10),
            device_id="confirmation-device",
        )
        await self.agent.handle(
            "confirmation-device",
            "Cancel the stretch reminder",
            request_id="confirmation-request",
        )
        self.assertEqual(len(self.store.active("confirmation-device")), 1)

        restarted = FridayAgent(
            self.settings, ReminderStore(self.settings.assistant_database_path)
        )
        reply = await restarted.handle(
            "confirmation-device", "Yes, do it.", request_id="confirmation-yes"
        )

        self.assertEqual(reply.provider, "confirmation")
        self.assertEqual(self.store.active("confirmation-device"), [])
        history = self.store.conversation("confirmation-device", limit=10)
        confirmed_call = next(
            call
            for message in history
            for call in message.get("tool_calls", [])
            if call["name"] == "cancel_reminder"
            and call["arguments"]["reminder_id"] == reminder.id
            and message["content"] == ""
        )
        self.assertRegex(confirmed_call["id"], r"^call_[0-9a-f]{24}$")

    async def test_confirmation_can_be_cancelled_without_side_effects(self):
        self.store.create(
            "stretch",
            datetime.now(timezone.utc) + timedelta(minutes=10),
            device_id="cancel-device",
        )
        await self.agent.handle(
            "cancel-device",
            "Cancel the stretch reminder",
            request_id="cancel-request",
        )

        reply = await self.agent.handle(
            "cancel-device", "Nope, leave it.", request_id="cancel-response"
        )

        self.assertEqual(reply.surface.title, "Cancelled")
        self.assertEqual(len(self.store.active("cancel-device")), 1)

    async def test_unrelated_request_cancels_stale_confirmation_and_proceeds(self):
        self.store.create(
            "stretch",
            datetime.now(timezone.utc) + timedelta(minutes=10),
            device_id="supersede-device",
        )
        await self.agent.handle(
            "supersede-device",
            "Cancel the stretch reminder",
            request_id="supersede-request",
        )

        reply = await self.agent.handle(
            "supersede-device", "What's next?", request_id="new-request"
        )

        self.assertEqual(reply.surface.kind, "timeline")
        self.assertIn("I dropped the pending request to", reply.text)
        self.assertIn("I dropped the pending request to", reply.surface.subtitle)
        self.assertIsNone(self.store.pending_confirmation("supersede-device"))
        self.assertEqual(len(self.store.active("supersede-device")), 1)

    async def test_request_prefixed_with_confirmation_word_does_not_run_action(self):
        for index, text in enumerate(
            (
                "Okay, what's next?",
                "Sure, and remind me to call mom at 5",
                "Yes and also what is on my calendar",
                "ok google",
            )
        ):
            device_id = f"prefixed-confirmation-{index}"
            original = self.store.create(
                "keep me",
                datetime.now(timezone.utc) + timedelta(minutes=10),
                device_id=device_id,
            )
            await self.agent.handle(
                device_id,
                "Cancel the keep me reminder",
                request_id=f"pending-{index}",
            )

            reply = await self.agent.handle(
                device_id, text, request_id=f"replacement-{index}"
            )

            self.assertNotEqual(reply.provider, "confirmation")
            self.assertIn("I dropped the pending request to", reply.text)
            self.assertIsNotNone(self.store.active_by_id(original.id, device_id))

    async def test_confirmation_decision_requires_a_complete_reply(self):
        for text in (
            "Yeah, go ahead.",
            "Yes, do it.",
            "Yeah, cancel it",
            "Yes cancel it",
            "Yeah sure",
        ):
            self.assertTrue(self.agent._confirmation_decision(text), text)
        for text in (
            "Nope, don't do that.",
            "No, don't",
            "Nope, leave it",
        ):
            self.assertFalse(self.agent._confirmation_decision(text), text)
        for text in (
            "Okay, what's next?",
            "Sure, and remind me to call mom at 5",
            "Yes and also what is on my calendar",
            "ok google",
        ):
            self.assertIsNone(self.agent._confirmation_decision(text), text)

    async def test_legacy_text_history_is_migrated(self):
        legacy_path = Path(self.tempdir.name) / "legacy-conversation.db"
        with closing(sqlite3.connect(legacy_path)) as connection:
            connection.execute(
                "CREATE TABLE conversation_messages ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL, "
                "request_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, "
                "created_at TEXT NOT NULL, UNIQUE(request_id, role))"
            )
            connection.execute(
                "INSERT INTO conversation_messages "
                "(device_id, request_id, role, content, created_at) "
                "VALUES ('legacy-device', 'legacy-request', 'user', 'hello', "
                "'2026-01-01T00:00:00+00:00')"
            )
            connection.commit()

        migrated = ReminderStore(legacy_path)

        self.assertEqual(
            migrated.conversation("legacy-device"),
            [{"role": "user", "content": "hello"}],
        )

    async def test_legacy_tool_call_ids_are_migrated_consistently(self):
        legacy_path = Path(self.tempdir.name) / "legacy-tool-calls.db"
        store = ReminderStore(legacy_path)
        store.append_messages(
            "legacy-device",
            "legacy-request",
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "confirmation:legacy:0",
                            "name": "cancel_reminder",
                            "arguments": {"reminder_id": 13},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "confirmation:legacy:0",
                    "name": "cancel_reminder",
                    "content": '{"ok": true}',
                },
            ],
        )

        migrated = ReminderStore(legacy_path)
        history = migrated.conversation("legacy-device")
        call_id = history[0]["tool_calls"][0]["id"]

        self.assertRegex(call_id, r"^call_[0-9a-f]{24}$")
        self.assertEqual(history[1]["tool_call_id"], call_id)

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

    async def test_model_can_cancel_an_exact_reminder_after_confirmation(self):
        reminder = self.store.create(
            "prepare the demo",
            datetime.now(timezone.utc) + timedelta(hours=1),
            device_id="model-cancel-device",
        )
        provider = FakeToolProvider(
            [
                ProviderTurn(
                    tool_calls=[
                        ToolCall(
                            id="cancel-call",
                            name="cancel_reminder",
                            arguments={"reminder_id": reminder.id},
                        )
                    ]
                )
            ]
        )

        with patch.object(self.agent.providers, "select", return_value=provider):
            requested = await self.agent.handle(
                "model-cancel-device",
                "Cancel my demo reminder",
                request_id="model-cancel-request",
            )
        confirmed = await self.agent.handle(
            "model-cancel-device", "yes", request_id="model-cancel-confirm"
        )

        self.assertIn("prepare the demo", requested.surface.subtitle)
        self.assertIn("cancelled", confirmed.text)
        self.assertEqual(self.store.active("model-cancel-device"), [])
        tool_names = {
            tool["function"]["name"] for tool in provider.requests[0]["tools"]
        }
        self.assertIn("cancel_reminder", tool_names)
        self.assertIn("snooze_reminder", tool_names)
        self.assertIn("complete_reminder", tool_names)

    async def test_reminder_tool_is_idempotent_for_a_retried_request(self):
        def provider():
            return FakeToolProvider(
                [
                    ProviderTurn(
                        tool_calls=[
                            ToolCall(
                                id="call-reminder",
                                name="create_reminder",
                                arguments={
                                    "text": "prepare",
                                    "due_at": "2030-01-02T12:00:00-05:00",
                                },
                            )
                        ]
                    ),
                    ProviderTurn(text="I'll remind you."),
                ]
            )

        first = provider()
        with patch.object(self.agent.providers, "select", return_value=first):
            await self.agent.handle(
                "retry-device", "Remind me to prepare", request_id="stable-request"
            )
        second = provider()
        with patch.object(self.agent.providers, "select", return_value=second):
            await self.agent.handle(
                "retry-device", "Remind me to prepare", request_id="stable-request"
            )

        self.assertEqual(len(self.store.pending(device_id="retry-device")), 1)

    async def test_one_request_can_create_multiple_distinct_reminders(self):
        provider = FakeToolProvider(
            [
                ProviderTurn(
                    tool_calls=[
                        ToolCall(
                            id="call-first",
                            name="create_reminder",
                            arguments={
                                "text": "prepare",
                                "due_at": "2030-01-02T12:00:00-05:00",
                            },
                        ),
                        ToolCall(
                            id="call-second",
                            name="create_reminder",
                            arguments={
                                "text": "leave",
                                "due_at": "2030-01-02T13:00:00-05:00",
                            },
                        ),
                    ]
                ),
                ProviderTurn(text="Both reminders are set."),
            ]
        )
        with patch.object(self.agent.providers, "select", return_value=provider):
            await self.agent.handle(
                "multi-device", "Set two reminders", request_id="multi-request"
            )

        self.assertEqual(
            [item.text for item in self.store.pending(device_id="multi-device")],
            ["prepare", "leave"],
        )

    async def test_mock_cancel_requires_confirmation_and_survives_restart(self):
        reminder = self.store.create(
            "prepare slides",
            datetime.now(timezone.utc) + timedelta(hours=1),
            device_id="cancel-reminder-device",
        )

        reply = await self.agent.handle(
            "cancel-reminder-device",
            "Cancel the prepare slides reminder",
            request_id="cancel-reminder-request",
        )
        self.assertEqual(reply.surface.title, "Confirmation required")
        self.assertEqual(len(self.store.active("cancel-reminder-device")), 1)

        restarted = FridayAgent(
            self.settings, ReminderStore(self.settings.assistant_database_path)
        )
        confirmed = await restarted.handle(
            "cancel-reminder-device", "yes", request_id="cancel-reminder-confirm"
        )

        self.assertIn("cancelled", confirmed.text)
        self.assertEqual(self.store.active("cancel-reminder-device"), [])
        with closing(
            sqlite3.connect(self.settings.assistant_database_path)
        ) as connection:
            cancelled_at = connection.execute(
                "SELECT cancelled_at FROM reminders WHERE id = ?", (reminder.id,)
            ).fetchone()[0]
        self.assertIsNotNone(cancelled_at)

    async def test_mock_complete_removes_reminder_from_active_list(self):
        reminder = self.store.create(
            "submit expenses",
            datetime.now(timezone.utc) + timedelta(hours=1),
            device_id="complete-device",
        )

        await self.agent.handle(
            "complete-device",
            "Mark the submit expenses reminder complete",
            request_id="complete-request",
        )
        reply = await self.agent.handle(
            "complete-device", "yes", request_id="complete-confirm"
        )

        self.assertIn("complete", reply.text)
        self.assertEqual(self.store.active("complete-device"), [])
        with closing(
            sqlite3.connect(self.settings.assistant_database_path)
        ) as connection:
            completed, completed_at = connection.execute(
                "SELECT completed, completed_at FROM reminders WHERE id = ?",
                (reminder.id,),
            ).fetchone()
        self.assertEqual(completed, 1)
        self.assertIsNotNone(completed_at)

    async def test_mock_snooze_resets_delivery_and_moves_due_time(self):
        now = datetime.now(timezone.utc)
        reminder = self.store.create(
            "call the dentist", now - timedelta(minutes=1), device_id="snooze-device"
        )
        self.store.mark_delivered(reminder.id, "snooze-device", now)

        reply = await self.agent.handle(
            "snooze-device",
            "Snooze the call the dentist reminder for 20 minutes",
            request_id="snooze-request",
        )

        snoozed = self.store.active("snooze-device")[0]
        self.assertIn("snoozed", reply.text)
        self.assertIsNone(snoozed.delivered_at)
        self.assertGreater(snoozed.due_at.timestamp(), now.timestamp() + 19 * 60)

    async def test_mock_ambiguous_reminder_action_does_not_request_confirmation(self):
        due_at = datetime.now(timezone.utc) + timedelta(hours=1)
        self.store.create("prepare slides", due_at, device_id="ambiguous-device")
        self.store.create("prepare notes", due_at, device_id="ambiguous-device")

        reply = await self.agent.handle(
            "ambiguous-device", "Cancel my reminder", request_id="ambiguous-request"
        )

        self.assertIn("multiple matches", reply.text)
        self.assertIsNone(self.store.pending_confirmation("ambiguous-device"))
        self.assertEqual(len(self.store.active("ambiguous-device")), 2)

        selected = await self.agent.handle(
            "ambiguous-device",
            "reminder 2",
            request_id="ambiguous-selection",
        )

        self.assertEqual(selected.surface.title, "Confirmation required")
        self.assertIn("prepare notes", selected.surface.subtitle)
        self.assertEqual(len(self.store.active("ambiguous-device")), 2)

    async def test_reminder_mutation_is_device_scoped_and_idempotent(self):
        reminder = self.store.create(
            "private task",
            datetime.now(timezone.utc) + timedelta(hours=1),
            device_id="owner-device",
        )

        rejected = self.store.mutate_reminder(
            reminder.id,
            "other-device",
            "cancel",
            idempotency_key="wrong-device-action",
        )
        first = self.store.mutate_reminder(
            reminder.id,
            "owner-device",
            "cancel",
            idempotency_key="stable-action",
        )
        replay = self.store.mutate_reminder(
            reminder.id,
            "owner-device",
            "cancel",
            idempotency_key="stable-action",
        )

        self.assertFalse(rejected["ok"])
        self.assertTrue(first["ok"])
        self.assertEqual(replay, first)

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
