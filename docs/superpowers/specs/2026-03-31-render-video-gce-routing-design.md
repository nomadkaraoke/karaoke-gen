# Route render_video to GCE Encoding Worker

**Date:** 2026-03-31
**Status:** Approved
**Branch:** feat/sess-20260331-1123-video-render-routing

## Problem

The `render_video_worker` runs OutputGenerator (ffmpeg/libass video rendering) on Cloud Run. After right-sizing Cloud Run to 2Gi memory, this OOMs. The GCE encoding worker handles final encoding (`/encode`) and preview encoding (`/encode-preview`), but render_video was never routed there — it was an intentional scope limitation from the original Jan 2026 PR #164, since render_video_worker predated the GCE encoding worker.

This also explains why karaoke jobs are slower than expected: the most resource-intensive rendering step runs on a constrained Cloud Run instance instead of the dedicated high-performance GCE VM.

## Solution

Add a `/render-video` endpoint to the GCE encoding worker and route render_video through `EncodingService`, following the same pattern as `/encode` and `/encode-preview`.

## Architecture

### GCE Worker: New `/render-video` endpoint

**Request model** (`RenderVideoRequest`):

| Field | Type | Description |
|-------|------|-------------|
| `job_id` | str | Job identifier |
| `original_corrections_gcs_path` | str | `gs://` path to corrections.json |
| `updated_corrections_gcs_path` | str (optional) | `gs://` path to corrections_updated.json |
| `audio_gcs_path` | str | `gs://` path to input audio |
| `style_params_gcs_path` | str (optional) | `gs://` path to style_params.json |
| `style_assets` | dict (optional) | Map of asset key → GCS path |
| `output_gcs_prefix` | str | `gs://` prefix for outputs (e.g. `gs://bucket/jobs/{id}/`) |
| `artist` | str | Artist name |
| `title` | str | Song title |
| `subtitle_offset_ms` | int | Subtitle timing offset (default 0) |
| `video_resolution` | str | Resolution (default "4k") |

**Processing** (`run_render_video()`):
1. `ensure_latest_wheel()` — hot code updates, same as `/encode`
2. Download inputs from GCS (with disk cache for style assets)
3. Load `CorrectionResult` from original corrections
4. Apply user corrections if `updated_corrections_gcs_path` provided (via `CorrectionOperations.update_correction_result_with_data`)
5. Run `CountdownProcessor` on correction result + audio
6. Load styles via `load_styles_from_gcs`
7. Configure and run `OutputGenerator.generate_outputs()`
8. Upload outputs to GCS:
   - `{output_gcs_prefix}videos/with_vocals.mkv`
   - `{output_gcs_prefix}lyrics/karaoke.ass`
   - `{output_gcs_prefix}lyrics/karaoke.lrc`
   - `{output_gcs_prefix}lyrics/corrected.txt`
9. Return output file paths + countdown metadata in job status

**Response:** Standard `JobStatus` model, with `output_files` listing GCS blob paths and `metadata` containing:
- `countdown_padding_added` (bool)
- `countdown_padding_seconds` (float)
- `render_duration_seconds` (float)

### Style Asset Disk Cache

To avoid repeated GCS downloads of the same default theme assets across jobs:

- **Location:** `/var/cache/karaoke-gen/{fonts,backgrounds,styles}/`
- **Cache key:** SHA-256 hash of the GCS path (deterministic, changes when asset path changes)
- **Lookup:** Before downloading, check if cached file exists; skip GCS download on hit
- **Scope:** Fonts, background images, style_params.json
- **No TTL needed:** GCS paths are versioned by theme ID — when a theme's assets change, the path changes, so the cache key changes naturally
- **Startup:** Create cache directories on worker boot

This is a simple wrapper around `download_single_file_from_gcs` — a `download_with_cache(gcs_uri, local_path, cache_dir)` helper that checks the cache first.

### EncodingService: New methods

```python
async def submit_render_video_job(self, job_id, render_config) -> dict:
    """POST to /render-video, with retry logic."""

async def render_video_on_gce(self, job_id, render_config, progress_callback=None) -> dict:
    """Submit + wait convenience method (like encode_videos)."""
```

Uses the same `_request_with_retry`, `wait_for_completion`, retry/backoff, and poll failure tolerance as existing methods.

### render_video_worker: Conditional routing

```python
encoding_service = get_encoding_service()

if encoding_service.is_enabled:
    # GCE path: build config from job data, delegate to GCE
    render_config = RenderVideoConfig(
        original_corrections_gcs_path=f"gs://{bucket}/jobs/{job_id}/lyrics/corrections.json",
        updated_corrections_gcs_path=...,  # if exists
        audio_gcs_path=f"gs://{bucket}/{job.input_media_gcs_path}",
        style_params_gcs_path=job.style_params_gcs_path,
        style_assets=job.style_assets,
        output_gcs_prefix=f"gs://{bucket}/jobs/{job_id}/",
        artist=job.artist,
        title=job.title,
        subtitle_offset_ms=job.subtitle_offset_ms or 0,
        video_resolution="4k",
    )
    result = await encoding_service.render_video_on_gce(job_id, render_config)
    # Parse result: update file_urls, lyrics_metadata from response
else:
    # Local fallback: existing OutputGenerator code (unchanged)
```

Cloud Run does zero downloads in the GCE path — it just reads job metadata from Firestore and constructs GCS paths.

### Configuration

No new env vars. Reuses `USE_GCE_ENCODING` — render_video is video work that follows the same routing decision.

## What doesn't change

- The job pipeline flow (states, transitions, triggering video_worker after)
- The local fallback path (existing code stays as-is for when GCE is disabled)
- OutputGenerator itself (same library, same wheel, same output)
- The GCE encoding worker's existing `/encode` and `/encode-preview` endpoints

## Risk: Code path divergence

Per LESSONS-LEARNED.md ("Alternative Code Paths Must Implement All Features"), both paths must produce identical output. This is inherently guaranteed because:
- Both use the same `OutputGenerator` from the karaoke-gen wheel
- Both use the same `load_styles_from_gcs` for style loading
- Both use the same `CorrectionOperations` for applying user corrections
- The GCE worker runs `ensure_latest_wheel()` to stay current

The only divergence risk is in the orchestration layer (Firestore updates, file URL construction), which stays on Cloud Run in both paths.

## Testing

- Unit tests for the new EncodingService methods (mock HTTP)
- Unit tests for render_video_worker GCE routing logic (mock encoding_service)
- Integration test: GCE worker `/render-video` endpoint with test fixtures
- E2E: existing happy-path CI test validates the full pipeline
