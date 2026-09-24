from __future__ import annotations

from .anthropic_provider import AnthropicProvider
from .base import TextProvider
from .openai_provider import OpenAIProvider
from ..config import Settings


class ProviderRouter:
    def __init__(self, settings: Settings):
        self.settings = settings

    def select(self, prompt: str) -> TextProvider | None:
        requested = self.settings.assistant_provider
        if requested == "mock":
            return None

        wants_long_form = len(prompt) > 800 or any(
            word in prompt.lower() for word in ("document", "analyze", "architecture")
        )

        if requested in ("auto", "anthropic") and wants_long_form:
            if self.settings.anthropic_api_key:
                return AnthropicProvider(
                    self.settings.anthropic_api_key,
                    self.settings.anthropic_text_model,
                )

        if requested in ("auto", "openai") and self.settings.openai_api_key:
            return OpenAIProvider(
                self.settings.openai_api_key,
                self.settings.openai_text_model,
            )

        if self.settings.anthropic_api_key:
            return AnthropicProvider(
                self.settings.anthropic_api_key,
                self.settings.anthropic_text_model,
            )
        return None

