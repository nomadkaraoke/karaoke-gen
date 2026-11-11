"""CountdownProcessor helpers used by the review preview.

The review preview must show the same countdown intro the final render adds
(CountdownProcessor.process in the encoding worker): a song whose vocals start
within 3s gets lyrics shifted +3s and a "3... 2... 1..." line. These helpers
split that into a lyrics-only step and a dict-level offset check.
"""
from karaoke_gen.lyrics_transcriber.output.countdown_processor import CountdownProcessor
from karaoke_gen.lyrics_transcriber.types import CorrectionResult, LyricsSegment, Word


def _result(first_word_start: float) -> CorrectionResult:
    word = Word(id="w1", text="hello", start_time=first_word_start, end_time=first_word_start + 0.5)
    seg = LyricsSegment(id="s1", text="hello", words=[word], start_time=first_word_start,
                        end_time=first_word_start + 0.5)
    return CorrectionResult(
        original_segments=[seg], corrected_segments=[seg], corrections=[], corrections_made=0,
        confidence=1.0, reference_lyrics={}, anchor_sequences=[], gap_sequences=[],
        resized_segments=[], metadata={}, correction_steps=[], word_id_map={}, segment_id_map={},
    )


class TestPaddingForSegmentsData:
    def test_early_vocals_need_padding(self):
        segs = [{"text": "hi", "words": [{"text": "hi", "start_time": 0.5}]}]
        assert CountdownProcessor.padding_for_segments_data(segs) == 3.0

    def test_late_vocals_need_none(self):
        segs = [{"text": "hi", "words": [{"text": "hi", "start_time": 12.0}]}]
        assert CountdownProcessor.padding_for_segments_data(segs) == 0.0

    def test_threshold_is_exclusive(self):
        segs = [{"text": "hi", "words": [{"text": "hi", "start_time": 3.0}]}]
        assert CountdownProcessor.padding_for_segments_data(segs) == 0.0

    def test_skips_wordless_segments(self):
        segs = [{"text": "", "words": []}, {"text": "hi", "words": [{"text": "hi", "start_time": 1.0}]}]
        assert CountdownProcessor.padding_for_segments_data(segs) == 3.0

    def test_already_counted_down_still_reports_offset(self):
        """Timings are already shifted, so the timeline offset is still 3s."""
        segs = [{"text": CountdownProcessor.COUNTDOWN_TEXT,
                 "words": [{"text": CountdownProcessor.COUNTDOWN_TEXT, "start_time": 0.1}]},
                {"text": "hi", "words": [{"text": "hi", "start_time": 3.4}]}]
        assert CountdownProcessor.padding_for_segments_data(segs) == 3.0

    def test_empty_or_malformed(self):
        assert CountdownProcessor.padding_for_segments_data(None) == 0.0
        assert CountdownProcessor.padding_for_segments_data([]) == 0.0
        assert CountdownProcessor.padding_for_segments_data(
            [{"text": "x", "words": [{"text": "x", "start_time": None}]}]) == 0.0


class TestAddCountdownToLyricsIfNeeded:
    def test_shifts_and_prepends_countdown(self, tmp_path):
        cp = CountdownProcessor(cache_dir=str(tmp_path))
        original = _result(0.4)
        result, padding = cp.add_countdown_to_lyrics_if_needed(original)
        assert padding == 3.0
        assert result.corrected_segments[0].text == CountdownProcessor.COUNTDOWN_TEXT
        assert result.corrected_segments[1].words[0].start_time == 3.4
        # The input is not mutated.
        assert original.corrected_segments[0].words[0].start_time == 0.4

    def test_late_vocals_unchanged(self, tmp_path):
        cp = CountdownProcessor(cache_dir=str(tmp_path))
        original = _result(8.0)
        result, padding = cp.add_countdown_to_lyrics_if_needed(original)
        assert padding == 0.0
        assert result is original

    def test_idempotent_but_still_needs_padded_audio(self, tmp_path):
        """A result that already has the countdown isn't shifted again, but its
        audio must still be padded to match the shifted timings."""
        cp = CountdownProcessor(cache_dir=str(tmp_path))
        once, _ = cp.add_countdown_to_lyrics_if_needed(_result(0.4))
        twice, padding = cp.add_countdown_to_lyrics_if_needed(once)
        assert padding == 3.0
        assert twice is once
        assert twice.corrected_segments[1].words[0].start_time == 3.4

    def test_agrees_with_dict_helper(self, tmp_path):
        cp = CountdownProcessor(cache_dir=str(tmp_path))
        for start in (0.0, 2.99, 3.0, 7.5):
            r = _result(start)
            out, padding = cp.add_countdown_to_lyrics_if_needed(r)
            segs = [s.to_dict() for s in r.corrected_segments]
            assert CountdownProcessor.padding_for_segments_data(segs) == padding
            # ...and on the already-processed result too.
            _, again = cp.add_countdown_to_lyrics_if_needed(out)
            assert CountdownProcessor.padding_for_segments_data(
                [s.to_dict() for s in out.corrected_segments]) == again
