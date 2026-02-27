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


# Singleton instance
_gdrive_service: Optional[GoogleDriveService] = None


def get_gdrive_service() -> GoogleDriveService:
    """Get the singleton Google Drive service instance."""
    global _gdrive_service
    if _gdrive_service is None:
        _gdrive_service = GoogleDriveService()
    return _gdrive_service
