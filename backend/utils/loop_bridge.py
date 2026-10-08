"""Run a blocking route body in a worker thread while its coroutines still run on the loop.

The API has one event loop per Cloud Run instance, so sync Firestore / GCS calls
inside an ``async def`` route freeze every request on the instance (see
``backend/services/loop_watchdog.py``). Routes that are mostly sync I/O but must
await a few coroutines (worker triggers) do::

    loop = asyncio.get_running_loop()
    return await asyncio.to_thread(_do_work_sync, ..., loop)

and, inside ``_do_work_sync``, ``run_on_loop(loop, worker_service.trigger_x(job_id))``.
The coroutine runs on the API loop (so loop APIs like ``create_task`` inside it
keep working) while the worker thread waits for its result.
"""
import asyncio
from typing import Coroutine, Any, TypeVar

T = TypeVar("T")


def run_on_loop(loop: asyncio.AbstractEventLoop, coro: Coroutine[Any, Any, T]) -> T:
    """From a worker thread, run ``coro`` on ``loop`` and return (or raise) its result."""
    return asyncio.run_coroutine_threadsafe(coro, loop).result()
