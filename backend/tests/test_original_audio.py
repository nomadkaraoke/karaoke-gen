"""Tests for original-audio naming/location helpers (backend/services/original_audio.py).

These back the regression fix that restores the original input audio
(e.g. '<artist> - <title> (flacfetch).flac') to the Dropbox track folder.
"""

from types import SimpleNamespace

from backend.services.original_audio import (
    original_audio_source_tag,
    original_audio_gcs_path,
    original_audio_output_filename,
)


def _job(**kwargs):
    base = dict(
        audio_source_type=None,
        url=None,
        filename=None,
        input_media_gcs_path=None,
        file_urls={},
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


class TestSourceTag:
    def test_file_upload_maps_to_uploaded(self):
        assert original_audio_source_tag(_job(audio_source_type="file_upload")) == "uploaded"

    def test_youtube_url_maps_to_youtube(self):
        assert original_audio_source_tag(_job(audio_source_type="youtube_url")) == "YouTube"

    def test_audio_search_maps_to_flacfetch(self):
        assert original_audio_source_tag(_job(audio_source_type="audio_search")) == "flacfetch"

    def test_legacy_url_job_without_source_type(self):
        assert original_audio_source_tag(_job(url="https://youtu.be/x")) == "YouTube"

    def test_legacy_upload_without_source_type(self):
        assert original_audio_source_tag(_job(filename="song.mp3")) == "uploaded"

    def test_unknown_falls_back_to_input(self):
        assert original_audio_source_tag(_job()) == "input"


class TestGcsPath:
    def test_prefers_input_media_gcs_path(self):
        job = _job(input_media_gcs_path="jobs/j1/input/song.mp3",
                   file_urls={"input": "jobs/j1/other.flac"})
        assert original_audio_gcs_path(job) == "jobs/j1/input/song.mp3"

    def test_file_urls_input_as_string(self):
        job = _job(file_urls={"input": "jobs/j1/input.flac"})
        assert original_audio_gcs_path(job) == "jobs/j1/input.flac"

    def test_file_urls_input_as_dict(self):
        job = _job(file_urls={"input": {"audio": "jobs/j1/input/song.flac"}})
        assert original_audio_gcs_path(job) == "jobs/j1/input/song.flac"

    def test_none_when_no_source(self):
        assert original_audio_gcs_path(_job()) is None


class TestOutputFilename:
    def test_flacfetch_flac(self):
        job = _job(audio_source_type="audio_search",
                   input_media_gcs_path="jobs/j1/input/track.flac")
        assert original_audio_output_filename(job, "Artist - Title") == "Artist - Title (flacfetch).flac"

    def test_uploaded_mp3(self):
        job = _job(audio_source_type="file_upload",
                   input_media_gcs_path="jobs/j1/input/My Song.mp3")
        assert original_audio_output_filename(job, "Artist - Title") == "Artist - Title (uploaded).mp3"

    def test_none_when_no_audio(self):
        assert original_audio_output_filename(_job(), "Artist - Title") is None
