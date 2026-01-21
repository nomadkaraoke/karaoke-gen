# Plan: Disable Auto-Correction (Agentic and Heuristic)

**Created:** 2026-01-20
**Branch:** feat/sess-20260120-1914-disable-auto-correction
**Status:** Draft

## Overview

Disable all auto-correction during the lyrics transcription & correction phase. Currently, auto-correction (both agentic AI-based and heuristic rule-based) creates more work for human reviewers by introducing errors that need to be manually fixed. The solution is to skip all correction processing and pass the raw transcription directly to human review.

## Requirements

- [x] Disable agentic AI correction (LLM-based via Gemini)
- [x] Disable heuristic correction (rule-based handlers like ExtendAnchorHandler, WordCountMatchHandler, etc.)
- [x] Preserve the transcription → human review → video generation flow
- [x] Minimal code changes (configuration-based if possible)
- [x] Easy to re-enable in the future if correction quality improves

## Technical Approach

The simplest approach is to change the backend configuration default for `use_agentic_ai` from `true` to `false`, AND ensure that when agentic AI is disabled, the heuristic handlers also don't run.

**Current behavior:**
1. `backend/config.py` line 46: `use_agentic_ai` defaults to `true`
2. `lyrics_worker.py` sets `USE_AGENTIC_AI` env var based on config
3. `corrector.py` line 84/132: Checks `USE_AGENTIC_AI` env var
4. When agentic is disabled, rule-based handlers still run (lines 669-733)

**Proposed changes:**

### Option A: Add a new `skip_correction` config (Recommended)
Add a single boolean `skip_correction` setting that:
- When `true`: Skips the entire correction process, returning raw transcription
- When `false` (future): Falls back to existing agentic/heuristic behavior

This is cleaner than disabling both systems separately because:
1. Single config controls both
2. Clear intent ("skip correction" vs "disable two different systems")
3. Easy to understand for future maintainers

### Option B: Just disable both individually
- Set `use_agentic_ai` default to `false`
- Disable default heuristic handlers by setting `enabled_handlers=[]`

Option A is cleaner, but Option B requires fewer code changes.

**Decision: Option A** - Add `skip_correction` config for clarity and easier future re-enablement.

## Implementation Steps

1. [x] **Add `skip_correction` config to backend/config.py**
   - Add `skip_correction: bool` setting, default `true`
   - Comment explaining this disables both agentic and heuristic correction

2. [x] **Update lyrics_worker.py to pass skip_correction**
   - Pass the config value through to LyricsProcessor/LyricsTranscriber

3. [x] **Update corrector.py to support skip_correction**
   - When `skip_correction=True`, return immediately with uncorrected segments
   - No handler processing, no agentic AI
   - Still need to do anchor/gap finding? No - skip entirely

4. [x] **Update LyricsProcessor to support skip_correction**
   - Pass through to LyricsTranscriber

5. [x] **Update LyricsTranscriber controller to support skip_correction**
   - Pass through to corrector or skip corrector.run() entirely

6. [x] **Update docs/README.md**
   - Note that auto-correction is disabled
   - Update "Agentic AI correction" status to "Disabled"

7. [x] **Run tests to verify no regressions**

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `backend/config.py` | Modify | Add `skip_correction` config (default: true) |
| `backend/workers/lyrics_worker.py` | Modify | Pass `skip_correction` to transcription |
| `karaoke_gen/lyrics_transcriber/correction/corrector.py` | Modify | Return early when skip_correction=True |
| `karaoke_gen/lyrics_transcriber/controller.py` | Modify | Pass skip_correction to corrector |
| `karaoke_gen/lyrics_processor.py` | Modify | Pass skip_correction through |
| `docs/README.md` | Modify | Update status to reflect disabled correction |

## Testing Strategy

- **Unit tests**: No new tests needed - this is a config change
- **Existing tests**: Run `make test` to ensure no regressions
- **Manual verification**: The correction metadata in the corrections.json should show no corrections made

## Open Questions

None - the approach is straightforward.

## Rollback Plan

To re-enable auto-correction:
1. Set `SKIP_CORRECTION=false` environment variable, OR
2. Change the default back to `false` in `backend/config.py`

The code paths for agentic and heuristic correction remain intact and can be re-enabled at any time.

## Alternative Considered

Instead of adding a new config, we could:
1. Set `USE_AGENTIC_AI=false` (disables agentic)
2. Set `enabled_handlers=[]` somewhere (disables heuristic)

But this is more fragmented and harder to understand. A single `skip_correction` config is clearer.
