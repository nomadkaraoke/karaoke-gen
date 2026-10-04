"""
Instrumental resolution fixes (storage-retention work, 2026-10-03):

- the GCE encoder's with-backing / clean lookups fail loudly instead of
  silently falling back to another instrumental;
- a user-supplied ("existing") instrumental recorded under uploads/ (7-day
  lifecycle) resolves to its kept job-root copy, repointing the job.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.services.gce_encoding.main import resolve_instrumental
from backend.utils.existing_instrumental import persistent_instrumental_path, resolve_existing_instrumental


def _touch(root: Path, *names):
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")


class TestEncoderLookupIsStrict:
    def test_with_backing_found(self, tmp_path):
        _touch(tmp_path, "stems/instrumental_clean.flac", "stems/instrumental_with_backing.flac")
        assert resolve_instrumental(tmp_path, {"instrumental_selection": "with_backing"}).name == \
            "instrumental_with_backing.flac"

    def test_with_backing_missing_does_not_fall_back_to_clean(self, tmp_path):
        _touch(tmp_path, "stems/instrumental_clean.flac", "custom_instrumental.flac")
        assert resolve_instrumental(tmp_path, {"instrumental_selection": "with_backing"}) is None

    def test_legacy_named_backing_file_still_found(self, tmp_path):
        _touch(tmp_path, "Artist - Title (Instrumental Backing).flac")
        assert resolve_instrumental(tmp_path, {"instrumental_selection": "with_backing"}) is not None

    def test_clean_found(self, tmp_path):
        _touch(tmp_path, "stems/instrumental_clean.flac", "stems/instrumental_with_backing.flac")
        assert resolve_instrumental(tmp_path, {"instrumental_selection": "clean"}).name == "instrumental_clean.flac"

    @pytest.mark.parametrize("other", [
        "stems/instrumental_with_backing.flac", "custom_instrumental.flac", "stems/custom_instrumental.flac",
    ])
    def test_clean_missing_does_not_fall_back(self, tmp_path, other):
        _touch(tmp_path, other)
        assert resolve_instrumental(tmp_path, {"instrumental_selection": "clean"}) is None

    def test_legacy_named_clean_file_still_found(self, tmp_path):
        _touch(tmp_path, "Artist - Title (Instrumental Clean).flac")
        assert resolve_instrumental(tmp_path, {"instrumental_selection": "clean"}) is not None


class TestExistingInstrumentalResolution:
    def _job(self, path="uploads/job1/audio/existing_instrumental.wav"):
        return SimpleNamespace(job_id="job1", existing_instrumental_gcs_path=path)

    def test_persistent_path(self):
        assert persistent_instrumental_path("job1", "uploads/job1/audio/existing_instrumental.wav") == \
            "jobs/job1/custom_instrumental.wav"
        assert persistent_instrumental_path("job1", "uploads/job1/conformed/existing_instrumental.flac") == \
            "jobs/job1/custom_instrumental.flac"

    def test_recorded_path_used_when_present(self):
        storage = MagicMock()
        storage.file_exists.return_value = True
        jm = MagicMock()
        assert resolve_existing_instrumental(self._job(), storage, jm) == "uploads/job1/audio/existing_instrumental.wav"
        jm.update_job.assert_not_called()

    def test_expired_upload_falls_back_to_staged_copy_and_repoints(self):
        storage = MagicMock()
        storage.file_exists.return_value = False
        storage.list_files.return_value = [
            "jobs/job1/input/song.flac", "jobs/job1/stems/custom_instrumental.flac",
            "jobs/job1/custom_instrumental.wav",
        ]
        jm, log = MagicMock(), MagicMock()
        assert resolve_existing_instrumental(self._job(), storage, jm, log) == "jobs/job1/custom_instrumental.wav"
        jm.update_job.assert_called_once_with("job1", {"existing_instrumental_gcs_path": "jobs/job1/custom_instrumental.wav"})
        log.warning.assert_called_once()

    def test_legacy_existing_instrumental_staged_name(self):
        storage = MagicMock()
        storage.file_exists.return_value = False
        storage.list_files.return_value = ["jobs/job1/existing_instrumental.mp3"]
        assert resolve_existing_instrumental(self._job(), storage) == "jobs/job1/existing_instrumental.mp3"

    def test_nothing_staged_keeps_recorded_path(self):
        storage = MagicMock()
        storage.file_exists.return_value = False
        storage.list_files.return_value = ["jobs/job1/stems/custom_instrumental.flac"]
        jm = MagicMock()
        assert resolve_existing_instrumental(self._job(), storage, jm) == "uploads/job1/audio/existing_instrumental.wav"
        jm.update_job.assert_not_called()

    def test_no_existing_instrumental(self):
        assert resolve_existing_instrumental(self._job(path=None), MagicMock()) is None


class TestOrchestratorStagingRepoints:
    @pytest.mark.asyncio
    async def test_staging_from_uploads_repoints_job(self):
        from backend.workers.video_worker_orchestrator import OrchestratorConfig, VideoWorkerOrchestrator

        config = OrchestratorConfig(
            job_id="job1", artist="A", title="T", title_video_path="", karaoke_video_path="",
            instrumental_audio_path="", existing_instrumental_gcs_path="uploads/job1/audio/existing_instrumental.wav",
        )
        jm, storage = MagicMock(), MagicMock()
        orch = VideoWorkerOrchestrator(config=config, job_manager=jm, storage=storage)
        backend = MagicMock()
        backend.name = "gce"
        backend.encode = MagicMock(side_effect=RuntimeError("stop after staging"))
        orch._get_encoding_backend = MagicMock(return_value=backend)

        async def boom(*args, **kwargs):
            raise RuntimeError("stop after staging")

        from unittest.mock import patch
        with patch("backend.services.encoding_service.run_with_lost_job_resubmit", side_effect=boom):
            with pytest.raises(RuntimeError):
                await orch._run_encoding()
        storage.copy_blob.assert_called_once_with(
            "uploads/job1/audio/existing_instrumental.wav", "jobs/job1/custom_instrumental.wav")
        jm.update_job.assert_any_call("job1", {"existing_instrumental_gcs_path": "jobs/job1/custom_instrumental.wav"})

    @pytest.mark.asyncio
    async def test_already_persisted_path_is_not_recopied(self):
        from unittest.mock import patch
        from backend.workers.video_worker_orchestrator import OrchestratorConfig, VideoWorkerOrchestrator

        config = OrchestratorConfig(
            job_id="job1", artist="A", title="T", title_video_path="", karaoke_video_path="",
            instrumental_audio_path="", existing_instrumental_gcs_path="jobs/job1/custom_instrumental.wav",
        )
        jm, storage = MagicMock(), MagicMock()
        orch = VideoWorkerOrchestrator(config=config, job_manager=jm, storage=storage)
        backend = MagicMock()
        backend.name = "gce"
        orch._get_encoding_backend = MagicMock(return_value=backend)

        async def boom(*args, **kwargs):
            raise RuntimeError("stop")

        with patch("backend.services.encoding_service.run_with_lost_job_resubmit", side_effect=boom):
            with pytest.raises(RuntimeError):
                await orch._run_encoding()
        storage.copy_blob.assert_not_called()
