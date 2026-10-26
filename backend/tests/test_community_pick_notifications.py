"""Review/selection emails for requests-board (community) picks."""
from unittest.mock import MagicMock, patch

from backend.services.email_service import EmailService
from backend.services.job_notification_service import JobNotificationService


def test_note_prepended_only_for_community_jobs():
    svc = MagicMock()
    svc.get_by_job_id.return_value = object()
    with patch("backend.services.song_request_service.get_song_request_service", return_value=svc):
        note = JobNotificationService._community_pick_note("job1", "en")
    assert "requests board" in note and note.endswith("\n\n")

    svc.get_by_job_id.return_value = None
    with patch("backend.services.song_request_service.get_song_request_service", return_value=svc):
        assert JobNotificationService._community_pick_note("job1", "en") == ""


def test_note_lookup_failure_never_blocks_the_email():
    with patch("backend.services.song_request_service.get_song_request_service", side_effect=RuntimeError("db")):
        assert JobNotificationService._community_pick_note("job1", "en") == ""


def test_audio_selection_email_links_to_the_job():
    email = EmailService.__new__(EmailService)
    email.frontend_url = "https://gen.example.com"
    email._build_email_html = MagicMock(return_value="<html></html>")
    email._log_and_send = MagicMock(return_value=True)
    assert email.send_community_audio_selection_needed("fan@x.com", "Raynes", "Lemon Drop", "job9") is True
    kwargs = email._log_and_send.call_args.kwargs
    assert kwargs["to_email"] == "fan@x.com"
    assert "Raynes - Lemon Drop" in kwargs["subject"]
    assert "https://gen.example.com/en/app/jobs#/job9" in kwargs["text_content"]
