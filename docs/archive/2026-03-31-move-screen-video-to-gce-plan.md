# Plan: Move Screen Video Generation to GCE Encoding Worker

**Created:** 2026-03-31
**Branch:** feat/sess-20260331-1619-investigate-render-failure
**Status:** Draft

## Overview

Screen video generation (title.mov, end.mov) currently runs FFmpeg in the Cloud Run API container via `VideoGenerator._create_video_from_image()`. After PR #640 right-sized Cloud Run to 2CPU/2Gi, the 4K H.264 encoding OOMs silently (producing 36-byte empty MOV shells). This breaks all downstream encoding since `encode_lossless_mp4` can't concatenate corrupt inputs.

Fix: Stop generating MOV in screens_worker. Generate them on the GCE encoding worker (which has adequate resources) just before the encoding step.

## Root Cause

- `VideoGenerator._create_video_from_image()` uses `os.system()` — silently swallows FFmpeg failures
- 4K H.264 encoding needs more than 2Gi RAM
- screens_worker checks `os.path.exists(output_video_filepath)` — the 36-byte shell passes this check
- All jobs since the Cloud Run right-sizing (March 31) are affected

## Implementation Steps

### 1. screens_worker.py — Stop generating MOV videos

**Changes:**
- In `_generate_title_screen`: pass `intro_video_duration=0` to skip MOV creation, return PNG path
- In `_generate_end_screen`: pass `end_video_duration=0` to skip MOV creation, return PNG path
- In `_upload_screens`: only upload PNG and JPG (remove MOV upload logic)
- Update function return types/docstrings to reflect image-only output

**Why duration=0 works:** `VideoGenerator._save_output_files` already has `if duration > 0:` guard around `_create_video_from_image`.

### 2. gce_encoding/main.py — Generate MOV from PNG before encoding

**Changes in `run_encoding()`:**
- After `find_file` for title/end videos, check if they're missing or invalid (< 1KB)
- If missing/invalid, look for corresponding PNG: `screens/title.png`, `screens/end.png`
- Generate MOV from PNG using `subprocess.run()` with proper error handling
- Use same FFmpeg command as `_create_video_from_image` but with error checking
- Log clearly when generating MOV from PNG vs using existing MOV

**Backwards compatible:** Old jobs with valid MOVs still work. New jobs with only PNGs get MOVs generated on GCE.

### 3. Fix `_create_video_from_image` error handling (defensive)

**Changes in `karaoke_gen/video_generator.py`:**
- Replace `os.system()` with `subprocess.run(check=True)`
- Log stderr on failure
- This protects the local CLI path too

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `backend/workers/screens_worker.py` | Modify | Stop generating MOV, return/upload images only |
| `backend/services/gce_encoding/main.py` | Modify | Generate MOV from PNG in `run_encoding()` |
| `karaoke_gen/video_generator.py` | Modify | Replace `os.system()` with `subprocess.run()` |

## Testing Strategy

- Unit tests for GCE worker MOV generation helper
- Verify existing encoding tests still pass
- Manually re-run job f0d51a0a after deploy to confirm fix

## Rollback Plan

Revert the 3 files. The old behavior (generating MOV in Cloud Run) would still fail with 2Gi, but increasing memory to 4Gi would restore it.
