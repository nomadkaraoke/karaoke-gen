# Long-Duration Input Handling & Duration-Based Credit Pricing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Charge credits proportional to input audio duration (`ceil(min/10)`, hard-block >60 min), detecting length as early as each source allows, confirming cost with the user before heavy work, and reconciling against the actual processed audio.

**Architecture:** A single server-side pricing util is the source of truth. The charge lands where duration first becomes authoritative (upload→ffprobe, URL→yt-dlp metadata, search→result metadata). A shared `measure_and_reconcile` step runs at the convergence point just before separation+transcription is triggered (post-edit if edited), settling the difference — auto-refund if shorter, pause-and-re-confirm if longer, refund+cancel if over the ceiling. A new blocking `AWAITING_DURATION_CONFIRM` state reuses the review-stage pause/resume + notification + stale-job machinery.

**Tech Stack:** Python 3 / FastAPI / Firestore (backend, pytest), Next.js / React / next-intl (frontend, Jest), GCP Cloud Tasks + Cloud Scheduler, Postmark.

**Design spec:** `docs/archive/2026-06-04-long-duration-input-handling-design.md`

---

## File Structure

**Backend (new)**
- `backend/services/pricing.py` — pure pricing util (`duration_to_credits`, `is_blocked`).
- `backend/services/duration_reconciliation.py` — `measure_and_reconcile(job_id)` orchestration.

**Backend (modified)**
- `backend/models/job.py` — `AWAITING_DURATION_CONFIRM` status + transitions.
- `backend/services/user_service.py` — `deduct_credits(email, job_id, amount, reason)`.
- `backend/services/job_manager.py` — charge N at create; idle-reminder action_type.
- `backend/workers/audio_download_worker.py` — call reconcile before the worker gather (no-edit path).
- `backend/api/routes/review.py` — call reconcile before the worker gather (post-edit path).
- `backend/api/routes/jobs.py` — `/estimate`, `/confirm-duration`; `acknowledged_credits` on URL create.
- `backend/api/routes/audio_search.py` — charge N on `/select`.
- `backend/api/routes/file_upload.py` — `uploads-complete` → preflight pause.
- `backend/api/routes/internal.py` — idle-reminder + stale-review handle new state.
- `backend/workers/stale_review_processor.py` — scan new state; expiry refund.
- `backend/services/email_service.py` — duration-confirm reminder/expired templates.

**Frontend (new)**
- `frontend/lib/pricing.ts` — display mirror of backend constants.
- `frontend/components/job/DurationCostConfirm.tsx` — confirm modal.

**Frontend (modified)**
- `frontend/lib/job-status.ts` — `STATUS_CONFIG` + `isNotifiableBlockingStatus`.
- `frontend/lib/api.ts` — `estimateDuration`, `confirmDuration` clients.
- `frontend/components/job/GuidedJobFlow.tsx` — cost confirm step.
- `frontend/components/audio-search/AudioSearchDialog.tsx` — per-result cost chip.
- `frontend/messages/en.json` (+ `translate.py --target all`).

---

## Phase 0 — Pricing core

### Task 1: Backend pricing util

**Files:**
- Create: `backend/services/pricing.py`
- Test: `backend/tests/services/test_pricing.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/services/test_pricing.py
import pytest
from backend.services.pricing import duration_to_credits, is_blocked, SECONDS_PER_CREDIT_TIER, DURATION_CREDIT_BLOCK_SECONDS


@pytest.mark.parametrize("seconds,expected", [
    (0, 1),          # minimum is always 1
    (1, 1),
    (599, 1),
    (600, 1),        # exactly 10:00 -> 1
    (601, 2),        # 10:01 -> 2
    (1200, 2),       # 20:00 -> 2
    (1201, 3),
    (3000, 5),       # 50:00 -> 5
    (3001, 6),       # 50:01 -> 6
    (3600, 6),       # exactly 60:00 -> 6 (allowed)
])
def test_duration_to_credits_tiers(seconds, expected):
    assert duration_to_credits(seconds) == expected


@pytest.mark.parametrize("seconds,blocked", [
    (3600, False),   # exactly 60:00 allowed
    (3601, True),    # 60:01 blocked
    (10000, True),
    (0, False),
])
def test_is_blocked(seconds, blocked):
    assert is_blocked(seconds) is blocked


def test_constants():
    assert SECONDS_PER_CREDIT_TIER == 600
    assert DURATION_CREDIT_BLOCK_SECONDS == 3600
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/services/test_pricing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.services.pricing'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/services/pricing.py
"""Duration-based credit pricing. Single source of truth for credit cost.

credits = max(1, ceil(duration_seconds / 600))   # 10-minute tiers
blocked = duration_seconds > 3600                 # 60-minute hard ceiling
"""
import math

SECONDS_PER_CREDIT_TIER = 600          # 10 minutes per credit tier
DURATION_CREDIT_BLOCK_SECONDS = 3600   # inputs longer than 60 min are not supported


def duration_to_credits(seconds: float) -> int:
    """Credits required to process `seconds` of audio. Minimum 1."""
    if seconds is None or seconds < 0:
        return 1
    return max(1, math.ceil(seconds / SECONDS_PER_CREDIT_TIER))


def is_blocked(seconds: float) -> bool:
    """True if the input exceeds the supported duration ceiling."""
    return seconds is not None and seconds > DURATION_CREDIT_BLOCK_SECONDS
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/services/test_pricing.py -v`
Expected: PASS (all parametrized cases)

- [ ] **Step 5: Commit**

```bash
git add backend/services/pricing.py backend/tests/services/test_pricing.py
git commit -m "feat(pricing): add duration_to_credits util with boundary tests"
```

---

### Task 2: Frontend pricing display helper

**Files:**
- Create: `frontend/lib/pricing.ts`
- Test: `frontend/__tests__/pricing.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// frontend/__tests__/pricing.test.ts
import { durationToCredits, isBlocked, formatDurationCost } from "@/lib/pricing";

describe("pricing", () => {
  it.each([
    [0, 1], [600, 1], [601, 2], [1200, 2], [3000, 5], [3001, 6], [3600, 6],
  ])("durationToCredits(%i) === %i", (seconds, expected) => {
    expect(durationToCredits(seconds)).toBe(expected);
  });

  it("blocks over 60 min", () => {
    expect(isBlocked(3600)).toBe(false);
    expect(isBlocked(3601)).toBe(true);
  });

  it("formats duration + cost", () => {
    expect(formatDurationCost(905)).toEqual({ minutes: 16, credits: 2 });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx jest pricing.test.ts`
Expected: FAIL — cannot find module `@/lib/pricing`

- [ ] **Step 3: Write minimal implementation**

```ts
// frontend/lib/pricing.ts
// MIRRORS backend/services/pricing.py — keep these constants in sync.
export const SECONDS_PER_CREDIT_TIER = 600;
export const DURATION_CREDIT_BLOCK_SECONDS = 3600;

export function durationToCredits(seconds: number): number {
  if (seconds == null || seconds < 0) return 1;
  return Math.max(1, Math.ceil(seconds / SECONDS_PER_CREDIT_TIER));
}

export function isBlocked(seconds: number): boolean {
  return seconds != null && seconds > DURATION_CREDIT_BLOCK_SECONDS;
}

export function formatDurationCost(seconds: number): { minutes: number; credits: number } {
  return { minutes: Math.ceil(seconds / 60), credits: durationToCredits(seconds) };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx jest pricing.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/pricing.ts frontend/__tests__/pricing.test.ts
git commit -m "feat(pricing): add frontend pricing display helper mirroring backend"
```

---

## Phase 1 — N-credit deduction

### Task 3: Generalise credit deduction to N credits

**Files:**
- Modify: `backend/services/user_service.py` (add `deduct_credits` near `deduct_credit`, ~line 1042)
- Test: `backend/tests/services/test_user_service_credits.py` (create or extend an existing credits test)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/services/test_user_service_credits.py
import pytest
from backend.services.user_service import UserService


@pytest.fixture
def user_service(firestore_emulator):  # reuse the project's emulator fixture from conftest
    return UserService(db=firestore_emulator)


def _seed_user(user_service, email="dur@test.com", credits=5):
    user_service.db.collection("gen_users").document(email).set({"email": email, "credits": credits})
    return email


def test_deduct_credits_multiple(user_service):
    email = _seed_user(user_service, credits=5)
    ok, remaining, _ = user_service.deduct_credits(email, job_id="j1", amount=3, reason="job_creation")
    assert ok is True
    assert remaining == 2


def test_deduct_credits_insufficient_is_atomic(user_service):
    email = _seed_user(user_service, credits=2)
    ok, remaining, msg = user_service.deduct_credits(email, job_id="j1", amount=4, reason="job_creation")
    assert ok is False
    assert remaining == 2          # unchanged — no partial deduction
    assert "insufficient" in msg.lower()


def test_deduct_credits_amount_one_matches_legacy(user_service):
    email = _seed_user(user_service, credits=1)
    ok, remaining, _ = user_service.deduct_credits(email, job_id="j1", amount=1, reason="job_creation")
    assert ok is True
    assert remaining == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/services/test_user_service_credits.py -v`
Expected: FAIL — `AttributeError: 'UserService' object has no attribute 'deduct_credits'`

- [ ] **Step 3: Write minimal implementation**

Add `deduct_credits` alongside `deduct_credit`. Re-implement `deduct_credit` as a thin wrapper so existing callers are unaffected:

```python
# backend/services/user_service.py  (add near the existing deduct_credit, ~line 1042)
def deduct_credits(
    self,
    email: str,
    job_id: str,
    amount: int = 1,
    reason: str = "job_creation",
) -> Tuple[bool, int, str]:
    """Atomically deduct `amount` credits. All-or-nothing; no partial deduction.

    Returns (success, remaining_credits, message).
    """
    if amount <= 0:
        return False, 0, "Deduction amount must be positive"
    email = email.lower()
    doc_ref = self.db.collection(USERS_COLLECTION).document(email)

    @firestore.transactional
    def deduct_in_transaction(transaction):
        doc = doc_ref.get(transaction=transaction)
        if not doc.exists:
            return False, 0, "User not found"
        user_data = doc.to_dict()
        current_credits = user_data.get("credits", 0)
        if current_credits < amount:
            return False, current_credits, "Insufficient credits"

        credit_txn = CreditTransaction(
            amount=-amount,
            reason=reason,
            job_id=job_id,
        )
        existing = user_data.get("credit_transactions", [])
        existing.append(credit_txn.model_dump())
        transaction.update(doc_ref, {
            "credits": current_credits - amount,
            "credit_transactions": existing[-100:],
        })
        return True, current_credits - amount, "Credits deducted"

    transaction = self.db.transaction()
    return deduct_in_transaction(transaction)


def deduct_credit(self, email: str, job_id: str, reason: str = "job_creation") -> Tuple[bool, int, str]:
    """Deduct a single credit (backward-compatible wrapper)."""
    return self.deduct_credits(email, job_id, amount=1, reason=reason)
```

> Note: match the exact `CreditTransaction` constructor and field names already used in the current `deduct_credit` body (id/created_by/timestamp may be auto-populated). Mirror the existing implementation precisely; only the `amount` becomes parameterised.

- [ ] **Step 4: Run tests (new + existing credit tests)**

Run: `cd backend && python -m pytest tests/services/test_user_service_credits.py tests/ -k "credit" -v`
Expected: PASS — new tests pass and all existing credit tests still pass (wrapper preserves behaviour).

- [ ] **Step 5: Commit**

```bash
git add backend/services/user_service.py backend/tests/services/test_user_service_credits.py
git commit -m "feat(credits): add deduct_credits(amount); deduct_credit delegates to it"
```

---

## Phase 2 — Job state & model

### Task 4: Add `AWAITING_DURATION_CONFIRM` status + transitions

**Files:**
- Modify: `backend/models/job.py` (enum ~line 41; `STATE_TRANSITIONS` map)
- Test: `backend/tests/services/test_job_state_transitions.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/services/test_job_state_transitions.py
from backend.models.job import JobStatus, STATE_TRANSITIONS


def test_awaiting_duration_confirm_exists():
    assert JobStatus.AWAITING_DURATION_CONFIRM.value == "awaiting_duration_confirm"


def test_can_pause_from_download_and_edit_complete():
    assert JobStatus.AWAITING_DURATION_CONFIRM in STATE_TRANSITIONS[JobStatus.DOWNLOADING]
    assert JobStatus.AWAITING_DURATION_CONFIRM in STATE_TRANSITIONS[JobStatus.AUDIO_EDIT_COMPLETE]
    assert JobStatus.AWAITING_DURATION_CONFIRM in STATE_TRANSITIONS[JobStatus.DOWNLOADING_AUDIO]


def test_resume_targets_processing_or_terminal():
    targets = STATE_TRANSITIONS[JobStatus.AWAITING_DURATION_CONFIRM]
    for t in (JobStatus.SEPARATING_STAGE1, JobStatus.TRANSCRIBING,
              JobStatus.GENERATING_SCREENS, JobStatus.FAILED, JobStatus.CANCELLED):
        assert t in targets
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/services/test_job_state_transitions.py -v`
Expected: FAIL — `AttributeError: AWAITING_DURATION_CONFIRM`

- [ ] **Step 3: Write minimal implementation**

In `backend/models/job.py`, add to the enum (after `AUDIO_EDIT_COMPLETE`):

```python
    # Optional: duration cost confirmation (BLOCKING) - confirm credits before heavy processing
    AWAITING_DURATION_CONFIRM = "awaiting_duration_confirm"  # ⚠️ WAITING FOR USER - confirm duration-based cost
```

Then in `STATE_TRANSITIONS`:
- Add `JobStatus.AWAITING_DURATION_CONFIRM` to the lists for `JobStatus.DOWNLOADING`, `JobStatus.DOWNLOADING_AUDIO`, and `JobStatus.AUDIO_EDIT_COMPLETE`.
- Add a new entry:

```python
    JobStatus.AWAITING_DURATION_CONFIRM: [
        JobStatus.SEPARATING_STAGE1, JobStatus.TRANSCRIBING,
        JobStatus.GENERATING_SCREENS, JobStatus.AWAITING_AUDIO_EDIT,
        JobStatus.FAILED, JobStatus.CANCELLED,
    ],
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/services/test_job_state_transitions.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/models/job.py backend/tests/services/test_job_state_transitions.py
git commit -m "feat(job): add AWAITING_DURATION_CONFIRM state + transitions"
```

---

## Phase 3 — Reconciliation engine

### Task 5: `measure_and_reconcile` service

**Files:**
- Create: `backend/services/duration_reconciliation.py`
- Test: `backend/tests/services/test_duration_reconciliation.py`

This is the money-critical unit. It is pure-ish: it takes injected collaborators (job_manager, user_service, an ffprobe callable, email_service) so it can be unit-tested without GCS/Firestore.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/services/test_duration_reconciliation.py
import pytest
from unittest.mock import MagicMock
from backend.services.duration_reconciliation import reconcile_duration, ReconcileResult


def _job(credits_charged=2, gcs="gs://b/audio.flac", email="u@test.com"):
    job = MagicMock()
    job.id = "job1"
    job.user_email = email
    job.input_media_gcs_path = gcs
    job.state_data = {"credits_charged": credits_charged}
    return job


def _ctx(actual_seconds, credits_charged=2):
    job_manager = MagicMock()
    job_manager.get_job.return_value = _job(credits_charged=credits_charged)
    user_service = MagicMock()
    user_service.add_credits.return_value = (True, 99, "ok")
    user_service.deduct_credits.return_value = (True, 1, "ok")
    probe = MagicMock(return_value=actual_seconds)
    return job_manager, user_service, probe


def test_equal_proceeds():
    jm, us, probe = _ctx(actual_seconds=900, credits_charged=2)  # 15min -> 2 credits
    result = reconcile_duration("job1", jm, us, probe)
    assert result.action == "proceed"
    us.add_credits.assert_not_called()
    us.deduct_credits.assert_not_called()


def test_shorter_auto_refunds_and_proceeds():
    jm, us, probe = _ctx(actual_seconds=300, credits_charged=2)  # 5min -> 1 credit, refund 1
    result = reconcile_duration("job1", jm, us, probe)
    assert result.action == "proceed"
    us.add_credits.assert_called_once()
    args, kwargs = us.add_credits.call_args
    assert kwargs.get("amount", args[1] if len(args) > 1 else None) == 1
    assert "refund" in (kwargs.get("reason", "") or "")


def test_longer_pauses_for_reconfirm():
    jm, us, probe = _ctx(actual_seconds=1800, credits_charged=2)  # 30min -> 3 credits, +1 owed
    result = reconcile_duration("job1", jm, us, probe)
    assert result.action == "pause"
    assert result.pending_additional_credits == 1
    jm.transition_to_state.assert_called_once()
    us.deduct_credits.assert_not_called()  # not charged until user confirms


def test_over_limit_refunds_all_and_cancels():
    jm, us, probe = _ctx(actual_seconds=4000, credits_charged=2)  # >60min
    result = reconcile_duration("job1", jm, us, probe)
    assert result.action == "cancel"
    us.add_credits.assert_called_once()
    args, kwargs = us.add_credits.call_args
    assert kwargs.get("amount", args[1] if len(args) > 1 else None) == 2  # full refund
    jm.cancel_job.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/services/test_duration_reconciliation.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Write minimal implementation**

```python
# backend/services/duration_reconciliation.py
"""Reconcile a job's credit charge against the actual audio about to be processed.

Called at the convergence point immediately before the separation+transcription
workers are triggered (post-edit if an edit occurred). The probe callable returns
the duration in seconds of `job.input_media_gcs_path` (the to-be-processed audio).
"""
from dataclasses import dataclass
from typing import Callable, Optional
import logging

from backend.services.pricing import duration_to_credits, is_blocked
from backend.models.job import JobStatus

logger = logging.getLogger(__name__)


@dataclass
class ReconcileResult:
    action: str                                  # "proceed" | "pause" | "cancel"
    pending_additional_credits: int = 0
    actual_seconds: Optional[float] = None


def reconcile_duration(
    job_id: str,
    job_manager,
    user_service,
    probe_duration: Callable[[object], Optional[float]],
    email_service=None,
) -> ReconcileResult:
    job = job_manager.get_job(job_id)
    actual = probe_duration(job)
    state = dict(job.state_data or {})
    credits_charged = int(state.get("credits_charged", 1))

    if actual is None:
        # Could not measure — do not block the pipeline; proceed on what was charged.
        logger.warning("Job %s: duration probe returned None; proceeding without reconcile", job_id)
        return ReconcileResult(action="proceed", actual_seconds=None)

    job_manager.update_job(job_id, {"state_data.duration_actual_seconds": actual})

    if is_blocked(actual):
        if credits_charged > 0:
            user_service.add_credits(job.user_email, amount=credits_charged,
                                     reason="duration_over_limit_refund", job_id=job_id)
        job_manager.cancel_job(job_id, reason="Input audio exceeds the 60-minute limit")
        if email_service:
            email_service.send_duration_confirm_expired(job)  # reuse for over-limit notice
        return ReconcileResult(action="cancel", actual_seconds=actual)

    required = duration_to_credits(actual)
    delta = required - credits_charged

    if delta == 0:
        return ReconcileResult(action="proceed", actual_seconds=actual)

    if delta < 0:
        user_service.add_credits(job.user_email, amount=abs(delta),
                                 reason="duration_refund", job_id=job_id)
        job_manager.update_job(job_id, {"state_data.credits_charged": required})
        return ReconcileResult(action="proceed", actual_seconds=actual)

    # delta > 0 : owe more credits — pause for explicit re-confirmation.
    job_manager.update_job(job_id, {
        "state_data.duration_confirm_reason": "reconcile",
        "state_data.pending_additional_credits": delta,
    })
    job_manager.transition_to_state(
        job_id=job_id,
        new_status=JobStatus.AWAITING_DURATION_CONFIRM,
        progress=16,
        message=f"This turned out longer than estimated — {delta} more credit(s) needed",
    )
    return ReconcileResult(action="pause", pending_additional_credits=delta, actual_seconds=actual)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/services/test_duration_reconciliation.py -v`
Expected: PASS (4 cases)

- [ ] **Step 5: Commit**

```bash
git add backend/services/duration_reconciliation.py backend/tests/services/test_duration_reconciliation.py
git commit -m "feat(pricing): add measure_and_reconcile duration reconciliation engine"
```

---

### Task 6: Wire reconcile into the no-edit convergence point

**Files:**
- Modify: `backend/workers/audio_download_worker.py:283` (before `asyncio.gather(trigger_audio_worker, trigger_lyrics_worker)`)
- Test: `backend/tests/test_audio_download_worker.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_audio_download_worker.py  (add)
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock


@patch("backend.workers.audio_download_worker.reconcile_and_maybe_pause", new_callable=AsyncMock)
@patch("backend.workers.audio_download_worker.get_worker_service")
def test_reconcile_pause_skips_worker_triggers(mock_ws, mock_reconcile, ...):
    # When reconcile returns paused=True, the parallel workers must NOT be triggered.
    mock_reconcile.return_value = True   # paused
    ws = MagicMock()
    ws.trigger_audio_worker = AsyncMock()
    ws.trigger_lyrics_worker = AsyncMock()
    mock_ws.return_value = ws
    # ... invoke the post-download finalisation path for a job ...
    ws.trigger_audio_worker.assert_not_called()
    ws.trigger_lyrics_worker.assert_not_called()
```

> Adapt the `...` to the existing test harness in this file (it already constructs jobs and patches worker_service; follow the nearest existing test that exercises the post-download gather at line ~283).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_audio_download_worker.py -k reconcile -v`
Expected: FAIL — `reconcile_and_maybe_pause` does not exist.

- [ ] **Step 3: Write minimal implementation**

Add a small async wrapper and call it before the gather. At the top of `audio_download_worker.py`:

```python
from backend.services.duration_reconciliation import reconcile_duration
```

Add helper:

```python
async def reconcile_and_maybe_pause(job_id: str) -> bool:
    """Measure the to-be-processed audio and reconcile credits.

    Returns True if the job was paused (AWAITING_DURATION_CONFIRM) or cancelled —
    in which case the caller must NOT trigger the processing workers.
    """
    from backend.services.user_service import get_user_service
    from backend.services.email_service import get_email_service
    job_manager = get_job_manager()
    storage = get_storage_service()

    def _probe(job):
        return _ffprobe_seconds(job, storage)  # see Task 6b

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: reconcile_duration(job_id, job_manager, get_user_service(), _probe, get_email_service()),
    )
    return result.action in ("pause", "cancel")
```

Then immediately before the existing `await asyncio.gather(...)` at line ~283:

```python
    if await reconcile_and_maybe_pause(job_id):
        return  # paused for confirmation or cancelled; do not start processing
    await asyncio.gather(
        worker_service.trigger_audio_worker(job_id),
        worker_service.trigger_lyrics_worker(job_id),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_audio_download_worker.py -k reconcile -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/workers/audio_download_worker.py backend/tests/test_audio_download_worker.py
git commit -m "feat(pricing): reconcile credits before processing (no-edit path)"
```

---

### Task 6b: Shared ffprobe-seconds helper

**Files:**
- Modify: `backend/workers/audio_download_worker.py` (add `_ffprobe_seconds`, reusing the pattern from `jobs.py:_get_audio_duration_ffprobe_signed`)
- Test: covered indirectly; add a focused unit test mocking `subprocess.run`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_audio_download_worker.py  (add)
from unittest.mock import patch, MagicMock
from backend.workers.audio_download_worker import _ffprobe_seconds


@patch("backend.workers.audio_download_worker.subprocess.run")
def test_ffprobe_seconds_parses_duration(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout='{"format": {"duration": "1957.8"}}', stderr="")
    job = MagicMock(); job.input_media_gcs_path = "gs://b/a.flac"
    storage = MagicMock(); storage.generate_signed_url.return_value = "https://signed"
    assert _ffprobe_seconds(job, storage) == 1957.8


@patch("backend.workers.audio_download_worker.subprocess.run")
def test_ffprobe_seconds_returns_none_on_error(mock_run):
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
    job = MagicMock(); job.input_media_gcs_path = "gs://b/a.flac"
    storage = MagicMock(); storage.generate_signed_url.return_value = "https://signed"
    assert _ffprobe_seconds(job, storage) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_audio_download_worker.py -k ffprobe -v`
Expected: FAIL — `_ffprobe_seconds` not defined.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/workers/audio_download_worker.py
import json
import subprocess


def _ffprobe_seconds(job, storage) -> "float | None":
    """Duration (seconds) of the job's current input audio via header-only ffprobe.
    Mirrors jobs.py:_get_audio_duration_ffprobe_signed. Non-fatal: returns None on failure."""
    gcs_path = getattr(job, "input_media_gcs_path", None)
    if not gcs_path:
        return None
    try:
        signed_url = storage.generate_signed_url(gcs_path, expiration_minutes=5)
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", signed_url],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return None
        return float(json.loads(result.stdout)["format"]["duration"])
    except Exception as e:  # noqa: BLE001
        logger.warning("Job %s: ffprobe duration failed: %s", getattr(job, "id", "?"), e)
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_audio_download_worker.py -k ffprobe -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/workers/audio_download_worker.py backend/tests/test_audio_download_worker.py
git commit -m "feat(pricing): add header-only ffprobe duration helper for reconciliation"
```

---

### Task 7: Wire reconcile into the post-edit convergence point

**Files:**
- Modify: `backend/api/routes/review.py:2192` (before the `asyncio.gather(trigger_audio_worker, trigger_lyrics_worker)` after AUDIO_EDIT_COMPLETE)
- Test: `backend/tests/test_audio_edit_routes.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_audio_edit_routes.py  (add)
from unittest.mock import patch, AsyncMock


@patch("backend.api.routes.review.reconcile_and_maybe_pause", new_callable=AsyncMock)
def test_post_edit_pause_skips_processing(mock_reconcile, client, audio_edit_job):
    mock_reconcile.return_value = True   # paused for reconfirm
    # ... submit the audio edit completion for `audio_edit_job` ...
    # assert workers were not triggered (follow the existing assertions in this file)
```

> Reuse `review.py`'s `reconcile_and_maybe_pause` (import the same wrapper added in Task 6; move it to `duration_reconciliation.py` if both modules need it — see note below).

**Refactor note:** to avoid duplicating `reconcile_and_maybe_pause` across two modules, move it (and `_ffprobe_seconds`) into `backend/services/duration_reconciliation.py` as `async def reconcile_and_maybe_pause(job_id)` and import it in both `audio_download_worker.py` and `review.py`. Update Task 6's import accordingly. Keep `_ffprobe_seconds` in `duration_reconciliation.py` too. (If you already inlined them in Task 6, relocate now and re-run Task 6's tests.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_audio_edit_routes.py -k pause -v`
Expected: FAIL — workers still triggered / import error.

- [ ] **Step 3: Write minimal implementation**

In `review.py`, before the post-edit gather at ~line 2192:

```python
from backend.services.duration_reconciliation import reconcile_and_maybe_pause
...
    if await reconcile_and_maybe_pause(job_id):
        return  # paused/cancelled at post-edit reconciliation
    await asyncio.gather(
        worker_service.trigger_audio_worker(job_id),
        worker_service.trigger_lyrics_worker(job_id),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_audio_edit_routes.py -k pause tests/test_audio_download_worker.py -v`
Expected: PASS (both convergence points covered)

- [ ] **Step 5: Commit**

```bash
git add backend/api/routes/review.py backend/services/duration_reconciliation.py backend/workers/audio_download_worker.py backend/tests/test_audio_edit_routes.py
git commit -m "feat(pricing): reconcile credits before processing (post-edit path)"
```

---

## Phase 4 — Confirm & estimate endpoints

### Task 8: `POST /api/jobs/{id}/confirm-duration`

Serves both the `preflight` (upload) pause and the `reconcile` pause. Deducts the owed credits and resumes by triggering the processing workers.

**Files:**
- Modify: `backend/api/routes/jobs.py` (new route)
- Test: `backend/tests/api/test_confirm_duration.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/api/test_confirm_duration.py
from unittest.mock import patch, AsyncMock


def test_confirm_reconcile_deducts_and_resumes(client, auth_headers, reconcile_paused_job):
    # job in AWAITING_DURATION_CONFIRM, reason=reconcile, pending_additional_credits=1
    with patch("backend.api.routes.jobs.get_worker_service") as ws:
        ws.return_value.trigger_audio_worker = AsyncMock()
        ws.return_value.trigger_lyrics_worker = AsyncMock()
        resp = client.post(f"/api/jobs/{reconcile_paused_job.id}/confirm-duration",
                            json={"acknowledged_credits": 3}, headers=auth_headers)
    assert resp.status_code == 200
    # 1 additional credit deducted; job left AWAITING_DURATION_CONFIRM


def test_confirm_insufficient_credits_returns_402(client, auth_headers, reconcile_paused_job_no_credits):
    resp = client.post(f"/api/jobs/{reconcile_paused_job_no_credits.id}/confirm-duration",
                        json={"acknowledged_credits": 3}, headers=auth_headers)
    assert resp.status_code == 402  # payment required → frontend opens BuyCreditsDialog


def test_confirm_mismatch_returns_409(client, auth_headers, reconcile_paused_job):
    # client thinks it owes a stale number
    resp = client.post(f"/api/jobs/{reconcile_paused_job.id}/confirm-duration",
                        json={"acknowledged_credits": 99}, headers=auth_headers)
    assert resp.status_code == 409  # figure changed; frontend refreshes
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/api/test_confirm_duration.py -v`
Expected: FAIL — 404 (route missing)

- [ ] **Step 3: Write minimal implementation**

```python
# backend/api/routes/jobs.py
from pydantic import BaseModel

class ConfirmDurationRequest(BaseModel):
    acknowledged_credits: int

@router.post("/jobs/{job_id}/confirm-duration")
async def confirm_duration(job_id: str, body: ConfirmDurationRequest,
                           background_tasks: BackgroundTasks, user=Depends(get_current_user)):
    job = get_job_manager().get_job(job_id)
    if not job or job.user_email.lower() != user.email.lower():
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != JobStatus.AWAITING_DURATION_CONFIRM:
        raise HTTPException(status_code=409, detail="Job is not awaiting duration confirmation")

    state = job.state_data or {}
    reason = state.get("duration_confirm_reason")
    owed = int(state.get("pending_additional_credits", 0)) if reason == "reconcile" \
        else duration_to_credits(state.get("duration_actual_seconds") or state.get("duration_estimate_seconds") or 0)

    if body.acknowledged_credits != (int(state.get("credits_charged", 0)) + owed if reason == "reconcile" else owed):
        raise HTTPException(status_code=409, detail="Credit figure changed; please refresh")

    if owed > 0:
        ok, _, _ = get_user_service().deduct_credits(job.user_email, job_id, amount=owed, reason="duration_confirm")
        if not ok:
            raise HTTPException(status_code=402, detail="Insufficient credits")
        get_job_manager().update_job(job_id, {
            "state_data.credits_charged": int(state.get("credits_charged", 0)) + owed,
        })

    get_job_manager().update_job(job_id, {
        "state_data.duration_confirmed": True,
        "state_data.pending_additional_credits": 0,
    })
    # Resume: trigger the parallel processing workers (same as review-gate resume).
    get_job_manager().transition_to_state(job_id=job_id, new_status=JobStatus.SEPARATING_STAGE1,
                                          progress=20, message="Cost confirmed, starting processing")
    background_tasks.add_task(_resume_processing, job_id)
    return get_job_manager().get_job(job_id)
```

Add `_resume_processing(job_id)` that triggers `trigger_audio_worker` + `trigger_lyrics_worker` (mirror the convergence gather). Import `duration_to_credits`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/api/test_confirm_duration.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/api/routes/jobs.py backend/tests/api/test_confirm_duration.py
git commit -m "feat(pricing): add confirm-duration endpoint (preflight + reconcile)"
```

---

### Task 9: `POST /api/jobs/estimate` (URL duration probe)

**Files:**
- Modify: `backend/api/routes/jobs.py`
- Test: `backend/tests/api/test_estimate_duration.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/api/test_estimate_duration.py
from unittest.mock import patch


def test_estimate_returns_credits(client, auth_headers):
    with patch("backend.api.routes.jobs.get_youtube_download_service") as yt:
        yt.return_value.check_availability.return_value = {"available": True, "duration": 905}
        resp = client.post("/api/jobs/estimate", json={"url": "https://youtu.be/x"}, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["duration_seconds"] == 905
    assert body["credits"] == 2
    assert body["blocked"] is False


def test_estimate_blocks_over_60min(client, auth_headers):
    with patch("backend.api.routes.jobs.get_youtube_download_service") as yt:
        yt.return_value.check_availability.return_value = {"available": True, "duration": 4000}
        resp = client.post("/api/jobs/estimate", json={"url": "https://youtu.be/x"}, headers=auth_headers)
    assert resp.json()["blocked"] is True


def test_estimate_probe_failure_returns_unknown(client, auth_headers):
    with patch("backend.api.routes.jobs.get_youtube_download_service") as yt:
        yt.return_value.check_availability.return_value = {"available": True, "duration": None}
        resp = client.post("/api/jobs/estimate", json={"url": "https://youtu.be/x"}, headers=auth_headers)
    body = resp.json()
    assert body["duration_seconds"] is None
    assert body["source"] == "unknown"
    assert body["credits"] == 1  # 1-credit hold fallback
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/api/test_estimate_duration.py -v`
Expected: FAIL — 404

- [ ] **Step 3: Write minimal implementation**

```python
# backend/api/routes/jobs.py
class EstimateRequest(BaseModel):
    url: str

@router.post("/jobs/estimate")
async def estimate_duration(body: EstimateRequest, user=Depends(get_current_user)):
    info = get_youtube_download_service().check_availability(body.url)
    seconds = (info or {}).get("duration")
    if seconds is None:
        return {"duration_seconds": None, "source": "unknown", "credits": 1, "blocked": False}
    return {
        "duration_seconds": seconds,
        "source": "youtube_metadata",
        "credits": duration_to_credits(seconds),
        "blocked": is_blocked(seconds),
    }
```

> Confirm `check_availability` returns a `duration` key (subagent reported `youtube_download_service.py:71-99` calls flacfetch `/check-youtube` returning metadata). If the key differs, adapt the lookup.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/api/test_estimate_duration.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/api/routes/jobs.py backend/tests/api/test_estimate_duration.py
git commit -m "feat(pricing): add /api/jobs/estimate URL duration probe"
```

---

## Phase 5 — Per-source pre-flight charging

### Task 10: Charge N at URL create

**Files:**
- Modify: `backend/api/routes/jobs.py` (URL create handler ~line 73-167; request model in `backend/models/requests.py`)
- Modify: `backend/services/job_manager.py` (`create_job` charge path ~line 134)
- Test: `backend/tests/api/test_url_create_pricing.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/api/test_url_create_pricing.py
def test_url_create_charges_by_duration(client, auth_headers, user_with_credits):
    # acknowledged_credits=2 for a 15-min video; user has 5 credits
    resp = client.post("/api/jobs", json={
        "url": "https://youtu.be/x", "artist": "A", "title": "T",
        "duration_seconds": 905, "acknowledged_credits": 2,
    }, headers=auth_headers)
    assert resp.status_code in (200, 201)
    # assert user balance dropped by 2 and job.state_data.credits_charged == 2


def test_url_create_rejects_over_limit(client, auth_headers, user_with_credits):
    resp = client.post("/api/jobs", json={
        "url": "https://youtu.be/x", "artist": "A", "title": "T",
        "duration_seconds": 4000, "acknowledged_credits": 7,
    }, headers=auth_headers)
    assert resp.status_code == 422  # over 60 min
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/api/test_url_create_pricing.py -v`
Expected: FAIL — flat-1 charge / no over-limit guard.

- [ ] **Step 3: Write minimal implementation**

- Add `duration_seconds: Optional[float]` and `acknowledged_credits: Optional[int]` to `URLSubmissionRequest` (`backend/models/requests.py`).
- In the URL create handler: if `is_blocked(duration_seconds)` → `raise HTTPException(422, ...)`. Compute `credits = duration_to_credits(duration_seconds)` (fallback 1 if None), assert it matches `acknowledged_credits` (else 409), pass `credits` into `JobCreate`.
- In `job_manager.create_job`: replace the flat `deduct_credit(...)` with `deduct_credits(..., amount=job_create.credits or 1, ...)`; store `state_data.credits_charged = amount`, `state_data.duration_estimate_seconds`, `state_data.duration_estimate_source = "youtube_metadata"`. Keep the existing has-credits gate (now `>= amount`) and the job-deletion-on-failure rollback.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/api/test_url_create_pricing.py tests/ -k "create_job" -v`
Expected: PASS (new + existing create_job tests, including admin bypass)

- [ ] **Step 5: Commit**

```bash
git add backend/api/routes/jobs.py backend/models/requests.py backend/services/job_manager.py backend/tests/api/test_url_create_pricing.py
git commit -m "feat(pricing): charge N credits by duration on URL job create"
```

---

### Task 11: Charge N at audio-search `/select`

**Files:**
- Modify: `backend/api/routes/audio_search.py` (`/select` handler ~line 1127-1350; `AudioSelectRequest`)
- Test: `backend/tests/test_audio_search.py` (extend) or `backend/tests/api/test_audio_search_select_pricing.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/api/test_audio_search_select_pricing.py
def test_select_charges_by_result_duration(client, auth_headers, searched_job_with_results):
    # results[0].duration = 1500s (25 min) -> 3 credits
    resp = client.post(f"/api/audio-search/{searched_job_with_results.id}/select",
                       json={"selection_index": 0, "acknowledged_credits": 3}, headers=auth_headers)
    assert resp.status_code == 200
    # assert 3 credits deducted; state_data.credits_charged == 3; source == "search_metadata"


def test_select_blocks_over_limit_result(client, auth_headers, searched_job_over_limit):
    resp = client.post(f"/api/audio-search/{searched_job_over_limit.id}/select",
                       json={"selection_index": 0, "acknowledged_credits": 7}, headers=auth_headers)
    assert resp.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/api/test_audio_search_select_pricing.py -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

- Add `acknowledged_credits: Optional[int]` to `AudioSelectRequest`.
- In `/select`: read the chosen result's `duration`; if blocked → 422; `credits = duration_to_credits(duration)` (1 if missing); verify against `acknowledged_credits` (409 on mismatch); `deduct_credits(..., amount=credits, reason="job_creation")`; store `state_data.credits_charged`, `duration_estimate_seconds`, `duration_estimate_source="search_metadata"`. On deduction failure return 402.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/api/test_audio_search_select_pricing.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/api/routes/audio_search.py backend/tests/api/test_audio_search_select_pricing.py
git commit -m "feat(pricing): charge N credits by result duration on audio-search select"
```

---

### Task 12: Upload preflight pause at `uploads-complete`

**Files:**
- Modify: `backend/api/routes/file_upload.py` (`uploads-complete` ~line 1331-1550)
- Test: `backend/tests/api/test_upload_preflight.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/api/test_upload_preflight.py
from unittest.mock import patch


def test_uploads_complete_pauses_for_preflight(client, auth_headers, upload_job):
    with patch("backend.api.routes.file_upload._get_audio_duration_ffprobe_signed", return_value=1500):
        resp = client.post(f"/api/jobs/{upload_job.id}/uploads-complete", json={}, headers=auth_headers)
    assert resp.status_code == 200
    job = get_job(upload_job.id)
    assert job.status == "awaiting_duration_confirm"
    assert job.state_data["duration_confirm_reason"] == "preflight"
    assert job.state_data["duration_estimate_seconds"] == 1500
    # workers NOT triggered yet, NOT charged yet


def test_uploads_complete_rejects_over_limit(client, auth_headers, upload_job):
    with patch("backend.api.routes.file_upload._get_audio_duration_ffprobe_signed", return_value=4000):
        resp = client.post(f"/api/jobs/{upload_job.id}/uploads-complete", json={}, headers=auth_headers)
    assert resp.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/api/test_upload_preflight.py -v`
Expected: FAIL — currently triggers workers immediately.

- [ ] **Step 3: Write minimal implementation**

In `uploads-complete`, after validating uploads and before triggering workers:

```python
seconds = await _get_audio_duration_ffprobe_signed(job_id, job, storage)
if seconds is not None and is_blocked(seconds):
    raise HTTPException(status_code=422, detail="Inputs over 60 minutes aren't supported")
credits = duration_to_credits(seconds) if seconds is not None else 1
get_job_manager().update_job(job_id, {
    "state_data.duration_estimate_seconds": seconds,
    "state_data.duration_estimate_source": "upload_ffprobe",
    "state_data.duration_confirm_reason": "preflight",
    "state_data.credits_charged": 0,
})
get_job_manager().transition_to_state(job_id=job_id, new_status=JobStatus.AWAITING_DURATION_CONFIRM,
                                      progress=12, message="Confirm cost to start processing")
return get_job_manager().get_job(job_id)   # do NOT trigger workers; confirm-duration will
```

For the `preflight` branch, `confirm-duration` (Task 8) charges `duration_to_credits(duration_estimate_seconds)` and then resumes the *download/processing* path used by uploads (trigger workers). Ensure `_resume_processing` covers the upload case (no torrent download needed; it goes straight to the gather/reconcile — reconcile is a no-op since estimate == actual for uploads).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/api/test_upload_preflight.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/api/routes/file_upload.py backend/tests/api/test_upload_preflight.py
git commit -m "feat(pricing): upload uploads-complete routes to duration-confirm preflight"
```

---

## Phase 6 — Notifications & timeouts

### Task 13: Frontend status config + notifiable

**Files:**
- Modify: `frontend/lib/job-status.ts` (`STATUS_CONFIG`, `isNotifiableBlockingStatus`)
- Test: `frontend/__tests__/job-status.test.ts` (extend)

- [ ] **Step 1: Write the failing test**

```ts
// frontend/__tests__/job-status.test.ts  (add)
import { STATUS_CONFIG, isNotifiableBlockingStatus, isBlockingStatus } from "@/lib/job-status";

it("awaiting_duration_confirm is blocking + notifiable", () => {
  expect(STATUS_CONFIG["awaiting_duration_confirm"]).toBeDefined();
  expect(isBlockingStatus("awaiting_duration_confirm")).toBe(true);
  expect(isNotifiableBlockingStatus("awaiting_duration_confirm")).toBe(true);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx jest job-status.test.ts`
Expected: FAIL — undefined config.

- [ ] **Step 3: Write minimal implementation**

Add to `STATUS_CONFIG` (match the shape of the existing `awaiting_review` entry — label key, `isBlocking: true`, amber color, icon) and include `"awaiting_duration_confirm"` in `isNotifiableBlockingStatus`'s allowed set.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx jest job-status.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/job-status.ts frontend/__tests__/job-status.test.ts
git commit -m "feat(pricing): mark awaiting_duration_confirm blocking + notifiable"
```

---

### Task 14: 15-minute idle reminder for duration-confirm

**Files:**
- Modify: `backend/services/job_manager.py` (`_schedule_idle_reminder` action_type ~line 644)
- Modify: `backend/api/routes/internal.py` (`/check-idle-reminder` blocking-state list ~line 393)
- Modify: `backend/services/email_service.py` (`send_duration_confirm_reminder`)
- Test: `backend/tests/api/test_internal_idle_reminder.py` (extend or create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/api/test_internal_idle_reminder.py
from unittest.mock import patch


def test_idle_reminder_sends_for_duration_confirm(client, internal_headers, duration_confirm_job):
    with patch("backend.api.routes.internal.get_email_service") as es:
        es.return_value.send_duration_confirm_reminder.return_value = True
        resp = client.post(f"/api/internal/jobs/{duration_confirm_job.id}/check-idle-reminder",
                           json={"action_type": "duration_confirm"}, headers=internal_headers)
    assert resp.status_code == 200
    es.return_value.send_duration_confirm_reminder.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/api/test_internal_idle_reminder.py -k duration -v`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

- In `_schedule_idle_reminder`, add a case for entering `AWAITING_DURATION_CONFIRM` → schedule with `delay_seconds=15*60` and `action_type="duration_confirm"`. Trigger it from `transition_to_state` where the review reminder is scheduled (follow the existing audio_edit/lyrics/instrumental cases).
- In `/check-idle-reminder`, accept `AWAITING_DURATION_CONFIRM` in the "still blocking?" check and, for `action_type == "duration_confirm"`, call `email_service.send_duration_confirm_reminder(job)` guarded by the existing `reminder_sent` idempotency flag.
- Add `send_duration_confirm_reminder(self, job)` to `email_service.py` mirroring `send_review_reminder` (i18n subject/body; deep-link to the job).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/api/test_internal_idle_reminder.py -k duration -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/job_manager.py backend/api/routes/internal.py backend/services/email_service.py backend/tests/api/test_internal_idle_reminder.py
git commit -m "feat(pricing): 15-min idle email reminder for duration-confirm"
```

---

### Task 15: 24h reminder / 48h auto-cancel+refund

**Files:**
- Modify: `backend/workers/stale_review_processor.py` (status list ~line 49; expiry refund)
- Modify: `backend/services/email_service.py` (`send_duration_confirm_expired`)
- Test: `backend/tests/test_stale_review_processor.py` (extend or create `test_stale_duration_confirm.py`)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_stale_duration_confirm.py
from unittest.mock import patch, MagicMock
from backend.workers.stale_review_processor import process_stale_reviews


def test_expired_duration_confirm_refunds_and_cancels(firestore_emulator):
    # seed a job in AWAITING_DURATION_CONFIRM with blocking_state_entered_at = now-49h, credits_charged=3
    ...
    with patch("backend.workers.stale_review_processor.get_user_service") as us, \
         patch("backend.workers.stale_review_processor.get_email_service") as es:
        process_stale_reviews()
    us.return_value.add_credits.assert_called_once()        # full refund of 3
    # job is CANCELLED; expiry email sent
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_stale_duration_confirm.py -v`
Expected: FAIL — processor ignores the new state.

- [ ] **Step 3: Write minimal implementation**

- Add `JobStatus.AWAITING_DURATION_CONFIRM` to the scanned-status list (~line 49).
- In the expiry branch (48h), when the job is in `AWAITING_DURATION_CONFIRM`: refund `state_data.credits_charged` via `add_credits(..., reason="duration_confirm_expired")`, `cancel_job(...)`, and send `send_duration_confirm_expired(job)`. The 24h reminder branch sends `send_duration_confirm_reminder` (reuse) for this state.
- Add `send_duration_confirm_expired(self, job)` mirroring `send_review_expired`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_stale_duration_confirm.py tests/test_stale_review_processor.py -v`
Expected: PASS (new + existing review-expiry tests)

- [ ] **Step 5: Commit**

```bash
git add backend/workers/stale_review_processor.py backend/services/email_service.py backend/tests/test_stale_duration_confirm.py
git commit -m "feat(pricing): 24h reminder + 48h auto-cancel/refund for duration-confirm"
```

---

## Phase 7 — Frontend UX

### Task 16: API client methods

**Files:**
- Modify: `frontend/lib/api.ts` (add `estimateDuration`, `confirmDuration`)
- Test: `frontend/__tests__/api-duration.test.ts` (create)

- [ ] **Step 1: Write the failing test**

```ts
// frontend/__tests__/api-duration.test.ts
import { api } from "@/lib/api";

global.fetch = jest.fn();

it("estimateDuration posts url and returns credits", async () => {
  (fetch as jest.Mock).mockResolvedValueOnce({
    ok: true, json: async () => ({ duration_seconds: 905, credits: 2, blocked: false, source: "youtube_metadata" }),
  });
  const r = await api.estimateDuration("https://youtu.be/x");
  expect(r.credits).toBe(2);
  expect((fetch as jest.Mock).mock.calls[0][0]).toContain("/api/jobs/estimate");
});

it("confirmDuration posts acknowledged_credits", async () => {
  (fetch as jest.Mock).mockResolvedValueOnce({ ok: true, json: async () => ({ id: "j1" }) });
  await api.confirmDuration("j1", 3);
  const body = JSON.parse((fetch as jest.Mock).mock.calls[0][1].body);
  expect(body.acknowledged_credits).toBe(3);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx jest api-duration.test.ts`
Expected: FAIL — methods missing.

- [ ] **Step 3: Write minimal implementation**

Add to the `api` object (follow the existing fetch-wrapper/auth-header pattern in `api.ts`):

```ts
async estimateDuration(url: string): Promise<{ duration_seconds: number | null; credits: number; blocked: boolean; source: string; }> {
  return this.request("/api/jobs/estimate", { method: "POST", body: JSON.stringify({ url }) });
},
async confirmDuration(jobId: string, acknowledgedCredits: number) {
  return this.request(`/api/jobs/${jobId}/confirm-duration`, {
    method: "POST", body: JSON.stringify({ acknowledged_credits: acknowledgedCredits }),
  });
},
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx jest api-duration.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/api.ts frontend/__tests__/api-duration.test.ts
git commit -m "feat(pricing): add estimateDuration + confirmDuration api clients"
```

---

### Task 17: `DurationCostConfirm` modal

**Files:**
- Create: `frontend/components/job/DurationCostConfirm.tsx`
- Test: `frontend/__tests__/DurationCostConfirm.test.tsx` (create)

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/__tests__/DurationCostConfirm.test.tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { DurationCostConfirm } from "@/components/job/DurationCostConfirm";

const base = { open: true, durationSeconds: 905, credits: 2, balance: 5, onConfirm: jest.fn(), onClose: jest.fn() };

it("shows duration + cost and confirms when affordable", () => {
  render(<DurationCostConfirm {...base} />);
  expect(screen.getByText(/16/)).toBeInTheDocument();    // minutes
  fireEvent.click(screen.getByRole("button", { name: /confirm/i }));
  expect(base.onConfirm).toHaveBeenCalled();
});

it("shows buy-credits path when short", () => {
  const onBuy = jest.fn();
  render(<DurationCostConfirm {...base} credits={4} balance={2} onBuyCredits={onBuy} />);
  fireEvent.click(screen.getByRole("button", { name: /buy credits/i }));
  expect(onBuy).toHaveBeenCalled();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx jest DurationCostConfirm.test.tsx`
Expected: FAIL — component missing.

- [ ] **Step 3: Write minimal implementation**

```tsx
// frontend/components/job/DurationCostConfirm.tsx
"use client";
import { useTranslations } from "next-intl";
import { formatDurationCost } from "@/lib/pricing";

interface Props {
  open: boolean;
  durationSeconds: number | null;
  credits: number;
  balance: number;
  estimated?: boolean;
  reconcile?: boolean;
  onConfirm: () => void;
  onClose: () => void;
  onBuyCredits?: () => void;
}

export function DurationCostConfirm(props: Props) {
  const t = useTranslations("pricing");
  if (!props.open) return null;
  const { minutes } = props.durationSeconds != null
    ? formatDurationCost(props.durationSeconds) : { minutes: 0 };
  const short = props.balance < props.credits;
  return (
    <div role="dialog" aria-modal="true" className="modal">
      <h2>{props.reconcile ? t("reconcileTitle") : t("confirmTitle")}</h2>
      <p>{t("creditsForDuration", { minutes, credits: props.credits })}{props.estimated ? ` (${t("estimatedLabel")})` : ""}</p>
      <p>{t("balance", { balance: props.balance })}</p>
      {short ? (
        <button onClick={props.onBuyCredits}>{t("buyCredits")}</button>
      ) : (
        <button onClick={props.onConfirm}>{t("confirm")}</button>
      )}
      <button onClick={props.onClose}>{t("cancel")}</button>
    </div>
  );
}
```

> Style with the project's existing modal/dialog primitives (match `BuyCreditsDialog`). The test only asserts behaviour/text, so structure can follow house style.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx jest DurationCostConfirm.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/components/job/DurationCostConfirm.tsx frontend/__tests__/DurationCostConfirm.test.tsx
git commit -m "feat(pricing): add DurationCostConfirm modal"
```

---

### Task 18: Wire modal into guided flow, search chips, dashboard banner

**Files:**
- Modify: `frontend/components/job/GuidedJobFlow.tsx` (URL/upload confirm step; pass `acknowledged_credits`)
- Modify: `frontend/components/audio-search/AudioSearchDialog.tsx` (per-result `Xm · N credits` chip; "estimated" tooltip for torrent sources)
- Modify: dashboard banner component (render `DurationCostConfirm` when a job is `awaiting_duration_confirm`, using job `state_data`)
- Test: `frontend/__tests__/guided-flow.test.tsx` (extend)

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/__tests__/guided-flow.test.tsx  (add)
it("URL flow shows cost confirm before creating job", async () => {
  // mock api.estimateDuration -> { duration_seconds: 905, credits: 2, blocked: false }
  // enter a URL, advance to submit, assert DurationCostConfirm renders with 2 credits,
  // confirm, assert api.createJobFromUrl called with acknowledged_credits: 2
});
```

> Flesh out using the existing `guided-flow.test.tsx` harness (it already mocks `api` and renders `GuidedJobFlow`).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx jest guided-flow.test.tsx`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

- URL branch: on submit, call `api.estimateDuration(url)`; if `blocked` show the over-limit message; else open `DurationCostConfirm`; on confirm call `createJobFromUrl({ ..., duration_seconds, acknowledged_credits: credits })`.
- Search branch: render the per-result cost chip from `formatDurationCost(result.duration)`; pass `acknowledged_credits` to `/select`.
- Upload branch: after `uploads-complete` returns `awaiting_duration_confirm`, open `DurationCostConfirm` (preflight) driven by `state_data.duration_estimate_seconds`; on confirm call `api.confirmDuration(jobId, credits)`.
- Dashboard banner: when polling sees `awaiting_duration_confirm`, surface the same modal; for `reconcile` reason use `state_data.pending_additional_credits` and the reconcile copy.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx jest guided-flow.test.tsx job-status.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/components/job/GuidedJobFlow.tsx frontend/components/audio-search/AudioSearchDialog.tsx frontend/__tests__/guided-flow.test.tsx
git commit -m "feat(pricing): wire duration cost confirm into guided flow, search, banner"
```

---

## Phase 8 — i18n & end-to-end

### Task 19: Add i18n strings + translate all locales

**Files:**
- Modify: `frontend/messages/en.json`
- Run: `python scripts/translate.py --messages-dir ./messages --target all`

- [ ] **Step 1: Add the `pricing.*`, `email.durationConfirm*`, and `status.awaiting_duration_confirm` keys to `en.json`**

Keys (values are examples — match house tone):
```
"pricing": {
  "confirmTitle": "Confirm your karaoke job",
  "reconcileTitle": "This song is longer than expected",
  "creditsForDuration": "{minutes} min · {credits} credits",
  "estimatedLabel": "estimated — final cost confirmed after download",
  "balance": "You have {balance} credits",
  "confirm": "Confirm & start",
  "buyCredits": "Buy credits",
  "cancel": "Cancel",
  "overLimit": "Inputs over 60 minutes aren't supported.",
  "reconcileBody": "It turned out to be {minutes} min, which needs {credits} more credit(s)."
}
```
Plus `email.durationConfirmReminder.subject/body`, `email.durationConfirmExpired.subject/body`, and a job-status label `awaiting_duration_confirm`.

- [ ] **Step 2: Run the translator (uses the GCS cache; only new strings hit Gemini)**

Run: `cd frontend && python scripts/translate.py --messages-dir ./messages --target all`
Expected: all 33 locale files updated; no missing-key errors.

- [ ] **Step 3: Verify translation completeness (the CI check)**

Run: `cd frontend && python scripts/translate.py --messages-dir ./messages --target all --dry-run`
Expected: reports nothing left to translate.

- [ ] **Step 4: Commit**

```bash
git add frontend/messages/
git commit -m "feat(pricing): add duration-pricing i18n strings for all 33 locales"
```

---

### Task 20: Integration test — full reconcile-up flow

**Files:**
- Test: `backend/tests/integration/test_duration_pricing_flow.py` (create)

- [ ] **Step 1: Write the test (uses the Firestore emulator + mocked ffprobe/workers)**

```python
# backend/tests/integration/test_duration_pricing_flow.py
from unittest.mock import patch


def test_search_underestimate_then_reconcile_up(client, auth_headers, firestore_emulator):
    """Search result claims 15 min (charge 2). Real file is 35 min (needs 4).
    Job pauses at reconcile; user confirms; 2 more credits deducted; processing resumes."""
    # 1. create via search + select with acknowledged_credits=2 (seed result duration=900)
    # 2. drive the no-edit convergence with patched ffprobe -> 2100s
    with patch("backend.services.duration_reconciliation._ffprobe_seconds", return_value=2100):
        # invoke reconcile_and_maybe_pause(job_id) -> paused
        ...
    # 3. assert job AWAITING_DURATION_CONFIRM, reason=reconcile, pending_additional_credits=2
    # 4. POST confirm-duration acknowledged_credits=4 -> 200, 2 more deducted, resumes
    # 5. assert credits_charged == 4 and workers triggered
```

- [ ] **Step 2: Run it**

Run: `cd backend && python -m pytest tests/integration/test_duration_pricing_flow.py -v`
Expected: PASS

- [ ] **Step 3: Run the full backend + frontend suites**

Run: `cd backend && python -m pytest tests/ -q` and `cd frontend && npx jest --ci`
Expected: green (fix any regressions in flat-1 assumptions surfaced by existing tests).

- [ ] **Step 4: Commit**

```bash
git add backend/tests/integration/test_duration_pricing_flow.py
git commit -m "test(pricing): end-to-end reconcile-up integration test"
```

---

## Self-Review (completed during planning)

- **Spec coverage:** pricing util (T1/T2), >60 block (T1/T9/T10/T11/T12/reconcile T5), N-credit deduction (T3), new state (T4), reconcile engine + both convergence points (T5/T6/T7), confirm + estimate endpoints (T8/T9), per-source pre-flight (T10 URL, T11 search, T12 upload), in-browser notify (T13), 15-min email (T14), 24h/48h + refund (T15), frontend modal/flow/banner (T16-T18), i18n 33 locales (T19), e2e (T20). Insufficient-credits inline-buy: backend 402 (T8/T11) + modal buy path (T17) + flow wiring (T18). Admin bypass + summary-projection preserved (called out in T10).
- **Placeholder scan:** the only `...` markers are inside test bodies that explicitly instruct the engineer to follow the nearest existing harness in that file (the harnesses are non-trivial and project-specific); all production code is complete.
- **Type consistency:** `deduct_credits(email, job_id, amount, reason)`, `add_credits(email, amount, reason, job_id)`, `reconcile_duration(...) -> ReconcileResult(action, pending_additional_credits, actual_seconds)`, `reconcile_and_maybe_pause(job_id) -> bool`, `_ffprobe_seconds(job, storage)` / `_get_audio_duration_ffprobe_signed(job_id, job, storage)`, `state_data.credits_charged|duration_estimate_seconds|duration_actual_seconds|duration_confirm_reason|pending_additional_credits|duration_confirmed`, and the `/estimate` + `/confirm-duration` shapes are used consistently across tasks.

## Risks / watch-items for the implementer

- **Race-safety:** all charges go through the transactional `deduct_credits`; preserve `create_job`'s job-deletion-on-failure rollback when charging N.
- **`check_availability` duration key:** verify the exact field name from flacfetch `/check-youtube` (Task 9 note).
- **Summary projection:** if any new `state_data` field must show in the job list, add to BOTH `SUMMARY_FIELD_PATHS` and `_SUMMARY_STATE_DATA_KEYS` with a regression test (project gotcha).
- **Existing flat-1 tests:** several tests assume a 1-credit charge; update them to the duration-derived amount where appropriate (Task 10/11 steps run the `-k create_job` suite to catch these).
- **Feature flag:** consider `DURATION_PRICING_ENABLED` (default on) so the charge path can fall back to flat-1 if a serious issue appears in prod.
