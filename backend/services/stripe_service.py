"""
Stripe service for payment processing.

Handles:
- Creating checkout sessions for credit purchases
- Processing webhook events
- Managing customer records
"""
import logging
import os
from typing import Optional, Dict, Any, Tuple

import stripe

from backend.config import get_settings


logger = logging.getLogger(__name__)


# Credit packages available for purchase
CREDIT_PACKAGES = {
    "1_credit": {
        "credits": 1,
        "price_cents": 500,  # $5.00
        "name": "1 Karaoke Credit",
        "description": "Create 1 professional karaoke video",
    },
    "3_credits": {
        "credits": 3,
        "price_cents": 1200,  # $12.00 (20% discount)
        "name": "3 Karaoke Credits",
        "description": "Create 3 professional karaoke videos (Save 20%)",
    },
    "5_credits": {
        "credits": 5,
        "price_cents": 1750,  # $17.50 (30% discount)
        "name": "5 Karaoke Credits",
        "description": "Create 5 professional karaoke videos (Save 30%)",
    },
    "10_credits": {
        "credits": 10,
        "price_cents": 3000,  # $30.00 (40% discount)
        "name": "10 Karaoke Credits",
        "description": "Create 10 professional karaoke videos (Save 40%)",
    },
}

# Made-for-you service package (not a credit package - creates a job directly)
MADE_FOR_YOU_PACKAGE = {
    "price_cents": 1500,  # $15.00
    "name": "Made For You Karaoke Video",
    "description": "Professional 4K karaoke video with perfectly synced lyrics, delivered to your email within 24 hours",
    "images": ["https://gen.nomadkaraoke.com/nomad-logo.png"],
}


class StripeService:
    """Service for Stripe payment processing."""

    def __init__(self):
        """Initialize Stripe with API key."""
        self.settings = get_settings()
        self.secret_key = os.getenv("STRIPE_SECRET_KEY")
        self.webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")
        self.frontend_url = os.getenv("FRONTEND_URL", "https://gen.nomadkaraoke.com")
        # After consolidation, buy URL is the same as frontend URL
        self.buy_url = os.getenv("BUY_URL", self.frontend_url)
        # Payment method configuration ID from Stripe Dashboard
        # Enables Google Pay, Apple Pay, Link, and other wallet methods
        # Get this from: Dashboard > Settings > Payment methods > [Your Config] > Copy ID
        self.payment_method_config = os.getenv("STRIPE_PAYMENT_METHOD_CONFIG")

        if self.secret_key:
            stripe.api_key = self.secret_key
            logger.info("Stripe initialized with API key")
        else:
            logger.warning("STRIPE_SECRET_KEY not set - payments disabled")

        if self.payment_method_config:
            logger.info(f"Using payment method config: {self.payment_method_config}")
        else:
            logger.info("No STRIPE_PAYMENT_METHOD_CONFIG set - using Stripe defaults")

    def is_configured(self) -> bool:
        """Check if Stripe is properly configured."""
        return bool(self.secret_key)

    def get_credit_packages(self) -> Dict[str, Dict[str, Any]]:
        """Get available credit packages."""
        return CREDIT_PACKAGES

    def create_checkout_session(
        self,
        package_id: str,
        user_email: str,
        success_url: Optional[str] = None,
        cancel_url: Optional[str] = None,
    ) -> Tuple[bool, Optional[str], str]:
        """
        Create a Stripe Checkout session for purchasing credits.

        Args:
            package_id: ID of the credit package to purchase
            user_email: Email of the purchasing user
            success_url: URL to redirect to on success (optional)
            cancel_url: URL to redirect to on cancel (optional)

        Returns:
            (success, checkout_url, message)
        """
        if not self.is_configured():
            return False, None, "Payment processing is not configured"

        package = CREDIT_PACKAGES.get(package_id)
        if not package:
            return False, None, f"Invalid package: {package_id}"

        try:
            # Default URLs
            if not success_url:
                success_url = f"{self.frontend_url}/payment/success?session_id={{CHECKOUT_SESSION_ID}}"
            if not cancel_url:
                cancel_url = f"{self.buy_url}?cancelled=true"

            # Build checkout session params
            session_params = {
                # Omit payment_method_types to auto-enable Apple Pay, Google Pay, Link, etc.
                'line_items': [{
                    'price_data': {
                        'currency': 'usd',
                        'product_data': {
                            'name': package['name'],
                            'description': package['description'],
                            'images': ['https://gen.nomadkaraoke.com/nomad-logo.png'],
                        },
                        'unit_amount': package['price_cents'],
                    },
                    'quantity': 1,
                }],
                'mode': 'payment',
                'success_url': success_url,
                'cancel_url': cancel_url,
                'customer_email': user_email,
                'metadata': {
                    'package_id': package_id,
                    'credits': str(package['credits']),
                    'user_email': user_email,
                },
                # Allow promotion codes
                'allow_promotion_codes': True,
            }

            # Add payment method configuration if set (enables Google Pay, Link, etc.)
            if self.payment_method_config:
                session_params['payment_method_configuration'] = self.payment_method_config

            # Create checkout session
            session = stripe.checkout.Session.create(**session_params)

            logger.info(f"Created checkout session {session.id} for {user_email}, package {package_id}")
            return True, session.url, "Checkout session created"

        except stripe.error.StripeError as e:
            logger.error(f"Stripe error creating checkout session: {e}")
            return False, None, f"Payment error: {str(e)}"
        except Exception as e:
            logger.error(f"Error creating checkout session: {e}")
            return False, None, "Failed to create checkout session"

    def create_made_for_you_checkout_session(
        self,
        customer_email: str,
        artist: str,
        title: str,
        source_type: str = "search",
        youtube_url: Optional[str] = None,
        notes: Optional[str] = None,
        success_url: Optional[str] = None,
        cancel_url: Optional[str] = None,
    ) -> Tuple[bool, Optional[str], str]:
        """
        Create a Stripe Checkout session for a made-for-you order.

        This is for the full-service karaoke video creation where Nomad Karaoke
        handles all the work (lyrics review, instrumental selection, etc.).

        Args:
            customer_email: Customer's email for delivery
            artist: Song artist
            title: Song title
            source_type: Audio source type (search, youtube, upload)
            youtube_url: YouTube URL if source_type is youtube
            notes: Any special requests from customer
            success_url: URL to redirect to on success (optional)
            cancel_url: URL to redirect to on cancel (optional)

        Returns:
            (success, checkout_url, message)
        """
        if not self.is_configured():
            return False, None, "Payment processing is not configured"

        try:
            # Default URLs - redirect to gen app success page
            if not success_url:
                success_url = f"{self.frontend_url}/order/success?session_id={{CHECKOUT_SESSION_ID}}"
            if not cancel_url:
                cancel_url = "https://nomadkaraoke.com/#do-it-for-me"

            # Build metadata for job creation after payment
            metadata = {
                'order_type': 'made_for_you',
                'customer_email': customer_email,
                'artist': artist,
                'title': title,
                'source_type': source_type,
            }
            if youtube_url:
                metadata['youtube_url'] = youtube_url
            if notes:
                # Truncate notes to fit Stripe's 500 char limit per metadata value
                metadata['notes'] = notes[:500] if len(notes) > 500 else notes

            # Build checkout session params
            session_params = {
                # Omit payment_method_types to auto-enable Apple Pay, Google Pay, Link, etc.
                'line_items': [{
                    'price_data': {
                        'currency': 'usd',
                        'product_data': {
                            'name': f"Karaoke Video: {artist} - {title}",
                            'description': MADE_FOR_YOU_PACKAGE['description'],
                            'images': MADE_FOR_YOU_PACKAGE['images'],
                        },
                        'unit_amount': MADE_FOR_YOU_PACKAGE['price_cents'],
                    },
                    'quantity': 1,
                }],
                'mode': 'payment',
                'success_url': success_url,
                'cancel_url': cancel_url,
                'customer_email': customer_email,
                'metadata': metadata,
                'allow_promotion_codes': True,
            }

            # Add payment method configuration if set (enables Google Pay, Link, etc.)
            if self.payment_method_config:
                session_params['payment_method_configuration'] = self.payment_method_config

            # Create checkout session
            session = stripe.checkout.Session.create(**session_params)

            logger.info(
                f"Created made-for-you checkout session {session.id} for {customer_email}, "
                f"song: {artist} - {title}"
            )
            return True, session.url, "Checkout session created"

        except stripe.error.StripeError as e:
            logger.error(f"Stripe error creating made-for-you checkout session: {e}")
            return False, None, f"Payment error: {str(e)}"
        except Exception as e:
            logger.error(f"Error creating made-for-you checkout session: {e}")
            return False, None, "Failed to create checkout session"

    def verify_webhook_signature(self, payload: bytes, signature: str) -> Tuple[bool, Optional[Dict], str]:
        """
        Verify a Stripe webhook signature.

        Args:
            payload: Raw request body
            signature: Stripe-Signature header value

        Returns:
            (valid, event_data, message)
        """
        if not self.webhook_secret:
            logger.error("STRIPE_WEBHOOK_SECRET not configured")
            return False, None, "Webhook secret not configured"

        try:
            event = stripe.Webhook.construct_event(
                payload, signature, self.webhook_secret
            )
            return True, event, "Webhook verified"
        except stripe.error.SignatureVerificationError as e:
            logger.error(f"Invalid webhook signature: {e}")
            return False, None, "Invalid signature"
        except Exception as e:
            logger.error(f"Error verifying webhook: {e}")
            return False, None, str(e)

    def handle_checkout_completed(self, session: Dict) -> Tuple[bool, Optional[str], int, str]:
        """
        Handle a completed checkout session.

        Args:
            session: Stripe checkout session object

        Returns:
            (success, user_email, credits_to_add, message)
        """
        try:
            metadata = session.get('metadata', {})
            user_email = metadata.get('user_email') or session.get('customer_email')
            try:
                credits = int(metadata.get('credits', 0))
            except (ValueError, TypeError):
                logger.error(f"Invalid credits metadata in session {session.get('id')}: {metadata.get('credits')}")
                return False, user_email, 0, "Invalid credit amount in session metadata"
            package_id = metadata.get('package_id')

            if not user_email:
                logger.error(f"No user email in checkout session {session.get('id')}")
                return False, None, 0, "No user email found"

            if credits <= 0:
                logger.error(f"Invalid credits in checkout session {session.get('id')}")
                return False, user_email, 0, "Invalid credit amount"

            logger.info(
                f"Checkout completed: {user_email} purchased {credits} credits "
                f"(package: {package_id}, session: {session.get('id')})"
            )

            return True, user_email, credits, f"Successfully purchased {credits} credits"

        except Exception as e:
            logger.error(f"Error handling checkout completed: {e}")
            return False, None, 0, str(e)

    def get_session(self, session_id: str) -> Optional[Dict]:
        """
        Get a checkout session by ID.

        Args:
            session_id: Stripe checkout session ID

        Returns:
            Session data or None
        """
        if not self.is_configured():
            return None

        try:
            session = stripe.checkout.Session.retrieve(session_id)
            return dict(session)
        except Exception as e:
            logger.error(f"Error retrieving session {session_id}: {e}")
            return None

    def create_customer(self, email: str, name: Optional[str] = None) -> Optional[str]:
        """
        Create or get a Stripe customer for a user.

        Args:
            email: User's email
            name: User's display name (optional)

        Returns:
            Stripe customer ID or None
        """
        if not self.is_configured():
            return None

        try:
            # Check if customer already exists
            customers = stripe.Customer.list(email=email, limit=1)
            if customers.data:
                return customers.data[0].id

            # Create new customer
            customer = stripe.Customer.create(
                email=email,
                name=name,
                metadata={'source': 'nomad_karaoke'},
            )
            logger.info(f"Created Stripe customer {customer.id} for {email}")
            return customer.id

        except Exception as e:
            logger.error(f"Error creating Stripe customer: {e}")
            return None


# Global instance
_stripe_service = None


def get_stripe_service() -> StripeService:
    """Get the global Stripe service instance."""
    global _stripe_service
    if _stripe_service is None:
        _stripe_service = StripeService()
    return _stripe_service
