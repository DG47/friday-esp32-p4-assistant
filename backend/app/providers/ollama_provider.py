from __future__ import annotations

import json
from typing import Any
import uuid

import httpx

from .base import ProviderTurn, TextProvider, ToolCall
from .local_policy import LocalModelUnavailable, local_endpoint, local_model


class OllamaProvider(TextProvider):
    name = "ollama"

    def __init__(self, base_url: str, model: str, *, local_only=False, context_tokens=None):
        if context_tokens is not None and (type(context_tokens) is not int or not 1024 <= context_tokens <= 8192):
            raise ValueError('Invalid bounded context size')
        self.context_tokens = context_tokens
        self.local_only = local_only
        self.base_url = local_endpoint(base_url) if local_only else base_url.rstrip("/")
        self.model = local_model(model) if local_only else model

    @staticmethod
    def _messages(messages, instructions):
        converted_messages = [{"role": "system", "content": instructions}]
        for message in messages:
            converted = dict(message)
            if converted.get("role") == "assistant" and converted.get("tool_calls"):
                converted["tool_calls"] = [
                    {"id": call["id"], "function": {"index": index, "name": call["name"],
                                                    "arguments": call.get("arguments", {})}}
                    for index, call in enumerate(converted["tool_calls"])
                ]
            elif converted.get("role") == "tool":
                converted["tool_name"] = converted.pop("name")
                # Ollama0.35 supports IDs natively; retain matching result IDs.
            converted_messages.append(converted)
        return converted_messages

    async def _preflight(self, client, *, tools=False):
        # Fresh checks on every model round. Never pull or preload a model.
        try:
            status = await client.get(f"{self.base_url}/api/status", timeout=5.0)
            if status.status_code != 200:
                raise LocalModelUnavailable('cloud_status_unverified')
            data = status.json()
            if (not isinstance(data, dict) or not isinstance(data.get('cloud'), dict)
                    or data['cloud'].get('disabled') is not True):
                raise LocalModelUnavailable('cloud_disable_required')
            shown = await client.post(f"{self.base_url}/api/show", json={'model': self.model}, timeout=5.0)
            if shown.status_code == 404:
                raise LocalModelUnavailable('local_model_missing')
            if shown.status_code != 200:
                raise LocalModelUnavailable('local_model_unavailable')
            model = shown.json()
            if not isinstance(model, dict) or model.get('remote_host', '') != '' or model.get('remote_model', '') != '':
                raise LocalModelUnavailable('remote_model_rejected')
            capabilities = model.get('capabilities')
            if (not isinstance(model.get('model_info'), dict) or not model['model_info']
                    or not isinstance(capabilities, list) or 'completion' not in capabilities
                    or tools and 'tools' not in capabilities):
                raise LocalModelUnavailable('local_model_capabilities_unverified')
        except httpx.RequestError:
            raise LocalModelUnavailable('local_server_unavailable') from None
        except (ValueError, TypeError):
            raise LocalModelUnavailable('local_metadata_invalid') from None

    async def ensure_available(self, *, tools=True):
        if not self.local_only:
            raise ValueError('Local-only availability probe requires local-only mode')
        self.base_url = local_endpoint(self.base_url)
        local_model(self.model)
        # Disable environment proxies and redirects, including for metadata.
        async with httpx.AsyncClient(timeout=5.0, trust_env=False, follow_redirects=False) as client:
            await self._preflight(client, tools=tools)

    async def complete(
        self,
        messages: list[dict[str, Any]],
        instructions: str,
        tools: list[dict[str, Any]],
    ) -> ProviderTurn:
        payload = {
            "model": self.model,
            "messages": self._messages(messages, instructions),
            "stream": False,
            "think": False,
            "keep_alive": "10m",
            "options": {"temperature": 0.4, "num_predict": 180},
        }
        if self.context_tokens is not None:
            payload["options"]["num_ctx"] = self.context_tokens
        if tools:
            payload["tools"] = tools
        kwargs = {'timeout': 120.0}
        if self.local_only:
            # Revalidate even if a caller mutated this provider instance.
            self.base_url = local_endpoint(self.base_url)
            local_model(self.model)
            kwargs.update(trust_env=False, follow_redirects=False)
        async with httpx.AsyncClient(**kwargs) as client:
            if self.local_only:
                await self._preflight(client, tools=bool(tools))
            try:
                response = await client.post(f"{self.base_url}/api/chat", json=payload)
                if self.local_only and response.status_code != 200:
                    raise LocalModelUnavailable('local_model_missing' if response.status_code == 404 else 'local_inference_failed')
                response.raise_for_status()
            except httpx.RequestError:
                if self.local_only:
                    raise LocalModelUnavailable('local_server_unavailable') from None
                raise

        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError()
            if self.local_only and (data.get('remote_host', '') != '' or data.get('remote_model', '') != ''):
                raise LocalModelUnavailable('remote_response_rejected')
            message = data.get('message')
            if not isinstance(message, dict) or not isinstance(message.get('content', ''), str):
                raise ValueError()
            content = message.get('content', '').strip()
            raw_calls = message.get('tool_calls', [])
            if not isinstance(raw_calls, list):
                raise ValueError()
            # Missing IDs are unique across rounds, including parallel same-name tools.
            used = {call['id'] for entry in messages for call in entry.get('tool_calls', [])}
            calls = []
            prefix = 'ollama-' + uuid.uuid4().hex
            for index, item in enumerate(raw_calls):
                function = item['function']
                name = function['name']
                arguments = function.get('arguments', {})
                if isinstance(arguments, str):
                    arguments = json.loads(arguments)
                identifier = item.get('id')
                if identifier is None:
                    identifier = f'{prefix}-{index}'
                if (not isinstance(name, str) or not name or not isinstance(arguments, dict)
                        or not isinstance(identifier, str) or not identifier or identifier in used):
                    raise ValueError()
                used.add(identifier)
                calls.append(ToolCall(id=identifier, name=name, arguments=arguments))
        except (ValueError, TypeError, KeyError):
            if self.local_only:
                raise LocalModelUnavailable('local_response_invalid') from None
            raise RuntimeError('Ollama returned an invalid response') from None
        if not content and not calls:
            if self.local_only:
                raise LocalModelUnavailable('local_response_empty')
            raise RuntimeError("Ollama returned an empty response")
        return ProviderTurn(text=content, tool_calls=calls)
