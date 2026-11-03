"""create-from-url when YouTube bot-blocks the download.

Job 526bf46c (2026-11-03) failed within 3s of the first bot wall and showed the
customer the raw yt-dlp error, while the credential keeper replaces dead cookies
within minutes.
"""
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException

from backend.api.routes import file_upload
from backend.api.routes.file_upload import CreateJobFromUrlRequest, create_job_from_url
from backend.services.auth_service import AuthResult, UserType
from backend.services.youtube_download_service import (
    YouTubeDownloadError,
    is_bot_block_error,
)

BOT_WALL = (
    "Remote download failed: Download failed: ERROR: [youtube] abcdefghijk: Sign in to "
    "confirm you’re not a bot. This helps protect our community. Use "
    "--cookies-from-browser or --cookies for the authentication."
)
TRANSLATIONS = Path(file_upload.__file__).parents[2] / "translations"


async def _create(download):
    request = Mock()
    request.headers = {}
    request.client = Mock(host="127.0.0.1")
    request.url = Mock(path="/api/jobs/create-from-url")
    request.state.tenant_config = None

    service = Mock()
    service.check_availability = AsyncMock(return_value=None)
    service.download = download

    auth = AuthResult(
        is_valid=True, user_type=UserType.ADMIN, remaining_uses=-1,
        message="ok", user_email="user@example.com", is_admin=True,
    )
    body = CreateJobFromUrlRequest(url="https://www.youtube.com/watch?v=abcdefghijk", artist="A", title="T")

    with patch.object(file_upload, "job_manager") as job_manager, \
         patch.object(file_upload, "worker_service"), \
         patch.object(file_upload, "get_credential_manager"), \
         patch.object(file_upload, "get_youtube_download_service", return_value=service), \
         patch.object(file_upload.asyncio, "sleep", new=AsyncMock()) as sleep:
        job_manager.create_job.return_value = Mock(job_id="job-1")
        try:
            result = await create_job_from_url(
                request=request, background_tasks=Mock(), body=body, auth_result=auth,
            )
        except HTTPException as e:
            result = e
    return result, job_manager, sleep


@pytest.mark.asyncio
async def test_bot_block_retries_after_delay_and_succeeds():
    download = AsyncMock(side_effect=[YouTubeDownloadError(BOT_WALL), "uploads/job-1/audio/a.flac"])
    result, job_manager, sleep = await _create(download)

    assert result.job_id == "job-1"
    assert download.await_count == 2
    sleep.assert_awaited_once_with(file_upload.BOT_BLOCK_RETRY_DELAY_SECONDS)
    job_manager.fail_job.assert_not_called()


@pytest.mark.asyncio
async def test_bot_block_twice_fails_job_with_friendly_message():
    download = AsyncMock(side_effect=YouTubeDownloadError(BOT_WALL))
    result, job_manager, _ = await _create(download)

    assert download.await_count == 2
    assert isinstance(result, HTTPException)
    assert result.status_code == 500  # 503 would read as a backend blip in the frontend
    assert "Audio Search" in result.detail
    assert "cookies" not in result.detail
    assert "ERROR" not in result.detail

    job_manager.fail_job.assert_called_once()
    args = job_manager.fail_job.call_args
    assert args.args[1] == result.detail
    # Operators keep the real error.
    assert "not a bot" in args.kwargs["error_details"]["raw_error"]
    assert args.kwargs["error_details"]["code"] == "youtube_bot_block"


@pytest.mark.asyncio
async def test_retry_failing_differently_reports_the_new_error():
    download = AsyncMock(side_effect=[YouTubeDownloadError(BOT_WALL), YouTubeDownloadError("Video unavailable")])
    result, job_manager, _ = await _create(download)

    assert download.await_count == 2
    assert "Video unavailable" in result.detail
    assert "Video unavailable" in job_manager.fail_job.call_args.args[1]


@pytest.mark.asyncio
async def test_other_download_errors_fail_without_retry():
    download = AsyncMock(side_effect=YouTubeDownloadError("Video unavailable"))
    result, job_manager, sleep = await _create(download)

    assert download.await_count == 1
    sleep.assert_not_awaited()
    assert result.status_code == 500
    assert "Video unavailable" in result.detail
    assert job_manager.fail_job.call_args.args[1] == "Download failed: Video unavailable"


@pytest.mark.parametrize("message,expected", [
    (BOT_WALL, True),
    ("Sign in to confirm you're NOT A BOT", True),
    ("The provided YouTube account cookies are no longer valid.", True),
    ("Sign in to confirm your age", False),
    ("Video unavailable", False),
])
def test_is_bot_block_error(message, expected):
    assert is_bot_block_error(YouTubeDownloadError(message)) is expected


def test_friendly_message_is_translated_in_every_locale():
    locale_files = sorted(TRANSLATIONS.glob("*.json"))
    assert len(locale_files) == 33
    english = json.loads((TRANSLATIONS / "en.json").read_text())["audioSearch"]["youtubeBotBlocked"]
    for path in locale_files:
        value = json.loads(path.read_text())["audioSearch"]["youtubeBotBlocked"]
        assert value.strip(), path.name
        if path.name != "en.json":
            assert value != english, f"{path.name} is untranslated"
