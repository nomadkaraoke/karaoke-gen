# GCE Encoding Worker Cold-Start Resilience — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop render/preview-encode requests from failing when the GCE encoding VM is cold (TERMINATED), by adding an active readiness wait that engages only when the VM had to be started from scratch.

**Architecture:** Add `EncodingWorkerManager.wait_for_worker_ready()` (two-phase poll: VM status → worker `/health`). Make `EncodingService._warmup_encoding_worker_fallback` async and have it await readiness when `ensure_primary_running()` reports `started=True`. Existing 90s retry loop stays as the deploy-restart fast path. Improve exception logging so empty `aiohttp.ClientConnectorError` messages still show the type.

**Tech Stack:** Python 3.11, FastAPI, aiohttp, pytest + pytest-asyncio, google-cloud-compute, google-cloud-firestore.

**Spec:** `docs/archive/2026-04-27-gce-encoding-cold-start-design.md`

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `backend/services/encoding_worker_manager.py` | Modify | Add `wait_for_worker_ready()` method (~50 lines) |
| `backend/services/encoding_service.py` | Modify | Make `_warmup_encoding_worker_fallback` async; integrate readiness wait; add `_format_exception` helper; update `_request_with_retry` log calls |
| `backend/tests/test_encoding_worker_manager.py` | Modify | Add tests for `wait_for_worker_ready` (5 cases) |
| `backend/tests/test_encoding_service.py` | Modify | Update existing 3 warmup tests for async; add new tests for cold-start path, log format, integration |
| `docs/TROUBLESHOOTING.md` | Modify | Add runbook entry for cold-start verification |
| `pyproject.toml` | Modify | Bump patch version `0.172.2 → 0.172.3` |

No new files. No new dependencies.

---

## Task 1: Add `wait_for_worker_ready` to `EncodingWorkerManager`

**Files:**
- Modify: `backend/services/encoding_worker_manager.py`
- Test: `backend/tests/test_encoding_worker_manager.py`

- [ ] **Step 1.1: Write the failing tests**

Append to `backend/tests/test_encoding_worker_manager.py`:

```python
# ---------------------------------------------------------------------------
# Task 3: wait_for_worker_ready (cold-start readiness gate)
# ---------------------------------------------------------------------------

import asyncio
from unittest.mock import AsyncMock


class TestWaitForWorkerReady:
    """Tests for the wait_for_worker_ready() readiness gate."""

    @pytest.mark.asyncio
    async def test_returns_immediately_when_vm_running_and_healthy(self, manager, mock_compute):
        """Happy path: VM already RUNNING, /health returns 200 on first poll."""
        mock_instance = MagicMock()
        mock_instance.status = "RUNNING"
        mock_compute.get.return_value = mock_instance

        async def fake_get(url, **kwargs):
            class Resp:
                status = 200
                async def __aenter__(self): return self
                async def __aexit__(self, *a): return None
            return Resp()

        mock_session = MagicMock()
        mock_session.get = fake_get
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("backend.services.encoding_worker_manager.aiohttp.ClientSession", return_value=mock_session):
            await manager.wait_for_worker_ready(
                "encoding-worker-blue",
                "http://10.0.0.1:8080/health",
                vm_timeout=2.0,
                health_timeout=2.0,
                poll_interval=0.05,
            )

        # Single VM status check, single health check
        assert mock_compute.get.call_count == 1

    @pytest.mark.asyncio
    async def test_polls_vm_until_running(self, manager, mock_compute):
        """Polls VM status through STAGING → RUNNING."""
        statuses = ["STAGING", "STAGING", "RUNNING"]
        instances = [MagicMock(status=s) for s in statuses]
        mock_compute.get.side_effect = instances

        async def healthy_get(url, **kwargs):
            class Resp:
                status = 200
                async def __aenter__(self): return self
                async def __aexit__(self, *a): return None
            return Resp()

        mock_session = MagicMock()
        mock_session.get = healthy_get
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("backend.services.encoding_worker_manager.aiohttp.ClientSession", return_value=mock_session):
            await manager.wait_for_worker_ready(
                "encoding-worker-blue",
                "http://10.0.0.1:8080/health",
                vm_timeout=2.0,
                health_timeout=2.0,
                poll_interval=0.05,
            )

        assert mock_compute.get.call_count == 3

    @pytest.mark.asyncio
    async def test_times_out_when_vm_never_runs(self, manager, mock_compute):
        """Raises TimeoutError if VM stays in STAGING past vm_timeout."""
        mock_instance = MagicMock()
        mock_instance.status = "STAGING"
        mock_compute.get.return_value = mock_instance

        with pytest.raises(TimeoutError, match="did not reach RUNNING"):
            await manager.wait_for_worker_ready(
                "encoding-worker-blue",
                "http://10.0.0.1:8080/health",
                vm_timeout=0.2,
                health_timeout=2.0,
                poll_interval=0.05,
            )

    @pytest.mark.asyncio
    async def test_times_out_when_health_never_200(self, manager, mock_compute):
        """Raises TimeoutError if /health never returns 200 past health_timeout."""
        mock_instance = MagicMock()
        mock_instance.status = "RUNNING"
        mock_compute.get.return_value = mock_instance

        async def unhealthy_get(url, **kwargs):
            class Resp:
                status = 503
                async def __aenter__(self): return self
                async def __aexit__(self, *a): return None
            return Resp()

        mock_session = MagicMock()
        mock_session.get = unhealthy_get
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("backend.services.encoding_worker_manager.aiohttp.ClientSession", return_value=mock_session):
            with pytest.raises(TimeoutError, match="did not become healthy"):
                await manager.wait_for_worker_ready(
                    "encoding-worker-blue",
                    "http://10.0.0.1:8080/health",
                    vm_timeout=2.0,
                    health_timeout=0.2,
                    poll_interval=0.05,
                )

    @pytest.mark.asyncio
    async def test_tolerates_health_connection_errors(self, manager, mock_compute):
        """Connection errors during /health polling are treated as 'not ready', keep polling."""
        mock_instance = MagicMock()
        mock_instance.status = "RUNNING"
        mock_compute.get.return_value = mock_instance

        call_count = {"n": 0}

        async def flaky_get(url, **kwargs):
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise aiohttp.ClientConnectorError(MagicMock(), OSError("connection refused"))
            class Resp:
                status = 200
                async def __aenter__(self): return self
                async def __aexit__(self, *a): return None
            return Resp()

        mock_session = MagicMock()
        mock_session.get = flaky_get
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("backend.services.encoding_worker_manager.aiohttp.ClientSession", return_value=mock_session):
            await manager.wait_for_worker_ready(
                "encoding-worker-blue",
                "http://10.0.0.1:8080/health",
                vm_timeout=2.0,
                health_timeout=2.0,
                poll_interval=0.05,
            )

        assert call_count["n"] == 3
```

Also add `import aiohttp` at the top of the test file alongside the other imports.

- [ ] **Step 1.2: Run tests to verify they fail**

```bash
cd backend && python -m pytest tests/test_encoding_worker_manager.py::TestWaitForWorkerReady -v
```
Expected: 5 failures with `AttributeError: 'EncodingWorkerManager' object has no attribute 'wait_for_worker_ready'`.

- [ ] **Step 1.3: Implement `wait_for_worker_ready`**

In `backend/services/encoding_worker_manager.py`, add the import at the top:

```python
import asyncio
import aiohttp
```

Add at the bottom of the `EncodingWorkerManager` class (after `ensure_primary_running`):

```python
    # ------------------------------------------------------------------
    # Task 3: cold-start readiness gate
    # ------------------------------------------------------------------

    async def wait_for_worker_ready(
        self,
        vm_name: str,
        health_url: str,
        *,
        vm_timeout: float = 120.0,
        health_timeout: float = 180.0,
        poll_interval: float = 5.0,
    ) -> None:
        """Block until the VM is RUNNING and the worker /health endpoint returns 200.

        Two-phase wait, used after ensure_primary_running() reports started=True
        (i.e., the VM was actually TERMINATED and is cold-booting):

          Phase 1 — poll get_vm_status until RUNNING (max vm_timeout seconds)
          Phase 2 — poll GET health_url until 200 (max health_timeout seconds)

        Connection errors and non-200 responses during phase 2 are treated as
        "not ready, keep polling". A TimeoutError is raised if either phase
        exceeds its budget.

        Args:
            vm_name: Name of the GCE VM to poll.
            health_url: Full URL of the worker /health endpoint
                (e.g. "http://10.0.0.1:8080/health").
            vm_timeout: Max seconds to wait for VM RUNNING.
            health_timeout: Max seconds to wait for /health 200.
            poll_interval: Seconds between polls in either phase.

        Raises:
            TimeoutError: If VM never reaches RUNNING, or /health never returns 200.
        """
        # Phase 1: VM status
        loop = asyncio.get_event_loop()
        deadline = loop.time() + vm_timeout
        last_status = None
        last_log = 0.0
        while loop.time() < deadline:
            last_status = self.get_vm_status(vm_name)
            if last_status == "RUNNING":
                logger.info("VM %s reached RUNNING", vm_name)
                break
            now = loop.time()
            if now - last_log >= 30.0:
                logger.info("Waiting for VM %s (status=%s)", vm_name, last_status)
                last_log = now
            await asyncio.sleep(poll_interval)
        else:
            raise TimeoutError(
                f"VM {vm_name} did not reach RUNNING within {vm_timeout}s "
                f"(last status: {last_status})"
            )

        # Phase 2: worker /health
        deadline = loop.time() + health_timeout
        last_log = 0.0
        last_detail = "no response"
        while loop.time() < deadline:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(health_url, timeout=5.0) as resp:
                        if resp.status == 200:
                            logger.info("Worker at %s is healthy", health_url)
                            return
                        last_detail = f"HTTP {resp.status}"
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
                last_detail = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
            now = loop.time()
            if now - last_log >= 30.0:
                logger.info("Waiting for worker /health at %s (last: %s)", health_url, last_detail)
                last_log = now
            await asyncio.sleep(poll_interval)

        raise TimeoutError(
            f"Worker at {health_url} did not become healthy within {health_timeout}s "
            f"(last: {last_detail})"
        )
```

- [ ] **Step 1.4: Run tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_encoding_worker_manager.py::TestWaitForWorkerReady -v
```
Expected: 5 PASSED.

- [ ] **Step 1.5: Commit**

```bash
git add backend/services/encoding_worker_manager.py backend/tests/test_encoding_worker_manager.py
git commit -m "feat(encoding): add wait_for_worker_ready cold-start readiness gate

Two-phase poll (VM status -> /health 200) used by encoding_service when
ensure_primary_running reports the VM was actually started from
TERMINATED. Lets cold-start jobs survive the 2-5 minute VM boot window
without exhausting the 90s deploy-restart retry loop.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Add `_format_exception` helper and use it in retry logs

**Files:**
- Modify: `backend/services/encoding_service.py`
- Test: `backend/tests/test_encoding_service.py`

- [ ] **Step 2.1: Write the failing test**

Append to `backend/tests/test_encoding_service.py` (after `TestWarmupFallback`):

```python
class TestFormatException:
    """Tests for the _format_exception helper used in retry logs."""

    def test_renders_message_when_present(self):
        from backend.services.encoding_service import _format_exception
        e = RuntimeError("something broke")
        assert _format_exception(e) == "RuntimeError: something broke"

    def test_renders_type_only_when_message_empty(self):
        """aiohttp.ClientConnectorError often has empty str(e) — show type."""
        from backend.services.encoding_service import _format_exception

        class _SilentError(Exception):
            def __str__(self):
                return ""

        assert _format_exception(_SilentError()) == "_SilentError"

    def test_handles_real_aiohttp_connector_error(self):
        from backend.services.encoding_service import _format_exception
        e = aiohttp.ClientConnectorError(MagicMock(), OSError())
        # Type name should always appear
        assert "ClientConnectorError" in _format_exception(e)
```

- [ ] **Step 2.2: Run test to verify it fails**

```bash
cd backend && python -m pytest tests/test_encoding_service.py::TestFormatException -v
```
Expected: 3 failures with `ImportError: cannot import name '_format_exception'`.

- [ ] **Step 2.3: Add the helper and apply to retry logs**

In `backend/services/encoding_service.py`, after the `MAX_CONSECUTIVE_POLL_FAILURES` constant (around line 45), add:

```python
def _format_exception(e: BaseException) -> str:
    """Render an exception with type info.

    Some aiohttp connection errors have empty str(e), which made the original
    "GCE worker connection failed after 8 attempts: " log line useless during
    the 2026-04-24 cold-start incident (job 2c577535). Always include the
    type name so operators can tell what failure class hit them.
    """
    msg = str(e)
    return f"{type(e).__name__}: {msg}" if msg else type(e).__name__
```

Then update the two log calls inside `_request_with_retry` (around lines 204-213) to use it:

```python
                if attempt < MAX_RETRIES:
                    logger.warning(
                        f"[job:{job_id}] GCE worker connection failed "
                        f"(attempt {attempt + 1}/{MAX_RETRIES + 1}): {_format_exception(e)}. "
                        f"Retrying in {backoff:.1f}s..."
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
                else:
                    logger.error(
                        f"[job:{job_id}] GCE worker connection failed "
                        f"after {MAX_RETRIES + 1} attempts: {_format_exception(e)}"
                    )
```

- [ ] **Step 2.4: Run test to verify it passes**

```bash
cd backend && python -m pytest tests/test_encoding_service.py::TestFormatException -v
```
Expected: 3 PASSED.

- [ ] **Step 2.5: Commit**

```bash
git add backend/services/encoding_service.py backend/tests/test_encoding_service.py
git commit -m "feat(encoding): show exception type in retry logs

Some aiohttp.ClientConnectorError instances have empty str(e), which made
the 'GCE worker connection failed after 8 attempts:' log line during the
2026-04-24 cold-start incident (job 2c577535) useless. Always include the
exception type name so operators can identify the failure class.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Make `_warmup_encoding_worker_fallback` async and integrate readiness wait

**Files:**
- Modify: `backend/services/encoding_service.py`
- Test: `backend/tests/test_encoding_service.py`

- [ ] **Step 3.1: Update existing warmup tests for async signature**

In `backend/tests/test_encoding_service.py`, replace the entire `TestWarmupFallback` class (around lines 242-270) with:

```python
class TestWarmupFallback:
    """Tests for _warmup_encoding_worker_fallback() in the retry loop."""

    @pytest.mark.asyncio
    async def test_warmup_fallback_called_on_first_connection_failure(self, encoding_service):
        """Warmup fallback is called when the first connection attempt fails."""
        mock_manager = MagicMock()
        mock_manager.ensure_primary_running.return_value = {
            "started": False, "vm_name": "encoding-worker-b", "primary_url": "http://1.2.3.4:8080"
        }
        encoding_service._worker_manager = mock_manager

        await encoding_service._warmup_encoding_worker_fallback("test-job")

        mock_manager.ensure_primary_running.assert_called_once()

    @pytest.mark.asyncio
    async def test_warmup_fallback_noop_without_worker_manager(self, encoding_service):
        """Warmup fallback is a no-op when worker_manager is not set (dev mode)."""
        encoding_service._worker_manager = None
        # Should not raise
        await encoding_service._warmup_encoding_worker_fallback("test-job")

    @pytest.mark.asyncio
    async def test_warmup_fallback_swallows_exceptions(self, encoding_service):
        """Warmup fallback never raises — failures are logged but non-fatal."""
        mock_manager = MagicMock()
        mock_manager.ensure_primary_running.side_effect = Exception("Compute API down")
        encoding_service._worker_manager = mock_manager

        # Should not raise
        await encoding_service._warmup_encoding_worker_fallback("test-job")

    @pytest.mark.asyncio
    async def test_warmup_skips_readiness_wait_when_vm_already_running(self, encoding_service):
        """When started=False (deploy restart), DO NOT await readiness — fall back to fast retry."""
        mock_manager = MagicMock()
        mock_manager.ensure_primary_running.return_value = {
            "started": False, "vm_name": "encoding-worker-b", "primary_url": "http://1.2.3.4:8080"
        }
        mock_manager.wait_for_worker_ready = AsyncMock()
        encoding_service._worker_manager = mock_manager

        await encoding_service._warmup_encoding_worker_fallback("test-job")

        mock_manager.wait_for_worker_ready.assert_not_called()

    @pytest.mark.asyncio
    async def test_warmup_awaits_readiness_when_cold_started(self, encoding_service):
        """When started=True (VM was TERMINATED), AWAIT wait_for_worker_ready."""
        mock_manager = MagicMock()
        mock_manager.ensure_primary_running.return_value = {
            "started": True, "vm_name": "encoding-worker-a", "primary_url": "http://1.2.3.4:8080"
        }
        mock_manager.wait_for_worker_ready = AsyncMock()
        encoding_service._worker_manager = mock_manager

        await encoding_service._warmup_encoding_worker_fallback("test-job")

        mock_manager.wait_for_worker_ready.assert_awaited_once_with(
            "encoding-worker-a",
            "http://1.2.3.4:8080/health",
        )

    @pytest.mark.asyncio
    async def test_warmup_swallows_readiness_timeout(self, encoding_service):
        """If wait_for_worker_ready times out, log and return — main retry loop will surface it."""
        mock_manager = MagicMock()
        mock_manager.ensure_primary_running.return_value = {
            "started": True, "vm_name": "encoding-worker-a", "primary_url": "http://1.2.3.4:8080"
        }
        mock_manager.wait_for_worker_ready = AsyncMock(side_effect=TimeoutError("VM stuck in STAGING"))
        encoding_service._worker_manager = mock_manager

        # Should not raise
        await encoding_service._warmup_encoding_worker_fallback("test-job")

        mock_manager.wait_for_worker_ready.assert_awaited_once()
```

- [ ] **Step 3.2: Run updated warmup tests to verify they fail**

```bash
cd backend && python -m pytest tests/test_encoding_service.py::TestWarmupFallback -v
```
Expected: at least 3 failures (the new test cases) — old ones may pass coincidentally because the sync method gets called, but the new `await` is not yet wired.

- [ ] **Step 3.3: Convert the method and integrate readiness wait**

In `backend/services/encoding_service.py`, replace the entire `_warmup_encoding_worker_fallback` method (around lines 118-138) with:

```python
    async def _warmup_encoding_worker_fallback(self, job_id: str) -> None:
        """Safety net: try to start the encoding worker VM on first connection failure.

        Two paths:
          - VM already RUNNING/STAGING (deploy restart): return after the start
            attempt; the existing 90s retry loop covers the systemctl restart window.
          - VM was TERMINATED (cold start): block on wait_for_worker_ready until
            the VM is up and /health returns 200. The next retry attempt will
            then succeed immediately. If the readiness wait times out, log and
            return — the retry loop will surface the failure with a clear error.

        If the worker_manager is not set (e.g. dev mode with static URL), this
        is a no-op.
        """
        if not self._worker_manager:
            return
        try:
            result = self._worker_manager.ensure_primary_running()
        except Exception as e:
            logger.warning(
                f"[job:{job_id}] Encoding worker warmup fallback failed (non-fatal): "
                f"{_format_exception(e)}"
            )
            return

        if result["started"]:
            logger.warning(
                f"[job:{job_id}] Encoding worker unreachable — started VM {result['vm_name']} as fallback"
            )
            try:
                await self._worker_manager.wait_for_worker_ready(
                    result["vm_name"],
                    f"{result['primary_url']}/health",
                )
                logger.info(
                    f"[job:{job_id}] Cold-started VM {result['vm_name']} is now ready"
                )
            except TimeoutError as e:
                logger.warning(
                    f"[job:{job_id}] Cold-start readiness wait timed out: {e}"
                )
            except Exception as e:
                logger.warning(
                    f"[job:{job_id}] Cold-start readiness wait failed (non-fatal): "
                    f"{_format_exception(e)}"
                )
        else:
            logger.info(
                f"[job:{job_id}] Encoding worker unreachable — VM {result['vm_name']} already running/starting"
            )
```

Then update the single caller inside `_request_with_retry` (around line 202) to `await`:

```python
                if attempt == 0:
                    await self._warmup_encoding_worker_fallback(job_id)
```

- [ ] **Step 3.4: Run all warmup tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_encoding_service.py::TestWarmupFallback -v
```
Expected: 6 PASSED.

- [ ] **Step 3.5: Run the full encoding_service test file to catch regressions**

```bash
cd backend && python -m pytest tests/test_encoding_service.py -v
```
Expected: all PASSED. The retry-loop tests should still pass because the `await` change is a pure additive.

- [ ] **Step 3.6: Commit**

```bash
git add backend/services/encoding_service.py backend/tests/test_encoding_service.py
git commit -m "feat(encoding): await readiness wait on cold-start, async warmup fallback

When ensure_primary_running() reports started=True (VM was TERMINATED),
the warmup fallback now awaits wait_for_worker_ready() before returning.
This blocks the retry loop's next attempt until the VM is genuinely up
and /health returns 200, fixing the 2026-04-24 cold-start incident
(job 2c577535) where the 90s retry window was insufficient for a
2-5 minute GCE cold boot.

The deploy-restart fast path (started=False) is unchanged — the existing
retry loop handles systemctl restart windows.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: End-to-end integration test — cold start → readiness wait → retry succeeds

**Files:**
- Test: `backend/tests/test_encoding_service.py`

- [ ] **Step 4.1: Write the failing test**

Append to `backend/tests/test_encoding_service.py` (after `TestWarmupFallback`):

```python
class TestColdStartIntegration:
    """End-to-end: first request fails, warmup awaits readiness, retry succeeds."""

    @pytest.mark.asyncio
    async def test_cold_start_recovery_no_retry_exhaustion(self, encoding_service):
        """
        Simulates the 2026-04-24 incident path with the fix in place:
          1. First HTTP attempt raises ClientConnectorError (VM was TERMINATED).
          2. Warmup fallback runs, ensure_primary_running returns started=True.
          3. wait_for_worker_ready resolves quickly (mocked).
          4. Second HTTP attempt succeeds.
        With the fix, no 8-retry exhaustion happens.
        """
        # Mock worker manager
        mock_manager = MagicMock()
        mock_manager.ensure_primary_running.return_value = {
            "started": True, "vm_name": "encoding-worker-a", "primary_url": "http://1.2.3.4:8080"
        }
        mock_manager.wait_for_worker_ready = AsyncMock()
        encoding_service._worker_manager = mock_manager

        # First call raises, second call succeeds
        call_count = {"n": 0}

        class _Resp:
            def __init__(self, status, payload):
                self.status = status
                self._payload = payload
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None
            async def json(self): return self._payload
            async def text(self): return ""

        class _Session:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None
            def post(self, *a, **kw):
                call_count["n"] += 1
                if call_count["n"] == 1:
                    raise aiohttp.ClientConnectorError(MagicMock(), OSError())
                return _Resp(200, {"status": "accepted", "job_id": "j1"})

        with patch("backend.services.encoding_service.aiohttp.ClientSession", return_value=_Session()), \
             patch("backend.services.encoding_service.asyncio.sleep", new_callable=AsyncMock):
            result = await encoding_service._request_with_retry(
                "POST",
                "http://1.2.3.4:8080/encode",
                headers={},
                json_payload={},
                timeout=5.0,
                job_id="j1",
            )

        assert result["status"] == 200
        assert call_count["n"] == 2  # one fail, one success — no retry exhaustion
        mock_manager.ensure_primary_running.assert_called_once()
        mock_manager.wait_for_worker_ready.assert_awaited_once()
```

- [ ] **Step 4.2: Run the test to verify it passes**

```bash
cd backend && python -m pytest tests/test_encoding_service.py::TestColdStartIntegration -v
```
Expected: 1 PASSED. (This test should pass on the current code from Tasks 1-3 — it's a regression guard, not a TDD-driven test.)

- [ ] **Step 4.3: Commit**

```bash
git add backend/tests/test_encoding_service.py
git commit -m "test(encoding): cold-start integration regression guard

Simulates the 2026-04-24 incident path end-to-end: first HTTP attempt
raises ClientConnectorError, warmup fallback awaits readiness, second
attempt succeeds. Guards against future regressions of the cold-start fix.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Add troubleshooting runbook entry

**Files:**
- Modify: `docs/TROUBLESHOOTING.md`

- [ ] **Step 5.1: Read the existing TROUBLESHOOTING.md to find the right insertion point**

```bash
grep -n "^##" docs/TROUBLESHOOTING.md | head -30
```

Find the section that covers GCE encoding worker issues (likely "Job stuck at encoding" or similar). The new runbook entry goes there as a subsection. If no GCE section exists, create one before "Other issues" or at the end.

- [ ] **Step 5.2: Add the runbook entry**

Append (or insert in the appropriate section) the following to `docs/TROUBLESHOOTING.md`:

```markdown
### Verifying GCE encoding worker cold-start fix

The encoding worker VM auto-stops when idle to save cost. When a render request
hits a TERMINATED VM, the backend now awaits a readiness gate
(`EncodingWorkerManager.wait_for_worker_ready`) instead of relying on the
deploy-restart-sized 90s retry window.

To verify the fix is working in production after a deploy:

1. Get the primary VM name from Firestore config:
   ```bash
   python3 -c "
   import os; os.environ['GOOGLE_CLOUD_PROJECT']='nomadkaraoke'
   from google.cloud import firestore
   db = firestore.Client(project='nomadkaraoke')
   print(db.collection('config').document('encoding-worker').get().to_dict()['primary_vm'])
   "
   ```

2. Stop it:
   ```bash
   gcloud compute instances stop <vm-name> --zone=us-central1-c --project=nomadkaraoke
   ```

3. Submit a test render through the dashboard or trigger one with a known
   review-complete job.

4. Watch the backend logs for the new readiness-wait sequence:
   ```bash
   gcloud logging read 'resource.labels.service_name="karaoke-backend" AND textPayload=~"Waiting for VM|Worker at .* is healthy|Cold-started VM"' \
     --project=nomadkaraoke --freshness=10m --order=asc \
     --format='value(timestamp,textPayload)'
   ```

   Expected sequence:
   - `Encoding worker unreachable — started VM <name> as fallback`
   - `Waiting for VM <name> (status=STAGING)` (every 30s during VM boot)
   - `VM <name> reached RUNNING`
   - `Waiting for worker /health at ...` (every 30s during service init)
   - `Worker at .../health is healthy`
   - `Cold-started VM <name> is now ready`
   - Job proceeds to encoding without retry exhaustion.

5. If the readiness wait times out (5 min total budget), the log will show
   `Cold-start readiness wait timed out: ...` — investigate VM boot
   (`gcloud compute instances get-serial-port-output ...`) or systemd unit
   status (`gcloud compute ssh ... --command="sudo systemctl status encoding-worker"`).
```

- [ ] **Step 5.3: Commit**

```bash
git add docs/TROUBLESHOOTING.md
git commit -m "docs: runbook for GCE cold-start verification

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Bump version

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 6.1: Bump patch version**

Edit `pyproject.toml` line 3, change:

```toml
version = "0.172.2"
```

to:

```toml
version = "0.172.3"
```

- [ ] **Step 6.2: Commit**

```bash
git add pyproject.toml
git commit -m "chore: bump version to 0.172.3

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Final verification — run the full test suite

- [ ] **Step 7.1: Run the full backend test suite**

```bash
make test 2>&1 | tail -n 200
```

Expected: all tests pass. Pay particular attention to:
- `tests/test_encoding_worker_manager.py` — all green
- `tests/test_encoding_service.py` — all green
- `tests/test_encoding_interface*.py`, `tests/test_local_encoding_service.py`, `tests/test_render_video_worker_integration.py` — all green (these may exercise related paths)

- [ ] **Step 7.2: If any unrelated tests fail, investigate**

Per project CLAUDE.md: "Don't dismiss failures as 'pre-existing' - investigate and fix them." Read the failure, decide if it's caused by these changes (then fix), or genuinely pre-existing on `main` (then surface to user before proceeding).

To check whether a failure is pre-existing, run the same test on `origin/main`:

```bash
git stash
git checkout origin/main -- <failing-test-file>
python -m pytest <failing-test> -v
git checkout HEAD -- <failing-test-file>
git stash pop
```

- [ ] **Step 7.3: No commit needed for this step — just verification.**

---

## Self-Review Notes

- **Spec coverage:** All four spec components (wait_for_worker_ready method, async warmup fallback with readiness gate, log format helper, troubleshooting runbook) covered by Tasks 1–5.
- **Type consistency:** `wait_for_worker_ready` signature matches across spec, manager implementation, test mocks, and warmup_fallback caller.
- **Out-of-scope items in spec stay out-of-scope:** stale GCE cache and pre-warm on review-page open are not added to any task.
- **No placeholders:** every step shows the exact code, command, or text. Test code in Step 1.1 is complete and runnable. Implementation code in Step 1.3 is complete. Test updates in Step 3.1 replace the entire prior class.
- **TDD ordering:** Tasks 1, 2, and 3 follow red→green. Task 4 is a regression guard (post-fix). Tasks 5–7 are non-code changes.
- **Frequent commits:** 6 commits across the work, each with a focused scope.
