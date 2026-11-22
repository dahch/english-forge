from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator
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
        # Set once a streaming call starts producing: lets the caller label the
        # partial reply without a second round-trip.
        self.last_provider: str | None = None
        self.last_model: str | None = None

    async def _load_providers(self) -> list[ChatProvider]:
        providers: list[ChatProvider] = []
        if self._db is not None:
            try:
                result = await self._db.execute(
                    select(ProviderConfig)
                    .where(ProviderConfig.user_id == self._user_id, ProviderConfig.is_active == True)
                    .order_by(ProviderConfig.priority.desc())
                )
                configs = result.scalars().all()
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
            except Exception as e:
                logger.warning(f"Failed to query ProviderConfig for user {self._user_id}: {e}")

        if not providers:
            # Fall back to server environment settings
            try:
                from app.config import get_settings
                settings = get_settings()
                if settings.OPENAI_API_KEY:
                    providers.append(
                        OpenAICompatibleProvider(
                            base_url=settings.OPENAI_BASE_URL,
                            api_key=settings.OPENAI_API_KEY,
                            model=settings.OPENAI_MODEL,
                            provider_label="openai_env",
                        )
                    )
                if settings.ANTHROPIC_API_KEY:
                    providers.append(
                        AnthropicProvider(
                            api_key=settings.ANTHROPIC_API_KEY,
                            model=settings.ANTHROPIC_MODEL,
                        )
                    )
                if settings.DEEPSEEK_API_KEY:
                    providers.append(
                        OpenAICompatibleProvider(
                            base_url=settings.DEEPSEEK_BASE_URL,
                            api_key=settings.DEEPSEEK_API_KEY,
                            model=settings.DEEPSEEK_MODEL,
                            provider_label="deepseek_env",
                        )
                    )
                if settings.FIREWORKS_API_KEY:
                    providers.append(
                        OpenAICompatibleProvider(
                            base_url=settings.FIREWORKS_BASE_URL,
                            api_key=settings.FIREWORKS_API_KEY,
                            model=settings.FIREWORKS_MODEL,
                            provider_label="fireworks_env",
                        )
                    )
                if settings.CLINEPASS_API_KEY and settings.CLINEPASS_BASE_URL and settings.CLINEPASS_MODEL:
                    providers.append(
                        OpenAICompatibleProvider(
                            base_url=settings.CLINEPASS_BASE_URL,
                            api_key=settings.CLINEPASS_API_KEY,
                            model=settings.CLINEPASS_MODEL,
                            provider_label="clinepass_env",
                        )
                    )
                if settings.CUSTOM_API_KEY and settings.CUSTOM_BASE_URL and settings.CUSTOM_MODEL:
                    providers.append(
                        OpenAICompatibleProvider(
                            base_url=settings.CUSTOM_BASE_URL,
                            api_key=settings.CUSTOM_API_KEY,
                            model=settings.CUSTOM_MODEL,
                            provider_label="custom_env",
                        )
                    )
            except Exception as e:
                logger.warning(f"Failed to load environment LLM providers: {e}")

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
            # Reasoning models can burn the whole token budget on thinking and
            # return HTTP 200 with empty content (usually finish_reason=
            # "length"). One retry with a bigger budget usually recovers the
            # real answer before the next provider is tried.
            budget = max_tokens
            for attempt in range(2):
                try:
                    result = await provider.complete(
                        messages=messages,
                        system_prompt=system_prompt,
                        temperature=temperature,
                        max_tokens=budget,
                    )
                except Exception as e:
                    logger.warning(f"Provider {provider.provider_name} failed: {e}")
                    last_error = e
                    break

                if result.get("content"):
                    result["provider"] = provider.provider_name
                    result["model"] = provider.model_name
                    return result

                # Empty content with finish_reason="length" means a reasoning
                # model burned the whole budget on thinking before writing the
                # answer — one retry with a bigger budget usually recovers it.
                # Any OTHER empty content won't be fixed by more tokens, so it
                # fails the provider immediately (no wasted spend before the
                # next provider is tried).
                if attempt == 0 and result.get("finish_reason") == "length":
                    budget = max(budget * 4, 4096)
                    logger.warning(
                        f"Provider {provider.provider_name} returned empty content "
                        f"(finish_reason=length) — retrying with max_tokens={budget}"
                    )
                    continue

                # Empty content that wasn't a length-limit (or still empty after
                # the budget bump) — fail loudly so the next provider is tried
                # instead of persisting blank output.
                last_error = ValueError(
                    f"Provider {provider.provider_name} returned empty content "
                    f"(finish_reason={result.get('finish_reason')})"
                )
                logger.warning(f"Provider {provider.provider_name} failed: {last_error}")
                break

        raise RuntimeError(f"All providers failed. Last error: {last_error}")

    async def stream_with_fallback(
        self,
        messages: list[dict[str, str]],
        system_prompt: str,
        *,
        task: str = "conversation",
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> AsyncIterator[str]:
        """Stream text chunks, falling back only before the first token.

        Once a provider has produced output we cannot transparently switch:
        the user would see two different replies concatenated. A mid-stream
        failure is therefore raised to the caller, which decides whether to
        keep the partial text or ask for a retry.
        """
        if self._providers is None:
            self._providers = await self._load_providers()
        if not self._providers:
            raise RuntimeError("No LLM providers configured. Add at least one in Settings.")

        last_error: Exception | None = None
        for provider in self._providers:
            produced = False
            try:
                async for chunk in provider.stream(
                    messages=messages,
                    system_prompt=system_prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ):
                    if not chunk:
                        continue
                    if not produced:
                        produced = True
                        self.last_provider = provider.provider_name
                        self.last_model = provider.model_name
                    yield chunk
                if produced:
                    return
                last_error = ValueError(
                    f"Provider {provider.provider_name} returned an empty stream"
                )
                logger.warning("%s", last_error)
            except Exception as e:  # noqa: BLE001 - fail over before first token
                last_error = e
                logger.warning(f"Provider {provider.provider_name} stream failed: {e}")
                if produced:
                    raise
                continue

        raise RuntimeError(f"All providers failed. Last error: {last_error}")


def parse_llm_json(content: str) -> dict[str, Any]:
    """Parse a JSON object from an LLM response, tolerating markdown fences and prose.

    Tries the whole response first, then balanced JSON objects, then falls back to
    regex extraction for the common conversational/correction/vocab shapes. This
    keeps assessment messages working when the model returns prose while also
    handling structured outputs (assessment analysis, learning paths) that may be
    wrapped in ```json fences or accompanied by explanatory text.

    Always returns a dict — a top-level JSON array (e.g. a bare lessons list) is
    wrapped as {"items": [...]} so callers never crash on a non-dict shape.
    """
    cleaned = _strip_code_fences(content).strip()

    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            return {"items": data}
    except json.JSONDecodeError:
        pass

    # Try repaired JSON (smart quotes, trailing commas) on the whole response.
    try:
        data = json.loads(_repair_json(cleaned))
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            return {"items": data}
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
            if isinstance(data, dict) and any(
                k in data
                for k in (
                    "exercises",
                    "title",
                    "scenario_context",
                    "items",
                    "vocab_items",
                    "passage",
                    "explanation",
                    "goal",
                )
            ):
                return data
    raise ValueError("No lesson JSON found in LLM response")
