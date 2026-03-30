import pytest
from karaoke_gen.lyrics_transcriber.correction.relevance import (
    compute_source_relevance,
    filter_irrelevant_sources,
    SourceRelevanceResult,
)
from karaoke_gen.lyrics_transcriber.types import (
    AnchorSequence,
    LyricsData,
    LyricsMetadata,
    LyricsSegment,
    TranscriptionData,
    TranscriptionResult,
    Word,
)


def _make_words(texts: list[str], id_prefix: str = "w") -> list[Word]:
    """Create Word objects from text strings."""
    return [
        Word(id=f"{id_prefix}_{i}", text=t, start_time=0.0, end_time=0.0)
        for i, t in enumerate(texts)
    ]


def _make_lyrics_data(word_texts: list[list[str]], source: str) -> LyricsData:
    """Create LyricsData from a list of lines (each line is a list of word strings)."""
    segments = []
    for i, line_words in enumerate(word_texts):
        words = _make_words(line_words, id_prefix=f"{source}_s{i}_w")
        segments.append(
            LyricsSegment(
                id=f"{source}_seg_{i}",
                text=" ".join(line_words),
                words=words,
                start_time=0.0,
                end_time=0.0,
            )
        )
    return LyricsData(
        segments=segments,
        metadata=LyricsMetadata(source=source, track_name="Test", artist_names="Test"),
        source=source,
    )


def _make_anchor(
    trans_word_ids: list[str],
    reference_word_ids: dict[str, list[str]],
    confidence: float = 1.0,
) -> AnchorSequence:
    """Create an AnchorSequence with given word ID mappings."""
    return AnchorSequence(
        id=f"anchor_{trans_word_ids[0]}",
        transcribed_word_ids=trans_word_ids,
        transcription_position=0,
        reference_positions={s: 0 for s in reference_word_ids},
        reference_word_ids=reference_word_ids,
        confidence=confidence,
    )


class TestComputeSourceRelevance:
    def test_high_match_returns_high_score(self):
        """Source with 8/10 words in anchors scores 0.8."""
        lyrics = _make_lyrics_data([["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]], "genius")
        all_word_ids = [w.id for seg in lyrics.segments for w in seg.words]
        # 8 of 10 words matched
        anchors = [
            _make_anchor(["t1", "t2"], {"genius": all_word_ids[0:2]}),
            _make_anchor(["t3", "t4", "t5"], {"genius": all_word_ids[2:5]}),
            _make_anchor(["t6", "t7", "t8"], {"genius": all_word_ids[5:8]}),
        ]
        result = compute_source_relevance("genius", lyrics, anchors)
        assert result.relevance == pytest.approx(0.8)
        assert result.matched_words == 8
        assert result.total_words == 10

    def test_no_match_returns_zero(self):
        """Source with no words in any anchors scores 0.0."""
        lyrics = _make_lyrics_data([["a", "b", "c"]], "spotify")
        anchors = [
            _make_anchor(["t1"], {"genius": ["other_id"]}),  # Different source
        ]
        result = compute_source_relevance("spotify", lyrics, anchors)
        assert result.relevance == 0.0
        assert result.matched_words == 0

    def test_empty_source_returns_zero(self):
        """Source with zero words scores 0.0."""
        lyrics = _make_lyrics_data([], "empty")
        result = compute_source_relevance("empty", lyrics, [])
        assert result.relevance == 0.0


class TestFilterIrrelevantSources:
    def test_filters_below_threshold(self):
        """Sources below threshold are removed from lyrics_results."""
        good_lyrics = _make_lyrics_data([["a", "b", "c", "d", "e"]], "genius")
        bad_lyrics = _make_lyrics_data([["x", "y", "z", "q", "r"]], "spotify")
        good_word_ids = [w.id for seg in good_lyrics.segments for w in seg.words]

        lyrics_results = {"genius": good_lyrics, "spotify": bad_lyrics}
        anchors = [
            _make_anchor(["t1", "t2", "t3", "t4"], {"genius": good_word_ids[0:4]}),
        ]

        filtered, rejected = filter_irrelevant_sources(
            lyrics_results, anchors, min_relevance=0.5
        )
        assert "genius" in filtered
        assert "spotify" not in filtered
        assert "spotify" in rejected
        assert rejected["spotify"].relevance == 0.0

    def test_keeps_all_above_threshold(self):
        """All sources above threshold are kept."""
        lyrics_a = _make_lyrics_data([["a", "b"]], "src_a")
        lyrics_b = _make_lyrics_data([["a", "b"]], "src_b")
        a_ids = [w.id for seg in lyrics_a.segments for w in seg.words]
        b_ids = [w.id for seg in lyrics_b.segments for w in seg.words]

        anchors = [
            _make_anchor(["t1", "t2"], {"src_a": a_ids, "src_b": b_ids}),
        ]
        filtered, rejected = filter_irrelevant_sources(
            {"src_a": lyrics_a, "src_b": lyrics_b}, anchors, min_relevance=0.5
        )
        assert len(filtered) == 2
        assert len(rejected) == 0

    def test_empty_input_returns_empty(self):
        """No sources in, no sources out."""
        filtered, rejected = filter_irrelevant_sources({}, [], min_relevance=0.5)
        assert filtered == {}
        assert rejected == {}


class TestCorrectorRelevanceIntegration:
    """Test that LyricsCorrector filters irrelevant sources."""

    def test_corrector_removes_irrelevant_source(self, tmp_path):
        """A source with 0% match should be excluded from the CorrectionResult."""
        from karaoke_gen.lyrics_transcriber.correction.corrector import LyricsCorrector

        # Transcription: "hello world foo bar"
        trans_words = _make_words(["hello", "world", "foo", "bar"], "trans")
        trans_segment = LyricsSegment(
            id="trans_seg_0",
            text="hello world foo bar",
            words=trans_words,
            start_time=0.0,
            end_time=1.0,
        )
        transcription = TranscriptionData(
            segments=[trans_segment],
            words=trans_words,
            text="hello world foo bar",
            source="test",
        )

        # Good source: matching words
        good_lyrics = _make_lyrics_data([["hello", "world", "foo", "bar"]], "good_source")
        # Bad source: completely different words
        bad_lyrics = _make_lyrics_data([["alpha", "beta", "gamma", "delta"]], "bad_source")

        corrector = LyricsCorrector(cache_dir=str(tmp_path))
        result = corrector.run(
            transcription_results=[
                TranscriptionResult(name="test", priority=1, result=transcription)
            ],
            lyrics_results={"good_source": good_lyrics, "bad_source": bad_lyrics},
        )

        # Good source should be in result, bad source should not
        assert "good_source" in result.reference_lyrics
        assert "bad_source" not in result.reference_lyrics

        # Rejected sources should be logged in metadata
        assert "rejected_sources" in result.metadata
        assert "bad_source" in result.metadata["rejected_sources"]

    def test_corrector_keeps_all_relevant_sources(self, tmp_path):
        """Sources that match the transcription should all be kept."""
        from karaoke_gen.lyrics_transcriber.correction.corrector import LyricsCorrector

        trans_words = _make_words(["hello", "world", "foo", "bar"], "trans")
        trans_segment = LyricsSegment(
            id="trans_seg_0",
            text="hello world foo bar",
            words=trans_words,
            start_time=0.0,
            end_time=1.0,
        )
        transcription = TranscriptionData(
            segments=[trans_segment],
            words=trans_words,
            text="hello world foo bar",
            source="test",
        )

        # Both sources match the transcription
        source_a = _make_lyrics_data([["hello", "world", "foo", "bar"]], "source_a")
        source_b = _make_lyrics_data([["hello", "world", "foo", "bar"]], "source_b")

        corrector = LyricsCorrector(cache_dir=str(tmp_path))
        result = corrector.run(
            transcription_results=[
                TranscriptionResult(name="test", priority=1, result=transcription)
            ],
            lyrics_results={"source_a": source_a, "source_b": source_b},
        )

        assert "source_a" in result.reference_lyrics
        assert "source_b" in result.reference_lyrics
        assert "rejected_sources" not in result.metadata

    def test_corrector_handles_all_sources_filtered_out(self, tmp_path):
        """When ALL sources fail relevance, corrector returns empty references and no corrections."""
        from karaoke_gen.lyrics_transcriber.correction.corrector import LyricsCorrector

        trans_words = _make_words(["hello", "world", "foo", "bar"], "trans")
        trans_segment = LyricsSegment(
            id="trans_seg_0",
            text="hello world foo bar",
            words=trans_words,
            start_time=0.0,
            end_time=1.0,
        )
        transcription = TranscriptionData(
            segments=[trans_segment],
            words=trans_words,
            text="hello world foo bar",
            source="test",
        )

        # All sources have completely different words — all will be filtered
        bad_a = _make_lyrics_data([["alpha", "beta", "gamma", "delta"]], "bad_a")
        bad_b = _make_lyrics_data([["uno", "dos", "tres", "cuatro"]], "bad_b")
        bad_c = _make_lyrics_data([["eins", "zwei", "drei", "vier"]], "bad_c")

        corrector = LyricsCorrector(cache_dir=str(tmp_path))
        result = corrector.run(
            transcription_results=[
                TranscriptionResult(name="test", priority=1, result=transcription)
            ],
            lyrics_results={"bad_a": bad_a, "bad_b": bad_b, "bad_c": bad_c},
        )

        # All sources should be rejected
        assert len(result.reference_lyrics) == 0
        assert "rejected_sources" in result.metadata
        assert len(result.metadata["rejected_sources"]) == 3
        # No corrections should have been made (no references to correct against)
        assert result.corrections_made == 0
        # Original segments should be preserved
        assert len(result.original_segments) == 1
