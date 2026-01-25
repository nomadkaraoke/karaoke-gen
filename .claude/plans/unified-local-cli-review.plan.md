# Plan: Unified Combined Review for Local CLI

**Created:** 2026-01-24
**Branch:** feat/sess-20260124-1444-combined-review-flow
**Status:** Draft

## Problem Statement

The combined lyrics + instrumental review flow was implemented for the cloud web app, but the local CLI is broken in two ways:

1. **Bug: Lyrics review is skipped when `SKIP_CORRECTION=true`** (the default)
   - Review code lives inside `correct_lyrics()` method in controller.py
   - When auto-correction is disabled, `correct_lyrics()` is never called
   - Users never get a chance to review lyrics

2. **Standalone instrumental UI still exists for CLI**
   - `InstrumentalReviewServer` in `instrumental_review/server.py` is a separate server
   - CLI launches this standalone server after lyrics review
   - The combined review flow we built only works for the web app

## Goals

1. **Fix the skip bug** - Review should happen even when auto-correction is disabled
2. **Unify all review UIs** - One combined review flow for CLI, cloud CLI, and web app
3. **Remove dead code** - Delete standalone `InstrumentalReviewServer` and related code
4. **Keep it simple** - Minimal changes to achieve the goal

## Architecture Overview

### Current State (Broken)

```
CLI Flow:
  controller.py::process()
    → correct_lyrics() [SKIPPED if SKIP_CORRECTION=true]
        → ReviewServer starts (lyrics only)
        → User reviews
        → ReviewServer stops
    → gen_cli.py::run_instrumental_review()
        → InstrumentalReviewServer starts (instrumental only)
        → User selects instrumental
        → InstrumentalReviewServer stops

Cloud Web App Flow:
  Job enters AWAITING_REVIEW
    → User visits /app/jobs/{id}/review
    → Combined UI: lyrics + instrumental
    → POST /api/jobs/{id}/complete-review (includes instrumental_selection)
```

### Target State

```
CLI Flow (and Cloud CLI):
  controller.py::process()
    → _run_human_review() [NEW - always called when review enabled]
        → ReviewServer starts (combined: lyrics + instrumental)
        → User reviews lyrics AND selects instrumental
        → ReviewServer stops, returns both results
    → Continue with selected instrumental

Cloud Web App Flow: (unchanged)
  Same as before
```

## Key Insight

The ReviewServer already serves the bundled Next.js frontend. That frontend already has the combined review UI with `InstrumentalSelectorEmbedded`. We just need to:

1. Make `ReviewServer` serve instrumental options in its `/correction-data` endpoint
2. Make `ReviewServer` accept instrumental selection in its `/complete-review` endpoint
3. Call the review flow from the right place (not inside `correct_lyrics()`)
4. Delete `InstrumentalReviewServer` entirely

## Implementation Steps

### Phase 1: Fix ReviewServer to Support Instrumental Selection

**1.1. Add instrumental data to ReviewServer's correction-data endpoint**

File: `karaoke_gen/lyrics_transcriber/review/server.py`

The `ReviewServer.__init__()` accepts a `CorrectionResult`. We need to also accept instrumental options:

```python
def __init__(
    self,
    correction_result: CorrectionResult,
    instrumental_options: list[dict] | None = None,  # NEW
    backing_vocals_analysis: dict | None = None,     # NEW
    ...
):
```

Update `_correction_data()` endpoint to include these in response:

```python
@app.get("/api/review/{job_id}/correction-data")
def _correction_data(job_id: str):
    return {
        "lyrics_data": self.correction_result.to_dict(),
        "instrumental_options": self.instrumental_options,      # NEW
        "backing_vocals_analysis": self.backing_vocals_analysis # NEW
    }
```

**1.2. Update ReviewServer's complete-review endpoint to accept instrumental selection**

File: `karaoke_gen/lyrics_transcriber/review/server.py`

The existing endpoint accepts `corrected_lyrics`. Add `instrumental_selection`:

```python
@app.post("/api/review/{job_id}/complete-review")
def _complete_review(job_id: str, request: ReviewCompleteRequest):
    # Existing: store corrected lyrics
    self.corrected_lyrics = request.corrected_lyrics

    # NEW: store instrumental selection
    self.instrumental_selection = request.instrumental_selection  # "clean" | "with_backing"

    self.review_completed = True
```

**1.3. Expose instrumental selection after review completes**

The calling code needs to get the selection. Update the return value or add an attribute:

```python
# After review loop completes:
return {
    "corrected_lyrics": self.corrected_lyrics,
    "instrumental_selection": self.instrumental_selection
}
```

### Phase 2: Fix controller.py to Call Review at the Right Time

**2.1. Extract review logic from `correct_lyrics()` into standalone method**

File: `karaoke_gen/lyrics_transcriber/core/controller.py`

Create new method `_run_human_review()` that can be called independently:

```python
def _run_human_review(
    self,
    correction_result: CorrectionResult,
    instrumental_options: list[dict] | None = None,
    backing_vocals_analysis: dict | None = None
) -> tuple[CorrectionResult, str | None]:
    """Run human review server and return corrected result + instrumental selection."""

    if not self.output_config.review_enabled:
        return correction_result, None

    server = ReviewServer(
        correction_result=correction_result,
        instrumental_options=instrumental_options,
        backing_vocals_analysis=backing_vocals_analysis,
        ...
    )

    result = server.start()  # Blocks until review complete

    return result["corrected_lyrics"], result.get("instrumental_selection")
```

**2.2. Update `process()` to always call review when appropriate**

File: `karaoke_gen/lyrics_transcriber/core/controller.py`

Current flow (simplified):
```python
def process(self):
    self.transcribe()
    self.separate_audio()
    if self.output_config.run_correction:
        self.correct_lyrics()  # Review is inside here!
    # ...
```

New flow:
```python
def process(self):
    self.transcribe()
    self.separate_audio()

    # Build correction result (with or without auto-correction)
    if self.output_config.run_correction:
        correction_result = self._run_auto_correction()
    else:
        correction_result = self._create_uncorrected_result()

    # Human review is separate from auto-correction
    if self.output_config.review_enabled:
        instrumental_options = self._get_instrumental_options()
        backing_vocals_analysis = self._analyze_backing_vocals()

        corrected, instrumental_selection = self._run_human_review(
            correction_result,
            instrumental_options,
            backing_vocals_analysis
        )

        self.results.correction_result = corrected
        self.results.instrumental_selection = instrumental_selection
```

### Phase 3: Update gen_cli.py to Use Combined Review

**3.1. Remove standalone instrumental review call**

File: `karaoke_gen/utils/gen_cli.py`

Remove the `run_instrumental_review()` function call from main flow. The instrumental selection now comes from the combined review.

Before:
```python
# After lyrics processing
if not args.non_interactive:
    instrumental_selection = run_instrumental_review(...)
```

After:
```python
# Instrumental selection comes from controller.results.instrumental_selection
instrumental_selection = controller.results.instrumental_selection
```

**3.2. Pass instrumental options to controller**

The controller needs instrumental options to display in review. These come from the separated audio stems:

```python
controller = LyricsTranscriber(
    # ... existing params ...
)

# Before calling process(), set up instrumental options
# These are typically discovered during audio separation
```

### Phase 4: Delete Standalone InstrumentalReviewServer

**4.1. Delete files**

- Delete `karaoke_gen/instrumental_review/server.py`
- Delete `karaoke_gen/instrumental_review/__init__.py` (if empty after)
- Remove `run_instrumental_review()` from `karaoke_gen/utils/gen_cli.py`

**4.2. Clean up imports**

Remove any imports of `InstrumentalReviewServer` throughout the codebase.

### Phase 5: Update Frontend for Local Mode

**5.1. Verify local mode detection works**

File: `frontend/lib/api.ts`

The frontend already has local mode detection based on hostname and port. Verify it works:

```typescript
const isLocalMode = () => {
  if (typeof window === 'undefined') return false;
  const hostname = window.location.hostname;
  const port = parseInt(window.location.port, 10);
  return hostname === 'localhost' && port >= 8000 && port <= 8770;
};
```

**5.2. Verify correction-data endpoint parsing**

Ensure the frontend correctly parses instrumental_options and backing_vocals_analysis from the local server's response.

### Phase 6: Testing

**6.1. Manual testing checklist**

- [ ] Run CLI with `SKIP_CORRECTION=true` - review should still appear
- [ ] Run CLI with `SKIP_CORRECTION=false` - review should appear after correction
- [ ] Combined UI shows both lyrics editor and instrumental selector
- [ ] Selecting instrumental and completing review works
- [ ] No standalone instrumental review page appears
- [ ] Cloud web app still works (no regression)

**6.2. Update unit tests**

- Remove tests for `InstrumentalReviewServer`
- Add tests for `ReviewServer` with instrumental options
- Add tests for `controller._run_human_review()`

## Files to Modify

| File | Action | Description |
|------|--------|-------------|
| `lyrics_transcriber/review/server.py` | Modify | Add instrumental options/selection support |
| `lyrics_transcriber/core/controller.py` | Modify | Extract review into standalone method, call at right time |
| `utils/gen_cli.py` | Modify | Remove `run_instrumental_review()`, use combined result |
| `instrumental_review/server.py` | Delete | Standalone server no longer needed |
| `instrumental_review/__init__.py` | Modify/Delete | Clean up imports |

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Breaking cloud flow | Cloud doesn't use ReviewServer, uses backend API - no risk |
| Missing instrumental options in local mode | Fail clearly if options not provided |
| Review server port conflicts | Keep existing port allocation logic |

## Open Questions

1. **Where does backing vocals analysis run for local CLI?**
   - Currently runs in `render_video_worker.py` for cloud
   - For local, need to run it after audio separation, before review
   - May need to call analysis function from controller

2. **How are instrumental stems exposed to local review?**
   - Need to serve audio files from ReviewServer
   - Or use file:// URLs (may have CORS issues)
   - Existing approach: ReviewServer already serves static files

## Success Criteria

1. `SKIP_CORRECTION=true` no longer skips human review
2. Single combined review UI appears (no separate instrumental step)
3. All platforms (CLI, cloud CLI, web) use same review flow
4. No dead code remaining for standalone instrumental review
