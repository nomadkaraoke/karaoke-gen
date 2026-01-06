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
from typing import Optional, List

from backend.config import get_settings


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
    ) -> bool:
        """Send an email. Returns True if successful."""
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
    ) -> bool:
        logger.info("=" * 60)
        logger.info(f"EMAIL TO: {to_email}")
        if cc_emails:
            logger.info(f"CC: {', '.join(cc_emails)}")
        logger.info(f"SUBJECT: {subject}")
        logger.info("-" * 60)
        logger.info(text_content or html_content)
        logger.info("=" * 60)
        return True


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
    ) -> bool:
        try:
            # Import here to avoid requiring sendgrid in all environments
            from sendgrid import SendGridAPIClient
            from sendgrid.helpers.mail import Mail, Email, To, Content, Cc

            sg = SendGridAPIClient(api_key=self.api_key)

            message = Mail(
                from_email=Email(self.from_email, self.from_name),
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

            if text_content:
                message.add_content(Content("text/plain", text_content))

            response = sg.send(message)

            if response.status_code >= 200 and response.status_code < 300:
                cc_info = f" (CC: {', '.join(cc_emails)})" if cc_emails else ""
                logger.info(f"Email sent to {to_email}{cc_info} via SendGrid")
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

    def send_magic_link(self, email: str, token: str) -> bool:
        """
        Send a magic link email for authentication.

        Args:
            email: User's email address
            token: Magic link token

        Returns:
            True if email was sent successfully
        """
        magic_link_url = f"{self.frontend_url}/auth/verify?token={token}"

        subject = "Sign in to Nomad Karaoke"

        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
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
        .logo {{
            font-size: 24px;
            font-weight: bold;
            color: #3b82f6;
        }}
        .button {{
            display: inline-block;
            background-color: #3b82f6;
            color: white;
            padding: 14px 28px;
            text-decoration: none;
            border-radius: 8px;
            font-weight: 600;
            margin: 20px 0;
        }}
        .button:hover {{
            background-color: #2563eb;
        }}
        .footer {{
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #eee;
            font-size: 12px;
            color: #666;
        }}
        .warning {{
            background-color: #fef3c7;
            border: 1px solid #fcd34d;
            border-radius: 4px;
            padding: 12px;
            margin: 20px 0;
            font-size: 14px;
        }}
    </style>
</head>
<body>
    <div class="header">
        <div class="logo">🎤 Nomad Karaoke</div>
    </div>

    <p>Hi there,</p>

    <p>Click the button below to sign in to Nomad Karaoke:</p>

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

    <div class="footer">
        <p>© {self._get_year()} Nomad Karaoke. All rights reserved.</p>
        <p>This is an automated message, please do not reply.</p>
    </div>
</body>
</html>
"""

        text_content = f"""
Sign in to Nomad Karaoke
========================

Click this link to sign in:
{magic_link_url}

This link expires in 15 minutes and can only be used once.

If you didn't request this email, you can safely ignore it.

---
© {self._get_year()} Nomad Karaoke
"""

        return self.provider.send_email(email, subject, html_content, text_content)

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

        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
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
        .logo {{
            font-size: 24px;
            font-weight: bold;
            color: #3b82f6;
        }}
        .credits-box {{
            background-color: #ecfdf5;
            border: 2px solid #10b981;
            border-radius: 12px;
            padding: 24px;
            text-align: center;
            margin: 20px 0;
        }}
        .credits-number {{
            font-size: 48px;
            font-weight: bold;
            color: #10b981;
        }}
        .button {{
            display: inline-block;
            background-color: #3b82f6;
            color: white;
            padding: 14px 28px;
            text-decoration: none;
            border-radius: 8px;
            font-weight: 600;
            margin: 20px 0;
        }}
        .footer {{
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #eee;
            font-size: 12px;
            color: #666;
        }}
    </style>
</head>
<body>
    <div class="header">
        <div class="logo">🎤 Nomad Karaoke</div>
    </div>

    <p>Great news! 🎉</p>

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

    <div class="footer">
        <p>© {self._get_year()} Nomad Karaoke. All rights reserved.</p>
    </div>
</body>
</html>
"""

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

        credits_text = f"You have <strong>{credits} credits</strong> to get started!" if credits > 0 else ""

        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
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
        .logo {{
            font-size: 24px;
            font-weight: bold;
            color: #3b82f6;
        }}
        .button {{
            display: inline-block;
            background-color: #3b82f6;
            color: white;
            padding: 14px 28px;
            text-decoration: none;
            border-radius: 8px;
            font-weight: 600;
            margin: 20px 0;
        }}
        .feature {{
            display: flex;
            align-items: flex-start;
            margin: 16px 0;
        }}
        .feature-icon {{
            font-size: 24px;
            margin-right: 12px;
        }}
        .footer {{
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #eee;
            font-size: 12px;
            color: #666;
        }}
    </style>
</head>
<body>
    <div class="header">
        <div class="logo">🎤 Nomad Karaoke</div>
    </div>

    <p>Welcome to Nomad Karaoke!</p>

    <p>Turn any song into a professional karaoke video in minutes. {credits_text}</p>

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
            <strong>2. AI does the magic</strong><br>
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

    <p style="text-align: center;">
        <a href="{self.frontend_url}" class="button">Get Started</a>
    </p>

    <div class="footer">
        <p>Questions? Reply to this email and we'll help you out.</p>
        <p>© {self._get_year()} Nomad Karaoke. All rights reserved.</p>
    </div>
</body>
</html>
"""

        text_content = f"""
Welcome to Nomad Karaoke!

Turn any song into a professional karaoke video in minutes.

Here's how it works:

1. Search for a song
   Enter the artist and title, and we'll find high-quality audio.

2. AI does the magic
   We separate vocals, transcribe lyrics, and sync everything perfectly.

3. Review & customize
   Fine-tune the lyrics if needed, choose your instrumental.

4. Get your video
   Download your 4K karaoke video or upload directly to YouTube.

Get started: {self.frontend_url}

---
© {self._get_year()} Nomad Karaoke
"""

        return self.provider.send_email(email, subject, html_content, text_content)

    def send_beta_welcome_email(self, email: str, credits: int = 1) -> bool:
        """
        Send welcome email to new beta testers.

        Args:
            email: User's email address
            credits: Initial free credits granted

        Returns:
            True if email was sent successfully
        """
        subject = "Welcome Beta Tester! Free Karaoke Credits Inside 🎤"

        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
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
        .logo {{
            font-size: 24px;
            font-weight: bold;
            color: #3b82f6;
        }}
        .beta-badge {{
            display: inline-block;
            background: linear-gradient(135deg, #8b5cf6, #ec4899);
            color: white;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: bold;
            margin-left: 8px;
        }}
        .credits-box {{
            background-color: #ecfdf5;
            border: 2px solid #10b981;
            border-radius: 12px;
            padding: 24px;
            text-align: center;
            margin: 20px 0;
        }}
        .credits-number {{
            font-size: 48px;
            font-weight: bold;
            color: #10b981;
        }}
        .reminder {{
            background-color: #fef3c7;
            border: 1px solid #fcd34d;
            border-radius: 8px;
            padding: 16px;
            margin: 20px 0;
        }}
        .button {{
            display: inline-block;
            background-color: #3b82f6;
            color: white;
            padding: 14px 28px;
            text-decoration: none;
            border-radius: 8px;
            font-weight: 600;
            margin: 20px 0;
        }}
        .footer {{
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #eee;
            font-size: 12px;
            color: #666;
        }}
    </style>
</head>
<body>
    <div class="header">
        <span class="logo">🎤 Nomad Karaoke</span>
        <span class="beta-badge">BETA TESTER</span>
    </div>

    <p>Thank you for joining our beta program! 🎉</p>

    <p>As promised, here's your free credit to create a karaoke video:</p>

    <div class="credits-box">
        <div>Your balance:</div>
        <div class="credits-number">{credits}</div>
        <div>free credit{'' if credits == 1 else 's'}</div>
    </div>

    <div class="reminder">
        <strong>📝 Your Promise:</strong><br>
        Remember, as a beta tester you agreed to:
        <ul style="margin: 10px 0; padding-left: 20px;">
            <li>Review and correct any lyrics timing issues</li>
            <li>Share your honest feedback after trying the tool</li>
        </ul>
        We'll send you a quick feedback form after your job completes!
    </div>

    <p style="text-align: center;">
        <a href="{self.frontend_url}" class="button">Create Your Karaoke Video</a>
    </p>

    <p>Thanks for helping us make Nomad Karaoke better!</p>

    <div class="footer">
        <p>© {self._get_year()} Nomad Karaoke. All rights reserved.</p>
    </div>
</body>
</html>
"""

        text_content = f"""
Welcome Beta Tester!

Thank you for joining our beta program!

You've been granted {credits} free credit{'' if credits == 1 else 's'} to create karaoke videos.

YOUR PROMISE:
As a beta tester, you agreed to:
- Review and correct any lyrics timing issues
- Share your honest feedback after trying the tool

We'll send you a quick feedback form after your job completes!

Create your karaoke video: {self.frontend_url}

Thanks for helping us make Nomad Karaoke better!

---
© {self._get_year()} Nomad Karaoke
"""

        return self.provider.send_email(email, subject, html_content, text_content)

    def send_feedback_request_email(self, email: str, feedback_url: str, job_title: Optional[str] = None) -> bool:
        """
        Send feedback request email to beta testers.

        Args:
            email: User's email address
            feedback_url: URL to the feedback form
            job_title: Optional title of the completed job

        Returns:
            True if email was sent successfully
        """
        subject = "Quick feedback on your karaoke experience? 🎤"

        # Escape job_title to prevent XSS in email clients that render HTML
        safe_job_title = html.escape(job_title) if job_title else ""
        job_context = f" for <strong>{safe_job_title}</strong>" if job_title else ""

        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
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
        .logo {{
            font-size: 24px;
            font-weight: bold;
            color: #3b82f6;
        }}
        .feedback-box {{
            background: linear-gradient(135deg, #f0f9ff, #e0f2fe);
            border: 2px solid #3b82f6;
            border-radius: 12px;
            padding: 24px;
            text-align: center;
            margin: 20px 0;
        }}
        .stars {{
            font-size: 36px;
            margin: 10px 0;
        }}
        .button {{
            display: inline-block;
            background-color: #3b82f6;
            color: white;
            padding: 14px 28px;
            text-decoration: none;
            border-radius: 8px;
            font-weight: 600;
            margin: 20px 0;
        }}
        .time-note {{
            background-color: #fef3c7;
            border-radius: 8px;
            padding: 12px;
            margin: 20px 0;
            font-size: 14px;
        }}
        .footer {{
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #eee;
            font-size: 12px;
            color: #666;
        }}
    </style>
</head>
<body>
    <div class="header">
        <div class="logo">🎤 Nomad Karaoke</div>
    </div>

    <p>Hi there!</p>

    <p>Hope you enjoyed creating your karaoke video{job_context}! As a beta tester, your feedback is super valuable to us.</p>

    <div class="feedback-box">
        <div class="stars">⭐⭐⭐⭐⭐</div>
        <p><strong>How was your experience?</strong></p>
        <p>Quick 2-minute survey - we'd love to hear your thoughts!</p>
    </div>

    <p style="text-align: center;">
        <a href="{feedback_url}" class="button">Share Your Feedback</a>
    </p>

    <div class="time-note">
        ⏱️ This takes less than 2 minutes and helps us improve the tool for everyone!
    </div>

    <p>Specifically, we'd love to know:</p>
    <ul>
        <li>How easy was it to use?</li>
        <li>Were the lyrics accurate?</li>
        <li>How was the correction experience?</li>
        <li>What could we improve?</li>
    </ul>

    <p>Thanks for being part of making Nomad Karaoke better! 🙏</p>

    <div class="footer">
        <p>© {self._get_year()} Nomad Karaoke. All rights reserved.</p>
    </div>
</body>
</html>
"""

        text_content = f"""
Quick feedback on your karaoke experience?

Hi there!

Hope you enjoyed creating your karaoke video{' for ' + safe_job_title if safe_job_title else ''}!

As a beta tester, your feedback is super valuable to us.

Share your feedback (2-minute survey): {feedback_url}

We'd love to know:
- How easy was it to use?
- Were the lyrics accurate?
- How was the correction experience?
- What could we improve?

Thanks for being part of making Nomad Karaoke better!

---
© {self._get_year()} Nomad Karaoke
"""

        return self.provider.send_email(email, subject, html_content, text_content)

    def _get_year(self) -> int:
        """Get current year for copyright notices."""
        from datetime import datetime
        return datetime.now().year

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
        if brand_code and artist and title:
            subject = f"{brand_code}: {artist} - {title} (Your karaoke video is ready!)"
        elif artist and title:
            subject = f"{artist} - {title} (Your karaoke video is ready!)"
        else:
            subject = "Your karaoke video is ready!"

        # Convert plain text to simple HTML (preserve line breaks)
        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 600px;
            margin: 0 auto;
            padding: 20px;
            white-space: pre-wrap;
        }}
        .header {{
            text-align: center;
            padding: 20px 0;
        }}
        .logo {{
            font-size: 24px;
            font-weight: bold;
            color: #3b82f6;
        }}
        .content {{
            white-space: pre-wrap;
            font-size: 14px;
        }}
        .footer {{
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #eee;
            font-size: 12px;
            color: #666;
        }}
    </style>
</head>
<body>
    <div class="header">
        <div class="logo">🎤 Nomad Karaoke</div>
    </div>

    <div class="content">{html.escape(message_content)}</div>

    <div class="footer">
        <p>© {self._get_year()} Nomad Karaoke. All rights reserved.</p>
    </div>
</body>
</html>
"""

        cc_emails = ["gen@nomadkaraoke.com"] if cc_admin else None

        return self.provider.send_email(
            to_email=to_email,
            subject=subject,
            html_content=html_content,
            text_content=message_content,
            cc_emails=cc_emails,
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

        # Convert plain text to simple HTML
        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
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
        .logo {{
            font-size: 24px;
            font-weight: bold;
            color: #3b82f6;
        }}
        .alert {{
            background-color: #fef3c7;
            border: 1px solid #fcd34d;
            border-radius: 8px;
            padding: 16px;
            margin: 20px 0;
            text-align: center;
        }}
        .content {{
            white-space: pre-wrap;
            font-size: 14px;
        }}
        .footer {{
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #eee;
            font-size: 12px;
            color: #666;
        }}
    </style>
</head>
<body>
    <div class="header">
        <div class="logo">🎤 Nomad Karaoke</div>
    </div>

    <div class="alert">
        ⏰ Your karaoke video is waiting for you!
    </div>

    <div class="content">{html.escape(message_content)}</div>

    <div class="footer">
        <p>© {self._get_year()} Nomad Karaoke. All rights reserved.</p>
    </div>
</body>
</html>
"""

        return self.provider.send_email(
            to_email=to_email,
            subject=subject,
            html_content=html_content,
            text_content=message_content,
        )


# Global instance
_email_service = None


def get_email_service() -> EmailService:
    """Get the global email service instance."""
    global _email_service
    if _email_service is None:
        _email_service = EmailService()
    return _email_service
