"""
Push Notification Service for Web Push Notifications.

Handles sending push notifications to users' browsers/devices using the Web Push protocol.
Uses pywebpush library for VAPID authentication and push message encryption.
"""
import logging
from datetime import datetime, timezone
from typing import Optional, List

from pywebpush import webpush, WebPushException

from backend.config import get_settings
from backend.models.user import User, PushSubscription


logger = logging.getLogger(__name__)


class SubscriptionGoneError(Exception):
    """Raised when a push subscription is no longer valid (410 Gone)."""
    pass


class PushNotificationService:
    """
    Service for sending Web Push notifications.

    Requires VAPID keys to be configured in Secret Manager:
    - vapid-public-key: Base64-encoded public key
    - vapid-private-key: Base64-encoded private key
    """

    def __init__(self, db=None):
        """
        Initialize the push notification service.

        Args:
            db: Firestore client (optional, will use singleton if not provided)
        """
        self.settings = get_settings()
        self._vapid_private_key: Optional[str] = None
        self._vapid_public_key: Optional[str] = None
        self._db = db

    @property
    def db(self):
        """Get Firestore client (lazy initialization)."""
        if self._db is None:
            from google.cloud import firestore
            self._db = firestore.Client()
        return self._db

    @property
    def vapid_private_key(self) -> Optional[str]:
        """Get VAPID private key from Secret Manager (cached)."""
        if self._vapid_private_key is None:
            self._vapid_private_key = self.settings.get_secret("vapid-private-key")
        return self._vapid_private_key

    @property
    def vapid_public_key(self) -> Optional[str]:
        """Get VAPID public key from Secret Manager (cached)."""
        if self._vapid_public_key is None:
            self._vapid_public_key = self.settings.get_secret("vapid-public-key")
        return self._vapid_public_key

    def is_enabled(self) -> bool:
        """Check if push notifications are enabled and properly configured."""
        return (
            self.settings.enable_push_notifications and
            self.vapid_private_key is not None and
            self.vapid_public_key is not None
        )

    def get_public_key(self) -> Optional[str]:
        """Get the VAPID public key for client-side subscription."""
        if not self.settings.enable_push_notifications:
            return None
        return self.vapid_public_key

    async def send_push(
        self,
        user_email: str,
        title: str,
        body: str,
        url: str = "/app/",
        tag: Optional[str] = None
    ) -> int:
        """
        Send a push notification to all of a user's subscribed devices.

        Args:
            user_email: The user's email address
            title: Notification title
            body: Notification body text
            url: URL to open when notification is clicked
            tag: Optional tag for notification grouping (replaces notifications with same tag)

        Returns:
            Number of notifications successfully sent
        """
        if not self.is_enabled():
            logger.debug("Push notifications not enabled, skipping")
            return 0

        # Get user from Firestore
        user_doc = self.db.collection("gen_users").document(user_email).get()
        if not user_doc.exists:
            logger.warning(f"User {user_email} not found for push notification")
            return 0

        user_data = user_doc.to_dict()
        subscriptions = user_data.get("push_subscriptions", [])

        if not subscriptions:
            logger.debug(f"User {user_email} has no push subscriptions")
            return 0

        # Build notification payload
        import json
        payload = json.dumps({
            "title": title,
            "body": body,
            "url": url,
            "tag": tag or "default",
            "icon": "/nomad-logo.png"
        })

        # Send to each subscription
        success_count = 0
        invalid_subscriptions = []

        for sub in subscriptions:
            try:
                webpush(
                    subscription_info={
                        "endpoint": sub["endpoint"],
                        "keys": sub["keys"]
                    },
                    data=payload,
                    vapid_private_key=self.vapid_private_key,
                    vapid_claims={
                        "sub": self.settings.vapid_subject
                    }
                )
                success_count += 1
                logger.debug(f"Push sent to {sub.get('device_name', 'unknown device')}")

            except WebPushException as e:
                logger.warning(f"Push failed: {e}")
                # Check if subscription is gone (410) or unauthorized (401/403)
                if e.response and e.response.status_code in (404, 410):
                    logger.info(f"Subscription gone, will remove: {sub['endpoint'][:50]}...")
                    invalid_subscriptions.append(sub["endpoint"])
                elif e.response and e.response.status_code in (401, 403):
                    logger.warning(f"Push auth failed for {sub['endpoint'][:50]}...")

            except Exception as e:
                logger.error(f"Unexpected error sending push: {e}")

        # Remove invalid subscriptions
        if invalid_subscriptions:
            await self._remove_invalid_subscriptions(user_email, invalid_subscriptions)

        logger.info(f"Push notifications sent to {success_count}/{len(subscriptions)} devices for {user_email}")
        return success_count

    async def _remove_invalid_subscriptions(self, user_email: str, endpoints: List[str]) -> None:
        """Remove invalid subscriptions from user's list."""
        try:
            user_ref = self.db.collection("gen_users").document(user_email)
            user_doc = user_ref.get()
            if not user_doc.exists:
                return

            user_data = user_doc.to_dict()
            current_subs = user_data.get("push_subscriptions", [])

            # Filter out invalid subscriptions
            valid_subs = [s for s in current_subs if s["endpoint"] not in endpoints]

            if len(valid_subs) < len(current_subs):
                user_ref.update({"push_subscriptions": valid_subs})
                logger.info(f"Removed {len(current_subs) - len(valid_subs)} invalid subscriptions for {user_email}")

        except Exception as e:
            logger.error(f"Failed to remove invalid subscriptions: {e}")

    async def send_blocking_notification(
        self,
        job: dict,
        action_type: str  # "lyrics" or "instrumental"
    ) -> int:
        """
        Send notification when a job enters a blocking state requiring user action.

        Args:
            job: Job dictionary with job_id, user_email, artist, title
            action_type: Type of action needed ("lyrics" or "instrumental")

        Returns:
            Number of notifications sent
        """
        user_email = job.get("user_email")
        if not user_email:
            return 0

        job_id = job.get("job_id", "unknown")
        artist = job.get("artist", "Unknown Artist")
        title = job.get("title", "Unknown Title")

        if action_type == "lyrics":
            notif_title = "Review Lyrics"
            notif_body = f'"{title}" by {artist} needs lyrics review'
            url = f"/review/{job_id}"
            tag = f"lyrics-{job_id}"
        else:  # instrumental
            notif_title = "Select Instrumental"
            notif_body = f'"{title}" by {artist} needs instrumental selection'
            url = f"/instrumental/{job_id}"
            tag = f"instrumental-{job_id}"

        return await self.send_push(
            user_email=user_email,
            title=notif_title,
            body=notif_body,
            url=url,
            tag=tag
        )

    async def send_completion_notification(self, job: dict) -> int:
        """
        Send notification when a job completes successfully.

        Args:
            job: Job dictionary with job_id, user_email, artist, title

        Returns:
            Number of notifications sent
        """
        user_email = job.get("user_email")
        if not user_email:
            return 0

        job_id = job.get("job_id", "unknown")
        artist = job.get("artist", "Unknown Artist")
        title = job.get("title", "Unknown Title")

        return await self.send_push(
            user_email=user_email,
            title="Video Ready!",
            body=f'Your karaoke video for "{title}" by {artist} is ready to download',
            url=f"/app/?job={job_id}",
            tag=f"complete-{job_id}"
        )

    async def add_subscription(
        self,
        user_email: str,
        endpoint: str,
        keys: dict,
        device_name: Optional[str] = None
    ) -> bool:
        """
        Add a push subscription for a user.

        Enforces max subscriptions per user - oldest removed if limit exceeded.

        Args:
            user_email: User's email address
            endpoint: Push service endpoint URL
            keys: Encryption keys (p256dh, auth)
            device_name: Optional device identifier

        Returns:
            True if subscription was added successfully
        """
        try:
            user_ref = self.db.collection("gen_users").document(user_email)
            user_doc = user_ref.get()

            if not user_doc.exists:
                logger.warning(f"User {user_email} not found")
                return False

            user_data = user_doc.to_dict()
            subscriptions = user_data.get("push_subscriptions", [])

            # Check if this endpoint already exists (update it)
            existing_idx = None
            for i, sub in enumerate(subscriptions):
                if sub["endpoint"] == endpoint:
                    existing_idx = i
                    break

            new_sub = {
                "endpoint": endpoint,
                "keys": keys,
                "device_name": device_name,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "last_used_at": None
            }

            if existing_idx is not None:
                # Update existing subscription
                subscriptions[existing_idx] = new_sub
                logger.info(f"Updated existing push subscription for {user_email}")
            else:
                # Add new subscription
                subscriptions.append(new_sub)
                logger.info(f"Added new push subscription for {user_email}")

            # Enforce max subscriptions (remove oldest)
            max_subs = self.settings.max_push_subscriptions_per_user
            if len(subscriptions) > max_subs:
                # Sort by created_at and keep only the newest
                subscriptions.sort(key=lambda s: s.get("created_at", ""), reverse=True)
                removed = subscriptions[max_subs:]
                subscriptions = subscriptions[:max_subs]
                logger.info(f"Removed {len(removed)} old subscriptions for {user_email} (max {max_subs})")

            user_ref.update({"push_subscriptions": subscriptions})
            return True

        except Exception as e:
            logger.error(f"Failed to add subscription for {user_email}: {e}")
            return False

    async def remove_subscription(self, user_email: str, endpoint: str) -> bool:
        """
        Remove a push subscription for a user.

        Args:
            user_email: User's email address
            endpoint: Push service endpoint URL to remove

        Returns:
            True if subscription was removed
        """
        try:
            user_ref = self.db.collection("gen_users").document(user_email)
            user_doc = user_ref.get()

            if not user_doc.exists:
                return False

            user_data = user_doc.to_dict()
            subscriptions = user_data.get("push_subscriptions", [])

            # Filter out the subscription
            new_subs = [s for s in subscriptions if s["endpoint"] != endpoint]

            if len(new_subs) < len(subscriptions):
                user_ref.update({"push_subscriptions": new_subs})
                logger.info(f"Removed push subscription for {user_email}")
                return True

            return False

        except Exception as e:
            logger.error(f"Failed to remove subscription for {user_email}: {e}")
            return False

    async def list_subscriptions(self, user_email: str) -> List[dict]:
        """
        List all push subscriptions for a user.

        Args:
            user_email: User's email address

        Returns:
            List of subscription info (endpoint, device_name, created_at, last_used_at)
        """
        try:
            user_doc = self.db.collection("gen_users").document(user_email).get()

            if not user_doc.exists:
                return []

            user_data = user_doc.to_dict()
            subscriptions = user_data.get("push_subscriptions", [])

            # Return safe subset of subscription data
            return [
                {
                    "endpoint": s["endpoint"],
                    "device_name": s.get("device_name"),
                    "created_at": s.get("created_at"),
                    "last_used_at": s.get("last_used_at")
                }
                for s in subscriptions
            ]

        except Exception as e:
            logger.error(f"Failed to list subscriptions for {user_email}: {e}")
            return []


# Singleton instance
_push_service: Optional[PushNotificationService] = None


def get_push_notification_service(db=None) -> PushNotificationService:
    """Get the push notification service singleton."""
    global _push_service
    if _push_service is None:
        _push_service = PushNotificationService(db)
    return _push_service
