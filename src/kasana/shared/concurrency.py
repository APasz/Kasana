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


async def run_blocking[**Parameters, Result](
    operation: Callable[Parameters, Result],
    /,
    *args: Parameters.args,
    **kwargs: Parameters.kwargs,
) -> Result:
    """Run bounded filesystem or database work without blocking the event loop."""

    future: Future[Result] = _BLOCKING_EXECUTOR.submit(partial(operation, *args, **kwargs))
    # Python 3.14 can leave an asyncio waiter unsignalled when a worker result is complex.
    while not future.done():  # noqa: ASYNC110
        await asyncio.sleep(0.001)
    return future.result()
