"""Run a blocking route body in a worker thread while its coroutines still run on the loop.

The API has one event loop per Cloud Run instance, so sync Firestore / GCS calls
inside an ``async def`` route freeze every request on the instance (see
``backend/services/loop_watchdog.py``). Routes that are mostly sync I/O but must
await a few coroutines (worker triggers) do::

    return await run_in_body_thread(_do_work_sync, ..., asyncio.get_running_loop())

and, inside ``_do_work_sync``, ``run_on_loop(loop, worker_service.trigger_x(job_id))``.
The coroutine runs on the API loop (so loop APIs like ``create_task`` inside it
keep working) while the worker thread waits for its result.
"""
import asyncio
import concurrent.futures
import contextvars
import functools
from typing import Any, Callable, Coroutine, TypeVar

T = TypeVar("T")

# Route bodies block in run_on_loop while the coroutine they dispatched may itself
# need asyncio.to_thread (the loop's default executor). Running the bodies on
# their own pool means they can never use up the threads those coroutines wait for.
_BODY_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=16, thread_name_prefix="route-body")

# Worker triggers finish in seconds (bounded run_job retries); anything this slow
# is hung, and the cron/route should fail rather than hold its thread forever.
RUN_ON_LOOP_TIMEOUT_SECONDS = 120


async def run_in_body_thread(func: Callable[..., T], *args: Any) -> T:
    """Run a sync route body on the dedicated route-body pool and await its result.

    Like ``asyncio.to_thread``, the caller's contextvars (trace / tenant / request
    context) are copied into the thread.
    """
    loop = asyncio.get_running_loop()
    ctx = contextvars.copy_context()
    return await loop.run_in_executor(_BODY_EXECUTOR, functools.partial(ctx.run, func, *args))


def run_on_loop(
    loop: asyncio.AbstractEventLoop,
    coro: Coroutine[Any, Any, T],
    timeout: float = RUN_ON_LOOP_TIMEOUT_SECONDS,
) -> T:
    """From a worker thread, run ``coro`` on ``loop`` and return (or raise) its result."""
    try:
        future = asyncio.run_coroutine_threadsafe(coro, loop)
    except Exception:
        coro.close()  # loop closed (instance shutdown) — don't leak a never-awaited coroutine
        raise
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        future.cancel()
        raise
