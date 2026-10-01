"""Unit tests for backend.services.published_outputs_cleanup (shared by Edit + admin re-render)."""
from unittest.mock import MagicMock, patch

from backend.services import published_outputs_cleanup as cleanup


class TestSnapshot:
    def test_only_present_published_keys(self):
        state = {"youtube_url": "https://youtu.be/a", "brand_code": "NOMAD-1", "dropbox_link": None,
                 "instrumental_selection": "clean"}
        assert cleanup.snapshot_published_outputs(state) == {"youtube_url": "https://youtu.be/a",
                                                             "brand_code": "NOMAD-1"}

    def test_none(self):
        assert cleanup.snapshot_published_outputs(None) == {}


class TestYouTube:
    def test_video_id_parsing(self):
        assert cleanup.youtube_video_id("https://www.youtube.com/watch?v=abc123&t=3") == "abc123"
        assert cleanup.youtube_video_id("https://youtu.be/xyz") == "xyz"
        assert cleanup.youtube_video_id("https://example.com") is None

    def test_skipped_without_url(self):
        assert cleanup.delete_youtube_video("j", None)["status"] == "skipped"

    def test_unparseable_url_fails(self):
        assert cleanup.delete_youtube_video("j", "https://example.com/v")["status"] == "failed"

    def _patched(self, configured=True, delete_ok=True, raises=None):
        yt = MagicMock(is_configured=configured)
        yt.get_credentials_dict.return_value = {"token": "t"}
        finalise = MagicMock()
        finalise.return_value.delete_youtube_video.return_value = delete_ok
        if raises:
            finalise.return_value.delete_youtube_video.side_effect = raises
        return (patch("backend.services.youtube_service.get_youtube_service", return_value=yt),
                patch("karaoke_gen.karaoke_finalise.karaoke_finalise.KaraokeFinalise", finalise), finalise)

    def test_deletes(self):
        p1, p2, finalise = self._patched()
        with p1, p2:
            result = cleanup.delete_youtube_video("j", "https://www.youtube.com/watch?v=abc123")
        assert result == {"status": "success", "video_id": "abc123"}
        finalise.return_value.delete_youtube_video.assert_called_once_with("abc123")

    def test_not_configured(self):
        p1, p2, _ = self._patched(configured=False)
        with p1, p2:
            assert cleanup.delete_youtube_video("j", "https://youtu.be/a")["status"] == "skipped"

    def test_error_never_raises(self):
        p1, p2, _ = self._patched(raises=RuntimeError("quotaExceeded"))
        with p1, p2:
            result = cleanup.delete_youtube_video("j", "https://youtu.be/a")
        assert result["status"] == "error" and "quotaExceeded" in result["error"]


class TestDropbox:
    def test_folder_path_matches_distribution_sanitisation(self):
        with patch("karaoke_gen.utils.sanitize_filename", side_effect=lambda s: s.replace("/", "_")):
            assert cleanup.dropbox_folder_path("/K", "NOMAD-1", "AC/DC", "T") == "/K/NOMAD-1 - AC_DC - T"

    def test_skipped_without_brand_code_or_path(self):
        assert cleanup.delete_dropbox_folder("j", None, "NOMAD-1", "A", "T")["status"] == "skipped"
        assert cleanup.delete_dropbox_folder("j", "/K", None, "A", "T")["status"] == "skipped"

    def test_deletes(self):
        dropbox = MagicMock(is_configured=True)
        dropbox.delete_folder.return_value = True
        with patch("backend.services.dropbox_service.get_dropbox_service", return_value=dropbox):
            result = cleanup.delete_dropbox_folder("j", "/K", "NOMAD-1", "Artist", "Title")
        assert result == {"status": "success", "path": "/K/NOMAD-1 - Artist - Title",
                          "deleted": ["/K/NOMAD-1 - Artist - Title"]}
        dropbox.file_exists.assert_not_called()  # names identical: no probing

    def test_error(self):
        dropbox = MagicMock(is_configured=True)
        dropbox.delete_folder.side_effect = RuntimeError("boom")
        with patch("backend.services.dropbox_service.get_dropbox_service", return_value=dropbox):
            assert cleanup.delete_dropbox_folder("j", "/K", "NOMAD-1", "A", "T")["status"] == "error"


class TestGDrive:
    def test_skipped_without_files(self):
        assert cleanup.delete_gdrive_files("j", None)["status"] == "skipped"

    def _gdrive(self, results):
        gdrive = MagicMock(is_configured=True)
        gdrive.delete_files.return_value = results
        return gdrive

    def test_deletes_and_cleans_mirror_by_default(self):
        gdrive = self._gdrive({"g1": True, "g2": True})
        with patch("backend.services.gdrive_service.get_gdrive_service", return_value=gdrive), \
             patch("backend.services.nomad_master_mirror.cleanup_nomad_masters") as mirror:
            result = cleanup.delete_gdrive_files("j", {"mp4": "g1", "mp4_720p": "g2"}, "NOMAD-1")
        assert result["status"] == "success"
        gdrive.delete_files.assert_called_once_with(["g1", "g2"])
        mirror.assert_called_once_with("NOMAD-1")

    def test_keeps_mirror_when_asked(self):
        gdrive = self._gdrive({"g1": False})
        with patch("backend.services.gdrive_service.get_gdrive_service", return_value=gdrive), \
             patch("backend.services.nomad_master_mirror.cleanup_nomad_masters") as mirror:
            result = cleanup.delete_gdrive_files("j", {"mp4": "g1"}, "NOMAD-1", cleanup_mirror=False)
        assert result["status"] == "partial"
        mirror.assert_not_called()
