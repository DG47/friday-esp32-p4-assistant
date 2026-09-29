import unittest

from app.providers.anthropic_provider import AnthropicProvider
from app.providers.openai_provider import OpenAIProvider


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


if __name__ == "__main__":
    unittest.main()
