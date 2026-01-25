import asyncio
import glob as glob_module
import json
import logging
import os
import subprocess
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, BackgroundTasks, HTTPException, Header, Depends
from google.cloud import storage
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Encoding Worker", version="1.0.0")

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

# Job tracking
jobs: dict[str, dict] = {}
executor = ThreadPoolExecutor(max_workers=4)  # 4 parallel encoding jobs

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


class JobStatus(BaseModel):
    job_id: str
    status: str  # pending, running, complete, failed
    progress: int  # 0-100
    error: Optional[str] = None
    output_files: Optional[list[str]] = None


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


def run_preview_encoding(job_id: str, work_dir: Path, request: "EncodePreviewRequest"):
    # Run FFmpeg encoding for preview video (480x270, fast settings)
    jobs[job_id]["status"] = "running"
    jobs[job_id]["progress"] = 10

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
        raise


def ensure_latest_wheel():
    '''Download and install latest karaoke-gen wheel from GCS.

    Called at the start of each job to enable hot code updates without restart.
    In-progress jobs continue with their version, new jobs get latest code.
    '''
    try:
        logger.info("Checking for latest karaoke-gen wheel in GCS...")

        # Download latest wheel
        result = subprocess.run(
            ["gsutil", "cp", "gs://karaoke-gen-storage-nomadkaraoke/wheels/karaoke_gen-*.whl", "/tmp/"],
            capture_output=True, text=True, timeout=60
        )

        # Find the downloaded wheel (get the latest by version sorting)
        wheels = glob_module.glob("/tmp/karaoke_gen-*.whl")
        if not wheels:
            logger.warning("No wheel found in GCS, using fallback encoding logic")
            return False

        # Sort to get latest version
        wheel_path = sorted(wheels)[-1]
        logger.info(f"Installing wheel: {wheel_path}")

        # Install (or upgrade) the wheel
        # Use 5-minute timeout - first install at job start may need to resolve dependencies
        # Subsequent installs are faster since dependencies are cached
        install_result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "--quiet", wheel_path],
            capture_output=True, text=True, timeout=300
        )

        if install_result.returncode != 0:
            logger.warning(f"Wheel installation failed: {install_result.stderr}")
            return False

        logger.info(f"Successfully installed wheel: {wheel_path}")
        return True

    except subprocess.TimeoutExpired:
        logger.warning("Wheel download/install timed out, using fallback")
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

    try:
        # Import LocalEncodingService from installed wheel (required, no fallback)
        from backend.services.local_encoding_service import LocalEncodingService, EncodingConfig
        logger.info("Using LocalEncodingService from installed wheel")

        # Get artist/title from config for proper naming
        artist = config.get("artist", "Unknown Artist")
        title = config.get("title", "Unknown Title")
        base_name = f"{artist} - {title}"
        logger.info(f"Encoding for: {base_name}")

        # Find input files in work_dir
        # Title/end screens are in screens/ subdirectory
        title_video = find_file(work_dir, "screens/title.mov", "*Title*.mov", "*title*.mov")
        end_video = find_file(work_dir, "screens/end.mov", "*End*.mov", "*end*.mov")

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
        logger.info(f"Instrumental selection from config: {instrumental_selection}")

        if instrumental_selection == "with_backing":
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
        raise RuntimeError(error_msg) from e

    except Exception as e:
        logger.error(f"Encoding failed: {e}")
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)
        raise


async def process_job(job_id: str, request: EncodeRequest):
    # Process an encoding job asynchronously
    try:
        # Download and install latest wheel at job start (allows hot updates without restart)
        # This means in-progress jobs continue with their version, new jobs get latest code
        ensure_latest_wheel()

        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir) / "work"
            work_dir.mkdir()

            # Download input files
            jobs[job_id]["progress"] = 5
            logger.info(f"Downloading from {request.input_gcs_path}")
            download_from_gcs(request.input_gcs_path, work_dir)

            # Run encoding in thread pool (CPU-bound)
            loop = asyncio.get_event_loop()
            output_dir = await loop.run_in_executor(
                executor,
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
            logger.info(f"Job {job_id} complete")

    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}")
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)


async def process_preview_job(job_id: str, request: EncodePreviewRequest):
    # Process a preview encoding job asynchronously
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir) / "work"
            work_dir.mkdir()

            # Run preview encoding in thread pool (CPU-bound)
            # Note: run_preview_encoding handles download/upload internally
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                executor,
                run_preview_encoding,
                job_id,
                work_dir,
                request
            )

            jobs[job_id]["status"] = "complete"
            jobs[job_id]["progress"] = 100
            logger.info(f"Preview job {job_id} complete")

    except Exception as e:
        logger.error(f"Preview job {job_id} failed: {e}")
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)


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
    }

    background_tasks.add_task(process_preview_job, job_id, request)

    return {"status": "accepted", "job_id": job_id}


@app.post("/encode")
async def submit_encode_job(request: EncodeRequest, background_tasks: BackgroundTasks, _auth: bool = Depends(verify_api_key)):
    # Submit an encoding job
    job_id = request.job_id

    if job_id in jobs:
        raise HTTPException(status_code=409, detail=f"Job {job_id} already exists")

    jobs[job_id] = {
        "job_id": job_id,
        "status": "pending",
        "progress": 0,
        "error": None,
        "output_files": None,
    }

    background_tasks.add_task(process_job, job_id, request)

    return {"status": "accepted", "job_id": job_id}


@app.get("/status/{job_id}")
async def get_job_status(job_id: str, _auth: bool = Depends(verify_api_key)) -> JobStatus:
    # Get the status of an encoding job
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return JobStatus(**jobs[job_id])


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
