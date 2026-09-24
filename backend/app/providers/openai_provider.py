from openai import AsyncOpenAI

from .base import TextProvider


class OpenAIProvider(TextProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str):
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model

    async def respond(self, prompt: str, instructions: str) -> str:
        response = await self.client.responses.create(
            model=self.model,
            instructions=instructions,
            input=prompt,
        )
        return response.output_text.strip()

