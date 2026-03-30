# Plan: Fix 413 Audio Separation Error via GCS Path Passthrough

**Created:** 2026-03-25
**Branch:** feat/sess-20260325-1130-fix-413-audio-sep
**Status:** Draft

## Overview

Audio separation fails with `413 Request Entity Too Large` when FLAC files exceed Cloud Run's 32MB request body limit. The current architecture uploads the entire audio file as a multipart POST body to the separator API. Since the file is **already in GCS** by the time the audio worker runs, we should pass the GCS path and let the separator download directly from GCS.

**Root cause:** `api_client.py:77,136` sends the file as `files={"file": (filename, open(file_path, "rb"))}` in a POST to `/separate`. Cloud Run rejects bodies > 32MB.

## Requirements

- [ ] Audio separation works for files of any size (no HTTP body limit)
- [ ] Backward compatible — file upload still works for non-GCS callers (CLI, tests)
- [ ] No data duplication — separator reads from the same GCS bucket, doesn't copy
- [ ] Stage 2 (backing vocals) still works — uses Stage 1 output which is local on the separator

## Technical Approach

**Change:** Add a `gcs_uri` parameter to the `/separate` endpoint. When provided, the separator downloads from GCS instead of expecting a file upload. The API client gains a new parameter for this.

Both services are in the same GCP project (`nomadkaraoke`), so the separator's service account just needs `roles/storage.objectViewer` on the `karaoke-gen-storage` bucket.

### Flow: Before
```
audio_worker → download from GCS → local file → HTTP POST (file bytes) → separator
```

### Flow: After
```
audio_worker → pass GCS path → separator downloads from GCS directly
```

Stage 2 (backing vocals) still uploads the local mixed_vocals file since it's a Stage 1 output that only exists on the separator's local disk. But Stage 2 files are small (vocals only), so no 413 risk.

## Implementation Steps

### Repo 1: python-audio-separator (separate worktree)

1. [ ] **Server: Add `gcs_uri` parameter to `/separate` endpoint** (`audio_separator/remote/deploy_cloudrun.py`)
   - Add `gcs_uri: Optional[str] = Form(None)` parameter
   - When provided, download from GCS instead of reading `file` upload
   - Make `file` parameter optional (required only when `gcs_uri` not provided)
   - Validate: exactly one of `file` or `gcs_uri` must be provided

2. [ ] **Server: Add GCS download helper** (`audio_separator/remote/deploy_cloudrun.py`)
   - `download_from_gcs(gcs_uri: str, output_dir: str) -> tuple[bytes, str]`
   - Parse `gs://bucket/path` or just `bucket/path` format
   - Use `google-cloud-storage` (already a dependency for model download)
   - Return `(audio_bytes, filename)`

3. [ ] **Client: Add `gcs_uri` parameter** (`audio_separator/remote/api_client.py`)
   - Add `gcs_uri` param to `separate_audio()` and `separate_audio_and_wait()`
   - When `gcs_uri` is provided, send it as form data instead of file upload
   - When `gcs_uri` is provided, `file_path` becomes optional (not opened/sent)

4. [ ] **Tests: Add unit tests for GCS passthrough**
   - Test server accepts `gcs_uri` parameter
   - Test client sends `gcs_uri` instead of file when provided
   - Test validation (can't provide both `file` and `gcs_uri`, must provide one)

5. [ ] **Bump version, push, publish**

### Repo 2: karaoke-gen (this worktree)

6. [ ] **Infrastructure: Grant GCS read access to separator SA** (`infrastructure/modules/audio_separator_service.py`)
   - Add `roles/storage.objectViewer` on `karaoke-gen-storage` bucket to the `audio-separator` service account

7. [ ] **Audio worker: Pass GCS path to separator** (`karaoke_gen/audio_processor.py`)
   - In `_process_audio_separation_remote()`, pass `gcs_uri` for Stage 1
   - Construct URI: `gs://karaoke-gen-storage/{job.input_media_gcs_path}`
   - Stage 2 continues using local file upload (the mixed_vocals file is on separator's disk, not in GCS)

8. [ ] **Update audio-separator dependency** (`pyproject.toml`)
   - `poetry update audio-separator` after the new version is published

9. [ ] **Tests**
   - Update existing audio processor tests to verify `gcs_uri` is passed
   - Test that Stage 1 uses GCS path, Stage 2 uses file upload

## Files to Create/Modify

### python-audio-separator
| File | Action | Description |
|------|--------|-------------|
| `audio_separator/remote/deploy_cloudrun.py` | Modify | Add `gcs_uri` param to `/separate`, add GCS download helper |
| `audio_separator/remote/api_client.py` | Modify | Add `gcs_uri` param to client methods |
| `tests/unit/test_remote_api_client.py` | Modify | Add tests for GCS passthrough |
| `pyproject.toml` | Modify | Bump version |

### karaoke-gen
| File | Action | Description |
|------|--------|-------------|
| `infrastructure/modules/audio_separator_service.py` | Modify | Add GCS read permission |
| `karaoke_gen/audio_processor.py` | Modify | Pass `gcs_uri` in Stage 1 |
| `backend/workers/audio_worker.py` | Modify | Pass GCS path to audio processor |
| `pyproject.toml` | Modify | Update audio-separator dependency |

## Testing Strategy

- **Unit tests:** Mock GCS client in separator, verify download_from_gcs works
- **Unit tests:** Verify API client sends gcs_uri param correctly
- **Integration test:** Deploy separator locally, test with a real GCS file
- **Production test:** Re-run failed job `45f9bbc0` after deployment

## Open Questions

- [ ] Should we also upload Stage 2 input (mixed_vocals) to GCS and use gcs_uri? Not needed now — vocals-only files are small — but would be more consistent.
- [ ] Should the `gcs_uri` format be `gs://bucket/path` or just the blob path with a separate bucket param? Suggest `gs://` URI for clarity and flexibility.

## Rollback Plan

- Revert to file upload by not passing `gcs_uri` — the parameter is optional
- The separator still accepts file uploads as before (backward compatible)
- No infrastructure changes needed for rollback (extra IAM permission is harmless)

## Execution Order

1. Start with python-audio-separator (steps 1-5) — this is the dependency
2. Apply infra change (step 6) — can be done in parallel
3. Update karaoke-gen (steps 7-9) — depends on step 5
