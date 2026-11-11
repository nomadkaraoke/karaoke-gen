"""The review preview gets the same countdown intro as the final render.

Before this, a song whose vocals start within 3s showed a countdown in the
final video (CountdownProcessor in the encoding worker) but none in the review
preview, so reviewers asked for a countdown that was already coming.
"""
from unittest.mock import MagicMock

import pytest

from karaoke_gen.lyrics_transcriber.correction.operations import CorrectionOperations
from karaoke_gen.lyrics_transcriber.core.config import OutputConfig
from karaoke_gen.lyrics_transcriber.output.countdown_processor import CountdownProcessor
from tests.unit.lyrics_transcriber.correction.test_operations_multisinger import _empty_correction_result


def _updated_data(first_word_start: float) -> dict:
    return {
        "corrections": [],
        "corrected_segments": [{
            "id": "s1", "text": "hello", "start_time": first_word_start, "end_time": first_word_start + 0.5,
            "words": [{"id": "w1", "text": "hello", "start_time": first_word_start,
                       "end_time": first_word_start + 0.5}],
        }],
    }


@pytest.fixture
def run_preview(monkeypatch, tmp_path):
    captured = {}

    class _StubGenerator:
        def __init__(self, config, preview_mode=False, **kwargs):
            pass

        def generate_outputs(self, transcription_corrected, audio_filepath, **kwargs):
            captured["correction"] = transcription_corrected
            captured["audio"] = audio_filepath
            out = MagicMock()
            out.video = str(tmp_path / "preview.mp4")
            out.ass = str(tmp_path / "preview.ass")
            return out

    monkeypatch.setattr(
        "karaoke_gen.lyrics_transcriber.correction.operations.OutputGenerator", _StubGenerator
    )
    padded = str(tmp_path / "audio_padded.flac")
    pad_calls = []
    monkeypatch.setattr(
        CountdownProcessor, "create_padded_audio_only",
        lambda self, path: pad_calls.append(path) or padded,
    )
    (tmp_path / "previews").mkdir(exist_ok=True)
    cfg = OutputConfig(output_styles_json="", output_dir=str(tmp_path), cache_dir=str(tmp_path))

    def _run(updated_data, ass_only):
        result = CorrectionOperations.generate_preview_video(
            correction_result=_empty_correction_result(),
            updated_data=updated_data,
            output_config=cfg,
            audio_filepath=str(tmp_path / "song.flac"),
            logger=MagicMock(),
            ass_only=ass_only,
        )
        return result, captured, pad_calls, padded

    return _run


def test_early_vocals_get_countdown_in_ass_only_preview(run_preview):
    result, captured, pad_calls, _ = run_preview(_updated_data(0.4), ass_only=True)
    assert result["countdown_padding_seconds"] == 3.0
    segs = captured["correction"].corrected_segments
    assert segs[0].text == CountdownProcessor.COUNTDOWN_TEXT
    assert segs[1].words[0].start_time == pytest.approx(3.4)
    # ass_only callers pad the encoder's audio themselves.
    assert pad_calls == []


def test_full_local_render_pads_audio(run_preview):
    result, captured, pad_calls, padded = run_preview(_updated_data(0.4), ass_only=False)
    assert result["countdown_padding_seconds"] == 3.0
    assert len(pad_calls) == 1
    assert captured["audio"] == padded


def test_late_vocals_untouched(run_preview):
    result, captured, pad_calls, _ = run_preview(_updated_data(10.0), ass_only=False)
    assert result["countdown_padding_seconds"] == 0.0
    assert captured["correction"].corrected_segments[0].text == "hello"
    assert pad_calls == []
    assert captured["audio"].endswith("song.flac")


def test_countdown_previews_get_a_fresh_hash(run_preview):
    """Previews cached before countdown support must not be served again."""
    import hashlib, json
    data = _updated_data(0.4)
    result, *_ = run_preview(data, ass_only=True)
    legacy = hashlib.md5(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    assert result["preview_hash"] != legacy

    late = _updated_data(10.0)
    result_late, *_ = run_preview(late, ass_only=True)
    legacy_late = hashlib.md5(json.dumps(late, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    assert result_late["preview_hash"] == legacy_late
