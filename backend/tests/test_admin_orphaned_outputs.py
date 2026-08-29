"""
Tests for POST /api/admin/orphaned-outputs/cleanup.

This endpoint deletes distributed outputs whose owning job doc no longer exists
(a job deleted while its outputs were still published). Targets are explicit —
YouTube video ID, Dropbox folder path, GDrive file IDs, kjbox mirror filename
prefix — because there is no job doc left to resolve them from.

Origin: NOMAD-1583 duplicate incident (2026-08-29) — a job was deleted with its
outputs still live, the brand code was recycled and reused, and the orphaned files
had to be removed without any job record.
"""
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.admin import router
from backend.api.dependencies import require_admin
from backend.services.auth_service import AuthResult, UserType


def get_mock_admin():
    return AuthResult(
        is_valid=True,
        user_type=UserType.ADMIN,
        remaining_uses=999,
        message="Admin authenticated",
        user_email="admin@example.com",
        is_admin=True,
    )


app = FastAPI()
app.include_router(router, prefix="/api")
app.dependency_overrides[require_admin] = get_mock_admin


@pytest.fixture
def client():
    return TestClient(app)


URL = "/api/admin/orphaned-outputs/cleanup"


class TestOrphanedOutputsCleanupValidation:
    def test_no_targets_returns_400(self, client):
        response = client.post(URL, json={})
        assert response.status_code == 400
        assert "No cleanup targets" in response.json()["detail"]

    @pytest.mark.parametrize("prefix", ["NOMAD-1583", "NOMAD-1583 - ", "Mazzy Star"])
    def test_unsafe_mirror_prefix_returns_400_before_any_cleanup(self, client, prefix):
        """An unsafe mirror prefix fails the whole request up front — even the other
        (valid) targets must not run, so a bad call can be corrected and retried
        atomically."""
        with patch("backend.services.youtube_service.get_youtube_service") as mock_get_yt:
            response = client.post(URL, json={
                "youtube_video_id": "vid",
                "mirror_filename_prefix": prefix,
            })
        assert response.status_code == 400
        assert "mirror_filename_prefix" in response.json()["detail"]
        mock_get_yt.assert_not_called()


class TestOrphanedOutputsCleanupYouTube:
    def test_deletes_youtube_video_by_id(self, client):
        mock_finalise = MagicMock()
        mock_finalise.delete_youtube_video.return_value = True
        mock_yt = MagicMock()
        mock_yt.is_configured = True
        mock_yt.get_credentials_dict.return_value = {"token": "x"}

        with patch("karaoke_gen.karaoke_finalise.karaoke_finalise.KaraokeFinalise", return_value=mock_finalise), \
             patch("backend.services.youtube_service.get_youtube_service", return_value=mock_yt):
            response = client.post(URL, json={"youtube_video_id": "R3QZm8yXqvw"})

        assert response.status_code == 200
        assert response.json()["results"]["youtube"] == {"status": "success", "video_id": "R3QZm8yXqvw"}
        mock_finalise.delete_youtube_video.assert_called_once_with("R3QZm8yXqvw")

    def test_youtube_not_configured_reports_failed(self, client):
        mock_yt = MagicMock()
        mock_yt.is_configured = False
        with patch("backend.services.youtube_service.get_youtube_service", return_value=mock_yt):
            response = client.post(URL, json={"youtube_video_id": "abc123"})
        assert response.status_code == 200
        assert response.json()["results"]["youtube"]["status"] == "failed"

    def test_youtube_error_is_captured_not_raised(self, client):
        with patch("backend.services.youtube_service.get_youtube_service", side_effect=RuntimeError("boom")):
            response = client.post(URL, json={"youtube_video_id": "abc123"})
        assert response.status_code == 200
        assert response.json()["results"]["youtube"]["status"] == "error"


class TestOrphanedOutputsCleanupDropbox:
    def test_deletes_dropbox_folder(self, client):
        mock_dropbox = MagicMock()
        mock_dropbox.is_configured = True
        mock_dropbox.delete_folder.return_value = True
        path = "/MediaUnsynced/Karaoke/Tracks-Organized/NOMAD-1583 - Mazzy Star - Fade Into You"

        with patch("backend.services.dropbox_service.get_dropbox_service", return_value=mock_dropbox):
            response = client.post(URL, json={"dropbox_folder_path": path})

        assert response.status_code == 200
        assert response.json()["results"]["dropbox"] == {"status": "success", "path": path}
        mock_dropbox.delete_folder.assert_called_once_with(path)

    def test_dropbox_delete_failure_reported(self, client):
        mock_dropbox = MagicMock()
        mock_dropbox.is_configured = True
        mock_dropbox.delete_folder.return_value = False
        with patch("backend.services.dropbox_service.get_dropbox_service", return_value=mock_dropbox):
            response = client.post(URL, json={"dropbox_folder_path": "/x/y"})
        assert response.json()["results"]["dropbox"]["status"] == "failed"


class TestOrphanedOutputsCleanupGDrive:
    def test_deletes_gdrive_files_by_id(self, client):
        mock_gdrive = MagicMock()
        mock_gdrive.is_configured = True
        mock_gdrive.delete_files.return_value = {"id1": True, "id2": True}

        with patch("backend.services.gdrive_service.get_gdrive_service", return_value=mock_gdrive):
            response = client.post(URL, json={"gdrive_file_ids": ["id1", "id2"]})

        assert response.status_code == 200
        assert response.json()["results"]["gdrive"]["status"] == "success"
        mock_gdrive.delete_files.assert_called_once_with(["id1", "id2"])

    def test_partial_gdrive_failure_reported(self, client):
        mock_gdrive = MagicMock()
        mock_gdrive.is_configured = True
        mock_gdrive.delete_files.return_value = {"id1": True, "id2": False}
        with patch("backend.services.gdrive_service.get_gdrive_service", return_value=mock_gdrive):
            response = client.post(URL, json={"gdrive_file_ids": ["id1", "id2"]})
        assert response.json()["results"]["gdrive"]["status"] == "partial"


class TestOrphanedOutputsCleanupMirror:
    def test_deletes_mirror_objects_by_track_prefix(self, client):
        mock_mirror = MagicMock()
        mock_mirror.delete_track_objects_by_filename_prefix.return_value = {
            "masters_deleted": 1, "vocals_guides_deleted": 1,
        }
        with patch("backend.services.nomad_master_mirror.NomadMasterMirror", return_value=mock_mirror):
            response = client.post(URL, json={"mirror_filename_prefix": "NOMAD-1583 - Mazzy Star"})

        assert response.status_code == 200
        assert response.json()["results"]["mirror"] == {
            "status": "success", "masters_deleted": 1, "vocals_guides_deleted": 1,
        }
        mock_mirror.delete_track_objects_by_filename_prefix.assert_called_once_with("NOMAD-1583 - Mazzy Star")


class TestOrphanedOutputsCleanupCombined:
    def test_all_targets_cleaned_in_one_call(self, client):
        mock_finalise = MagicMock()
        mock_finalise.delete_youtube_video.return_value = True
        mock_yt = MagicMock(is_configured=True)
        mock_yt.get_credentials_dict.return_value = {}
        mock_dropbox = MagicMock(is_configured=True)
        mock_dropbox.delete_folder.return_value = True
        mock_gdrive = MagicMock(is_configured=True)
        mock_gdrive.delete_files.return_value = {"id1": True}
        mock_mirror = MagicMock()
        mock_mirror.delete_track_objects_by_filename_prefix.return_value = {
            "masters_deleted": 1, "vocals_guides_deleted": 0,
        }

        with patch("karaoke_gen.karaoke_finalise.karaoke_finalise.KaraokeFinalise", return_value=mock_finalise), \
             patch("backend.services.youtube_service.get_youtube_service", return_value=mock_yt), \
             patch("backend.services.dropbox_service.get_dropbox_service", return_value=mock_dropbox), \
             patch("backend.services.gdrive_service.get_gdrive_service", return_value=mock_gdrive), \
             patch("backend.services.nomad_master_mirror.NomadMasterMirror", return_value=mock_mirror):
            response = client.post(URL, json={
                "youtube_video_id": "vid",
                "dropbox_folder_path": "/a/b",
                "gdrive_file_ids": ["id1"],
                "mirror_filename_prefix": "NOMAD-1583 - Mazzy Star",
            })

        assert response.status_code == 200
        results = response.json()["results"]
        assert {results[k]["status"] for k in ("youtube", "dropbox", "gdrive", "mirror")} == {"success"}
