"""Tests for recovering from a transient Google Drive upload failure (NOMAD-1783).

A Drive 500 on the orchestrator's upload used to (1) skip retrying, (2) fire the
publish-completeness SHADOW alert before the worker's fallback Drive pass
re-uploaded everything, (3) drop the original-vocals guide from that fallback
pass, and (4) let the orchestrator's empty gdrive_files + stale warning
overwrite the recovered ids in state_data.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest
from googleapiclient.errors import HttpError

from backend.services.gdrive_service import is_transient_error
from backend.workers.video_worker import _adopt_fallback_gdrive_upload, _handle_native_distribution
from backend.workers.video_worker_orchestrator import (
    OrchestratorConfig,
    OrchestratorResult,
    VideoWorkerOrchestrator,
)


def _http_error(status: int, reason: str = "") -> HttpError:
    content = json.dumps({"error": {"errors": [{"reason": reason}]}} if reason else {"error": {}})
    return HttpError(resp=SimpleNamespace(status=status, reason="x"), content=content.encode())


class TestIsTransientError:
    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
    def test_drive_5xx_and_429_are_transient(self, status):
        assert is_transient_error(_http_error(status))

    @pytest.mark.parametrize("status", [400, 403, 404])
    def test_client_errors_are_not_transient(self, status):
        assert not is_transient_error(_http_error(status))

    @pytest.mark.parametrize("reason", ["rateLimitExceeded", "userRateLimitExceeded"])
    def test_403_rate_limit_is_transient(self, reason):
        assert is_transient_error(_http_error(403, reason))

    def test_403_permission_denied_is_not_transient(self):
        assert not is_transient_error(_http_error(403, "insufficientFilePermissions"))

    def test_connection_errors_are_transient(self):
        assert is_transient_error(BrokenPipeError())
        assert is_transient_error(ConnectionResetError())

    def test_other_errors_are_not_transient(self):
        assert not is_transient_error(ValueError("bad"))


class TestUploadFileRetriesDrive500:
    @patch("backend.services.gdrive_service.get_settings")
    def test_upload_retries_after_internal_error(self, mock_get_settings, tmp_path):
        from backend.services.gdrive_service import GoogleDriveService

        mock_get_settings.return_value = Mock(
            get_secret=Mock(return_value=json.dumps(
                {"refresh_token": "t", "client_id": "i", "client_secret": "s"}
            ))
        )
        service = GoogleDriveService()
        files_api = Mock()
        files_api.list.return_value.execute.return_value = {"files": []}
        files_api.create.return_value.execute.side_effect = [
            _http_error(500),
            {"id": "file-123"},
        ]
        drive = Mock()
        drive.files.return_value = files_api
        service._service = drive
        service._loaded = True
        service._reset_service = lambda: setattr(service, "_service", drive)

        local = tmp_path / "video.mp4"
        local.write_bytes(b"x")
        with patch("tenacity.nap.time.sleep"):
            file_id = service.upload_file(str(local), "parent", "NOMAD-1 - A - B.mp4")

        assert file_id == "file-123"
        assert files_api.create.return_value.execute.call_count == 2
        # The same-name cleanup re-runs per attempt so a half-landed create can't duplicate.
        assert files_api.list.return_value.execute.call_count == 2


class TestUploadFileNoReplaceDoesNotRetryHttpErrors:
    @patch("backend.services.gdrive_service.get_settings")
    def test_no_retry_on_500_without_same_name_cleanup(self, mock_get_settings, tmp_path):
        from backend.services.gdrive_service import GoogleDriveService

        mock_get_settings.return_value = Mock(get_secret=Mock(return_value="{}"))
        service = GoogleDriveService()
        files_api = Mock()
        files_api.create.return_value.execute.side_effect = _http_error(500)
        drive = Mock()
        drive.files.return_value = files_api
        service._service = drive
        service._loaded = True

        local = tmp_path / "video.mp4"
        local.write_bytes(b"x")
        with pytest.raises(HttpError):
            service.upload_file(str(local), "parent", "x.mp4", replace_existing=False)
        assert files_api.create.return_value.execute.call_count == 1


def _config(**kw):
    return OrchestratorConfig(
        job_id="dd885ea9",
        artist="Sezen Aksu",
        title="Hadi Bakalım",
        title_video_path="/p/title.mov",
        karaoke_video_path="/p/karaoke.mov",
        instrumental_audio_path="/p/audio.flac",
        gdrive_folder_id="folder-123",
        **kw,
    )


class TestDeferredPublishCompletenessCheck:
    @pytest.mark.asyncio
    async def test_distribution_skips_check_when_deferred(self):
        orch = VideoWorkerOrchestrator(_config())
        orch.defer_publish_completeness_check = True
        with patch.object(orch, "_upload_to_gdrive"), \
             patch.object(orch, "check_publish_completeness") as check, \
             patch.object(orch, "_stage_original_audio_for_upload"), \
             patch.object(orch, "_update_progress"):
            await orch._run_distribution()
        check.assert_not_called()

    @pytest.mark.asyncio
    async def test_distribution_runs_check_by_default(self):
        orch = VideoWorkerOrchestrator(_config())
        with patch.object(orch, "_upload_to_gdrive"), \
             patch.object(orch, "check_publish_completeness") as check, \
             patch.object(orch, "_stage_original_audio_for_upload"), \
             patch.object(orch, "_update_progress"):
            await orch._run_distribution()
        check.assert_called_once()

    def test_no_alert_once_fallback_recovered_the_upload(self):
        orch = VideoWorkerOrchestrator(_config())
        orch.result.final_karaoke_cdg_zip = "/tmp/x.zip"
        orch.result.gdrive_files = {"mp4": "a", "mp4_720p": "b", "cdg": "c"}
        with patch("backend.services.ops_alerts.send_ops_alert") as alert:
            orch.check_publish_completeness()
        alert.assert_not_called()

    def test_alerts_when_still_missing(self):
        orch = VideoWorkerOrchestrator(_config())
        orch.result.final_karaoke_cdg_zip = "/tmp/x.zip"
        with patch("backend.services.ops_alerts.send_ops_alert") as alert:
            orch.check_publish_completeness()
        alert.assert_called_once()


class TestOrchestratorKeepsGuidePath:
    @pytest.mark.asyncio
    async def test_guide_path_kept_even_when_upload_fails(self):
        orch = VideoWorkerOrchestrator(_config())
        orch.result.brand_code = "NOMAD-1783"
        gdrive = MagicMock(is_configured=True)
        gdrive.upload_to_public_share.side_effect = _http_error(500)
        with patch("backend.services.gdrive_service.get_gdrive_service", return_value=gdrive), \
             patch.object(orch, "_emit_original_vocals_guide", return_value="/tmp/guide.flac"):
            await orch._upload_to_gdrive()
        assert orch.result.original_vocals_guide == "/tmp/guide.flac"
        assert orch.result.gdrive_files == {}


class TestAdoptFallbackGdriveUpload:
    def test_adopts_recovered_ids_and_drops_stale_warning(self):
        result = OrchestratorResult(success=True, final_karaoke_cdg_zip="/t/cdg.zip")
        result.distribution_warnings = [
            "Google Drive upload failed: <HttpError 500 ...>",
            "Dropbox upload failed: boom",
        ]
        recovered = {"mp4": "a", "mp4_720p": "b", "cdg": "c"}
        _adopt_fallback_gdrive_upload(_config(), result, {"gdrive_files": recovered}, MagicMock())
        assert result.gdrive_files == recovered
        assert result.distribution_warnings == ["Dropbox upload failed: boom"]

    def test_partial_recovery_keeps_warning(self):
        result = OrchestratorResult(success=True, final_karaoke_cdg_zip="/t/cdg.zip")
        result.distribution_warnings = ["Google Drive upload failed: x"]
        partial = {"mp4": "a", "mp4_720p": "b"}
        _adopt_fallback_gdrive_upload(_config(), result, {"gdrive_files": partial}, MagicMock())
        assert result.gdrive_files == partial
        assert result.distribution_warnings == ["Google Drive upload failed: x"]

    def test_keeps_orchestrator_ids_when_it_succeeded(self):
        result = OrchestratorResult(success=True, gdrive_files={"mp4": "orig"})
        _adopt_fallback_gdrive_upload(_config(), result, {"gdrive_files": {"mp4": "orig"}}, MagicMock())
        assert result.gdrive_files == {"mp4": "orig"}

    def test_no_recovery_keeps_warning(self):
        result = OrchestratorResult(success=True)
        result.distribution_warnings = ["Google Drive upload failed: x"]
        _adopt_fallback_gdrive_upload(_config(), result, {"gdrive_files": None}, MagicMock())
        assert result.gdrive_files == {}
        assert result.distribution_warnings == ["Google Drive upload failed: x"]


class TestFallbackPassShipsGuide:
    @pytest.mark.asyncio
    async def test_fallback_upload_includes_original_vocals_guide(self, tmp_path):
        job = SimpleNamespace(
            artist="Sezen Aksu", title="Hadi Bakalım", dropbox_path=None, brand_prefix="NOMAD",
            keep_brand_code=None, gdrive_folder_id="folder-123", user_email="u@x",
        )
        gdrive = MagicMock(is_configured=True)
        gdrive.upload_to_public_share.return_value = {"mp4": "a", "mp4_720p": "b", "cdg": "c"}
        native_result = {
            "brand_code": "NOMAD-1783",
            "final_video_lossy": "/t/4k.mp4",
            "final_video_720p": "/t/720.mp4",
            "final_karaoke_cdg_zip": "/t/cdg.zip",
            "original_vocals_guide": "/t/guide.flac",
            "gdrive_files": {},
        }
        with patch("backend.services.gdrive_service.get_gdrive_service", return_value=gdrive), \
             patch("backend.services.job_defaults_service.get_effective_distribution_for_job",
                   return_value=SimpleNamespace(
                       dropbox_path=None, brand_prefix="NOMAD", gdrive_folder_id="folder-123",
                   )):
            await _handle_native_distribution(
                job_id="dd885ea9", job=job, job_log=MagicMock(), job_manager=MagicMock(),
                temp_dir=str(tmp_path), result=native_result,
            )
        output_files = gdrive.upload_to_public_share.call_args.kwargs["output_files"]
        assert output_files["original_vocals_guide"] == "/t/guide.flac"
        assert native_result["gdrive_files"] == {"mp4": "a", "mp4_720p": "b", "cdg": "c"}
