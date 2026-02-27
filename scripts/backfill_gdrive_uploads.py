#!/usr/bin/env python3
"""
Backfill Google Drive uploads for jobs that completed with empty gdrive_files.

This addresses the issue where GDrive uploads silently failed due to stale
HTTP connections (BrokenPipeError / SSLError) on idle Cloud Run containers.

Usage:
    # Dry run - show what would be uploaded
    python scripts/backfill_gdrive_uploads.py --dry-run --job-ids ca408e46,99baa198

    # Upload specific jobs
    python scripts/backfill_gdrive_uploads.py --job-ids ca408e46,99baa198

    # Find and upload all jobs with empty gdrive_files
    python scripts/backfill_gdrive_uploads.py --all-missing

    # Override gdrive folder ID (instead of using DEFAULT_GDRIVE_FOLDER_ID env var)
    python scripts/backfill_gdrive_uploads.py --job-ids ca408e46 --gdrive-folder-id 1abc...

Requires:
    - google-cloud-firestore
    - google-cloud-storage
    - google-api-python-client, google-auth
    - GOOGLE_APPLICATION_CREDENTIALS or gcloud auth application-default login
"""
import argparse
import logging
import os
import sys
import tempfile

# Add project root to path so we can import backend modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google.cloud import firestore, storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

GCS_BUCKET = "karaoke-gen-storage-nomadkaraoke"
PROJECT = "nomadkaraoke"

# Default GDrive public share folder (from infrastructure/config.py)
DEFAULT_GDRIVE_FOLDER_ID = "1laRKAyxo0v817SstfM5XkpbWiNKNAMSX"

# Known affected jobs from the incident
KNOWN_AFFECTED_JOBS = [
    "ca408e46", "99baa198", "1e15d1dd", "ebba0996",
    "cb744c24", "1389f765", "fde3ae7d", "21f4b34f",
    "443c34a1", "56192d3a", "fdb4dc63", "4eeaa729",
]


def get_job(db: firestore.Client, job_id: str) -> dict | None:
    """Fetch job document from Firestore."""
    doc = db.collection("jobs").document(job_id).get()
    if doc.exists:
        return doc.to_dict()
    return None


def find_jobs_with_missing_gdrive(db: firestore.Client) -> list[dict]:
    """Find completed jobs that have empty gdrive_files in state_data.

    Looks for jobs that have a brand_code (indicating they went through
    distribution) but have empty or missing gdrive_files.
    """
    jobs = []
    query = db.collection("jobs").where("status", "==", "complete")

    for doc in query.stream():
        data = doc.to_dict()
        state_data = data.get("state_data", {})
        gdrive_files = state_data.get("gdrive_files", {})
        brand_code = state_data.get("brand_code")

        # Job should have had GDrive uploads if it has a brand_code
        # but gdrive_files is empty/missing
        if brand_code and not gdrive_files:
            jobs.append({"job_id": doc.id, **data})

    return jobs


def _classify_blob(filename: str) -> str | None:
    """Classify a GCS blob filename to an output_files key, or None to skip.

    Only returns keys for the 3 file types that get uploaded to GDrive:
    lossy 4K MP4, lossy 720p MP4, and CDG ZIP.
    """
    lower = filename.lower()
    if lower.endswith(".zip") and "cdg" in lower:
        return "final_karaoke_cdg_zip"
    if "720p" in lower and lower.endswith(".mp4"):
        return "final_karaoke_lossy_720p_mp4"
    if "lossy" in lower and lower.endswith(".mp4") and "720p" not in lower:
        return "final_karaoke_lossy_mp4"
    return None


def download_finals(gcs_client: storage.Client, job_id: str, temp_dir: str) -> dict:
    """Download only the final output files needed for GDrive upload.

    Only downloads lossy 4K MP4, lossy 720p MP4, and CDG ZIP — skips
    lossless files, MKVs, karaoke video, etc. to save bandwidth.

    Returns dict mapping output_files keys to local paths.
    """
    bucket = gcs_client.bucket(GCS_BUCKET)
    prefix = f"jobs/{job_id}/finals/"
    blobs = list(bucket.list_blobs(prefix=prefix))

    output_files = {}
    for blob in blobs:
        filename = os.path.basename(blob.name)
        if not filename:
            continue

        key = _classify_blob(filename)
        if key is None:
            continue
        if key in output_files:
            continue  # already have this type

        local_path = os.path.join(temp_dir, filename)
        logger.info(f"  Downloading {filename} ({blob.size / 1024 / 1024:.1f} MB)")
        blob.download_to_filename(local_path)
        output_files[key] = local_path

    return output_files


def list_finals(gcs_client: storage.Client, job_id: str) -> list[str]:
    """List uploadable final output files in GCS without downloading."""
    bucket = gcs_client.bucket(GCS_BUCKET)
    prefix = f"jobs/{job_id}/finals/"
    blobs = list(bucket.list_blobs(prefix=prefix))
    return [
        f"{os.path.basename(b.name)} ({b.size / 1024 / 1024:.1f} MB)"
        for b in blobs
        if os.path.basename(b.name) and _classify_blob(os.path.basename(b.name))
    ]


def backfill_job(
    db: firestore.Client,
    gcs_client: storage.Client,
    job_id: str,
    gdrive_folder_id: str,
    dry_run: bool = False,
) -> bool:
    """Backfill GDrive upload for a single job.

    Returns True if successful (or dry run).
    """
    logger.info(f"Processing job {job_id}")

    # Fetch job data
    job_data = get_job(db, job_id)
    if not job_data:
        logger.error(f"  Job {job_id} not found in Firestore")
        return False

    state_data = job_data.get("state_data", {})
    artist = job_data.get("artist", "Unknown")
    title = job_data.get("title", "Unknown")
    brand_code = state_data.get("brand_code")
    status = job_data.get("status")

    logger.info(f"  {artist} - {title} (brand: {brand_code}, status: {status})")

    # Check existing gdrive_files
    existing = state_data.get("gdrive_files", {})
    if existing:
        logger.info(f"  Already has gdrive_files: {existing}")
        logger.info(f"  Skipping (already uploaded)")
        return True

    if not brand_code:
        logger.warning(f"  No brand code, skipping")
        return False

    if dry_run:
        # List what files are available in GCS
        files = list_finals(gcs_client, job_id)
        if files:
            logger.info(f"  [DRY RUN] Would upload {len(files)} files to GDrive folder {gdrive_folder_id}:")
            for f in files:
                logger.info(f"    - {f}")
        else:
            logger.warning(f"  [DRY RUN] No final files found in GCS")
        return True

    # Download files and upload
    with tempfile.TemporaryDirectory(prefix=f"backfill-{job_id}-") as temp_dir:
        # Download finals from GCS
        logger.info(f"  Downloading finals from GCS...")
        output_files = download_finals(gcs_client, job_id, temp_dir)

        if not output_files:
            logger.warning(f"  No final files found in GCS")
            return False

        logger.info(f"  Found {len(output_files)} files to upload")

        # Upload to GDrive
        from backend.services.gdrive_service import GoogleDriveService

        gdrive = GoogleDriveService()
        if not gdrive.is_configured:
            logger.error(f"  Google Drive not configured")
            return False

        base_name = f"{artist} - {title}"
        uploaded = gdrive.upload_to_public_share(
            root_folder_id=gdrive_folder_id,
            brand_code=brand_code,
            base_name=base_name,
            output_files=output_files,
        )

        logger.info(f"  Uploaded {len(uploaded)} files: {uploaded}")

        # Update Firestore
        db.collection("jobs").document(job_id).update({
            "state_data.gdrive_files": uploaded,
        })
        logger.info(f"  Updated Firestore state_data.gdrive_files")

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Backfill Google Drive uploads for jobs with empty gdrive_files"
    )
    parser.add_argument(
        "--job-ids",
        help="Comma-separated list of job IDs to process",
    )
    parser.add_argument(
        "--all-missing",
        action="store_true",
        help="Find and process all jobs with missing gdrive_files",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    parser.add_argument(
        "--gdrive-folder-id",
        default=os.getenv("DEFAULT_GDRIVE_FOLDER_ID", DEFAULT_GDRIVE_FOLDER_ID),
        help=f"Google Drive folder ID for public share (default: {DEFAULT_GDRIVE_FOLDER_ID})",
    )
    args = parser.parse_args()

    if not args.job_ids and not args.all_missing:
        parser.error("Specify --job-ids or --all-missing")

    db = firestore.Client(project=PROJECT)
    gcs_client = storage.Client(project=PROJECT)

    if args.job_ids:
        job_ids = [j.strip() for j in args.job_ids.split(",")]
    elif args.all_missing:
        logger.info("Searching for jobs with missing GDrive uploads...")
        jobs = find_jobs_with_missing_gdrive(db)
        job_ids = [j["job_id"] for j in jobs]
        logger.info(f"Found {len(job_ids)} jobs with missing uploads")

    if args.dry_run:
        logger.info("[DRY RUN MODE]")

    logger.info(f"GDrive folder: {args.gdrive_folder_id}")

    success_count = 0
    fail_count = 0

    for job_id in job_ids:
        try:
            ok = backfill_job(
                db, gcs_client, job_id,
                gdrive_folder_id=args.gdrive_folder_id,
                dry_run=args.dry_run,
            )
            if ok:
                success_count += 1
            else:
                fail_count += 1
        except Exception as e:
            logger.error(f"  Failed: {e}", exc_info=True)
            fail_count += 1

    logger.info(f"\nDone: {success_count} succeeded, {fail_count} failed out of {len(job_ids)} jobs")


if __name__ == "__main__":
    main()
