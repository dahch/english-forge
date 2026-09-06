from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.config import get_settings
from app.llm.base import ChatProvider

logger = logging.getLogger(__name__)


def _extract_content(data: dict, provider_label: str) -> tuple[str | None, str | None]:
    """Extract (content, finish_reason) from a chat-completions response.

    Reasoning models (e.g. DeepSeek served on Fireworks) return the thinking
    process in `message.reasoning_content` and the final answer in
    `message.content` — but when the token budget is consumed by thinking,
    `content` comes back null/empty with finish_reason="length". Some of those
    models emit the answer inside reasoning_content, so it is used as a
    fallback before declaring the response empty. The raw shape is logged
    either way so failures are diagnosable.
    """
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = message.get("content")
    finish_reason = choice.get("finish_reason")
    usage = data.get("usage", {})

    if not content:
        reasoning = message.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning.strip():
            logger.warning(
                f"Provider {provider_label} returned empty content but has reasoning_content "
                f"(finish_reason={finish_reason}, completion_tokens={usage.get('completion_tokens')}) "
                f"— falling back to reasoning_content"
            )
            content = reasoning
        else:
            logger.warning(
                f"Provider {provider_label} returned empty content "
                f"(finish_reason={finish_reason}, message_keys={sorted(message.keys())}, "
                f"usage={usage})"
            )
    return content, finish_reason


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

        content, finish_reason = _extract_content(data, self._label)

        return {
            "content": content,
            "finish_reason": finish_reason,
            "usage": data.get("usage", {}),
            "model": data.get("model", self._model),
        }
