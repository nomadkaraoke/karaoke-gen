"""Tests for CDG multi-singer adapter."""
from karaoke_gen.lyrics_transcriber.types import Word, LyricsSegment


def _segment(seg_id, start, end, text, singer=None):
    words = [Word(id=f"{seg_id}-w{i}", text=t, start_time=start + i*0.1, end_time=start + (i+1)*0.1)
             for i, t in enumerate(text.split())]
    return LyricsSegment(
        id=seg_id, text=text, words=words,
        start_time=start, end_time=end, singer=singer,
    )


class TestBuildCdgLyrics:
    def test_solo_omits_singer_tag(self):
        from karaoke_gen.lyrics_transcriber.output.cdg import build_cdg_lyrics
        segments = [_segment("s1", 0.0, 1.0, "hello world", singer=None)]
        result = build_cdg_lyrics(segments, is_duet=False, line_tile_height=4, lines_per_page=3)
        assert len(result) == 1
        # Solo path defaults to singer=1 (CDG default)
        assert result[0].singer == 1

    def test_duet_singer_1_maps_to_1(self):
        from karaoke_gen.lyrics_transcriber.output.cdg import build_cdg_lyrics
        segments = [_segment("s1", 0.0, 1.0, "hi", singer=1)]
        result = build_cdg_lyrics(segments, is_duet=True, line_tile_height=4, lines_per_page=3)
        assert result[0].singer == 1

    def test_duet_singer_2_maps_to_2(self):
        from karaoke_gen.lyrics_transcriber.output.cdg import build_cdg_lyrics
        segments = [_segment("s1", 0.0, 1.0, "hi", singer=2)]
        result = build_cdg_lyrics(segments, is_duet=True, line_tile_height=4, lines_per_page=3)
        assert result[0].singer == 2

    def test_duet_both_maps_to_3(self):
        from karaoke_gen.lyrics_transcriber.output.cdg import build_cdg_lyrics
        # SingerId 0 = Both → CDG singer 3
        segments = [_segment("s1", 0.0, 1.0, "hi", singer=0)]
        result = build_cdg_lyrics(segments, is_duet=True, line_tile_height=4, lines_per_page=3)
        assert result[0].singer == 3

    def test_duet_none_singer_defaults_to_1(self):
        from karaoke_gen.lyrics_transcriber.output.cdg import build_cdg_lyrics
        segments = [_segment("s1", 0.0, 1.0, "hi", singer=None)]
        result = build_cdg_lyrics(segments, is_duet=True, line_tile_height=4, lines_per_page=3)
        assert result[0].singer == 1

    def test_duet_word_level_overrides_ignored_for_cdg(self):
        """CDG uses segment-level singer only — word-level overrides are ignored."""
        from karaoke_gen.lyrics_transcriber.output.cdg import build_cdg_lyrics
        words = [
            Word(id="w1", text="hi", start_time=0.0, end_time=0.5, singer=2),
            Word(id="w2", text="bye", start_time=0.5, end_time=1.0),
        ]
        segments = [LyricsSegment(id="s1", text="hi bye", words=words,
                                  start_time=0.0, end_time=1.0, singer=1)]
        result = build_cdg_lyrics(segments, is_duet=True, line_tile_height=4, lines_per_page=3)
        # Only ONE SettingsLyric, tagged with segment singer (1), not split
        assert len(result) == 1
        assert result[0].singer == 1
