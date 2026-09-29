from __future__ import annotations

from .anthropic_provider import AnthropicProvider
from .base import TextProvider
from .ollama_provider import OllamaProvider
from .openai_provider import OpenAIProvider
from ..config import Settings


class ProviderRouter:
    def __init__(self, settings: Settings):
        self.settings = settings

    def select(self, prompt: str) -> TextProvider | None:
        requested = self.settings.assistant_provider
        if requested == "mock":
            return None

        if requested == "ollama":
            return OllamaProvider(
                self.settings.ollama_base_url,
                self.settings.ollama_model,
            )

        if requested == "openai":
            if not self.settings.openai_api_key:
                return None
            return OpenAIProvider(
                self.settings.openai_api_key,
                self.settings.openai_text_model,
            )

        if requested == "anthropic":
            if not self.settings.anthropic_api_key:
                return None
            return AnthropicProvider(
                self.settings.anthropic_api_key,
                self.settings.anthropic_text_model,
                self.settings.anthropic_max_tokens,
            )

        providers = {
            "openai": lambda: OpenAIProvider(
                self.settings.openai_api_key,
                self.settings.openai_text_model,
            ),
            "anthropic": lambda: AnthropicProvider(
                self.settings.anthropic_api_key,
                self.settings.anthropic_text_model,
                self.settings.anthropic_max_tokens,
            ),
        }
        available = {
            "openai": bool(self.settings.openai_api_key),
            "anthropic": bool(self.settings.anthropic_api_key),
        }
        preference = self.settings.assistant_auto_provider_preference
        for provider_name in (preference, "anthropic" if preference == "openai" else "openai"):
            if available[provider_name]:
                return providers[provider_name]()
        return None
