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
from typing import Optional

from google.cloud import storage
from google.cloud.exceptions import NotFound

from backend.config import settings

logger = logging.getLogger(__name__)


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
