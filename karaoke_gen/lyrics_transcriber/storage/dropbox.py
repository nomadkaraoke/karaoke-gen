from dataclasses import dataclass
from typing import Protocol, BinaryIO, Optional, List, Any
import os
import time
import logging
import requests
from dropbox import Dropbox
from dropbox.files import WriteMode, FileMetadata
from dropbox.sharing import RequestedVisibility, SharedLinkSettings
from dropbox.exceptions import AuthError, ApiError

logger = logging.getLogger(__name__)


@dataclass
class DropboxConfig:
    """Configuration for Dropbox client."""

    app_key: Optional[str] = None
    app_secret: Optional[str] = None
    refresh_token: Optional[str] = None

    @classmethod
    def from_env(cls) -> "DropboxConfig":
        """Create config from environment variables."""
        return cls(
            app_key=os.environ.get("WHISPER_DROPBOX_APP_KEY"),
            app_secret=os.environ.get("WHISPER_DROPBOX_APP_SECRET"),
            refresh_token=os.environ.get("WHISPER_DROPBOX_REFRESH_TOKEN"),
        )


class DropboxAPI(Protocol):
    """Protocol for Dropbox API operations."""

    def files_upload(self, f: bytes, path: str, mode: WriteMode) -> Any: ...
    def files_list_folder(self, path: str, recursive: bool = False) -> Any: ...
    def files_list_folder_continue(self, cursor: str) -> Any: ...
    def files_download(self, path: str) -> tuple[Any, Any]: ...
    def files_download_to_file(self, download_path: str, path: str) -> None: ...
    def files_get_metadata(self, path: str) -> Any: ...
    def sharing_create_shared_link_with_settings(self, path: str, settings: SharedLinkSettings) -> Any: ...
    def sharing_list_shared_links(self, path: str) -> Any: ...


class DropboxHandler:
    """Handles Dropbox storage operations with automatic token refresh."""

    def __init__(
        self,
        config: Optional[DropboxConfig] = None,
        client: Optional[DropboxAPI] = None,
    ):
        """Initialize the Dropbox handler."""
        self.config = config or DropboxConfig.from_env()
        self._validate_config()

        self.client = client or Dropbox(
            app_key=self.config.app_key,
            app_secret=self.config.app_secret,
            oauth2_refresh_token=self.config.refresh_token,
        )

    def _validate_config(self) -> None:
        """Validate the configuration."""
        logger.debug("Validating DropboxConfig with values:")
        logger.debug(f"app_key: {self.config.app_key[:4] + '...' if self.config.app_key else 'None'}")
        logger.debug(f"app_secret: {self.config.app_secret[:4] + '...' if self.config.app_secret else 'None'}")
        logger.debug(f"refresh_token: {self.config.refresh_token[:4] + '...' if self.config.refresh_token else 'None'}")

        missing = []
        if not self.config.app_key:
            missing.append("app_key")
        if not self.config.app_secret:
            missing.append("app_secret")
        if not self.config.refresh_token:
            missing.append("refresh_token")

        if missing:
            error_msg = f"Missing required Dropbox configuration: {', '.join(missing)}"
            logger.error(error_msg)
            raise ValueError(error_msg)

    def upload_with_retry(self, file: BinaryIO, path: str, max_retries: int = 3) -> None:
        """Upload a file to Dropbox with retries."""
        for attempt in range(max_retries):
            try:
                logger.debug(f"Attempting file upload to {path} (attempt {attempt + 1}/{max_retries})")
                file.seek(0)
                self.client.files_upload(file.read(), path, mode=WriteMode.overwrite)
                logger.debug(f"Successfully uploaded file to {path}")
                return
            except ApiError as e:
                logger.warning(f"Upload attempt {attempt + 1} failed: {str(e)}")
                if attempt == max_retries - 1:
                    logger.error(f"All upload attempts failed for {path}")
                    raise
                time.sleep(1 * (attempt + 1))

    def upload_string_with_retry(self, content: str, path: str, max_retries: int = 3) -> None:
        """Upload a string content to Dropbox with retries."""
        for attempt in range(max_retries):
            try:
                logger.debug(f"Attempting string upload to {path} (attempt {attempt + 1}/{max_retries})")
                self.client.files_upload(content.encode(), path, mode=WriteMode.overwrite)
                logger.debug(f"Successfully uploaded string content to {path}")
                return
            except ApiError as e:
                logger.warning(f"Upload attempt {attempt + 1} failed: {str(e)}")
                if attempt == max_retries - 1:
                    logger.error(f"All upload attempts failed for {path}")
                    raise
                time.sleep(1 * (attempt + 1))

    def list_folder_recursive(self, path: str = "") -> List[FileMetadata]:
        """List all files in a folder recursively."""
        try:
            logger.debug(f"Listing files recursively from {path}")
            entries = []
            result = self.client.files_list_folder(path, recursive=True)

            while True:
                entries.extend(result.entries)
                if not result.has_more:
                    break
                result = self.client.files_list_folder_continue(result.cursor)

            return entries
        except Exception as e:
            logger.error(f"Error listing files: {str(e)}", exc_info=True)
            raise

    def download_file_content(self, path: str) -> bytes:
        """Download and return the content of a file."""
        try:
            logger.debug(f"Downloading file content from {path}")
            return self.client.files_download(path)[1].content
        except Exception as e:
            logger.error(f"Error downloading file: {str(e)}", exc_info=True)
            raise

    def download_folder(self, dropbox_path: str, local_path: str) -> None:
        """Download all files from a Dropbox folder to a local path."""
        try:
            logger.debug(f"Downloading folder {dropbox_path} to {local_path}")
            entries = self.list_folder_recursive(dropbox_path)

            for entry in entries:
                if isinstance(entry, FileMetadata):
                    rel_path = entry.path_display[len(dropbox_path) :].lstrip("/")
                    local_file_path = os.path.join(local_path, rel_path)

                    os.makedirs(os.path.dirname(local_file_path), exist_ok=True)
                    logger.debug(f"Downloading {entry.path_display} to {local_file_path}")
                    self.client.files_download_to_file(local_file_path, entry.path_display)

            logger.debug(f"Successfully downloaded folder {dropbox_path}")
        except Exception as e:
            logger.error(f"Error downloading folder: {str(e)}", exc_info=True)
            raise

    def upload_folder(self, local_path: str, dropbox_path: str) -> None:
        """Upload all files from a local folder to a Dropbox path."""
        try:
            logger.debug(f"Uploading folder {local_path} to {dropbox_path}")
            for root, _, files in os.walk(local_path):
                for filename in files:
                    local_file_path = os.path.join(root, filename)
                    rel_path = os.path.relpath(local_file_path, local_path)
                    target_path = f"{dropbox_path}/{rel_path}"

                    logger.debug(f"Uploading {rel_path} to {target_path}")
                    with open(local_file_path, "rb") as f:
                        self.client.files_upload(f.read(), target_path, mode=WriteMode.overwrite)

            logger.debug(f"Successfully uploaded folder {local_path}")
        except Exception as e:
            logger.error(f"Error uploading folder: {str(e)}", exc_info=True)
            raise

    def create_shared_link(self, path: str) -> str:
        """Create a shared link for a file that's accessible without login."""
        try:
            logger.debug(f"Creating shared link for {path}")
            shared_link = self.client.sharing_create_shared_link_with_settings(
                path, settings=SharedLinkSettings(requested_visibility=RequestedVisibility.public)
            )
            return shared_link.url.replace("www.dropbox.com", "dl.dropboxusercontent.com")
        except Exception as e:
            logger.error(f"Error creating shared link: {str(e)}", exc_info=True)
            raise

    def get_existing_shared_link(self, path: str) -> Optional[str]:
        """Get existing shared link for a file if it exists."""
        try:
            logger.debug(f"Getting existing shared link for {path}")
            shared_links = self.client.sharing_list_shared_links(path=path).links
            if shared_links:
                return shared_links[0].url.replace("www.dropbox.com", "dl.dropboxusercontent.com")
            return None
        except Exception as e:
            logger.error(f"Error getting existing shared link: {str(e)}", exc_info=True)
            return None

    def create_or_get_shared_link(self, path: str) -> str:
        """Create a shared link or get existing one."""
        try:
            existing_link = self.get_existing_shared_link(path)
            if existing_link:
                logger.debug(f"Found existing shared link for {path}")
                return existing_link

            logger.debug(f"Creating new shared link for {path}")
            return self.create_shared_link(path)
        except Exception as e:
            logger.error(f"Error creating/getting shared link: {str(e)}", exc_info=True)
            raise

    def file_exists(self, path: str) -> bool:
        """Check if a file exists in Dropbox."""
        try:
            self.client.files_get_metadata(path)
            return True
        except:
            return False
