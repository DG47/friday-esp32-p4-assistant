from typing import Any

from anthropic import AsyncAnthropic

from .base import ProviderTurn, TextProvider, ToolCall


class AnthropicProvider(TextProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str, max_tokens: int = 1024):
        self.client = AsyncAnthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens

    @staticmethod
    def _messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for message in messages:
            role = message["role"]
            if role == "assistant":
                content: list[dict[str, Any]] = []
                if message.get("content"):
                    content.append({"type": "text", "text": message["content"]})
                content.extend(
                    {
                        "type": "tool_use",
                        "id": call["id"],
                        "name": call["name"],
                        "input": call.get("arguments", {}),
                    }
                    for call in message.get("tool_calls", [])
                )
                converted.append({"role": "assistant", "content": content})
            elif role == "tool":
                block = {
                    "type": "tool_result",
                    "tool_use_id": message["tool_call_id"],
                    "content": message["content"],
                }
                if converted and converted[-1]["role"] == "user" and isinstance(
                    converted[-1]["content"], list
                ):
                    converted[-1]["content"].append(block)
                else:
                    converted.append({"role": "user", "content": [block]})
            else:
                converted.append({"role": "user", "content": message["content"]})
        return converted

    async def complete(
        self,
        messages: list[dict[str, Any]],
        instructions: str,
        tools: list[dict[str, Any]],
    ) -> ProviderTurn:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": instructions,
            "messages": self._messages(messages),
        }
        if tools:
            kwargs["tools"] = [
                {
                    "name": tool["function"]["name"],
                    "description": tool["function"]["description"],
                    "input_schema": tool["function"]["parameters"],
                }
                for tool in tools
            ]
        response = await self.client.messages.create(
            **kwargs
        )
        text = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
        calls = [
            ToolCall(id=block.id, name=block.name, arguments=block.input)
            for block in response.content
            if block.type == "tool_use"
        ]
        return ProviderTurn(text=text, tool_calls=calls)
