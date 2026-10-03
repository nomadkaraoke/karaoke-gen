"""Per-key re-entrant locks (in-process).

Route handlers now run in FastAPI's threadpool (plain ``def``), so two requests
for the same job/user on one instance can interleave inside non-transactional
check-then-write code (e.g. a double-clicked Cancel refunding twice). When those
handlers were never-awaiting ``async def``s they ran start-to-finish on the
event loop, which serialized them per instance; these locks restore exactly
that per-instance guarantee. They do NOT coordinate across Cloud Run instances
(nothing did before either) — that would need Firestore transactions.

Usage::

    with job_lock(job_id):
        ...check status, then write...
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Dict, Iterator


class KeyedLocks:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: Dict[str, threading.RLock] = {}
        self._refs: Dict[str, int] = {}

    @contextmanager
    def hold(self, key: str) -> Iterator[None]:
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = self._locks[key] = threading.RLock()
            self._refs[key] = self._refs.get(key, 0) + 1
        try:
            with lock:
                yield
        finally:
            with self._guard:
                self._refs[key] -= 1
                if self._refs[key] == 0:
                    # Nobody holds or waits on it — drop it so the map stays small.
                    del self._refs[key]
                    del self._locks[key]


_job_locks = KeyedLocks()
_user_locks = KeyedLocks()


def job_lock(job_id: str):
    return _job_locks.hold(job_id or "")


def user_lock(email: str):
    return _user_locks.hold((email or "").lower())
