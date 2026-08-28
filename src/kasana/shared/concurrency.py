"""Small async bridge for bounded blocking work."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures._base import Future
from functools import partial

_BLOCKING_EXECUTOR: ThreadPoolExecutor = ThreadPoolExecutor(
    max_workers=8, thread_name_prefix="kasana-blocking"
)


class BlockingExecutor:
    """Own one explicitly bounded pool for a class of blocking operations."""

    def __init__(self, *, max_workers: int, thread_name_prefix: str) -> None:
        if max_workers <= 0:
            raise ValueError("max_workers must be positive.")
        if not thread_name_prefix:
            raise ValueError("thread_name_prefix must not be blank.")
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix=thread_name_prefix
        )
        self._closed = False

    async def run[**Parameters, Result](
        self,
        operation: Callable[Parameters, Result],
        /,
        *args: Parameters.args,
        **kwargs: Parameters.kwargs,
    ) -> Result:
        """Run one operation without blocking the active event loop."""

        if self._closed:
            raise RuntimeError("Blocking executor is closed.")
        future: Future[Result] = self._executor.submit(partial(operation, *args, **kwargs))
        return await _await_future(future)

    def close(self) -> None:
        """Wait for submitted work and release the pool's worker threads."""

        if self._closed:
            return
        self._closed = True
        self._executor.shutdown(wait=True)


async def run_blocking[**Parameters, Result](
    operation: Callable[Parameters, Result],
    /,
    *args: Parameters.args,
    **kwargs: Parameters.kwargs,
) -> Result:
    """Run bounded general filesystem or database work without blocking the event loop."""

    future: Future[Result] = _BLOCKING_EXECUTOR.submit(partial(operation, *args, **kwargs))
    return await _await_future(future)


async def _await_future[Result](future: Future[Result]) -> Result:
    """Await a concurrent future while avoiding Python 3.14 waiter wake-up stalls."""

    # Python 3.14 can leave an asyncio waiter unsignalled when a worker result is complex.
    while not future.done():  # noqa: ASYNC110
        await asyncio.sleep(0.001)
    return future.result()
