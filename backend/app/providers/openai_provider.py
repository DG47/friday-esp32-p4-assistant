import json
from typing import Any

from openai import AsyncOpenAI

from .base import ProviderTurn, TextProvider, ToolCall


class OpenAIProvider(TextProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str):
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model

    @staticmethod
    def _messages(
        messages: list[dict[str, Any]], instructions: str
    ) -> list[dict[str, Any]]:
        converted_messages: list[dict[str, Any]] = [
            {"role": "system", "content": instructions}
        ]
        for message in messages:
            converted = dict(message)
            if converted.get("role") == "assistant" and converted.get("tool_calls"):
                converted["tool_calls"] = [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": json.dumps(call.get("arguments", {})),
                        },
                    }
                    for call in converted["tool_calls"]
                ]
            elif converted.get("role") == "tool":
                converted.pop("name", None)
            converted_messages.append(converted)
        return converted_messages

    async def complete(
        self,
        messages: list[dict[str, Any]],
        instructions: str,
        tools: list[dict[str, Any]],
    ) -> ProviderTurn:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self._messages(messages, instructions),
        }
        if tools:
            kwargs["tools"] = tools
        response = await self.client.chat.completions.create(**kwargs)
        message = response.choices[0].message
        calls = []
        for item in message.tool_calls or []:
            arguments = json.loads(item.function.arguments or "{}")
            calls.append(
                ToolCall(
                    id=item.id,
                    name=item.function.name,
                    arguments=arguments if isinstance(arguments, dict) else {},
                )
            )
        return ProviderTurn(text=message.content or "", tool_calls=calls)
