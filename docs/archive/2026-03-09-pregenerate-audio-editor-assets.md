# Plan: Pre-generate Audio Editor Assets

**Created:** 2026-03-09
**Branch:** feat/sess-20260309-1749-pregenerate-audio-editor
**Status:** In Progress

## Overview

The audio editor UI takes 10-20 seconds to load because it generates waveform data and transcodes FLAC→OGG on-demand when the user opens the editor. The lyrics/instrumental review UI doesn't have this problem because `screens_worker` eagerly pre-generates all assets before the job reaches `awaiting_review`.

We need the same eager pre-generation for audio editing: transcode the input audio to OGG and generate waveform data *during* the download worker, before the job transitions to `awaiting_audio_edit`. Then the `input-audio-info` endpoint serves cached data instead of computing it from scratch.

## Current Flow (Slow)

```
audio_download_worker: downloads FLAC → saves to GCS → transitions to awaiting_audio_edit
                                                        (no pre-generation)

User opens editor → GET /input-audio-info →
  1. download FLAC from GCS to Cloud Run tmp      (~5-8s)
  2. generate waveform from FLAC (pydub)           (~2-3s)
  3. transcode FLAC→OGG + upload to GCS            (~3-5s)
  4. generate signed URL                           (~0.5s)
  Total: ~10-20s
```

## Target Flow (Fast)

```
audio_download_worker: downloads FLAC → saves to GCS →
  pre-generate:
    1. transcode FLAC→OGG (reuse AudioTranscodingService)
    2. generate waveform data → save as JSON to GCS
  → transitions to awaiting_audio_edit

User opens editor → GET /input-audio-info →
  1. load cached waveform JSON from GCS             (~0.5s)
  2. generate signed URL for cached OGG             (~0.5s)
  Total: ~1s
```

## Implementation Steps

1. [ ] Add `cache_waveform_data` and `load_cached_waveform` to `AudioAnalysisService`
2. [ ] Add pre-generation to `audio_download_worker` before `awaiting_audio_edit` transition
3. [ ] Modify `input-audio-info` endpoint to use cached waveform when available
4. [ ] Add unit tests for cache methods, download worker, and endpoint cache hit/miss
5. [ ] Run full test suite

## Files to Modify

| File | Description |
|------|-------------|
| `backend/services/audio_analysis_service.py` | Add waveform cache/load methods |
| `backend/workers/audio_download_worker.py` | Add pre-generation before state transition |
| `backend/api/routes/review.py` | Use cached waveform in `input-audio-info` |
| `backend/tests/test_audio_edit_routes.py` | Tests for cache hit/miss |
| `backend/tests/test_audio_download_worker.py` | Test for pre-generation call |
