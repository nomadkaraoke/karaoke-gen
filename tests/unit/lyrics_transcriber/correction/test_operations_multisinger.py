"""Regression tests: multi-singer fields must survive the review→render pipeline.

Two bugs caught during manual validation of the duet feature:

1. `CorrectionOperations.update_correction_result_with_data` rebuilt each
   `Word` / `LyricsSegment` without forwarding the new `singer` field, so the
   renderer always saw `segment.singer=None` / `word.singer=None` even when the
   frontend sent them set.

2. `CorrectionOperations.generate_preview_video` constructed its internal
   `preview_config = OutputConfig(...)` without threading `is_duet` — so the
   preview render always ran in solo mode regardless of the flag in the outer
   config or request body.

Both failures showed up in the Preview Video output rendering every singer
in Singer 1 blue, even though the review UI correctly marked segments as
Singer 2 / Both.
"""
from unittest.mock import MagicMock

from karaoke_gen.lyrics_transcriber.correction.operations import CorrectionOperations
from karaoke_gen.lyrics_transcriber.core.config import OutputConfig
from karaoke_gen.lyrics_transcriber.types import (
    CorrectionResult,
    LyricsSegment,
    Word,
)


def _empty_correction_result() -> CorrectionResult:
    return CorrectionResult(
        corrections=[],
        corrected_segments=[],
        original_segments=[],
        corrections_made=0,
        confidence=1.0,
        reference_lyrics={},
        anchor_sequences=[],
        gap_sequences=[],
        resized_segments=None,
        metadata={},
        correction_steps=[],
        word_id_map={},
        segment_id_map={},
    )


class TestUpdateCorrectionResultWithDataPreservesSinger:
    def test_segment_singer_is_forwarded(self):
        updated_data = {
            "corrections": [],
            "corrected_segments": [
                {
                    "id": "s1",
                    "text": "hello world",
                    "start_time": 0.0,
                    "end_time": 1.0,
                    "singer": 2,
                    "words": [
                        {"id": "w1", "text": "hello", "start_time": 0.0, "end_time": 0.5},
                        {"id": "w2", "text": "world", "start_time": 0.5, "end_time": 1.0},
                    ],
                }
            ],
        }
        result = CorrectionOperations.update_correction_result_with_data(
            _empty_correction_result(), updated_data
        )
        assert result.corrected_segments[0].singer == 2

    def test_word_level_singer_override_is_forwarded(self):
        updated_data = {
            "corrections": [],
            "corrected_segments": [
                {
                    "id": "s1",
                    "text": "hello world",
                    "start_time": 0.0,
                    "end_time": 1.0,
                    "singer": 1,
                    "words": [
                        {"id": "w1", "text": "hello", "start_time": 0.0, "end_time": 0.5},
                        {
                            "id": "w2",
                            "text": "world",
                            "start_time": 0.5,
                            "end_time": 1.0,
                            "singer": 2,
                        },
                    ],
                }
            ],
        }
        result = CorrectionOperations.update_correction_result_with_data(
            _empty_correction_result(), updated_data
        )
        words = result.corrected_segments[0].words
        assert words[0].singer is None
        assert words[1].singer == 2

    def test_absent_singer_defaults_to_none(self):
        """Solo path: no singer field present anywhere — result has None, not a crash."""
        updated_data = {
            "corrections": [],
            "corrected_segments": [
                {
                    "id": "s1",
                    "text": "hi",
                    "start_time": 0.0,
                    "end_time": 0.5,
                    "words": [
                        {"id": "w1", "text": "hi", "start_time": 0.0, "end_time": 0.5}
                    ],
                }
            ],
        }
        result = CorrectionOperations.update_correction_result_with_data(
            _empty_correction_result(), updated_data
        )
        seg = result.corrected_segments[0]
        assert seg.singer is None
        assert seg.words[0].singer is None


class TestGeneratePreviewVideoThreadsIsDuet:
    """generate_preview_video's inner preview_config must receive is_duet.

    We stub the OutputGenerator to capture the config actually used for
    rendering so we can assert is_duet survives the rebuild.
    """

    def _run(self, monkeypatch, tmp_path, updated_data, outer_is_duet=False):
        captured = {}

        class _StubGenerator:
            def __init__(self, config, preview_mode=False, **kwargs):
                captured["config"] = config

            def generate_outputs(self, *args, **kwargs):
                # Return something truthy so the operation can compute the hash
                output = MagicMock()
                output.video = str(tmp_path / "preview.mp4")
                output.ass = str(tmp_path / "preview.ass")
                return output

        monkeypatch.setattr(
            "karaoke_gen.lyrics_transcriber.correction.operations.OutputGenerator",
            _StubGenerator,
        )

        outer_config = OutputConfig(
            output_styles_json="",
            output_dir=str(tmp_path),
            cache_dir=str(tmp_path),
            is_duet=outer_is_duet,
        )

        # Create the previews dir the operation writes to
        (tmp_path / "previews").mkdir(exist_ok=True)

        CorrectionOperations.generate_preview_video(
            correction_result=_empty_correction_result(),
            updated_data=updated_data,
            output_config=outer_config,
            audio_filepath=str(tmp_path / "fake.wav"),
            logger=MagicMock(),
            ass_only=True,  # Skip the ffmpeg path that would need a real audio file
        )
        return captured["config"]

    def test_is_duet_from_updated_data_reaches_preview_config(self, monkeypatch, tmp_path):
        (tmp_path / "fake.wav").write_bytes(b"")
        cfg = self._run(
            monkeypatch,
            tmp_path,
            updated_data={
                "corrections": [],
                "corrected_segments": [],
                "is_duet": True,
            },
            outer_is_duet=False,
        )
        assert cfg.is_duet is True

    def test_is_duet_from_outer_config_preserved_when_body_omits(self, monkeypatch, tmp_path):
        (tmp_path / "fake.wav").write_bytes(b"")
        cfg = self._run(
            monkeypatch,
            tmp_path,
            updated_data={
                "corrections": [],
                "corrected_segments": [],
                # no is_duet in body
            },
            outer_is_duet=True,
        )
        assert cfg.is_duet is True

    def test_solo_default(self, monkeypatch, tmp_path):
        (tmp_path / "fake.wav").write_bytes(b"")
        cfg = self._run(
            monkeypatch,
            tmp_path,
            updated_data={"corrections": [], "corrected_segments": []},
            outer_is_duet=False,
        )
        assert cfg.is_duet is False
