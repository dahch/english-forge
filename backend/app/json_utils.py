"""Defensive JSON-column access.

Several columns store JSON as TEXT (`examples`, `exercises`, `metrics`,
`dimension_scores`, `strengths`…). A single corrupt row used to either 500 an
entire list endpoint or silently poison a calculation. Every reader should go
through this helper: corruption is logged once with context and replaced by a
safe default, never by an exception on the request path.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Type

logger = logging.getLogger(__name__)


def load_json(
    value: Any,
    *,
    default: Any = None,
    expected_type: Type | tuple[Type, ...] | None = None,
    context: str = "json column",
) -> Any:
    """Parse a JSON column, returning `default` for anything unusable."""
    if value is None:
        return default

    parsed = value
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning("Corrupt %s: non-UTF-8 bytes", context)
            return default
        parsed = value

    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except (TypeError, ValueError):
            logger.warning("Corrupt %s: invalid JSON (%.120s)", context, parsed)
            return default

    if expected_type is not None and not isinstance(parsed, expected_type):
        logger.warning(
            "Unexpected %s type %s (expected %s)",
            context,
            type(parsed).__name__,
            getattr(expected_type, "__name__", expected_type),
        )
        return default

    return parsed


def as_dict_list(value: Any, *, context: str = "json list") -> list[dict]:
    """List of dicts, dropping anything else (exercises, vocab items…)."""
    items = load_json(value, default=[], expected_type=list, context=context)
    return [item for item in items if isinstance(item, dict)]
