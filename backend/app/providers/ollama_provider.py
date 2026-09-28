from __future__ import annotations

import json
from typing import Any

import httpx

from .base import ProviderTurn, TextProvider, ToolCall


class OllamaProvider(TextProvider):
    name = "ollama"

    def __init__(self, base_url: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.model = model

    async def complete(
        self,
        messages: list[dict[str, Any]],
        instructions: str,
        tools: list[dict[str, Any]],
    ) -> ProviderTurn:
        ollama_messages = [{"role": "system", "content": instructions}]
        for message in messages:
            converted = dict(message)
            if converted.get("role") == "assistant" and converted.get("tool_calls"):
                converted["tool_calls"] = [
                    {
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": call.get("arguments", {}),
                        },
                    }
                    for call in converted["tool_calls"]
                ]
            elif converted.get("role") == "tool":
                converted["tool_name"] = converted.pop("name")
                converted.pop("tool_call_id", None)
            ollama_messages.append(converted)

        payload = {
            "model": self.model,
            "messages": ollama_messages,
            "stream": False,
            "think": False,
            "keep_alive": "10m",
            "options": {"temperature": 0.4, "num_predict": 180},
        }
        if tools:
            payload["tools"] = tools
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(f"{self.base_url}/api/chat", json=payload)
            response.raise_for_status()

        message = response.json().get("message", {})
        calls = []
        for index, item in enumerate(message.get("tool_calls", [])):
            function = item.get("function", {})
            arguments = function.get("arguments", {})
            if isinstance(arguments, str):
                arguments = json.loads(arguments or "{}")
            calls.append(
                ToolCall(
                    id=item.get("id") or f"ollama-{index}",
                    name=str(function.get("name", "")),
                    arguments=arguments if isinstance(arguments, dict) else {},
                )
            )
        content = str(message.get("content", "")).strip()
        if not content and not calls:
            raise RuntimeError("Ollama returned an empty response")
        return ProviderTurn(text=content, tool_calls=calls)
