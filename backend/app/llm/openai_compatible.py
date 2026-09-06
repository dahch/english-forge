from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import get_settings
from app.llm.base import ChatProvider


class OpenAICompatibleProvider(ChatProvider):
    def __init__(self, base_url: str, api_key: str, model: str, *, provider_label: str = "openai"):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._label = provider_label
        self._timeout = get_settings().LLM_TIMEOUT_SECONDS

    @property
    def provider_name(self) -> str:
        return self._label

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
        full_messages = [{"role": "system", "content": system_prompt}] + messages

        body: dict[str, Any] = {
            "model": self._model,
            "messages": full_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            body["response_format"] = response_format

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()

        content = data["choices"][0]["message"].get("content")

        return {
            "content": content,
            "usage": data.get("usage", {}),
            "model": data.get("model", self._model),
        }
