# Plan: Fix YouTube Remote Download Missing GCS Path

**Created:** 2026-01-20
**Branch:** feat/sess-20260120-1756-fix-youtube-remote-download
**Status:** Complete

## Overview

When remote flacfetch is enabled and a user selects a YouTube audio source, the backend calls `download_by_id()` without the `gcs_path` parameter. This causes flacfetch to download the file locally on the VM instead of uploading to GCS. The backend then tries to open the VM's local path, which doesn't exist on Cloud Run, causing the job to fail.

This is the same class of bug that was fixed on 2025-12-22 for RED/OPS torrent sources (see `docs/archive/2025-12-phase1-testing-gaps.md`), but the fix only covered torrent sources, not YouTube.

## Root Cause

In `backend/api/routes/audio_search.py` lines 354-363:

```python
# Use download_by_id if we have source_id and remote is enabled
if source_id and source_name and is_remote_enabled:
    logger.info(f"Using download_by_id for local download: {source_name} ID={source_id}")

    result = audio_search_service.download_by_id(
        source_name=source_name,
        source_id=source_id,
        output_dir=temp_dir,  # <-- This is a local temp dir, but flacfetch runs on VM!
        target_file=target_file,
        download_url=download_url,
        # <-- MISSING: gcs_path parameter
    )
```

When `is_remote_enabled=True`, `download_by_id()` routes to the remote flacfetch VM. Without `gcs_path`, the VM downloads locally and returns a local path like `/var/lib/transmission-daemon/downloads/...` which doesn't exist on Cloud Run.

## Requirements

- [x] YouTube downloads with remote flacfetch should upload directly to GCS
- [x] Backend should receive a `gs://` path from flacfetch
- [x] No changes to flacfetch itself (it already supports `upload_to_gcs` and `gcs_destination`)
- [x] Maintain backwards compatibility (local-only deployments should still work)
- [x] Add test coverage for this code path

## Technical Approach

**Option A (chosen):** Pass `gcs_path` to `download_by_id()` for ALL remote downloads, not just torrent sources.

The current code structure has two branches:
1. `if is_torrent_source and is_remote_enabled:` - handles RED/OPS with GCS upload ✅
2. `else:` branch - handles YouTube, but incorrectly assumes "local download" even when remote is enabled

The fix refactors to check `is_remote_enabled` first, then handle torrent vs YouTube within that:
- If remote enabled: always pass `gcs_path` to let flacfetch upload directly
- If remote disabled: download locally and upload to GCS from Cloud Run

## Implementation Steps

1. [x] Refactor the `else` branch in `_download_and_start_processing()` to check for remote-enabled YouTube downloads
2. [x] Pass `gcs_path` when calling `download_by_id()` for remote YouTube downloads
3. [x] Add test case for YouTube with remote enabled to verify `gcs_path` is passed
4. [x] Update logging to clarify the download path being used
5. [x] Bump version in `pyproject.toml`
6. [x] Run tests to verify no regressions

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `backend/api/routes/audio_search.py` | Modify | Add `gcs_path` parameter for remote YouTube downloads |
| `backend/tests/test_audio_search.py` | Modify | Add test case for YouTube remote download with GCS path |
| `pyproject.toml` | Modify | Bump version |

## Detailed Code Changes

### audio_search.py (lines 349-393)

Current structure:
```python
else:
    # Local download (YouTube or fallback)  <-- WRONG: not always local!
    temp_dir = tempfile.mkdtemp(...)

    if source_id and source_name and is_remote_enabled:
        result = audio_search_service.download_by_id(
            ...
            output_dir=temp_dir,  # <-- BUG: unused for remote
            # Missing gcs_path!
        )
```

Fixed structure:
```python
else:
    # Non-torrent download (YouTube or fallback)

    if is_remote_enabled and source_id and source_name:
        # Remote download via flacfetch VM - upload directly to GCS
        gcs_destination = f"uploads/{job_id}/audio/"
        logger.info(f"Using remote download_by_id for {source_name} ID={source_id} with GCS upload to: {gcs_destination}")

        result = audio_search_service.download_by_id(
            source_name=source_name,
            source_id=source_id,
            output_dir="",  # Not used for remote
            target_file=target_file,
            download_url=download_url,
            gcs_path=gcs_destination,  # <-- FIX: Add this!
        )

        # Handle GCS path response (same logic as torrent branch)
        if result.filepath.startswith("gs://"):
            parts = result.filepath.replace("gs://", "").split("/", 1)
            if len(parts) == 2:
                audio_gcs_path = parts[1]
            else:
                audio_gcs_path = result.filepath
            filename = os.path.basename(result.filepath)
        else:
            # Fallback: upload manually
            filename = os.path.basename(result.filepath)
            audio_gcs_path = f"uploads/{job_id}/audio/{filename}"
            logger.warning(f"Remote download returned local path: {result.filepath}, uploading manually")
            with open(result.filepath, 'rb') as f:
                storage_service.upload_fileobj(f, audio_gcs_path, content_type='audio/flac')

        logger.info(f"Remote download complete, GCS path: {audio_gcs_path}")
    else:
        # Local download (remote disabled or no source_id)
        temp_dir = tempfile.mkdtemp(prefix=f"audio_download_{job_id}_")
        # ... existing local download logic ...
```

## Testing Strategy

### Unit Tests
Add to `test_audio_search.py` in `TestAudioSearchApiRouteDownload`:

```python
def test_youtube_remote_download_passes_gcs_path(self, mock_job_manager, mock_storage_service):
    """
    Test that YouTube downloads via remote flacfetch include GCS path.

    This is the same bug pattern as the torrent download bug fixed in 2025-12:
    when remote is enabled, we must pass gcs_path so flacfetch uploads to GCS.
    """
    # Setup: YouTube source with remote enabled
    # Verify: download_by_id called with gcs_path parameter
```

### Manual Testing (post-deploy)
1. Create a job and select a YouTube audio source
2. Verify job completes successfully
3. Check GCS path is properly set in job data

## Open Questions

None - the fix is straightforward and follows the pattern established for torrent sources.

## Rollback Plan

This is a minor code change. If issues occur:
1. Revert the PR
2. The failure mode returns to the current state (YouTube downloads fail when remote is enabled)

## Related Documentation

- `docs/archive/2025-12-phase1-testing-gaps.md` - Original bug fix for torrent sources
- `docs/LESSONS-LEARNED.md` - "Fix Both Sides of Dual Code Paths" pattern
