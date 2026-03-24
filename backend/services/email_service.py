"""
Email service for sending transactional emails.

Supports multiple providers:
- SendGrid (recommended for production)
- Console logging (for development/testing)

Future providers can be added:
- Mailgun
- AWS SES
- Postmark
"""
import html
import logging
import os
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Optional, List
from zoneinfo import ZoneInfo

from backend.config import get_settings
from karaoke_gen.utils import sanitize_filename


logger = logging.getLogger(__name__)


class EmailProvider(ABC):
    """Abstract base class for email providers."""

    @abstractmethod
    def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None,
        cc_emails: Optional[List[str]] = None,
        bcc_emails: Optional[List[str]] = None,
        from_email_override: Optional[str] = None,
    ) -> bool:
        """
        Send an email. Returns True if successful.

        Args:
            to_email: Recipient email address
            subject: Email subject
            html_content: HTML content
            text_content: Plain text content (optional)
            cc_emails: CC recipients (optional)
            bcc_emails: BCC recipients (optional)
            from_email_override: Override the default sender email (optional, for multi-tenant)
        """
        pass


class ConsoleEmailProvider(EmailProvider):
    """
    Development email provider that logs to console.

    Useful for local development and testing.
    """

    def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None,
        cc_emails: Optional[List[str]] = None,
        bcc_emails: Optional[List[str]] = None,
        from_email_override: Optional[str] = None,
    ) -> bool:
        logger.info("=" * 60)
        if from_email_override:
            logger.info(f"FROM: {from_email_override}")
        logger.info(f"EMAIL TO: {to_email}")
        if cc_emails:
            logger.info(f"CC: {', '.join(cc_emails)}")
        if bcc_emails:
            logger.info(f"BCC: {', '.join(bcc_emails)}")
        logger.info(f"SUBJECT: {subject}")
        logger.info("-" * 60)
        logger.info(text_content or html_content)
        logger.info("=" * 60)
        return True


class PreviewEmailProvider(EmailProvider):
    """
    Email provider that captures HTML content for previewing.

    Instead of sending emails, stores the HTML content for later retrieval.
    Useful for generating email previews.
    """

    def __init__(self):
        self.last_html: Optional[str] = None
        self.last_subject: Optional[str] = None
        self.last_to_email: Optional[str] = None

    def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None,
        cc_emails: Optional[List[str]] = None,
        bcc_emails: Optional[List[str]] = None,
        from_email_override: Optional[str] = None,
    ) -> bool:
        self.last_to_email = to_email
        self.last_subject = subject
        self.last_html = html_content
        return True

    def get_last_html(self) -> Optional[str]:
        """Get the HTML content from the last 'sent' email."""
        return self.last_html


class SendGridEmailProvider(EmailProvider):
    """
    SendGrid email provider for production.

    Requires SENDGRID_API_KEY environment variable.
    """

    def __init__(self, api_key: str, from_email: str, from_name: str = "Nomad Karaoke"):
        self.api_key = api_key
        self.from_email = from_email
        self.from_name = from_name

    def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None,
        cc_emails: Optional[List[str]] = None,
        bcc_emails: Optional[List[str]] = None,
        from_email_override: Optional[str] = None,
    ) -> bool:
        try:
            # Import here to avoid requiring sendgrid in all environments
            from sendgrid import SendGridAPIClient
            from sendgrid.helpers.mail import Mail, Email, To, Content, Cc, Bcc

            sg = SendGridAPIClient(api_key=self.api_key)

            # Use override sender if provided (for multi-tenant)
            sender_email = from_email_override or self.from_email

            message = Mail(
                from_email=Email(sender_email, self.from_name),
                to_emails=To(to_email),
                subject=subject,
                html_content=Content("text/html", html_content)
            )

            # Add CC recipients if provided (deduplicated and normalized)
            if cc_emails:
                # Normalize emails (lowercase, strip whitespace) and deduplicate
                seen = set()
                unique_cc_emails = []
                for cc_email in cc_emails:
                    normalized = cc_email.strip().lower()
                    if normalized and normalized not in seen:
                        seen.add(normalized)
                        unique_cc_emails.append(cc_email.strip())

                for cc_email in unique_cc_emails:
                    message.add_cc(Cc(cc_email))

            # Add BCC recipients if provided (deduplicated and normalized)
            if bcc_emails:
                seen = set()
                unique_bcc_emails = []
                for bcc_email in bcc_emails:
                    normalized = bcc_email.strip().lower()
                    if normalized and normalized not in seen:
                        seen.add(normalized)
                        unique_bcc_emails.append(bcc_email.strip())

                for bcc_email in unique_bcc_emails:
                    message.add_bcc(Bcc(bcc_email))

            if text_content:
                message.add_content(Content("text/plain", text_content))

            response = sg.send(message)

            if response.status_code >= 200 and response.status_code < 300:
                cc_info = f" (CC: {', '.join(cc_emails)})" if cc_emails else ""
                bcc_info = f" (BCC: {', '.join(bcc_emails)})" if bcc_emails else ""
                logger.info(f"Email sent to {to_email}{cc_info}{bcc_info} via SendGrid")
                return True
            else:
                logger.error(f"SendGrid returned status {response.status_code}")
                return False

        except Exception:
            logger.exception("Failed to send email via SendGrid")
            return False


class EmailService:
    """
    High-level email service for sending transactional emails.

    Automatically selects the appropriate provider based on configuration.
    """

    # Brand colors and assets for consistent email styling
    BRAND_PRIMARY = "#ff7acc"  # Pink
    BRAND_PRIMARY_HOVER = "#e066b3"  # Darker pink for hover states
    BRAND_SECONDARY = "#ffdf6b"  # Yellow (accent)
    BRAND_SUCCESS = "#22c55e"  # Green for success states (Tailwind green-500)
    LOGO_URL = "https://beveradb.github.io/public-images/Nomad-Karaoke-Logo-small-indexed-websafe-rectangle.gif"

    def __init__(self):
        self.settings = get_settings()
        self.provider = self._get_provider()
        self.frontend_url = os.getenv("FRONTEND_URL", "https://gen.nomadkaraoke.com")
        # After consolidation, buy URL is the same as frontend URL
        self.buy_url = os.getenv("BUY_URL", self.frontend_url)

    def _get_provider(self) -> EmailProvider:
        """Get the configured email provider."""
        sendgrid_api_key = os.getenv("SENDGRID_API_KEY")
        from_email = os.getenv("EMAIL_FROM", "gen@nomadkaraoke.com")
        from_name = os.getenv("EMAIL_FROM_NAME", "Nomad Karaoke")

        if sendgrid_api_key:
            logger.info("Using SendGrid email provider")
            return SendGridEmailProvider(sendgrid_api_key, from_email, from_name)
        else:
            logger.warning("No email provider configured, using console logging")
            return ConsoleEmailProvider()

    def is_configured(self) -> bool:
        """Check if a real email provider is configured (not just console logging)."""
        return isinstance(self.provider, SendGridEmailProvider)

    def send_magic_link(
        self,
        email: str,
        token: str,
        sender_email: Optional[str] = None,
        tenant_frontend_url: Optional[str] = None,
        tenant_name: Optional[str] = None,
    ) -> bool:
        """
        Send a magic link email for authentication.

        Args:
            email: User's email address
            token: Magic link token
            sender_email: Override sender email address (for multi-tenant)
            tenant_frontend_url: Override frontend URL for tenant portals (e.g., https://vocalstar.nomadkaraoke.com)
            tenant_name: Override brand name for tenant portals (e.g., "Vocal Star")

        Returns:
            True if email was sent successfully
        """
        base_url = tenant_frontend_url or self.frontend_url
        magic_link_url = f"{base_url}/auth/verify?token={token}"

        brand_name = tenant_name or "Nomad Karaoke"
        subject = f"Sign in to {brand_name}"

        extra_styles = """
        .warning {
            background-color: #fef3c7;
            border: 1px solid #fcd34d;
            border-radius: 4px;
            padding: 12px;
            margin: 20px 0;
            font-size: 14px;
        }
"""

        content = f"""
    <p>Hi there,</p>

    <p>Click the button below to sign in to {html.escape(brand_name)}:</p>

    <p style="text-align: center;">
        <a href="{magic_link_url}" class="button">Sign In</a>
    </p>

    <div class="warning">
        ⏰ This link expires in 15 minutes and can only be used once.
    </div>

    <p>If the button doesn't work, copy and paste this link into your browser:</p>
    <p style="word-break: break-all; font-size: 14px; color: #666;">
        {magic_link_url}
    </p>

    <p>If you didn't request this email, you can safely ignore it.</p>
"""

        html_content = self._build_email_html(content, extra_styles)

        text_content = f"""
Sign in to {brand_name}
========================

Click this link to sign in:
{magic_link_url}

This link expires in 15 minutes and can only be used once.

If you didn't request this email, you can safely ignore it.

---
© {self._get_year()} {brand_name}
"""

        return self.provider.send_email(
            email, subject, html_content, text_content, from_email_override=sender_email
        )

    def send_credits_added(self, email: str, credits: int, total_credits: int) -> bool:
        """
        Send notification when credits are added to account.

        Args:
            email: User's email address
            credits: Number of credits added
            total_credits: New total credit balance

        Returns:
            True if email was sent successfully
        """
        subject = f"🎉 {credits} credits added to your Nomad Karaoke account"

        extra_styles = f"""
        .credits-box {{
            background-color: #ecfdf5;
            border: 2px solid {self.BRAND_SUCCESS};
            border-radius: 12px;
            padding: 24px;
            text-align: center;
            margin: 20px 0;
        }}
        .credits-number {{
            font-size: 48px;
            font-weight: bold;
            color: {self.BRAND_SUCCESS};
        }}
"""

        content = f"""
    <p>Great news!</p>

    <p><strong>{credits} credits</strong> have been added to your account.</p>

    <div class="credits-box">
        <div>Your balance:</div>
        <div class="credits-number">{total_credits}</div>
        <div>credits</div>
    </div>

    <p>Each credit lets you create one professional karaoke video with:</p>
    <ul>
        <li>AI-powered vocal/instrumental separation</li>
        <li>Synchronized lyrics with word-level timing</li>
        <li>4K video output</li>
        <li>YouTube upload</li>
    </ul>

    <p style="text-align: center;">
        <a href="{self.frontend_url}" class="button">Create Karaoke Now</a>
    </p>
"""

        html_content = self._build_email_html(content, extra_styles)

        text_content = f"""
{credits} credits added to your Nomad Karaoke account!

Your new balance: {total_credits} credits

Each credit lets you create one professional karaoke video.

Start creating: {self.frontend_url}

---
© {self._get_year()} Nomad Karaoke
"""

        return self.provider.send_email(email, subject, html_content, text_content)

    def send_welcome_email(self, email: str, credits: int = 0) -> bool:
        """
        Send welcome email to new users.

        Args:
            email: User's email address
            credits: Initial credit balance (if any)

        Returns:
            True if email was sent successfully
        """
        subject = "Welcome to Nomad Karaoke! 🎤"

        extra_styles = """
        .credits-box {
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            border: 2px solid #e2b714;
            border-radius: 12px;
            padding: 24px;
            text-align: center;
            margin: 24px 0;
        }
        .credits-box .credits-number {
            font-size: 48px;
            font-weight: bold;
            color: #e2b714;
            line-height: 1;
        }
        .credits-box .credits-label {
            font-size: 16px;
            color: #ccc;
            margin-top: 4px;
        }
        .credits-box .credits-note {
            font-size: 13px;
            color: #999;
            margin-top: 12px;
        }
        .feature {
            display: flex;
            align-items: flex-start;
            margin: 16px 0;
        }
        .feature-icon {
            font-size: 24px;
            margin-right: 12px;
        }
"""

        credits_box = ""
        if credits > 0:
            credits_box = f"""
    <div class="credits-box">
        <div class="credits-number">{credits}</div>
        <div class="credits-label">Free Credits</div>
        <div class="credits-note">Each credit creates one professional karaoke video</div>
    </div>
"""

        content = f"""
    <p>Welcome to Nomad Karaoke!</p>

    <p>Turn any song into a professional karaoke video in minutes.</p>

    {credits_box}

    <p><strong>Here's how it works:</strong></p>

    <div class="feature">
        <span class="feature-icon">🎵</span>
        <div>
            <strong>1. Search for a song</strong><br>
            Enter the artist and title, and we'll find high-quality audio.
        </div>
    </div>

    <div class="feature">
        <span class="feature-icon">✨</span>
        <div>
            <strong>2. Our system works its magic</strong><br>
            We separate vocals, transcribe lyrics, and sync everything perfectly.
        </div>
    </div>

    <div class="feature">
        <span class="feature-icon">✏️</span>
        <div>
            <strong>3. Review & customize</strong><br>
            Fine-tune the lyrics if needed, choose your instrumental.
        </div>
    </div>

    <div class="feature">
        <span class="feature-icon">🎬</span>
        <div>
            <strong>4. Get your video</strong><br>
            Download your 4K karaoke video or upload directly to YouTube.
        </div>
    </div>

    <p style="text-align: center; font-size: 14px; color: #999; margin-top: 20px;">
        Complete 2 videos and earn 2 more free credits by sharing your feedback!
    </p>

    <p style="text-align: center;">
        <a href="{self.frontend_url}" class="button">Get Started</a>
    </p>
"""

        html_content = self._build_email_html(content, extra_styles)

        credits_text = f"\nYou have {credits} free credits to get started!\nEach credit creates one professional karaoke video.\n" if credits > 0 else ""

        text_content = f"""
Welcome to Nomad Karaoke!

Turn any song into a professional karaoke video in minutes.
{credits_text}
Here's how it works:

1. Search for a song
   Enter the artist and title, and we'll find high-quality audio.

2. Our system works its magic
   We separate vocals, transcribe lyrics, and sync everything perfectly.

3. Review & customize
   Fine-tune the lyrics if needed, choose your instrumental.

4. Get your video
   Download your 4K karaoke video or upload directly to YouTube.

Complete 2 videos and earn 2 more free credits by sharing your feedback!

Get started: {self.frontend_url}

---
© {self._get_year()} Nomad Karaoke
"""

        return self.provider.send_email(email, subject, html_content, text_content)

    def _get_year(self) -> int:
        """Get current year for copyright notices."""
        from datetime import datetime
        return datetime.now().year

    def _get_email_header(self, extra_header_content: str = "") -> str:
        """Get the standard email header with logo.

        Args:
            extra_header_content: Optional extra content to add after the logo (e.g., beta badge)
        """
        return f"""
    <div class="header">
        <a href="https://nomadkaraoke.com"><img src="{self.LOGO_URL}" alt="Nomad Karaoke" /></a>
        {extra_header_content}
    </div>
"""

    def _get_email_footer(self) -> str:
        """Get the standard email footer with support message and signature."""
        return f"""
    <p style="margin-top: 30px; font-style: italic; color: #666;">If anything isn't perfect, just reply to this email and I'll fix it!</p>

    <div class="signature">
        {self._get_email_signature()}
    </div>
"""

    def _get_base_styles(self) -> str:
        """Get base CSS styles shared by all emails."""
        return f"""
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 600px;
            margin: 0 auto;
            padding: 20px;
        }}
        .header {{
            text-align: center;
            padding: 20px 0;
        }}
        .header img {{
            max-width: 180px;
            height: auto;
            border-radius: 10px;
        }}
        .button {{
            display: inline-block;
            background-color: {self.BRAND_PRIMARY};
            color: white;
            padding: 14px 28px;
            text-decoration: none;
            border-radius: 8px;
            font-weight: 600;
            margin: 20px 0;
        }}
        .signature {{
            margin-top: 30px;
            padding-top: 20px;
        }}
"""

    def _build_email_html(self, content: str, extra_styles: str = "", extra_header_content: str = "") -> str:
        """Build a complete HTML email with standard header, footer, and styles.

        Args:
            content: The main body content of the email
            extra_styles: Additional CSS styles specific to this email type
            extra_header_content: Optional extra content to add in header (e.g., beta badge)

        Returns:
            Complete HTML email string
        """
        return f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        {self._get_base_styles()}
        {extra_styles}
    </style>
</head>
<body>
    {self._get_email_header(extra_header_content)}

    {content}

    {self._get_email_footer()}
</body>
</html>
"""

    def _get_email_signature(self) -> str:
        """Get the HTML email signature for Nomad Karaoke."""
        return """
<table cellpadding="0" cellspacing="0" border="0" style="font-size: medium; font-family: Trebuchet MS;">
    <tbody>
        <tr>
            <td>
                <table cellpadding="0" cellspacing="0" border="0" style="font-size: medium; font-family: Trebuchet MS;">
                    <tbody>
                        <tr>
                            <td style="vertical-align: top;">
                                <table cellpadding="0" cellspacing="0" border="0"
                                    style="font-size: medium; font-family: Trebuchet MS;">
                                    <tbody>
                                        <tr>
                                            <td style="text-align: center;"><a
                                                    href="https://www.linkedin.com/in/andrewbeveridge"
                                                    target="_blank"><img
                                                        src="https://beveradb.github.io/public-images/andrew-buildspace-circle-150px.png"
                                                        role="presentation" style="display: block; max-width: 128px;"
                                                        width="130"></a></td>
                                        </tr>
                                        <tr>
                                            <td height="5"></td>
                                        </tr>
                                        <tr>
                                            <td style="text-align: center;"><a href="https://nomadkaraoke.com"
                                                    target="_blank"><img role="presentation" width="130"
                                                        style="display: block; max-width: 130px; border-radius: 7px;"
                                                        src="https://beveradb.github.io/public-images/Nomad-Karaoke-Logo-small-indexed-websafe-rectangle.gif"></a>
                                            </td>
                                        </tr>
                                    </tbody>
                                </table>
                            </td>
                            <td width="10">
                                <div></div>
                            </td>
                            <td style="padding: 0px; vertical-align: middle;">
                                <h2 color="#000000"
                                    style="margin: 0px; font-size: 18px; color: rgb(0, 0, 0); font-weight: 600;">
                                    <span>Andrew</span><span>&nbsp;</span><span>Beveridge</span>
                                </h2>
                                <p color="#000000" font-size="medium"
                                    style="margin: 0px; color: rgb(0, 0, 0); font-size: 14px; line-height: 22px;">
                                    <span>Founder</span>
                                </p>
                                <p color="#000000" font-size="medium"
                                    style="margin: 0px; font-weight: 500; color: rgb(0, 0, 0); font-size: 14px; line-height: 22px;">
                                    <span>Nomad Karaoke
                                        LLC</span>
                                </p>
                                <table cellpadding="0" cellspacing="0" border="0"
                                    style="width: 100%; font-size: medium; font-family: Trebuchet MS; margin-top: 3px; margin-bottom: 3px;">
                                    <tbody>
                                        <tr>
                                            <td color="#ff7acc" direction="horizontal" width="auto" height="15"
                                                style="width: 100%; display: block; line-height:0; font-size:0;">
                                            </td>
                                        </tr>
                                        <tr>
                                            <td color="#ff7acc" direction="horizontal" width="auto" height="1"
                                                style="width: 100%; border-bottom: 1px solid rgb(255, 122, 204); border-left: medium; display: block; line-height:0; font-size:0;">
                                            </td>
                                        </tr>
                                    </tbody>
                                </table>

                                <table cellpadding="0" cellspacing="0" border="0"
                                    style="width: 100%; font-size: medium; font-family: Trebuchet MS; margin-top: 3px; margin-bottom: 3px;">
                                    <tbody>
                                        <tr>
                                            <td style="text-align: center; line-height:0; font-size:0;">
                                                <table cellpadding="0" cellspacing="0" border="0"
                                                    style="display: inline-block; font-size: medium; font-family: Trebuchet MS;">
                                                    <tbody>
                                                        <tr style="text-align: center;">
                                                            <td><a href="https://www.youtube.com/@nomadkaraoke"
                                                                    color="#230a89"
                                                                    style="display: inline-block; padding: 0px; background-color: rgb(35, 10, 137);"
                                                                    target="_blank"><img
                                                                        src="https://beveradb.github.io/public-images/youtube-icon-2x.png"
                                                                        alt="youtube" color="#230a89" width="24"
                                                                        style="background-color: rgb(35, 10, 137); max-width: 135px; display: block;"></a>
                                                            </td>
                                                            <td width="5">
                                                                <div></div>
                                                            </td>
                                                            <td><a href="https://github.com/nomadkaraoke"
                                                                    color="#230a89"
                                                                    style="display: inline-block; padding: 0px; background-color: rgb(35, 10, 137);"
                                                                    target="_blank"><img
                                                                        src="https://beveradb.github.io/public-images/github-icon-2x.png"
                                                                        alt="github" color="#230a89" width="24"
                                                                        style="background-color: rgb(35, 10, 137); max-width: 135px; display: block;"></a>
                                                            </td>
                                                            <td width="5">
                                                                <div></div>
                                                            </td>
                                                            <td><a href="https://www.linkedin.com/in/andrewbeveridge"
                                                                    color="#230a89"
                                                                    style="display: inline-block; padding: 0px; background-color: rgb(35, 10, 137);"
                                                                    target="_blank"><img
                                                                        src="https://beveradb.github.io/public-images/linkedin-icon-2x.png"
                                                                        alt="linkedin" color="#230a89" width="24"
                                                                        style="background-color: rgb(35, 10, 137); max-width: 135px; display: block;"></a>
                                                            </td>
                                                            <td width="5">
                                                                <div></div>
                                                            </td>
                                                            <td><a href="https://twitter.com/beveradb" color="#230a89"
                                                                    style="display: inline-block; padding: 0px; background-color: rgb(35, 10, 137);"><img
                                                                        src="https://beveradb.github.io/public-images/twitter-icon-2x.png"
                                                                        alt="twitter" color="#230a89" width="24"
                                                                        style="background-color: rgb(35, 10, 137); max-width: 135px; display: block;"></a>
                                                            </td>
                                                            <td width="5">
                                                                <div></div>
                                                            </td>
                                                            <td><a href="https://www.instagram.com/beveradb/"
                                                                    color="#230a89"
                                                                    style="display: inline-block; padding: 0px; background-color: rgb(35, 10, 137);"><img
                                                                        src="https://beveradb.github.io/public-images/instagram-icon-2x.png"
                                                                        alt="instagram" color="#230a89" width="24"
                                                                        style="background-color: rgb(35, 10, 137); max-width: 135px; display: block;"></a>
                                                            </td>
                                                            <td width="5">
                                                                <div></div>
                                                            </td>
                                                        </tr>
                                                    </tbody>
                                                </table>
                                            </td>
                                        </tr>
                                    </tbody>
                                </table>
                                <table cellpadding="0" cellspacing="0" border="0"
                                    style="width: 100%; font-size: medium; font-family: Trebuchet MS; margin-top: 3px; margin-bottom: 3px;">
                                    <tbody>
                                        <tr>
                                            <td color="#ff7acc" direction="horizontal" width="auto" height="1"
                                                style="width: 100%; border-bottom: 1px solid rgb(255, 122, 204); border-left: medium; display: block; line-height:0; font-size:0;">
                                            </td>
                                        </tr>
                                        <tr>
                                            <td color="#ff7acc" direction="horizontal" width="auto" height="15"
                                                style="width: 100%; display: block;">
                                            </td>
                                        </tr>
                                    </tbody>
                                </table>
                                <table cellpadding="0" cellspacing="0" border="0"
                                    style="font-size: medium; font-family: Trebuchet MS;">
                                    <tbody>
                                        <tr style="vertical-align: middle;" height="20">
                                            <td width="30" style="vertical-align: middle;">
                                                <table cellpadding="0" cellspacing="0" border="0"
                                                    style="font-size: medium; font-family: Trebuchet MS;">
                                                    <tbody>
                                                        <tr>
                                                            <td style="vertical-align: bottom;"><span color="#ff7acc"
                                                                    width="11"
                                                                    style="display: inline-block; background-color: rgb(255, 122, 204);"><img
                                                                        src="https://beveradb.github.io/public-images/phone-icon-2x.png"
                                                                        color="#ff7acc" alt="mobilePhone" width="13"
                                                                        style="display: block; background-color: rgb(255, 122, 204);"></span>
                                                            </td>
                                                        </tr>
                                                    </tbody>
                                                </table>
                                            </td>
                                            <td style="padding: 0px; color: rgb(0, 0, 0);"><a href="tel:8036363267"
                                                color="#000000"
                                                style="text-decoration: none; color: rgb(0, 0, 0); font-size: 12px;"><span>+1 (803) 636-3267</span></a> | <a href="tel:07835171222"
                                                color="#000000"
                                                style="text-decoration: none; color: rgb(0, 0, 0); font-size: 12px;"><span>+44 07835171222</span></a></td>
                                        </tr>
                                        <tr style="vertical-align: middle;" height="20">
                                            <td width="30" style="vertical-align: middle;">
                                                <table cellpadding="0" cellspacing="0" border="0"
                                                    style="font-size: medium; font-family: Trebuchet MS;">
                                                    <tbody>
                                                        <tr>
                                                            <td style="vertical-align: bottom;"><span color="#ff7acc"
                                                                    width="11"
                                                                    style="display: inline-block; background-color: rgb(255, 122, 204);"><img
                                                                        src="https://beveradb.github.io/public-images/email-icon-2x.png"
                                                                        color="#ff7acc" alt="emailAddress" width="13"
                                                                        style="display: block; background-color: rgb(255, 122, 204);"></span>
                                                            </td>
                                                        </tr>
                                                    </tbody>
                                                </table>
                                            </td>
                                            <td style="padding: 0px;"><a href="mailto:andrew@nomadkaraoke.com"
                                                    color="#000000"
                                                    style="text-decoration: none; color: rgb(0, 0, 0); font-size: 12px;"><span>andrew@nomadkaraoke.com</span></a>
                                            </td>
                                        </tr>
                                        <tr style="vertical-align: middle;" height="20">
                                            <td width="30" style="vertical-align: middle;">
                                                <table cellpadding="0" cellspacing="0" border="0"
                                                    style="font-size: medium; font-family: Trebuchet MS;">
                                                    <tbody>
                                                        <tr>
                                                            <td style="vertical-align: bottom;"><span color="#ff7acc"
                                                                    width="11"
                                                                    style="display: inline-block; background-color: rgb(255, 122, 204);"><img
                                                                        src="https://beveradb.github.io/public-images/link-icon-2x.png"
                                                                        color="#ff7acc" alt="website" width="13"
                                                                        style="display: block; background-color: rgb(255, 122, 204);"></span>
                                                            </td>
                                                        </tr>
                                                    </tbody>
                                                </table>
                                            </td>
                                            <td style="padding: 0px;"><a href="https://nomadkaraoke.com" target="_blank"
                                                    color="#000000"
                                                    style="text-decoration: none; color: rgb(0, 0, 0); font-size: 12px;"><span>nomadkaraoke.com</span></a>
                                            </td>
                                        </tr>
                                    </tbody>
                                </table>
                            </td>
                        </tr>
                    </tbody>
                </table>
            </td>
        </tr>
    </tbody>
</table>
"""

    def send_job_completion(
        self,
        to_email: str,
        message_content: str,
        artist: Optional[str] = None,
        title: Optional[str] = None,
        brand_code: Optional[str] = None,
        cc_admin: bool = True,
    ) -> bool:
        """
        Send job completion email with the rendered template content.

        Args:
            to_email: User's email address
            message_content: Pre-rendered message content (plain text)
            artist: Artist name for subject line
            title: Song title for subject line
            brand_code: Release ID (e.g., "NOMAD-1178") for subject line
            cc_admin: Whether to CC gen@nomadkaraoke.com

        Returns:
            True if email was sent successfully
        """
        # Build subject: "NOMAD-1178: Artist - Title (Your karaoke video is ready!)"
        # Sanitize artist/title to handle Unicode characters (curly quotes, em dashes, etc.)
        # that cause email header encoding issues (MIME headers use latin-1)
        safe_artist = sanitize_filename(artist) if artist else None
        safe_title = sanitize_filename(title) if title else None
        if brand_code and safe_artist and safe_title:
            subject = f"{brand_code}: {safe_artist} - {safe_title} (Your karaoke video is ready!)"
        elif safe_artist and safe_title:
            subject = f"{safe_artist} - {safe_title} (Your karaoke video is ready!)"
        else:
            subject = "Your karaoke video is ready!"

        extra_styles = """
        .content {
            white-space: pre-wrap;
        }
"""

        content = f"""
    <div class="content">{html.escape(message_content)}</div>
"""

        html_content = self._build_email_html(content, extra_styles)

        cc_emails = ["gen@nomadkaraoke.com"] if cc_admin else None

        return self.provider.send_email(
            to_email=to_email,
            subject=subject,
            html_content=html_content,
            text_content=message_content,
            cc_emails=cc_emails,
            bcc_emails=["done@nomadkaraoke.com"],
        )

    def send_action_reminder(
        self,
        to_email: str,
        message_content: str,
        action_type: str,
        artist: Optional[str] = None,
        title: Optional[str] = None,
    ) -> bool:
        """
        Send action-needed reminder email.

        Args:
            to_email: User's email address
            message_content: Pre-rendered message content (plain text)
            action_type: Type of action needed ("lyrics" or "instrumental")
            artist: Artist name for subject line
            title: Song title for subject line

        Returns:
            True if email was sent successfully
        """
        # Build subject based on action type
        song_info = f" for {artist} - {title}" if artist and title else ""
        if action_type == "lyrics":
            subject = f"Action needed: Review lyrics{song_info}"
        elif action_type == "instrumental":
            subject = f"Action needed: Select instrumental{song_info}"
        else:
            subject = f"Action needed{song_info}"

        extra_styles = """
        .alert {
            background-color: #fef3c7;
            border: 1px solid #fcd34d;
            border-radius: 8px;
            padding: 16px;
            margin: 20px 0;
            text-align: center;
        }
        .content {
            white-space: pre-wrap;
        }
"""

        content = f"""
    <div class="alert">
        ⏰ Your karaoke video needs input from you!
    </div>

    <div class="content">{html.escape(message_content)}</div>
"""

        html_content = self._build_email_html(content, extra_styles)

        return self.provider.send_email(
            to_email=to_email,
            subject=subject,
            html_content=html_content,
            text_content=message_content,
        )

    def send_review_reminder(
        self,
        to_email: str,
        artist: Optional[str] = None,
        title: Optional[str] = None,
        job_id: Optional[str] = None,
    ) -> bool:
        """
        Send 24h review reminder email with expiry warning.

        Sent when a job has been awaiting review for 24 hours.
        Offers help and warns that the job will expire in another 24 hours.

        Args:
            to_email: User's email address
            artist: Artist name for subject line
            title: Song title for subject line
            job_id: Job ID for constructing the review link

        Returns:
            True if email was sent successfully
        """
        song_info = f" — {artist} - {title}" if artist and title else ""
        subject = f"Reminder: Your karaoke video is waiting for review{song_info}"

        review_url = f"{self.frontend_url}/app/jobs#/{job_id}/review" if job_id else f"{self.frontend_url}/app"

        extra_styles = """
        .alert {
            background-color: #fef3c7;
            border: 1px solid #fcd34d;
            border-radius: 8px;
            padding: 16px;
            margin: 20px 0;
            text-align: center;
        }
        .expiry-warning {
            background-color: #fef2f2;
            border: 1px solid #fca5a5;
            border-radius: 8px;
            padding: 16px;
            margin: 20px 0;
            text-align: center;
            color: #991b1b;
        }
        .review-btn {
            display: inline-block;
            background-color: #ff7acc;
            color: #ffffff !important;
            text-decoration: none;
            padding: 12px 24px;
            border-radius: 8px;
            font-weight: bold;
            margin: 16px 0;
        }
"""

        song_name = f" for <strong>{html.escape(artist)} - {html.escape(title)}</strong>" if artist and title else ""

        content = f"""
    <div class="alert">
        ⏰ Your karaoke video is waiting for you!
    </div>

    <p>Hi there! Just a friendly reminder that your karaoke video{song_name} is ready for review.</p>

    <p style="text-align: center;">
        <a href="{html.escape(review_url)}" class="review-btn">Review Your Video</a>
    </p>

    <p>During the review, you can check the lyrics are correct and choose your preferred instrumental track. It usually only takes a few minutes!</p>

    <p>If you're having any trouble with the review process, just reply to this email and I'll be happy to help you out.</p>

    <div class="expiry-warning">
        ⚠️ Please note: if the review isn't completed within the next <strong>24 hours</strong>,
        the job will automatically expire and your credit will be refunded.
    </div>
"""

        html_content = self._build_email_html(content, extra_styles)

        text_content = (
            f"Reminder: Your karaoke video{' for ' + artist + ' - ' + title if artist and title else ''} is ready for review.\n\n"
            f"Review it here: {review_url}\n\n"
            "If you're having trouble with the review process, just reply to this email and I'll help you out.\n\n"
            "Please note: if the review isn't completed within the next 24 hours, "
            "the job will automatically expire and your credit will be refunded."
        )

        return self.provider.send_email(
            to_email=to_email,
            subject=subject,
            html_content=html_content,
            text_content=text_content,
        )

    def send_review_expired(
        self,
        to_email: str,
        artist: Optional[str] = None,
        title: Optional[str] = None,
        credits_balance: int = 0,
    ) -> bool:
        """
        Send review expiry notification email.

        Sent when a job is auto-cancelled after 48 hours in review.
        Informs the user their credit has been refunded.

        Args:
            to_email: User's email address
            artist: Artist name for subject line
            title: Song title for subject line
            credits_balance: User's current credit balance after refund

        Returns:
            True if email was sent successfully
        """
        song_info = f" — {artist} - {title}" if artist and title else ""
        subject = f"Your karaoke video has expired{song_info}"

        extra_styles = """
        .info-box {
            background-color: #f0f9ff;
            border: 1px solid #93c5fd;
            border-radius: 8px;
            padding: 16px;
            margin: 20px 0;
            text-align: center;
        }
        .credit-badge {
            display: inline-block;
            background-color: #22c55e;
            color: #ffffff;
            padding: 4px 12px;
            border-radius: 12px;
            font-weight: bold;
        }
        .create-btn {
            display: inline-block;
            background-color: #ff7acc;
            color: #ffffff !important;
            text-decoration: none;
            padding: 12px 24px;
            border-radius: 8px;
            font-weight: bold;
            margin: 16px 0;
        }
"""

        song_name = f" for <strong>{html.escape(artist)} - {html.escape(title)}</strong>" if artist and title else ""

        content = f"""
    <div class="info-box">
        Your karaoke video{song_name} has been automatically cancelled because the review
        wasn't completed within 48 hours.
    </div>

    <p>Don't worry — your credit has been refunded! Your current balance is
    <span class="credit-badge">{credits_balance} credit{'s' if credits_balance != 1 else ''}</span>.</p>

    <p>You can create a new karaoke video anytime using your credits:</p>

    <p style="text-align: center;">
        <a href="{html.escape(self.frontend_url)}/app" class="create-btn">Create New Video</a>
    </p>

    <p>If you had any trouble with the review process or need help, just reply to this email — I'd love to help!</p>
"""

        html_content = self._build_email_html(content, extra_styles)

        text_content = (
            f"Your karaoke video{' for ' + artist + ' - ' + title if artist and title else ''} has been "
            "automatically cancelled because the review wasn't completed within 48 hours.\n\n"
            f"Your credit has been refunded. Current balance: {credits_balance} credit{'s' if credits_balance != 1 else ''}.\n\n"
            f"Create a new video anytime: {self.frontend_url}/app\n\n"
            "If you had any trouble or need help, just reply to this email."
        )

        return self.provider.send_email(
            to_email=to_email,
            subject=subject,
            html_content=html_content,
            text_content=text_content,
        )

    def send_youtube_upload_complete(
        self,
        to_email: str,
        message_content: str,
        artist: Optional[str] = None,
        title: Optional[str] = None,
        brand_code: Optional[str] = None,
    ) -> bool:
        """
        Send follow-up email when a deferred YouTube upload completes.

        Args:
            to_email: User's email address
            message_content: Pre-rendered message content (plain text)
            artist: Artist name for subject line
            title: Song title for subject line
            brand_code: Release ID for subject line

        Returns:
            True if email was sent successfully
        """
        safe_artist = sanitize_filename(artist) if artist else None
        safe_title = sanitize_filename(title) if title else None
        if brand_code and safe_artist and safe_title:
            subject = f"{brand_code}: {safe_artist} - {safe_title} (YouTube upload ready!)"
        elif safe_artist and safe_title:
            subject = f"{safe_artist} - {safe_title} (YouTube upload ready!)"
        else:
            subject = "Your YouTube upload is ready!"

        extra_styles = """
        .content {
            white-space: pre-wrap;
        }
"""

        content = f"""
    <div class="content">{html.escape(message_content)}</div>
"""

        html_content = self._build_email_html(content, extra_styles)

        return self.provider.send_email(
            to_email=to_email,
            subject=subject,
            html_content=html_content,
            text_content=message_content,
            cc_emails=["gen@nomadkaraoke.com"],
        )

    def send_made_for_you_order_confirmation(
        self,
        to_email: str,
        artist: str,
        title: str,
        job_id: str,
        notes: Optional[str] = None,
    ) -> bool:
        """
        Send order confirmation email to customer for made-for-you orders.

        Args:
            to_email: Customer's email address
            artist: Artist name
            title: Song title
            job_id: Order/job ID for reference
            notes: Optional customer notes

        Returns:
            True if email was sent successfully
        """
        subject = f"Order Confirmed: {artist} - {title} | Nomad Karaoke"

        extra_styles = """
        .order-details {
            background-color: #f8fafc;
            border-radius: 8px;
            padding: 20px;
            margin: 20px 0;
        }
        .order-details h3 {
            margin-top: 0;
            color: #1e293b;
        }
        .order-details p {
            margin: 8px 0;
            color: #475569;
        }
        .highlight {
            color: #059669;
            font-weight: bold;
        }
"""

        notes_html = ""
        notes_text = ""
        if notes:
            notes_html = f"<p><strong>Your Notes:</strong> {html.escape(notes)}</p>"
            notes_text = f"\nYour Notes: {notes}"

        content = f"""
    <h2>Thank You for Your Order!</h2>

    <div class="order-details">
        <h3>Order Details</h3>
        <p><strong>Order ID:</strong> {html.escape(job_id)}</p>
        <p><strong>Artist:</strong> {html.escape(artist)}</p>
        <p><strong>Title:</strong> {html.escape(title)}</p>
        {notes_html}
    </div>

    <p>Our team will create your custom karaoke video within <span class="highlight">24 hours</span>.</p>

    <p>You'll receive an email with download links as soon as your video is ready.</p>

    <p><strong>No action needed</strong> - sit back and we'll take care of everything!</p>
"""

        text_content = f"""Thank You for Your Order!

Order Details:
Order ID: {job_id}
Artist: {artist}
Title: {title}{notes_text}

Our team will create your custom karaoke video within 24 hours.

You'll receive an email with download links as soon as your video is ready.

No action needed - sit back and we'll take care of everything!
"""

        html_content = self._build_email_html(content, extra_styles)

        return self.provider.send_email(
            to_email=to_email,
            subject=subject,
            html_content=html_content,
            text_content=text_content,
            bcc_emails=["done@nomadkaraoke.com"],
        )

    def send_made_for_you_admin_notification(
        self,
        to_email: str,
        customer_email: str,
        artist: str,
        title: str,
        job_id: str,
        admin_login_token: str,
        notes: Optional[str] = None,
        audio_source_count: int = 0,
    ) -> bool:
        """
        Send notification email to admin for new made-for-you orders.

        Args:
            to_email: Admin email address
            customer_email: Customer's email address
            artist: Artist name
            title: Song title
            job_id: Job ID for reference
            admin_login_token: Token for one-click admin login
            notes: Optional customer notes
            audio_source_count: Number of audio sources found

        Returns:
            True if email was sent successfully
        """
        subject = f"Karaoke Order: {artist} - {title} [ID: {job_id}]"

        extra_styles = """
        .order-info {
            background-color: #f8fafc;
            border-radius: 8px;
            padding: 20px;
            margin: 20px 0;
        }
        .order-info p {
            margin: 8px 0;
        }
        .action-button {
            display: inline-block;
            background-color: #2563eb;
            color: white !important;
            padding: 12px 24px;
            border-radius: 6px;
            text-decoration: none;
            font-weight: bold;
            margin-top: 16px;
        }
"""

        notes_html = ""
        notes_text = ""
        if notes:
            notes_html = f"<p><strong>Customer Notes:</strong> {html.escape(notes)}</p>"
            notes_text = f"\nCustomer Notes: {notes}"

        # Calculate deadline (24 hours from now, converted to Eastern Time)
        deadline_utc = datetime.now(timezone.utc) + timedelta(hours=24)
        eastern_tz = ZoneInfo("America/New_York")
        deadline_eastern = deadline_utc.astimezone(eastern_tz)
        # Use "ET" (Eastern Time) to be correct for both EST and EDT
        deadline_str = deadline_eastern.strftime("%B %d, %Y at %I:%M %p") + " ET"

        # Link to /app/ with admin login token for one-click access
        app_url = f"{self.frontend_url.rstrip('/')}/app/?admin_token={admin_login_token}"

        content = f"""
    <div class="order-info">
        <p><strong>Order / Job ID:</strong> {html.escape(job_id)}</p>
        <p><strong>Customer:</strong> {html.escape(customer_email)}</p>
        <p><strong>Artist:</strong> {html.escape(artist)}</p>
        <p><strong>Title:</strong> {html.escape(title)}</p>
        <p><strong>Audio Sources Found:</strong> {audio_source_count}</p>
        {notes_html}
        <p><strong>Deliver By:</strong> {deadline_str}</p>
        <p style="margin-top: 16px;"><strong>Action Required:</strong> Select an audio source to start processing.</p>
        <a href="{app_url}" class="action-button">Open Job</a>
    </div>
"""

        text_content = f"""New Karaoke Order

Order / Job ID: {job_id}
Customer: {customer_email}
Artist: {artist}
Title: {title}
Audio Sources Found: {audio_source_count}{notes_text}
Deliver By: {deadline_str}

Action Required: Select an audio source to start processing.

Open Job: {app_url}
"""

        html_content = self._build_email_html(content, extra_styles)

        return self.provider.send_email(
            to_email=to_email,
            subject=subject,
            html_content=html_content,
            text_content=text_content,
        )


    def send_feedback_notification(
        self,
        user_email: str,
        overall_rating: int,
        ease_of_use_rating: int,
        lyrics_accuracy_rating: int,
        correction_experience_rating: int,
        what_went_well: Optional[str] = None,
        what_could_improve: Optional[str] = None,
        additional_comments: Optional[str] = None,
        would_recommend: bool = True,
        would_use_again: bool = True,
    ) -> bool:
        """
        Send admin notification when a user submits product feedback.

        Args:
            user_email: Email of the user who submitted feedback
            overall_rating: 1-5 star rating
            ease_of_use_rating: 1-5 star rating
            lyrics_accuracy_rating: 1-5 star rating
            correction_experience_rating: 1-5 star rating
            what_went_well: Optional text feedback
            what_could_improve: Optional text feedback
            additional_comments: Optional text feedback
            would_recommend: Whether user would recommend
            would_use_again: Whether user would use again

        Returns:
            True if email was sent successfully
        """
        admin_email = os.getenv("ADMIN_NOTIFICATION_EMAIL", "admin@nomadkaraoke.com")
        subject = f"New Feedback from {user_email} — {overall_rating}/5 overall"

        def stars(rating: int) -> str:
            return "★" * rating + "☆" * (5 - rating)

        def text_block(label: str, value: Optional[str]) -> str:
            if not value:
                return ""
            escaped = html.escape(value).replace("\n", "<br>")
            return f"""
            <tr>
                <td style="padding: 8px 12px; font-weight: bold; vertical-align: top; white-space: nowrap;">{label}</td>
                <td style="padding: 8px 12px;">{escaped}</td>
            </tr>"""

        recommend_badge = "Yes ✓" if would_recommend else "No ✗"
        use_again_badge = "Yes ✓" if would_use_again else "No ✗"

        admin_url = f"{self.frontend_url}/admin/feedback"

        extra_styles = """
        .feedback-table {
            width: 100%;
            border-collapse: collapse;
            margin: 16px 0;
        }
        .feedback-table td {
            border-bottom: 1px solid #e5e7eb;
        }
        .stars {
            color: #eab308;
            font-size: 18px;
            letter-spacing: 2px;
        }
"""

        content = f"""
    <p>New product feedback submitted by <strong>{html.escape(user_email)}</strong>:</p>

    <table class="feedback-table">
        <tr>
            <td style="padding: 8px 12px; font-weight: bold;">Overall</td>
            <td style="padding: 8px 12px;"><span class="stars">{stars(overall_rating)}</span> ({overall_rating}/5)</td>
        </tr>
        <tr>
            <td style="padding: 8px 12px; font-weight: bold;">Ease of Use</td>
            <td style="padding: 8px 12px;"><span class="stars">{stars(ease_of_use_rating)}</span> ({ease_of_use_rating}/5)</td>
        </tr>
        <tr>
            <td style="padding: 8px 12px; font-weight: bold;">Lyrics Accuracy</td>
            <td style="padding: 8px 12px;"><span class="stars">{stars(lyrics_accuracy_rating)}</span> ({lyrics_accuracy_rating}/5)</td>
        </tr>
        <tr>
            <td style="padding: 8px 12px; font-weight: bold;">Correction Experience</td>
            <td style="padding: 8px 12px;"><span class="stars">{stars(correction_experience_rating)}</span> ({correction_experience_rating}/5)</td>
        </tr>
        <tr>
            <td style="padding: 8px 12px; font-weight: bold;">Would Recommend</td>
            <td style="padding: 8px 12px;">{recommend_badge}</td>
        </tr>
        <tr>
            <td style="padding: 8px 12px; font-weight: bold;">Would Use Again</td>
            <td style="padding: 8px 12px;">{use_again_badge}</td>
        </tr>
        {text_block("What went well", what_went_well)}
        {text_block("What could improve", what_could_improve)}
        {text_block("Additional comments", additional_comments)}
    </table>

    <p style="text-align: center;">
        <a href="{admin_url}" class="button">View All Feedback</a>
    </p>
"""

        text_content = f"""New Feedback from {user_email}

Overall: {overall_rating}/5
Ease of Use: {ease_of_use_rating}/5
Lyrics Accuracy: {lyrics_accuracy_rating}/5
Correction Experience: {correction_experience_rating}/5
Would Recommend: {recommend_badge}
Would Use Again: {use_again_badge}

What went well: {what_went_well or '(empty)'}
What could improve: {what_could_improve or '(empty)'}
Additional comments: {additional_comments or '(empty)'}

View all feedback: {admin_url}
"""

        html_content = self._build_email_html(content, extra_styles)

        return self.provider.send_email(
            to_email=admin_email,
            subject=subject,
            html_content=html_content,
            text_content=text_content,
        )


    def send_credit_review_needed_email(self, user_email: str, grant_type: str, reason: str) -> bool:
        """
        Notify Andrew that a credit grant needs manual review.

        Sent when AI evaluation fails, is uncertain, or encounters an error.
        Includes a direct link to the user's admin page for quick action.
        """
        admin_user_url = f"{self.frontend_url}/admin/users/detail?email={user_email}"
        subject = f"Credit review needed: {user_email} ({grant_type})"

        content = f"""
    <p>A {grant_type} credit grant for <strong>{user_email}</strong> needs your review.</p>

    <p><strong>Reason:</strong> {reason}</p>

    <p>The user has been told that their signup is being reviewed and credits will be
    assigned shortly if everything checks out.</p>

    <p style="text-align: center;">
        <a href="{admin_user_url}" class="button">Review User</a>
    </p>

    <p style="color: #999; font-size: 14px;">
        If the user looks legitimate, grant them credits from their admin page.
        If not, no action needed — they already have 0 credits.
    </p>
"""

        text_content = f"""Credit review needed: {user_email} ({grant_type})

Reason: {reason}

Review user: {admin_user_url}

If the user looks legitimate, grant them credits from their admin page.
"""

        html_content = self._build_email_html(content)

        return self.provider.send_email(
            to_email="andrew@beveridge.uk",
            subject=subject,
            html_content=html_content,
            text_content=text_content,
        )

    def send_credit_denied_email(self, email: str, grant_type: str = "welcome") -> bool:
        """
        Send a friendly-but-firm email when free credits are denied due to abuse signals.

        CC'd to andrew@beveridge.uk so he can intervene if it's a false positive.
        The tone invites the user to reply if it's a mistake.
        """
        subject = "About your Nomad Karaoke free credits"

        credit_type = "welcome credits" if grant_type == "welcome" else "feedback bonus credits"

        content = f"""
    <p>Hi there,</p>

    <p>Thanks for signing up for Nomad Karaoke!</p>

    <p>Unfortunately, we weren't able to grant your free {credit_type} this time. Our system detected
    patterns that suggest this may not be your first account with us, and each karaoke generation job
    costs us real money in cloud computing and API fees.</p>

    <p>We totally understand if this is a mistake! If you're a genuine new user, please just
    <strong>reply to this email</strong> and let Andrew (the founder) know. He built this service and is
    happy to grant you some free credits to try it out — he just asks that you provide honest feedback
    on the experience in return.</p>

    <p>If you'd like to get started right away, you can also purchase credits:</p>

    <p style="text-align: center;">
        <a href="{self.buy_url}" class="button">Buy Credits</a>
    </p>

    <p style="color: #999; font-size: 14px;">We keep costs low so we can offer the service affordably,
    which means we need to be careful about free credit abuse. Thanks for understanding!</p>

    {self._get_email_signature()}
"""

        text_content = f"""Hi there,

Thanks for signing up for Nomad Karaoke!

Unfortunately, we weren't able to grant your free {credit_type} this time. Our system detected patterns that suggest this may not be your first account with us, and each karaoke generation job costs us real money in cloud computing and API fees.

We totally understand if this is a mistake! If you're a genuine new user, please just reply to this email and let Andrew (the founder) know. He built this service and is happy to grant you some free credits to try it out - he just asks that you provide honest feedback on the experience in return.

If you'd like to get started right away, you can also purchase credits:
{self.buy_url}

---
© {self._get_year()} Nomad Karaoke
"""

        html_content = self._build_email_html(content)

        return self.provider.send_email(
            to_email=email,
            subject=subject,
            html_content=html_content,
            text_content=text_content,
            cc_emails=["andrew@beveridge.uk"],
        )


# Global instance
_email_service = None


def get_email_service() -> EmailService:
    """Get the global email service instance."""
    global _email_service
    if _email_service is None:
        _email_service = EmailService()
    return _email_service
