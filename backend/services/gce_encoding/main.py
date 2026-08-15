import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, BackgroundTasks, HTTPException, Header, Depends
from google.cloud import storage
from packaging.version import Version
from pydantic import BaseModel

from .persistence import JobStatePersister

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Job tracking — process-local dict, mirrored to Firestore via `persister`
# so polls after a worker restart return the *last known* status instead of
# 404. Hot path stays in-memory; persistence is best-effort.
jobs: dict[str, dict] = {}

# Two execution lanes with very different memory profiles:
#
#   heavy_executor — full renders (`run_render_video`) and final encodes
#     (`run_encoding`). Each of these spawns an ffmpeg that already saturates
#     every core, so running two in parallel gains no throughput but doubles
#     peak RAM (a single 4K encode can use ~18 GB). Three concurrent encodes
#     OOM-killed the 32 GB n2 fallback worker on 2026-08-15, which restarted
#     the service and lost every in-flight job. Default to ONE heavy job at a
#     time so the worker is OOM-proof on any machine type; the rest queue as
#     `status="pending"` (surfaced via /health `queue_length` and /status
#     `queue_position`). Override with ENCODING_HEAVY_CONCURRENCY only on
#     boxes with headroom to spare.
#
#   light_executor — wheel installs (`ensure_latest_wheel`) and interactive
#     review previews (`run_preview_encoding`). These are small/fast and
#     latency-sensitive, so they get their own lane and never wait behind a
#     multi-minute encode.
HEAVY_CONCURRENCY = int(os.environ.get("ENCODING_HEAVY_CONCURRENCY", "1"))
heavy_executor = ThreadPoolExecutor(max_workers=HEAVY_CONCURRENCY)
light_executor = ThreadPoolExecutor(max_workers=2)

# Persister is initialized lazily — see persistence.py — so import-time
# stays free of GCP creds. Tests can monkey-patch this attribute.
persister = JobStatePersister()


def _persist(job_id: str) -> None:
    """Mirror a job's current state to Firestore (best-effort, never raises)."""
    state = jobs.get(job_id)
    if state is not None:
        persister.save(state)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: read back any in-progress jobs from Firestore. They were
    # interrupted by the restart that brought us here — their work_dir is
    # gone, so mark them failed with the restart code so polls return a
    # clear failure instead of 404.
    try:
        marked = persister.mark_orphans_failed_on_startup(jobs)
        if marked:
            logger.warning(
                "Marked %d orphaned encoding job(s) failed on startup", marked,
            )
    except Exception as exc:
        logger.error("Encoding worker startup orphan-recovery failed: %r", exc)
    yield


app = FastAPI(title="Encoding Worker", version="1.0.0", lifespan=lifespan)

# API key authentication
API_KEY = os.environ.get("ENCODING_API_KEY", "")


async def verify_api_key(x_api_key: str = Header(None)):
    # Verify API key for authentication
    if not API_KEY:
        logger.warning("No API key configured - authentication disabled")
        return True
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return True

# GCS client
storage_client = storage.Client()

class EncodeRequest(BaseModel):
    job_id: str
    input_gcs_path: str  # gs://bucket/path/to/inputs/
    output_gcs_path: str  # gs://bucket/path/to/outputs/
    encoding_config: dict  # Video formats to generate


class EncodePreviewRequest(BaseModel):
    job_id: str
    ass_gcs_path: str      # gs://bucket/path/to/subtitles.ass
    audio_gcs_path: str    # gs://bucket/path/to/audio.flac
    output_gcs_path: str   # gs://bucket/path/to/output.mp4
    background_color: str = "black"
    background_image_gcs_path: Optional[str] = None
    font_gcs_path: Optional[str] = None  # gs://bucket/path/to/custom-font.ttf


class RenderVideoRequest(BaseModel):
    job_id: str
    original_corrections_gcs_path: str
    updated_corrections_gcs_path: Optional[str] = None
    audio_gcs_path: str
    style_params_gcs_path: Optional[str] = None
    style_assets: Optional[dict] = None
    output_gcs_prefix: str
    artist: str
    title: str
    subtitle_offset_ms: int = 0
    video_resolution: str = "4k"
    # Multi-singer / duet rendering. When True, OutputConfig is_duet is set
    # so the SubtitlesGenerator and CDGGenerator emit per-singer styles.
    is_duet: bool = False


class JobStatus(BaseModel):
    job_id: str
    status: str  # pending, running, complete, failed
    progress: int  # 0-100
    error: Optional[str] = None
    output_files: Optional[list[str]] = None
    metadata: Optional[dict] = None
    # Set when a job was interrupted by a worker-process restart (OOM, deploy,
    # crash). Its work_dir is gone, so the client should resubmit rather than
    # treat this as a permanent failure. See persistence.mark_orphans_failed_on_startup.
    restart_failure_code: Optional[str] = None
    # How many heavy jobs (renders/encodes) are ahead of this one in the
    # serialized heavy lane (0 = running or next up). None for previews /
    # terminal jobs. Lets the client wait through a deep queue without
    # tripping the encode timeout.
    queue_position: Optional[int] = None


def download_from_gcs(gcs_uri: str, local_path: Path):
    # Download a file or folder from GCS
    # Parse gs://bucket/path
    parts = gcs_uri.replace("gs://", "").split("/", 1)
    bucket_name = parts[0]
    prefix = parts[1] if len(parts) > 1 else ""

    bucket = storage_client.bucket(bucket_name)
    blobs = list(bucket.list_blobs(prefix=prefix))

    for blob in blobs:
        # Get relative path from prefix
        rel_path = blob.name[len(prefix):].lstrip("/")
        if not rel_path:
            continue
        dest = local_path / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(dest))
        logger.info(f"Downloaded: {blob.name} -> {dest}")


def upload_to_gcs(local_path: Path, gcs_uri: str):
    # Upload a file or folder to GCS
    parts = gcs_uri.replace("gs://", "").split("/", 1)
    bucket_name = parts[0]
    prefix = parts[1].rstrip("/") if len(parts) > 1 else ""

    bucket = storage_client.bucket(bucket_name)

    if local_path.is_file():
        blob_name = f"{prefix}/{local_path.name}" if prefix else local_path.name
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(str(local_path))
        logger.info(f"Uploaded: {local_path} -> gs://{bucket_name}/{blob_name}")
    else:
        for file in local_path.rglob("*"):
            if file.is_file():
                rel_path = file.relative_to(local_path)
                blob_name = f"{prefix}/{rel_path}" if prefix else str(rel_path)
                blob = bucket.blob(blob_name)
                blob.upload_from_filename(str(file))
                logger.info(f"Uploaded: {file} -> gs://{bucket_name}/{blob_name}")


def download_single_file_from_gcs(gcs_uri: str, local_path: Path):
    # Download a single file from GCS
    parts = gcs_uri.replace("gs://", "").split("/", 1)
    bucket_name = parts[0]
    blob_name = parts[1] if len(parts) > 1 else ""

    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    blob.download_to_filename(str(local_path))
    logger.info(f"Downloaded: {gcs_uri} -> {local_path}")


def upload_single_file_to_gcs(local_path: Path, gcs_uri: str):
    # Upload a single file to a specific GCS path
    parts = gcs_uri.replace("gs://", "").split("/", 1)
    bucket_name = parts[0]
    blob_name = parts[1] if len(parts) > 1 else ""

    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(str(local_path))
    logger.info(f"Uploaded: {local_path} -> {gcs_uri}")


# Style asset disk cache directory
# Persists across jobs to avoid re-downloading the same theme assets (fonts, backgrounds)
STYLE_CACHE_DIR = Path("/var/cache/karaoke-gen/styles")


def download_with_cache(gcs_uri: str, local_path: Path, cache_dir: Optional[Path] = STYLE_CACHE_DIR):
    """Download a file from GCS, using a local disk cache to skip repeated downloads.

    Cache key is SHA-256 of the GCS URI. Cache hits are validated by comparing
    the cached file size against the GCS object size (via a lightweight metadata
    call). On size mismatch the stale entry is replaced.
    When cache_dir is None, downloads directly (no caching).
    """
    if cache_dir is None:
        download_single_file_from_gcs(gcs_uri, local_path)
        return

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_key = hashlib.sha256(gcs_uri.encode()).hexdigest()
    cached_path = cache_dir / cache_key

    if cached_path.exists():
        # Validate cached file against GCS object size
        try:
            parts = gcs_uri.replace("gs://", "").split("/", 1)
            bucket_name = parts[0]
            blob_name = parts[1] if len(parts) > 1 else ""
            blob = storage_client.bucket(bucket_name).blob(blob_name)
            blob.reload()  # Fetch metadata (lightweight HEAD request)
            gcs_size = blob.size

            cached_size = cached_path.stat().st_size
            if gcs_size is not None and cached_size != gcs_size:
                logger.warning(
                    f"Cache stale for {gcs_uri}: cached={cached_size}B, GCS={gcs_size}B. Re-downloading."
                )
                cached_path.unlink()
            else:
                logger.info(f"Cache hit for {gcs_uri} -> {cached_path}")
                shutil.copy2(str(cached_path), str(local_path))
                return
        except Exception as e:
            # On transient GCS errors, serve from cache rather than failing the job
            logger.warning(f"Cache validation failed for {gcs_uri}: {e}. Serving from cache.")
            shutil.copy2(str(cached_path), str(local_path))
            return

    logger.info(f"Cache miss for {gcs_uri}, downloading...")
    download_single_file_from_gcs(gcs_uri, local_path)
    # Store in cache
    shutil.copy2(str(local_path), str(cached_path))


def run_preview_encoding(job_id: str, work_dir: Path, request: "EncodePreviewRequest"):
    # Run FFmpeg encoding for preview video (480x270, fast settings)
    jobs[job_id]["status"] = "running"
    jobs[job_id]["progress"] = 10
    _persist(job_id)

    try:
        # Download input files
        ass_path = work_dir / "subtitles.ass"
        audio_path = work_dir / "audio.flac"

        download_single_file_from_gcs(request.ass_gcs_path, ass_path)
        jobs[job_id]["progress"] = 20

        download_single_file_from_gcs(request.audio_gcs_path, audio_path)
        jobs[job_id]["progress"] = 30

        # Download background image if provided
        bg_image_path = None
        if request.background_image_gcs_path:
            bg_image_path = work_dir / "background.png"
            download_single_file_from_gcs(request.background_image_gcs_path, bg_image_path)

        # Download custom font if provided and register with fontconfig
        if request.font_gcs_path:
            # Use standard fontconfig location that's already in the search path
            fonts_dir = Path("/usr/local/share/fonts/custom")
            fonts_dir.mkdir(parents=True, exist_ok=True)
            font_filename = request.font_gcs_path.split("/")[-1]
            font_path = fonts_dir / font_filename
            download_single_file_from_gcs(request.font_gcs_path, font_path)
            logger.info(f"Downloaded custom font: {font_path}")
            # Update fontconfig cache so libass can find the font
            subprocess.run(["fc-cache", "-fv"], capture_output=True)
            logger.info(f"Updated fontconfig cache with custom font: {font_filename}")

        # Build FFmpeg command
        output_path = work_dir / "preview.mp4"

        # Escape special characters in path for FFmpeg filter syntax
        # FFmpeg filter parsing requires escaping: \ : , [ ] ;
        def escape_ffmpeg_filter_path(path: str) -> str:
            # Note: Extra escaping needed since this is inside a triple-quoted string in Pulumi
            return path.replace("\\", "\\\\").replace(":", "\\:").replace(",", "\\,").replace("[", "\\[").replace("]", "\\]").replace(";", "\\;")

        escaped_ass_path = escape_ffmpeg_filter_path(str(ass_path))

        # Base command with frame rate
        cmd = ["ffmpeg", "-y", "-r", "24"]

        # Video input: background image or solid color
        if bg_image_path and bg_image_path.exists():
            cmd.extend(["-loop", "1", "-i", str(bg_image_path)])
            # Scale and pad background to 480x270
            vf = f"scale=480:270:force_original_aspect_ratio=decrease,pad=480:270:(ow-iw)/2:(oh-ih)/2,ass={escaped_ass_path}"
        else:
            # Solid color background
            color = request.background_color or "black"
            cmd.extend(["-f", "lavfi", "-i", f"color=c={color}:s=480x270:r=24"])
            vf = f"ass={escaped_ass_path}"

        # Audio input
        cmd.extend(["-i", str(audio_path)])

        # Video filter and encoding settings
        cmd.extend([
            "-vf", vf,
            "-c:a", "aac", "-b:a", "96k",
            "-c:v", "libx264",
            "-preset", "superfast",
            "-crf", "28",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-threads", "8",
            "-shortest",
            str(output_path)
        ])

        jobs[job_id]["progress"] = 40
        logger.info(f"Running preview encoding: {' '.join(cmd)}")

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            logger.error(f"FFmpeg failed: {result.stderr}")
            raise RuntimeError(f"FFmpeg preview encoding failed: {result.stderr[-500:]}")

        jobs[job_id]["progress"] = 80
        logger.info(f"Preview encoded: {output_path}")

        # Upload output to GCS
        upload_single_file_to_gcs(output_path, request.output_gcs_path)
        jobs[job_id]["progress"] = 95

        jobs[job_id]["output_files"] = [request.output_gcs_path]
        return output_path

    except Exception as e:
        logger.error(f"Preview encoding failed: {e}")
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)
        _persist(job_id)
        raise


def run_render_video(job_id: str, work_dir: Path, request: "RenderVideoRequest"):
    """Run video rendering using OutputGenerator from the karaoke-gen wheel.

    Downloads corrections, audio, and style assets from GCS, runs OutputGenerator
    to produce with_vocals.mkv and subtitle files, then uploads results.
    Style assets use disk cache to avoid repeated downloads of the same theme.

    This mirrors the logic in render_video_worker.py but runs on the GCE VM
    instead of Cloud Run, providing more memory and CPU for ffmpeg/libass.
    """
    jobs[job_id]["status"] = "running"
    jobs[job_id]["progress"] = 5
    _persist(job_id)

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
            if isinstance(updated_data, dict) and "corrections" in updated_data:
                correction_result = CorrectionOperations.update_correction_result_with_data(
                    base_result, updated_data
                )
                logger.info(f"[job:{job_id}] Applied user corrections")
            else:
                logger.warning(f"[job:{job_id}] corrections_updated.json exists but has no 'corrections' key, using base result")
                correction_result = base_result
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
                    ext = os.path.splitext(gcs_path)[1]
                    style_font = work_dir / "style" / f"{asset_key}{ext}"
                    if style_font.exists():
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
            is_duet=request.is_duet,
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
        _persist(job_id)
        raise RuntimeError(error_msg) from e

    except Exception as e:
        logger.error(f"[job:{job_id}] Render video failed: {e}")
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)
        _persist(job_id)
        raise


# Wheel install/verification tuning.
#
# On a freshly-provisioned worker the Packer image only pre-installs a handful
# of packages (fastapi/uvicorn/gcs/...) and boot installs the wheel with
# --no-deps, so the FIRST job's install here has to resolve the entire
# karaoke-gen dependency tree (torch, langchain, tenacity, ...). That can take
# well over 5 minutes and, if it times out or partially completes, leaves the
# environment importable-but-broken (e.g. "No module named 'tenacity'"). We
# therefore retry, and — critically — verify the import chain actually works
# before declaring success, since pip can return 0 while a dependency is still
# missing.
WHEEL_INSTALL_TIMEOUT = int(os.environ.get("WHEEL_INSTALL_TIMEOUT", "900"))
WHEEL_INSTALL_ATTEMPTS = int(os.environ.get("WHEEL_INSTALL_ATTEMPTS", "3"))

# Modules exercised by the render (OutputGenerator) and encode
# (LocalEncodingService) code paths. Importing these confirms the whole
# dependency tree they pull in — including transitive deps like tenacity — is
# actually present, not just that `pip install` exited 0.
_WHEEL_IMPORT_CHECK = (
    "import tenacity; "
    "from karaoke_gen.lyrics_transcriber.output.generator import OutputGenerator; "
    "from karaoke_gen.lyrics_transcriber.correction.operations import CorrectionOperations; "
    "from backend.services.local_encoding_service import LocalEncodingService"
)


def verify_wheel_imports():
    '''Verify the karaoke-gen render/encode import chain is fully importable.

    Runs in a subprocess so a broken/partial install surfaces as a clean
    boolean instead of crashing (or half-importing into) the worker process.
    Returns True if every module imports, False otherwise.
    '''
    try:
        check = subprocess.run(
            [sys.executable, "-c", _WHEEL_IMPORT_CHECK],
            capture_output=True, text=True, timeout=180,
        )
    except subprocess.TimeoutExpired:
        logger.warning("Wheel import verification timed out")
        return False
    if check.returncode != 0:
        # Tail of stderr carries the actionable ModuleNotFoundError.
        logger.warning(f"Wheel import verification failed: {check.stderr.strip()[-500:]}")
        return False
    return True


def ensure_latest_wheel():
    '''Download, install, and verify the latest karaoke-gen wheel from GCS.

    Called at the start of each job to enable hot code updates without restart.
    In-progress jobs continue with their version, new jobs get latest code.

    Retries the install and verifies the full import chain before returning, so
    a transient/partial dependency install can't leave the worker running jobs
    against a broken environment. Returns True only when the wheel is installed
    AND its render/encode imports succeed.
    '''
    try:
        logger.info("Checking for latest karaoke-gen wheel in GCS...")

        # Sort helper: extract version from a wheel name/URI like
        # karaoke_gen-0.116.1-py3-none-any.whl
        def extract_version(wheel_path):
            match = re.search(r'karaoke_gen-([0-9.]+)-', wheel_path)
            if match:
                return Version(match.group(1))
            return Version("0.0.0")  # Fallback for unparseable filenames

        # List wheel names only (fast), then copy just the single latest one.
        # NOTE: `gsutil cp gs://.../karaoke_gen-*.whl /tmp/` would download the
        # ENTIRE wheels/ directory (hundreds of wheels, multiple GiB) on every
        # job and blow the timeout on fresh workers — the historical cause of
        # "no wheel found" / partial installs. Listing + single copy is ~2s.
        ls_result = subprocess.run(
            ["gsutil", "ls", "gs://karaoke-gen-storage-nomadkaraoke/wheels/karaoke_gen-*.whl"],
            capture_output=True, text=True, timeout=60
        )
        # Filter out karaoke_gen-current.whl (not a valid PEP 427 wheel name)
        wheel_uris = [
            line.strip() for line in ls_result.stdout.splitlines()
            if line.strip() and '-current' not in line
        ]

        if not wheel_uris:
            logger.warning("No versioned wheel found in GCS, using fallback encoding logic")
            return False

        wheel_uris.sort(key=extract_version)
        latest_uri = wheel_uris[-1]
        wheel_path = os.path.join("/tmp", latest_uri.rsplit("/", 1)[-1])

        cp_result = subprocess.run(
            ["gsutil", "cp", latest_uri, wheel_path],
            capture_output=True, text=True, timeout=120
        )
        if cp_result.returncode != 0:
            logger.warning(f"Failed to download wheel {latest_uri}: {cp_result.stderr.strip()[-500:]}")
            return False

        # Install (with deps), then verify the import chain is complete. Retry a
        # failed or partial install: pip is resumable, so a second pass fills in
        # dependencies a timed-out first pass left missing (e.g. tenacity).
        for attempt in range(1, WHEEL_INSTALL_ATTEMPTS + 1):
            logger.info(f"Installing wheel (attempt {attempt}/{WHEEL_INSTALL_ATTEMPTS}): {wheel_path}")
            try:
                install_result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--upgrade", "--quiet", wheel_path],
                    capture_output=True, text=True, timeout=WHEEL_INSTALL_TIMEOUT
                )
            except subprocess.TimeoutExpired:
                logger.warning(
                    f"Wheel install timed out after {WHEEL_INSTALL_TIMEOUT}s "
                    f"(attempt {attempt}/{WHEEL_INSTALL_ATTEMPTS})"
                )
                install_result = None

            if install_result is not None and install_result.returncode != 0:
                logger.warning(
                    f"Wheel installation failed (attempt {attempt}/{WHEEL_INSTALL_ATTEMPTS}): "
                    f"{install_result.stderr.strip()[-500:]}"
                )
            elif install_result is not None:
                logger.info(f"pip install completed for wheel: {wheel_path}")

            # A clean `pip install` exit is not sufficient — confirm the deps
            # the render/encode paths need are actually importable before we let
            # a job run against this environment.
            if verify_wheel_imports():
                logger.info(f"Successfully installed and verified wheel: {wheel_path}")
                return True

            logger.warning(
                f"Wheel environment incomplete after attempt {attempt}/{WHEEL_INSTALL_ATTEMPTS}; "
                "dependencies are missing or failed to import"
            )

        logger.error(
            f"Wheel not ready after {WHEEL_INSTALL_ATTEMPTS} attempts: import verification "
            "still failing (environment is missing dependencies)"
        )
        return False

    except subprocess.TimeoutExpired:
        logger.warning("Wheel download timed out, using fallback")
        return False
    except Exception as e:
        logger.warning(f"Failed to ensure latest wheel: {e}")
        return False


def find_file(work_dir: Path, *patterns):
    '''Find a file matching any of the given glob patterns.'''
    for pattern in patterns:
        matches = list(work_dir.glob(f"**/{pattern}"))
        if matches:
            return matches[0]
    return None


def generate_mov_from_png(png_path: Path, mov_path: Path, duration: int = 5) -> Path:
    """Generate a MOV video from a static PNG image using FFmpeg.

    This runs on the GCE encoding worker (not Cloud Run) because 4K H.264
    encoding requires more memory than the right-sized Cloud Run container
    provides (2Gi). See PR #640 / #647.

    Args:
        png_path: Path to input PNG image
        mov_path: Path for output MOV video
        duration: Video duration in seconds (default 5)

    Returns:
        Path to generated MOV file

    Raises:
        RuntimeError: If FFmpeg fails
    """
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats", "-y",
        "-loop", "1", "-framerate", "30", "-i", str(png_path),
        "-f", "lavfi", "-i", "anullsrc",
        "-c:v", "libx264", "-r", "30", "-t", str(duration),
        "-pix_fmt", "yuv420p", "-vf", "scale=3840:2160",
        "-c:a", "aac", "-shortest",
        str(mov_path),
    ]
    logger.info(f"Generating MOV from PNG: {png_path.name} -> {mov_path.name}")
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg failed to generate {mov_path.name}: {result.stderr}")
    if not mov_path.exists() or mov_path.stat().st_size < 1024:
        raise RuntimeError(f"FFmpeg produced invalid output for {mov_path.name}")
    logger.info(f"Generated MOV: {mov_path.name} ({mov_path.stat().st_size} bytes)")
    return mov_path


# Portrait (9:16) karaoke render is default-on; set PORTRAIT_RENDER_ENABLED=false to disable.
PORTRAIT_RENDER_ENABLED = os.environ.get("PORTRAIT_RENDER_ENABLED", "true").lower() == "true"
PORTRAIT_SUFFIX = "(Final Karaoke Portrait 1080x1920)"


def _resolve_portrait_font(styles: dict, work_dir: Path) -> Optional[str]:
    """Resolve a usable .ttf file for the portrait header (PIL needs a file path).

    libass resolves the lyrics font via fontconfig (the theme font is registered by
    the earlier render step on the same VM), but PIL needs an actual file. Ask
    fontconfig for the theme font family's file; fall back to any bundled style font.
    """
    family = (styles.get("karaoke", {}) or {}).get("font") or "Avenir Next Bold"
    try:
        res = subprocess.run(
            ["fc-match", "--format=%{file}", family],
            capture_output=True, text=True, timeout=5,
        )
        if res.returncode == 0 and res.stdout and os.path.isfile(res.stdout.strip()):
            return res.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    for pattern in ("style/*.ttf", "style/*.otf", "*.ttf"):
        hit = next(iter(work_dir.glob(pattern)), None)
        if hit:
            return str(hit)
    return None


def render_portrait_into_outputs(
    job_id: str, work_dir: Path, output_dir: Path, base_name: str,
    artist: str, title: str, instrumental: Path, config: dict,
) -> Optional[str]:
    """Render the portrait (9:16) karaoke video into ``output_dir`` (best-effort).

    Reuses the same corrected-lyrics data + theme as the landscape render. Uses the
    un-padded instrumental and un-countdown-processed segments so the two stay in
    sync without the countdown offset (the portrait title card provides lead-in).

    This is additive and non-fatal: any failure logs and returns ``None`` so the
    landscape outputs and distribution proceed unaffected.
    """
    if not PORTRAIT_RENDER_ENABLED:
        logger.info(f"[job:{job_id}] Portrait render disabled via PORTRAIT_RENDER_ENABLED")
        return None
    try:
        from karaoke_gen.lyrics_transcriber.types import CorrectionResult
        from karaoke_gen.portrait import render_portrait_video, PortraitBrandConfig

        # Corrected lyrics: prefer the reviewed corrections_updated.json.
        corrections_file = None
        for candidate in ("lyrics/corrections_updated.json", "lyrics/corrections.json"):
            p = work_dir / candidate
            if p.is_file():
                corrections_file = p
                break
        if corrections_file is None:
            found = next(iter(work_dir.glob("**/corrections*.json")), None)
            corrections_file = found
        if corrections_file is None:
            logger.warning(f"[job:{job_id}] Portrait skipped: no corrections JSON found")
            return None
        with open(corrections_file, "r", encoding="utf-8") as f:
            correction_result = CorrectionResult.from_dict(json.load(f))

        # Theme styles (optional — renderer fills defaults).
        styles = {}
        style_params = work_dir / "style" / "style_params.json"
        if not style_params.is_file():
            style_params = next(iter(work_dir.glob("**/style_params.json")), None)
        if style_params and Path(style_params).is_file():
            with open(style_params, "r", encoding="utf-8") as f:
                styles = json.load(f)

        font_path = _resolve_portrait_font(styles, work_dir)
        brand = PortraitBrandConfig(
            brand_text=config.get("portrait_brand_text", "NOMAD KARAOKE"),
            footer_text=config.get("portrait_footer_text", "nomadkaraoke.com"),
            font_path=font_path,
        )
        out_path = output_dir / f"{base_name} {PORTRAIT_SUFFIX}.mp4"
        logger.info(f"[job:{job_id}] Rendering portrait karaoke video -> {out_path.name}")
        render_portrait_video(
            correction_result=correction_result,
            instrumental_path=str(instrumental),
            styles=styles,
            artist=artist,
            title=title,
            output_path=str(out_path),
            brand=brand,
            font_path=font_path,
            work_dir=str(work_dir / "portrait_tmp"),
            logger=logger,
        )
        if out_path.is_file():
            logger.info(f"[job:{job_id}] Portrait render complete ({out_path.stat().st_size} bytes)")
            return str(out_path)
        logger.warning(f"[job:{job_id}] Portrait render produced no file")
        return None
    except Exception as e:
        logger.error(f"[job:{job_id}] Portrait render failed (non-fatal): {e}", exc_info=True)
        return None


def run_encoding(job_id: str, work_dir: Path, config: dict):
    '''Run encoding using LocalEncodingService (single source of truth).

    Uses LocalEncodingService from the installed karaoke-gen wheel to ensure
    output files match local CLI exactly:
    - Proper names like "Artist - Title (Final Karaoke Lossless 4k).mp4"
    - Concatenated title + karaoke + end screens
    - All formats: lossless 4K MP4, lossy 4K MP4, lossless MKV, 720p MP4

    Requires the karaoke-gen wheel to be installed (done by ensure_latest_wheel).
    '''
    jobs[job_id]["status"] = "running"
    jobs[job_id]["progress"] = 10
    _persist(job_id)

    try:
        # Import LocalEncodingService from installed wheel (required, no fallback)
        from backend.services.local_encoding_service import LocalEncodingService, EncodingConfig
        logger.info("Using LocalEncodingService from installed wheel")

        # Get artist/title from config for proper naming
        artist = config.get("artist", "Unknown Artist")
        title = config.get("title", "Unknown Title")
        # Sanitize for use in file paths (e.g., title "PORTRAIT/WALK/BORN" → "PORTRAIT_WALK_BORN")
        safe_artist = re.sub(r'[<>:"/\\|?*]', '_', artist)
        safe_title = re.sub(r'[<>:"/\\|?*]', '_', title)
        base_name = f"{safe_artist} - {safe_title}"
        logger.info(f"Encoding for: {base_name}")

        # Find input files in work_dir
        # Title/end screens: look for MOV first, fall back to generating from PNG.
        # Since PR #640 right-sized Cloud Run to 2Gi, the screens_worker only generates
        # PNG/JPG images — MOV video generation now happens here on the GCE worker.
        title_video = find_file(work_dir, "screens/title.mov", "*Title*.mov", "*title*.mov")
        end_video = find_file(work_dir, "screens/end.mov", "*End*.mov", "*end*.mov")

        # Get intro/end video durations from style config (default 5s)
        intro_duration = config.get("intro_video_duration", 5)
        end_duration = config.get("end_video_duration", 5)

        # Generate title MOV from PNG if missing or invalid (< 1KB = empty shell)
        if not title_video or title_video.stat().st_size < 1024:
            title_png = find_file(work_dir, "screens/title.png", "*Title*.png", "*title*.png")
            if title_png:
                title_mov_path = title_png.parent / "title.mov"
                logger.info(f"Title MOV missing/invalid, generating from PNG: {title_png}")
                title_video = generate_mov_from_png(title_png, title_mov_path, duration=intro_duration)
            elif title_video:
                logger.warning(f"Title MOV is only {title_video.stat().st_size} bytes and no PNG found")

        # Generate end MOV from PNG if missing or invalid
        if not end_video or (end_video and end_video.stat().st_size < 1024):
            end_png = find_file(work_dir, "screens/end.png", "*End*.png", "*end*.png")
            if end_png:
                end_mov_path = end_png.parent / "end.mov"
                logger.info(f"End MOV missing/invalid, generating from PNG: {end_png}")
                end_video = generate_mov_from_png(end_png, end_mov_path, duration=end_duration)
            elif end_video:
                logger.warning(f"End MOV is only {end_video.stat().st_size} bytes and no PNG found")

        # Karaoke video - search for With Vocals or main karaoke video
        # IMPORTANT: Search order matters! Search specific paths first to avoid picking up
        # old encoding outputs from finals/ directory when re-encoding after reset
        karaoke_video = find_file(
            work_dir,
            # First: Look in videos/ subdirectory (where render_video_worker puts output)
            "videos/with_vocals.mkv", "videos/with_vocals.mov",
            "videos/*With Vocals*.mkv", "videos/*With Vocals*.mov",
            # Second: Look for specific filename patterns (case-insensitive variants)
            "*with_vocals*.mkv", "*with_vocals*.mov",
            "*With Vocals*.mov", "*With Vocals*.mkv",
            "*vocals*.mkv", "*vocals*.mov",
            "*Vocals*.mov", "*Vocals*.mkv",
            # Last resort: any video file (but excluding finals/outputs)
            "*.mkv", "*.mov"
        )
        # Exclude title/end/output/finals videos to avoid re-encoding old outputs
        if karaoke_video:
            path_str = str(karaoke_video).lower()
            name_lower = karaoke_video.name.lower()
            if ("title" in name_lower or "end" in name_lower or
                "outputs" in path_str or "finals" in path_str or
                "final karaoke" in name_lower or "lossless" in name_lower or
                "lossy" in name_lower or "720p" in name_lower):
                # Search more specifically for karaoke video in videos/ directory only
                karaoke_video = find_file(
                    work_dir,
                    "videos/with_vocals.mkv", "videos/with_vocals.mov",
                    "videos/*vocals*.mkv", "videos/*vocals*.mov"
                )

        # Instrumental audio - respect user's selection from encoding config
        instrumental_selection = config.get("instrumental_selection", "clean")
        existing_instrumental = config.get("existing_instrumental")
        logger.info(f"Instrumental selection from config: {instrumental_selection}")
        if existing_instrumental:
            logger.info(f"Existing instrumental from config: {existing_instrumental}")

        if existing_instrumental:
            # User-provided instrumental uploaded at job creation
            # Downloaded to work_dir by process_job before run_encoding is called
            instrumental = find_file(
                work_dir,
                "*existing_instrumental*", "*Instrumental User*",
            )
        elif instrumental_selection == "custom":
            # Custom instrumental created via mute-region editing in review UI
            instrumental = find_file(
                work_dir,
                "*custom_instrumental*.flac", "*Instrumental Custom*.flac",
                "*custom_instrumental*.mp3",
            )
        elif instrumental_selection == "with_backing":
            # User selected instrumental with backing vocals
            instrumental = find_file(
                work_dir,
                "*instrumental_with_backing*.flac", "*Instrumental Backing*.flac",
                "*with_backing*.flac", "*Backing*.flac",
                "*instrumental*.flac", "*Instrumental*.flac",
                "*instrumental*.wav"
            )
        else:
            # Default to clean instrumental
            instrumental = find_file(
                work_dir,
                "*instrumental_clean*.flac", "*Instrumental Clean*.flac",
                "*instrumental*.flac", "*Instrumental*.flac",
                "*instrumental*.wav"
            )

        logger.info(f"Found files:")
        logger.info(f"  Title video: {title_video}")
        logger.info(f"  Karaoke video: {karaoke_video}")
        logger.info(f"  End video: {end_video}")
        logger.info(f"  Instrumental ({instrumental_selection}): {instrumental}")

        # Check for countdown padding - if vocals were padded, instrumental must match
        countdown_padding_seconds = config.get("countdown_padding_seconds")
        if countdown_padding_seconds:
            logger.info(f"  Countdown padding: {countdown_padding_seconds}s - will be handled by LocalEncodingService")

        # Validate required files
        if not title_video:
            raise ValueError(f"No title video found in {work_dir}. Check screens/ subdirectory.")
        if not karaoke_video:
            raise ValueError(f"No karaoke video found in {work_dir}")
        if not instrumental:
            raise ValueError(f"No instrumental audio found in {work_dir}")

        output_dir = work_dir / "outputs"
        output_dir.mkdir(exist_ok=True)

        jobs[job_id]["progress"] = 20

        # Build encoding config with proper file names
        # Note: countdown_padding_seconds is passed to LocalEncodingService which handles padding
        encoding_config = EncodingConfig(
            title_video=str(title_video),
            karaoke_video=str(karaoke_video),
            instrumental_audio=str(instrumental),
            end_video=str(end_video) if end_video else None,
            output_karaoke_mp4=str(output_dir / f"{base_name} (Karaoke).mp4"),
            output_with_vocals_mp4=str(output_dir / f"{base_name} (With Vocals).mp4"),
            output_lossless_4k_mp4=str(output_dir / f"{base_name} (Final Karaoke Lossless 4k).mp4"),
            output_lossy_4k_mp4=str(output_dir / f"{base_name} (Final Karaoke Lossy 4k).mp4"),
            output_lossless_mkv=str(output_dir / f"{base_name} (Final Karaoke Lossless 4k).mkv"),
            output_720p_mp4=str(output_dir / f"{base_name} (Final Karaoke Lossy 720p).mp4"),
            countdown_padding_seconds=countdown_padding_seconds,
        )

        # Create service and run encoding
        service = LocalEncodingService(logger=logger)

        jobs[job_id]["progress"] = 30
        logger.info("Starting LocalEncodingService.encode_all_formats()")

        result = service.encode_all_formats(encoding_config)

        if not result.success:
            raise RuntimeError(f"Encoding failed: {result.error}")

        jobs[job_id]["progress"] = 90

        # Include the standalone 5-second screen videos in the uploaded outputs so they
        # land in the Dropbox folder. They are generated above (or passed in) purely as
        # concat inputs and used to live only in screens/; copy them into outputs/ with
        # the canonical names. Regression fix: screen MOVs disappeared from Dropbox after
        # #647/#650 moved screen generation to this encoder.
        for screen_video, suffix in ((title_video, "Title"), (end_video, "End")):
            if screen_video and Path(screen_video).is_file():
                dest = output_dir / f"{base_name} ({suffix}).mov"
                try:
                    shutil.copy2(screen_video, dest)
                    logger.info(f"Staged screen video for upload: {dest.name}")
                except Exception as e:
                    logger.warning(f"Failed to stage {suffix} MOV into outputs: {e}")

        # Portrait (9:16) karaoke render — additive, non-fatal. Written into output_dir
        # so it rides the same GCS upload + Dropbox distribution as the other finals.
        jobs[job_id]["progress"] = 92
        render_portrait_into_outputs(
            job_id, work_dir, output_dir, base_name, artist, title, instrumental, config,
        )

        # Collect output files
        output_files = [str(f) for f in output_dir.glob("*") if f.is_file()]
        jobs[job_id]["output_files"] = output_files

        logger.info(f"Encoding complete. Output files: {output_files}")
        return output_dir

    except ImportError as e:
        # No fallback - wheel must be installed
        error_msg = (
            f"LocalEncodingService not available: {e}. "
            "The karaoke-gen wheel must be installed. "
            "Check that ensure_latest_wheel() succeeded and wheel exists in GCS."
        )
        logger.error(error_msg)
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = error_msg
        _persist(job_id)
        raise RuntimeError(error_msg) from e

    except Exception as e:
        logger.error(f"Encoding failed: {e}")
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)
        _persist(job_id)
        raise


async def process_job(job_id: str, request: EncodeRequest):
    # Process an encoding job asynchronously
    try:
        # Download and install latest wheel at job start (allows hot updates without restart)
        # This means in-progress jobs continue with their version, new jobs get latest code.
        # Fail fast (retryable) if the environment isn't ready rather than proceeding into a
        # confusing "No module named ..." ImportError deep in encoding.
        # ensure_latest_wheel() makes blocking subprocess calls (install can retry for many
        # minutes) — run it in the thread pool so the async event loop keeps serving health
        # checks and status polls.
        loop = asyncio.get_event_loop()
        if not await loop.run_in_executor(light_executor, ensure_latest_wheel):
            raise RuntimeError(
                "karaoke-gen wheel/dependencies not ready on this worker after retries; "
                "job should be retried on a healthy worker"
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir) / "work"
            work_dir.mkdir()

            # Download input files
            jobs[job_id]["progress"] = 5
            logger.info(f"Downloading from {request.input_gcs_path}")
            download_from_gcs(request.input_gcs_path, work_dir)

            # Download existing instrumental if present (stored under uploads/, not jobs/)
            existing_instrumental = request.encoding_config.get("existing_instrumental") if request.encoding_config else None
            if existing_instrumental:
                # Extract bucket name from input_gcs_path (gs://bucket/jobs/...)
                bucket_name = request.input_gcs_path.replace("gs://", "").split("/", 1)[0]
                ext = Path(existing_instrumental).suffix.lower()
                dest = work_dir / f"existing_instrumental{ext}"
                gcs_uri = f"gs://{bucket_name}/{existing_instrumental}"
                logger.info(f"Downloading existing instrumental: {gcs_uri} -> {dest}")
                download_single_file_from_gcs(gcs_uri, dest)

            # Run encoding in the heavy lane (CPU/RAM-bound; serialized to
            # avoid OOM — see heavy_executor comment).
            loop = asyncio.get_event_loop()
            output_dir = await loop.run_in_executor(
                heavy_executor,
                run_encoding,
                job_id,
                work_dir,
                request.encoding_config
            )

            # Upload outputs
            jobs[job_id]["progress"] = 95
            logger.info(f"Uploading to {request.output_gcs_path}")
            upload_to_gcs(output_dir, request.output_gcs_path)

            # Convert local paths to blob paths (backend expects blob paths, not full gs:// URIs)
            # output_gcs_path is like "gs://bucket/jobs/id/encoded/"
            # We need paths like "jobs/id/encoded/Artist - Title (Final Karaoke Lossless 4k).mp4"
            gcs_path = request.output_gcs_path.replace("gs://", "")
            parts = gcs_path.split("/", 1)
            prefix = parts[1].rstrip("/") if len(parts) > 1 else ""
            local_output_files = jobs[job_id].get("output_files", [])
            blob_paths = []
            for local_path in local_output_files:
                filename = Path(local_path).name
                blob_path = f"{prefix}/{filename}" if prefix else filename
                blob_paths.append(blob_path)
            jobs[job_id]["output_files"] = blob_paths
            logger.info(f"Output files (blob paths): {blob_paths}")

            jobs[job_id]["status"] = "complete"
            jobs[job_id]["progress"] = 100
            _persist(job_id)
            logger.info(f"Job {job_id} complete")

    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}")
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)
        _persist(job_id)


async def process_preview_job(job_id: str, request: EncodePreviewRequest):
    # Process a preview encoding job asynchronously
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir) / "work"
            work_dir.mkdir()

            # Run preview encoding in the light lane so interactive review
            # previews never queue behind a multi-minute full encode.
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                light_executor,
                run_preview_encoding,
                job_id,
                work_dir,
                request
            )

            jobs[job_id]["status"] = "complete"
            jobs[job_id]["progress"] = 100
            _persist(job_id)
            logger.info(f"Preview job {job_id} complete")

    except Exception as e:
        logger.error(f"Preview job {job_id} failed: {e}")
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)
        _persist(job_id)


async def process_render_video_job(job_id: str, request: RenderVideoRequest):
    # Process a render-video job asynchronously
    try:
        # Download and install latest wheel at job start (allows hot updates without restart).
        # Fail fast (retryable) if the environment isn't ready rather than proceeding into a
        # confusing "No module named 'tenacity'" ImportError inside render_video.
        # Run in the thread pool: ensure_latest_wheel() blocks on subprocess installs (which
        # can retry for many minutes) and must not stall the async event loop.
        loop = asyncio.get_event_loop()
        if not await loop.run_in_executor(light_executor, ensure_latest_wheel):
            raise RuntimeError(
                "karaoke-gen wheel/dependencies not ready on this worker after retries; "
                "job should be retried on a healthy worker"
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir) / "work"
            work_dir.mkdir()

            # Run render-video in the heavy lane (CPU/RAM-bound; serialized to
            # avoid OOM — see heavy_executor comment).
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                heavy_executor,
                run_render_video,
                job_id,
                work_dir,
                request
            )

            jobs[job_id]["status"] = "complete"
            jobs[job_id]["progress"] = 100
            _persist(job_id)
            logger.info(f"Render video job {job_id} complete")

    except Exception as e:
        logger.error(f"Render video job {job_id} failed: {e}")
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)
        _persist(job_id)


@app.post("/encode-preview")
async def submit_preview_encode_job(request: EncodePreviewRequest, background_tasks: BackgroundTasks, _auth: bool = Depends(verify_api_key)):
    # Submit a preview encoding job
    job_id = request.job_id

    # If job already exists, return cached result or current status
    if job_id in jobs:
        existing_job = jobs[job_id]
        if existing_job["status"] == "complete":
            # Return cached result - preview already encoded
            return {"status": "cached", "job_id": job_id, "output_path": existing_job.get("output_path")}
        elif existing_job["status"] == "failed":
            # Previous attempt failed, allow retry by replacing the job
            pass
        else:
            # Job is still in progress
            return {"status": "in_progress", "job_id": job_id}

    jobs[job_id] = {
        "job_id": job_id,
        "status": "pending",
        "progress": 0,
        "error": None,
        "output_files": None,
        "kind": "preview",  # light lane — never counted in the heavy queue
    }
    _persist(job_id)

    background_tasks.add_task(process_preview_job, job_id, request)

    return {"status": "accepted", "job_id": job_id}


@app.post("/render-video")
async def submit_render_video_job(request: RenderVideoRequest, background_tasks: BackgroundTasks, _auth: bool = Depends(verify_api_key)):
    # Submit a render-video job
    job_id = request.job_id

    # If job already exists, return cached result or current status
    if job_id in jobs:
        existing_job = jobs[job_id]
        if existing_job["status"] == "complete":
            return {"status": "cached", "job_id": job_id, "output_files": existing_job.get("output_files"), "metadata": existing_job.get("metadata")}
        elif existing_job["status"] == "failed":
            # Previous attempt failed, allow retry by replacing the job
            pass
        else:
            # Job is still in progress
            return {"status": "in_progress", "job_id": job_id}

    jobs[job_id] = {
        "job_id": job_id,
        "status": "pending",
        "progress": 0,
        "error": None,
        "output_files": None,
        "metadata": None,
        "kind": "render",  # heavy lane
    }
    _persist(job_id)

    background_tasks.add_task(process_render_video_job, job_id, request)

    return {"status": "accepted", "job_id": job_id}


@app.post("/encode")
async def submit_encode_job(request: EncodeRequest, background_tasks: BackgroundTasks, _auth: bool = Depends(verify_api_key)):
    # Submit an encoding job
    job_id = request.job_id

    # If job already exists, return cached result or current status
    if job_id in jobs:
        existing_job = jobs[job_id]
        if existing_job["status"] == "complete":
            # Return cached result - encoding already done
            return {"status": "cached", "job_id": job_id, "output_files": existing_job.get("output_files")}
        elif existing_job["status"] == "failed":
            # Previous attempt failed, allow retry by replacing the job
            pass
        else:
            # Job is still in progress
            return {"status": "in_progress", "job_id": job_id}

    jobs[job_id] = {
        "job_id": job_id,
        "status": "pending",
        "progress": 0,
        "error": None,
        "output_files": None,
        "kind": "encode",  # heavy lane
    }
    _persist(job_id)

    background_tasks.add_task(process_job, job_id, request)

    return {"status": "accepted", "job_id": job_id}


_HEAVY_KINDS = frozenset({"encode", "render"})


def _heavy_queue_position(job_id: str) -> Optional[int]:
    """Number of heavy jobs ahead of `job_id` in the serialized heavy lane.

    0 means this job is running or next up; None means it isn't a queued heavy
    job (a preview, a terminal job, or unknown-kind). Relies on `jobs` being
    insertion-ordered (dict order) so "ahead" == submitted earlier and still
    pending/running.
    """
    job = jobs.get(job_id)
    if job is None or job.get("kind") not in _HEAVY_KINDS:
        return None
    if job.get("status") not in ("pending", "running"):
        return None
    ahead = 0
    for other_id, other in jobs.items():
        if other_id == job_id:
            break
        if other.get("kind") in _HEAVY_KINDS and other.get("status") in ("pending", "running"):
            ahead += 1
    return ahead


@app.get("/status/{job_id}")
async def get_job_status(job_id: str, _auth: bool = Depends(verify_api_key)) -> JobStatus:
    # Get the status of an encoding job
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return JobStatus(**jobs[job_id], queue_position=_heavy_queue_position(job_id))


@app.get("/health")
async def health_check():
    # Health check endpoint
    active_jobs = sum(1 for j in jobs.values() if j["status"] == "running")

    # Get karaoke-gen wheel version if installed
    wheel_version = None
    try:
        from importlib.metadata import version as get_version
        wheel_version = get_version("karaoke-gen")
    except Exception:
        pass

    return {
        "status": "ok",
        "active_jobs": active_jobs,
        "queue_length": sum(1 for j in jobs.values() if j["status"] == "pending"),
        "ffmpeg_version": subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout.split("\n")[0],
        "wheel_version": wheel_version,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
