from anthropic import AsyncAnthropic

from .base import TextProvider


class AnthropicProvider(TextProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str):
        self.client = AsyncAnthropic(api_key=api_key)
        self.model = model

    async def respond(self, prompt: str, instructions: str) -> str:
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=500,
            system=instructions,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()

