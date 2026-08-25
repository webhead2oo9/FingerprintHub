"""Async helpers for running blocking callables."""

import asyncio
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional, TypeVar

T = TypeVar("T")
_DEFAULT_MAX_WORKERS = 16


def _get_max_workers() -> int:
    """Resolve max worker threads for blocking IO offloads."""
    raw = os.getenv("FINGERPRINTHUB_BLOCKING_IO_MAX_WORKERS", str(_DEFAULT_MAX_WORKERS))
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_WORKERS


_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    """Return the shared bounded executor, creating it on first use."""
    global _executor
    if _executor is None:
        with _executor_lock:
            if _executor is None:
                _executor = ThreadPoolExecutor(
                    max_workers=_get_max_workers(),
                    thread_name_prefix="fingerprinthub-blocking-io",
                )
    return _executor


async def run_blocking_io(func: Callable[[], T], poll_interval: float = 0.005) -> T:
    """
    Run a blocking callable in the shared worker pool and await completion.

    The pool's ``max_workers`` is the concurrency cap, so bursty load queues
    work instead of growing unbounded threads. Exceptions (including
    BaseException subclasses) raised by ``func`` propagate to the caller.

    ``poll_interval`` is retained for signature compatibility; the result is
    awaited directly, with no polling.
    """
    del poll_interval
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_get_executor(), func)
