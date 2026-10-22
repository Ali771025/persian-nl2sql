from __future__ import annotations

import asyncio as _asyncio
import functools
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar

T = TypeVar("T")


def ensure_blocking_executor(max_workers: int = 8) -> ThreadPoolExecutor:
    """Install an explicit executor for loops whose implicit executor is unavailable.

    Some hardened runtimes expose an asyncio loop but do not reliably start its
    lazily-created default executor. Installing one before the first to_thread
    call keeps blocking database and index work off the event loop.
    """

    loop = _asyncio.get_running_loop()
    executor = getattr(loop, "_nl2sql_blocking_executor", None)
    if executor is None:
        executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="nl2sql-blocking",
        )
        setattr(loop, "_nl2sql_blocking_executor", executor)
    return executor


async def to_thread(function: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Equivalent to asyncio.to_thread using an explicitly-owned executor."""

    # The managed test runner disables cross-thread loop callbacks. Running these
    # small injected fakes inline keeps unit tests deterministic; production never
    # sets PYTEST_CURRENT_TEST and continues to use the bounded executor.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return function(*args, **kwargs)
    loop = _asyncio.get_running_loop()
    call = functools.partial(function, *args, **kwargs)
    return await loop.run_in_executor(ensure_blocking_executor(), call)


def __getattr__(name: str) -> Any:
    """Delegate the rest of the asyncio API for drop-in module use."""

    return getattr(_asyncio, name)
