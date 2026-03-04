#!/usr/bin/env python3
"""
Preview email templates by generating HTML files.

Usage:
    python scripts/preview-emails.py

This will create HTML files in /tmp/email-previews/ that can be opened in a browser.
"""
import os
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Set dummy env vars before importing
os.environ.setdefault("FRONTEND_URL", "https://gen.nomadkaraoke.com")

from backend.services.email_service import EmailService, PreviewEmailProvider
from backend.services.template_service import TemplateService


def main():
    output_dir = Path("/tmp/email-previews")
    output_dir.mkdir(exist_ok=True)

    # Create email service with preview provider to capture HTML
    preview_provider = PreviewEmailProvider()
    email_service = EmailService()
    email_service.provider = preview_provider

    # Create template service for realistic message content
    template_service = TemplateService()

    print(f"Generating email previews in {output_dir}...")

    # Magic link email
    email_service.send_magic_link("user@example.com", "abc123xyz")
    save_preview(output_dir, "magic_link", preview_provider)

    # Credits added email
    email_service.send_credits_added("user@example.com", credits=5, total_credits=8)
    save_preview(output_dir, "credits_added", preview_provider)

    # Welcome email
    email_service.send_welcome_email("user@example.com", credits=3)
    save_preview(output_dir, "welcome", preview_provider)

    # Feedback request email
    email_service.send_feedback_request_email(
        "user@example.com",
        feedback_url="https://forms.example.com/feedback",
        job_title="Taylor Swift - Love Story",
    )
    save_preview(output_dir, "feedback_request", preview_provider)

    # Job completion email - use template service for realistic content
    job_completion_content = template_service.render_job_completion(
        name="there",
        youtube_url="https://www.youtube.com/watch?v=abc123",
        dropbox_url="https://www.dropbox.com/folder/example",
        artist="Taylor Swift",
        title="Love Story",
        job_id="abc123",
        feedback_url="https://forms.example.com/feedback",
    )
    email_service.send_job_completion(
        to_email="user@example.com",
        message_content=job_completion_content,
        artist="Taylor Swift",
        title="Love Story",
        brand_code="NOMAD-1234",
    )
    save_preview(output_dir, "job_completion", preview_provider)

    # Action reminder (lyrics) email - use template service for realistic content
    lyrics_reminder_content = template_service.render_action_needed_lyrics(
        name="there",
        artist="Taylor Swift",
        title="Love Story",
        review_url="https://gen.nomadkaraoke.com/lyrics/?baseApiUrl=...",
    )
    email_service.send_action_reminder(
        to_email="user@example.com",
        message_content=lyrics_reminder_content,
        action_type="lyrics",
        artist="Taylor Swift",
        title="Love Story",
    )
    save_preview(output_dir, "action_reminder", preview_provider)

    print(f"\nOpen these files in a browser to preview the emails.")
    print(f"Example: open {output_dir}/magic_link.html")


def save_preview(output_dir: Path, name: str, provider: PreviewEmailProvider):
    """Save the captured HTML to a file."""
    html = provider.get_last_html()
    if html:
        output_file = output_dir / f"{name}.html"
        output_file.write_text(html)
        print(f"  Created: {output_file}")
    else:
        print(f"  ERROR: No HTML captured for {name}")


if __name__ == "__main__":
    main()
