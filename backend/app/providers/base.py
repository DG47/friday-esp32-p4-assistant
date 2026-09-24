from __future__ import annotations

from abc import ABC, abstractmethod


class TextProvider(ABC):
    name: str

    @abstractmethod
    async def respond(self, prompt: str, instructions: str) -> str:
        raise NotImplementedError

