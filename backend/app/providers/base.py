from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderTurn:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class TextProvider(ABC):
    name: str

    @abstractmethod
    async def complete(
        self,
        messages: list[dict[str, Any]],
        instructions: str,
        tools: list[dict[str, Any]],
    ) -> ProviderTurn:
        raise NotImplementedError

    async def respond(self, prompt: str, instructions: str) -> str:
        turn = await self.complete(
            [{"role": "user", "content": prompt}], instructions, []
        )
        return turn.text
