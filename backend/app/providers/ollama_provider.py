from __future__ import annotations

import httpx

from .base import TextProvider


class OllamaProvider(TextProvider):
    name = "ollama"

    def __init__(self, base_url: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.model = model

    async def respond(self, prompt: str, instructions: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "think": False,
            "keep_alive": "10m",
            "options": {"temperature": 0.4, "num_predict": 180},
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(f"{self.base_url}/api/chat", json=payload)
            response.raise_for_status()

        content = response.json().get("message", {}).get("content", "").strip()
        if not content:
            raise RuntimeError("Ollama returned an empty response")
        return content
