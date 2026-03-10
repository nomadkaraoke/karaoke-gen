# Plan: Store Job Processing Metadata Permanently

**Date**: 2026-03-10
**Branch**: `feat/sess-20260309-2358-store-audioshake-ids`
**Status**: Implemented (all 6 phases)

## Problem

During a data export for AudioShake (lyrics correction feedback), we discovered that AudioShake task_id and asset_id were only stored ephemerally during processing and never persisted. We had to do a complex one-off recovery from Cloud Logging + AudioShake API pagination to reconstruct the mapping.

This is symptomatic of a broader issue: many pieces of valuable metadata generated during job processing are only logged or held in memory, then discarded. When a future data export, debugging, or analytics need arises, we're left scrambling to recover from logs (which have limited retention).

## Goals

1. **Immediate**: Persist AudioShake task_id and asset_id permanently with each job
2. **Broader**: Identify and store other high-value processing metadata that would be painful to reconstruct later
3. **Principle**: "Store it when you have it" - the cost of storing a few extra fields is negligible compared to the cost of not having them later

## Design: `processing_metadata` Field

Add a new top-level dict field `processing_metadata` to the Job model, separate from `state_data` (which is mutable workflow state). `processing_metadata` is **write-once** data captured during processing that describes _how_ the job was processed.

### Why Not `state_data`?

- `state_data` is for mutable workflow state (current stems, build IDs, etc.)
- `processing_metadata` is for immutable audit/provenance data
- Keeps concerns separate: "what stage is the job in" vs "how was it processed"
- Easier to project in queries (one field to include vs picking through state_data keys)

### Schema

```python
# In Job model
processing_metadata: Dict[str, Any] = Field(default_factory=dict)
"""
Immutable processing provenance data. Written once by each worker during processing.
Never modified after initial write. Used for analytics, debugging, and data export.

Structure:
{
    "transcription": {
        "provider": "audioshake",
        "audioshake_task_id": "cmmjpbo7f...",
        "audioshake_asset_id": "cmmjpbma4...",
        "language_detected": "en",
        "duration_seconds": 125.5,
        "word_count": 358,
        "segment_count": 42,
    },
    "correction": {
        "handlers_applied": ["anchor_sequence", "gap_word_count", ...],
        "corrections_made": 12,
        "correction_ratio": 0.034,
        "reference_sources_found": ["genius", "spotify", "lrclib"],
        "agentic_routing": "rule-based",
        "duration_seconds": 33.2,
    },
    "separation": {
        "provider": "modal",
        "clean_model": "model_bs_roformer_ep_317_sdr_12.9755.ckpt",
        "backing_models": ["mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt"],
        "stems_generated": ["clean", "with_backing", "vocals", "backing_vocals"],
        "duration_seconds": 420.5,
    },
    "audio_source": {
        "input_format": "flac",
        "input_sample_rate": 44100,
        "input_channels": 2,
        "input_duration_seconds": 210.4,
        "input_file_size_bytes": 26541900,
    },
    "rendering": {
        "render_duration_seconds": 18.7,
        "encoding_duration_seconds": 180.5,
        "output_formats": ["lossless_4k_mkv", "lossless_4k_mp4", "lossy_4k_mp4", "lossy_720p_mp4"],
        "output_sizes_bytes": {
            "lossless_4k_mkv": 45168200,
            "lossy_720p_mp4": 34571800,
        },
    },
    "review": {
        "review_duration_seconds": 450.2,
        "corrections_made_by_human": 5,
        "total_words_reviewed": 358,
        "instrumental_selected": "clean",
    },
    "distribution": {
        "youtube_video_id": "dQw4w9WgXcQ",
        "youtube_upload_duration_seconds": 240.5,
        "dropbox_upload_duration_seconds": 120.5,
        "gdrive_upload_duration_seconds": 95.3,
    },
    "cache": {
        "transcription_cache_hit": true,
        "lyrics_cache_hits": {"genius": true, "spotify": true, "lrclib": false},
    },
    "timing": {
        "total_processing_seconds": 890.2,
        "worker_durations": {
            "audio": 420.5,
            "lyrics": 125.3,
            "screens": 45.2,
            "render_video": 18.7,
            "video": 180.2,
        },
    },
}
```

### Field Projection

Per the known gotcha (MEMORY.md), when adding new fields the dashboard needs, must add to BOTH:
1. `SUMMARY_FIELD_PATHS` in `backend/services/firestore_service.py`
2. `_SUMMARY_STATE_DATA_KEYS` in `backend/api/routes/jobs.py`

For `processing_metadata`, we'll add it to `SUMMARY_FIELD_PATHS` so it's available in job list queries. Since it's a top-level field (not under `state_data`), it doesn't need `_SUMMARY_STATE_DATA_KEYS`.

## Implementation Plan

### Phase 1: AudioShake IDs (Priority - the original ask)

**Files to modify:**

1. **`backend/models/job.py`** - Add `processing_metadata` field to Job model
2. **`karaoke_gen/lyrics_transcriber/core/controller.py`** - Pass transcription metadata (including task_id) through to CorrectionResult.metadata
3. **`backend/workers/lyrics_worker.py`** - Extract AudioShake IDs + transcription metadata from corrections.json and store in `processing_metadata.transcription`
4. **`backend/services/firestore_service.py`** - Add `processing_metadata` to `SUMMARY_FIELD_PATHS`
5. **`backend/api/routes/jobs.py`** - Ensure `processing_metadata` is included in job responses

**Detail:**

In `controller.py`, the `_convert_result_format` method on AudioShakeTranscriber already stores `task_id` in `TranscriptionData.metadata`. The issue is that `LyricsCorrector.run()` builds a new `CorrectionResult.metadata` dict that doesn't include this data. Fix:
- In `corrector.py`, pass through the primary transcription's metadata into the CorrectionResult metadata dict
- In `lyrics_worker.py`, after loading corrections.json, extract `metadata.transcription_metadata` and write to `processing_metadata`

Alternatively (simpler): in `lyrics_worker.py`, we already have access to the transcription result object. We can extract the AudioShake IDs directly from it before they're lost, and store them in `processing_metadata`.

**Chosen approach**: Store in `lyrics_worker.py` right after transcription completes, since we have direct access to the result there. Also flow the metadata through corrections.json for data integrity.

### Phase 2: Worker Timing & Processing Stats

**Files to modify:**

6. **`backend/workers/lyrics_worker.py`** - Add timing data + correction stats to `processing_metadata`
7. **`backend/workers/audio_worker.py`** - Add separation timing + model info to `processing_metadata`
8. **`backend/workers/screens_worker.py`** - Add screen generation timing
9. **`backend/workers/render_video_worker.py`** - Add render timing
10. **`backend/workers/video_worker.py`** / **`video_worker_orchestrator.py`** - Add encoding timing + output file sizes

**For each worker, the pattern is:**
```python
# At the end of successful processing:
job_manager.update_processing_metadata(job_id, 'separation', {
    'provider': 'modal',
    'clean_model': model_names.get('clean'),
    'duration_seconds': round(sep_duration, 1),
    ...
})
```

### Phase 3: Audio Source & Cache Metadata

**Files to modify:**

11. **`backend/workers/lyrics_worker.py`** - Cache hit/miss stats
12. **`backend/api/routes/jobs.py`** or audio download handler - Input audio metadata (format, sample rate, duration, file size)

### Phase 4: Review & Distribution Metadata

**Files to modify:**

13. **`backend/api/routes/review.py`** - Review completion metadata (duration, corrections count)
14. **`backend/workers/video_worker.py`** - YouTube upload metadata (video ID is probably already stored, but upload timing is not)

### JobManager Helper

Add a helper method to JobManager for appending to `processing_metadata` without overwriting:

```python
def update_processing_metadata(self, job_id: str, section: str, data: dict):
    """Merge data into processing_metadata[section]. Write-once per section."""
    from google.cloud.firestore_v1 import transforms
    self.db.collection("jobs").document(job_id).update({
        f"processing_metadata.{section}": data,
    })
```

## Testing Strategy

### Unit Tests
- **`backend/tests/test_job_model.py`**: Verify `processing_metadata` field exists and defaults to empty dict
- **`backend/tests/test_lyrics_worker.py`**: Mock transcription result with AudioShake metadata, verify it's stored in `processing_metadata.transcription`
- **`backend/tests/test_audio_worker.py`**: Verify separation metadata stored
- **`backend/tests/test_job_manager.py`**: Test `update_processing_metadata` helper

### Integration Tests
- **`backend/tests/integration/`**: Firestore emulator test that processes a job through lyrics worker and verifies `processing_metadata` is written correctly

### Regression Test
- Add a test asserting `processing_metadata` is in `SUMMARY_FIELD_PATHS` (per the known gotcha)

## Migration

No migration needed for existing jobs - `processing_metadata` defaults to `{}`. New jobs will populate it as they process. The extraction script (`extract_audioshake_corrections.py`) already handles the one-off backfill for historical AudioShake IDs.

### Phase 5: Input Audio Metadata (ffprobe at ingest)

Currently we have ffprobe and pydub available in workers, but never store audio properties permanently. When a user uploads or we download audio, run ffprobe once and store the results.

**Files to modify:**

15. **`backend/workers/audio_worker.py`** (or wherever audio is first downloaded to disk) - Run ffprobe on the input audio file and store:

```json
"audio_source": {
    "input_format": "flac",
    "input_codec": "flac",
    "input_sample_rate": 44100,
    "input_bit_depth": 16,
    "input_channels": 2,
    "input_duration_seconds": 210.4,
    "input_file_size_bytes": 26541900,
    "input_bitrate_kbps": 1411
}
```

This is valuable for:
- Understanding what quality of source material produces the best results
- Debugging encoding/quality issues
- Analytics on user upload patterns
- Detecting re-encoded or low-quality uploads

### Phase 6: User & Abuse-Related Metadata

**Current state**: `request_metadata` already captures IP, User-Agent, client_id, and custom headers at job creation time. This is good for basic abuse investigation. However, some gaps:

**6a. Auth context at job creation**

Store which auth method was used and user tier, so we can correlate job creation patterns with account types during abuse investigation.

16. **`backend/api/routes/file_upload.py`** - Add to `extract_request_metadata()`:

```python
# Add auth context (already available from auth_result)
request_metadata['auth_method'] = auth_result.auth_method  # "session", "api_key", "admin_token"
request_metadata['user_type'] = auth_result.user_type.value  # "admin", "unlimited", "limited", etc.
request_metadata['credits_at_creation'] = auth_result.remaining_uses  # snapshot of credits when job created
```

**6b. Audio search metadata (for audio search flow jobs)**

When a job is created from audio search, we know the search query, number of results, and which result was selected. Store these so we can understand search patterns and detect gaming.

17. **`backend/api/routes/audio_search.py`** - When creating a job from search, store:

```json
"request_metadata": {
    ...existing fields...,
    "search_query": {"artist": "...", "title": "..."},
    "search_results_count": 8,
    "selected_result_rank": 1,
    "selected_source": "RED"
}
```

**Not adding (already handled):**
- IP address: Already in `request_metadata.client_ip`
- User-Agent: Already in `request_metadata.user_agent`
- Email: Already in `job.user_email`
- Tenant: Already in `job.tenant_id`
- IP blocklist: Already exists in `blocklists/config`
- Session IP/UA: Already stored in session documents
- Magic link IP/UA: Already stored in magic link documents

## Scope & Boundaries

- **In scope**: Storing metadata that already exists in memory during processing
- **Out of scope**: Computing new metrics, changing external API calls, frontend changes
- **Out of scope**: Backfilling historical jobs (the one-off script handles AudioShake IDs)
- **Principle**: Each worker writes its own section. No worker reads another worker's metadata.

## Estimated Changes

| Phase | Files | Complexity | Notes |
|-------|-------|-----------|-------|
| 1 | 5 | Low | AudioShake IDs - the critical fix |
| 2 | 5 | Low | Timing data - already computed, just needs storing |
| 3 | 2 | Low | Source/cache metadata |
| 4 | 2 | Low | Review/distribution metadata |
| 5 | 1-2 | Low | ffprobe on input audio, store properties |
| 6 | 2 | Low | Auth context + search metadata at job creation |
| Tests | 4-5 | Medium | Unit + integration tests |

Total: ~18 files, mostly small additions (5-15 lines per file).
