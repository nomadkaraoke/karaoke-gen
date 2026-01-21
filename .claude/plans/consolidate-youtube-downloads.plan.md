# Plan: Consolidate YouTube Download Paths

**Created:** 2026-01-20
**Status:** Planning

## Problem Statement

YouTube downloads currently have **3 different code paths** with different behaviors:

1. **Audio Search → Select YouTube** (`audio_search.py`)
   - Can use remote flacfetch, local flacfetch, OR direct `download_from_url()`
   - Multiple conditional branches with subtle bugs (e.g., `source_id` extraction)

2. **Direct URL Submission** (`file_upload.py` → `audio_worker.py`)
   - Always uses `download_from_url()` → `FileHandler.download_video()` → yt_dlp
   - Runs on Cloud Run, blocked by YouTube bot detection
   - Never uses remote flacfetch even when configured

3. **Local CLI** (`karaoke_gen/audio_fetcher.py`)
   - Uses `FlacFetcher.download_from_url()` → local yt_dlp
   - Works fine locally, not relevant to cloud issues

### Current Issues

1. **Direct URL path ignores remote flacfetch** - Users submitting YouTube URLs directly hit bot detection because Cloud Run IPs are blocked
2. **Multiple implementations** - `FileHandler.download_video()`, `download_from_url()`, `FlacFetcher.download_from_url()` all do similar things
3. **Inconsistent error handling** - Different paths handle errors differently
4. **Testing gaps** - No integration tests verifying the full flow
5. **Dead/confusing code** - Fallback paths that "may fail on multi-instance deployments"

## Goals

1. **Single source of truth** - ONE function handles YouTube downloads for the cloud backend
2. **Always use remote flacfetch** when configured - All YouTube downloads should go through the flacfetch VM
3. **Graceful fallback** - When remote is disabled, use local download with clear error messaging
4. **Comprehensive testing** - Unit tests for each component, integration tests for full flow
5. **Remove dead code** - Clean up redundant implementations

## Proposed Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Entry Points                                 │
├──────────────────────┬──────────────────────┬───────────────────────┤
│  Audio Search Select │   Direct URL Submit  │     File Upload       │
│  (audio_search.py)   │   (file_upload.py)   │   (file_upload.py)    │
└──────────┬───────────┴──────────┬───────────┴───────────┬───────────┘
           │                      │                       │
           ▼                      ▼                       │
┌──────────────────────────────────────────────┐          │
│           YouTubeDownloadService             │          │
│  (NEW: backend/services/youtube_service.py)  │          │
│                                              │          │
│  - download_by_url(url, job_id) → gcs_path   │          │
│  - download_by_id(video_id, job_id) → gcs_path│         │
│                                              │          │
│  Internally:                                 │          │
│  1. Check if remote flacfetch enabled        │          │
│  2. If yes: Use FlacfetchClient.download_by_id│         │
│  3. If no: Use local yt_dlp with clear error │          │
│     messaging about bot detection risk       │          │
└──────────────────┬───────────────────────────┘          │
                   │                                      │
                   ▼                                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                              GCS                                     │
│                    uploads/{job_id}/audio/                          │
└─────────────────────────────────────────────────────────────────────┘
```

## Implementation Steps

### Phase 1: Create YouTubeDownloadService

**File:** `backend/services/youtube_download_service.py`

```python
class YouTubeDownloadService:
    """
    Single point of entry for all YouTube downloads in the cloud backend.

    When remote flacfetch is configured (FLACFETCH_API_URL), downloads happen
    on the flacfetch VM which has YouTube cookies and avoids bot detection.

    When remote is not configured, falls back to local yt_dlp with a warning
    that downloads may be blocked.
    """

    def __init__(self):
        self._flacfetch_client = get_flacfetch_client()

    async def download(
        self,
        url: str,
        job_id: str,
        artist: Optional[str] = None,
        title: Optional[str] = None,
    ) -> str:
        """
        Download YouTube audio and upload to GCS.

        Args:
            url: YouTube URL (any format - watch, youtu.be, shorts, etc.)
            job_id: Job ID for GCS path
            artist: Optional artist name for filename
            title: Optional title for filename

        Returns:
            GCS path (not gs:// prefix, just the path portion)

        Raises:
            YouTubeDownloadError: If download fails
        """
        video_id = self._extract_video_id(url)

        if self._flacfetch_client:
            return await self._download_remote(video_id, job_id, artist, title)
        else:
            logger.warning(
                "Remote flacfetch not configured. Attempting local download, "
                "but this may fail due to YouTube bot detection on Cloud Run IPs."
            )
            return await self._download_local(url, job_id, artist, title)

    async def download_by_id(
        self,
        video_id: str,
        job_id: str,
        artist: Optional[str] = None,
        title: Optional[str] = None,
    ) -> str:
        """Download by video ID instead of URL."""
        url = f"https://www.youtube.com/watch?v={video_id}"
        return await self.download(url, job_id, artist, title)
```

### Phase 2: Refactor Audio Search Path

**File:** `backend/api/routes/audio_search.py`

Changes to `_download_and_start_processing()`:

```python
# BEFORE: Complex branching with multiple paths
if is_torrent_source and is_remote_enabled:
    # Torrent path...
elif is_remote_enabled and source_id and source_name:
    # YouTube remote path (buggy source_id extraction)...
else:
    # Local path with another YouTube branch...

# AFTER: Clean separation by source type
if is_torrent_source:
    result = await _download_torrent(source_name, source_id, target_file, job_id)
elif source_name == 'YouTube':
    result = await youtube_service.download_by_id(source_id, job_id, artist, title)
elif source_name == 'Spotify':
    result = await _download_spotify(source_id, job_id, artist, title)
else:
    raise DownloadError(f"Unknown source: {source_name}")
```

### Phase 3: Refactor Direct URL Path

**File:** `backend/api/routes/file_upload.py`

Changes to `create_job_from_url()`:

```python
# BEFORE: Creates job and triggers audio_worker which uses download_from_url()
# This always runs on Cloud Run and gets blocked

# AFTER: If YouTube URL, download immediately using YouTubeDownloadService
if is_youtube_url(body.url):
    youtube_service = get_youtube_download_service()
    gcs_path = await youtube_service.download(body.url, job_id, body.artist, body.title)
    # Update job with audio already downloaded
    job.input_media_gcs_path = gcs_path
    # Skip audio download in worker
```

**File:** `backend/workers/audio_worker.py`

Changes to `download_audio()`:

```python
# BEFORE: Has YouTube download logic with download_from_url()
# AFTER: Remove YouTube-specific logic - it's handled before worker runs

async def download_audio(job_id: str, job: Job) -> str:
    """Download audio from GCS (already uploaded by service layer)."""
    if not job.input_media_gcs_path:
        raise Exception("No audio path - this shouldn't happen for URL jobs")

    # Download from GCS to local for processing
    local_path = f"/tmp/{job_id}/audio/{os.path.basename(job.input_media_gcs_path)}"
    storage_service.download_file(job.input_media_gcs_path, local_path)
    return local_path
```

### Phase 4: Clean Up Dead Code

Remove or consolidate:

1. **`backend/workers/audio_worker.py:download_from_url()`** - Move to youtube_service or delete
2. **`karaoke_gen/file_handler.py:FileHandler.download_video()`** - Keep for CLI, but backend doesn't use it
3. **Multiple fallback paths in audio_search.py** - Simplify to single path per source type
4. **`_trigger_lyrics_worker_after_url_download()`** - Inline or remove if no longer needed

### Phase 5: Comprehensive Testing

#### Unit Tests (`backend/tests/test_youtube_download_service.py`)

```python
class TestYouTubeDownloadService:
    def test_download_uses_remote_when_configured(self):
        """When FLACFETCH_API_URL is set, should use remote client."""

    def test_download_falls_back_to_local_when_remote_disabled(self):
        """When remote not configured, should use local yt_dlp."""

    def test_download_extracts_video_id_from_various_url_formats(self):
        """Should handle youtube.com, youtu.be, shorts, etc."""

    def test_download_returns_correct_gcs_path_format(self):
        """Should return path without gs:// prefix."""

    def test_download_handles_remote_failure_gracefully(self):
        """If remote fails, should raise clear error, not fall back silently."""
```

#### Integration Tests (`backend/tests/integration/test_youtube_flow.py`)

```python
class TestYouTubeDownloadFlow:
    @pytest.mark.integration
    async def test_audio_search_youtube_selection_downloads_correctly(self):
        """Full flow: search → select YouTube → verify GCS upload."""

    @pytest.mark.integration
    async def test_direct_url_submission_downloads_correctly(self):
        """Full flow: submit YouTube URL → verify GCS upload."""

    @pytest.mark.integration
    async def test_youtube_download_with_remote_flacfetch(self):
        """Verify remote flacfetch integration works end-to-end."""
```

#### Flacfetch Tests (in flacfetch repo)

```python
class TestYouTubeDownloadByIdEndpoint:
    def test_download_by_id_with_youtube_source(self):
        """POST /download-by-id with source_name=YouTube works."""

    def test_download_by_id_uploads_to_gcs_when_requested(self):
        """When gcs_path provided, should upload and return gs:// path."""

    def test_download_by_id_handles_bot_detection_error(self):
        """Should return clear error if YouTube blocks the request."""
```

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `backend/services/youtube_download_service.py` | Create | New consolidated YouTube download service |
| `backend/api/routes/audio_search.py` | Modify | Use YouTubeDownloadService, remove complex branching |
| `backend/api/routes/file_upload.py` | Modify | Download YouTube before creating job |
| `backend/workers/audio_worker.py` | Modify | Remove YouTube download logic |
| `backend/services/audio_search_service.py` | Modify | Fix source_id extraction (quick fix already done) |
| `backend/tests/test_youtube_download_service.py` | Create | Unit tests for new service |
| `backend/tests/integration/test_youtube_flow.py` | Create | Integration tests |

## Migration Strategy

1. **Phase 1-2** can be deployed independently - new service + audio search refactor
2. **Phase 3** changes the URL submission flow - may need careful rollout
3. **Phase 4** cleanup can happen after Phase 1-3 are stable
4. **Phase 5** tests should be written alongside each phase

## Quick Fix vs Full Refactor

### Quick Fix (do now)
- Fix `source_id` extraction in `audio_search_service.py` ✅ (done)
- This unblocks the "search → select YouTube" flow

### Full Refactor (this plan)
- Consolidate all YouTube downloads into `YouTubeDownloadService`
- Fix the "direct URL submission" flow to use remote flacfetch
- Remove dead code and simplify branching
- Add comprehensive tests

## Risk Assessment

| Risk | Mitigation |
|------|------------|
| Breaking existing flows | Comprehensive tests before deployment |
| Remote flacfetch unavailable | Graceful fallback with clear error messaging |
| YouTube bot detection | Remote flacfetch with cookies is the solution |
| GCS path format inconsistencies | Standardize on path-only format (no gs:// prefix) |

## Success Criteria

1. **One code path** for YouTube downloads in cloud backend
2. **Both entry points** (search+select, direct URL) use remote flacfetch when configured
3. **All tests pass** with >80% coverage on new code
4. **Production verified** with real YouTube downloads
5. **No dead code** remaining in the codebase

## Open Questions

1. Should we support local YouTube downloads at all in Cloud Run? (Current: yes with warning)
2. Should direct URL submission be synchronous (wait for download) or async (background)?
3. How do we handle YouTube cookies management for remote flacfetch?

## Related Documentation

- `docs/LESSONS-LEARNED.md` - "Fix Both Sides of Dual Code Paths" pattern
- `docs/ARCHITECTURE.md` - Remote flacfetch integration
- Previous PR #320 - Initial YouTube remote download fix (incomplete)
