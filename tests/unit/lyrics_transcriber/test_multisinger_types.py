"""Tests for multi-singer fields on Word and LyricsSegment."""
import pytest

from karaoke_gen.lyrics_transcriber.types import Word, LyricsSegment


class TestWordSinger:
    def test_word_singer_defaults_to_none(self):
        w = Word(id="w1", text="hello", start_time=0.0, end_time=0.5)
        assert w.singer is None

    def test_word_accepts_singer_ids(self):
        for sid in (0, 1, 2):
            w = Word(id="w1", text="hi", start_time=0.0, end_time=0.5, singer=sid)
            assert w.singer == sid

    def test_word_to_dict_omits_singer_when_none(self):
        w = Word(id="w1", text="hi", start_time=0.0, end_time=0.5)
        assert "singer" not in w.to_dict()

    def test_word_to_dict_includes_singer_when_set(self):
        w = Word(id="w1", text="hi", start_time=0.0, end_time=0.5, singer=2)
        assert w.to_dict()["singer"] == 2

    def test_word_from_dict_round_trip(self):
        original = Word(id="w1", text="hi", start_time=0.0, end_time=0.5, singer=2)
        restored = Word.from_dict(original.to_dict())
        assert restored.singer == 2

    def test_word_from_dict_missing_singer_is_none(self):
        restored = Word.from_dict({"id": "w1", "text": "hi", "start_time": 0.0, "end_time": 0.5})
        assert restored.singer is None


class TestLyricsSegmentSinger:
    def _word(self) -> Word:
        return Word(id="w1", text="hi", start_time=0.0, end_time=0.5)

    def test_segment_singer_defaults_to_none(self):
        seg = LyricsSegment(id="s1", text="hi", words=[self._word()], start_time=0.0, end_time=0.5)
        assert seg.singer is None

    def test_segment_to_dict_omits_singer_when_none(self):
        seg = LyricsSegment(id="s1", text="hi", words=[self._word()], start_time=0.0, end_time=0.5)
        assert "singer" not in seg.to_dict()

    def test_segment_to_dict_includes_singer_when_set(self):
        seg = LyricsSegment(id="s1", text="hi", words=[self._word()], start_time=0.0, end_time=0.5, singer=1)
        assert seg.to_dict()["singer"] == 1

    def test_segment_from_dict_round_trip(self):
        original = LyricsSegment(
            id="s1",
            text="hi",
            words=[Word(id="w1", text="hi", start_time=0.0, end_time=0.5, singer=2)],
            start_time=0.0,
            end_time=0.5,
            singer=1,
        )
        restored = LyricsSegment.from_dict(original.to_dict())
        assert restored.singer == 1
        assert restored.words[0].singer == 2

    def test_segment_from_dict_missing_singer_is_none(self):
        data = {
            "id": "s1", "text": "hi",
            "words": [{"id": "w1", "text": "hi", "start_time": 0.0, "end_time": 0.5}],
            "start_time": 0.0, "end_time": 0.5,
        }
        restored = LyricsSegment.from_dict(data)
        assert restored.singer is None
        assert restored.words[0].singer is None
