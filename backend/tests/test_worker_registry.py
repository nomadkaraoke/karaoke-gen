"""Tests for the worker registry module."""
import asyncio
import pytest

from backend.workers.registry import WorkerRegistry


@pytest.fixture
def registry():
    """Create a fresh WorkerRegistry for each test."""
    return WorkerRegistry()


class TestWorkerRegistry:
    """Test cases for WorkerRegistry."""

    @pytest.mark.asyncio
    async def test_register_adds_worker(self, registry):
        """Register should add worker to active workers."""
        await registry.register("job-123", "audio")

        assert registry.has_active_workers()
        active = registry.get_active_workers()
        assert "job-123" in active
        assert "audio" in active["job-123"]

    @pytest.mark.asyncio
    async def test_unregister_removes_worker(self, registry):
        """Unregister should remove worker from active workers."""
        await registry.register("job-123", "audio")
        await registry.unregister("job-123", "audio")

        assert not registry.has_active_workers()
        assert "job-123" not in registry.get_active_workers()

    @pytest.mark.asyncio
    async def test_multiple_workers_same_job(self, registry):
        """Multiple workers can be registered for the same job."""
        await registry.register("job-123", "audio")
        await registry.register("job-123", "lyrics")

        active = registry.get_active_workers()
        assert "job-123" in active
        assert active["job-123"] == {"audio", "lyrics"}

    @pytest.mark.asyncio
    async def test_partial_unregister_keeps_other_workers(self, registry):
        """Unregistering one worker should keep others active."""
        await registry.register("job-123", "audio")
        await registry.register("job-123", "lyrics")

        await registry.unregister("job-123", "audio")

        assert registry.has_active_workers()
        active = registry.get_active_workers()
        assert "job-123" in active
        assert active["job-123"] == {"lyrics"}

    @pytest.mark.asyncio
    async def test_multiple_jobs(self, registry):
        """Workers from different jobs are tracked separately."""
        await registry.register("job-123", "audio")
        await registry.register("job-456", "lyrics")

        active = registry.get_active_workers()
        assert "job-123" in active
        assert "job-456" in active

    @pytest.mark.asyncio
    async def test_unregister_nonexistent_worker(self, registry):
        """Unregistering a worker that doesn't exist should not error."""
        await registry.unregister("nonexistent-job", "audio")
        assert not registry.has_active_workers()

    @pytest.mark.asyncio
    async def test_wait_for_completion_no_workers(self, registry):
        """Wait should return immediately when no workers are active."""
        result = await registry.wait_for_completion(timeout=1.0)
        assert result is True

    @pytest.mark.asyncio
    async def test_wait_for_completion_workers_finish(self, registry):
        """Wait should complete when workers finish."""
        await registry.register("job-123", "audio")

        # Create a task that unregisters after a delay
        async def unregister_after_delay():
            await asyncio.sleep(0.2)
            await registry.unregister("job-123", "audio")

        asyncio.create_task(unregister_after_delay())

        result = await registry.wait_for_completion(timeout=5.0)
        assert result is True

    @pytest.mark.asyncio
    async def test_wait_for_completion_timeout(self, registry):
        """Wait should return False on timeout."""
        await registry.register("job-123", "audio")

        # Short timeout, worker never unregisters
        result = await registry.wait_for_completion(timeout=0.1)
        assert result is False

    @pytest.mark.asyncio
    async def test_concurrent_registration(self, registry):
        """Concurrent registrations should be thread-safe."""
        job_ids = [f"job-{i}" for i in range(10)]

        async def register_worker(job_id):
            await registry.register(job_id, "audio")

        # Register all workers concurrently
        await asyncio.gather(*[register_worker(job_id) for job_id in job_ids])

        active = registry.get_active_workers()
        assert len(active) == 10
        for job_id in job_ids:
            assert job_id in active

    @pytest.mark.asyncio
    async def test_get_active_workers_returns_copy(self, registry):
        """get_active_workers should return a copy, not the internal state."""
        await registry.register("job-123", "audio")

        active = registry.get_active_workers()
        active["job-123"].add("fake-worker")

        # Internal state should not be affected
        internal = registry.get_active_workers()
        assert "fake-worker" not in internal.get("job-123", set())
