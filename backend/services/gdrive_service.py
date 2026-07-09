"""Native Google Drive API service for cloud backend.

This service provides Google Drive operations using the native API,
for uploading files to public share folders. It handles:
- Folder creation and lookup
- File uploads with resumable upload support
- Uploading to organized folder structure (MP4/, MP4-720p/, CDG/)

Credentials are loaded from Google Cloud Secret Manager and can be
shared with YouTube credentials if scopes include drive.file.
"""
import json
import logging
import os
import ssl
from typing import Any, Dict, Optional

from tenacity import Retrying, stop_after_attempt, wait_exponential, retry_if_exception_type

from backend.config import get_settings
from karaoke_gen.utils import sanitize_filename

logger = logging.getLogger(__name__)

# Transient errors that indicate a stale HTTP connection (Cloud Run idle containers)
TRANSIENT_ERRORS = (BrokenPipeError, ConnectionResetError, ConnectionError, ssl.SSLError)


class GoogleDriveService:
    """Google Drive operations using native API."""

    # Secret Manager secret name for Google Drive credentials
    # Can be same as YouTube if scopes include drive.file
    GDRIVE_CREDENTIALS_SECRET = "gdrive-oauth-credentials"

    def __init__(self):
        self.settings = get_settings()
        self._service = None
        self._credentials_data: Optional[Dict[str, Any]] = None
        self._loaded = False

    def _reset_service(self):
        """Reset the cached Drive service to force a fresh connection on next use."""
        logger.info("Resetting Google Drive service connection")
        self._service = None

    def _load_credentials(self) -> Optional[Dict[str, Any]]:
        """Load OAuth credentials from Secret Manager."""
        if self._loaded:
            return self._credentials_data

        try:
            creds_json = self.settings.get_secret(self.GDRIVE_CREDENTIALS_SECRET)

            if not creds_json:
                # Try falling back to YouTube credentials (may have drive scope)
                logger.info(
                    "Google Drive credentials not found, trying YouTube credentials"
                )
                creds_json = self.settings.get_secret("youtube-oauth-credentials")

            if not creds_json:
                logger.warning("Google Drive credentials not found in Secret Manager")
                self._loaded = True
                return None

            self._credentials_data = json.loads(creds_json)

            # Validate required fields
            required_fields = ["refresh_token", "client_id", "client_secret"]
            missing = [f for f in required_fields if not self._credentials_data.get(f)]

            if missing:
                logger.error(f"Google Drive credentials missing required fields: {missing}")
                self._credentials_data = None
                self._loaded = True
                return None

            logger.info("Google Drive credentials loaded successfully from Secret Manager")
            self._loaded = True
            return self._credentials_data

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Google Drive credentials JSON: {e}")
            self._loaded = True
            return None
        except Exception as e:
            logger.error(f"Failed to load Google Drive credentials: {e}")
            self._loaded = True
            return None

    @property
    def is_configured(self) -> bool:
        """Check if Google Drive credentials are available."""
        creds = self._load_credentials()
        return creds is not None

    @property
    def service(self):
        """Get or create Google Drive service."""
        if self._service is None:
            # Import here to avoid import errors if google packages not installed
            try:
                from google.oauth2.credentials import Credentials
                from googleapiclient.discovery import build
            except ImportError:
                raise ImportError(
                    "google-api-python-client and google-auth packages are required. "
                    "Install them with: pip install google-api-python-client google-auth"
                )

            creds_data = self._load_credentials()
            if not creds_data:
                raise RuntimeError(
                    "Google Drive credentials not configured in Secret Manager"
                )

            # Create credentials object
            credentials = Credentials(
                token=creds_data.get("token"),
                refresh_token=creds_data.get("refresh_token"),
                token_uri=creds_data.get(
                    "token_uri", "https://oauth2.googleapis.com/token"
                ),
                client_id=creds_data.get("client_id"),
                client_secret=creds_data.get("client_secret"),
                scopes=creds_data.get(
                    "scopes", ["https://www.googleapis.com/auth/drive.file"]
                ),
            )

            self._service = build("drive", "v3", credentials=credentials)
            logger.info("Google Drive service initialized successfully")

        return self._service

    def get_or_create_folder(self, parent_id: str, folder_name: str) -> str:
        """
        Get existing folder or create new one, return folder ID.

        Retries on transient connection errors (BrokenPipeError, SSLError)
        that occur when Cloud Run containers sit idle between jobs.

        Args:
            parent_id: Parent folder ID
            folder_name: Name of folder to find or create

        Returns:
            Folder ID
        """
        for attempt in Retrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=8),
            retry=retry_if_exception_type(TRANSIENT_ERRORS),
            before_sleep=lambda retry_state: self._reset_service(),
            reraise=True,
        ):
            with attempt:
                logger.info(f"Looking for folder '{folder_name}' in parent {parent_id}")

                # Search for existing folder
                # Escape single quotes in folder name for Google Drive API query syntax
                escaped_folder_name = folder_name.replace("'", "\\'")
                query = (
                    f"name='{escaped_folder_name}' and '{parent_id}' in parents "
                    f"and mimeType='application/vnd.google-apps.folder' and trashed=false"
                )
                results = self.service.files().list(q=query, fields="files(id, name)").execute()

                if results.get("files"):
                    folder_id = results["files"][0]["id"]
                    logger.info(f"Found existing folder '{folder_name}': {folder_id}")
                    return folder_id

                # Create folder
                logger.info(f"Creating new folder '{folder_name}'")
                metadata = {
                    "name": folder_name,
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [parent_id],
                }
                folder = self.service.files().create(body=metadata, fields="id").execute()
                folder_id = folder["id"]
                logger.info(f"Created folder '{folder_name}': {folder_id}")
                return folder_id

    def upload_file(
        self,
        local_path: str,
        parent_id: str,
        filename: str,
        replace_existing: bool = True,
    ) -> str:
        """
        Upload a file to a specific Drive folder.

        Retries on transient connection errors (BrokenPipeError, SSLError)
        that occur when Cloud Run containers sit idle between jobs.

        Args:
            local_path: Local file path
            parent_id: Parent folder ID in Google Drive
            filename: Name for the file in Drive
            replace_existing: If True, delete existing file with same name first

        Returns:
            File ID of uploaded file
        """
        from googleapiclient.http import MediaFileUpload

        for attempt in Retrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=8),
            retry=retry_if_exception_type(TRANSIENT_ERRORS),
            before_sleep=lambda retry_state: self._reset_service(),
            reraise=True,
        ):
            with attempt:
                file_size = os.path.getsize(local_path)
                logger.info(
                    f"Uploading {local_path} ({file_size / 1024 / 1024:.1f} MB) "
                    f"as '{filename}' to folder {parent_id}"
                )

                # Determine MIME type
                ext = os.path.splitext(local_path)[1].lower()
                mime_types = {
                    ".mp4": "video/mp4",
                    ".mkv": "video/x-matroska",
                    ".zip": "application/zip",
                    ".flac": "audio/flac",
                    ".mp3": "audio/mpeg",
                    ".wav": "audio/wav",
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                }
                mime_type = mime_types.get(ext, "application/octet-stream")

                # Check for existing file with same name
                if replace_existing:
                    # Escape single quotes in filename for Google Drive API query syntax
                    escaped_filename = filename.replace("'", "\\'")
                    query = (
                        f"name='{escaped_filename}' and '{parent_id}' in parents and trashed=false"
                    )
                    results = self.service.files().list(q=query, fields="files(id)").execute()
                    for existing_file in results.get("files", []):
                        logger.info(f"Deleting existing file: {existing_file['id']}")
                        self.service.files().delete(fileId=existing_file["id"]).execute()

                # Upload file
                metadata = {"name": filename, "parents": [parent_id]}
                media = MediaFileUpload(local_path, mimetype=mime_type, resumable=True)

                file_result = (
                    self.service.files().create(body=metadata, media_body=media, fields="id").execute()
                )
                file_id = file_result["id"]
                logger.info(f"Successfully uploaded '{filename}': {file_id}")
                return file_id

    def upload_to_public_share(
        self,
        root_folder_id: str,
        brand_code: str,
        base_name: str,
        output_files: dict,
        warnings: Optional[list] = None,
    ) -> Dict[str, str]:
        """
        Upload final files to public share folder structure.

        Creates/uses subfolders:
        - MP4/{brand_code} - {base_name}.mp4 (lossy 4k)
        - MP4-720p/{brand_code} - {base_name}.mp4
        - CDG/{brand_code} - {base_name}.zip

        Args:
            root_folder_id: Google Drive folder ID for public share root
            brand_code: Brand code (e.g., "NOMAD-1163")
            base_name: Base filename (e.g., "Artist - Title")
            output_files: Dictionary with output file paths:
                - final_karaoke_lossy_mp4: 4K lossy MP4
                - final_karaoke_lossy_720p_mp4: 720p lossy MP4
                - final_karaoke_cdg_zip: CDG package ZIP

        Returns:
            Dictionary mapping category to uploaded file ID
        """
        # Sanitize base_name to handle Unicode characters (curly quotes, em dashes, etc.)
        # that could cause issues with Google Drive API queries and file naming
        safe_base_name = sanitize_filename(base_name) if base_name else base_name
        filename_base = f"{brand_code} - {safe_base_name}"
        uploaded_files = {}

        logger.info(
            f"Uploading public share files to Google Drive folder {root_folder_id}"
        )
        logger.info(f"Filename base: {filename_base}")

        # Upload lossy 4k to MP4/
        lossy_mp4_path = output_files.get("final_karaoke_lossy_mp4")
        if lossy_mp4_path and os.path.exists(lossy_mp4_path):
            mp4_folder_id = self.get_or_create_folder(root_folder_id, "MP4")
            file_id = self.upload_file(
                lossy_mp4_path,
                mp4_folder_id,
                f"{filename_base}.mp4",
            )
            uploaded_files["mp4"] = file_id
            logger.info(f"Uploaded 4K MP4 to MP4/ folder")

        # Upload 720p to MP4-720p/
        mp4_720p_path = output_files.get("final_karaoke_lossy_720p_mp4")
        if mp4_720p_path and os.path.exists(mp4_720p_path):
            mp4_720_folder_id = self.get_or_create_folder(root_folder_id, "MP4-720p")
            file_id = self.upload_file(
                mp4_720p_path,
                mp4_720_folder_id,
                f"{filename_base}.mp4",
            )
            uploaded_files["mp4_720p"] = file_id
            logger.info(f"Uploaded 720p MP4 to MP4-720p/ folder")

            # Fast-sync the 720p master straight to the Divebar GCS mirror so kjbox
            # (which mirrors that folder every 5 min) picks it up within minutes,
            # instead of waiting for the nightly Drive->GCS VM. Nomad-brand only,
            # best-effort, never fatal to the Drive upload. A failure is surfaced as
            # a distribution warning (drives the admin alert) so this can't silently
            # rot into "nightly VM only" — the nightly VM stays the backfill net.
            fast_sync_warning = self._fast_sync_nomad_master(
                brand_code, mp4_720p_path, f"{filename_base}.mp4"
            )
            if fast_sync_warning and warnings is not None:
                warnings.append(fast_sync_warning)

        # Push the padded original-vocals guide (silence[intro] + mixed_vocals) to the
        # vocals prefix in the same Divebar mirror, so kjbox can layer it under the master
        # at the "Original Vocals" slider. Same Nomad-brand gating + best-effort contract
        # as the 720p push; the guide was pre-built by the orchestrator when available.
        guide_path = output_files.get("original_vocals_guide")
        if guide_path and os.path.exists(guide_path):
            guide_warning = self._fast_sync_vocals_guide(
                brand_code, guide_path, f"{filename_base}.flac"
            )
            if guide_warning and warnings is not None:
                warnings.append(guide_warning)

        # Upload CDG ZIP to CDG/
        cdg_zip_path = output_files.get("final_karaoke_cdg_zip")
        if cdg_zip_path and os.path.exists(cdg_zip_path):
            cdg_folder_id = self.get_or_create_folder(root_folder_id, "CDG")
            file_id = self.upload_file(
                cdg_zip_path,
                cdg_folder_id,
                f"{filename_base}.zip",
            )
            uploaded_files["cdg"] = file_id
            logger.info(f"Uploaded CDG ZIP to CDG/ folder")

        logger.info(f"Public share upload complete: {len(uploaded_files)} files uploaded")
        return uploaded_files

    def _fast_sync_nomad_master(
        self, brand_code: str, local_720p_path: str, filename: str
    ) -> Optional[str]:
        """Best-effort push of a freshly-published Nomad 720p master to the Divebar
        GCS mirror so kjbox picks it up within minutes (vs the nightly VM).

        No-op unless the fast-sync is enabled and this is a public Nomad release
        (``NOMAD-####``, excluding ``NOMADNP`` private tracks). Never raises — the
        Drive upload has already succeeded and the nightly VM is the backfill net.

        Returns a human-readable warning string on failure (so the caller can surface
        it via ``distribution_warnings`` / the admin alert), or ``None`` on
        success or when skipped.
        """
        try:
            from backend.config import settings
            from backend.services.nomad_master_mirror import (
                NomadMasterMirror,
                is_nomad_public_brand,
            )

            if not settings.nomad_master_fast_sync_enabled:
                return None
            if not is_nomad_public_brand(brand_code):
                return None

            if NomadMasterMirror().push_720p(local_720p_path, filename):
                return None
            return (
                f"Nomad master fast-sync to the GCS mirror failed for {filename} "
                f"(the nightly Drive->GCS VM will backfill it)"
            )
        except Exception as e:  # noqa: BLE001 - never fatal to the pipeline
            logger.warning(f"Nomad master fast-sync skipped (unexpected error): {e}")
            return f"Nomad master fast-sync errored for {filename}: {e}"

    def _fast_sync_vocals_guide(
        self, brand_code: str, local_path: str, filename: str
    ) -> Optional[str]:
        """Best-effort push of a freshly-built original-vocals guide to the Divebar GCS
        mirror's vocals prefix so kjbox can pull it into ``NOMAD-vocals-padded/``.

        No-op unless the fast-sync is enabled and this is a public Nomad release
        (``NOMAD-####``, excluding ``NOMADNP``). Never raises — nothing downstream
        depends on the guide. Returns a human-readable warning on failure (surfaced via
        ``distribution_warnings`` / the admin alert), or ``None`` on success or skip.
        """
        try:
            from backend.config import settings
            from backend.services.nomad_master_mirror import (
                NomadMasterMirror,
                is_nomad_public_brand,
            )

            if not settings.nomad_master_fast_sync_enabled:
                return None
            if not is_nomad_public_brand(brand_code):
                return None

            if NomadMasterMirror().push_vocals_guide(local_path, filename):
                return None
            return (
                f"Original-vocals guide sync to the GCS mirror failed for {filename}"
            )
        except Exception as e:  # noqa: BLE001 - never fatal to the pipeline
            logger.warning(f"Original-vocals guide sync skipped (unexpected error): {e}")
            return f"Original-vocals guide sync errored for {filename}: {e}"

    def delete_file(self, file_id: str) -> bool:
        """
        Delete a file from Google Drive.

        Retries on transient connection errors (BrokenPipeError, SSLError)
        that occur when Cloud Run containers sit idle between jobs.

        Args:
            file_id: Google Drive file ID to delete

        Returns:
            True if deleted successfully, False otherwise
        """
        logger.info(f"Deleting Google Drive file: {file_id}")

        try:
            for attempt in Retrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=1, min=2, max=8),
                retry=retry_if_exception_type(TRANSIENT_ERRORS),
                before_sleep=lambda retry_state: self._reset_service(),
                reraise=True,
            ):
                with attempt:
                    self.service.files().delete(fileId=file_id).execute()
                    logger.info(f"Successfully deleted file: {file_id}")
                    return True
        except Exception as e:
            # Check if it's a 404 (already deleted)
            if hasattr(e, 'resp') and e.resp.status == 404:
                logger.warning(f"File not found (already deleted?): {file_id}")
                return True
            logger.error(f"Failed to delete Google Drive file: {e}")
            return False

    def delete_files(self, file_ids: list[str]) -> dict[str, bool]:
        """
        Delete multiple files from Google Drive.

        Args:
            file_ids: List of Google Drive file IDs to delete

        Returns:
            Dictionary mapping file_id to success status
        """
        results = {}
        for file_id in file_ids:
            results[file_id] = self.delete_file(file_id)
        return results

    def _search_subfolder_for_brand(
        self, root_folder_id: str, subfolder: str, brand_code: str
    ) -> list[str]:
        """Return file IDs in ``subfolder`` whose name starts with ``{brand_code} - ``.

        Read-only Drive lookups, wrapped in retry-with-backoff so transient
        connection drops (SSL EOF / BrokenPipe against idle Cloud Run containers)
        self-heal instead of surfacing as "GDrive brand_code search incomplete".
        Non-transient errors reraise immediately for the caller to record.
        """

        def _do_search() -> list[str]:
            ids: list[str] = []
            # Find the subfolder
            escaped_subfolder = subfolder.replace("'", "\\'")
            query = (
                f"name='{escaped_subfolder}' and '{root_folder_id}' in parents "
                f"and mimeType='application/vnd.google-apps.folder' and trashed=false"
            )
            results = self.service.files().list(
                q=query, fields="files(id, name)", supportsAllDrives=True,
                includeItemsFromAllDrives=True
            ).execute()

            if not results.get("files"):
                logger.debug(f"Subfolder '{subfolder}' not found in {root_folder_id}")
                return ids

            subfolder_id = results["files"][0]["id"]

            # Search for files whose name starts with the brand code
            # Use contains for efficiency, then filter by exact prefix
            escaped_brand = brand_code.replace("'", "\\'")
            file_query = (
                f"name contains '{escaped_brand}' and '{subfolder_id}' in parents "
                f"and trashed=false"
            )
            file_results = self.service.files().list(
                q=file_query, fields="files(id, name)", supportsAllDrives=True,
                includeItemsFromAllDrives=True
            ).execute()

            for f in file_results.get("files", []):
                if f["name"].startswith(f"{brand_code} - "):
                    ids.append(f["id"])
                    logger.info(
                        f"Found GDrive file to clean up in {subfolder}/: "
                        f"{f['name']} ({f['id']})"
                    )
            return ids

        for attempt in Retrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=8),
            retry=retry_if_exception_type(TRANSIENT_ERRORS),
            before_sleep=lambda retry_state: self._reset_service(),
            reraise=True,
        ):
            with attempt:
                return _do_search()

        return []  # pragma: no cover - Retrying always returns or raises

    def find_files_by_brand_code(self, root_folder_id: str, brand_code: str) -> list[str]:
        """
        Search for files in public share subfolders matching a brand code prefix.

        Used as a fallback when gdrive_files is not tracked in state_data (e.g., old
        jobs uploaded before file ID tracking was implemented, or jobs where GDrive
        upload succeeded but file IDs were not saved due to a bug).

        Searches CDG/, MP4/, MP4-720p/ subfolders for files whose name starts with
        '{brand_code} - '.

        Args:
            root_folder_id: Root Google Drive folder ID for the public share
            brand_code: Brand code prefix to search for (e.g., "NOMAD-1271")

        Returns:
            List of file IDs found matching the brand code
        """
        found_ids = []
        subfolders = ["CDG", "MP4", "MP4-720p"]
        search_errors: list[Exception] = []

        for subfolder in subfolders:
            try:
                found_ids.extend(
                    self._search_subfolder_for_brand(root_folder_id, subfolder, brand_code)
                )
            except Exception as e:
                logger.error(
                    f"Error searching for brand_code '{brand_code}' in '{subfolder}': {e}",
                    exc_info=True,
                )
                search_errors.append(e)

        if search_errors:
            # Raise so the caller knows the search was incomplete.
            # This prevents the cleanup endpoint from treating an uncertain result
            # as "GDrive is clean" and incorrectly recycling the brand code.
            raise RuntimeError(
                f"GDrive brand_code search incomplete: "
                f"{len(search_errors)}/{len(subfolders)} subfolder(s) failed"
            )

        return found_ids


# Singleton instance
_gdrive_service: Optional[GoogleDriveService] = None


def get_gdrive_service() -> GoogleDriveService:
    """Get the singleton Google Drive service instance."""
    global _gdrive_service
    if _gdrive_service is None:
        _gdrive_service = GoogleDriveService()
    return _gdrive_service
