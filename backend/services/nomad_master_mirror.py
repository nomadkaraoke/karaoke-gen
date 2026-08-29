"""
Fast-path mirror of freshly-published Nomad masters into the Divebar GCS bucket.

New Nomad releases are uploaded to Google Drive at finalize time, but the Drive->GCS
byte-sync VM only runs nightly, so kjbox (which mirrors that GCS folder every 5 min)
would otherwise wait up to ~24h for a new master. This pushes the 720p master straight
to the exact GCS object the nightly VM would produce, so kjbox picks it up within minutes.

Idempotent with the nightly VM: identical object name + size means its additive
`gcloud storage rsync` sees the file already present and skips it.

Scoped to public Nomad Karaoke masters only (brand prefix ``NOMAD``, excluding the
``NOMADNP`` private-track prefix). Every operation is best-effort / non-fatal: a failure
here must never break the distribution pipeline — the nightly VM remains the safety net.
"""
import logging
import re
from typing import Optional

from google.cloud import storage
from google.cloud.exceptions import NotFound

from backend.config import settings

logger = logging.getLogger(__name__)

# A single-track filename prefix must be a full public brand code PLUS at least part
# of the artist name ("NOMAD-#### - X..."). Stricter than the by-brand deletes: when a
# recycled brand code is shared by two tracks (orphaned files), this lets cleanup
# target exactly one of them without touching the other.
_TRACK_FILENAME_PREFIX_RE = re.compile(r"^NOMAD-\d{4,} - \S.*")


def is_nomad_public_brand(brand_code: Optional[str]) -> bool:
    """True only for public Nomad releases (``NOMAD-####``), not ``NOMADNP`` privates."""
    if not brand_code:
        return False
    prefix = brand_code.split("-", 1)[0].strip().upper()
    return prefix == "NOMAD"


class NomadMasterMirror:
    """Push/remove Nomad 720p masters in the Divebar GCS bucket that kjbox mirrors."""

    def __init__(self, client: Optional[storage.Client] = None):
        self._client = client or storage.Client(project=settings.google_cloud_project)
        self._bucket = self._client.bucket(settings.divebar_files_bucket)

    def _blob_name(self, filename: str) -> str:
        prefix = settings.nomad_master_gcs_prefix.strip("/")
        return f"{prefix}/{filename}"

    def _vocals_blob_name(self, filename: str) -> str:
        prefix = settings.nomad_vocals_guide_gcs_prefix.strip("/")
        return f"{prefix}/{filename}"

    def push_720p(self, local_path: str, filename: str) -> bool:
        """Upload a 720p master to the divebar GCS mirror. Best-effort; returns success."""
        blob_name = self._blob_name(filename)
        try:
            blob = self._bucket.blob(blob_name)
            blob.upload_from_filename(local_path)
            logger.info(
                "Fast-synced Nomad master to gs://%s/%s",
                settings.divebar_files_bucket,
                blob_name,
            )
            return True
        except Exception as e:  # noqa: BLE001 - never fatal to the pipeline
            logger.warning("Nomad master fast-sync push failed for %s: %s", blob_name, e)
            return False

    def delete_720p(self, filename: str) -> bool:
        """Delete a 720p master from the mirror. Best-effort; 404 == already gone."""
        blob_name = self._blob_name(filename)
        try:
            self._bucket.blob(blob_name).delete()
            logger.info(
                "Removed Nomad master from gs://%s/%s",
                settings.divebar_files_bucket,
                blob_name,
            )
            return True
        except NotFound:
            logger.debug("Nomad master already absent in mirror: %s", blob_name)
            return False
        except Exception as e:  # noqa: BLE001 - never fatal to the pipeline
            logger.warning("Nomad master mirror delete failed for %s: %s", blob_name, e)
            return False

    def push_vocals_guide(self, local_path: str, filename: str) -> bool:
        """Upload a padded original-vocals guide to the divebar GCS mirror's vocals
        prefix (which kjbox pulls into ``NOMAD-vocals-padded/``). Best-effort."""
        blob_name = self._vocals_blob_name(filename)
        try:
            blob = self._bucket.blob(blob_name)
            blob.upload_from_filename(local_path)
            logger.info(
                "Pushed original-vocals guide to gs://%s/%s",
                settings.divebar_files_bucket,
                blob_name,
            )
            return True
        except Exception as e:  # noqa: BLE001 - never fatal to the pipeline
            logger.warning("Vocals-guide push failed for %s: %s", blob_name, e)
            return False

    def delete_vocals_guides_by_brand(self, brand_code: str) -> int:
        """Delete ALL original-vocals guide objects for a Nomad release, matched by the
        unique ``{brand_code} - `` filename prefix (covers renames). Best-effort; returns
        the count deleted. Refuses a non-Nomad / bare brand code (same guardrail as the
        master prefix-delete) so a broad prefix can never wipe unrelated objects."""
        if not is_nomad_public_brand(brand_code):
            return 0
        suffix = brand_code.split("-", 1)[1] if "-" in brand_code else ""
        if not suffix.strip():
            return 0
        prefix = f"{settings.nomad_vocals_guide_gcs_prefix.strip('/')}/{brand_code} - "
        deleted = 0
        try:
            for blob in self._bucket.list_blobs(prefix=prefix):
                try:
                    blob.delete()
                    deleted += 1
                except Exception as e:  # noqa: BLE001
                    logger.warning("Failed to delete vocals-guide object %s: %s", blob.name, e)
            if deleted:
                logger.info(
                    "Removed %d original-vocals guide object(s) from gs://%s/%s*",
                    deleted, settings.divebar_files_bucket, prefix,
                )
        except Exception as e:  # noqa: BLE001 - never fatal to the pipeline
            logger.warning("Vocals-guide mirror prefix-delete failed for %s: %s", prefix, e)
        return deleted

    def delete_masters_by_brand(self, brand_code: str) -> int:
        """Delete ALL 720p master objects for a Nomad release, matched by the unique
        ``{brand_code} - `` filename prefix.

        Keying on the brand_code prefix (rather than a reconstructed title) covers
        renames for free: every artist/title variant a release was ever published
        under shares the same ``NOMAD-#### - `` prefix. Best-effort; returns the count
        deleted. Refuses to run for a non-Nomad / empty brand code so a broad prefix
        can never match unrelated objects.
        """
        if not is_nomad_public_brand(brand_code):
            return 0
        # Require a full ``NOMAD-####`` code (non-empty suffix after the hyphen) so the
        # delete prefix is always specific to one release — never a bare ``NOMAD``.
        suffix = brand_code.split("-", 1)[1] if "-" in brand_code else ""
        if not suffix.strip():
            return 0
        prefix = f"{settings.nomad_master_gcs_prefix.strip('/')}/{brand_code} - "
        deleted = 0
        try:
            for blob in self._bucket.list_blobs(prefix=prefix):
                try:
                    blob.delete()
                    deleted += 1
                except Exception as e:  # noqa: BLE001
                    logger.warning("Failed to delete mirror object %s: %s", blob.name, e)
            if deleted:
                logger.info(
                    "Removed %d Nomad master object(s) from gs://%s/%s*",
                    deleted, settings.divebar_files_bucket, prefix,
                )
        except Exception as e:  # noqa: BLE001 - never fatal to the pipeline
            logger.warning("Nomad master mirror prefix-delete failed for %s: %s", prefix, e)
        return deleted


    def delete_track_objects_by_filename_prefix(self, filename_prefix: str) -> dict:
        """Delete mirror objects (720p masters + original-vocals guides) whose filename
        starts with ``filename_prefix`` — e.g. ``"NOMAD-1583 - Mazzy Star"``.

        Used by orphaned-output cleanup when the owning job doc is gone and a recycled
        brand code is shared with a live track: the prefix must include artist text
        (enforced by ``_TRACK_FILENAME_PREFIX_RE``) so only the orphaned track's
        objects match, never the whole brand code. Best-effort; returns counts.
        """
        if not _TRACK_FILENAME_PREFIX_RE.match(filename_prefix or ""):
            logger.warning(
                "Refusing mirror track delete for unsafe prefix %r "
                "(must look like 'NOMAD-#### - Artist...')", filename_prefix,
            )
            return {"masters_deleted": 0, "vocals_guides_deleted": 0}

        counts = {"masters_deleted": 0, "vocals_guides_deleted": 0}
        targets = [
            ("masters_deleted", settings.nomad_master_gcs_prefix.strip("/")),
            ("vocals_guides_deleted", settings.nomad_vocals_guide_gcs_prefix.strip("/")),
        ]
        for key, gcs_prefix in targets:
            prefix = f"{gcs_prefix}/{filename_prefix}"
            try:
                for blob in self._bucket.list_blobs(prefix=prefix):
                    try:
                        blob.delete()
                        counts[key] += 1
                        logger.info(
                            "Removed orphaned mirror object gs://%s/%s",
                            settings.divebar_files_bucket, blob.name,
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning("Failed to delete mirror object %s: %s", blob.name, e)
            except Exception as e:  # noqa: BLE001 - never fatal to the caller
                logger.warning("Mirror track prefix-delete failed for %s: %s", prefix, e)
        return counts


def cleanup_nomad_masters(brand_code: Optional[str]) -> int:
    """Best-effort removal of a Nomad release's 720p master(s) from the GCS mirror,
    keyed by brand_code prefix (covers renames). No-op for non-Nomad / ``NOMADNP``
    brands or when fast-sync is disabled. Never raises — a cleanup failure must not
    break job delete/edit flows (the object just lingers until overwritten). Call this
    wherever the pipeline deletes a job's Drive/Dropbox outputs.
    """
    try:
        if not settings.nomad_master_fast_sync_enabled:
            return 0
        if not is_nomad_public_brand(brand_code):
            return 0
        mirror = NomadMasterMirror()
        deleted = mirror.delete_masters_by_brand(brand_code)
        # A recycled brand must not keep serving its old original-vocals guide, so remove
        # the GCS guide(s) too. (Note: with the device's additive-only vocals sync this
        # won't auto-remove an already-downloaded device copy — a documented limitation.)
        mirror.delete_vocals_guides_by_brand(brand_code)
        return deleted
    except Exception as e:  # noqa: BLE001 - never fatal to the caller
        logger.warning("Nomad master mirror cleanup skipped: %s", e)
        return 0
