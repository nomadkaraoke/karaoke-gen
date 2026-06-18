"""Tests for the original-audio backfill planner (backend/scripts/backfill_original_audio.py)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from backend.scripts.backfill_original_audio import plan_job_backfill


def _job(**kwargs):
    base = dict(
        job_id="job-1",
        artist="Eddie Money",
        title="I'll Get By",
        audio_source_type="audio_search",
        url=None,
        filename=None,
        input_media_gcs_path="jobs/job-1/input/track.flac",
        file_urls={},
        dropbox_path="/Karaoke/Tracks-Organized",
        state_data={"brand_code": "NOMAD-1184"},
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _services(gcs_exists=True, folder_exists=True, file_present=False):
    storage = MagicMock()
    storage.file_exists.return_value = gcs_exists
    dropbox = MagicMock()
    # plan_job_backfill calls dropbox.file_exists for the folder first, then the file.
    calls = {"n": 0}

    def _fe(_path):
        calls["n"] += 1
        return folder_exists if calls["n"] == 1 else file_present

    dropbox.file_exists.side_effect = _fe
    return storage, dropbox


class TestPlanJobBackfill:
    def test_upload_when_audio_present_and_not_yet_uploaded(self):
        storage, dropbox = _services(gcs_exists=True, folder_exists=True, file_present=False)
        action, detail = plan_job_backfill(_job(), storage=storage, dropbox=dropbox)
        assert action == "upload"
        assert detail["gcs_path"] == "jobs/job-1/input/track.flac"
        assert detail["remote_path"] == (
            "/Karaoke/Tracks-Organized/NOMAD-1184 - Eddie Money - I'll Get By/"
            "Eddie Money - I'll Get By (flacfetch).flac"
        )
        assert detail["filename"] == "Eddie Money - I'll Get By (flacfetch).flac"

    def test_skip_when_no_brand_code(self):
        storage, dropbox = _services()
        action, _ = plan_job_backfill(_job(state_data={}), storage=storage, dropbox=dropbox)
        assert action == "skip-no-dropbox"

    def test_skip_when_no_dropbox_path(self):
        storage, dropbox = _services()
        action, _ = plan_job_backfill(_job(dropbox_path=None), storage=storage, dropbox=dropbox)
        assert action == "skip-no-dropbox"

    def test_skip_when_no_audio_record(self):
        storage, dropbox = _services()
        job = _job(input_media_gcs_path=None, file_urls={})
        action, _ = plan_job_backfill(job, storage=storage, dropbox=dropbox)
        assert action == "skip-no-audio-record"

    def test_skip_when_audio_missing_from_gcs(self):
        storage, dropbox = _services(gcs_exists=False)
        action, detail = plan_job_backfill(_job(), storage=storage, dropbox=dropbox)
        assert action == "skip-audio-missing-gcs"
        assert detail == "jobs/job-1/input/track.flac"

    def test_skip_when_already_present_in_dropbox(self):
        storage, dropbox = _services(gcs_exists=True, folder_exists=True, file_present=True)
        action, _ = plan_job_backfill(_job(), storage=storage, dropbox=dropbox)
        assert action == "skip-already-present"

    def test_skip_when_track_folder_missing(self):
        storage, dropbox = _services(gcs_exists=True, folder_exists=False)
        action, detail = plan_job_backfill(_job(), storage=storage, dropbox=dropbox)
        assert action == "skip-folder-missing"
        assert detail.endswith("NOMAD-1184 - Eddie Money - I'll Get By")

    def test_sanitizes_artist_title_for_paths(self):
        storage, dropbox = _services(gcs_exists=True, folder_exists=True, file_present=False)
        job = _job(artist="AC/DC", title="T:N/T")
        action, detail = plan_job_backfill(job, storage=storage, dropbox=dropbox)
        assert action == "upload"
        # No raw slashes/colons leak into the Dropbox path beyond real separators
        tail = detail["remote_path"].split("/NOMAD-1184 - ", 1)[1]
        assert ":" not in tail
