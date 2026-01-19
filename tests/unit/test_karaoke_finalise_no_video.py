"""Tests for --no-video flag handling in KaraokeFinalise.

These tests verify that when --no-video is set:
1. Video files are not required in check_input_files_exist()
2. Artist/title are extracted from directory name instead of video filename
3. CDG/TXT generation works without video files
"""

import pytest
import os
import logging
from unittest.mock import MagicMock, patch
from karaoke_gen.karaoke_finalise.karaoke_finalise import KaraokeFinalise


@pytest.fixture
def base_finalise_kwargs():
    """Base kwargs for creating KaraokeFinalise instances."""
    return {
        "logger": MagicMock(spec=logging.Logger),
        "enable_cdg": False,
        "enable_txt": False,
        "no_video": False,
        "non_interactive": True,
        "dry_run": True,  # Don't actually do anything
    }


class TestNoVideoCheckInputFilesExist:
    """Tests for check_input_files_exist with --no-video flag."""

    def test_video_files_required_when_no_video_false(self, base_finalise_kwargs, tmp_path):
        """When no_video=False, video files should be required."""
        base_finalise_kwargs["no_video"] = False
        kf = KaraokeFinalise(**base_finalise_kwargs)

        # Create instrumental file but no video files
        instrumental = tmp_path / "Artist - Title (Instrumental).flac"
        instrumental.touch()

        original_dir = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(Exception, match="Input file .* not found"):
                kf.check_input_files_exist(
                    "Artist - Title",
                    "Artist - Title (With Vocals).mkv",  # Doesn't exist
                    str(instrumental)
                )
        finally:
            os.chdir(original_dir)

    def test_video_files_not_required_when_no_video_true(self, base_finalise_kwargs, tmp_path):
        """When no_video=True, video files should NOT be required."""
        base_finalise_kwargs["no_video"] = True
        base_finalise_kwargs["enable_cdg"] = True  # Enable CDG to require LRC
        kf = KaraokeFinalise(**base_finalise_kwargs)

        # Create only instrumental and LRC files (no video files)
        instrumental = tmp_path / "Artist - Title (Instrumental).flac"
        instrumental.touch()
        lrc_file = tmp_path / "Artist - Title (Karaoke).lrc"
        lrc_file.touch()

        original_dir = os.getcwd()
        os.chdir(tmp_path)
        try:
            # Should NOT raise an error
            input_files = kf.check_input_files_exist(
                "Artist - Title",
                None,  # No with_vocals_file
                str(instrumental)
            )
            assert "instrumental_audio" in input_files
            assert "karaoke_lrc" in input_files
            # Video-specific keys should NOT be present
            assert "with_vocals_mov" not in input_files
            assert "title_mov" not in input_files
        finally:
            os.chdir(original_dir)

    def test_lrc_still_required_for_cdg_when_no_video(self, base_finalise_kwargs, tmp_path):
        """LRC file should still be required for CDG even with --no-video."""
        base_finalise_kwargs["no_video"] = True
        base_finalise_kwargs["enable_cdg"] = True
        kf = KaraokeFinalise(**base_finalise_kwargs)

        # Create only instrumental (no LRC)
        instrumental = tmp_path / "Artist - Title (Instrumental).flac"
        instrumental.touch()

        original_dir = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(Exception, match="Input file karaoke_lrc not found"):
                kf.check_input_files_exist(
                    "Artist - Title",
                    None,
                    str(instrumental)
                )
        finally:
            os.chdir(original_dir)


class TestNoVideoProcessArtistTitleExtraction:
    """Tests for artist/title extraction in process() with --no-video flag."""

    def test_extracts_artist_title_from_directory_name(self, base_finalise_kwargs, tmp_path):
        """When no_video=True, should extract artist/title from directory name."""
        base_finalise_kwargs["no_video"] = True
        base_finalise_kwargs["enable_cdg"] = True
        base_finalise_kwargs["dry_run"] = False  # Need real processing
        base_finalise_kwargs["non_interactive"] = True
        kf = KaraokeFinalise(**base_finalise_kwargs)

        # Create a directory with artist-title format and required files
        track_dir = tmp_path / "Test Artist - Test Song"
        track_dir.mkdir()
        instrumental = track_dir / "Test Artist - Test Song (Instrumental).flac"
        instrumental.touch()
        lrc_file = track_dir / "Test Artist - Test Song (Karaoke).lrc"
        lrc_file.write_text("[00:00.00]Test lyrics")

        original_dir = os.getcwd()
        os.chdir(track_dir)
        try:
            mock_output_files = {
                "final_karaoke_cdg_zip": "test.cdg.zip",
                "final_karaoke_txt_zip": "test.txt.zip",
            }
            with patch.object(kf, 'create_cdg_zip_file'), \
                 patch.object(kf, 'validate_input_parameters_for_features'), \
                 patch.object(kf, 'prepare_output_filenames', return_value=mock_output_files):
                result = kf.process()
                assert result["artist"] == "Test Artist"
                assert result["title"] == "Test Song"
        finally:
            os.chdir(original_dir)

    def test_raises_error_for_invalid_directory_name(self, base_finalise_kwargs, tmp_path):
        """Should raise error if directory name doesn't have artist-title format."""
        base_finalise_kwargs["no_video"] = True
        kf = KaraokeFinalise(**base_finalise_kwargs)

        # Create a directory without the " - " separator
        track_dir = tmp_path / "InvalidDirectoryName"
        track_dir.mkdir()

        original_dir = os.getcwd()
        os.chdir(track_dir)
        try:
            with patch.object(kf, 'validate_input_parameters_for_features'), \
                 pytest.raises(Exception, match="Cannot extract artist/title from directory name"):
                kf.process()
        finally:
            os.chdir(original_dir)


class TestNoVideoEndToEnd:
    """End-to-end tests for --no-video workflow."""

    def test_cdg_generation_without_video(self, base_finalise_kwargs, tmp_path):
        """CDG should be generated successfully without any video files."""
        base_finalise_kwargs["no_video"] = True
        base_finalise_kwargs["enable_cdg"] = True
        base_finalise_kwargs["enable_txt"] = True
        base_finalise_kwargs["dry_run"] = False
        base_finalise_kwargs["non_interactive"] = True
        kf = KaraokeFinalise(**base_finalise_kwargs)

        # Create directory structure
        track_dir = tmp_path / "ABBA - Waterloo"
        track_dir.mkdir()

        # Create required files
        instrumental = track_dir / "ABBA - Waterloo (Instrumental model.ckpt).flac"
        instrumental.touch()
        lrc_file = track_dir / "ABBA - Waterloo (Karaoke).lrc"
        lrc_file.write_text("[00:00.00]My, my, at Waterloo Napoleon did surrender")

        original_dir = os.getcwd()
        os.chdir(track_dir)
        try:
            mock_output_files = {
                "final_karaoke_cdg_zip": "test.cdg.zip",
                "final_karaoke_txt_zip": "test.txt.zip",
            }
            with patch.object(kf, 'create_cdg_zip_file') as mock_cdg, \
                 patch.object(kf, 'create_txt_zip_file') as mock_txt, \
                 patch.object(kf, 'validate_input_parameters_for_features'), \
                 patch.object(kf, 'prepare_output_filenames', return_value=mock_output_files):
                result = kf.process()

                # Verify CDG and TXT creation were called
                mock_cdg.assert_called_once()
                mock_txt.assert_called_once()

                # Verify result has correct artist/title
                assert result["artist"] == "ABBA"
                assert result["title"] == "Waterloo"

                # Video-related values should be None when --no-video is set
                assert result.get("video_with_vocals") is None
                assert result.get("final_video") is None
        finally:
            os.chdir(original_dir)
