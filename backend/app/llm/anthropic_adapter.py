from __future__ import annotations

from typing import Any

import anthropic

from app.config import get_settings
from app.llm.base import ChatProvider


class AnthropicProvider(ChatProvider):
    def __init__(self, api_key: str, model: str):
        self._api_key = api_key
        self._model = model
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,
            timeout=get_settings().LLM_TIMEOUT_SECONDS,
        )

    @property
    def provider_name(self) -> str:
        return "anthropic"

    @property
    def model_name(self) -> str:
        return self._model

    async def complete(
        self,
        messages: list[dict[str, str]],
        system_prompt: str,
        *,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        response_format: dict | None = None,
    ) -> dict[str, Any]:
        resp = await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=messages,
        )

        content = ""
        for block in resp.content:
            if hasattr(block, "text"):
                content += block.text

        usage = {
            "prompt_tokens": resp.usage.input_tokens,
            "completion_tokens": resp.usage.output_tokens,
            "total_tokens": resp.usage.input_tokens + resp.usage.output_tokens,
        }

        return {
            "content": content,
            "usage": usage,
            "model": resp.model,
        }
