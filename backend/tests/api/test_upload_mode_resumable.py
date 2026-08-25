"""Tests for the opt-in resumable upload mode on create-with-upload-urls.

- upload_mode omitted / "signed_put" → signed PUT URLs (unchanged default path)
- upload_mode "resumable" → GCS resumable session URIs created with the
  caller's Origin (so GCS answers CORS on the session itself), resumable=True
"""
import pytest
from unittest.mock import Mock, patch

from backend.api.routes.file_upload import (
    CreateJobWithUploadUrlsRequest,
    FileUploadRequest,
    create_job_with_upload_urls,
)
from backend.services.auth_service import UserType, AuthResult


@pytest.fixture
def auth():
    return AuthResult(
        is_valid=True,
        user_type=UserType.UNLIMITED,
        remaining_uses=-1,
        message="OK",
        user_email="op@vocalstar.com",
        is_admin=False,
    )


def _request(origin="https://vocalstar.nomadkaraoke.com"):
    req = Mock()
    req.headers = {"Origin": origin}
    req.state = Mock()
    return req


def _body(**overrides):
    kwargs = dict(
        artist="Eddy Grant",
        title="I Don't Wanna Dance",
        files=[
            FileUploadRequest(filename="mixed.mp3", content_type="audio/mpeg", file_type="audio"),
            FileUploadRequest(filename="inst.mp3", content_type="audio/mpeg", file_type="existing_instrumental"),
        ],
        is_private=True,
        existing_instrumental=True,
    )
    kwargs.update(overrides)
    return CreateJobWithUploadUrlsRequest(**kwargs)


@pytest.fixture
def endpoint_mocks():
    """Patch the endpoint's collaborators down to URL generation."""
    with patch("backend.api.routes.file_upload.get_tenant_config_from_request", return_value=None), \
         patch("backend.api.routes.file_upload.get_locale_from_request", return_value="en"), \
         patch("backend.api.routes.file_upload.get_full_locale_from_request", return_value="en"), \
         patch("backend.api.routes.file_upload.extract_request_metadata", return_value={}), \
         patch("backend.api.routes.file_upload.get_theme_service") as theme_svc, \
         patch("backend.api.routes.file_upload.get_credential_manager") as cred_mgr, \
         patch("backend.api.routes.file_upload.get_effective_distribution_settings") as dist, \
         patch("backend.api.routes.file_upload.job_manager") as job_manager, \
         patch("backend.api.routes.file_upload.storage_service") as storage, \
         patch("backend.api.routes.file_upload.metrics"):
        theme_svc.return_value.get_default_theme_id.return_value = None
        # No distribution targets → no credential checks fire.
        dist.return_value = Mock(
            brand_prefix=None, dropbox_path=None, gdrive_folder_id=None, discord_webhook_url=None
        )
        cred_mgr.return_value = Mock()
        job_manager.create_job.return_value = Mock(job_id="job-123")
        storage.generate_signed_upload_url.return_value = "https://signed-put"
        storage.create_resumable_upload_session.return_value = "https://session-uri"
        yield {"storage": storage, "job_manager": job_manager}


@pytest.mark.asyncio
async def test_default_mode_returns_signed_put_urls(endpoint_mocks, auth):
    resp = await create_job_with_upload_urls(_request(), _body(), auth)
    assert resp.job_id == "job-123"
    assert [u.resumable for u in resp.upload_urls] == [False, False]
    assert all(u.upload_url == "https://signed-put" for u in resp.upload_urls)
    endpoint_mocks["storage"].create_resumable_upload_session.assert_not_called()


@pytest.mark.asyncio
async def test_resumable_mode_returns_session_uris_with_origin(endpoint_mocks, auth):
    resp = await create_job_with_upload_urls(
        _request(origin="https://vocalstar.nomadkaraoke.com"),
        _body(upload_mode="resumable", batch_id="batch-1"),
        auth,
    )
    assert [u.resumable for u in resp.upload_urls] == [True, True]
    assert all(u.upload_url == "https://session-uri" for u in resp.upload_urls)
    endpoint_mocks["storage"].generate_signed_upload_url.assert_not_called()
    # Sessions created with the caller's Origin so GCS handles session CORS.
    for call in endpoint_mocks["storage"].create_resumable_upload_session.call_args_list:
        assert call.kwargs["origin"] == "https://vocalstar.nomadkaraoke.com"
    # batch_id stamping still applies in resumable mode.
    endpoint_mocks["job_manager"].update_state_data.assert_any_call("job-123", "batch_id", "batch-1")


def test_upload_mode_rejects_unknown_values():
    with pytest.raises(Exception):
        _body(upload_mode="carrier-pigeon")
