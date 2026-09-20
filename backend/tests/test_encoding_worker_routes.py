"""Route tests for the encoding-worker warmup/heartbeat endpoints.

Regression context (2026-09-19): these handlers called sync manager methods
(Firestore + GCE compute API, 10s+) directly in async handlers, freezing the
single event loop — every other request on the instance (the same review
page's audio/lyrics loads!) stalled behind a cold-VM warmup. They must run
their manager work via asyncio.to_thread.
"""

import asyncio
import time
from unittest.mock import MagicMock

from backend.api.routes import encoding_worker
from backend.services.encoding_errors import EncodingWorkerStartError


class TestWarmupHandler:
    def _run(self, manager):
        return asyncio.run(
            encoding_worker.warmup_encoding_worker("job-1", ("admin", "full"), manager)
        )

    def test_success_passthrough(self):
        manager = MagicMock()
        manager.ensure_primary_running.return_value = {"started": True, "vm_name": "encoding-worker-a"}
        assert self._run(manager)["started"] is True

    def test_start_error_is_soft(self):
        manager = MagicMock()
        manager.ensure_primary_running.side_effect = EncodingWorkerStartError("zone exhausted")
        result = self._run(manager)
        assert result["started"] is False
        assert "zone exhausted" in result["error"]

    def test_slow_manager_does_not_block_event_loop(self):
        """The whole point of the fix: a slow warmup must not freeze the loop."""
        manager = MagicMock()

        def slow_warmup():
            time.sleep(0.5)
            return {"started": False}

        manager.ensure_primary_running.side_effect = slow_warmup

        async def scenario():
            ticks = 0

            async def ticker():
                nonlocal ticks
                while True:
                    ticks += 1
                    await asyncio.sleep(0.05)

            t = asyncio.create_task(ticker())
            await encoding_worker.warmup_encoding_worker("job-1", ("admin", "full"), manager)
            t.cancel()
            return ticks

        # With the sync-on-loop bug, the ticker would run ~0 times during the
        # 0.5s warmup; off-loop it keeps ticking throughout.
        assert asyncio.run(scenario()) >= 5


class TestHeartbeatHandler:
    def test_ok(self):
        manager = MagicMock()
        result = asyncio.run(
            encoding_worker.heartbeat_encoding_worker("job-1", ("admin", "full"), manager)
        )
        assert result == {"status": "ok"}
        manager.update_activity.assert_called_once()
