"""Native Dropbox API service for cloud backend.

This service provides Dropbox operations using the native Python SDK,
replacing rclone for server-side operations. It handles:
- Folder listing for brand code calculation
- File and folder uploads
- Shared link generation

Credentials are loaded from Google Cloud Secret Manager.
"""
import json
import logging
import os
import re
from typing import Optional

from google.cloud import secretmanager

logger = logging.getLogger(__name__)


class DropboxService:
    """Dropbox operations using native Python SDK."""

    def __init__(self):
        self._client = None
        self._is_configured = False

    def _load_credentials(self) -> Optional[dict]:
        """Load OAuth credentials from Secret Manager."""
        try:
            client = secretmanager.SecretManagerServiceClient()
            # Try to get the project ID from environment or use default
            project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "karaoke-gen")
            name = f"projects/{project_id}/secrets/dropbox-oauth-credentials/versions/latest"
            response = client.access_secret_version(request={"name": name})
            return json.loads(response.payload.data.decode("UTF-8"))
        except Exception as e:
            logger.warning(f"Failed to load Dropbox credentials from Secret Manager: {e}")
            return None

    @property
    def is_configured(self) -> bool:
        """Check if Dropbox credentials are available."""
        if self._is_configured:
            return True
        creds = self._load_credentials()
        self._is_configured = creds is not None and "access_token" in creds
        return self._is_configured

    @property
    def client(self):
        """Get or create Dropbox client."""
        if self._client is None:
            # Import here to avoid import errors if dropbox is not installed
            try:
                from dropbox import Dropbox
            except ImportError:
                raise ImportError(
                    "dropbox package is not installed. "
                    "Install it with: pip install dropbox"
                )

            creds = self._load_credentials()
            if not creds:
                raise RuntimeError("Dropbox credentials not configured in Secret Manager")

            self._client = Dropbox(
                oauth2_access_token=creds["access_token"],
                oauth2_refresh_token=creds.get("refresh_token"),
                app_key=creds.get("app_key"),
                app_secret=creds.get("app_secret"),
            )
        return self._client

    def list_folders(self, path: str) -> list[str]:
        """
        List folder names at path for brand code calculation.

        Args:
            path: Dropbox path to list (e.g., "/Karaoke/Tracks-Organized")

        Returns:
            List of folder names in the path
        """
        from dropbox.files import FolderMetadata

        # Ensure path starts with /
        if not path.startswith("/"):
            path = f"/{path}"

        logger.info(f"Listing folders at Dropbox path: {path}")

        result = self.client.files_list_folder(path)
        folders = []

        # Get all entries (handling pagination)
        while True:
            for entry in result.entries:
                if isinstance(entry, FolderMetadata):
                    folders.append(entry.name)

            if not result.has_more:
                break
            result = self.client.files_list_folder_continue(result.cursor)

        logger.info(f"Found {len(folders)} folders in {path}")
        return folders

    def get_next_brand_code(self, path: str, brand_prefix: str) -> str:
        """
        Calculate next brand code from existing folders, filling gaps in the sequence.

        Scans the folder for existing brand codes like "NOMAD-0001" and
        returns the first available code, filling any gaps left by deleted outputs.

        Args:
            path: Dropbox path containing organized folders
            brand_prefix: Brand prefix (e.g., "NOMAD")

        Returns:
            Next brand code (e.g., "NOMAD-0003" if 0003 is the first gap)
        """
        folders = self.list_folders(path)
        pattern = re.compile(rf"^{re.escape(brand_prefix)}-(\d{{4}})")

        existing_nums: set[int] = set()
        for folder in folders:
            match = pattern.match(folder)
            if match:
                existing_nums.add(int(match.group(1)))

        max_existing = max(existing_nums) if existing_nums else 0

        # Only fill gaps for numbers >= 1001 (preserve legacy gaps below 1000)
        if max_existing >= 1000:
            next_num = 1001
            while next_num in existing_nums:
                next_num += 1
        else:
            next_num = max_existing + 1

        next_code = f"{brand_prefix}-{next_num:04d}"
        if max_existing >= 1000 and next_num <= max_existing:
            logger.info(f"Next brand code for {brand_prefix}: {next_code} (filling gap, max existing: {max_existing})")
        else:
            logger.info(f"Next brand code for {brand_prefix}: {next_code} (max existing: {max_existing})")
        return next_code

    def upload_file(self, local_path: str, remote_path: str) -> None:
        """
        Upload a single file to Dropbox.

        Args:
            local_path: Local file path
            remote_path: Dropbox destination path (must start with /)
        """
        from dropbox.files import WriteMode

        # Ensure remote path starts with /
        if not remote_path.startswith("/"):
            remote_path = f"/{remote_path}"

        file_size = os.path.getsize(local_path)
        logger.info(f"Uploading {local_path} ({file_size / 1024 / 1024:.1f} MB) to {remote_path}")

        # For large files (>150MB), use upload sessions
        CHUNK_SIZE = 150 * 1024 * 1024  # 150 MB

        with open(local_path, "rb") as f:
            if file_size <= CHUNK_SIZE:
                # Simple upload for smaller files
                self.client.files_upload(f.read(), remote_path, mode=WriteMode.overwrite)
            else:
                # Chunked upload for large files
                self._upload_large_file(f, remote_path, file_size)

        logger.info(f"Successfully uploaded to {remote_path}")

    def _upload_large_file(self, file_obj, remote_path: str, file_size: int) -> None:
        """Upload a large file using upload sessions."""
        from dropbox.files import CommitInfo, UploadSessionCursor, WriteMode

        CHUNK_SIZE = 150 * 1024 * 1024  # 150 MB

        # Start upload session
        session_start = self.client.files_upload_session_start(file_obj.read(CHUNK_SIZE))
        cursor = UploadSessionCursor(
            session_id=session_start.session_id,
            offset=file_obj.tell(),
        )
        commit = CommitInfo(path=remote_path, mode=WriteMode.overwrite)

        # Upload remaining chunks
        while file_obj.tell() < file_size:
            remaining = file_size - file_obj.tell()
            if remaining <= CHUNK_SIZE:
                # Final chunk
                self.client.files_upload_session_finish(
                    file_obj.read(CHUNK_SIZE),
                    cursor,
                    commit,
                )
            else:
                # Intermediate chunk
                self.client.files_upload_session_append_v2(
                    file_obj.read(CHUNK_SIZE),
                    cursor,
                )
                cursor.offset = file_obj.tell()

    def upload_folder(self, local_dir: str, remote_path: str) -> None:
        """
        Recursively upload all files and subdirectories to Dropbox folder.

        Args:
            local_dir: Local directory to upload
            remote_path: Dropbox destination folder path
        """
        # Ensure remote path starts with /
        if not remote_path.startswith("/"):
            remote_path = f"/{remote_path}"

        logger.info(f"Uploading folder {local_dir} to {remote_path}")

        uploaded_count = 0
        for root, _dirs, files in os.walk(local_dir):
            # Calculate the relative path from local_dir to current root
            rel_root = os.path.relpath(root, local_dir)
            if rel_root == ".":
                current_remote = remote_path
            else:
                current_remote = f"{remote_path}/{rel_root}"

            for filename in files:
                local_file = os.path.join(root, filename)
                remote_file = f"{current_remote}/{filename}"
                self.upload_file(local_file, remote_file)
                uploaded_count += 1

        logger.info(f"Uploaded {uploaded_count} files to {remote_path}")

    def create_shared_link(self, path: str) -> str:
        """
        Create and return a shared link for the path.

        Args:
            path: Dropbox path to share

        Returns:
            Shared link URL
        """
        from dropbox.exceptions import ApiError
        from dropbox.sharing import SharedLinkSettings

        # Ensure path starts with /
        if not path.startswith("/"):
            path = f"/{path}"

        logger.info(f"Creating shared link for: {path}")

        try:
            # Try to create a new shared link
            settings = SharedLinkSettings(requested_visibility=None)
            link = self.client.sharing_create_shared_link_with_settings(path, settings)
            logger.info(f"Created new shared link: {link.url}")
            return link.url
        except ApiError as e:
            # Link may already exist - try to get existing link
            if e.error.is_shared_link_already_exists():
                logger.info("Shared link already exists, retrieving existing link")
                links = self.client.sharing_list_shared_links(path=path, direct_only=True)
                if links.links:
                    logger.info(f"Found existing shared link: {links.links[0].url}")
                    return links.links[0].url
            raise

    def delete_folder(self, path: str) -> bool:
        """
        Delete a folder and all its contents from Dropbox.

        Args:
            path: Dropbox path to delete (e.g., "/Karaoke/Tracks-Organized/NOMAD-1234 - Artist - Title")

        Returns:
            True if deleted successfully, False otherwise
        """
        from dropbox.exceptions import ApiError

        # Ensure path starts with /
        if not path.startswith("/"):
            path = f"/{path}"

        logger.info(f"Deleting Dropbox folder: {path}")

        try:
            self.client.files_delete_v2(path)
            logger.info(f"Successfully deleted: {path}")
            return True
        except ApiError as e:
            if e.error.is_path_lookup() and e.error.get_path_lookup().is_not_found():
                logger.warning(f"Folder not found (already deleted?): {path}")
                return True  # Consider it success if already gone
            logger.error(f"Failed to delete Dropbox folder: {e}")
            return False
        except Exception as e:
            logger.error(f"Error deleting Dropbox folder: {e}")
            return False


def get_dropbox_service() -> DropboxService:
    """Factory function to get a DropboxService instance."""
    return DropboxService()
