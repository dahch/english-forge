from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.base import ChatProvider
from app.llm.openai_compatible import OpenAICompatibleProvider
from app.llm.anthropic_adapter import AnthropicProvider
from app.models.models import ProviderConfig
from app.integrations.crypto import decrypt_value

logger = logging.getLogger(__name__)


class LLMRouter:
    def __init__(self, db: AsyncSession, user_id: str):
        self._db = db
        self._user_id = user_id
        self._providers: list[ChatProvider] | None = None

    async def _load_providers(self) -> list[ChatProvider]:
        result = await self._db.execute(
            select(ProviderConfig)
            .where(ProviderConfig.user_id == self._user_id, ProviderConfig.is_active == True)
            .order_by(ProviderConfig.priority.desc())
        )
        configs = result.scalars().all()
        providers: list[ChatProvider] = []
        for cfg in configs:
            try:
                api_key = decrypt_value(cfg.api_key_enc) if cfg.api_key_enc else ""
                if not api_key:
                    continue
                if cfg.protocol == "anthropic":
                    providers.append(AnthropicProvider(api_key=api_key, model=cfg.model))
                else:
                    providers.append(
                        OpenAICompatibleProvider(
                            base_url=cfg.base_url,
                            api_key=api_key,
                            model=cfg.model,
                            provider_label=cfg.provider_name,
                        )
                    )
            except Exception as e:
                logger.warning(f"Failed to load provider {cfg.provider_name}: {e}")
        return providers

    async def get_provider(self, task: str = "conversation") -> ChatProvider:
        if self._providers is None:
            self._providers = await self._load_providers()
        if not self._providers:
            raise RuntimeError("No LLM providers configured. Add at least one in Settings.")

        for p in self._providers:
            return p
        raise RuntimeError("No active LLM providers found.")

    async def complete_with_fallback(
        self,
        messages: list[dict[str, str]],
        system_prompt: str,
        *,
        task: str = "conversation",
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        if self._providers is None:
            self._providers = await self._load_providers()
        if not self._providers:
            raise RuntimeError("No LLM providers configured. Add at least one in Settings.")

        last_error: Exception | None = None
        for provider in self._providers:
            try:
                result = await provider.complete(
                    messages=messages,
                    system_prompt=system_prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                result["provider"] = provider.provider_name
                result["model"] = provider.model_name
                return result
            except Exception as e:
                logger.warning(f"Provider {provider.provider_name} failed: {e}")
                last_error = e
                continue

        raise RuntimeError(f"All providers failed. Last error: {last_error}")


def parse_llm_json(content: str) -> dict[str, Any]:
    content = content.strip()

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    json_match = re.search(r"\{[\s\S]*\}", content)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

    reply_match = re.search(r'"reply"\s*:\s*"([^"]*)"', content)
    reply = reply_match.group(1) if reply_match else content

    corrections: list[dict] = []
    corr_pattern = re.compile(
        r'\{\s*"error_type"\s*:\s*"([^"]*)"\s*,\s*"original"\s*:\s*"([^"]*)"\s*,'
        r'\s*"correction"\s*:\s*"([^"]*)"\s*,\s*"explanation"\s*:\s*"([^"]*)"\s*\}'
    )
    for m in corr_pattern.finditer(content):
        corrections.append({
            "error_type": m.group(1),
            "original": m.group(2),
            "correction": m.group(3),
            "explanation": m.group(4),
        })

    new_vocab: list[dict] = []
    vocab_pattern = re.compile(
        r'\{\s*"word"\s*:\s*"([^"]*)"\s*,\s*"definition"\s*:\s*"([^"]*)"\s*,'
        r'\s*"example"\s*:\s*"([^"]*)"\s*\}'
    )
    for m in vocab_pattern.finditer(content):
        new_vocab.append({
            "word": m.group(1),
            "definition": m.group(2),
            "example": m.group(3),
        })

    return {
        "reply": reply,
        "corrections": corrections,
        "new_vocab": new_vocab,
    }
