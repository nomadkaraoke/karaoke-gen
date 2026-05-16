"""
OAuth Credential Manager for validating and refreshing credentials.

This service provides:
1. Credential validation for YouTube, Dropbox, and Google Drive
2. Device Authorization Flow for re-authentication
3. Proactive monitoring with alerts when credentials expire
"""
import json
import logging
import time
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass
from enum import Enum
from datetime import datetime

from google.oauth2.credentials import Credentials as GoogleCredentials
from google.auth.transport.requests import Request as GoogleAuthRequest

from backend.config import get_settings

logger = logging.getLogger(__name__)


def _destroy_old_secret_versions(client, secret_path: str, keep: int = 5) -> None:
    """Destroy ENABLED secret versions beyond the newest `keep`.

    OAuth credential secrets are rewritten on every refresh, which would
    otherwise accumulate hundreds of versions and cost ~$0.06/version/month
    in replica storage. Only the newest version is read (`versions/latest`),
    so older versions are dead weight.
    """
    try:
        versions = list(client.list_secret_versions(request={"parent": secret_path}))
        enabled = [
            v for v in versions
            if v.state == v.State.ENABLED  # type: ignore[attr-defined]
        ]
        enabled.sort(key=lambda v: v.create_time, reverse=True)
        for v in enabled[keep:]:
            try:
                client.destroy_secret_version(request={"name": v.name})
            except Exception as e:
                logger.warning(f"Failed to destroy {v.name}: {e}")
    except Exception as e:
        logger.warning(f"Failed to prune old secret versions for {secret_path}: {e}")


class CredentialStatus(str, Enum):
    """Status of OAuth credentials."""
    VALID = "valid"
    EXPIRED = "expired"  # Token expired but refresh may work
    INVALID = "invalid"  # Refresh failed, re-auth needed
    NOT_CONFIGURED = "not_configured"  # No credentials stored
    ERROR = "error"  # Unknown error during validation


@dataclass
class CredentialCheckResult:
    """Result of a credential validation check."""
    service: str
    status: CredentialStatus
    message: str
    last_checked: datetime
    expires_at: Optional[datetime] = None


@dataclass  
class DeviceAuthInfo:
    """Information for device authorization flow."""
    device_code: str
    user_code: str
    verification_url: str
    expires_in: int
    interval: int
    started_at: datetime


class CredentialManager:
    """
    Manages OAuth credentials for all external services.
    
    Provides validation, refresh, and device authorization flow for:
    - YouTube (Google OAuth)
    - Google Drive (Google OAuth)
    - Dropbox
    """
    
    # Secret names in Secret Manager
    YOUTUBE_SECRET = "youtube-oauth-credentials"
    GDRIVE_SECRET = "gdrive-oauth-credentials"
    DROPBOX_SECRET = "dropbox-oauth-credentials"
    
    # OAuth client credentials (for device auth flow)
    YOUTUBE_CLIENT_SECRET = "youtube-client-credentials"
    GDRIVE_CLIENT_SECRET = "gdrive-client-credentials"
    
    # Google OAuth endpoints
    GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
    GOOGLE_DEVICE_AUTH_URI = "https://oauth2.googleapis.com/device/code"
    
    # Dropbox OAuth endpoints
    DROPBOX_TOKEN_URI = "https://api.dropboxapi.com/oauth2/token"
    
    # Scopes for each service
    YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube"]
    GDRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
    
    def __init__(self):
        self.settings = get_settings()
        self._pending_device_auths: Dict[str, DeviceAuthInfo] = {}
    
    def _get_client_credentials(self, secret_name: str) -> Optional[Dict[str, str]]:
        """
        Load OAuth client credentials from Secret Manager.
        
        The secret should contain:
        {
            "client_id": "...",
            "client_secret": "..."
        }
        """
        try:
            creds_json = self.settings.get_secret(secret_name)
            if not creds_json:
                return None
            
            creds = json.loads(creds_json)
            if creds.get("client_id") and creds.get("client_secret"):
                return creds
            
            logger.warning(f"{secret_name} missing client_id or client_secret")
            return None
            
        except Exception as e:
            logger.error(f"Failed to load {secret_name}: {e}")
            return None
    
    def get_youtube_client_credentials(self) -> Optional[Dict[str, str]]:
        """Get YouTube OAuth client credentials from Secret Manager."""
        return self._get_client_credentials(self.YOUTUBE_CLIENT_SECRET)
    
    def get_gdrive_client_credentials(self) -> Optional[Dict[str, str]]:
        """Get Google Drive OAuth client credentials from Secret Manager."""
        return self._get_client_credentials(self.GDRIVE_CLIENT_SECRET)
    
    # =========================================================================
    # Credential Validation
    # =========================================================================
    
    def check_all_credentials(self) -> Dict[str, CredentialCheckResult]:
        """
        Check validity of all OAuth credentials.
        
        Returns:
            Dictionary mapping service name to check result
        """
        results = {}
        
        results["youtube"] = self.check_youtube_credentials()
        results["gdrive"] = self.check_gdrive_credentials()
        results["dropbox"] = self.check_dropbox_credentials()
        
        return results
    
    def check_youtube_credentials(self) -> CredentialCheckResult:
        """Check if YouTube credentials are valid and can be refreshed."""
        return self._check_google_credentials(
            secret_name=self.YOUTUBE_SECRET,
            service_name="youtube",
            scopes=self.YOUTUBE_SCOPES,
            test_api_call=self._test_youtube_api
        )
    
    def check_gdrive_credentials(self) -> CredentialCheckResult:
        """Check if Google Drive credentials are valid and can be refreshed."""
        return self._check_google_credentials(
            secret_name=self.GDRIVE_SECRET,
            service_name="gdrive",
            scopes=self.GDRIVE_SCOPES,
            test_api_call=self._test_gdrive_api
        )
    
    def check_dropbox_credentials(self) -> CredentialCheckResult:
        """Check if Dropbox credentials are valid."""
        try:
            creds_json = self.settings.get_secret(self.DROPBOX_SECRET)
            
            if not creds_json:
                return CredentialCheckResult(
                    service="dropbox",
                    status=CredentialStatus.NOT_CONFIGURED,
                    message="Dropbox credentials not configured",
                    last_checked=datetime.utcnow()
                )
            
            creds = json.loads(creds_json)
            
            if not creds.get("access_token"):
                return CredentialCheckResult(
                    service="dropbox",
                    status=CredentialStatus.INVALID,
                    message="Dropbox credentials missing access_token",
                    last_checked=datetime.utcnow()
                )
            
            # Test the credentials with a simple API call
            if self._test_dropbox_api(creds):
                return CredentialCheckResult(
                    service="dropbox",
                    status=CredentialStatus.VALID,
                    message="Dropbox credentials are valid",
                    last_checked=datetime.utcnow()
                )
            else:
                # Try to refresh if we have refresh token
                if creds.get("refresh_token") and creds.get("app_key") and creds.get("app_secret"):
                    if self._refresh_dropbox_token(creds):
                        return CredentialCheckResult(
                            service="dropbox",
                            status=CredentialStatus.VALID,
                            message="Dropbox credentials refreshed successfully",
                            last_checked=datetime.utcnow()
                        )
                
                return CredentialCheckResult(
                    service="dropbox",
                    status=CredentialStatus.INVALID,
                    message="Dropbox credentials invalid and refresh failed",
                    last_checked=datetime.utcnow()
                )
                
        except Exception as e:
            logger.error(f"Error checking Dropbox credentials: {e}")
            return CredentialCheckResult(
                service="dropbox",
                status=CredentialStatus.ERROR,
                message=f"Error checking credentials: {str(e)}",
                last_checked=datetime.utcnow()
            )
    
    def _check_google_credentials(
        self,
        secret_name: str,
        service_name: str,
        scopes: list,
        test_api_call: callable
    ) -> CredentialCheckResult:
        """Generic Google OAuth credential check."""
        try:
            creds_json = self.settings.get_secret(secret_name)
            
            if not creds_json:
                return CredentialCheckResult(
                    service=service_name,
                    status=CredentialStatus.NOT_CONFIGURED,
                    message=f"{service_name} credentials not configured",
                    last_checked=datetime.utcnow()
                )
            
            creds_data = json.loads(creds_json)
            
            # Check for required fields
            required = ["refresh_token", "client_id", "client_secret"]
            missing = [f for f in required if not creds_data.get(f)]
            if missing:
                return CredentialCheckResult(
                    service=service_name,
                    status=CredentialStatus.INVALID,
                    message=f"Missing required fields: {missing}",
                    last_checked=datetime.utcnow()
                )
            
            # Create credentials object
            credentials = GoogleCredentials(
                token=creds_data.get("token"),
                refresh_token=creds_data.get("refresh_token"),
                token_uri=creds_data.get("token_uri", self.GOOGLE_TOKEN_URI),
                client_id=creds_data.get("client_id"),
                client_secret=creds_data.get("client_secret"),
                scopes=creds_data.get("scopes", scopes)
            )
            
            # Try to refresh if expired
            if credentials.expired or not credentials.token:
                try:
                    credentials.refresh(GoogleAuthRequest())
                    # Update stored credentials with new token
                    self._update_google_credentials(secret_name, creds_data, credentials)
                except Exception as e:
                    logger.error(f"Failed to refresh {service_name} credentials: {e}")
                    return CredentialCheckResult(
                        service=service_name,
                        status=CredentialStatus.INVALID,
                        message=f"Token refresh failed: {str(e)}",
                        last_checked=datetime.utcnow()
                    )
            
            # Test with API call
            if test_api_call(credentials):
                return CredentialCheckResult(
                    service=service_name,
                    status=CredentialStatus.VALID,
                    message=f"{service_name} credentials are valid",
                    last_checked=datetime.utcnow(),
                    expires_at=credentials.expiry
                )
            else:
                return CredentialCheckResult(
                    service=service_name,
                    status=CredentialStatus.INVALID,
                    message=f"{service_name} API test failed",
                    last_checked=datetime.utcnow()
                )
                
        except json.JSONDecodeError as e:
            return CredentialCheckResult(
                service=service_name,
                status=CredentialStatus.INVALID,
                message=f"Invalid JSON in credentials: {str(e)}",
                last_checked=datetime.utcnow()
            )
        except Exception as e:
            logger.error(f"Error checking {service_name} credentials: {e}")
            return CredentialCheckResult(
                service=service_name,
                status=CredentialStatus.ERROR,
                message=f"Error: {str(e)}",
                last_checked=datetime.utcnow()
            )
    
    def _update_google_credentials(
        self,
        secret_name: str,
        original_data: dict,
        credentials: GoogleCredentials
    ) -> bool:
        """Update stored Google credentials with refreshed token."""
        try:
            from google.cloud import secretmanager
            
            updated_data = original_data.copy()
            updated_data["token"] = credentials.token
            if credentials.expiry:
                updated_data["expiry"] = credentials.expiry.isoformat()
            
            # Add new secret version
            client = secretmanager.SecretManagerServiceClient()
            project_id = self.settings.google_cloud_project
            secret_path = f"projects/{project_id}/secrets/{secret_name}"
            
            client.add_secret_version(
                parent=secret_path,
                payload={"data": json.dumps(updated_data).encode("utf-8")}
            )

            _destroy_old_secret_versions(client, secret_path, keep=5)

            logger.info(f"Updated {secret_name} with refreshed token")
            return True
            
        except Exception as e:
            logger.error(f"Failed to update {secret_name}: {e}")
            return False
    
    # =========================================================================
    # API Test Methods
    # =========================================================================
    
    def _test_youtube_api(self, credentials: GoogleCredentials) -> bool:
        """Test YouTube credentials with a simple API call."""
        try:
            from googleapiclient.discovery import build
            
            youtube = build("youtube", "v3", credentials=credentials)
            # Just get channel info - minimal API call
            request = youtube.channels().list(part="id", mine=True)
            response = request.execute()
            
            return "items" in response
            
        except Exception as e:
            logger.error(f"YouTube API test failed: {e}")
            return False
    
    def _test_gdrive_api(self, credentials: GoogleCredentials) -> bool:
        """Test Google Drive credentials with a simple API call."""
        try:
            from googleapiclient.discovery import build
            
            drive = build("drive", "v3", credentials=credentials)
            # Just get about info - minimal API call
            request = drive.about().get(fields="user")
            response = request.execute()
            
            return "user" in response
            
        except Exception as e:
            logger.error(f"Google Drive API test failed: {e}")
            return False
    
    def _test_dropbox_api(self, creds: dict) -> bool:
        """Test Dropbox credentials with a simple API call."""
        try:
            import dropbox
            
            dbx = dropbox.Dropbox(creds["access_token"])
            # Just get account info - minimal API call
            dbx.users_get_current_account()
            return True
            
        except Exception as e:
            logger.error(f"Dropbox API test failed: {e}")
            return False
    
    def _refresh_dropbox_token(self, creds: dict) -> bool:
        """Try to refresh Dropbox token."""
        try:
            import requests
            
            response = requests.post(
                self.DROPBOX_TOKEN_URI,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": creds["refresh_token"],
                    "client_id": creds["app_key"],
                    "client_secret": creds["app_secret"],
                },
                timeout=30
            )
            
            if response.status_code == 200:
                token_data = response.json()
                creds["access_token"] = token_data["access_token"]
                
                # Update in Secret Manager
                self._update_dropbox_credentials(creds)
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Dropbox token refresh failed: {e}")
            return False
    
    def _update_dropbox_credentials(self, creds: dict) -> bool:
        """Update stored Dropbox credentials."""
        try:
            from google.cloud import secretmanager

            client = secretmanager.SecretManagerServiceClient()
            project_id = self.settings.google_cloud_project
            secret_path = f"projects/{project_id}/secrets/{self.DROPBOX_SECRET}"

            client.add_secret_version(
                parent=secret_path,
                payload={"data": json.dumps(creds).encode("utf-8")}
            )

            _destroy_old_secret_versions(client, secret_path, keep=5)

            logger.info("Updated Dropbox credentials with refreshed token")
            return True

        except Exception as e:
            logger.error(f"Failed to update Dropbox credentials: {e}")
            return False
    
    # =========================================================================
    # Device Authorization Flow
    # =========================================================================
    
    def start_youtube_device_auth(
        self, 
        client_id: Optional[str] = None, 
        client_secret: Optional[str] = None
    ) -> DeviceAuthInfo:
        """
        Start YouTube device authorization flow.
        
        Args:
            client_id: Google OAuth client ID (optional, reads from Secret Manager if not provided)
            client_secret: Google OAuth client secret (optional, reads from Secret Manager if not provided)
            
        Returns:
            DeviceAuthInfo with user code and verification URL
        """
        # Load from Secret Manager if not provided
        if not client_id or not client_secret:
            stored_creds = self.get_youtube_client_credentials()
            if not stored_creds:
                raise Exception(
                    "YouTube client credentials not found. Either pass client_id/client_secret "
                    "or create the 'youtube-client-credentials' secret in Secret Manager."
                )
            client_id = stored_creds["client_id"]
            client_secret = stored_creds["client_secret"]
        
        return self._start_google_device_auth(
            client_id=client_id,
            client_secret=client_secret,
            scopes=self.YOUTUBE_SCOPES,
            service_name="youtube"
        )
    
    def start_gdrive_device_auth(
        self, 
        client_id: Optional[str] = None, 
        client_secret: Optional[str] = None
    ) -> DeviceAuthInfo:
        """
        Start Google Drive device authorization flow.
        
        Args:
            client_id: Google OAuth client ID (optional, reads from Secret Manager if not provided)
            client_secret: Google OAuth client secret (optional, reads from Secret Manager if not provided)
            
        Returns:
            DeviceAuthInfo with user code and verification URL
        """
        # Load from Secret Manager if not provided
        if not client_id or not client_secret:
            stored_creds = self.get_gdrive_client_credentials()
            if not stored_creds:
                raise Exception(
                    "Google Drive client credentials not found. Either pass client_id/client_secret "
                    "or create the 'gdrive-client-credentials' secret in Secret Manager."
                )
            client_id = stored_creds["client_id"]
            client_secret = stored_creds["client_secret"]
        
        return self._start_google_device_auth(
            client_id=client_id,
            client_secret=client_secret,
            scopes=self.GDRIVE_SCOPES,
            service_name="gdrive"
        )
    
    def _start_google_device_auth(
        self,
        client_id: str,
        client_secret: str,
        scopes: list,
        service_name: str
    ) -> DeviceAuthInfo:
        """Start Google device authorization flow."""
        import requests
        
        logger.info(f"[{service_name}] Starting device auth flow with scopes: {scopes}")
        
        response = requests.post(
            self.GOOGLE_DEVICE_AUTH_URI,
            data={
                "client_id": client_id,
                "scope": " ".join(scopes),
            },
            timeout=30
        )
        
        if response.status_code != 200:
            logger.error(f"[{service_name}] Device auth request failed: {response.status_code} - {response.text}")
            raise Exception(f"Device auth request failed: {response.text}")
        
        logger.info(f"[{service_name}] Device auth initiated successfully")
        
        data = response.json()
        
        # Google uses 'verification_uri' but some docs show 'verification_url'
        verification_url = data.get("verification_uri") or data.get("verification_url")
        
        device_info = DeviceAuthInfo(
            device_code=data["device_code"],
            user_code=data["user_code"],
            verification_url=verification_url,
            expires_in=data["expires_in"],
            interval=data.get("interval", 5),
            started_at=datetime.utcnow()
        )
        
        # Store for polling, include client secret for token exchange
        self._pending_device_auths[f"{service_name}:{data['device_code']}"] = {
            "info": device_info,
            "client_id": client_id,
            "client_secret": client_secret,
            "scopes": scopes,
            "service_name": service_name
        }
        
        return device_info
    
    def poll_device_auth(self, service_name: str, device_code: str) -> Tuple[str, Optional[dict]]:
        """
        Poll for device authorization completion.
        
        This method is STATELESS - it fetches client credentials from Secret Manager
        and polls Google directly. This works correctly in serverless environments
        like Cloud Run where in-memory state is not preserved between requests.
        
        Args:
            service_name: "youtube" or "gdrive"
            device_code: The device code from start_*_device_auth
            
        Returns:
            Tuple of (status, token_data)
            status: "pending", "complete", "expired", "error"
            token_data: Token data if complete, None otherwise
        """
        import requests
        
        logger.info(f"[{service_name}] Polling device auth for code: {device_code[:20]}...")
        
        # Get client credentials from Secret Manager (stateless approach)
        if service_name == "youtube":
            client_creds = self.get_youtube_client_credentials()
            scopes = self.YOUTUBE_SCOPES
            secret_name = self.YOUTUBE_SECRET
        elif service_name == "gdrive":
            client_creds = self.get_gdrive_client_credentials()
            scopes = self.GDRIVE_SCOPES
            secret_name = self.GDRIVE_SECRET
        else:
            logger.error(f"[{service_name}] Unknown service")
            return ("error", {"message": f"Unknown service: {service_name}"})
        
        if not client_creds:
            logger.error(f"[{service_name}] Client credentials not found in Secret Manager")
            return ("error", {"message": f"Client credentials not found in Secret Manager for {service_name}"})
        
        client_id = client_creds.get("client_id")
        client_secret = client_creds.get("client_secret")
        
        if not client_id or not client_secret:
            logger.error(f"[{service_name}] Invalid client credentials (missing client_id or client_secret)")
            return ("error", {"message": f"Invalid client credentials for {service_name}"})
        
        logger.info(f"[{service_name}] Got client credentials, polling Google token endpoint...")
        
        # Poll Google token endpoint
        response = requests.post(
            self.GOOGLE_TOKEN_URI,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            timeout=30
        )
        
        data = response.json()
        logger.info(f"[{service_name}] Token endpoint response: status={response.status_code}")
        
        if response.status_code == 200:
            logger.info(f"[{service_name}] Token exchange successful! Got access token and refresh_token={bool(data.get('refresh_token'))}")
            
            # Success! Store the credentials
            token_data = {
                "token": data["access_token"],
                "refresh_token": data.get("refresh_token"),
                "token_uri": self.GOOGLE_TOKEN_URI,
                "client_id": client_id,
                "client_secret": client_secret,
                "scopes": scopes,
            }
            
            # Save to Secret Manager
            logger.info(f"[{service_name}] Saving credentials to secret: {secret_name}")
            saved = self._save_credentials_to_secret(secret_name, token_data)
            if not saved:
                logger.error(f"[{service_name}] Failed to save credentials to Secret Manager!")
                return ("error", {"message": "Token exchange succeeded but failed to save credentials to Secret Manager"})
            
            logger.info(f"[{service_name}] Device auth flow COMPLETE - credentials saved successfully")
            return ("complete", token_data)
        
        elif "error" in data:
            error = data["error"]
            logger.info(f"[{service_name}] Token endpoint returned error: {error}")
            
            if error == "authorization_pending":
                return ("pending", {"message": "Waiting for user to authorize. Please visit the verification URL and enter the code."})
            elif error == "slow_down":
                return ("pending", {"message": "Please wait a few more seconds before polling again."})
            elif error == "expired_token":
                logger.warning(f"[{service_name}] Device code expired")
                return ("expired", {"message": "Device code expired. Please start a new device auth flow."})
            elif error == "access_denied":
                logger.warning(f"[{service_name}] User denied access")
                return ("error", {"message": "User denied access"})
            else:
                logger.error(f"[{service_name}] Unexpected error: {data}")
                return ("error", {"message": data.get("error_description", error)})
        
        logger.error(f"[{service_name}] Unknown response from token endpoint: {data}")
        return ("error", {"message": "Unknown response from token endpoint"})
    
    def _save_credentials_to_secret(self, secret_name: str, token_data: dict) -> bool:
        """Save credentials to Secret Manager, creating the secret if needed."""
        logger.info(f"[SecretManager] Attempting to save credentials to: {secret_name}")
        
        try:
            from google.cloud import secretmanager
            from google.api_core import exceptions as gcp_exceptions
            
            client = secretmanager.SecretManagerServiceClient()
            project_id = self.settings.google_cloud_project
            
            if not project_id:
                logger.error(f"[SecretManager] No GCP project configured!")
                return False
                
            parent = f"projects/{project_id}"
            secret_path = f"{parent}/secrets/{secret_name}"
            
            logger.info(f"[SecretManager] Using project: {project_id}, secret path: {secret_path}")
            
            # Try to add a version to existing secret
            try:
                client.add_secret_version(
                    parent=secret_path,
                    payload={"data": json.dumps(token_data).encode("utf-8")}
                )
                logger.info(f"[SecretManager] SUCCESS - Added new version to existing secret: {secret_name}")
                return True
                
            except gcp_exceptions.NotFound:
                # Secret doesn't exist, create it first
                logger.info(f"[SecretManager] Secret {secret_name} not found, creating new secret...")
                client.create_secret(
                    parent=parent,
                    secret_id=secret_name,
                    secret={"replication": {"automatic": {}}}
                )
                logger.info(f"[SecretManager] Created secret: {secret_name}")
                
                # Now add the version
                client.add_secret_version(
                    parent=secret_path,
                    payload={"data": json.dumps(token_data).encode("utf-8")}
                )
                logger.info(f"[SecretManager] SUCCESS - Created secret and saved credentials to: {secret_name}")
                return True
            
        except Exception as e:
            logger.error(f"[SecretManager] FAILED to save credentials to {secret_name}: {type(e).__name__}: {e}")
            import traceback
            logger.error(f"[SecretManager] Traceback: {traceback.format_exc()}")
            return False
    
    # =========================================================================
    # Alerts
    # =========================================================================
    
    def send_credential_alert(
        self,
        invalid_services: list[CredentialCheckResult],
        discord_webhook_url: Optional[str] = None
    ) -> bool:
        """
        Send alert about invalid credentials.
        
        Args:
            invalid_services: List of services with invalid credentials
            discord_webhook_url: Discord webhook URL for notifications
            
        Returns:
            True if alert was sent successfully
        """
        if not discord_webhook_url:
            logger.warning("No Discord webhook URL configured for alerts")
            return False
        
        try:
            import requests
            
            services_list = "\n".join([
                f"• **{r.service}**: {r.message}"
                for r in invalid_services
            ])
            
            # Get the API base URL for re-auth links
            api_url = self.settings.api_base_url if hasattr(self.settings, 'api_base_url') else "https://your-api-url"
            
            message = {
                "embeds": [{
                    "title": "⚠️ OAuth Credentials Need Attention",
                    "description": f"The following service credentials need re-authorization:\n\n{services_list}",
                    "color": 16744256,  # Orange
                    "fields": [
                        {
                            "name": "Re-authorize",
                            "value": f"Visit `{api_url}/api/auth/status` to start re-authorization flow",
                            "inline": False
                        }
                    ],
                    "footer": {
                        "text": "karaoke-gen backend"
                    },
                    "timestamp": datetime.utcnow().isoformat()
                }]
            }
            
            response = requests.post(discord_webhook_url, json=message, timeout=30)
            return response.status_code in (200, 204)
            
        except Exception as e:
            logger.error(f"Failed to send credential alert: {e}")
            return False


# Singleton instance
_credential_manager: Optional[CredentialManager] = None


def get_credential_manager() -> CredentialManager:
    """Get the singleton credential manager instance."""
    global _credential_manager
    if _credential_manager is None:
        _credential_manager = CredentialManager()
    return _credential_manager
