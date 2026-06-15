"""
Google Cloud Storage operations for file management.
"""
import logging
import os
import json
from typing import Optional, BinaryIO, Any, Dict
from pathlib import Path
from google.cloud import storage
from google.cloud.exceptions import NotFound
from datetime import timedelta

from backend.config import settings


logger = logging.getLogger(__name__)


class StorageService:
    """Service for Google Cloud Storage operations."""
    
    def __init__(self):
        """Initialize GCS client."""
        self.client = storage.Client(project=settings.google_cloud_project)
        self.bucket = self.client.bucket(settings.gcs_bucket_name)
    
    def upload_file(self, local_path: str, destination_path: str) -> str:
        """Upload a file to GCS."""
        try:
            blob = self.bucket.blob(destination_path)
            blob.upload_from_filename(local_path)
            logger.info(f"Uploaded {local_path} to gs://{settings.gcs_bucket_name}/{destination_path}")
            return destination_path
        except Exception as e:
            logger.error(f"Error uploading file {local_path}: {e}")
            raise
    
    def upload_fileobj(self, file_obj: BinaryIO, destination_path: str, content_type: Optional[str] = None) -> str:
        """Upload a file object to GCS."""
        try:
            blob = self.bucket.blob(destination_path)
            if content_type:
                blob.content_type = content_type
            blob.upload_from_file(file_obj, rewind=True)
            logger.info(f"Uploaded file object to gs://{settings.gcs_bucket_name}/{destination_path}")
            return destination_path
        except Exception as e:
            logger.error(f"Error uploading file object: {e}")
            raise
    
    def download_file(self, source_path: str, destination_path: str) -> str:
        """Download a file from GCS."""
        try:
            blob = self.bucket.blob(source_path)
            blob.download_to_filename(destination_path)
            logger.info(f"Downloaded gs://{settings.gcs_bucket_name}/{source_path} to {destination_path}")
            return destination_path
        except Exception as e:
            logger.error(f"Error downloading file {source_path}: {e}")
            raise
    
    def copy_blob(self, source_path: str, destination_path: str) -> str:
        """Server-side copy of a blob within the bucket (no download/upload)."""
        try:
            src = self.bucket.blob(source_path)
            self.bucket.copy_blob(src, self.bucket, destination_path)
            logger.info(f"Copied gs://{settings.gcs_bucket_name}/{source_path} -> {destination_path}")
            return destination_path
        except Exception as e:
            logger.error(f"Error copying blob {source_path} -> {destination_path}: {e}")
            raise

    def generate_signed_url(self, blob_path: str, expiration_minutes: int = 60) -> str:
        """Generate a signed URL for downloading a file.
        
        In Cloud Run, this uses the IAM signBlob API since we don't have
        a private key available. Requires the service account to have
        roles/iam.serviceAccountTokenCreator on itself.
        """
        return self._generate_signed_url_internal(blob_path, "GET", expiration_minutes)
    
    def generate_signed_upload_url(self, blob_path: str, content_type: str = "application/octet-stream", expiration_minutes: int = 60) -> str:
        """Generate a signed URL for uploading a file directly to GCS.
        
        This allows clients to upload files directly to GCS without going through
        the backend, bypassing any request body size limits.
        
        Args:
            blob_path: The destination path in GCS
            content_type: The expected content type of the upload
            expiration_minutes: How long the URL is valid for
            
        Returns:
            A signed URL that accepts PUT requests with the file content
        """
        return self._generate_signed_url_internal(blob_path, "PUT", expiration_minutes, content_type)
    
    def _generate_signed_url_internal(self, blob_path: str, method: str, expiration_minutes: int = 60, content_type: Optional[str] = None) -> str:
        """Internal method to generate signed URLs for GET or PUT operations."""
        import google.auth
        from google.auth.transport import requests
        
        try:
            blob = self.bucket.blob(blob_path)
            
            # Get default credentials and refresh to ensure we have a valid token
            credentials, project = google.auth.default()
            
            # Common kwargs for signed URL generation
            kwargs = {
                "version": "v4",
                "expiration": timedelta(minutes=expiration_minutes),
                "method": method,
            }
            
            # For PUT requests, we need to specify the content type in headers
            if method == "PUT" and content_type:
                kwargs["headers"] = {"Content-Type": content_type}
            
            # Check if we're using compute credentials (Cloud Run/GCE)
            # These need to use IAM signBlob via service_account_email + access_token
            if hasattr(credentials, 'service_account_email'):
                # Refresh credentials to get a valid access token
                auth_request = requests.Request()
                credentials.refresh(auth_request)
                
                kwargs["service_account_email"] = credentials.service_account_email
                kwargs["access_token"] = credentials.token
            
            url = blob.generate_signed_url(**kwargs)
            
            logger.info(f"Generated signed {method} URL for {blob_path}")
            return url
        except Exception as e:
            logger.error(f"Error generating signed {method} URL for {blob_path}: {e}")
            raise
    
    def delete_file(self, blob_path: str, ignore_missing: bool = False) -> bool:
        """Delete a file from GCS.

        Args:
            blob_path: Path of the object to delete.
            ignore_missing: If True, a missing object (404 NotFound) is treated as a
                successful no-op and logged at debug level instead of error. Use for
                best-effort deletes over a template of possible paths (e.g. job
                restart/regen) where many files legitimately never existed.

        Returns:
            True if an object was actually deleted, False if it was already absent
            (only possible when ignore_missing=True; otherwise a missing object raises).
        """
        try:
            blob = self.bucket.blob(blob_path)
            blob.delete()
            logger.info(f"Deleted gs://{settings.gcs_bucket_name}/{blob_path}")
            return True
        except NotFound:
            if ignore_missing:
                logger.debug(f"File already absent, skipping delete: {blob_path}")
                return False
            logger.error(f"Error deleting file {blob_path}: 404 object not found")
            raise
        except Exception as e:
            logger.error(f"Error deleting file {blob_path}: {e}")
            raise
    
    def delete_folder(self, prefix: str) -> int:
        """
        Delete all files in GCS with a given prefix (folder).
        
        Args:
            prefix: The folder prefix to delete (e.g., "uploads/abc123/")
            
        Returns:
            Number of files deleted
        """
        try:
            blobs = list(self.bucket.list_blobs(prefix=prefix))
            deleted_count = 0
            
            for blob in blobs:
                try:
                    blob.delete()
                    deleted_count += 1
                except Exception as e:
                    logger.warning(f"Error deleting blob {blob.name}: {e}")
            
            if deleted_count > 0:
                logger.info(f"Deleted {deleted_count} files from gs://{settings.gcs_bucket_name}/{prefix}")
            
            return deleted_count
        except Exception as e:
            logger.error(f"Error deleting folder {prefix}: {e}")
            return 0  # Don't raise - folder deletion shouldn't break operations
    
    def list_files(self, prefix: str) -> list:
        """List files in GCS with a given prefix."""
        try:
            blobs = self.bucket.list_blobs(prefix=prefix)
            return [blob.name for blob in blobs]
        except Exception as e:
            logger.error(f"Error listing files with prefix {prefix}: {e}")
            raise
    
    def file_exists(self, blob_path: str) -> bool:
        """Check if a file exists in GCS."""
        try:
            blob = self.bucket.blob(blob_path)
            return blob.exists()
        except Exception as e:
            logger.error(f"Error checking file existence {blob_path}: {e}")
            raise
    
    def upload_json(self, destination_path: str, data: Dict[str, Any]) -> str:
        """Upload a JSON object to GCS."""
        try:
            blob = self.bucket.blob(destination_path)
            blob.content_type = "application/json"
            blob.upload_from_string(
                json.dumps(data, indent=2, ensure_ascii=False),
                content_type="application/json"
            )
            logger.info(f"Uploaded JSON to gs://{settings.gcs_bucket_name}/{destination_path}")
            return destination_path
        except Exception as e:
            logger.error(f"Error uploading JSON to {destination_path}: {e}")
            raise
    
    def download_json(self, source_path: str) -> Dict[str, Any]:
        """Download and parse a JSON file from GCS."""
        try:
            blob = self.bucket.blob(source_path)
            content = blob.download_as_text()
            data = json.loads(content)
            logger.info(f"Downloaded JSON from gs://{settings.gcs_bucket_name}/{source_path}")
            return data
        except Exception as e:
            logger.error(f"Error downloading JSON from {source_path}: {e}")
            raise

