"""
Delete a job's published (distributed) outputs: YouTube video, Dropbox folder,
Google Drive files.

Shared by the track Edit flow (``POST /api/jobs/{id}/edit``) and the admin
re-render (``POST /api/admin/jobs/{id}/rerender``). Each helper returns a small
result dict (``{"status": "success" | "failed" | "partial" | "skipped" |
"error", ...}``) suitable for the job timeline and API responses, and never
raises — the caller decides which failures are fatal.

Brand-code recycling is deliberately NOT done here: Edit recycles the code,
the admin re-render keeps it and re-publishes under the same code.
"""
import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_YOUTUBE_ID_RE = re.compile(r'(?:youtu\.be/|youtube\.com/watch\?v=)([^&\s]+)')

# state_data keys that point at published outputs.
PUBLISHED_OUTPUT_KEYS = ("youtube_url", "dropbox_link", "brand_code", "gdrive_files")


def snapshot_published_outputs(state_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The job's current published-output references (for the timeline/audit)."""
    state_data = state_data or {}
    return {key: state_data[key] for key in PUBLISHED_OUTPUT_KEYS if state_data.get(key)}


def youtube_video_id(youtube_url: Optional[str]) -> Optional[str]:
    match = _YOUTUBE_ID_RE.search(youtube_url or "")
    return match.group(1) if match else None


def delete_youtube_video(job_id: str, youtube_url: Optional[str]) -> Dict[str, Any]:
    """Delete the job's YouTube video (via the server-side YouTube credentials)."""
    if not youtube_url:
        return {"status": "skipped", "reason": "no youtube_url"}
    video_id = None
    try:
        # Inside the try: a malformed (e.g. non-string) URL must not raise.
        video_id = youtube_video_id(youtube_url)
        if not video_id:
            return {"status": "failed", "reason": f"Could not extract video ID from {youtube_url}"}
        from karaoke_gen.karaoke_finalise.karaoke_finalise import KaraokeFinalise
        from backend.services.youtube_service import get_youtube_service

        youtube_service = get_youtube_service()
        if not youtube_service.is_configured:
            return {"status": "skipped", "reason": "YouTube not configured"}
        finalise = KaraokeFinalise(
            dry_run=False,
            non_interactive=True,
            user_youtube_credentials=youtube_service.get_credentials_dict(),
        )
        success = finalise.delete_youtube_video(video_id)
        return {"status": "success" if success else "failed", "video_id": video_id}
    except Exception as e:
        logger.error(f"[job:{job_id}] Error deleting YouTube video {video_id}: {e}", exc_info=True)
        return {"status": "error", "error": str(e), "video_id": video_id}


def legacy_dropbox_folder_path(
    dropbox_path: str, brand_code: str, artist: Optional[str], title: Optional[str]
) -> str:
    """Raw-name folder (no sanitisation) that older Edit/delete code assumed."""
    return f"{dropbox_path}/{brand_code} - {artist or 'Unknown'} - {title or 'Unknown'}"


def dropbox_folder_path(dropbox_path: str, brand_code: str, artist: Optional[str], title: Optional[str]) -> str:
    """The Dropbox folder the distribution step uploads to (same sanitisation)."""
    from karaoke_gen.utils import sanitize_filename

    safe_artist = sanitize_filename(artist) if artist else "Unknown"
    safe_title = sanitize_filename(title) if title else "Unknown"
    return f"{dropbox_path}/{brand_code} - {safe_artist} - {safe_title}"


def delete_dropbox_folder(
    job_id: str,
    dropbox_path: Optional[str],
    brand_code: Optional[str],
    artist: Optional[str],
    title: Optional[str],
) -> Dict[str, Any]:
    """Delete the job's ``{dropbox_path}/{brand_code} - {Artist} - {Title}`` folder.

    The uploader sanitises artist/title; older code paths assumed the raw names.
    When the two differ, both are checked and whichever exists is deleted.
    ``success`` (which lets callers recycle the brand code) only when no folder
    remains; ``deleted`` lists what was removed.
    """
    if not (brand_code and dropbox_path):
        return {"status": "skipped", "reason": "no brand_code or dropbox_path"}
    try:
        from backend.services.dropbox_service import get_dropbox_service

        dropbox = get_dropbox_service()
        if not dropbox.is_configured:
            return {"status": "skipped", "reason": "Dropbox not configured"}
        full_path = dropbox_folder_path(dropbox_path, brand_code, artist, title)
        legacy_path = legacy_dropbox_folder_path(dropbox_path, brand_code, artist, title)
        candidates = [full_path] if legacy_path == full_path else [full_path, legacy_path]

        deleted, failed = [], []
        for path in candidates:
            if len(candidates) > 1:
                try:
                    if not dropbox.file_exists(path):
                        continue
                except Exception as e:  # unknown → attempt the delete anyway
                    logger.warning(f"[job:{job_id}] Dropbox existence check failed for {path}: {e}")
            # delete_folder is True when deleted OR already absent.
            (deleted if dropbox.delete_folder(path) else failed).append(path)
        result = {"status": "failed" if failed else "success", "path": full_path, "deleted": deleted}
        if failed:
            result["failed"] = failed
        return result
    except Exception as e:
        logger.error(f"[job:{job_id}] Error deleting Dropbox folder: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


def delete_gdrive_files(
    job_id: str,
    gdrive_files: Optional[Dict[str, str]],
    brand_code: Optional[str] = None,
    cleanup_mirror: bool = True,
) -> Dict[str, Any]:
    """Delete the job's Google Drive public-share files.

    ``cleanup_mirror`` also removes the Nomad 720p master from the GCS
    fast-sync mirror (kjbox). A re-render that re-publishes under the same brand
    code skips it: the new upload overwrites the same mirror object, so kjbox
    keeps the old master until the new one lands instead of losing the song.
    """
    if not gdrive_files:
        return {"status": "skipped", "reason": "no gdrive_files"}
    try:
        from backend.services.gdrive_service import get_gdrive_service

        gdrive = get_gdrive_service()
        if not gdrive.is_configured:
            return {"status": "skipped", "reason": "Google Drive not configured"}
        file_ids = list(gdrive_files.values()) if isinstance(gdrive_files, dict) else []
        delete_results = gdrive.delete_files(file_ids)
        all_success = all(delete_results.values())
        if cleanup_mirror:
            # Prefix-keyed by brand_code, so it covers renames. Non-fatal,
            # Nomad-brand only, no-op otherwise.
            from backend.services.nomad_master_mirror import cleanup_nomad_masters
            cleanup_nomad_masters(brand_code)
        return {"status": "success" if all_success else "partial", "files": delete_results}
    except Exception as e:
        logger.error(f"[job:{job_id}] Error deleting Google Drive files: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}
