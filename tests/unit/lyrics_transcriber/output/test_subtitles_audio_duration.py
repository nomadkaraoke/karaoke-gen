"""SubtitlesGenerator._get_audio_duration: no-audio callers (theme previews) must not probe or log errors."""
import subprocess
from unittest.mock import MagicMock, patch

from karaoke_gen.lyrics_transcriber.output.subtitles import SubtitlesGenerator
from karaoke_gen.lyrics_transcriber.types import Word, LyricsSegment
from karaoke_gen.style_loader import DEFAULT_KARAOKE_STYLE


def _segments():
    return [
        LyricsSegment(
            id="s1",
            text="hello world",
            words=[
                Word(id="w1", text="hello", start_time=0.5, end_time=1.0),
                Word(id="w2", text="world", start_time=1.1, end_time=1.8),
            ],
            start_time=0.5,
            end_time=1.8,
        ),
    ]


def _gen(tmp_path, logger):
    return SubtitlesGenerator(
        output_dir=str(tmp_path),
        video_resolution=(1920, 1080),
        font_size=100,
        line_height=60,
        styles={"karaoke": DEFAULT_KARAOKE_STYLE},
        logger=logger,
    )


def test_none_audio_path_skips_ffprobe_and_error_log(tmp_path):
    logger = MagicMock()
    with patch("karaoke_gen.lyrics_transcriber.output.subtitles.subprocess.check_output") as probe:
        duration = _gen(tmp_path, logger)._get_audio_duration(None, _segments())
    probe.assert_not_called()
    logger.error.assert_not_called()
    assert duration == 1.8 + 30.0


def test_none_audio_path_without_segments_returns_zero(tmp_path):
    logger = MagicMock()
    assert _gen(tmp_path, logger)._get_audio_duration(None, None) == 0.0
    logger.error.assert_not_called()


def test_failed_probe_still_logs_error_and_falls_back(tmp_path):
    logger = MagicMock()
    err = subprocess.CalledProcessError(1, ["ffprobe"])
    with patch("karaoke_gen.lyrics_transcriber.output.subtitles.subprocess.check_output", side_effect=err):
        duration = _gen(tmp_path, logger)._get_audio_duration("/missing.flac", _segments())
    logger.error.assert_called_once()
    assert duration == 1.8 + 30.0


def test_generate_ass_with_no_audio(tmp_path):
    logger = MagicMock()
    with patch("karaoke_gen.lyrics_transcriber.output.subtitles.subprocess.check_output") as probe:
        path = _gen(tmp_path, logger).generate_ass(_segments(), "preview", None)
    probe.assert_not_called()
    logger.error.assert_not_called()
    assert path.endswith("preview (Karaoke).ass")
