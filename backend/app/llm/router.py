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
    """Parse a JSON object from an LLM response, tolerating markdown fences and prose.

    Tries the whole response first, then balanced JSON objects, then falls back to
    regex extraction for the common conversational/correction/vocab shapes. This
    keeps assessment messages working when the model returns prose while also
    handling structured outputs (assessment analysis, learning paths) that may be
    wrapped in ```json fences or accompanied by explanatory text.
    """
    cleaned = _strip_code_fences(content).strip()

    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    # Try repaired JSON (smart quotes, trailing commas) on the whole response.
    try:
        return json.loads(_repair_json(cleaned))
    except json.JSONDecodeError:
        pass

    # Extract balanced {...} blocks, preferring larger dicts first.
    candidates = sorted(
        _extract_balanced_objects(cleaned),
        key=lambda b: len(b),
        reverse=True,
    )
    for text in candidates:
        for attempt in (text, _repair_json(text)):
            try:
                data = json.loads(attempt)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                continue

    # Legacy fallback for conversational/structured fragments.
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


def _strip_code_fences(text: str) -> str:
    return re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text.strip())


def _repair_json(text: str) -> str:
    # Common LLM JSON issues: smart quotes and trailing commas.
    text = (
        text.replace("\u201c", '"').replace("\u201d", '"')
        .replace("\u2018", "'").replace("\u2019", "'")
    )
    return re.sub(r",\s*([}\]])", r"\1", text)


def _extract_balanced_objects(text: str) -> list[str]:
    """Extract balanced {...} blocks from text, respecting string literals.

    Unlike a greedy regex, this handles prose containing braces and nested
    objects (e.g. exercise dicts inside a lesson object).
    """
    blocks: list[str] = []
    depth = 0
    start: int | None = None
    in_str = False
    escaped = False
    for i, ch in enumerate(text):
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                blocks.append(text[start:i + 1])
                start = None
    return blocks


def parse_lesson_json(content: str) -> dict[str, Any]:
    """Extract a lesson JSON object from an LLM response.

    Unlike parse_llm_json — which silently falls back to a conversational
    dict — this raises ValueError when no lesson-shaped object can be parsed,
    so callers fail loudly instead of persisting an empty lesson.
    """
    cleaned = _strip_code_fences(content)
    candidates = [cleaned, *_extract_balanced_objects(cleaned)]
    for text in candidates:
        for attempt in (text, _repair_json(text)):
            try:
                data = json.loads(attempt)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and ("exercises" in data or "title" in data):
                return data
    raise ValueError("No lesson JSON found in LLM response")
