"""
Unit tests for admin feedback endpoint and feedback email notification.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime

from backend.services.email_service import EmailService


class TestAdminFeedbackEndpoint:
    """Tests for GET /api/admin/feedback endpoint."""

    def _make_feedback_doc(self, email="user@example.com", overall=4, **kwargs):
        """Create a mock Firestore feedback document."""
        doc = Mock()
        doc.id = kwargs.get("id", "test-id-1")
        data = {
            "id": doc.id,
            "user_email": email,
            "created_at": kwargs.get("created_at", datetime(2026, 3, 1, 12, 0, 0)),
            "overall_rating": overall,
            "ease_of_use_rating": kwargs.get("ease_of_use", 4),
            "lyrics_accuracy_rating": kwargs.get("lyrics_accuracy", 3),
            "correction_experience_rating": kwargs.get("correction_experience", 4),
            "what_went_well": kwargs.get("what_went_well", "Great app"),
            "what_could_improve": kwargs.get("what_could_improve", "More songs"),
            "additional_comments": kwargs.get("additional_comments", None),
            "would_recommend": kwargs.get("would_recommend", True),
            "would_use_again": kwargs.get("would_use_again", True),
        }
        doc.to_dict.return_value = data
        return doc

    @patch("backend.api.routes.admin.get_user_service")
    @patch("backend.api.routes.admin.is_test_email")
    def test_list_feedback_returns_items(self, mock_is_test, mock_get_user_svc):
        """Test that feedback items are returned correctly."""
        from backend.api.routes.admin import list_user_feedback

        mock_db = Mock()
        mock_user_svc = Mock()
        mock_user_svc.db = mock_db
        mock_get_user_svc.return_value = mock_user_svc
        mock_is_test.return_value = False

        doc1 = self._make_feedback_doc("alice@example.com", 5, id="id-1")
        doc2 = self._make_feedback_doc("bob@example.com", 3, id="id-2")
        mock_db.collection.return_value.stream.return_value = [doc1, doc2]

        result = list_user_feedback(
            limit=50, offset=0, search=None, exclude_test=False,
            auth_result=("admin@test.com", "admin", 1),
        )

        assert result.total == 2
        assert len(result.items) == 2
        assert result.has_more is False

    @patch("backend.api.routes.admin.get_user_service")
    @patch("backend.api.routes.admin.is_test_email")
    def test_list_feedback_search_filter(self, mock_is_test, mock_get_user_svc):
        """Test that search filters by email."""
        from backend.api.routes.admin import list_user_feedback

        mock_db = Mock()
        mock_user_svc = Mock()
        mock_user_svc.db = mock_db
        mock_get_user_svc.return_value = mock_user_svc
        mock_is_test.return_value = False

        doc1 = self._make_feedback_doc("alice@example.com", 5, id="id-1")
        doc2 = self._make_feedback_doc("bob@example.com", 3, id="id-2")
        mock_db.collection.return_value.stream.return_value = [doc1, doc2]

        result = list_user_feedback(
            limit=50, offset=0, search="alice", exclude_test=False,
            auth_result=("admin@test.com", "admin", 1),
        )

        assert result.total == 1
        assert result.items[0].user_email == "alice@example.com"

    @patch("backend.api.routes.admin.get_user_service")
    @patch("backend.api.routes.admin.is_test_email")
    def test_list_feedback_exclude_test(self, mock_is_test, mock_get_user_svc):
        """Test that test emails are excluded when exclude_test=True."""
        from backend.api.routes.admin import list_user_feedback

        mock_db = Mock()
        mock_user_svc = Mock()
        mock_user_svc.db = mock_db
        mock_get_user_svc.return_value = mock_user_svc

        doc1 = self._make_feedback_doc("alice@example.com", 5, id="id-1")
        doc2 = self._make_feedback_doc("test@inbox.testmail.app", 3, id="id-2")
        mock_db.collection.return_value.stream.return_value = [doc1, doc2]

        # Make is_test_email return True for the test email
        mock_is_test.side_effect = lambda e: "testmail" in e

        result = list_user_feedback(
            limit=50, offset=0, search=None, exclude_test=True,
            auth_result=("admin@test.com", "admin", 1),
        )

        assert result.total == 1
        assert result.items[0].user_email == "alice@example.com"

    @patch("backend.api.routes.admin.get_user_service")
    @patch("backend.api.routes.admin.is_test_email")
    def test_list_feedback_pagination(self, mock_is_test, mock_get_user_svc):
        """Test pagination with offset and limit."""
        from backend.api.routes.admin import list_user_feedback

        mock_db = Mock()
        mock_user_svc = Mock()
        mock_user_svc.db = mock_db
        mock_get_user_svc.return_value = mock_user_svc
        mock_is_test.return_value = False

        docs = [
            self._make_feedback_doc(f"user{i}@example.com", 4, id=f"id-{i}")
            for i in range(5)
        ]
        mock_db.collection.return_value.stream.return_value = docs

        result = list_user_feedback(
            limit=2, offset=2, search=None, exclude_test=False,
            auth_result=("admin@test.com", "admin", 1),
        )

        assert result.total == 5
        assert len(result.items) == 2
        assert result.has_more is True
        assert result.offset == 2
        assert result.limit == 2

    @patch("backend.api.routes.admin.get_user_service")
    @patch("backend.api.routes.admin.is_test_email")
    def test_list_feedback_aggregate_stats(self, mock_is_test, mock_get_user_svc):
        """Test that aggregate rating averages are computed correctly."""
        from backend.api.routes.admin import list_user_feedback

        mock_db = Mock()
        mock_user_svc = Mock()
        mock_user_svc.db = mock_db
        mock_get_user_svc.return_value = mock_user_svc
        mock_is_test.return_value = False

        doc1 = self._make_feedback_doc("a@x.com", 5, id="id-1", ease_of_use=4, lyrics_accuracy=3, correction_experience=2)
        doc2 = self._make_feedback_doc("b@x.com", 3, id="id-2", ease_of_use=2, lyrics_accuracy=5, correction_experience=4)
        mock_db.collection.return_value.stream.return_value = [doc1, doc2]

        result = list_user_feedback(
            limit=50, offset=0, search=None, exclude_test=False,
            auth_result=("admin@test.com", "admin", 1),
        )

        assert result.avg_overall_rating == 4.0
        assert result.avg_ease_of_use_rating == 3.0
        assert result.avg_lyrics_accuracy_rating == 4.0
        assert result.avg_correction_experience_rating == 3.0

    @patch("backend.api.routes.admin.get_user_service")
    @patch("backend.api.routes.admin.is_test_email")
    def test_list_feedback_empty(self, mock_is_test, mock_get_user_svc):
        """Test empty results."""
        from backend.api.routes.admin import list_user_feedback

        mock_db = Mock()
        mock_user_svc = Mock()
        mock_user_svc.db = mock_db
        mock_get_user_svc.return_value = mock_user_svc

        mock_db.collection.return_value.stream.return_value = []

        result = list_user_feedback(
            limit=50, offset=0, search=None, exclude_test=False,
            auth_result=("admin@test.com", "admin", 1),
        )

        assert result.total == 0
        assert len(result.items) == 0
        assert result.avg_overall_rating is None


class TestFeedbackEmailNotification:
    """Tests for send_feedback_notification email method."""

    @patch.object(EmailService, '_get_provider')
    def test_send_feedback_notification_calls_provider(self, mock_get_provider):
        """Test that feedback notification sends email via provider."""
        mock_provider = Mock()
        mock_provider.send_email.return_value = True
        mock_get_provider.return_value = mock_provider

        service = EmailService()
        result = service.send_feedback_notification(
            user_email="alice@example.com",
            overall_rating=5,
            ease_of_use_rating=4,
            lyrics_accuracy_rating=3,
            correction_experience_rating=4,
            what_went_well="Great lyrics sync",
            what_could_improve="More song variety",
            additional_comments=None,
            would_recommend=True,
            would_use_again=True,
        )

        assert result is True
        mock_provider.send_email.assert_called_once()
        call_args = mock_provider.send_email.call_args

        # Check email is sent to admin
        assert call_args.kwargs.get("to_email") or call_args[0][0] == "admin@nomadkaraoke.com"

        # Check subject contains user email and rating
        subject = call_args.kwargs.get("subject") or call_args[0][1]
        assert "alice@example.com" in subject
        assert "5/5" in subject

    @patch.object(EmailService, '_get_provider')
    def test_send_feedback_notification_content(self, mock_get_provider):
        """Test that feedback notification email contains all feedback fields."""
        mock_provider = Mock()
        mock_provider.send_email.return_value = True
        mock_get_provider.return_value = mock_provider

        service = EmailService()
        service.send_feedback_notification(
            user_email="bob@example.com",
            overall_rating=3,
            ease_of_use_rating=2,
            lyrics_accuracy_rating=4,
            correction_experience_rating=5,
            what_went_well="Easy to use",
            what_could_improve="Better mobile support",
            additional_comments="Love it overall",
            would_recommend=False,
            would_use_again=True,
        )

        call_args = mock_provider.send_email.call_args
        html_content = call_args.kwargs.get("html_content") or call_args[0][2]
        text_content = call_args.kwargs.get("text_content") or call_args[0][3]

        # Check HTML contains feedback text
        assert "Easy to use" in html_content
        assert "Better mobile support" in html_content
        assert "Love it overall" in html_content
        assert "bob@example.com" in html_content

        # Check plain text also contains feedback
        assert "Easy to use" in text_content
        assert "Better mobile support" in text_content
        assert "3/5" in text_content

    @patch.object(EmailService, '_get_provider')
    @patch.dict("os.environ", {"ADMIN_NOTIFICATION_EMAIL": "custom@admin.com"})
    def test_send_feedback_notification_custom_admin_email(self, mock_get_provider):
        """Test that admin email can be configured via env var."""
        mock_provider = Mock()
        mock_provider.send_email.return_value = True
        mock_get_provider.return_value = mock_provider

        service = EmailService()
        service.send_feedback_notification(
            user_email="test@example.com",
            overall_rating=4,
            ease_of_use_rating=4,
            lyrics_accuracy_rating=4,
            correction_experience_rating=4,
        )

        call_args = mock_provider.send_email.call_args
        to_email = call_args.kwargs.get("to_email") or call_args[0][0]
        assert to_email == "custom@admin.com"

    @patch.object(EmailService, '_get_provider')
    def test_send_feedback_notification_returns_false_on_failure(self, mock_get_provider):
        """Test that failure is returned when provider fails."""
        mock_provider = Mock()
        mock_provider.send_email.return_value = False
        mock_get_provider.return_value = mock_provider

        service = EmailService()
        result = service.send_feedback_notification(
            user_email="test@example.com",
            overall_rating=1,
            ease_of_use_rating=1,
            lyrics_accuracy_rating=1,
            correction_experience_rating=1,
        )

        assert result is False
