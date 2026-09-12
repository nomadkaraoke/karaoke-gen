"""Unit tests for the publish-boundary completeness invariant (incident-hardening G1).

These pin the exact behaviour that closes the NOMAD-1632 silent-partial class: a
public GDrive release missing its 720p variant (or any expected output) must be
reported as a shortfall, while a complete release — and any non-public job —
reports nothing.
"""

from types import SimpleNamespace

from backend.services.publish_completeness import (
    compute_publish_shortfall,
    expected_gdrive_outputs,
)


def _config(gdrive_folder_id="folder-1", enable_cdg=True):
    return SimpleNamespace(
        gdrive_folder_id=gdrive_folder_id,
        enable_cdg=enable_cdg,
        artist="Nat King Cole",
        title="Portrait of Jennie",
    )


def _result(gdrive_files, cdg_produced=True):
    """A result whose CDG was produced by default (final_karaoke_cdg_zip set)."""
    return SimpleNamespace(
        gdrive_files=gdrive_files,
        brand_code="NOMAD-1632",
        final_karaoke_cdg_zip=("/tmp/x.zip" if cdg_produced else None),
    )


class TestExpectedOutputs:
    def test_cdg_produced_expects_all_three(self):
        res = _result({}, cdg_produced=True)
        assert expected_gdrive_outputs(_config(enable_cdg=True), res) == ["mp4", "mp4_720p", "cdg"]

    def test_cdg_not_produced_expects_two(self):
        res = _result({}, cdg_produced=False)
        assert expected_gdrive_outputs(_config(enable_cdg=True), res) == ["mp4", "mp4_720p"]


class TestComputePublishShortfall:
    def test_complete_release_no_shortfall(self):
        cfg = _config(enable_cdg=True)
        res = _result({"mp4": "id1", "mp4_720p": "id2", "cdg": "id3"})
        assert compute_publish_shortfall(cfg, res) == []

    def test_missing_720p_is_the_incident(self):
        """The NOMAD-1632 case: 4K + CDG shipped, 720p silently skipped."""
        cfg = _config(enable_cdg=True)
        res = _result({"mp4": "id1", "cdg": "id3"})
        assert compute_publish_shortfall(cfg, res) == ["720p MP4"]

    def test_missing_cdg_when_produced(self):
        """CDG was produced but not distributed → real shortfall."""
        cfg = _config(enable_cdg=True)
        res = _result({"mp4": "id1", "mp4_720p": "id2"}, cdg_produced=True)
        assert compute_publish_shortfall(cfg, res) == ["CDG zip"]

    def test_lyricless_skip_does_not_require_cdg(self):
        """enable_cdg True but no CDG produced (lyric-less track) → no false alarm."""
        cfg = _config(enable_cdg=True)
        res = _result({"mp4": "id1", "mp4_720p": "id2"}, cdg_produced=False)
        assert compute_publish_shortfall(cfg, res) == []

    def test_cdg_not_expected_when_disabled(self):
        cfg = _config(enable_cdg=False)
        res = _result({"mp4": "id1", "mp4_720p": "id2"}, cdg_produced=False)
        assert compute_publish_shortfall(cfg, res) == []

    def test_empty_distribution_lists_all_expected(self):
        cfg = _config(enable_cdg=True)
        res = _result({}, cdg_produced=True)
        assert compute_publish_shortfall(cfg, res) == ["lossy 4K MP4", "720p MP4", "CDG zip"]

    def test_empty_string_id_counts_as_missing(self):
        cfg = _config(enable_cdg=False)
        res = _result({"mp4": "id1", "mp4_720p": ""}, cdg_produced=False)
        assert compute_publish_shortfall(cfg, res) == ["720p MP4"]

    def test_non_public_job_never_flags(self):
        """No gdrive_folder_id → not a public-share release → always clean."""
        cfg = _config(gdrive_folder_id=None, enable_cdg=True)
        res = _result({})
        assert compute_publish_shortfall(cfg, res) == []

    def test_none_gdrive_files_is_safe(self):
        cfg = _config(enable_cdg=False)
        res = _result(None, cdg_produced=False)
        assert compute_publish_shortfall(cfg, res) == ["lossy 4K MP4", "720p MP4"]
