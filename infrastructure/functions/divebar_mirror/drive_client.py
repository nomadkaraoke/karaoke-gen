"""
Google Drive API client for listing files in the Divebar shared folder.

Handles:
- Recursive folder traversal
- Drive shortcuts (application/vnd.google-apps.shortcut)
- Pagination (1000 items per page)
- Rate limiting
"""

import logging
import time
from typing import Optional

from google.auth import default
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

# MIME types
FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"

# Fields to request for files
FILE_FIELDS = "id, name, mimeType, size, md5Checksum, shortcutDetails"
LIST_FIELDS = f"nextPageToken, files({FILE_FIELDS})"


def get_drive_service():
    """Create a Google Drive API service using Application Default Credentials."""
    credentials, _ = default(scopes=["https://www.googleapis.com/auth/drive.readonly"])
    return build("drive", "v3", credentials=credentials)


def _resolve_shortcut(service, file_info: dict) -> Optional[dict]:
    """Resolve a Drive shortcut to its target file/folder."""
    shortcut_details = file_info.get("shortcutDetails", {})
    target_id = shortcut_details.get("targetId")
    target_mime = shortcut_details.get("targetMimeType")

    if not target_id:
        logger.warning("Shortcut %s has no target ID", file_info.get("name"))
        return None

    return {
        "id": target_id,
        "name": file_info["name"],
        "mimeType": target_mime or FOLDER_MIME,
    }


def _list_page(service, folder_id: str, page_token: Optional[str] = None) -> tuple[list[dict], Optional[str]]:
    """List one page of files in a folder. Returns (files, next_page_token)."""
    query = f"'{folder_id}' in parents and trashed=false"
    try:
        response = service.files().list(
            q=query,
            fields=LIST_FIELDS,
            pageSize=1000,
            pageToken=page_token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        return response.get("files", []), response.get("nextPageToken")
    except HttpError as e:
        if e.resp.status == 404:
            logger.warning("Folder %s not found (may be deleted shortcut target)", folder_id)
            return [], None
        if e.resp.status == 403:
            logger.warning("Access denied to folder %s (may be private shortcut target)", folder_id)
            return [], None
        raise


def list_all_files(service, folder_id: str) -> list[dict]:
    """List ALL files in a folder (handles pagination)."""
    all_files = []
    page_token = None
    while True:
        files, page_token = _list_page(service, folder_id, page_token)
        all_files.extend(files)
        if not page_token:
            break
    return all_files


def list_divebar_recursive(
    service,
    root_folder_id: str,
    max_depth: int = 6,
) -> list[dict]:
    """
    Recursively list all karaoke files in the Divebar shared folder.

    Returns a flat list of file info dicts with added 'brand' and 'path' fields:
    {
        "id": "abc123",
        "name": "Artist - Title.mp4",
        "mimeType": "video/mp4",
        "size": "45000000",
        "md5Checksum": "abc...",
        "brand": "WTF Karaoke Videos",
        "path": "WTF Karaoke Videos/Artist - Title.mp4",
        "subfolder": "",  # e.g. "CDG" for brand > CDG > file.cdg
    }
    """
    result = []
    visited_folders = set()
    stats = {"folders_visited": 0, "files_found": 0, "shortcuts_resolved": 0, "errors": 0}

    def _recurse(folder_id: str, brand: str, path_prefix: str, subfolder: str, depth: int):
        if depth > max_depth:
            logger.warning("Max depth reached at %s", path_prefix)
            return

        items = list_all_files(service, folder_id)
        stats["folders_visited"] += 1

        # Brief rate limit pause every 10 folders
        if stats["folders_visited"] % 10 == 0:
            time.sleep(0.1)

        for item in items:
            name = item["name"]
            mime = item["mimeType"]

            # Handle shortcuts
            if mime == SHORTCUT_MIME:
                resolved = _resolve_shortcut(service, item)
                if not resolved:
                    stats["errors"] += 1
                    continue
                stats["shortcuts_resolved"] += 1
                item_id = resolved["id"]
                item_mime = resolved["mimeType"]
                item_name = resolved["name"]
            else:
                item_id = item["id"]
                item_mime = mime
                item_name = name

            # Recurse into subfolders (skip if already visited to prevent cycles)
            if item_mime == FOLDER_MIME:
                if item_id in visited_folders:
                    logger.debug("Skipping already-visited folder %s (%s)", item_name, item_id)
                    continue
                visited_folders.add(item_id)
                child_brand = brand or item_name
                child_subfolder = item_name if brand else ""
                child_path = f"{path_prefix}/{item_name}" if path_prefix else item_name
                _recurse(item_id, child_brand, child_path, child_subfolder, depth + 1)
            else:
                # It's a file
                file_path = f"{path_prefix}/{item_name}" if path_prefix else item_name
                result.append({
                    "id": item_id,
                    "name": item_name,
                    "mimeType": item_mime,
                    "size": item.get("size"),
                    "md5Checksum": item.get("md5Checksum"),
                    "brand": brand or "Unknown",
                    "path": file_path,
                    "subfolder": subfolder,
                })
                stats["files_found"] += 1

    logger.info("Starting recursive listing of folder %s", root_folder_id)
    _recurse(root_folder_id, "", "", "", 0)
    logger.info(
        "Listing complete: %d files, %d folders, %d shortcuts resolved, %d errors",
        stats["files_found"],
        stats["folders_visited"],
        stats["shortcuts_resolved"],
        stats["errors"],
    )
    return result
