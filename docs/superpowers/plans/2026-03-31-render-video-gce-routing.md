# Route render_video to GCE Encoding Worker — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route the render_video_worker's OutputGenerator (ffmpeg/libass) processing to the GCE encoding worker instead of running it on Cloud Run, fixing OOM crashes and improving job speed.

**Architecture:** Add a `/render-video` endpoint to the GCE encoding worker (`backend/services/gce_encoding/main.py`) that downloads inputs from GCS, runs OutputGenerator, and uploads outputs. Add corresponding `submit_render_video_job()` and `render_video_on_gce()` methods to `EncodingService`. Modify `render_video_worker` to delegate to GCE when `encoding_service.is_enabled`, keeping the existing local path as fallback. Add a disk cache for style assets on the GCE worker to avoid repeated downloads of the same theme files.

**Tech Stack:** Python, FastAPI, GCS, OutputGenerator (karaoke-gen wheel), aiohttp

**Spec:** `docs/superpowers/specs/2026-03-31-render-video-gce-routing-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/services/gce_encoding/main.py` | Modify | Add `RenderVideoRequest`, `run_render_video()`, `/render-video` endpoint, `download_with_cache()` |
| `backend/services/encoding_service.py` | Modify | Add `submit_render_video_job()`, `render_video_on_gce()` |
| `backend/workers/render_video_worker.py` | Modify | Add GCE routing conditional, extract local path into helper |
| `tests/unit/test_encoding_service_render_video.py` | Create | Tests for EncodingService render_video methods |
| `tests/unit/test_render_video_worker_gce.py` | Create | Tests for render_video_worker GCE routing logic |
| `tests/unit/test_gce_render_video_endpoint.py` | Create | Tests for GCE worker /render-video endpoint |

---

### Task 1: GCE Worker — Style Asset Disk Cache

Add a `download_with_cache()` helper to the GCE encoding worker that checks a local disk cache before downloading from GCS. This is used by the render-video endpoint (Task 2) and could benefit preview encoding too.

**Files:**
- Modify: `backend/services/gce_encoding/main.py` (add after `upload_single_file_to_gcs`, around line 137)
- Create: `tests/unit/test_gce_render_video_endpoint.py`

- [ ] **Step 1: Write the failing test for download_with_cache**

Create `tests/unit/test_gce_render_video_endpoint.py`:

```python
"""Tests for GCE encoding worker render-video endpoint and caching."""
import hashlib
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestDownloadWithCache:
    """Tests for the download_with_cache helper."""

    def test_cache_miss_downloads_and_caches(self, tmp_path):
        """On cache miss, downloads file and stores in cache."""
        from backend.services.gce_encoding.main import download_with_cache

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        dest = tmp_path / "output.ttf"
        gcs_uri = "gs://bucket/themes/nomad/font.ttf"

        # Mock download to write a file
        def fake_download(uri, path):
            Path(path).write_bytes(b"font-data")

        with patch("backend.services.gce_encoding.main.download_single_file_from_gcs", side_effect=fake_download):
            download_with_cache(gcs_uri, dest, cache_dir)

        assert dest.read_bytes() == b"font-data"
        # Verify cached copy exists
        cache_key = hashlib.sha256(gcs_uri.encode()).hexdigest()
        cached = cache_dir / cache_key
        assert cached.exists()
        assert cached.read_bytes() == b"font-data"

    def test_cache_hit_skips_download(self, tmp_path):
        """On cache hit, copies from cache without downloading."""
        from backend.services.gce_encoding.main import download_with_cache

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        dest = tmp_path / "output.ttf"
        gcs_uri = "gs://bucket/themes/nomad/font.ttf"

        # Pre-populate cache
        cache_key = hashlib.sha256(gcs_uri.encode()).hexdigest()
        cached = cache_dir / cache_key
        cached.write_bytes(b"cached-font-data")

        with patch("backend.services.gce_encoding.main.download_single_file_from_gcs") as mock_dl:
            download_with_cache(gcs_uri, dest, cache_dir)
            mock_dl.assert_not_called()

        assert dest.read_bytes() == b"cached-font-data"

    def test_cache_dir_none_downloads_directly(self, tmp_path):
        """When cache_dir is None, downloads without caching."""
        from backend.services.gce_encoding.main import download_with_cache

        dest = tmp_path / "output.ttf"
        gcs_uri = "gs://bucket/themes/nomad/font.ttf"

        def fake_download(uri, path):
            Path(path).write_bytes(b"font-data")

        with patch("backend.services.gce_encoding.main.download_single_file_from_gcs", side_effect=fake_download):
            download_with_cache(gcs_uri, dest, cache_dir=None)

        assert dest.read_bytes() == b"font-data"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-video-render-routing && python -m pytest tests/unit/test_gce_render_video_endpoint.py::TestDownloadWithCache -v`
Expected: FAIL with `ImportError` — `download_with_cache` doesn't exist yet.

- [ ] **Step 3: Implement download_with_cache**

In `backend/services/gce_encoding/main.py`, add after `upload_single_file_to_gcs` (after line 137):

```python
# Style asset disk cache directory
# Persists across jobs to avoid re-downloading the same theme assets (fonts, backgrounds)
STYLE_CACHE_DIR = Path("/var/cache/karaoke-gen/styles")


def download_with_cache(gcs_uri: str, local_path: Path, cache_dir: Optional[Path] = STYLE_CACHE_DIR):
    """Download a file from GCS, using a local disk cache to skip repeated downloads.

    Cache key is SHA-256 of the GCS URI. Cache hits copy from disk instead of
    downloading. When cache_dir is None, downloads directly (no caching).
    """
    if cache_dir is None:
        download_single_file_from_gcs(gcs_uri, local_path)
        return

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_key = hashlib.sha256(gcs_uri.encode()).hexdigest()
    cached_path = cache_dir / cache_key

    if cached_path.exists():
        logger.info(f"Cache hit for {gcs_uri} -> {cached_path}")
        import shutil
        shutil.copy2(str(cached_path), str(local_path))
        return

    logger.info(f"Cache miss for {gcs_uri}, downloading...")
    download_single_file_from_gcs(gcs_uri, local_path)
    # Store in cache
    import shutil
    shutil.copy2(str(local_path), str(cached_path))
```

Also add `import hashlib` to the imports at the top of the file (line 5 area).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-video-render-routing && python -m pytest tests/unit/test_gce_render_video_endpoint.py::TestDownloadWithCache -v`
Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/gce_encoding/main.py tests/unit/test_gce_render_video_endpoint.py
git commit -m "feat: add download_with_cache for GCE worker style asset caching"
```

---

### Task 2: GCE Worker — /render-video Endpoint

Add the `RenderVideoRequest` model, `run_render_video()` processing function, `process_render_video_job()` async wrapper, and `/render-video` POST endpoint to the GCE encoding worker.

**Files:**
- Modify: `backend/services/gce_encoding/main.py` (add request model, processing function, endpoint)
- Modify: `tests/unit/test_gce_render_video_endpoint.py` (add endpoint tests)

- [ ] **Step 1: Write the failing test for run_render_video**

Append to `tests/unit/test_gce_render_video_endpoint.py`:

```python
from unittest.mock import call


class TestRunRenderVideo:
    """Tests for the run_render_video processing function."""

    def test_sets_job_status_to_running(self, tmp_path):
        """run_render_video sets job status to running."""
        from backend.services.gce_encoding.main import run_render_video, jobs, RenderVideoRequest

        job_id = "test-render-001"
        jobs[job_id] = {"job_id": job_id, "status": "pending", "progress": 0, "error": None, "output_files": None, "metadata": None}

        request = RenderVideoRequest(
            job_id=job_id,
            original_corrections_gcs_path="gs://bucket/jobs/test/lyrics/corrections.json",
            audio_gcs_path="gs://bucket/jobs/test/audio.flac",
            output_gcs_prefix="gs://bucket/jobs/test/",
            artist="Test Artist",
            title="Test Song",
        )

        with patch("backend.services.gce_encoding.main.ensure_latest_wheel"):
            with patch("backend.services.gce_encoding.main.download_single_file_from_gcs"):
                with patch("backend.services.gce_encoding.main.download_with_cache"):
                    with patch("backend.services.gce_encoding.main.upload_single_file_to_gcs"):
                        # Patch OutputGenerator to avoid actual rendering
                        mock_outputs = MagicMock()
                        mock_outputs.video = str(tmp_path / "with_vocals.mkv")
                        mock_outputs.ass = str(tmp_path / "karaoke.ass")
                        mock_outputs.lrc = str(tmp_path / "karaoke.lrc")
                        mock_outputs.corrected_txt = str(tmp_path / "corrected.txt")
                        # Create fake output files
                        for attr in ["video", "ass", "lrc", "corrected_txt"]:
                            Path(getattr(mock_outputs, attr)).write_bytes(b"fake")

                        with patch("backend.services.gce_encoding.main.OutputGenerator") as mock_gen_cls:
                            mock_gen = MagicMock()
                            mock_gen.generate_outputs.return_value = mock_outputs
                            mock_gen_cls.return_value = mock_gen

                            with patch("backend.services.gce_encoding.main.CorrectionResult") as mock_cr:
                                mock_cr.from_dict.return_value = MagicMock(corrected_segments=[])
                                with patch("backend.services.gce_encoding.main.CountdownProcessor") as mock_cp:
                                    mock_cp.return_value.process.return_value = (mock_cr.from_dict.return_value, str(tmp_path / "audio.flac"), False, 0)
                                    with patch("backend.services.gce_encoding.main.load_styles_from_gcs") as mock_styles:
                                        mock_styles.return_value = (str(tmp_path / "styles.json"), {})

                                        run_render_video(job_id, tmp_path, request)

        assert jobs[job_id]["status"] == "running" or jobs[job_id]["status"] == "complete"
        assert jobs[job_id]["output_files"] is not None

    def test_uploads_all_output_files(self, tmp_path):
        """run_render_video uploads video, ass, lrc, and txt to GCS."""
        from backend.services.gce_encoding.main import run_render_video, jobs, RenderVideoRequest

        job_id = "test-render-002"
        jobs[job_id] = {"job_id": job_id, "status": "pending", "progress": 0, "error": None, "output_files": None, "metadata": None}

        request = RenderVideoRequest(
            job_id=job_id,
            original_corrections_gcs_path="gs://bucket/jobs/test/lyrics/corrections.json",
            audio_gcs_path="gs://bucket/jobs/test/audio.flac",
            output_gcs_prefix="gs://bucket/jobs/test/",
            artist="Test Artist",
            title="Test Song",
        )

        upload_calls = []

        def track_upload(local_path, gcs_uri):
            upload_calls.append(gcs_uri)

        with patch("backend.services.gce_encoding.main.ensure_latest_wheel"):
            with patch("backend.services.gce_encoding.main.download_single_file_from_gcs"):
                with patch("backend.services.gce_encoding.main.download_with_cache"):
                    with patch("backend.services.gce_encoding.main.upload_single_file_to_gcs", side_effect=track_upload):
                        mock_outputs = MagicMock()
                        mock_outputs.video = str(tmp_path / "with_vocals.mkv")
                        mock_outputs.ass = str(tmp_path / "karaoke.ass")
                        mock_outputs.lrc = str(tmp_path / "karaoke.lrc")
                        mock_outputs.corrected_txt = str(tmp_path / "corrected.txt")
                        for attr in ["video", "ass", "lrc", "corrected_txt"]:
                            Path(getattr(mock_outputs, attr)).write_bytes(b"fake")

                        with patch("backend.services.gce_encoding.main.OutputGenerator") as mock_gen_cls:
                            mock_gen = MagicMock()
                            mock_gen.generate_outputs.return_value = mock_outputs
                            mock_gen_cls.return_value = mock_gen
                            with patch("backend.services.gce_encoding.main.CorrectionResult") as mock_cr:
                                mock_cr.from_dict.return_value = MagicMock(corrected_segments=[])
                                with patch("backend.services.gce_encoding.main.CountdownProcessor") as mock_cp:
                                    mock_cp.return_value.process.return_value = (mock_cr.from_dict.return_value, str(tmp_path / "audio.flac"), False, 0)
                                    with patch("backend.services.gce_encoding.main.load_styles_from_gcs") as mock_styles:
                                        mock_styles.return_value = (str(tmp_path / "styles.json"), {})
                                        run_render_video(job_id, tmp_path, request)

        assert "gs://bucket/jobs/test/videos/with_vocals.mkv" in upload_calls
        assert "gs://bucket/jobs/test/lyrics/karaoke.ass" in upload_calls
        assert "gs://bucket/jobs/test/lyrics/karaoke.lrc" in upload_calls
        assert "gs://bucket/jobs/test/lyrics/corrected.txt" in upload_calls
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-video-render-routing && python -m pytest tests/unit/test_gce_render_video_endpoint.py::TestRunRenderVideo -v`
Expected: FAIL with `ImportError` — `RenderVideoRequest` and `run_render_video` don't exist yet.

- [ ] **Step 3: Implement RenderVideoRequest model and run_render_video**

In `backend/services/gce_encoding/main.py`, add the request model after `EncodePreviewRequest` (after line 60):

```python
class RenderVideoRequest(BaseModel):
    job_id: str
    original_corrections_gcs_path: str  # gs://bucket/jobs/{id}/lyrics/corrections.json
    updated_corrections_gcs_path: Optional[str] = None  # gs://bucket/jobs/{id}/lyrics/corrections_updated.json
    audio_gcs_path: str  # gs://bucket/jobs/{id}/audio.flac
    style_params_gcs_path: Optional[str] = None  # gs://bucket/themes/{id}/style_params.json
    style_assets: Optional[dict] = None  # {asset_key: "gs://bucket/path"}
    output_gcs_prefix: str  # gs://bucket/jobs/{id}/
    artist: str
    title: str
    subtitle_offset_ms: int = 0
    video_resolution: str = "4k"
```

Add the `run_render_video` function after `run_preview_encoding` (after line 239):

```python
def run_render_video(job_id: str, work_dir: Path, request: "RenderVideoRequest"):
    """Run video rendering using OutputGenerator from the karaoke-gen wheel.

    Downloads corrections, audio, and style assets from GCS, runs OutputGenerator
    to produce with_vocals.mkv and subtitle files, then uploads results.
    Style assets use disk cache to avoid repeated downloads of the same theme.
    """
    jobs[job_id]["status"] = "running"
    jobs[job_id]["progress"] = 5

    try:
        # Import from installed karaoke-gen wheel
        from karaoke_gen.lyrics_transcriber.output.generator import OutputGenerator
        from karaoke_gen.lyrics_transcriber.output.countdown_processor import CountdownProcessor
        from karaoke_gen.lyrics_transcriber.types import CorrectionResult
        from karaoke_gen.lyrics_transcriber.correction.operations import CorrectionOperations
        from karaoke_gen.lyrics_transcriber.core.config import OutputConfig
        from karaoke_gen.style_loader import load_styles_from_gcs
        from karaoke_gen.utils import sanitize_filename

        # 1. Download original corrections
        corrections_path = work_dir / "corrections.json"
        download_single_file_from_gcs(request.original_corrections_gcs_path, corrections_path)
        jobs[job_id]["progress"] = 15

        with open(corrections_path, 'r', encoding='utf-8') as f:
            original_data = json.load(f)
        base_result = CorrectionResult.from_dict(original_data)

        # 2. Apply user corrections if available
        if request.updated_corrections_gcs_path:
            updated_path = work_dir / "corrections_updated.json"
            download_single_file_from_gcs(request.updated_corrections_gcs_path, updated_path)
            with open(updated_path, 'r', encoding='utf-8') as f:
                updated_data = json.load(f)
            correction_result = CorrectionOperations.update_correction_result_with_data(
                base_result, updated_data
            )
            logger.info(f"[job:{job_id}] Applied user corrections")
        else:
            correction_result = base_result

        jobs[job_id]["progress"] = 25

        # 3. Download audio
        audio_path = work_dir / "audio.flac"
        download_single_file_from_gcs(request.audio_gcs_path, audio_path)
        jobs[job_id]["progress"] = 35

        # 4. Process countdown intro
        countdown_processor = CountdownProcessor(cache_dir=str(work_dir), logger=logger)
        correction_result, audio_path_str, padding_added, padding_seconds = countdown_processor.process(
            correction_result=correction_result,
            audio_filepath=str(audio_path),
        )
        audio_path = Path(audio_path_str)
        jobs[job_id]["progress"] = 40

        # 5. Download style assets (with disk cache for repeated themes)
        style_dir = work_dir / "style"
        style_dir.mkdir(exist_ok=True)

        def cached_download(gcs_path, local_path):
            download_with_cache(gcs_path, Path(local_path), STYLE_CACHE_DIR)

        styles_path, style_data = load_styles_from_gcs(
            style_params_gcs_path=request.style_params_gcs_path,
            style_assets=request.style_assets,
            temp_dir=str(work_dir),
            download_func=cached_download,
            logger=logger,
        )

        # Register any downloaded fonts with fontconfig
        for asset_key, gcs_path in (request.style_assets or {}).items():
            if 'font' in asset_key.lower() and gcs_path.endswith(('.ttf', '.otf', '.woff', '.woff2')):
                fonts_dir = Path("/usr/local/share/fonts/custom")
                fonts_dir.mkdir(parents=True, exist_ok=True)
                font_filename = gcs_path.split("/")[-1]
                font_dest = fonts_dir / font_filename
                if not font_dest.exists():
                    # Copy from style dir to fonts dir
                    ext = os.path.splitext(gcs_path)[1]
                    style_font = style_dir / f"{asset_key}{ext}"
                    if style_font.exists():
                        import shutil
                        shutil.copy2(str(style_font), str(font_dest))
                        subprocess.run(["fc-cache", "-fv"], capture_output=True)
                        logger.info(f"Registered font: {font_filename}")

        jobs[job_id]["progress"] = 50

        # 6. Configure and run OutputGenerator
        output_dir = work_dir / "output"
        cache_dir = work_dir / "cache"
        output_dir.mkdir(exist_ok=True)
        cache_dir.mkdir(exist_ok=True)

        config = OutputConfig(
            output_dir=str(output_dir),
            cache_dir=str(cache_dir),
            output_styles_json=styles_path,
            render_video=True,
            generate_cdg=False,
            generate_plain_text=True,
            generate_lrc=True,
            video_resolution=request.video_resolution,
            subtitle_offset_ms=request.subtitle_offset_ms,
        )

        output_generator = OutputGenerator(config, logger)

        safe_artist = sanitize_filename(request.artist) if request.artist else "Unknown"
        safe_title = sanitize_filename(request.title) if request.title else "Unknown"
        output_prefix = f"{safe_artist} - {safe_title}"

        logger.info(f"[job:{job_id}] Generating outputs with prefix '{output_prefix}'")
        jobs[job_id]["progress"] = 55

        outputs = output_generator.generate_outputs(
            transcription_corrected=correction_result,
            lyrics_results={},
            output_prefix=output_prefix,
            audio_filepath=str(audio_path),
            artist=request.artist,
            title=request.title,
        )

        jobs[job_id]["progress"] = 85

        # 7. Upload outputs to GCS
        output_prefix_gcs = request.output_gcs_prefix.rstrip("/")
        output_files = []

        if outputs.video and os.path.exists(outputs.video):
            gcs_path = f"{output_prefix_gcs}/videos/with_vocals.mkv"
            upload_single_file_to_gcs(Path(outputs.video), gcs_path)
            output_files.append(gcs_path)
            logger.info(f"[job:{job_id}] Uploaded with_vocals.mkv ({os.path.getsize(outputs.video)} bytes)")
        else:
            raise RuntimeError("Video generation failed - no output file produced")

        if outputs.ass and os.path.exists(outputs.ass):
            gcs_path = f"{output_prefix_gcs}/lyrics/karaoke.ass"
            upload_single_file_to_gcs(Path(outputs.ass), gcs_path)
            output_files.append(gcs_path)

        if outputs.lrc and os.path.exists(outputs.lrc):
            gcs_path = f"{output_prefix_gcs}/lyrics/karaoke.lrc"
            upload_single_file_to_gcs(Path(outputs.lrc), gcs_path)
            output_files.append(gcs_path)

        if outputs.corrected_txt and os.path.exists(outputs.corrected_txt):
            gcs_path = f"{output_prefix_gcs}/lyrics/corrected.txt"
            upload_single_file_to_gcs(Path(outputs.corrected_txt), gcs_path)
            output_files.append(gcs_path)

        jobs[job_id]["progress"] = 95
        jobs[job_id]["output_files"] = output_files
        jobs[job_id]["metadata"] = {
            "countdown_padding_added": padding_added,
            "countdown_padding_seconds": padding_seconds if padding_added else 0,
        }

        logger.info(f"[job:{job_id}] Render video complete. Output files: {output_files}")

    except ImportError as e:
        error_msg = (
            f"OutputGenerator not available: {e}. "
            "The karaoke-gen wheel must be installed. "
            "Check that ensure_latest_wheel() succeeded."
        )
        logger.error(error_msg)
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = error_msg
        raise RuntimeError(error_msg) from e

    except Exception as e:
        logger.error(f"[job:{job_id}] Render video failed: {e}")
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)
        raise
```

Add `process_render_video_job` async wrapper after `process_preview_job` (after line 584):

```python
async def process_render_video_job(job_id: str, request: RenderVideoRequest):
    """Process a render-video job asynchronously."""
    try:
        ensure_latest_wheel()

        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir) / "work"
            work_dir.mkdir()

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                executor,
                run_render_video,
                job_id,
                work_dir,
                request,
            )

            jobs[job_id]["status"] = "complete"
            jobs[job_id]["progress"] = 100
            logger.info(f"Render video job {job_id} complete")

    except Exception as e:
        logger.error(f"Render video job {job_id} failed: {e}")
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)
```

Add the `/render-video` endpoint after the `/encode-preview` endpoint (after line 615):

```python
@app.post("/render-video")
async def submit_render_video_job_endpoint(
    request: RenderVideoRequest,
    background_tasks: BackgroundTasks,
    _auth: bool = Depends(verify_api_key),
):
    """Submit a render-video job (OutputGenerator + ffmpeg/libass rendering)."""
    job_id = request.job_id

    if job_id in jobs:
        existing = jobs[job_id]
        if existing["status"] == "complete":
            return {
                "status": "cached",
                "job_id": job_id,
                "output_files": existing.get("output_files"),
                "metadata": existing.get("metadata"),
            }
        elif existing["status"] == "failed":
            pass  # Allow retry
        else:
            return {"status": "in_progress", "job_id": job_id}

    jobs[job_id] = {
        "job_id": job_id,
        "status": "pending",
        "progress": 0,
        "error": None,
        "output_files": None,
        "metadata": None,
    }

    background_tasks.add_task(process_render_video_job, job_id, request)
    return {"status": "accepted", "job_id": job_id}
```

Also update the `JobStatus` model to include `metadata`:

```python
class JobStatus(BaseModel):
    job_id: str
    status: str  # pending, running, complete, failed
    progress: int  # 0-100
    error: Optional[str] = None
    output_files: Optional[list[str]] = None
    metadata: Optional[dict] = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-video-render-routing && python -m pytest tests/unit/test_gce_render_video_endpoint.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/gce_encoding/main.py tests/unit/test_gce_render_video_endpoint.py
git commit -m "feat: add /render-video endpoint to GCE encoding worker"
```

---

### Task 3: EncodingService — render_video Methods

Add `submit_render_video_job()` and `render_video_on_gce()` methods to `EncodingService`, following the same pattern as `submit_encoding_job()` / `encode_videos()`.

**Files:**
- Modify: `backend/services/encoding_service.py` (add methods after `encode_preview_video`, around line 585)
- Create: `tests/unit/test_encoding_service_render_video.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_encoding_service_render_video.py`:

```python
"""Tests for EncodingService render_video methods."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.encoding_service import EncodingService


@pytest.fixture
def encoding_service():
    """Create an EncodingService with mocked credentials."""
    with patch.object(EncodingService, '_load_credentials') as mock_load:
        service = EncodingService()
        service._url = "http://test-worker:8080"
        service._api_key = "test-key"
        service._initialized = True
        yield service


class TestSubmitRenderVideoJob:
    """Tests for submit_render_video_job."""

    def test_posts_to_render_video_endpoint(self, encoding_service):
        """Submits render-video job to /render-video endpoint."""
        render_config = {
            "job_id": "test-123",
            "original_corrections_gcs_path": "gs://bucket/corrections.json",
            "audio_gcs_path": "gs://bucket/audio.flac",
            "output_gcs_prefix": "gs://bucket/jobs/test-123/",
            "artist": "Test",
            "title": "Song",
        }

        mock_response = {"status": 200, "json": {"status": "accepted", "job_id": "test-123"}, "text": None}

        with patch.object(encoding_service, '_request_with_retry', new_callable=AsyncMock, return_value=mock_response):
            result = asyncio.get_event_loop().run_until_complete(
                encoding_service.submit_render_video_job("test-123", render_config)
            )

        assert result["status"] == "accepted"

    def test_raises_on_401(self, encoding_service):
        """Raises RuntimeError on invalid API key."""
        render_config = {
            "job_id": "test-123",
            "original_corrections_gcs_path": "gs://bucket/corrections.json",
            "audio_gcs_path": "gs://bucket/audio.flac",
            "output_gcs_prefix": "gs://bucket/jobs/test-123/",
            "artist": "Test",
            "title": "Song",
        }

        mock_response = {"status": 401, "json": None, "text": "Unauthorized"}

        with patch.object(encoding_service, '_request_with_retry', new_callable=AsyncMock, return_value=mock_response):
            with pytest.raises(RuntimeError, match="Invalid API key"):
                asyncio.get_event_loop().run_until_complete(
                    encoding_service.submit_render_video_job("test-123", render_config)
                )

    def test_handles_cached_result(self, encoding_service):
        """Returns cached result when GCE worker already completed the job."""
        render_config = {
            "job_id": "test-123",
            "original_corrections_gcs_path": "gs://bucket/corrections.json",
            "audio_gcs_path": "gs://bucket/audio.flac",
            "output_gcs_prefix": "gs://bucket/jobs/test-123/",
            "artist": "Test",
            "title": "Song",
        }

        # First response: 409 conflict
        submit_response = {"status": 409, "json": None, "text": "Conflict"}
        # Status check returns complete
        status_response = {
            "status": "complete",
            "output_files": ["jobs/test-123/videos/with_vocals.mkv"],
            "metadata": {"countdown_padding_added": False},
        }

        with patch.object(encoding_service, '_request_with_retry', new_callable=AsyncMock, return_value=submit_response):
            with patch.object(encoding_service, 'get_job_status', new_callable=AsyncMock, return_value=status_response):
                result = asyncio.get_event_loop().run_until_complete(
                    encoding_service.submit_render_video_job("test-123", render_config)
                )

        assert result["status"] == "cached"


class TestRenderVideoOnGce:
    """Tests for render_video_on_gce convenience method."""

    def test_submits_and_waits(self, encoding_service):
        """Calls submit then wait_for_completion."""
        render_config = {
            "job_id": "test-123",
            "original_corrections_gcs_path": "gs://bucket/corrections.json",
            "audio_gcs_path": "gs://bucket/audio.flac",
            "output_gcs_prefix": "gs://bucket/jobs/test-123/",
            "artist": "Test",
            "title": "Song",
        }

        submit_result = {"status": "accepted", "job_id": "test-123"}
        wait_result = {
            "status": "complete",
            "output_files": ["jobs/test-123/videos/with_vocals.mkv"],
            "metadata": {"countdown_padding_added": False},
        }

        with patch.object(encoding_service, 'submit_render_video_job', new_callable=AsyncMock, return_value=submit_result):
            with patch.object(encoding_service, 'wait_for_completion', new_callable=AsyncMock, return_value=wait_result):
                result = asyncio.get_event_loop().run_until_complete(
                    encoding_service.render_video_on_gce("test-123", render_config)
                )

        assert result["status"] == "complete"
        assert result["output_files"] == ["jobs/test-123/videos/with_vocals.mkv"]

    def test_returns_immediately_on_cached(self, encoding_service):
        """Returns immediately when submit returns cached status."""
        render_config = {
            "job_id": "test-123",
            "original_corrections_gcs_path": "gs://bucket/corrections.json",
            "audio_gcs_path": "gs://bucket/audio.flac",
            "output_gcs_prefix": "gs://bucket/jobs/test-123/",
            "artist": "Test",
            "title": "Song",
        }

        submit_result = {
            "status": "cached",
            "job_id": "test-123",
            "output_files": ["jobs/test-123/videos/with_vocals.mkv"],
            "metadata": {"countdown_padding_added": False},
        }

        with patch.object(encoding_service, 'submit_render_video_job', new_callable=AsyncMock, return_value=submit_result):
            with patch.object(encoding_service, 'wait_for_completion', new_callable=AsyncMock) as mock_wait:
                result = asyncio.get_event_loop().run_until_complete(
                    encoding_service.render_video_on_gce("test-123", render_config)
                )
                mock_wait.assert_not_called()

        assert result["status"] == "complete"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-video-render-routing && python -m pytest tests/unit/test_encoding_service_render_video.py -v`
Expected: FAIL with `AttributeError` — methods don't exist yet.

- [ ] **Step 3: Implement submit_render_video_job and render_video_on_gce**

In `backend/services/encoding_service.py`, add after `encode_preview_video` (after line 585):

```python
    async def submit_render_video_job(
        self,
        job_id: str,
        render_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Submit a render-video job to the GCE worker.

        Args:
            job_id: Unique job identifier
            render_config: Dict with keys matching RenderVideoRequest fields:
                original_corrections_gcs_path, audio_gcs_path, output_gcs_prefix,
                artist, title, and optional updated_corrections_gcs_path,
                style_params_gcs_path, style_assets, subtitle_offset_ms, video_resolution

        Returns:
            Response from the encoding worker

        Raises:
            RuntimeError: If submission fails
        """
        self._load_credentials()

        if not self.is_configured:
            raise RuntimeError("Encoding service not configured")

        url = f"{self._get_worker_url()}/render-video"
        headers = {"X-API-Key": self._api_key, "Content-Type": "application/json"}
        payload = {"job_id": job_id, **render_config}

        logger.info(f"[job:{job_id}] Submitting render-video job to GCE worker: {url}")

        resp = await self._request_with_retry(
            method="POST",
            url=url,
            headers=headers,
            json_payload=payload,
            timeout=30.0,
            job_id=job_id,
        )

        if resp["status"] == 401:
            raise RuntimeError("Invalid API key for encoding worker")
        if resp["status"] == 409:
            logger.warning(f"[job:{job_id}] GCE worker returned 409, checking job status")
            try:
                status = await self.get_job_status(job_id)
                job_status = status.get("status", "unknown")
                if job_status == "complete":
                    logger.info(f"[job:{job_id}] Render-video already complete on GCE worker")
                    return {
                        "status": "cached",
                        "job_id": job_id,
                        "output_files": status.get("output_files"),
                        "metadata": status.get("metadata"),
                    }
                elif job_status in ("pending", "running"):
                    logger.info(f"[job:{job_id}] Render-video still in progress")
                    return {"status": "in_progress", "job_id": job_id}
                else:
                    raise RuntimeError(f"Render-video job {job_id} already exists with status: {job_status}")
            except RuntimeError as e:
                if "not found" in str(e).lower():
                    raise RuntimeError(f"Render-video job {job_id} conflict: 409 but job not found on status check")
                raise
        if resp["status"] != 200:
            raise RuntimeError(f"Failed to submit render-video job: {resp['status']} - {resp['text']}")

        return resp["json"]

    async def render_video_on_gce(
        self,
        job_id: str,
        render_config: Dict[str, Any],
        progress_callback=None,
    ) -> Dict[str, Any]:
        """
        Submit render-video job and wait for completion.

        Convenience method that combines submit + wait, like encode_videos().

        Args:
            job_id: Unique job identifier
            render_config: Render configuration (see submit_render_video_job)
            progress_callback: Optional callback(progress: int) for progress updates

        Returns:
            Final job status with output_files and metadata
        """
        submit_result = await self.submit_render_video_job(job_id, render_config)

        submit_status = submit_result.get("status")
        if submit_status == "cached":
            logger.info(f"[job:{job_id}] Render-video already cached, returning immediately")
            return {
                "status": "complete",
                "output_files": submit_result.get("output_files"),
                "metadata": submit_result.get("metadata"),
            }

        if submit_status == "in_progress":
            logger.info(f"[job:{job_id}] Render-video already in progress, joining poll")

        return await self.wait_for_completion(
            job_id, progress_callback=progress_callback
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-video-render-routing && python -m pytest tests/unit/test_encoding_service_render_video.py -v`
Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/encoding_service.py tests/unit/test_encoding_service_render_video.py
git commit -m "feat: add render_video_on_gce methods to EncodingService"
```

---

### Task 4: render_video_worker — GCE Routing

Modify the render_video_worker to delegate to GCE when `encoding_service.is_enabled`, keeping the existing local path as fallback.

**Files:**
- Modify: `backend/workers/render_video_worker.py`
- Create: `tests/unit/test_render_video_worker_gce.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_render_video_worker_gce.py`:

```python
"""Tests for render_video_worker GCE routing logic."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest


@pytest.fixture
def mock_job():
    """Create a mock job with typical fields."""
    job = MagicMock()
    job.artist = "Test Artist"
    job.title = "Test Song"
    job.input_media_gcs_path = "jobs/test-123/audio/input.flac"
    job.style_params_gcs_path = "themes/nomad/style_params.json"
    job.style_assets = {"font": "themes/nomad/font.ttf", "karaoke_background": "themes/nomad/bg.png"}
    job.subtitle_offset_ms = 0
    job.prep_only = False
    job.state_data = {"instrumental_selection": "clean"}
    job.file_urls = {"lyrics": {"corrections_updated": "jobs/test-123/lyrics/corrections_updated.json"}}
    return job


@pytest.fixture
def mock_services():
    """Create mock job_manager, storage, and encoding_service."""
    job_manager = MagicMock()
    storage = MagicMock()
    storage.file_exists.return_value = True
    encoding_service = MagicMock()
    encoding_service.is_enabled = True
    encoding_service.render_video_on_gce = AsyncMock(return_value={
        "status": "complete",
        "output_files": [
            "gs://karaoke-gen-storage/jobs/test-123/videos/with_vocals.mkv",
            "gs://karaoke-gen-storage/jobs/test-123/lyrics/karaoke.ass",
            "gs://karaoke-gen-storage/jobs/test-123/lyrics/karaoke.lrc",
            "gs://karaoke-gen-storage/jobs/test-123/lyrics/corrected.txt",
        ],
        "metadata": {
            "countdown_padding_added": False,
            "countdown_padding_seconds": 0,
        },
    })
    return job_manager, storage, encoding_service


class TestGceRouting:
    """Tests for GCE routing in render_video_worker."""

    def test_delegates_to_gce_when_enabled(self, mock_job, mock_services):
        """When encoding_service.is_enabled, delegates to GCE."""
        job_manager, storage, encoding_service = mock_services

        with patch("backend.workers.render_video_worker.JobManager", return_value=job_manager):
            with patch("backend.workers.render_video_worker.StorageService", return_value=storage):
                with patch("backend.workers.render_video_worker.get_encoding_service", return_value=encoding_service):
                    with patch("backend.workers.render_video_worker.get_settings") as mock_settings:
                        mock_settings.return_value.gcs_bucket_name = "karaoke-gen-storage"
                        job_manager.get_job.return_value = mock_job
                        with patch("backend.workers.render_video_worker.create_job_logger") as mock_log:
                            mock_log.return_value = MagicMock()
                            with patch("backend.workers.render_video_worker.setup_job_logging"):
                                with patch("backend.workers.render_video_worker.validate_worker_can_run", return_value=None):
                                    with patch("backend.workers.render_video_worker.job_span"):
                                        with patch("backend.workers.render_video_worker.job_logging_context"):
                                            result = asyncio.get_event_loop().run_until_complete(
                                                __import__("backend.workers.render_video_worker", fromlist=["process_render_video"]).process_render_video("test-123")
                                            )

        assert result is True
        encoding_service.render_video_on_gce.assert_called_once()
        call_args = encoding_service.render_video_on_gce.call_args
        assert call_args[0][0] == "test-123"  # job_id
        config = call_args[0][1]
        assert "original_corrections_gcs_path" in config
        assert config["artist"] == "Test Artist"

    def test_falls_back_to_local_when_disabled(self, mock_job, mock_services):
        """When encoding_service.is_enabled is False, runs locally."""
        job_manager, storage, encoding_service = mock_services
        encoding_service.is_enabled = False

        with patch("backend.workers.render_video_worker.JobManager", return_value=job_manager):
            with patch("backend.workers.render_video_worker.StorageService", return_value=storage):
                with patch("backend.workers.render_video_worker.get_encoding_service", return_value=encoding_service):
                    with patch("backend.workers.render_video_worker.get_settings") as mock_settings:
                        mock_settings.return_value.gcs_bucket_name = "karaoke-gen-storage"
                        job_manager.get_job.return_value = mock_job
                        with patch("backend.workers.render_video_worker.create_job_logger") as mock_log:
                            mock_log.return_value = MagicMock()
                            with patch("backend.workers.render_video_worker.setup_job_logging"):
                                with patch("backend.workers.render_video_worker.validate_worker_can_run", return_value=None):
                                    with patch("backend.workers.render_video_worker.job_span"):
                                        with patch("backend.workers.render_video_worker.job_logging_context"):
                                            # Mock OutputGenerator for local path
                                            with patch("backend.workers.render_video_worker.OutputGenerator") as mock_og:
                                                mock_outputs = MagicMock()
                                                mock_outputs.video = "/tmp/fake.mkv"
                                                mock_outputs.ass = None
                                                mock_outputs.lrc = None
                                                mock_outputs.corrected_txt = None
                                                mock_og.return_value.generate_outputs.return_value = mock_outputs
                                                with patch("os.path.exists", return_value=True):
                                                    with patch("os.path.getsize", return_value=1000):
                                                        with patch("backend.workers.render_video_worker.load_styles_from_gcs", return_value=("/tmp/styles.json", {})):
                                                            with patch("backend.workers.render_video_worker.CountdownProcessor") as mock_cp:
                                                                mock_cp.return_value.process.return_value = (MagicMock(corrected_segments=[]), "/tmp/audio.flac", False, 0)
                                                                with patch("backend.workers.render_video_worker.CorrectionResult") as mock_cr:
                                                                    mock_cr.from_dict.return_value = MagicMock(corrected_segments=[])
                                                                    with patch("backend.workers.render_video_worker.CorrectionOperations"):
                                                                        result = asyncio.get_event_loop().run_until_complete(
                                                                            __import__("backend.workers.render_video_worker", fromlist=["process_render_video"]).process_render_video("test-123")
                                                                        )

        encoding_service.render_video_on_gce.assert_not_called()

    def test_gce_path_updates_file_urls(self, mock_job, mock_services):
        """GCE path updates Firestore file_urls from response."""
        job_manager, storage, encoding_service = mock_services

        with patch("backend.workers.render_video_worker.JobManager", return_value=job_manager):
            with patch("backend.workers.render_video_worker.StorageService", return_value=storage):
                with patch("backend.workers.render_video_worker.get_encoding_service", return_value=encoding_service):
                    with patch("backend.workers.render_video_worker.get_settings") as mock_settings:
                        mock_settings.return_value.gcs_bucket_name = "karaoke-gen-storage"
                        job_manager.get_job.return_value = mock_job
                        with patch("backend.workers.render_video_worker.create_job_logger") as mock_log:
                            mock_log.return_value = MagicMock()
                            with patch("backend.workers.render_video_worker.setup_job_logging"):
                                with patch("backend.workers.render_video_worker.validate_worker_can_run", return_value=None):
                                    with patch("backend.workers.render_video_worker.job_span"):
                                        with patch("backend.workers.render_video_worker.job_logging_context"):
                                            asyncio.get_event_loop().run_until_complete(
                                                __import__("backend.workers.render_video_worker", fromlist=["process_render_video"]).process_render_video("test-123")
                                            )

        # Verify file_urls were updated
        job_manager.update_file_url.assert_any_call("test-123", "videos", "with_vocals", "jobs/test-123/videos/with_vocals.mkv")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-video-render-routing && python -m pytest tests/unit/test_render_video_worker_gce.py::TestGceRouting::test_delegates_to_gce_when_enabled -v`
Expected: FAIL — `get_encoding_service` is not imported in render_video_worker.

- [ ] **Step 3: Implement GCE routing in render_video_worker**

Modify `backend/workers/render_video_worker.py`:

**Add imports** (after line 42, before the logger):

```python
from backend.services.encoding_service import get_encoding_service
```

**Replace the body of `process_render_video`** (lines 115-374) with a conditional that checks `encoding_service.is_enabled`:

The key structural change is: wrap the existing local code (lines 122-374) inside an `else` branch, and add the GCE branch as the `if` branch. The GCE branch:

1. Constructs GCS paths from job metadata (no downloads)
2. Calls `encoding_service.render_video_on_gce()`
3. Parses the response to update Firestore file URLs and lyrics_metadata
4. Handles countdown padding metadata from the response
5. Transitions state and triggers video_worker (same as local path)

Here is the new code to insert after the `with job_logging_context(job_id):` line (replacing the code from the state transition through to the end of the with block):

```python
                # Check if GCE encoding is enabled
                encoding_service = get_encoding_service()
                use_gce = encoding_service.is_enabled

                if use_gce:
                    # ============ GCE RENDER VIDEO PATH ============
                    job_log.info("GCE encoding enabled - delegating render_video to encoding worker")
                    logger.info(f"[job:{job_id}] Using GCE encoding worker for render_video")

                    # Transition to RENDERING_VIDEO
                    job_manager.transition_to_state(
                        job_id=job_id,
                        new_status=JobStatus.RENDERING_VIDEO,
                        progress=75,
                        message="Rendering karaoke video on encoding worker"
                    )

                    # Build render config from job metadata (no downloads needed!)
                    bucket_name = settings.gcs_bucket_name

                    # Check for updated corrections
                    updated_corrections_gcs = f"jobs/{job_id}/lyrics/corrections_updated.json"
                    has_updated = storage.file_exists(updated_corrections_gcs)

                    render_config = {
                        "original_corrections_gcs_path": f"gs://{bucket_name}/jobs/{job_id}/lyrics/corrections.json",
                        "audio_gcs_path": f"gs://{bucket_name}/{job.input_media_gcs_path}",
                        "output_gcs_prefix": f"gs://{bucket_name}/jobs/{job_id}/",
                        "artist": job.artist,
                        "title": job.title,
                        "subtitle_offset_ms": getattr(job, 'subtitle_offset_ms', 0) or 0,
                        "video_resolution": "4k",
                    }

                    if has_updated:
                        render_config["updated_corrections_gcs_path"] = f"gs://{bucket_name}/{updated_corrections_gcs}"

                    if job.style_params_gcs_path:
                        render_config["style_params_gcs_path"] = f"gs://{bucket_name}/{job.style_params_gcs_path}" if not job.style_params_gcs_path.startswith("gs://") else job.style_params_gcs_path

                    if job.style_assets:
                        # Ensure all style asset paths are full gs:// URIs
                        render_config["style_assets"] = {
                            k: f"gs://{bucket_name}/{v}" if not v.startswith("gs://") else v
                            for k, v in job.style_assets.items()
                        }

                    def progress_callback(progress: int):
                        scaled = 75 + int(progress * 0.07)  # Map 0-100 to 75-82
                        job_manager.transition_to_state(
                            job_id=job_id,
                            new_status=JobStatus.RENDERING_VIDEO,
                            progress=scaled,
                            message=f"Rendering video ({progress}%)"
                        )

                    with job_span("gce-render-video", job_id) as render_span:
                        render_start = time.time()

                        result = await encoding_service.render_video_on_gce(
                            job_id, render_config, progress_callback=progress_callback
                        )

                        render_duration = time.time() - render_start
                        render_span.set_attribute("duration_seconds", render_duration)

                    job_log.info(f"GCE render_video complete in {render_duration:.1f}s")
                    logger.info(f"[job:{job_id}] GCE render_video complete in {render_duration:.1f}s")

                    # Parse response and update Firestore
                    output_files = result.get("output_files", [])
                    metadata = result.get("metadata", {})

                    # Update file URLs from GCE response
                    for gcs_path in output_files:
                        # Convert gs://bucket/path to blob path
                        blob_path = gcs_path
                        if blob_path.startswith("gs://"):
                            blob_path = blob_path.split("/", 3)[3] if blob_path.count("/") >= 3 else blob_path

                        if "with_vocals.mkv" in blob_path:
                            job_manager.update_file_url(job_id, 'videos', 'with_vocals', blob_path)
                        elif "karaoke.ass" in blob_path:
                            job_manager.update_file_url(job_id, 'lyrics', 'ass', blob_path)
                        elif "karaoke.lrc" in blob_path:
                            job_manager.update_file_url(job_id, 'lyrics', 'lrc', blob_path)
                        elif "corrected.txt" in blob_path:
                            job_manager.update_file_url(job_id, 'lyrics', 'corrected_txt', blob_path)

                    # Handle countdown padding metadata
                    padding_added = metadata.get("countdown_padding_added", False)
                    padding_seconds = metadata.get("countdown_padding_seconds", 0)
                    if padding_added:
                        existing_lyrics_metadata = job.state_data.get('lyrics_metadata', {})
                        existing_lyrics_metadata['has_countdown_padding'] = True
                        existing_lyrics_metadata['countdown_padding_seconds'] = padding_seconds
                        job_manager.update_state_data(job_id, 'lyrics_metadata', existing_lyrics_metadata)
                        job_log.info(f"Countdown padding: {padding_seconds}s")

                    # Store render timing
                    job_manager.update_processing_metadata(job_id, "rendering", {
                        "render_duration_seconds": round(render_duration, 1),
                        "countdown_padding_added": padding_added,
                        "countdown_padding_seconds": padding_seconds if padding_added else 0,
                        "rendered_on": "gce",
                    })
                    job_manager.update_processing_metadata(job_id, "timing.render_video_worker_seconds", round(render_duration, 1))

                    # Transition state (same as local path)
                    if getattr(job, 'prep_only', False):
                        job_manager.transition_to_state(
                            job_id=job_id,
                            new_status=JobStatus.PREP_COMPLETE,
                            progress=100,
                            message="Prep phase complete - download outputs to continue locally"
                        )
                        job_log.info("=== RENDER VIDEO WORKER COMPLETE (PREP ONLY, GCE) ===")
                        root_span.set_attribute("duration_seconds", render_duration)
                        root_span.set_attribute("rendered_on", "gce")
                        logger.info(f"[job:{job_id}] WORKER_END worker=render-video status=success duration={render_duration:.1f}s gce=true prep_only=true")
                    else:
                        job_manager.transition_to_state(
                            job_id=job_id,
                            new_status=JobStatus.INSTRUMENTAL_SELECTED,
                            progress=82,
                            message="Video rendered on encoding worker, starting final encoding"
                        )
                        job_log.info("=== RENDER VIDEO WORKER COMPLETE (GCE) ===")
                        root_span.set_attribute("duration_seconds", render_duration)
                        root_span.set_attribute("rendered_on", "gce")
                        logger.info(f"[job:{job_id}] WORKER_END worker=render-video status=success duration={render_duration:.1f}s gce=true")

                        from backend.services.worker_service import get_worker_service
                        worker_service = get_worker_service()
                        job_log.info("Triggering video worker for final encoding...")
                        await worker_service.trigger_video_worker(job_id)

                    job_manager.update_state_data(job_id, 'render_progress', {'stage': 'complete'})
                    return True

                else:
                    # ============ LOCAL RENDER VIDEO PATH ============
                    # (existing code from lines 122-374, indented one more level)
```

The existing local code block (from the `transition_to_state(RENDERING_VIDEO)` call through `return True`) goes inside the `else:` branch, unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-video-render-routing && python -m pytest tests/unit/test_render_video_worker_gce.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-video-render-routing && make test 2>&1 | tail -n 100`
Expected: All tests PASS, including existing tests (no regressions).

- [ ] **Step 6: Commit**

```bash
git add backend/workers/render_video_worker.py tests/unit/test_render_video_worker_gce.py
git commit -m "feat: route render_video to GCE encoding worker when enabled"
```

---

### Task 5: Full Integration Verification

Run the complete test suite and verify no regressions.

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-video-render-routing && make test 2>&1 | tail -n 200`
Expected: All tests PASS.

- [ ] **Step 2: Review the diff**

Run: `git diff origin/main --stat` and `git log --oneline origin/main..HEAD`
Verify:
- 3 new test files created
- 3 existing files modified (main.py, encoding_service.py, render_video_worker.py)
- 4 commits total

- [ ] **Step 3: Verify no code path divergence**

Check that the local fallback path in render_video_worker.py is unchanged from the original by running:
`git diff origin/main -- backend/workers/render_video_worker.py`

The only changes should be:
1. New `get_encoding_service` import
2. The `if use_gce:` / `else:` conditional wrapping the existing code
3. No modifications to the existing local rendering logic
