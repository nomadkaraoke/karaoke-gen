"""
Visibility change service for completed jobs.

Handles changing a job's visibility between public and private:
- Public -> Private: redistribute existing finals to private destination (fast, ~1-2 min)
- Private -> Public: reset styles, regenerate screens, re-render, re-encode (slow, ~15-30 min)
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from google.cloud.firestore_v1 import DELETE_FIELD, ArrayUnion

from backend.models.job import JobStatus
from backend.services.job_manager import JobManager
from backend.services.storage_service import StorageService

logger = logging.getLogger(__name__)


class VisibilityChangeService:
    """Service for changing job visibility after completion."""

    def __init__(self, job_manager: Optional[JobManager] = None):
        self.job_manager = job_manager or JobManager()

    def validate_change(self, job, user_email: str, is_admin: bool, target_visibility: str) -> Optional[str]:
        """Validate a visibility change request. Returns error message or None if valid."""
        if not job:
            return "Job not found"

        if job.status != JobStatus.COMPLETE.value and job.status != "complete":
            return f"Can only change visibility on completed jobs. Current status: {job.status}"

        if getattr(job, 'tenant_id', None):
            return "Cannot change visibility on tenant jobs"

        current_is_private = getattr(job, 'is_private', False)
        target_is_private = target_visibility == "private"

        if current_is_private == target_is_private:
            current = "private" if current_is_private else "public"
            return f"Job is already {current}"

        # Check user owns the job or is admin
        if not is_admin and job.user_email != user_email:
            return "You can only change visibility on your own jobs"

        # Check for in-progress visibility change
        state_data = job.state_data or {}
        if state_data.get('visibility_change_in_progress'):
            return "A visibility change is already in progress for this job"

        return None

    async def change_to_private(self, job_id: str, job, user_email: str) -> dict:
        """
        Change a public job to private (fast path).

        Distribute-then-delete: the track is first redistributed to the private
        destination (private Dropbox archive + NOMADNP code, no YouTube/GDrive),
        and only *after* that succeeds are the public outputs (YouTube, public
        Dropbox folder, public GDrive files) deleted and the public NOMAD number
        recycled. GCS finals are always kept.

        This ordering is deliberate: YouTube deletion is irreversible, so nothing
        public may be destroyed until the private archive exists. If redistribution
        fails, the job is reverted to public and left fully intact (no partial
        deletion, no leaked NOMADNP code — see redistribute_video's failure path).
        """
        logger.info(f"[job:{job_id}] Starting visibility change: public -> private (by {user_email})")

        db = self.job_manager.firestore.db
        job_ref = db.collection("jobs").document(job_id)

        # Capture the public output references BEFORE redistribution overwrites
        # state_data with the new private values — we need them to delete the
        # public outputs afterwards.
        state_data = job.state_data or {}
        public_outputs = {
            "youtube_url": state_data.get("youtube_url"),
            "brand_code": state_data.get("brand_code"),
            "gdrive_files": state_data.get("gdrive_files"),
            "dropbox_path": getattr(job, "dropbox_path", None),
            "artist": job.artist,
            "title": job.title,
        }

        # Set guard flag + flip to private so redistribution targets the private
        # destination. Public outputs remain live until the private archive exists.
        # No "changed to private" timeline event yet — it is only recorded once the
        # move actually succeeds (this is reverted if redistribution fails).
        job_ref.update({
            "state_data.visibility_change_in_progress": True,
            "is_private": True,
            "updated_at": datetime.now(timezone.utc),
        })

        # Step 1: Redistribute to the private destination FIRST (public outputs
        # still live). This is the only phase whose failure is safe to fully roll
        # back — nothing public has been touched yet. redistribute_video verifies
        # the archive actually materialized and recycles any NOMADNP code it
        # allocated on failure, so a False return means "no-op, still public".
        try:
            from backend.workers.video_worker import redistribute_video
            success = await redistribute_video(job_id)
        except Exception as e:
            logger.error(f"[job:{job_id}] Redistribution raised during visibility change: {e}", exc_info=True)
            success = False

        if not success:
            # Roll back to public: no public outputs were deleted, so restoring
            # is_private=False leaves the job fully intact and public.
            logger.error(f"[job:{job_id}] Visibility change to private failed: redistribution did not succeed")
            job_ref.update({
                "is_private": False,
                "state_data.visibility_change_in_progress": DELETE_FIELD,
                "updated_at": datetime.now(timezone.utc),
            })
            raise RuntimeError("Redistribution failed - check worker logs")

        # Step 2: Private archive now exists. From here we do NOT revert to public,
        # even if public cleanup hiccups — the private move has committed. Deleting
        # the public outputs + recycling the public NOMAD number is best-effort
        # (each failure is logged inside the helper, never raised), so a stray
        # orphaned public artifact cannot undo the successful private move.
        await self._delete_public_outputs(job_id, public_outputs)

        # Record the completed move and clear the guard flag. redistribute_video
        # already updated state_data with the new private brand_code etc.
        job_ref.update({
            "state_data.visibility_change_in_progress": DELETE_FIELD,
            "updated_at": datetime.now(timezone.utc),
            "timeline": ArrayUnion([{
                "status": "complete",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message": f"Visibility changed to private by {user_email}",
            }]),
        })

        logger.info(f"[job:{job_id}] Visibility change to private complete")
        return {
            "status": "success",
            "message": "Job changed to private. Outputs redistributed to private destination.",
            "reprocessing_required": False,
        }

    async def change_to_public(self, job_id: str, job, user_email: str) -> dict:
        """
        Change a private job to public (slow path).

        Resets custom styles, deletes all outputs/screens/rendered video,
        and triggers full re-processing from screens worker onward.
        """
        logger.info(f"[job:{job_id}] Starting visibility change: private -> public (by {user_email})")

        db = self.job_manager.firestore.db
        job_ref = db.collection("jobs").document(job_id)
        storage = StorageService()

        # Step 1: Delete distributed outputs + GCS finals
        await self._delete_distributed_outputs(job_id, job, keep_gcs_finals=False)

        # Step 2: Delete screens and rendered video from GCS
        for path in [
            f"jobs/{job_id}/screens/title.mov",
            f"jobs/{job_id}/screens/title.jpg",
            f"jobs/{job_id}/screens/title.png",
            f"jobs/{job_id}/screens/end.mov",
            f"jobs/{job_id}/screens/end.jpg",
            f"jobs/{job_id}/screens/end.png",
            f"jobs/{job_id}/videos/with_vocals.mkv",
            f"jobs/{job_id}/videos/with_vocals.mov",
        ]:
            try:
                # Best-effort delete over a template of possible artifacts; many of
                # these intermediates legitimately never existed for a given render
                # path. ignore_missing keeps a 404 a quiet no-op instead of a noisy
                # ERROR log (which would otherwise trip the error-monitor).
                storage.delete_file(path, ignore_missing=True)
            except Exception:
                pass

        # Delete custom style files from GCS
        try:
            style_prefix = f"jobs/{job_id}/style/"
            storage.delete_folder(style_prefix)
        except Exception:
            pass

        # Step 3: Prepare nomad theme style files for the job
        from backend.api.routes.file_upload import _prepare_theme_for_job
        style_params_path, style_assets_dict, _youtube_desc = _prepare_theme_for_job(
            job_id=job_id, theme_id="nomad"
        )

        # Step 4: Reset styles and update job fields
        # Step 5: Set status to LYRICS_COMPLETE with regen_restore_status
        # Step 6: Clear progress keys and set guard flag
        update_payload = {
            "is_private": False,
            "theme_id": "nomad",
            "color_overrides": {},
            "style_assets": style_assets_dict,
            "style_params_gcs_path": style_params_path,
            "status": JobStatus.LYRICS_COMPLETE.value,
            "state_data.visibility_change_in_progress": True,
            "state_data.regen_restore_status": "review_complete",
            "state_data.audio_complete": True,
            "state_data.lyrics_complete": True,
            "state_data.screens_progress": DELETE_FIELD,
            "state_data.render_progress": DELETE_FIELD,
            "state_data.video_progress": DELETE_FIELD,
            "state_data.encoding_progress": DELETE_FIELD,
            "outputs_deleted_at": None,
            "outputs_deleted_by": None,
            "updated_at": datetime.now(timezone.utc),
            "timeline": ArrayUnion([{
                "status": "lyrics_complete",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message": f"Visibility change to public initiated by {user_email}. "
                           "Re-processing from screens generation.",
            }]),
        }
        job_ref.update(update_payload)

        # Step 6: Trigger screens worker
        from backend.services.worker_service import get_worker_service
        worker_service = get_worker_service()
        triggered = await worker_service.trigger_screens_worker(job_id)

        if not triggered:
            logger.error(f"[job:{job_id}] Failed to trigger screens worker for visibility change")
            # Restore to complete status on failure
            job_ref.update({
                "status": "complete",
                "is_private": True,
                "state_data.visibility_change_in_progress": DELETE_FIELD,
                "state_data.regen_restore_status": DELETE_FIELD,
            })
            raise RuntimeError("Failed to trigger screens worker")

        logger.info(f"[job:{job_id}] Visibility change to public initiated, screens worker triggered")
        return {
            "status": "processing",
            "message": "Visibility change to public started. The video will be re-rendered "
                       "with default branding and published publicly. This takes ~15-30 minutes.",
            "reprocessing_required": True,
        }

    async def _delete_public_outputs(self, job_id: str, public_outputs: dict):
        """Delete the captured PUBLIC outputs and recycle the public NOMAD number.

        Used by change_to_private AFTER a successful private redistribution. Operates
        on references captured before redistribution (which overwrites state_data with
        private values). Does not touch GCS finals and does not clear state_data keys
        (redistribution already set the new private values). Best-effort: individual
        failures are logged, not raised, so one orphaned artifact can't undo the move.
        """
        import re

        # Track whether each public destination was fully cleaned. The NOMAD number
        # is only recycled when every deletion succeeded — otherwise a future public
        # track could reuse the number while the old public files still linger,
        # producing a duplicate. (Mirrors the admin delete-outputs recycle gate.)
        youtube_ok = True
        dropbox_ok = True
        gdrive_ok = True

        youtube_url = public_outputs.get("youtube_url")
        if youtube_url:
            try:
                video_id_match = re.search(r'(?:youtu\.be/|youtube\.com/watch\?v=)([^&\s]+)', youtube_url)
                if video_id_match:
                    video_id = video_id_match.group(1)
                    from karaoke_gen.karaoke_finalise.karaoke_finalise import KaraokeFinalise
                    from backend.services.youtube_service import get_youtube_service
                    youtube_service = get_youtube_service()
                    if youtube_service.is_configured:
                        finalise = KaraokeFinalise(
                            dry_run=False, non_interactive=True,
                            user_youtube_credentials=youtube_service.get_credentials_dict()
                        )
                        finalise.delete_youtube_video(video_id)
                        logger.info(f"[job:{job_id}] Deleted public YouTube video {video_id}")
            except Exception as e:
                youtube_ok = False
                logger.warning(f"[job:{job_id}] Failed to delete public YouTube video: {e}")

        brand_code = public_outputs.get("brand_code")
        dropbox_path = public_outputs.get("dropbox_path")
        if brand_code and dropbox_path:
            try:
                from backend.services.dropbox_service import get_dropbox_service
                dropbox = get_dropbox_service()
                if dropbox.is_configured:
                    from karaoke_gen.utils import sanitize_filename
                    safe_artist = sanitize_filename(public_outputs.get("artist")) if public_outputs.get("artist") else "Unknown"
                    safe_title = sanitize_filename(public_outputs.get("title")) if public_outputs.get("title") else "Unknown"
                    folder_name = f"{brand_code} - {safe_artist} - {safe_title}"
                    full_path = f"{dropbox_path}/{folder_name}"
                    dropbox.delete_folder(full_path)
                    logger.info(f"[job:{job_id}] Deleted public Dropbox folder {full_path}")
            except Exception as e:
                dropbox_ok = False
                logger.warning(f"[job:{job_id}] Failed to delete public Dropbox folder: {e}")

        gdrive_files = public_outputs.get("gdrive_files")
        if gdrive_files:
            try:
                from backend.services.gdrive_service import get_gdrive_service
                gdrive = get_gdrive_service()
                if gdrive.is_configured:
                    file_ids = list(gdrive_files.values()) if isinstance(gdrive_files, dict) else []
                    gdrive.delete_files(file_ids)
                    logger.info(f"[job:{job_id}] Deleted public Google Drive files")
            except Exception as e:
                gdrive_ok = False
                logger.warning(f"[job:{job_id}] Failed to delete public Google Drive files: {e}")

        # A track going private should also leave the public Nomad GCS fast-sync
        # mirror (and thus kjbox). Prefix-keyed by the public brand_code, non-fatal.
        if brand_code:
            try:
                from backend.services.nomad_master_mirror import cleanup_nomad_masters
                cleanup_nomad_masters(brand_code)
            except Exception as e:
                logger.warning(f"[job:{job_id}] Failed to clean up Nomad mirror for {brand_code}: {e}")

        # Recycle the public NOMAD number so the next public track fills the gap —
        # but only if every public deletion succeeded. If any lingered, keep the
        # number reserved (a permanent gap the validator will flag) rather than
        # risk a future duplicate; the leftover artifacts can then be cleaned up.
        if brand_code and youtube_ok and dropbox_ok and gdrive_ok:
            try:
                from backend.services.brand_code_service import BrandCodeService, get_brand_code_service
                prefix, number = BrandCodeService.parse_brand_code(brand_code)
                get_brand_code_service().recycle_brand_code(prefix, number)
                logger.info(f"[job:{job_id}] Recycled public brand code {brand_code}")
            except Exception as e:
                logger.warning(f"[job:{job_id}] Failed to recycle public brand code {brand_code}: {e}")
        elif brand_code:
            logger.warning(
                f"[job:{job_id}] NOT recycling public brand code {brand_code}: "
                f"public cleanup incomplete (youtube={youtube_ok}, dropbox={dropbox_ok}, gdrive={gdrive_ok})"
            )

    async def _delete_distributed_outputs(self, job_id: str, job, keep_gcs_finals: bool = False):
        """Delete distributed outputs (YouTube, Dropbox, GDrive) and recycle brand code."""
        import re

        state_data = job.state_data or {}

        # Delete YouTube video
        youtube_url = state_data.get('youtube_url')
        if youtube_url:
            try:
                video_id_match = re.search(r'(?:youtu\.be/|youtube\.com/watch\?v=)([^&\s]+)', youtube_url)
                if video_id_match:
                    video_id = video_id_match.group(1)
                    from karaoke_gen.karaoke_finalise.karaoke_finalise import KaraokeFinalise
                    from backend.services.youtube_service import get_youtube_service
                    youtube_service = get_youtube_service()
                    if youtube_service.is_configured:
                        finalise = KaraokeFinalise(
                            dry_run=False, non_interactive=True,
                            user_youtube_credentials=youtube_service.get_credentials_dict()
                        )
                        finalise.delete_youtube_video(video_id)
                        logger.info(f"[job:{job_id}] Deleted YouTube video {video_id}")
            except Exception as e:
                logger.warning(f"[job:{job_id}] Failed to delete YouTube video: {e}")

        # Delete Dropbox folder
        brand_code = state_data.get('brand_code')
        dropbox_path = getattr(job, 'dropbox_path', None)
        if brand_code and dropbox_path:
            try:
                from backend.services.dropbox_service import get_dropbox_service
                dropbox = get_dropbox_service()
                if dropbox.is_configured:
                    from karaoke_gen.utils import sanitize_filename
                    safe_artist = sanitize_filename(job.artist) if job.artist else "Unknown"
                    safe_title = sanitize_filename(job.title) if job.title else "Unknown"
                    folder_name = f"{brand_code} - {safe_artist} - {safe_title}"
                    full_path = f"{dropbox_path}/{folder_name}"
                    dropbox.delete_folder(full_path)
                    logger.info(f"[job:{job_id}] Deleted Dropbox folder {full_path}")
            except Exception as e:
                logger.warning(f"[job:{job_id}] Failed to delete Dropbox folder: {e}")

        # Delete Google Drive files
        gdrive_files = state_data.get('gdrive_files')
        if gdrive_files:
            try:
                from backend.services.gdrive_service import get_gdrive_service
                gdrive = get_gdrive_service()
                if gdrive.is_configured:
                    file_ids = list(gdrive_files.values()) if isinstance(gdrive_files, dict) else []
                    gdrive.delete_files(file_ids)
                    logger.info(f"[job:{job_id}] Deleted Google Drive files")
            except Exception as e:
                logger.warning(f"[job:{job_id}] Failed to delete Google Drive files: {e}")

        # A track going private should also leave the public Nomad GCS fast-sync mirror
        # (and thus kjbox). Prefix-keyed by brand_code, non-fatal, Nomad-only.
        if brand_code:
            from backend.services.nomad_master_mirror import cleanup_nomad_masters
            cleanup_nomad_masters(brand_code)

        # Delete GCS finals if requested
        if not keep_gcs_finals:
            try:
                storage = StorageService()
                deleted_count = storage.delete_folder(f"jobs/{job_id}/finals/")
                if deleted_count > 0:
                    logger.info(f"[job:{job_id}] Deleted {deleted_count} GCS finals files")
            except Exception as e:
                logger.warning(f"[job:{job_id}] Failed to delete GCS finals: {e}")

        # Recycle brand code
        if brand_code:
            try:
                from backend.services.brand_code_service import BrandCodeService, get_brand_code_service
                prefix, number = BrandCodeService.parse_brand_code(brand_code)
                get_brand_code_service().recycle_brand_code(prefix, number)
                logger.info(f"[job:{job_id}] Recycled brand code {brand_code}")
            except Exception as e:
                logger.warning(f"[job:{job_id}] Failed to recycle brand code: {e}")

        # Clear distribution state_data
        db = self.job_manager.firestore.db
        job_ref = db.collection("jobs").document(job_id)
        update = {}
        for key in ['youtube_url', 'brand_code', 'dropbox_link', 'gdrive_files']:
            if key in state_data:
                update[f"state_data.{key}"] = DELETE_FIELD
        if update:
            job_ref.update(update)
