"""Fake workers for the local E2E stack (``FAKE_WORKERS=1``, never in production).

``WorkerService.trigger_*`` hands every dispatch to :func:`dispatch` when the
setting is on. Each fake runs on its own thread with its own event loop, so it
outlives the request (or short-lived loop) that triggered it, just as a real
Cloud Run Job / Cloud Task would. See ``pipeline.py`` for what each one writes
and docs/TESTING.md § "Local stack" for how tests drive it.
"""
from __future__ import annotations

import asyncio
import logging
import threading

logger = logging.getLogger(__name__)

_threads: set = set()
_threads_lock = threading.Lock()


def fake_workers_enabled(settings=None) -> bool:
    """True only when FAKE_WORKERS is really on (strict, so a MagicMock settings in tests is off)."""
    if settings is None:
        from backend.config import get_settings

        settings = get_settings()
    return getattr(settings, "fake_workers", False) is True


def dispatch(kind: str, job_id: str) -> bool:
    """Start fake worker ``kind`` for ``job_id`` in the background. Returns True (dispatched)."""
    from backend.services.fake_workers.pipeline import RUNNERS, run

    if kind not in RUNNERS:
        raise ValueError(f"Unknown fake worker kind: {kind}")

    def _target():
        try:
            asyncio.run(run(kind, job_id))
        finally:
            with _threads_lock:
                _threads.discard(threading.current_thread())

    thread = threading.Thread(target=_target, name=f"fake-{kind}-{job_id[:8]}", daemon=True)
    with _threads_lock:
        _threads.add(thread)
    thread.start()
    logger.info(f"[job:{job_id}] fake worker dispatched: {kind}")
    return True


def active_worker_count() -> int:
    """Fake workers still running (the test API reports it so tests can wait for idle)."""
    with _threads_lock:
        return sum(1 for t in _threads if t.is_alive())
