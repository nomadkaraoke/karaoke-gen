"""
Tests for post-creation style upload endpoints.

Tests:
- POST /api/jobs/{job_id}/style-upload-urls
- POST /api/jobs/{job_id}/style-uploads-complete
"""
import pytest
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime, UTC

# Mock Firestore and GCS before importing
import sys
sys.modules['google.cloud.firestore'] = MagicMock()
sys.modules['google.cloud.storage'] = MagicMock()

from backend.api.routes.file_upload import (
    STYLE_FILE_TYPES,
    STYLE_UPLOAD_CUTOFF_STATUSES,
    StyleUploadUrlsRequest,
    StyleUploadsCompleteRequest,
    FileUploadRequest,
    get_style_upload_urls,
    complete_style_uploads,
)
from backend.models.job import Job, JobStatus
from backend.services.auth_service import UserType, AuthResult


@pytest.fixture
def mock_job_manager():
    with patch('backend.api.routes.file_upload.job_manager') as mock:
        yield mock


@pytest.fixture
def mock_storage_service():
    with patch('backend.api.routes.file_upload.storage_service') as mock:
        mock.bucket_name = "test-bucket"
        yield mock


@pytest.fixture
def admin_auth():
    return AuthResult(
        is_valid=True,
        user_type=UserType.ADMIN,
        remaining_uses=-1,
        message="OK",
        user_email="admin@test.com",
        is_admin=True,
    )


@pytest.fixture
def user_auth():
    return AuthResult(
        is_valid=True,
        user_type=UserType.LIMITED,
        remaining_uses=5,
        message="OK",
        user_email="user@test.com",
        is_admin=False,
    )


@pytest.fixture
def pending_job():
    return Job(
        job_id="job-abc",
        status=JobStatus.PENDING,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        artist="Test Artist",
        title="Test Song",
        user_email="user@test.com",
        theme_id="nomad",
        style_assets={"font": "themes/nomad/assets/font.ttf"},
    )


class TestStyleFileTypesConfig:
    """Test the STYLE_FILE_TYPES and cutoff status config."""

    def test_style_file_types_are_subset_of_valid(self):
        from backend.api.routes.file_upload import VALID_FILE_TYPES
        for ft in STYLE_FILE_TYPES:
            assert ft in VALID_FILE_TYPES

    def test_cutoff_includes_terminal_states(self):
        assert JobStatus.COMPLETE in STYLE_UPLOAD_CUTOFF_STATUSES
        assert JobStatus.FAILED in STYLE_UPLOAD_CUTOFF_STATUSES
        assert JobStatus.CANCELLED in STYLE_UPLOAD_CUTOFF_STATUSES

    def test_cutoff_includes_generating_screens(self):
        assert JobStatus.GENERATING_SCREENS in STYLE_UPLOAD_CUTOFF_STATUSES

    def test_cutoff_excludes_early_states(self):
        assert JobStatus.PENDING not in STYLE_UPLOAD_CUTOFF_STATUSES
        assert JobStatus.DOWNLOADING not in STYLE_UPLOAD_CUTOFF_STATUSES
        assert JobStatus.SEPARATING_STAGE1 not in STYLE_UPLOAD_CUTOFF_STATUSES
        assert JobStatus.TRANSCRIBING not in STYLE_UPLOAD_CUTOFF_STATUSES


class TestGetStyleUploadUrls:
    """Test POST /api/jobs/{job_id}/style-upload-urls."""

    @pytest.mark.asyncio
    async def test_returns_signed_urls_for_valid_request(
        self, mock_job_manager, mock_storage_service, admin_auth, pending_job
    ):
        mock_job_manager.get_job.return_value = pending_job
        mock_storage_service.generate_signed_upload_url.return_value = "https://signed-url.example.com"

        body = StyleUploadUrlsRequest(files=[
            FileUploadRequest(
                filename="bg.png",
                content_type="image/png",
                file_type="style_karaoke_background",
            )
        ])

        result = await get_style_upload_urls("job-abc", body, admin_auth)

        assert result["status"] == "success"
        assert len(result["upload_urls"]) == 1
        assert result["upload_urls"][0]["file_type"] == "style_karaoke_background"
        mock_storage_service.generate_signed_upload_url.assert_called_once()

    @pytest.mark.asyncio
    async def test_rejects_non_style_file_types(
        self, mock_job_manager, mock_storage_service, admin_auth, pending_job
    ):
        mock_job_manager.get_job.return_value = pending_job

        body = StyleUploadUrlsRequest(files=[
            FileUploadRequest(
                filename="song.mp3",
                content_type="audio/mpeg",
                file_type="audio",
            )
        ])

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await get_style_upload_urls("job-abc", body, admin_auth)
        assert exc_info.value.status_code == 400
        assert "Invalid file_type for style upload" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_rejects_job_not_found(
        self, mock_job_manager, mock_storage_service, admin_auth
    ):
        mock_job_manager.get_job.return_value = None

        body = StyleUploadUrlsRequest(files=[
            FileUploadRequest(
                filename="bg.png",
                content_type="image/png",
                file_type="style_karaoke_background",
            )
        ])

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await get_style_upload_urls("nonexistent", body, admin_auth)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_rejects_past_cutoff_status(
        self, mock_job_manager, mock_storage_service, admin_auth, pending_job
    ):
        pending_job.status = JobStatus.GENERATING_SCREENS
        mock_job_manager.get_job.return_value = pending_job

        body = StyleUploadUrlsRequest(files=[
            FileUploadRequest(
                filename="bg.png",
                content_type="image/png",
                file_type="style_karaoke_background",
            )
        ])

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await get_style_upload_urls("job-abc", body, admin_auth)
        assert exc_info.value.status_code == 400
        assert "cutoff" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_rejects_wrong_user(
        self, mock_job_manager, mock_storage_service, user_auth, pending_job
    ):
        pending_job.user_email = "other@test.com"
        mock_job_manager.get_job.return_value = pending_job

        body = StyleUploadUrlsRequest(files=[
            FileUploadRequest(
                filename="bg.png",
                content_type="image/png",
                file_type="style_karaoke_background",
            )
        ])

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await get_style_upload_urls("job-abc", body, user_auth)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_rejects_invalid_extension(
        self, mock_job_manager, mock_storage_service, admin_auth, pending_job
    ):
        mock_job_manager.get_job.return_value = pending_job

        body = StyleUploadUrlsRequest(files=[
            FileUploadRequest(
                filename="bg.bmp",
                content_type="image/bmp",
                file_type="style_karaoke_background",
            )
        ])

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await get_style_upload_urls("job-abc", body, admin_auth)
        assert exc_info.value.status_code == 400
        assert "extension" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_accepts_multiple_files(
        self, mock_job_manager, mock_storage_service, admin_auth, pending_job
    ):
        mock_job_manager.get_job.return_value = pending_job
        mock_storage_service.generate_signed_upload_url.return_value = "https://signed.example.com"

        body = StyleUploadUrlsRequest(files=[
            FileUploadRequest(filename="karaokeimg.png", content_type="image/png", file_type="style_karaoke_background"),
            FileUploadRequest(filename="introimg.jpg", content_type="image/jpeg", file_type="style_intro_background"),
        ])

        result = await get_style_upload_urls("job-abc", body, admin_auth)
        assert len(result["upload_urls"]) == 2


class TestCompleteStyleUploads:
    """Test POST /api/jobs/{job_id}/style-uploads-complete."""

    @pytest.mark.asyncio
    async def test_merges_assets_and_updates_job(
        self, mock_job_manager, mock_storage_service, admin_auth, pending_job
    ):
        mock_job_manager.get_job.return_value = pending_job
        mock_storage_service.list_files.return_value = ["jobs/job-abc/style/karaoke_background.png"]

        # Mock theme service
        with patch('backend.api.routes.file_upload.get_theme_service') as mock_get_ts:
            mock_ts = Mock()
            mock_ts.get_theme_style_params.return_value = {
                "intro": {"background_image": "gs://bucket/themes/nomad/intro.png"},
                "karaoke": {"background_image": "gs://bucket/themes/nomad/karaoke.png"},
                "end": {"background_image": "gs://bucket/themes/nomad/end.png"},
            }
            mock_ts.apply_color_overrides.return_value = mock_ts.get_theme_style_params.return_value
            mock_ts._update_asset_paths_in_style.return_value = mock_ts.get_theme_style_params.return_value
            mock_get_ts.return_value = mock_ts

            body = StyleUploadsCompleteRequest(
                uploaded_files=["style_karaoke_background"],
            )

            result = await complete_style_uploads("job-abc", body, admin_auth)

        assert result["status"] == "success"
        assert "karaoke_background" in result["assets_updated"]

        # Check job was updated with merged assets
        update_call = mock_job_manager.update_job.call_args
        assert update_call is not None
        update_data = update_call[0][1]
        assert "karaoke_background" in update_data["style_assets"]
        # Original font should be preserved
        assert "font" in update_data["style_assets"]

    @pytest.mark.asyncio
    async def test_applies_color_overrides(
        self, mock_job_manager, mock_storage_service, admin_auth, pending_job
    ):
        mock_job_manager.get_job.return_value = pending_job
        mock_storage_service.list_files.return_value = ["jobs/job-abc/style/intro_background.png"]

        with patch('backend.api.routes.file_upload.get_theme_service') as mock_get_ts:
            mock_ts = Mock()
            mock_ts.get_theme_style_params.return_value = {
                "intro": {"background_image": "old.png", "artist_color": "#ffdf6b"},
                "karaoke": {},
                "end": {},
            }
            # apply_color_overrides should be called with the overrides
            mock_ts.apply_color_overrides.return_value = {
                "intro": {"background_image": "old.png", "artist_color": "#ff0000"},
                "karaoke": {},
                "end": {},
            }
            mock_ts._update_asset_paths_in_style.side_effect = lambda tid, sp: sp
            mock_get_ts.return_value = mock_ts

            body = StyleUploadsCompleteRequest(
                uploaded_files=["style_intro_background"],
                color_overrides={"artist_color": "#ff0000"},
            )

            result = await complete_style_uploads("job-abc", body, admin_auth)

        assert result["status"] == "success"
        mock_ts.apply_color_overrides.assert_called_once()
        # Verify style_params.json was uploaded
        mock_storage_service.upload_json.assert_called_once()

    @pytest.mark.asyncio
    async def test_rejects_if_file_not_in_gcs(
        self, mock_job_manager, mock_storage_service, admin_auth, pending_job
    ):
        mock_job_manager.get_job.return_value = pending_job
        mock_storage_service.list_files.return_value = []  # File not found

        body = StyleUploadsCompleteRequest(
            uploaded_files=["style_karaoke_background"],
        )

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await complete_style_uploads("job-abc", body, admin_auth)
        assert exc_info.value.status_code == 400
        assert "not uploaded" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_rejects_past_cutoff(
        self, mock_job_manager, mock_storage_service, admin_auth, pending_job
    ):
        pending_job.status = JobStatus.COMPLETE
        mock_job_manager.get_job.return_value = pending_job

        body = StyleUploadsCompleteRequest(uploaded_files=["style_karaoke_background"])

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await complete_style_uploads("job-abc", body, admin_auth)
        assert exc_info.value.status_code == 400
