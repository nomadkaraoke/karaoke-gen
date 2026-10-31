"""
Google Play real-time developer notifications (RTDN) infrastructure module.

Play publishes purchase events for the mobile app (com.nomadkaraoke.mobile) to the
Pub/Sub topic below; RevenueCat pulls them through its own subscription
(RevenueCat-Subscriber-appc2bb515ecd) and calls gen's /webhooks/revenuecat.

The topic and subscription were created by RevenueCat's "Connect to Google" flow
(2026-10-25), so they are NOT managed here. This module only adds the one grant Play
needs: Pub/Sub Publisher on that topic for Google Play's notification service account.
TopicIAMMember is non-authoritative, so other members of the topic are untouched.
"""

from pulumi_gcp import pubsub

from config import PROJECT_ID

PLAY_RTDN_TOPIC = "Play-Store-Notifications"
PLAY_NOTIFICATIONS_SA = "google-play-developer-notifications@system.gserviceaccount.com"


def create_play_rtdn_resources() -> dict:
    """Grant Google Play permission to publish RTDN messages to the topic."""
    publisher = pubsub.TopicIAMMember(
        "play-rtdn-publisher",
        project=PROJECT_ID,
        topic=PLAY_RTDN_TOPIC,
        role="roles/pubsub.publisher",
        member=f"serviceAccount:{PLAY_NOTIFICATIONS_SA}",
    )
    return {"publisher": publisher}
