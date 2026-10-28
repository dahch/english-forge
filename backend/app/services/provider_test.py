"""Connectivity probe for a provider configuration.

Settings needs to answer "does this key/URL/model actually work?" before the
user discovers it mid-conversation. The probe performs one tiny completion and
never returns or logs the API key.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.llm.anthropic_adapter import AnthropicProvider
from app.llm.openai_compatible import OpenAICompatibleProvider

_PROBE_SYSTEM = "You are a connectivity check. Follow instructions exactly."
_PROBE_MESSAGE = "Reply with the single word: OK"


def _sanitize(message: str, api_key: str) -> str:
    if api_key:
        message = message.replace(api_key, "***")
    message = " ".join(message.split())
    return message[:300] or "Unknown provider error"


def _build_provider(
    *, protocol: str, base_url: str, api_key: str, model: str, label: str
):
    if protocol == "anthropic":
        return AnthropicProvider(api_key=api_key, model=model)
    return OpenAICompatibleProvider(
        base_url=base_url,
        api_key=api_key,
        model=model,
        provider_label=label,
    )


async def probe_provider(
    *,
    protocol: str,
    base_url: str,
    api_key: str,
    model: str,
    label: str = "test",
    timeout: float = 20.0,
) -> dict[str, Any]:
    provider = _build_provider(
        protocol=protocol, base_url=base_url, api_key=api_key, model=model, label=label
    )
    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(
            provider.complete(
                messages=[{"role": "user", "content": _PROBE_MESSAGE}],
                system_prompt=_PROBE_SYSTEM,
                temperature=0.0,
                max_tokens=16,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return {
            "ok": False,
            "provider": provider.provider_name,
            "error": f"No response within {int(timeout)}s. Check the base URL and model.",
        }
    except Exception as exc:  # noqa: BLE001 - surfaced to the user, sanitized
        return {
            "ok": False,
            "provider": provider.provider_name,
            "error": _sanitize(str(exc), api_key),
        }

    latency_ms = int((time.perf_counter() - started) * 1000)
    reply = (result.get("content") or "").strip()
    if not reply:
        return {
            "ok": False,
            "provider": provider.provider_name,
            "model": result.get("model") or model,
            "latency_ms": latency_ms,
            "error": "Provider answered but returned empty content (try another model).",
        }

    return {
        "ok": True,
        "provider": provider.provider_name,
        "model": result.get("model") or model,
        "latency_ms": latency_ms,
        "reply": reply[:80],
    }
