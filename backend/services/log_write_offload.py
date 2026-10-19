"""
Keep job-log Firestore writes off the API event loop.

Job logs (jobs/{id}/logs/*) are written synchronously by the JobLogHandler /
JobLogger / job_logging handlers. When that code runs on the API's event loop (the
screens worker and preview encodes run as in-process background tasks), every log
line is a 0.1-5s Firestore round-trip that freezes every request on the instance:
the loop watchdog attributed 45 stalls in a week to append_log_to_subcollection.

`submit_or_run` hands the write to one background thread when the caller is on a
running event loop in the API process, and runs it inline everywhere else (worker
threads, Cloud Run Jobs, scripts), so ordering-sensitive callers are unaffected.
Logs are read back ordered by their own timestamp, so the hand-off doesn't reorder
them. The queue is bounded: under a Firestore outage we drop job-log lines rather
than grow memory.
"""
import asyncio
import atexit
import logging
import os
import queue
import threading
import time
from typing import Callable

logger = logging.getLogger(__name__)

_MAX_PENDING = 5000
_DROP_WARNING_INTERVAL_S = 60.0

_queue: "queue.Queue[Callable[[], None]]" = queue.Queue(maxsize=_MAX_PENDING)
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()
_dropped = 0
_last_drop_warning = 0.0


def _on_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _should_offload() -> bool:
    # A Cloud Run Job runs a single job; blocking its own loop affects nobody else,
    # and a process that exits right after its last log line must not lose it.
    return _on_event_loop() and not os.environ.get("CLOUD_RUN_JOB")


def _run_worker() -> None:
    while True:
        write = _queue.get()
        try:
            write()
        except Exception as e:  # the write itself already swallows; belt and braces
            logger.debug(f"Offloaded job-log write failed: {e}")
        finally:
            _queue.task_done()


def _ensure_worker() -> None:
    global _worker
    if _worker is not None and _worker.is_alive():
        return
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run_worker, name="job-log-writer", daemon=True)
            _worker.start()


def submit_or_run(write: Callable[[], None]) -> None:
    """Run `write` on the background writer if called on the API loop, else inline."""
    global _dropped, _last_drop_warning
    if not _should_offload():
        write()
        return
    _ensure_worker()
    try:
        _queue.put_nowait(write)
    except queue.Full:
        _dropped += 1
        now = time.monotonic()
        if now - _last_drop_warning >= _DROP_WARNING_INTERVAL_S:
            _last_drop_warning = now
            logger.warning(f"Job-log writer queue full ({_MAX_PENDING}); dropped {_dropped} job-log lines so far")


def drain(timeout: float = 10.0) -> bool:
    """Wait (up to `timeout` s) for queued writes to finish. True if fully drained."""
    deadline = time.monotonic() + timeout
    while _queue.unfinished_tasks:
        if _worker is None or not _worker.is_alive() or time.monotonic() >= deadline:
            return False
        time.sleep(0.01)
    return True


atexit.register(drain)
