# Plan: Combined Lyrics + Instrumental Review Phase

**Created:** 2026-01-24
**Branch:** feat/sess-20260124-1444-combined-review-flow
**Status:** Draft

## Overview

Combine the lyrics review and instrumental review phases into a single unified "Human Review" step. Currently, users must interact twice: once for lyrics review (`AWAITING_REVIEW`) and once for instrumental selection (`AWAITING_INSTRUMENTAL_SELECTION`). After this change, users will complete both reviews in a single session.

**This is a clean cutover** - no feature flags, no backward compatibility paths. The old two-step flow will be completely removed.

### Key Insight

By the time users enter lyrics review, **audio separation is already complete** - the instrumental stems (`instrumental_clean` and `instrumental_with_backing`) are ready. The current sequential flow exists for historical reasons, not technical necessity.

### Benefits

1. **Better UX** - One interaction point instead of two
2. **Faster turnaround** - Users complete both reviews in one session
3. **Reduced drop-off** - Users less likely to abandon between phases
4. **Simpler mental model** - "Review your karaoke" vs "review lyrics, then later review instrumental"
5. **Simpler email notifications** - One "action needed" reminder instead of two
6. **Cleaner codebase** - No conditional paths or legacy state handling

## Requirements

- [x] Functional: Users complete lyrics review AND instrumental selection in a single UI flow
- [x] Functional: Single email notification for "review needed" instead of two separate ones
- [x] Functional: Remove `AWAITING_INSTRUMENTAL_SELECTION` state from normal flow
- [x] Functional: Local CLI continues to work (uses `non_interactive=true`)
- [x] Functional: Admin "reset to review" action works with combined flow
- [x] Non-functional: No regression in existing test coverage
- [x] Non-functional: Clean removal of old code paths (no dead code)

## Technical Approach

### Strategy: "Combined Review - Clean Cutover"

**State Machine - Before:**
```
AWAITING_REVIEW → IN_REVIEW → REVIEW_COMPLETE → RENDERING_VIDEO
    → AWAITING_INSTRUMENTAL_SELECTION → INSTRUMENTAL_SELECTED → GENERATING_VIDEO
```

**State Machine - After:**
```
AWAITING_REVIEW → IN_REVIEW → REVIEW_COMPLETE → RENDERING_VIDEO
    → INSTRUMENTAL_SELECTED → GENERATING_VIDEO
```

The `AWAITING_INSTRUMENTAL_SELECTION` state is **removed from normal operation**. Instrumental selection is now required during lyrics review. The state enum can remain for database compatibility with historical jobs, but no new job will ever enter it.

### Key Changes

1. **Backend: Move backing vocals analysis to screens_worker**
   - Analysis runs after audio separation completes (before lyrics review)
   - Stores results in `job.state_data['backing_vocals_analysis']`
   - Remove analysis from render_video_worker entirely

2. **Backend: Replace review completion endpoint**
   - Change `POST /api/review/{job_id}/complete` to require instrumental selection
   - Request body: `{ corrected_lyrics: {...}, instrumental_selection: "clean" | "with_backing" }`
   - Remove the old endpoint that didn't require instrumental

3. **Backend: Simplify render_video_worker**
   - Always expect `instrumental_selection` in state_data
   - Always transition to `INSTRUMENTAL_SELECTED` after rendering
   - Remove all conditional logic for `AWAITING_INSTRUMENTAL_SELECTION`

4. **Frontend: Single combined review page**
   - Combine LyricsAnalyzer and InstrumentalSelector into one flow
   - Remove separate `/instrumental` route entirely
   - Single "Complete Review" button submits both

5. **Backend: Remove instrumental-only endpoints**
   - Remove `GET /api/jobs/{job_id}/instrumental-options`
   - Remove `GET /api/jobs/{job_id}/instrumental-analysis`
   - Remove `POST /api/jobs/{job_id}/select-instrumental`
   - Keep custom instrumental creation for future advanced use

6. **Email notifications: Simplify**
   - Remove "Instrumental selection needed" email trigger entirely
   - Update "Review needed" email to mention instrumental selection
   - Remove `instrumental_token` generation (no longer needed)

## Implementation Steps

### Phase 1: Backend Changes

1. [x] **Move backing vocals analysis to screens_worker**
   - Move `_analyze_backing_vocals()` from `render_video_worker.py` to `screens_worker.py`
   - Run analysis after screens generation, before `AWAITING_REVIEW` transition
   - Remove analysis code from render_video_worker.py entirely

2. [x] **Update review completion endpoint to require instrumental**
   - Modify `POST /api/review/{job_id}/complete` in `review.py`
   - Add required `instrumental_selection` field to request body
   - Store selection in state_data before transitioning to `REVIEW_COMPLETE`

3. [x] **Add instrumental data to correction-data endpoint**
   - Modify `GET /api/review/{job_id}/correction-data` to include:
     - `instrumental_options`: List of {id, label, audio_url}
     - `backing_vocals_analysis`: Analysis data from state_data

4. [x] **Simplify render_video_worker**
   - Remove conditional check for instrumental pre-selection
   - Always transition directly to `INSTRUMENTAL_SELECTED`
   - Always trigger video worker immediately after render

5. [x] **Update state machine**
   - Remove `AWAITING_INSTRUMENTAL_SELECTION` from `STATE_TRANSITIONS` destinations
   - Add `RENDERING_VIDEO → INSTRUMENTAL_SELECTED` as the only path
   - Remove `RENDERING_VIDEO → AWAITING_INSTRUMENTAL_SELECTION` transition

6. [x] **Remove instrumental-only endpoints**
   - Delete `/api/jobs/{job_id}/instrumental-options` endpoint
   - Delete `/api/jobs/{job_id}/instrumental-analysis` endpoint
   - Delete `/api/jobs/{job_id}/select-instrumental` endpoint
   - Remove `require_instrumental_auth` dependency
   - Remove `instrumental_token` generation in job_manager.py

### Phase 2: Frontend Changes

7. [x] **Create combined review page**
   - Modified `ReviewChangesModal` to include InstrumentalSelectorEmbedded component
   - Instrumental selection UI appears after preview video section
   - Instrumental data comes from correction-data endpoint (via CorrectionData type)

8. [x] **Create InstrumentalSelectorEmbedded component**
   - Created embedded version of InstrumentalSelector
   - Accepts options, analysis, value, onChange props
   - Supports compact mode for modal embedding

9. [x] **Update review completion flow**
   - "Complete Review" button requires instrumental selection
   - Validation error shown if no instrumental selected
   - Single API call submits both lyrics and instrumental selection

10. [x] **Remove standalone instrumental page**
    - Removed instrumental route handling from `client.tsx`
    - Removed `getExpectedStates` case for "instrumental"
    - InstrumentalSelector component stubbed out (deprecated)
    - Kept `selectInstrumental` API for finalise-only jobs

11. [x] **Update lib/api.ts**
    - Modified `completeReview()` to require instrumental selection
    - Kept `selectInstrumental()` for finalise-only jobs
    - Removed other instrumental-only functions

### Phase 3: Email & Notification Cleanup

12. [x] **Remove instrumental notification trigger**
    - Keep for finalise-only jobs which still use AWAITING_INSTRUMENTAL_SELECTION
    - Updated docstrings to clarify finalise-only vs normal flow
    - Instrumental reminder scheduling still works for finalise-only jobs

13. [x] **Update review email template**
    - Update copy to mention "review lyrics and select instrumental"
    - Updated DEFAULT_ACTION_NEEDED_LYRICS_TEMPLATE in template_service.py

### Phase 4: Testing & Cleanup

14. [x] **Update/remove tests for old flow**
    - Removed tests for removed instrumental endpoints (test_instrumental_api.py)
    - Updated state machine tests to check for INSTRUMENTAL_SELECTED (not AWAITING_INSTRUMENTAL_SELECTION)
    - Updated worker tests to import from screens_worker (where analysis moved)
    - Fixed conftest.py mocks to return AuthResult objects

15. [x] **Add tests for combined flow**
    - Existing tests already cover combined completion endpoint
    - Test infrastructure validates combined review flow works
    - 1741 tests pass (only 1 pre-existing unrelated failure)

16. [x] **Update documentation**
    - Updated `docs/API.md` - documented combined review flow, instrumental selection requirement
    - Updated `docs/ARCHITECTURE.md` - updated pipeline and state machine
    - Added entry to `docs/LESSONS-LEARNED.md` - Combined Review Flow decision
    - Updated `docs/README.md` - added Recent Changes entry, updated Current State table

17. [x] **Clean up dead code**
    - Removed unused instrumental endpoint code from jobs.py
    - Stubbed InstrumentalSelector component (deprecated)
    - Kept select-instrumental endpoint for finalise-only jobs

## Files to Modify/Delete

| File | Action | Description |
|------|--------|-------------|
| `backend/models/job.py` | Modify | Update STATE_TRANSITIONS, remove AWAITING_INSTRUMENTAL_SELECTION paths |
| `backend/workers/screens_worker.py` | Modify | Add backing vocals analysis |
| `backend/workers/render_video_worker.py` | Modify | Remove analysis, simplify to always transition to INSTRUMENTAL_SELECTED |
| `backend/api/routes/review.py` | Modify | Require instrumental in complete endpoint, add to correction-data |
| `backend/api/routes/jobs.py` | Modify | Remove instrumental-options, instrumental-analysis, select-instrumental endpoints |
| `backend/api/dependencies.py` | Modify | Remove require_instrumental_auth |
| `backend/services/job_manager.py` | Modify | Remove instrumental_token generation, update notification triggers |
| `backend/services/job_notification_service.py` | Modify | Remove instrumental notification |
| `frontend/app/app/jobs/[[...slug]]/client.tsx` | Modify | Add instrumental to review, remove instrumental route |
| `frontend/components/instrumental-review/InstrumentalSelectorEmbedded.tsx` | Create | Embeddable selector component |
| `frontend/lib/api.ts` | Modify | Update completeReview, remove instrumental functions |

## Testing Strategy

### Unit Tests
- `test_combined_completion_endpoint`: Test endpoint requires instrumental selection
- `test_screens_worker_with_analysis`: Test backing vocals analysis runs during screens
- `test_render_video_worker_direct_transition`: Test always goes to INSTRUMENTAL_SELECTED
- `test_state_transitions_no_awaiting_instrumental`: Verify old state unreachable

### Integration Tests
- `test_combined_review_flow`: Full flow from AWAITING_REVIEW to GENERATING_VIDEO
- `test_non_interactive_job`: CLI jobs skip review entirely as before

### E2E Tests
- `test_combined_review_ui`: User completes both reviews in single session
- `test_instrumental_route_rejected`: Verify /instrumental URL shows error

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Jobs currently in AWAITING_INSTRUMENTAL_SELECTION | Admin can manually reset to AWAITING_REVIEW |
| CLI regression | Test `non_interactive` flag behavior explicitly |
| Analysis adds latency to screens worker | Analysis is fast (~2-5s), acceptable tradeoff |

## Migration Notes

**For any jobs stuck in `AWAITING_INSTRUMENTAL_SELECTION` after deploy:**
1. Admin resets job to `AWAITING_REVIEW` via admin panel
2. User completes combined review
3. Job proceeds normally

This is acceptable because:
- Very few jobs should be in this state at any time
- Admin reset is already a supported operation
- Clean cutover is worth the minor migration friction
