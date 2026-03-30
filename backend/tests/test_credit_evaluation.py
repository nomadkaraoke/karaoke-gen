"""
Tests for AI-powered credit evaluation service.
"""
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

import sys
sys.modules.setdefault('google.cloud.firestore', MagicMock())
sys.modules.setdefault('google.cloud.firestore_v1', MagicMock())

from backend.models.user import User


# =============================================================================
# Prompt Building Tests
# =============================================================================


class TestBuildUserPrompt:

    def test_builds_prompt_with_all_signals(self):
        from backend.services.credit_evaluation_service import _build_user_prompt

        prompt = _build_user_prompt(
            email="test@example.com",
            grant_type="welcome",
            user_data={
                "device_fingerprint": "fp123",
                "signup_ip": "1.2.3.4",
                "credits": 0,
                "total_jobs_created": 0,
                "total_jobs_completed": 0,
                "total_spent": 0,
                "created_at": "2026-03-23",
            },
            fingerprint_matches=[
                {"email": "other@test.com", "total_jobs_created": 5, "total_spent": 0, "created_at": "2026-03-20"},
            ],
            ip_matches=[],
            ip_geo={"status": "success", "country": "US", "country_code": "US", "city": "Portland", "region": "OR", "isp": "Comcast", "org": "Comcast", "as_number": "AS7922", "as_name": "Comcast"},
            recent_signups_ip=1,
            recent_signups_fp=2,
            user_agent="Mozilla/5.0",
        )

        assert "test@example.com" in prompt
        assert "fp123" in prompt
        assert "1.2.3.4" in prompt
        assert "other@test.com" in prompt
        assert "RED FLAG" in prompt
        assert "Portland" in prompt
        assert "Comcast" in prompt
        assert "Mozilla/5.0" in prompt

    def test_builds_prompt_without_correlations(self):
        from backend.services.credit_evaluation_service import _build_user_prompt

        prompt = _build_user_prompt(
            email="clean@user.com",
            grant_type="welcome",
            user_data={"device_fingerprint": "fp999", "signup_ip": "5.6.7.8"},
            fingerprint_matches=[],
            ip_matches=[],
            ip_geo=None,
            recent_signups_ip=1,
            recent_signups_fp=1,
            user_agent=None,
        )

        assert "None found" in prompt
        assert "clean@user.com" in prompt

    def test_includes_feedback_content(self):
        from backend.services.credit_evaluation_service import _build_user_prompt

        prompt = _build_user_prompt(
            email="test@example.com",
            grant_type="feedback",
            user_data={},
            fingerprint_matches=[],
            ip_matches=[],
            ip_geo=None,
            recent_signups_ip=0,
            recent_signups_fp=0,
            user_agent=None,
            feedback_content={
                "overall_rating": 5,
                "what_went_well": "Great audio quality!",
                "what_could_improve": "Faster processing",
                "additional_comments": "Love it",
            },
        )

        assert "Feedback Submitted" in prompt
        assert "Great audio quality!" in prompt
        assert "5" in prompt


# =============================================================================
# Response Parsing Tests
# =============================================================================


class TestParseGeminiResponse:

    def test_parses_grant_decision(self):
        from backend.services.credit_evaluation_service import _parse_gemini_response

        result = _parse_gemini_response('{"decision": "grant", "reasoning": "Clean user", "confidence": 0.95}')
        assert result.decision == "grant"
        assert result.reasoning == "Clean user"
        assert result.confidence == 0.95
        assert result.error is None

    def test_parses_deny_decision(self):
        from backend.services.credit_evaluation_service import _parse_gemini_response

        result = _parse_gemini_response('{"decision": "deny", "reasoning": "Same fingerprint as 3 other accounts", "confidence": 0.9}')
        assert result.decision == "deny"
        assert "fingerprint" in result.reasoning

    def test_handles_markdown_code_fences(self):
        from backend.services.credit_evaluation_service import _parse_gemini_response

        result = _parse_gemini_response('```json\n{"decision": "grant", "reasoning": "OK", "confidence": 0.8}\n```')
        assert result.decision == "grant"

    def test_fails_closed_on_invalid_json(self):
        from backend.services.credit_evaluation_service import _parse_gemini_response

        result = _parse_gemini_response("I think this user is fine")
        assert result.decision == "pending_review"
        assert result.error is not None

    def test_fails_closed_on_unexpected_decision(self):
        from backend.services.credit_evaluation_service import _parse_gemini_response

        result = _parse_gemini_response('{"decision": "maybe", "reasoning": "Unsure", "confidence": 0.5}')
        assert result.decision == "pending_review"  # fail-closed

    def test_fails_closed_on_empty_response(self):
        from backend.services.credit_evaluation_service import _parse_gemini_response

        result = _parse_gemini_response("")
        assert result.decision == "pending_review"
        assert result.error is not None


# =============================================================================
# Evaluation Service Tests
# =============================================================================


class TestCreditEvaluationService:

    @patch('backend.services.credit_evaluation_service.get_settings')
    def test_returns_grant_when_disabled(self, mock_settings):
        mock_settings.return_value = MagicMock(credit_eval_enabled=False)

        from backend.services.credit_evaluation_service import CreditEvaluationService
        service = CreditEvaluationService()
        result = service.evaluate("test@example.com", "welcome")

        assert result.decision == "grant"
        assert "disabled" in result.reasoning

    @patch('backend.services.credit_evaluation_service.get_settings')
    def test_quick_grants_clean_users(self, mock_settings):
        mock_settings.return_value = MagicMock(
            credit_eval_enabled=True,
            credit_eval_model="gemini-3.1-pro-preview",
            google_cloud_project="test",
        )

        from backend.services.credit_evaluation_service import CreditEvaluationService
        service = CreditEvaluationService()

        with patch.object(service, '_collect_signals', return_value={
            "user_data": {"email": "clean@user.com"},
            "fingerprint_matches": [],
            "ip_matches": [],
            "ip_geo": None,
            "recent_signups_ip": 1,
            "recent_signups_fp": 1,
            "user_agent": "Mozilla/5.0",
        }), patch.object(service, '_log_evaluation'):
            result = service.evaluate("clean@user.com", "welcome")

        assert result.decision == "grant"
        assert "clean" in result.reasoning.lower()

    @patch('backend.services.credit_evaluation_service.get_settings')
    def test_calls_gemini_when_correlations_found(self, mock_settings):
        mock_settings.return_value = MagicMock(
            credit_eval_enabled=True,
            credit_eval_model="gemini-3.1-pro-preview",
            google_cloud_project="test",
        )

        from backend.services.credit_evaluation_service import CreditEvaluationService
        service = CreditEvaluationService()

        with patch.object(service, '_collect_signals', return_value={
            "user_data": {"email": "sus@user.com", "device_fingerprint": "fp123", "signup_ip": "1.2.3.4"},
            "fingerprint_matches": [{"email": "other@test.com", "total_jobs_created": 5, "total_spent": 0, "created_at": "2026-03-20"}],
            "ip_matches": [],
            "ip_geo": None,
            "recent_signups_ip": 1,
            "recent_signups_fp": 2,
            "user_agent": None,
        }), patch.object(service, '_call_gemini', return_value='{"decision": "deny", "reasoning": "Same fingerprint", "confidence": 0.9}'), \
             patch.object(service, '_log_evaluation'):
            result = service.evaluate("sus@user.com", "welcome")

        assert result.decision == "deny"

    @patch('backend.services.credit_evaluation_service.get_settings')
    def test_fails_closed_on_gemini_error(self, mock_settings):
        mock_settings.return_value = MagicMock(
            credit_eval_enabled=True,
            credit_eval_model="gemini-3.1-pro-preview",
            google_cloud_project="test",
        )

        from backend.services.credit_evaluation_service import CreditEvaluationService
        service = CreditEvaluationService()

        with patch.object(service, '_collect_signals', side_effect=Exception("Firestore down")), \
             patch.object(service, '_log_evaluation'):
            result = service.evaluate("test@example.com", "welcome")

        assert result.decision == "pending_review"
        assert result.error is not None


# =============================================================================
# Welcome Credit Integration Tests
# =============================================================================


class TestWelcomeCreditsWithEvaluation:

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_grants_credits_when_evaluation_approves(self, mock_fs, mock_settings):
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db

        user = User(email="new@user.com", welcome_credits_granted=False)
        user_doc = MagicMock()
        user_doc.exists = True
        user_doc.to_dict.return_value = user.model_dump(mode='json')
        mock_db.collection.return_value.document.return_value.get.return_value = user_doc

        from backend.services.user_service import UserService
        from backend.services.credit_evaluation_service import CreditEvaluation

        service = UserService()

        with patch('backend.services.credit_evaluation_service.get_credit_evaluation_service') as mock_eval:
            mock_eval.return_value.evaluate.return_value = CreditEvaluation(
                decision="grant", reasoning="Clean user", confidence=0.95
            )
            granted, status = service.grant_welcome_credits_if_eligible("new@user.com")

        assert granted is True
        assert status == "granted"

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_denies_credits_when_evaluation_rejects(self, mock_fs, mock_settings):
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db

        user = User(email="abuser@test.com", welcome_credits_granted=False)
        user_doc = MagicMock()
        user_doc.exists = True
        user_doc.to_dict.return_value = user.model_dump(mode='json')
        mock_db.collection.return_value.document.return_value.get.return_value = user_doc

        from backend.services.user_service import UserService
        from backend.services.credit_evaluation_service import CreditEvaluation

        service = UserService()

        with patch('backend.services.credit_evaluation_service.get_credit_evaluation_service') as mock_eval, \
             patch('backend.services.email_service.get_email_service') as mock_email:
            mock_eval.return_value.evaluate.return_value = CreditEvaluation(
                decision="deny", reasoning="Same fingerprint as 3 accounts", confidence=0.9
            )
            granted, status = service.grant_welcome_credits_if_eligible("abuser@test.com")

        assert granted is False
        assert status == "denied"
        # Verify rejection email was sent
        mock_email.return_value.send_credit_denied_email.assert_called_once_with("abuser@test.com", "welcome")

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_pending_review_when_evaluation_fails(self, mock_fs, mock_settings):
        """Fail-closed: if evaluation crashes, credits are NOT granted, admin notified."""
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db

        user = User(email="new@user.com", welcome_credits_granted=False)
        user_doc = MagicMock()
        user_doc.exists = True
        user_doc.to_dict.return_value = user.model_dump(mode='json')
        mock_db.collection.return_value.document.return_value.get.return_value = user_doc

        from backend.services.user_service import UserService

        service = UserService()

        with patch('backend.services.credit_evaluation_service.get_credit_evaluation_service') as mock_eval, \
             patch('backend.services.email_service.get_email_service') as mock_email:
            mock_eval.return_value.evaluate.side_effect = Exception("Gemini is down")
            granted, status = service.grant_welcome_credits_if_eligible("new@user.com")

        assert granted is False
        assert status == "pending_review"
        # Verify admin review email was sent
        mock_email.return_value.send_credit_review_needed_email.assert_called_once()

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_skips_already_granted(self, mock_fs, mock_settings):
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db

        user = User(email="existing@user.com", welcome_credits_granted=True)
        user_doc = MagicMock()
        user_doc.exists = True
        user_doc.to_dict.return_value = user.model_dump(mode='json')
        mock_db.collection.return_value.document.return_value.get.return_value = user_doc

        from backend.services.user_service import UserService
        service = UserService()
        granted, status = service.grant_welcome_credits_if_eligible("existing@user.com")

        assert granted is False
        assert status == "already_granted"
