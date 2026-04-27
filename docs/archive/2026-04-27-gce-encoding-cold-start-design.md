# GCE Encoding Worker Cold-Start Resilience

**Status:** Approved (2026-04-27)
**Owner:** andrew
**Related:** [#619 warmup fallback](https://github.com/nomadkaraoke/karaoke-gen/pull/619), [#473 deploy-restart resilience](https://github.com/nomadkaraoke/karaoke-gen/pull/473), [#413 deploy interruption resilience](https://github.com/nomadkaraoke/karaoke-gen/pull/413)

## Problem

When a job's render or preview-encode request hits a TERMINATED encoding worker VM, the backend's connection-retry window (~90s, sized for deploy `systemctl restart`) is too short to cover a full GCE cold start (boot + service init = 2–5 minutes). The job fails with `GCE worker connection failed after 8 attempts:` and transitions to FAILED, even though the VM is already on its way up.

### Concrete incident (2026-04-24, job 2c577535)

- 02:19:34 — User submitted lyrics review; backend triggered render-video.
- 02:19:34 — `encoding_worker_manager` detected `encoding-worker-a` was TERMINATED, called `start()`, returned immediately.
- 02:19:36 — Backend POSTed to `http://34.57.78.246:8080/render-video`. VM not listening.
- 02:19:46 → 02:25:14 — 8 retry attempts (5s, 10s, 15s × 6 backoff). All failed.
- 02:25:14 — Job marked FAILED. Logged error message was empty (`{e}` rendered to nothing for the underlying `aiohttp.ClientConnectorError`).
- ~02:26 — VM actually finished cold-starting and became reachable. Too late.

## Root causes

1. **Cold-start path piggybacks on the deploy-restart retry loop.** `EncodingService._warmup_encoding_worker_fallback` is fire-and-forget — it triggers `ensure_primary_running()` and returns. The same 90s retry window is then used regardless of whether the VM was already RUNNING (deploy restart, ~30–90s) or TERMINATED (cold boot, 2–5 min).
2. **Empty error log.** `f"...: {e}"` renders empty when `str(aiohttp.ClientConnectorError)` is empty, hiding the failure mode from operators.

## Out of scope

- **Stale GCE cache (cached=True with no GCS artifacts).** Already handled gracefully by the worker_service via `_retry_<hash>` re-encoding. Root cause is on the GCE worker side; track separately.
- **Pre-warm on review-page open.** Considered as Option C; better UX but doesn't replace the safety net. May follow up later as polish.
- **Encoding worker B (secondary VM).** Stays as-is. Blue-green role swap behavior is unchanged.

## Design

### Approach: separate cold-start from deploy-restart

Add an active readiness wait that runs only when `ensure_primary_running()` reports the VM was actually started (i.e., it had been TERMINATED/STOPPING). For the deploy-restart case (VM already RUNNING/STAGING), the existing 90s retry loop is correct and stays unchanged.

```
Request → POST /encode (or /encode-preview, /render-video)
        ↓
   Connection fails
        ↓
  warmup_fallback() called
        ↓
  ensure_primary_running()
   ├─ started=False (VM was RUNNING/STAGING)
   │     ↓
   │  Return — fall back to existing retry loop (90s window)
   │
   └─ started=True (VM was TERMINATED)
         ↓
      wait_for_worker_ready(timeout=300)
       ├─ poll get_vm_status → wait for RUNNING (max 120s)
       ├─ poll GET /health → wait for 200 (max 180s)
       └─ raise on timeout
         ↓
      Return — main retry loop's next attempt succeeds immediately
```

### Components

**1. `EncodingWorkerManager.wait_for_worker_ready(vm_name: str, *, vm_timeout: float = 120.0, health_timeout: float = 180.0, poll_interval: float = 5.0) -> None`**

New method on the existing manager. Two-phase wait:

- **Phase 1 — VM status**: Poll `get_vm_status(vm_name)` every `poll_interval` until status is `RUNNING`. Raise `TimeoutError` if `vm_timeout` exceeded. Acceptable transitional states (`PROVISIONING`, `STAGING`) are normal during this phase.
- **Phase 2 — Worker health**: Poll `GET {primary_url}/health` every `poll_interval` until 200. Treat connection errors and non-200 responses as "not ready, keep polling". Raise `TimeoutError` if `health_timeout` exceeded.

Returns `None` on success. Logs progress every 30s so operators can see what's happening.

The split timeout is intentional: VM boot is bounded (~60–90s typical), but service init can vary depending on wheel download from GCS. We want to fail loudly if either phase stalls without conflating the two failure modes.

**2. `EncodingService._warmup_encoding_worker_fallback` — make it async-aware for cold starts**

Current signature is sync (`def`); it needs to become `async def` to await the readiness wait. The single caller (`_request_with_retry`) already awaits inside an `async` context, so this is mechanical.

Behavior change:

- `result["started"] is True` (was TERMINATED) → `await self._worker_manager.wait_for_worker_ready(result["vm_name"])`. If readiness wait succeeds, return — the next retry attempt will reach a healthy worker. If it raises `TimeoutError`, log and return; the retry loop will then exhaust and surface a clear "cold start exceeded N seconds" error.
- `result["started"] is False` (already running) → no change. Existing fast-path retry handles deploy restarts.

This is the surgical change that matters: it's the *only* place where we know "we just initiated a cold start, give it time".

**3. Better error logging in `_request_with_retry`**

Replace `{e}` with a small helper that renders empty exception messages as the type name:

```python
def _format_exception(e: Exception) -> str:
    msg = str(e)
    return f"{type(e).__name__}: {msg}" if msg else type(e).__name__
```

Used in both the `attempt < MAX_RETRIES` warning log and the final error log. This is a strict superset of current behavior (still shows the message when it's non-empty) and fixes the diagnostic gap from the 2c577535 incident.

### Data flow / state

No new Firestore fields, no new endpoints, no new dependencies. Pure backend logic change inside two existing files:

- `backend/services/encoding_worker_manager.py` (+1 method, ~50 lines)
- `backend/services/encoding_service.py` (~10-line change in `_warmup_encoding_worker_fallback` + log format helper)

### Error handling

| Scenario | Behavior |
|---|---|
| VM already RUNNING/STAGING, worker unreachable (deploy restart) | Existing 90s retry loop. Unchanged. |
| VM TERMINATED, starts cleanly, worker comes up | Cold-start wait succeeds (~90–180s typical). Job proceeds. Total overhead ~zero on top of intrinsic VM cold-start time. |
| VM TERMINATED, fails to reach RUNNING within 120s | `TimeoutError("VM <name> did not reach RUNNING within 120s")`. Bubbles up through `_warmup_encoding_worker_fallback` → retry loop exhausts → render fails with a real error message. Better than silently retrying on a wedged VM. |
| VM RUNNING but `/health` never returns 200 within 180s | `TimeoutError("Worker at <url> did not become healthy within 180s")`. Same surfacing as above. Operator can SSH and inspect. |
| `wait_for_worker_ready` itself raises an unexpected exception | Caught by the existing `try/except Exception` in `_warmup_encoding_worker_fallback` — logged as warning, retry loop continues. Fail-safe. |

### Concurrency / idempotency

`ensure_primary_running()` is already idempotent (no-op if VM is RUNNING/STAGING). Multiple concurrent jobs hitting a cold VM will each independently call it, find the VM in STAGING, and skip the start. They'll then all enter `wait_for_worker_ready` and poll independently. That's fine — small constant load on the GCE compute API and one HTTP request per poll per job. No coordination needed.

## Testing

### Unit tests (new)

`tests/services/test_encoding_worker_manager.py` (extend existing file):

- `wait_for_worker_ready` returns immediately when VM is already RUNNING and `/health` returns 200 on first poll.
- `wait_for_worker_ready` polls until VM transitions STAGING → RUNNING, then polls `/health` until 200.
- `wait_for_worker_ready` raises `TimeoutError` if VM stays in STAGING past `vm_timeout`.
- `wait_for_worker_ready` raises `TimeoutError` if `/health` keeps returning 503/connection error past `health_timeout`.
- `wait_for_worker_ready` tolerates connection errors during health polling (treats as "keep polling").

Mock the compute client and use `aresponses` (or simple aiohttp test server) for the health endpoint. Use small timeouts (e.g., `vm_timeout=0.5, poll_interval=0.05`) for fast tests.

### Unit tests (extend)

`tests/services/test_encoding_service.py`:

- `_warmup_encoding_worker_fallback` awaits `wait_for_worker_ready` when `started=True`.
- `_warmup_encoding_worker_fallback` does NOT await readiness when `started=False` (deploy restart fast-path).
- `_warmup_encoding_worker_fallback` swallows `TimeoutError` from readiness wait and returns (existing retry loop will surface the error).
- Log format helper renders `aiohttp.ClientConnectorError` with empty message as `ClientConnectorError`.

### Integration test (new, light)

One async test that wires `EncodingService` against a mock manager whose `ensure_primary_running` returns `started=True` and whose `wait_for_worker_ready` resolves after a brief delay. Asserts:
- `_request_with_retry` makes one initial attempt that fails.
- `_warmup_encoding_worker_fallback` is awaited.
- Next attempt succeeds.
- No 8-retry exhaustion happens.

### Production verification

Plan doc to include a manual verification step: after deploy, SSH-stop `encoding-worker-a` (or use the admin endpoint if one exists) to force TERMINATED state, submit a real test job through the dashboard, watch logs to confirm:
- `Encoding worker unreachable — started VM ... as fallback` (existing log)
- `Waiting for VM ... to reach RUNNING` (new)
- `Worker at ... is healthy` (new)
- Job completes without entering retry exhaustion.

This is a one-time manual check after deploy, not an ongoing E2E. Codified as a runbook entry in `docs/TROUBLESHOOTING.md`.

## Versioning

Bump `tool.poetry.version` in `pyproject.toml` (patch).

## Rollout

Ship as a single PR. No feature flag — the only behavior change is "wait longer for known-cold VMs", which is strictly safer than today's "give up at 90s". Deploy follows normal CI path.

## Future work (not this PR)

- **Pre-warm on review page open** (Option C): kick off `ensure_primary_running()` when the user loads the review screen so the VM is already hot by submission time. Pure UX improvement; the cold-start safety net stays in place underneath.
- **Stale GCE worker cache fix**: investigate why the worker reports `cached=True` when no output files exist in GCS. Likely a cache-key vs. artifact lifecycle bug on the worker side.
- **Surface VM readiness to the frontend**: a small status indicator on the review page ("Encoder warming up — this may take 2 minutes if it hasn't been used recently") would set user expectations.
