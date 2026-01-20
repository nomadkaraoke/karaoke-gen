"""
Worker registry for tracking active background workers.

This module provides coordination to prevent Cloud Run from shutting down
while workers are still processing. Without this, when one worker completes
and returns (ending the HTTP request), Cloud Run has no reason to keep the
container alive, even if other workers are still running.

Key insight: Workers run in BackgroundTasks, which are tied to the HTTP
request lifecycle. When the request completes, Cloud Run can terminate
the container, even if BackgroundTasks are still running.

This registry:
1. Tracks which workers are active for each job
2. Provides a shutdown hook that waits for workers to complete
3. Has timeout protection to prevent infinite hangs
"""
import asyncio
import logging
import time
from collections import defaultdict
from typing import Dict, Set

logger = logging.getLogger(__name__)


class WorkerRegistry:
    """Track active workers to prevent premature container shutdown."""

    def __init__(self):
        self._active_workers: Dict[str, Set[str]] = defaultdict(set)  # job_id -> {worker_types}
        self._lock = asyncio.Lock()

    async def register(self, job_id: str, worker_type: str) -> None:
        """Register a worker as active.

        Args:
            job_id: The job ID this worker is processing
            worker_type: Type of worker (e.g., 'audio', 'lyrics')
        """
        async with self._lock:
            self._active_workers[job_id].add(worker_type)
            logger.info(
                f"[job:{job_id}] Worker registered: {worker_type}, "
                f"active workers for job: {self._active_workers[job_id]}"
            )

    async def unregister(self, job_id: str, worker_type: str) -> None:
        """Unregister a worker when complete.

        Args:
            job_id: The job ID this worker was processing
            worker_type: Type of worker (e.g., 'audio', 'lyrics')
        """
        async with self._lock:
            self._active_workers[job_id].discard(worker_type)
            remaining = self._active_workers.get(job_id, set())
            if not remaining:
                # Clean up empty entries
                self._active_workers.pop(job_id, None)
                logger.info(
                    f"[job:{job_id}] Worker unregistered: {worker_type}, "
                    f"no workers remaining for job"
                )
            else:
                logger.info(
                    f"[job:{job_id}] Worker unregistered: {worker_type}, "
                    f"remaining workers: {remaining}"
                )

    def has_active_workers(self) -> bool:
        """Check if any workers are still active.

        Returns:
            True if any workers are registered as active
        """
        return len(self._active_workers) > 0

    def get_active_workers(self) -> Dict[str, Set[str]]:
        """Get a snapshot of all active workers.

        Returns:
            Dict mapping job_id to set of active worker types (deep copy)
        """
        # Return a deep copy to prevent external modification of internal state
        return {job_id: set(workers) for job_id, workers in self._active_workers.items()}

    async def wait_for_completion(self, timeout: float = 600) -> bool:
        """Wait for all workers to complete, with timeout.

        This is called during shutdown to ensure workers finish before
        the container terminates.

        Args:
            timeout: Maximum seconds to wait (default 10 minutes)

        Returns:
            True if all workers completed, False if timeout was reached
        """
        start = time.time()
        poll_interval = 1.0  # Check every second
        last_log_time = 0

        while self.has_active_workers():
            elapsed = time.time() - start
            if elapsed > timeout:
                active = self.get_active_workers()
                logger.warning(
                    f"Worker completion timeout after {timeout}s, "
                    f"active workers: {active}"
                )
                return False

            # Log progress every 10 seconds
            elapsed_int = int(elapsed)
            if elapsed_int >= last_log_time + 10:
                last_log_time = elapsed_int
                active = self.get_active_workers()
                logger.info(
                    f"Waiting for workers to complete... "
                    f"elapsed={elapsed:.0f}s, active={active}"
                )

            await asyncio.sleep(poll_interval)

        elapsed = time.time() - start
        logger.info(f"All workers completed (waited {elapsed:.1f}s)")
        return True


# Global registry instance - imported by workers and main.py
worker_registry = WorkerRegistry()
