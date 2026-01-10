"""
Tests for StripeService.

Tests cover:
- Made-for-you checkout session URL generation
- Credit purchase checkout session URL generation
- Package validation
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import os


class TestStripeServiceUrls:
    """Tests for checkout session URL generation."""

    @pytest.fixture
    def stripe_service(self):
        """Create a StripeService instance with mocked Stripe."""
        # Set required env vars
        with patch.dict(os.environ, {
            'STRIPE_SECRET_KEY': 'sk_test_fake',
            'FRONTEND_URL': 'https://gen.nomadkaraoke.com',
        }):
            # Import here to pick up env vars
            from backend.services.stripe_service import StripeService
            service = StripeService()
            return service

    def test_made_for_you_success_url_uses_frontend_url(self, stripe_service):
        """Test that made-for-you checkout uses frontend_url, not hardcoded domain."""
        # Mock stripe.checkout.Session.create to capture the params
        with patch('stripe.checkout.Session.create') as mock_create:
            mock_session = MagicMock()
            mock_session.id = 'cs_test_123'
            mock_session.url = 'https://checkout.stripe.com/test'
            mock_create.return_value = mock_session

            success, url, message = stripe_service.create_made_for_you_checkout_session(
                customer_email='customer@example.com',
                artist='Test Artist',
                title='Test Song',
            )

            # Verify the call was made
            assert mock_create.called
            call_kwargs = mock_create.call_args[1]

            # Verify success_url uses frontend_url and correct path
            success_url = call_kwargs['success_url']
            assert success_url.startswith('https://gen.nomadkaraoke.com')
            assert '/order/success' in success_url
            assert 'session_id=' in success_url

    def test_made_for_you_success_url_not_hardcoded_to_marketing_site(self, stripe_service):
        """Test that success_url is NOT the old hardcoded marketing site URL."""
        with patch('stripe.checkout.Session.create') as mock_create:
            mock_session = MagicMock()
            mock_session.id = 'cs_test_123'
            mock_session.url = 'https://checkout.stripe.com/test'
            mock_create.return_value = mock_session

            stripe_service.create_made_for_you_checkout_session(
                customer_email='customer@example.com',
                artist='Test Artist',
                title='Test Song',
            )

            call_kwargs = mock_create.call_args[1]
            success_url = call_kwargs['success_url']

            # The bug was: success_url = "https://nomadkaraoke.com/order/success/"
            # (note: nomadkaraoke.com WITHOUT the 'gen.' prefix - that's the marketing site)
            # This should NOT be the case anymore - should use gen.nomadkaraoke.com
            assert not success_url.startswith('https://nomadkaraoke.com/')
            # Should use the frontend URL (gen.nomadkaraoke.com)
            assert success_url.startswith('https://gen.nomadkaraoke.com')

    def test_made_for_you_respects_custom_frontend_url(self):
        """Test that made-for-you checkout uses custom FRONTEND_URL if set."""
        with patch.dict(os.environ, {
            'STRIPE_SECRET_KEY': 'sk_test_fake',
            'FRONTEND_URL': 'https://custom.example.com',
        }):
            from backend.services.stripe_service import StripeService
            service = StripeService()

            with patch('stripe.checkout.Session.create') as mock_create:
                mock_session = MagicMock()
                mock_session.id = 'cs_test_123'
                mock_session.url = 'https://checkout.stripe.com/test'
                mock_create.return_value = mock_session

                service.create_made_for_you_checkout_session(
                    customer_email='customer@example.com',
                    artist='Test Artist',
                    title='Test Song',
                )

                call_kwargs = mock_create.call_args[1]
                success_url = call_kwargs['success_url']
                assert success_url.startswith('https://custom.example.com')

    def test_credit_purchase_success_url_uses_payment_success(self, stripe_service):
        """Test that credit purchase checkout uses /payment/success path."""
        with patch('stripe.checkout.Session.create') as mock_create:
            mock_session = MagicMock()
            mock_session.id = 'cs_test_123'
            mock_session.url = 'https://checkout.stripe.com/test'
            mock_create.return_value = mock_session

            success, url, message = stripe_service.create_checkout_session(
                package_id='1_credit',
                user_email='user@example.com',
            )

            call_kwargs = mock_create.call_args[1]
            success_url = call_kwargs['success_url']

            # Credit purchases go to /payment/success
            assert '/payment/success' in success_url
            assert success_url.startswith('https://gen.nomadkaraoke.com')

    def test_made_for_you_uses_order_success_path(self, stripe_service):
        """Test that made-for-you uses /order/success path (not /payment/success)."""
        with patch('stripe.checkout.Session.create') as mock_create:
            mock_session = MagicMock()
            mock_session.id = 'cs_test_123'
            mock_session.url = 'https://checkout.stripe.com/test'
            mock_create.return_value = mock_session

            stripe_service.create_made_for_you_checkout_session(
                customer_email='customer@example.com',
                artist='Test Artist',
                title='Test Song',
            )

            call_kwargs = mock_create.call_args[1]
            success_url = call_kwargs['success_url']

            # Made-for-you orders go to /order/success (different messaging)
            assert '/order/success' in success_url
            # Should NOT go to /payment/success
            assert '/payment/success' not in success_url


class TestStripeServiceMetadata:
    """Tests for checkout session metadata."""

    @pytest.fixture
    def stripe_service(self):
        """Create a StripeService instance with mocked Stripe."""
        with patch.dict(os.environ, {
            'STRIPE_SECRET_KEY': 'sk_test_fake',
            'FRONTEND_URL': 'https://gen.nomadkaraoke.com',
        }):
            from backend.services.stripe_service import StripeService
            return StripeService()

    def test_made_for_you_includes_order_type_metadata(self, stripe_service):
        """Test that made-for-you checkout includes order_type in metadata."""
        with patch('stripe.checkout.Session.create') as mock_create:
            mock_session = MagicMock()
            mock_session.id = 'cs_test_123'
            mock_session.url = 'https://checkout.stripe.com/test'
            mock_create.return_value = mock_session

            stripe_service.create_made_for_you_checkout_session(
                customer_email='customer@example.com',
                artist='Test Artist',
                title='Test Song',
                notes='Special request',
            )

            call_kwargs = mock_create.call_args[1]
            metadata = call_kwargs['metadata']

            assert metadata['order_type'] == 'made_for_you'
            assert metadata['customer_email'] == 'customer@example.com'
            assert metadata['artist'] == 'Test Artist'
            assert metadata['title'] == 'Test Song'
            assert metadata['notes'] == 'Special request'

    def test_made_for_you_truncates_long_notes(self, stripe_service):
        """Test that notes longer than 500 chars are truncated."""
        with patch('stripe.checkout.Session.create') as mock_create:
            mock_session = MagicMock()
            mock_session.id = 'cs_test_123'
            mock_session.url = 'https://checkout.stripe.com/test'
            mock_create.return_value = mock_session

            long_notes = 'x' * 600

            stripe_service.create_made_for_you_checkout_session(
                customer_email='customer@example.com',
                artist='Test Artist',
                title='Test Song',
                notes=long_notes,
            )

            call_kwargs = mock_create.call_args[1]
            metadata = call_kwargs['metadata']

            assert len(metadata['notes']) == 500
