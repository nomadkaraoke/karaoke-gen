"""Tests for flyer PDF generation service."""
import os
from unittest.mock import patch, MagicMock

import pytest

from backend.services.flyer_service import FlyerService, FlyerError


class TestFlyerService:
    """Tests for FlyerService."""

    def setup_method(self):
        self.service = FlyerService()

    def test_render_template_light_substitutes_placeholders(self):
        html = self.service._render_template(
            theme="light",
            referral_code="MYCODE",
            discount_percent=15,
            qr_data_url="data:image/png;base64,abc123",
        )
        assert "MYCODE" in html
        assert "{{REFERRAL_CODE}}" not in html
        assert "{{QR_DATA_URL}}" not in html
        assert "{{DISCOUNT_PERCENT}}" not in html
        assert "{{REFERRAL_CODE_LOWER}}" not in html
        assert "15% off" in html
        assert "data:image/png;base64,abc123" in html
        assert "mycode" in html  # lowercase version in alt text

    def test_render_template_dark_substitutes_placeholders(self):
        html = self.service._render_template(
            theme="dark",
            referral_code="DARKCODE",
            discount_percent=20,
            qr_data_url="data:image/png;base64,xyz789",
        )
        assert "DARKCODE" in html
        assert "{{REFERRAL_CODE}}" not in html
        assert "data:image/png;base64,xyz789" in html
        assert "20% off" in html

    def test_render_template_invalid_theme_raises(self):
        with pytest.raises(FlyerError, match="Invalid theme"):
            self.service._render_template(
                theme="neon",
                referral_code="CODE",
                discount_percent=10,
                qr_data_url="data:image/png;base64,abc",
            )

    def test_render_template_uppercases_referral_code(self):
        html = self.service._render_template(
            theme="light",
            referral_code="mycode",
            discount_percent=10,
            qr_data_url="data:image/png;base64,abc",
        )
        assert '<span class="referral-code">MYCODE</span>' in html

    @patch("backend.services.flyer_service.subprocess.run")
    def test_generate_pdf_calls_chromium_with_correct_flags(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        self.service._chromium_path = "/usr/bin/chromium"

        with patch.object(self.service, "_render_template", return_value="<html>test</html>"):
            with patch("os.path.exists", return_value=True):
                with patch("builtins.open", MagicMock(return_value=MagicMock(read=MagicMock(return_value=b"%PDF")))):
                    with patch("os.unlink"):
                        self.service.generate_pdf(
                            theme="light",
                            referral_code="TEST",
                            discount_percent=10,
                            qr_data_url="data:image/png;base64,abc",
                        )

        call_args = mock_run.call_args[0][0]
        assert "--headless" in call_args
        assert "--no-margins" in call_args
        assert "--print-background" in call_args
        assert "--no-pdf-header-footer" in call_args
        assert "--virtual-time-budget=5000" in call_args
        assert "--paper-width=8.5" in call_args
        assert "--paper-height=11" in call_args

    def test_validate_qr_data_url_rejects_non_data_url(self):
        with pytest.raises(FlyerError, match="Invalid QR"):
            self.service._validate_qr_data_url("https://evil.com/qr.png")

    def test_validate_qr_data_url_rejects_too_large(self):
        huge_url = "data:image/png;base64," + "A" * 600_000
        with pytest.raises(FlyerError, match="too large"):
            self.service._validate_qr_data_url(huge_url)

    def test_validate_qr_data_url_accepts_valid(self):
        self.service._validate_qr_data_url("data:image/png;base64,abc123")
