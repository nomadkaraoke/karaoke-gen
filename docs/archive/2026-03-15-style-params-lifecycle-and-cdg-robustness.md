# Plan: Fix style_params.json Lifecycle Deletion, CDG Font Robustness, and Video Worker State Guard

**Created:** 2026-03-15
**Branch:** feat/sess-20260315-1520-investigate-state-transition
**Status:** Implemented
**Triggered by:** Job 27054772 (CDG generation failure) and Job 2d4a7bab (style_params.json 404 after 7 days)

## Overview

Three related issues discovered during investigation of production job failures:

1. **GCS lifecycle rule deletes `style_params.json` after 7 days** — The bucket has a lifecycle rule deleting objects with `uploads/` prefix after 7 days. `style_params.json` is stored under `uploads/{job_id}/style/` and gets deleted, breaking any job not completed within a week.

2. **CDG generation crashes with unhelpful error when font download fails** — When the font file can't be resolved, `_validate_and_setup_font()` sets `font_path = None`. Python's `toml.dump()` silently omits `None` values, causing `KeyError: 'font'` downstream in `KaraokeComposer`. The error message is opaque: "CDG generation was enabled but failed."

3. **Video worker doesn't guard against invalid state transitions** — Unlike other workers (audio, lyrics, render), the video worker doesn't call `validate_worker_can_run()` before transitioning to `GENERATING_VIDEO`. When Cloud Tasks retries a failed job, the worker attempts `failed -> GENERATING_VIDEO` which throws `InvalidStateTransitionError`, producing cascading noise in logs and error messages.

## Requirements

- [ ] Jobs must work regardless of how long they sit before completion (no 7-day style deadline)
- [ ] CDG generation must fail gracefully with a clear error, or fall back to a bundled font
- [ ] Video worker retries against failed/cancelled jobs must exit cleanly, not throw state errors
- [ ] All existing tests must continue to pass
- [ ] Backward compatibility: existing jobs with `uploads/` style paths must still work

## Technical Approach

### Fix 1: Move `style_params.json` from `uploads/` to `jobs/` prefix

The `jobs/` prefix is NOT subject to the lifecycle rule and is the natural home for job-scoped data. All other job outputs (stems, lyrics, screens, videos) already live under `jobs/{job_id}/`.

**Approach**: Change the path generation in `theme_service.prepare_job_style()` and all related upload paths in `file_upload.py` and `audio_search.py` from `uploads/{job_id}/style/` to `jobs/{job_id}/style/`.

**Backward compatibility**: Existing jobs in Firestore already store their `style_params_gcs_path` as a string field. Old jobs will still have `uploads/...` paths and will work until the lifecycle rule deletes them. No migration needed — workers read the path from the job document, not from a hardcoded pattern. New jobs will get `jobs/...` paths that persist forever.

**Note**: The `uploads/` prefix will still be used for temporary file upload staging (audio files, etc.) which is appropriate for the lifecycle rule.

### Fix 2: CDG font fallback to bundled default

The bundled fonts directory at `karaoke_gen/lyrics_transcriber/output/fonts/` already contains `AvenirNext-Bold.ttf` (the exact font used by the nomad theme). The CDG generator already tries this directory as a fallback, but the check at `_validate_and_setup_font()` looks for the full relative path (e.g., `themes/nomad/assets/AvenirNext-Bold.ttf`) rather than just the filename.

**Approach**: When the font path can't be resolved, fall back to the bundled font by filename (not full path). If even that fails, fall back to a known bundled font (`arial.ttf`) rather than setting `None`.

Additionally, add a safety net: if `font_path` is `None` when building the TOML data, raise a clear error instead of letting `toml.dump()` silently omit it.

### Fix 3: Video worker state guard

Follow the established pattern from `render_video_worker.py`, `audio_worker.py`, and `lyrics_worker.py`: call `validate_worker_can_run("video_worker", job)` before attempting the state transition. Make it a **hard failure** (return False) for terminal states (`failed`, `cancelled`, `complete`) since there's no point proceeding — unlike the soft warning in other workers.

Also update `_check_worker_idempotency()` to check for terminal job states, providing an additional safety layer at the HTTP endpoint level.

## Implementation Steps

### Step 1: Move style_params.json to `jobs/` prefix

1. [ ] **`backend/services/theme_service.py:337`** — Change `f"uploads/{job_id}/style/style_params.json"` to `f"jobs/{job_id}/style/style_params.json"`

2. [ ] **`backend/api/routes/file_upload.py`** — Update all `uploads/{job_id}/style/` references:
   - Line 725: Custom style_params upload path
   - Line 744: Style background image upload path
   - Line 755: Custom font upload path
   - Line 1079, 1083: `_get_gcs_path_for_file()` helper
   - Line 1376, 1379: Signed URL upload finding logic
   - Line 1873: Second path generation function
   - Line 2205, 2458: Finalise-only endpoint style finding
   - Line 2499: Style asset completion endpoint

3. [ ] **`backend/api/routes/audio_search.py:769,773`** — Update signed URL generation paths

4. [ ] **Update tests** to expect new `jobs/` prefix:
   - `backend/tests/test_theme_service.py:339,358`
   - `backend/tests/test_file_upload.py:701,708,715`
   - `backend/tests/test_style_upload.py:183-184`
   - `backend/tests/test_routes_review.py:331-337`
   - `backend/tests/emulator/test_style_loading_direct.py:153-161,258`
   - `tests/unit/test_remote_cli.py:699,2562,2644`
   - `backend/tests/test_style_upload_endpoints.py:254,290`

Note: `backend/tests/test_change_visibility.py` already uses `jobs/` prefix — no changes needed there.

### Step 2: CDG font fallback robustness

5. [ ] **`karaoke_gen/lyrics_transcriber/output/cdg.py` — `_validate_and_setup_font()`** (lines 173-187):
   - When font not found at full path, try just the filename in the bundled fonts directory
   - If still not found, fall back to `arial.ttf` from bundled fonts (always present)
   - Only set `None` if the bundled fonts directory itself doesn't exist (extreme edge case)
   - Log warning with the fallback path chosen

6. [ ] **`karaoke_gen/lyrics_transcriber/output/cdg.py` — `_create_toml_data()`** (line 400):
   - Add explicit check: if `cdg_styles["font_path"]` is None, raise `RuntimeError("CDG font_path is None — cannot generate CDG without a font")`
   - This prevents the silent `toml.dump()` omission that causes a confusing downstream error

7. [ ] **Add test** for CDG font fallback behavior:
   - Test that an unresolvable font path falls back to bundled font
   - Test that `_create_toml_data` raises if font is None

### Step 3: Video worker state guard

8. [ ] **`backend/workers/video_worker.py` — `generate_video_orchestrated()`** (around line 224):
   - Add `validate_worker_can_run("video_worker", job)` check after `_validate_prerequisites()`
   - For terminal states (`failed`, `cancelled`, `complete`), return False (hard failure)
   - Log the rejection clearly

9. [ ] **`backend/workers/video_worker.py` — `generate_video_legacy()`** (around line 637):
   - Same guard as above

10. [ ] **`backend/api/routes/internal.py` — `_check_worker_idempotency()`** (around line 79):
    - After checking `state_data` progress, also check if job is in a terminal state (`failed`, `cancelled`)
    - Return "skipped" response for terminal states to prevent Cloud Tasks from triggering workers on dead jobs

11. [ ] **Add test** for video worker state guard:
    - Test that video worker returns False when job is in `failed` state
    - Test that idempotency check rejects terminal-state jobs

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `backend/services/theme_service.py` | Modify | Change style path prefix from `uploads/` to `jobs/` |
| `backend/api/routes/file_upload.py` | Modify | Update ~11 `uploads/{job_id}/style/` path references |
| `backend/api/routes/audio_search.py` | Modify | Update 2 signed URL path references |
| `karaoke_gen/lyrics_transcriber/output/cdg.py` | Modify | Improve font fallback logic, add None guard |
| `backend/workers/video_worker.py` | Modify | Add `validate_worker_can_run()` guard to both pipelines |
| `backend/api/routes/internal.py` | Modify | Add terminal state check to idempotency function |
| `backend/tests/test_theme_service.py` | Modify | Update expected paths |
| `backend/tests/test_file_upload.py` | Modify | Update expected paths |
| `backend/tests/test_style_upload.py` | Modify | Update expected paths |
| `backend/tests/test_routes_review.py` | Modify | Update expected paths |
| `backend/tests/test_style_upload_endpoints.py` | Modify | Update expected paths |
| `backend/tests/emulator/test_style_loading_direct.py` | Modify | Update expected paths |
| `tests/unit/test_remote_cli.py` | Modify | Update expected paths |
| `backend/tests/test_cdg_countdown.py` | Modify | Add font fallback tests |
| `backend/tests/test_video_worker_orchestrator.py` | Modify | Add state guard test |

## Testing Strategy

- **Unit tests**: Update all existing path assertions to expect `jobs/` prefix. Add new tests for CDG font fallback and video worker state guard.
- **Integration tests**: The emulator test in `test_style_loading_direct.py` verifies end-to-end style loading with real GCS operations.
- **Run full suite**: `make test 2>&1 | tail -n 500` must pass before committing.

## Open Questions

None — all three fixes are well-understood from investigation.

## Rollback Plan

- **Fix 1**: If `jobs/` prefix causes issues, revert the path change. Existing jobs with `uploads/` paths will still work since workers read the path from the job document.
- **Fix 2**: CDG font fallback is purely additive — remove the fallback logic to restore original behavior.
- **Fix 3**: State guard is purely additive — remove the `validate_worker_can_run` call to restore original behavior.

## Immediate Manual Fix Applied

- Re-uploaded `style_params.json` for job `2d4a7bab` so it can be retried before this fix ships.
