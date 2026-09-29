import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.config import Settings
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.openai_provider import OpenAIProvider
from app.providers.router import ProviderRouter


class ProviderMessageTests(unittest.TestCase):
    def setUp(self):
        self.call_id = "call_0123456789abcdef01234567"
        self.history = [
            {"role": "user", "content": "Cancel it."},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": self.call_id,
                        "name": "cancel_reminder",
                        "arguments": {"reminder_id": 7},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": self.call_id,
                "name": "cancel_reminder",
                "content": '{"ok": true}',
            },
            {"role": "assistant", "content": "The reminder is cancelled."},
        ]

    def test_confirmed_history_converts_for_openai(self):
        messages = OpenAIProvider._messages(self.history, "Be concise.")

        tool_call = messages[2]["tool_calls"][0]
        tool_result = messages[3]
        self.assertEqual(tool_call["id"], self.call_id)
        self.assertEqual(tool_result["tool_call_id"], self.call_id)
        self.assertNotIn("name", tool_result)

    def test_confirmed_history_converts_for_anthropic(self):
        messages = AnthropicProvider._messages(self.history)

        tool_use = messages[1]["content"][0]
        tool_result = messages[2]["content"][0]
        self.assertEqual(tool_use["id"], self.call_id)
        self.assertEqual(tool_result["tool_use_id"], self.call_id)

    def test_auto_router_uses_explicit_preference_regardless_of_prompt_length(self):
        settings = Settings(
            assistant_provider="auto",
            assistant_auto_provider_preference="anthropic",
            anthropic_api_key="anthropic-test-key",
            openai_api_key="openai-test-key",
        )
        router = ProviderRouter(settings)

        short = router.select("Hello")
        long = router.select("Analyze this architecture. " * 100)

        self.assertIsInstance(short, AnthropicProvider)
        self.assertIsInstance(long, AnthropicProvider)

    def test_explicit_provider_does_not_fall_back_to_another_vendor(self):
        settings = Settings(
            assistant_provider="openai",
            openai_api_key="",
            anthropic_api_key="anthropic-test-key",
        )

        self.assertIsNone(ProviderRouter(settings).select("Hello"))


class AnthropicProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_configured_output_limit_is_sent(self):
        provider = AnthropicProvider("anthropic-test-key", "claude-test", 2048)
        client = MagicMock()
        client.messages.create = AsyncMock(return_value=SimpleNamespace(content=[]))
        provider.client = client

        await provider.complete(
            [{"role": "user", "content": "Hello"}],
            "Be concise.",
            [],
        )

        self.assertEqual(client.messages.create.await_args.kwargs["max_tokens"], 2048)


if __name__ == "__main__":
    unittest.main()
