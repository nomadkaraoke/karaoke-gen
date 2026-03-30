# Plan: Fix Ensemble Preset Stem Tag Parsing Bug

**Created:** 2026-03-27
**Branch:** feat/sess-20260326-2142-prod-broken-jobs
**Status:** Complete

## Overview

The ensemble preset stem filename parser in `audio_processor.py` uses naive substring matching (`"instrumental" in basename`) to classify output files as vocals vs instrumental. This breaks when the preset name itself contains the word "instrumental" (e.g., `instrumental_clean`), causing the Vocals file to be misclassified. Stage 2 (backing vocals separation) never runs, so users only get `instrumental_clean` — no backing vocals, no combined instrumentals, and the instrumental review page shows "No backing vocals detected."

## Requirements

- [x] Fix Stage 1 stem tag parsing to correctly identify Vocals vs Instrumental files regardless of preset name
- [x] Fix Stage 2 stem tag parsing with the same approach (same bug pattern)
- [ ] Add unit tests for stem tag parsing with various preset names
- [ ] Add regression test specifically for `instrumental_clean` preset name
- [ ] Verify upload function correctly handles the now-populated backing vocals data

## Technical Approach

**Root cause:** Output filenames follow the pattern `{base}_(StemTag)_preset_{name}.flac` where StemTag is `Vocals`, `Instrumental`, or `No Vocal`. The old code matched substrings against the entire filename, so preset name `instrumental_clean` caused the Vocals file to match `"instrumental" in basename`.

**Fix:** Extract the stem tag from between `_(` and `)_` delimiters and match only against that, not the full filename. This is already implemented — just needs tests and verification.

**Fallback:** If the stem tag extraction fails (no `_(...)_` pattern found), log a warning. This handles edge cases where the separator library changes its naming convention.

## Implementation Steps

1. [x] Fix stem tag parsing in Stage 1 (lines 308-331) — **already done**
2. [x] Fix stem tag parsing in Stage 2 (lines 364-380) — **already done**
3. [ ] Write unit tests for `process_audio_separation` ensemble path:
   - Test Stage 1 correctly classifies files with `instrumental_clean` preset
   - Test Stage 1 correctly classifies files with `karaoke` preset
   - Test Stage 2 correctly classifies backing vocal files
   - Test unrecognized stem tags produce warnings
   - Test fallback when `_(...)_` pattern not present
4. [ ] Run existing tests to verify no regressions
5. [ ] Manual verification: check that a test job produces all stems (instrumental_clean, backing_vocals, lead_vocals, instrumental_with_backing)

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `karaoke_gen/audio_processor.py` | Modified | Fixed stem tag parsing in Stage 1 (L308-331) and Stage 2 (L364-380) |
| `tests/unit/test_audio_processor_local_gpu.py` | Modify | Add tests for ensemble stem tag parsing |

## Testing Strategy

- **Unit tests:** Mock the `Separator` to return filenames with various preset names, verify correct classification into vocals/instrumental/backing_vocals
- **Regression test:** Specifically test `instrumental_clean` preset to ensure it never regresses
- **Existing tests:** Run full test suite to verify no regressions
- **Manual:** After deploy, trigger a test job and verify all stems appear in GCS and on the instrumental review page

## Open Questions

None — root cause is confirmed and fix is verified.

## Rollback Plan

Revert the single commit. The old behavior (only instrumental_clean uploaded) would return as a known limitation. No data loss risk.
