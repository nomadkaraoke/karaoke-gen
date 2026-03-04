# Plan: Credit Refund on Job Deletion and Cancellation

**Created:** 2026-03-04
**Branch:** feat/sess-20260304-1308-investigate-user-credits
**Status:** Implemented

## Overview

When a user deletes or cancels a job, their credit is not refunded. This was discovered when user john2011@sky.com deleted a job 16 seconds after creation (accidental duplicate) and lost the credit. Job *failures* already auto-refund via `mark_job_failed()` → `refund_credit()`, but deletions and cancellations do not.

## Requirements

- [x] Job failures already refund credits (existing behavior, no change needed)
- [ ] Job cancellation refunds 1 credit
- [ ] Job deletion refunds 1 credit (if not already completed)
- [ ] No refund for completed jobs (user got their video)
- [ ] No refund for admin-owned jobs (consistent with failure refund logic)
- [ ] Idempotency: prevent double-refund if a job is cancelled then deleted, or fails then deleted
- [ ] Credit transaction records the reason clearly ("job_cancelled", "job_deleted")
- [ ] Refund failure must not block the cancel/delete operation

## Technical Approach

### Key Insight: Idempotency via Job Field

The current `refund_credit()` has no guard against double-refund for the same job. Since a job can transition through cancelled → deleted, or failed → deleted, we need a way to track that a refund was already issued.

**Approach**: Add a `credit_refunded` boolean field to the Job model. Check it before refunding. This is simpler and more reliable than scanning the user's transaction history.

### What Should and Shouldn't Be Refunded

| Job Status at Time of Action | Delete | Cancel | Notes |
|------------------------------|--------|--------|-------|
| PENDING through pre-COMPLETE | Refund | Refund | Job didn't deliver value |
| COMPLETE / PREP_COMPLETE | No refund | N/A (can't cancel terminal) | User got their output |
| FAILED | No refund (already refunded) | N/A (can't cancel terminal) | Already refunded by fail_job |
| CANCELLED | No refund (already refunded) | N/A (already cancelled) | Already refunded by cancel |

For deletion: we need to read the job status *before* deleting it to decide whether to refund. The delete endpoint already fetches the job for ownership check, so this is straightforward.

### Refund Helper

Extract the refund logic (currently in `mark_job_failed`) into a reusable private method `_refund_credit_for_job()` that:
1. Checks `job.credit_refunded` — skip if already refunded
2. Checks `job.user_email` — skip if no user
3. Checks if admin — skip if admin job
4. Calls `user_service.refund_credit()` with appropriate reason
5. Sets `credit_refunded = True` on the job (for cancel; for delete the job is about to be removed but we set it anyway for the brief window)
6. Wraps everything in try/except so failures don't block the parent operation

## Implementation Steps

1. [ ] **Add `credit_refunded` field to Job model** (`backend/models/job.py`)
   - Add `credit_refunded: bool = False` to the Job model
   - No migration needed — Firestore is schemaless, defaults to False for existing docs

2. [ ] **Extract refund helper in JobManager** (`backend/services/job_manager.py`)
   - Create `_refund_credit_for_job(self, job_id: str, job: Job, reason: str) -> bool`
   - Move the refund logic from `mark_job_failed` (lines 821-839) into this helper
   - Add `credit_refunded` check at the top
   - Update `mark_job_failed` to call the helper with `reason="job_failed"`

3. [ ] **Add refund to `cancel_job()`** (`backend/services/job_manager.py`)
   - After setting status to CANCELLED, call `_refund_credit_for_job(job_id, job, reason="job_cancelled")`
   - The job object is already fetched at line 1017

4. [ ] **Add refund to `delete_job()`** (`backend/services/job_manager.py`)
   - Need to fetch the job (already done when `delete_files=True`, but not always)
   - Before deletion, check if job status is NOT in `[COMPLETE, PREP_COMPLETE, FAILED, CANCELLED]`
   - If eligible, call `_refund_credit_for_job(job_id, job, reason="job_deleted")`
   - For FAILED/CANCELLED: skip because refund was already issued during that transition
   - For COMPLETE/PREP_COMPLETE: skip because user received their output

5. [ ] **Update `mark_job_failed` to set `credit_refunded`**
   - The helper handles this, but also update the Firestore doc

6. [ ] **Add unit tests** (`backend/tests/test_credit_enforcement.py`)
   - Test: cancel_job refunds credit for non-admin user
   - Test: cancel_job skips refund for admin jobs
   - Test: delete_job refunds credit for non-complete, non-terminal jobs
   - Test: delete_job does NOT refund for completed jobs
   - Test: delete_job does NOT double-refund for already-failed jobs
   - Test: delete_job does NOT double-refund for already-cancelled jobs
   - Test: refund failure doesn't block cancel/delete
   - Test: credit_refunded flag prevents double refund

7. [ ] **Backfill: Refund john2011@sky.com** (manual one-time)
   - Grant 1 credit for the deleted job 40dcca89 via admin API or direct Firestore update

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `backend/models/job.py` | Modify | Add `credit_refunded: bool = False` field |
| `backend/services/job_manager.py` | Modify | Extract `_refund_credit_for_job()` helper, add refund to `cancel_job()` and `delete_job()` |
| `backend/tests/test_credit_enforcement.py` | Modify | Add tests for cancel/delete refund scenarios |

## Testing Strategy

- **Unit tests**: Extend `test_credit_enforcement.py` with new test classes `TestCreditRefundOnJobCancellation` and `TestCreditRefundOnJobDeletion`, following the exact pattern of existing `TestCreditRefundOnJobFailure`
- **Manual verification**: After deploy, cancel and delete test jobs and verify credit balance updates

## Open Questions

- [ ] Should we notify the user (email/in-app) when a credit is refunded? (Suggest: no, keep it simple for now — the credit just appears back in their balance)

## Rollback Plan

- The `credit_refunded` field is additive (defaults to False for old jobs)
- If issues arise, revert the PR — old behavior (no refund on cancel/delete) is restored
- Any refunds already issued are just bonus credits to users, no harm done
