"""Per-key async locks for entities that must be mutated one at a time.

SQLite has a single writer, but that alone does not order a *read → network →
write* sequence: two concurrent turns on the same session would both read the
same history, both call the LLM, and both append replies in an arbitrary
order. A keyed lock serializes the whole turn for one session without blocking
other sessions.

Single-process only (documented single uvicorn worker); a multi-worker
deployment needs a cross-process lease.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator


class KeyedLock:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        # Waiters + holder; the entry is dropped only when nobody references it,
        # so a lock object is never replaced while a coroutine waits on it.
        self._refs: dict[str, int] = {}

    @asynccontextmanager
    async def hold(self, key: str) -> AsyncIterator[None]:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        self._refs[key] = self._refs.get(key, 0) + 1
        try:
            async with lock:
                yield
        finally:
            remaining = self._refs.get(key, 1) - 1
            if remaining <= 0:
                self._refs.pop(key, None)
                self._locks.pop(key, None)
            else:
                self._refs[key] = remaining

    def active_keys(self) -> int:
        """Testing/diagnostics helper."""
        return len(self._locks)


_turn_locks = KeyedLock()


def session_turn_lock(session_id: str):
    """Serialize conversation turns for a single session."""
    return _turn_locks.hold(session_id)


def active_turn_locks() -> int:
    return _turn_locks.active_keys()
