# Plan: Fix visibility-change redistribution + make sequence-gap alerts recycle-aware

**Created:** 2026-08-18
**Branch:** feat/sess-20260818-1124-visibility-recycle-dataloss
**Status:** Draft

## Overview

Investigation of a "GDrive Validation: missing 1537" alert traced to a **public→private
visibility change** on NOMAD-1537 (`Alma Nocturna – Mejor Cállate`, job `25d076e5`),
not a brand-code allocation race. Two real defects and one policy gap were found:

1. **`redistribute_video` always crashes** on `complete → packaging`
   (`video_worker_orchestrator.py:661`), so **every** public→private conversion silently
   fails its private-side redistribution — the track is never archived to the
   `Tracks-NonPublished` Dropbox and never gets a `NOMADNP` code. It looks fine to the
   customer because downloads serve from GCS finals.
2. **`change_to_private` deletes first, rebuilds second, with no rollback**
   (`visibility_change_service.py:74-114`): it deletes YouTube/Dropbox/GDrive + recycles
   the public number, *then* calls the (always-failing) redistribution. Each failure also
   **leaks a `NOMADNP` code** (allocated, never saved, never recycled — e.g. `NOMADNP-0214`).
3. **The gdrive-validator has no awareness of intentionally-vacated numbers.** Going-private
   recycles the public number (kept behaviour — see Decisions). Between recycle and the next
   public job reusing it, the number is legitimately absent from the public share, so the
   daily validator fires a false "missing" alert. (Here, `e921ab26 / Luvcat – Spider` later
   reused 1537, closing the gap ~5h after the alert.)

### Root-cause timeline (UTC 2026-08-18, confirmed from logs)
- `00:05:54–58` — public→private on `25d076e5`: deleted YouTube `TJUKJRyb5MQ`, Dropbox folder,
  GDrive public-share files for 1537; recycled `NOMAD-1537`; cleared distribution state.
- `00:06:07` — allocated `NOMADNP-0214`, started distribution, crashed
  `Invalid state transition … complete -> packaging`; redistribution aborted, **no rollback**.
- `01:00:11` — gdrive-validator daily run reports `CDG/MP4/MP4-720p: missing 1537` → the alert.
- `05:5x` — unrelated public job `e921ab26` (Luvcat – Spider) allocated the recycled `1537`
  and published under it.
- `07:0x` — GCS/divebar mirror pulled `NOMAD-1537 - Luvcat - Spider.*` → gap closed.

Customer impact: **none** — `25d076e5` is a healthy private track (GCS-backed downloads work).
Internal impact: no `Tracks-NonPublished` archive / `NOMADNP` code for it; one leaked private
code; spurious operator alert.

## Decisions (from product owner, 2026-08-18)

- **Number policy: recycle & reuse (keep current).** A vacated public number returns to the
  pool and the next public job fills it.
- **Validator: leave as-is — the transient gap warning is DESIRED.** Going-private is rare, the
  gap fills naturally on the next public track, and the owner explicitly wants the alert even
  in that case. → **Part E is dropped**; no change to `gdrive_validator/main.py` or
  `check_public_share.py`. Do not add anything to static `KNOWN_GAPS` either.
- **Private archive: yes — fix redistribution.** Going-private must archive to
  `Tracks-NonPublished` with a `NOMADNP` code, consistent with native-private jobs, and must
  be atomic (public outputs removed only after the private archive succeeds).

Scope is therefore **A–D only** (fix + harden the redistribution path and recover the one
affected job); the alerting pipeline is intentionally untouched.

## Requirements

- [ ] **A.** `redistribute_video` runs org+distribution+notifications on a `complete` job
      without an illegal status transition; job remains `complete` throughout.
- [ ] **B.** `change_to_private` is **distribute-then-delete**: private redistribution +
      `NOMADNP` allocation succeed *before* public YouTube/Dropbox/GDrive deletion + public
      number recycle. On any failure: no public deletion, no leaked private code, job stays
      fully public and `complete`.
- [ ] **C.** No leaked `NOMADNP` codes: a private code allocated during a redistribution that
      later fails is recycled (or not allocated until the success path).
- [ ] **D.** Recover `25d076e5` (create its NonPublished archive + `NOMADNP` code) and recycle
      the leaked `NOMADNP-0214`.
- [ ] Regression tests for A, B, C. All existing `test_change_visibility.py` tests still pass.
- [ ] Validator and `KNOWN_GAPS` unchanged (transient going-private alert is desired).

**Out of scope (explicitly not doing):** ~~E — recycle-aware validator~~. The owner wants to keep
receiving the gap warning for going-private events; it self-resolves on the next public track.

## Technical Approach

### A. Fix the `complete → packaging` crash (proximate bug)
`redistribute_video` (`backend/workers/video_worker.py:481`) reuses
`VideoWorkerOrchestrator._run_organization/_run_distribution/_run_notifications`;
`_run_distribution` calls `self._update_progress(JobStatus.PACKAGING, 90, …)`
(`video_worker_orchestrator.py:661`), which invokes `JobManager.transition_status(...,
raise_on_invalid=True)`. From `complete` that's illegal (`models/job.py:193`:
`COMPLETE → [AWAITING_REVIEW, LYRICS_COMPLETE]`).

**Chosen fix:** add a `redistribute_mode: bool = False` flag to `OrchestratorConfig`
(or an `update_status: bool` param threaded to `_update_progress`). When set, `_update_progress`
updates the progress **percentage/message only** and **skips the status write**, leaving the job
`complete`. Rationale: a redistribution is not a lifecycle change; the job is and remains
complete. Avoids polluting `STATE_TRANSITIONS` with a `COMPLETE↔PACKAGING` round-trip that other
call sites could misuse.

- Guard: keep `raise_on_invalid=True` for the normal pipeline; the flag only bypasses the write.
- Verify no other stage in the redistribute-only path (`_run_organization`, `_run_distribution`,
  `_run_notifications`) performs a status transition that assumes a fresh pipeline.

### B. Distribute-then-delete + rollback (`change_to_private`)
Reorder `visibility_change_service.py:change_to_private`:

1. Set guard flag (unchanged).
2. **Redistribute to the private destination first** (`redistribute_video`): reuse GCS finals
   (`keep_gcs_finals` stays true — never deleted for this path), upload to `Tracks-NonPublished`,
   allocate `NOMADNP` code, write new `state_data.brand_code`/`dropbox_link`. Public outputs are
   still live at this point.
3. **Only on success**, flip `is_private=True`, then delete the *public* outputs (YouTube,
   public Dropbox folder, public GDrive files) and **recycle the public `NOMAD` number**
   (split the current `_delete_distributed_outputs` so recycle of the *public* code happens here,
   after success).
4. Clear guard flag.
5. **On any failure** (redistribution raises/returns False): recycle the just-allocated
   `NOMADNP` code (C), clear the guard flag, leave `is_private=False` and all public outputs
   intact, re-raise. Net effect: a failed going-private is a no-op, not a destructive partial.

Note the YouTube deletion is irreversible, which is *why* it must be last — the current
delete-first order can never be safely rolled back. `_delete_distributed_outputs` is refactored
so "delete public + recycle public code" is callable independently of "clear distribution state".

### C. No leaked private codes
Two options; prefer (i):
  - (i) Allocate the `NOMADNP` code inside the redistribution success path only; if redistribution
        fails before it is persisted, nothing to recycle. Where allocation already happens early
        (orchestrator `_run_organization`), wrap the redistribute call so a failure recycles
        `orchestrator.result.brand_code` if one was allocated.
  - (ii) Always recycle `orchestrator.result.brand_code` in the `except` path of `change_to_private`.

### D. Recovery (operational, after A–C deployed)
- **`25d076e5`:** re-run the fixed private redistribution (finals + packages confirmed present in
  `gs://…/jobs/25d076e5/{finals,packages}/`). Produces the NonPublished archive + `NOMADNP` code.
  Use the admin re-distribute path or a one-off `redistribute_video('25d076e5')` invocation.
- **Leaked `NOMADNP-0214`:** recycle into `brand_code_counters/NOMADNP.recycled` (currently
  `next_number=216, recycled=[215]`), or leave — private prefixes aren't validated. Recycle for
  hygiene.
- No action needed on `NOMAD-1537` (already correctly reused by Luvcat, gap closed).

### E. (dropped)
Recycle-aware validator suppression is intentionally **not** implemented — see Decisions. The
transient going-private gap warning is desired and self-resolves on the next public track. The
post-job re-validation trigger (`video_worker_orchestrator.py:305`, gated on `gdrive_folder_id`)
is correct as-is for private conversions (no public folder to re-check).

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `backend/workers/video_worker_orchestrator.py` | Modify | `redistribute_mode`/`update_status` flag; `_update_progress` skips status write when set |
| `backend/workers/video_worker.py` | Modify | `redistribute_video` passes the flag; recycle allocated code on failure |
| `backend/services/visibility_change_service.py` | Modify | Reorder `change_to_private` to distribute-then-delete; split `_delete_distributed_outputs`; recycle-on-failure |
| `backend/models/job.py` | Review | Confirm no `STATE_TRANSITIONS` change needed (preferred: none) |
| `backend/tests/test_change_visibility.py` | Modify | Real distribute-then-delete + rollback assertions (not just guard-flag) |
| `backend/tests/test_video_worker_redistribute.py` | Create | `redistribute_video` on `complete` job succeeds, no illegal transition |
| `docs/GDRIVE-VALIDATOR.md` | Modify | Add incident to History; note going-private is a legitimate (alerting) cause of gaps |
| `pyproject.toml` | Modify | Version bump |

## Testing Strategy

Follow `docs/TESTING.md`; backend unit/integration in `backend/tests/` (mock GCP), validator
unit in `infrastructure/functions/gdrive_validator/test_main.py`.

- **A:** `redistribute_video` on a `complete` job runs all three stages and does **not** raise
  `InvalidStateTransitionError`; job status stays `complete`; brand_code/dropbox_link written.
- **B:** `change_to_private` — (a) success path: private redistribute called *before* any public
  delete; public delete + public-code recycle happen only after; (b) failure path: redistribution
  raises → **assert public YouTube/Dropbox/GDrive delete were NOT called**, `is_private` stays
  False, guard flag cleared, exception re-raised. Strengthen the existing
  `test_rollback_on_redistribute_failure` (today only checks the guard flag).
- **C:** redistribution failure → allocated `NOMADNP` code is recycled exactly once.
- **Regression:** full `test_change_visibility.py` + `test_brand_code_service.py` green.
- **Post-deploy (once):** re-run `python scripts/check_public_share.py` → clean; recover
  `25d076e5` and confirm NonPublished archive + `NOMADNP` code appear.

## Recovery Runbook (D)

```python
# After A–C deployed. Recover the private archive for 25d076e5 (finals present in GCS).
from backend.workers.video_worker import redistribute_video
await redistribute_video("25d076e5")   # job already is_private=True → NonPublished + NOMADNP

# Recycle the leaked private code
from backend.services.brand_code_service import get_brand_code_service
get_brand_code_service().recycle_brand_code("NOMADNP", 214)
```

## Open Questions

- [ ] Preferred A mechanism: config `redistribute_mode` flag vs. threading `update_status=False`
      through `_update_progress`? (Leaning: config flag — single choke point.)
- [ ] For B, is it acceptable for the private `Tracks-NonPublished` archive to exist *simultaneously*
      with the still-live public outputs for the few seconds before public deletion? (Expected: yes.)

## Rollback Plan

- Pure backend code; no schema changes, no infra/validator changes. Revert the PR to restore prior
  behaviour.
- `redistribute_mode` is opt-in (only the redistribute path sets it); the normal pipeline is
  unaffected, so A/B cannot regress fresh-job distribution.
- Recovery step (D) is idempotent-ish: re-running `redistribute_video` re-uploads from GCS finals;
  safe to retry.
```
