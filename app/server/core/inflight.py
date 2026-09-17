"""In-flight request dedupe for slow, idempotent async pipelines.

``dedupe(registry, key, factory)`` runs ``factory()`` once per key while it is in
flight; concurrent callers with the same key await the same result instead of
starting a duplicate pipeline. The shared future is always settled in
``finally`` (result, exception, or cancellation), so joiners never hang, and the
key is always removed so a later retry starts fresh.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


async def dedupe(
    registry: dict[str, asyncio.Future],
    key: str,
    factory: Callable[[], Awaitable[T]],
) -> T:
    """Return ``await factory()``, sharing one run among concurrent callers.

    - Joiners get the same result, or the same exception re-raised.
    - If the first caller is cancelled, joiners get ``CancelledError``.
    - A cancelled joiner does not cancel the shared run (``asyncio.shield``).
    """
    existing = registry.get(key)
    if existing is not None:
        return await asyncio.shield(existing)

    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    registry[key] = fut
    try:
        result = await factory()
    except asyncio.CancelledError:
        if not fut.done():
            fut.cancel()
        raise
    except BaseException as exc:
        if not fut.done():
            fut.set_exception(exc)
            # Mark retrieved: with no joiner, asyncio would otherwise log
            # "Future exception was never retrieved" at GC time.
            fut.exception()
        raise
    else:
        if not fut.done():
            fut.set_result(result)
        return result
    finally:
        if not fut.done():  # defensive: never leave joiners waiting
            fut.cancel()
        if registry.get(key) is fut:
            del registry[key]
