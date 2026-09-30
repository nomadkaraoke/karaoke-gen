"""Guards on POST /api/jobs/{job_id}/uploads-complete (signed-URL upload flow).

- Only the job's owner (or an admin) may finalize its uploads.
- An existing-instrumental duration mismatch is first lined up with the mix
  (conformed); only if that fails is the job cancelled (and refunded) before
  returning the 400, so it can't be stranded PENDING with files.
"""
from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi import HTTPException

from backend.api.routes.file_upload import UploadsCompleteRequest, mark_uploads_complete
from backend.models.job import Job, JobStatus
from backend.services.auth_service import AuthResult, UserType


def _auth(email="owner@example.com", is_admin=False):
    return AuthResult(
        is_valid=True, user_type=UserType.UNLIMITED, remaining_uses=-1, message="OK",
        user_email=email, is_admin=is_admin,
    )


def _job():
    return Job(
        job_id="job-1", status=JobStatus.PENDING,
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
        artist="A", title="T", user_email="owner@example.com",
    )


@pytest.fixture
def mocks():
    with patch("backend.api.routes.file_upload.job_manager") as job_manager, \
         patch("backend.api.routes.file_upload.storage_service") as storage, \
         patch("backend.api.routes.file_upload.get_locale_from_request", return_value="en"):
        job_manager.get_job.return_value = _job()
        storage.list_files.side_effect = lambda prefix: {
            "uploads/job-1/audio/": ["uploads/job-1/audio/song.wav",
                                     "uploads/job-1/audio/existing_instrumental.wav"],
            "uploads/job-1/audio/existing_instrumental": ["uploads/job-1/audio/existing_instrumental.wav"],
        }.get(prefix, [])
        yield {"job_manager": job_manager, "storage": storage}


def _call(auth, files=("audio", "existing_instrumental")):
    return mark_uploads_complete(
        "job-1", Mock(headers={}), MagicMock(), UploadsCompleteRequest(uploaded_files=list(files)), auth,
    )


@pytest.mark.asyncio
async def test_non_owner_forbidden(mocks):
    with pytest.raises(HTTPException) as exc:
        await _call(_auth(email="someone-else@example.com"))
    assert exc.value.status_code == 403
    mocks["job_manager"].update_job.assert_not_called()


@pytest.mark.asyncio
async def test_email_less_token_auth_still_allowed(mocks):
    """Trusted API tokens without an email create jobs on behalf of body.user_email
    and must still be able to finalize them."""
    mocks["storage"].list_files.side_effect = lambda prefix: (
        ["uploads/job-1/audio/song.wav"] if prefix == "uploads/job-1/audio/" else []
    )
    with patch("backend.api.routes.file_upload.get_credential_manager"), \
         patch("backend.api.routes.file_upload._validate_audio_durations", new_callable=AsyncMock):
        try:
            await _call(_auth(email=None), files=("audio",))
        except HTTPException as e:
            assert e.status_code != 403


@pytest.mark.asyncio
async def test_unalignable_mismatch_cancels_job_then_400(mocks):
    with patch("backend.api.routes.file_upload._validate_audio_durations",
               new_callable=AsyncMock, return_value=(False, 200.0, 190.0)), \
         patch("backend.api.routes.file_upload._conform_mismatched_instrumental",
               new_callable=AsyncMock, return_value=None):
        with pytest.raises(HTTPException) as exc:
            await _call(_auth())

    assert exc.value.status_code == 400
    detail = exc.value.detail
    assert detail["error"] == "duration_mismatch"
    assert detail["audio_duration"] == 200.0 and detail["instrumental_duration"] == 190.0
    assert "cancelled" in detail["message"] and "190.0s" in detail["message"]
    mocks["job_manager"].cancel_job.assert_called_once()
    assert mocks["job_manager"].cancel_job.call_args[0][0] == "job-1"
    mocks["job_manager"].update_job.assert_not_called()


@pytest.mark.asyncio
async def test_matching_durations_do_not_cancel(mocks):
    with patch("backend.api.routes.file_upload._validate_audio_durations",
               new_callable=AsyncMock, return_value=(True, 200.0, 200.2)), \
         patch("backend.api.routes.file_upload.get_credential_manager"):
        await _call(_auth(email=None, is_admin=True))

    mocks["job_manager"].cancel_job.assert_not_called()
    update = mocks["job_manager"].update_job.call_args[0][1]
    assert update["existing_instrumental_gcs_path"] == "uploads/job-1/audio/existing_instrumental.wav"


@pytest.mark.asyncio
async def test_alignable_mismatch_uses_conformed_instrumental(mocks):
    conformed = "uploads/job-1/conformed/existing_instrumental.flac"
    info = {"start_trimmed_seconds": 2.974, "end_trimmed_seconds": 0.0}
    with patch("backend.api.routes.file_upload._validate_audio_durations",
               new_callable=AsyncMock, return_value=(False, 187.18, 190.16)), \
         patch("backend.api.routes.file_upload._conform_mismatched_instrumental",
               new_callable=AsyncMock, return_value=(conformed, info)) as conform, \
         patch("backend.api.routes.file_upload.get_credential_manager"):
        await _call(_auth(email=None, is_admin=True))

    conform.assert_awaited_once()
    mocks["job_manager"].cancel_job.assert_not_called()
    update = mocks["job_manager"].update_job.call_args[0][1]
    assert update["existing_instrumental_gcs_path"] == conformed
    mocks["job_manager"].update_state_data.assert_any_call("job-1", "instrumental_conformed", info)


@pytest.mark.asyncio
async def test_conform_helper_uploads_outside_audio_prefix():
    """The conformed copy must not land under uploads/{job}/audio/, where it
    would be mistaken for the mixed audio."""
    from backend.api.routes.file_upload import _conform_mismatched_instrumental
    from backend.services.instrumental_conform import ConformResult

    storage = MagicMock()
    result = ConformResult(187.18, 190.16, 2.97, 0.0, 0.8)
    with patch("backend.services.instrumental_conform.conform_instrumental", return_value=result):
        path, info = await _conform_mismatched_instrumental(
            storage, "job-1", "uploads/job-1/audio/song.wav", "uploads/job-1/audio/existing_instrumental.wav")
    assert path == "uploads/job-1/conformed/existing_instrumental.flac"
    assert storage.upload_file.call_args[0][1] == path
    assert info["original_gcs_path"] == "uploads/job-1/audio/existing_instrumental.wav"
    assert info["start_trimmed_seconds"] == 2.97


@pytest.mark.asyncio
async def test_conform_helper_returns_none_on_mismatch_or_unexpected_error():
    from backend.api.routes.file_upload import _conform_mismatched_instrumental
    from backend.services.instrumental_conform import InstrumentalConformError

    for exc in (InstrumentalConformError("wrong file"), OSError("disk full")):
        storage = MagicMock()
        with patch("backend.services.instrumental_conform.conform_instrumental", side_effect=exc):
            assert await _conform_mismatched_instrumental(
                storage, "job-1", "uploads/job-1/audio/a.wav", "uploads/job-1/audio/existing_instrumental.wav") is None
        storage.upload_file.assert_not_called()
