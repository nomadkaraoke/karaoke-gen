"""
Unit tests for anchor gap extension logic.

Tests the _extend_anchors_into_single_word_gaps method and related helpers
that absorb single-word gaps into adjacent anchors when validated against references.
"""

import pytest
import tempfile
import logging
from typing import Dict, List

from karaoke_gen.lyrics_transcriber.types import (
    AnchorSequence,
    PhraseScore,
    PhraseType,
    ScoredAnchor,
    Word,
)
from karaoke_gen.lyrics_transcriber.correction.anchor_sequence import AnchorSequenceFinder


@pytest.fixture
def logger():
    """Return a logger for tests."""
    return logging.getLogger(__name__)


@pytest.fixture
def temp_cache_dir():
    """Create a temporary cache directory."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield temp_dir


@pytest.fixture
def finder(temp_cache_dir, logger):
    """Return an AnchorSequenceFinder instance."""
    return AnchorSequenceFinder(
        cache_dir=temp_cache_dir,
        min_sequence_length=3,
        min_sources=1,
        timeout_seconds=60,
        logger=logger,
    )


def create_word(word_id: str, text: str, position: int) -> Word:
    """Create a Word object for testing."""
    return Word(
        id=word_id,
        text=text,
        start_time=position * 1.0,
        end_time=(position + 1) * 1.0,
        confidence=1.0,
    )


def create_scored_anchor(
    words: List[str],
    transcription_position: int,
    reference_positions: Dict[str, int],
    anchor_id: str = None,
) -> ScoredAnchor:
    """Create a ScoredAnchor for testing."""
    anchor = AnchorSequence(
        words=words,
        transcription_position=transcription_position,
        reference_positions=reference_positions,
        confidence=len(reference_positions) / 2.0,
    )
    if anchor_id:
        anchor.id = anchor_id

    phrase_score = PhraseScore(
        phrase_type=PhraseType.COMPLETE,
        natural_break_score=1.0,
        length_score=1.0,
    )

    return ScoredAnchor(anchor=anchor, phrase_score=phrase_score)


class TestContractionNormalization:
    """Tests for contraction normalization."""

    def test_normalize_common_contractions(self, finder):
        """Common contractions are normalized."""
        assert finder._normalize_contractions("dont") == "do not"
        assert finder._normalize_contractions("im") == "i am"
        assert finder._normalize_contractions("youre") == "you are"
        assert finder._normalize_contractions("its") == "it is"
        assert finder._normalize_contractions("heres") == "here is"
        assert finder._normalize_contractions("cant") == "can not"
        assert finder._normalize_contractions("didnt") == "did not"

    def test_non_contractions_unchanged(self, finder):
        """Non-contraction words are returned unchanged."""
        assert finder._normalize_contractions("hello") == "hello"
        assert finder._normalize_contractions("world") == "world"
        assert finder._normalize_contractions("test") == "test"


class TestGapContentMatching:
    """Tests for gap content matching with contraction handling."""

    def test_direct_match(self, finder):
        """Direct string match works."""
        assert finder._gap_content_matches("hello", "hello") is True
        assert finder._gap_content_matches("hello", "world") is False

    def test_contraction_expansion_match(self, finder):
        """Contraction expanded forms match."""
        # Transcription has contracted, reference has expanded
        assert finder._gap_content_matches("im", "i am") is True
        assert finder._gap_content_matches("dont", "do not") is True
        assert finder._gap_content_matches("its", "it is") is True

    def test_both_contracted_match(self, finder):
        """Both contracted forms match."""
        assert finder._gap_content_matches("im", "im") is True
        assert finder._gap_content_matches("dont", "dont") is True


class TestValidateExtension:
    """Tests for _validate_extension helper."""

    def test_forward_extension_valid(self, finder):
        """Forward extension is valid when gap word matches reference."""
        # Anchor: "hello world" at positions 0-1, ref positions {"src1": 0}
        # Gap word at position 2: "test"
        # Reference: ["hello", "world", "test", "data"]
        anchor = AnchorSequence(
            words=["hello", "world"],
            transcription_position=0,
            reference_positions={"src1": 0},
            confidence=1.0,
        )

        ref_texts_clean = {"src1": ["hello", "world", "test", "data"]}

        result = finder._validate_extension(
            anchor=anchor,
            gap_word="test",
            direction="forward",
            ref_texts_clean=ref_texts_clean,
        )

        assert result is True

    def test_forward_extension_invalid(self, finder):
        """Forward extension is invalid when gap word doesn't match reference."""
        anchor = AnchorSequence(
            words=["hello", "world"],
            transcription_position=0,
            reference_positions={"src1": 0},
            confidence=1.0,
        )

        ref_texts_clean = {"src1": ["hello", "world", "different", "data"]}

        result = finder._validate_extension(
            anchor=anchor,
            gap_word="test",
            direction="forward",
            ref_texts_clean=ref_texts_clean,
        )

        assert result is False

    def test_backward_extension_valid(self, finder):
        """Backward extension is valid when gap word matches reference."""
        # Anchor: "world test" at positions 2-3, ref positions {"src1": 1}
        # Gap word at position 1: "hello"
        # Reference: ["hello", "world", "test", "data"]
        anchor = AnchorSequence(
            words=["world", "test"],
            transcription_position=2,
            reference_positions={"src1": 1},
            confidence=1.0,
        )

        ref_texts_clean = {"src1": ["hello", "world", "test", "data"]}

        result = finder._validate_extension(
            anchor=anchor,
            gap_word="hello",
            direction="backward",
            ref_texts_clean=ref_texts_clean,
        )

        assert result is True

    def test_backward_extension_invalid(self, finder):
        """Backward extension is invalid when gap word doesn't match reference."""
        anchor = AnchorSequence(
            words=["world", "test"],
            transcription_position=2,
            reference_positions={"src1": 1},
            confidence=1.0,
        )

        ref_texts_clean = {"src1": ["different", "world", "test", "data"]}

        result = finder._validate_extension(
            anchor=anchor,
            gap_word="hello",
            direction="backward",
            ref_texts_clean=ref_texts_clean,
        )

        assert result is False

    def test_forward_extension_out_of_bounds(self, finder):
        """Forward extension is invalid when reference position would be out of bounds."""
        anchor = AnchorSequence(
            words=["hello", "world"],
            transcription_position=0,
            reference_positions={"src1": 2},  # Reference ends at position 3
            confidence=1.0,
        )

        ref_texts_clean = {"src1": ["foo", "bar", "hello", "world"]}

        result = finder._validate_extension(
            anchor=anchor,
            gap_word="test",
            direction="forward",
            ref_texts_clean=ref_texts_clean,
        )

        assert result is False

    def test_backward_extension_out_of_bounds(self, finder):
        """Backward extension is invalid when reference position would be negative."""
        anchor = AnchorSequence(
            words=["hello", "world"],
            transcription_position=1,
            reference_positions={"src1": 0},  # Can't go backwards from position 0
            confidence=1.0,
        )

        ref_texts_clean = {"src1": ["hello", "world", "test", "data"]}

        result = finder._validate_extension(
            anchor=anchor,
            gap_word="before",
            direction="backward",
            ref_texts_clean=ref_texts_clean,
        )

        assert result is False

    def test_extension_requires_min_sources(self, finder):
        """Extension requires validation in at least min_sources references."""
        finder.min_sources = 2

        anchor = AnchorSequence(
            words=["hello", "world"],
            transcription_position=0,
            reference_positions={"src1": 0, "src2": 0},
            confidence=1.0,
        )

        # Only src1 has matching next word
        ref_texts_clean = {
            "src1": ["hello", "world", "test", "data"],
            "src2": ["hello", "world", "different", "data"],
        }

        result = finder._validate_extension(
            anchor=anchor,
            gap_word="test",
            direction="forward",
            ref_texts_clean=ref_texts_clean,
        )

        assert result is False  # Only 1 source matches, need 2


class TestPreferForwardExtension:
    """Tests for _prefer_forward_extension helper."""

    def test_prefer_longer_current_anchor(self, finder):
        """Prefers extending the longer anchor (current)."""
        current = create_scored_anchor(
            words=["one", "two", "three"],  # 3 words
            transcription_position=0,
            reference_positions={"src1": 0},
        )
        next_anchor = create_scored_anchor(
            words=["four", "five"],  # 2 words
            transcription_position=4,
            reference_positions={"src1": 4},
        )

        result = finder._prefer_forward_extension(current, next_anchor)

        assert result is True  # Prefer forward (current is longer)

    def test_prefer_longer_next_anchor(self, finder):
        """Prefers extending the longer anchor (next)."""
        current = create_scored_anchor(
            words=["one", "two"],  # 2 words
            transcription_position=0,
            reference_positions={"src1": 0},
        )
        next_anchor = create_scored_anchor(
            words=["three", "four", "five"],  # 3 words
            transcription_position=3,
            reference_positions={"src1": 3},
        )

        result = finder._prefer_forward_extension(current, next_anchor)

        assert result is False  # Prefer backward (next is longer)

    def test_same_length_prefers_forward(self, finder):
        """When same length, prefers forward extension."""
        current = create_scored_anchor(
            words=["one", "two"],
            transcription_position=0,
            reference_positions={"src1": 0},
        )
        next_anchor = create_scored_anchor(
            words=["three", "four"],
            transcription_position=3,
            reference_positions={"src1": 3},
        )

        result = finder._prefer_forward_extension(current, next_anchor)

        assert result is True  # Forward as tiebreaker


class TestApplyForwardExtension:
    """Tests for _apply_forward_extension helper."""

    def test_forward_extension_adds_word(self, finder):
        """Forward extension adds gap word to end of anchor."""
        current = create_scored_anchor(
            words=["hello", "world"],
            transcription_position=0,
            reference_positions={"src1": 0},
            anchor_id="anchor1",
        )

        all_words = [
            create_word("w0", "hello", 0),
            create_word("w1", "world", 1),
            create_word("w2", "test", 2),  # Gap word
            create_word("w3", "data", 3),
        ]

        ref_words = {
            "src1": [
                create_word("r0", "hello", 0),
                create_word("r1", "world", 1),
                create_word("r2", "test", 2),
                create_word("r3", "data", 3),
            ]
        }

        result = finder._apply_forward_extension(
            scored_anchor=current,
            gap_position=2,
            all_words=all_words,
            ref_words=ref_words,
        )

        assert result.anchor.length == 3
        assert result.anchor.transcription_position == 0
        assert "w2" in result.anchor.transcribed_word_ids
        assert result.anchor._words == ["hello", "world", "test"]


class TestApplyBackwardExtension:
    """Tests for _apply_backward_extension helper."""

    def test_backward_extension_adds_word(self, finder):
        """Backward extension adds gap word to beginning of anchor."""
        next_anchor = create_scored_anchor(
            words=["world", "test"],
            transcription_position=2,
            reference_positions={"src1": 1},
            anchor_id="anchor2",
        )

        all_words = [
            create_word("w0", "before", 0),
            create_word("w1", "hello", 1),  # Gap word
            create_word("w2", "world", 2),
            create_word("w3", "test", 3),
        ]

        ref_words = {
            "src1": [
                create_word("r0", "hello", 0),
                create_word("r1", "world", 1),
                create_word("r2", "test", 2),
                create_word("r3", "data", 3),
            ]
        }

        result = finder._apply_backward_extension(
            scored_anchor=next_anchor,
            gap_position=1,
            all_words=all_words,
            ref_words=ref_words,
        )

        assert result.anchor.length == 3
        assert result.anchor.transcription_position == 1  # Now starts at gap position
        assert "w1" in result.anchor.transcribed_word_ids
        assert result.anchor._words == ["hello", "world", "test"]


class TestValidateGapContent:
    """Tests for _validate_gap_content helper."""

    def test_gap_content_matches_reference(self, finder):
        """Gap content that matches reference is valid."""
        anchor1 = AnchorSequence(
            words=["hello", "world"],
            transcription_position=0,
            reference_positions={"src1": 0},
            confidence=1.0,
        )
        anchor2 = AnchorSequence(
            words=["more", "words"],
            transcription_position=3,
            reference_positions={"src1": 3},
            confidence=1.0,
        )

        ref_texts_clean = {"src1": ["hello", "world", "test", "more", "words"]}

        result = finder._validate_gap_content(
            gap_words=["test"],
            preceding_anchor=anchor1,
            following_anchor=anchor2,
            ref_texts_clean=ref_texts_clean,
        )

        assert result is True

    def test_gap_content_contraction_matches(self, finder):
        """Gap content with contraction matches expanded form in reference."""
        anchor1 = AnchorSequence(
            words=["hello"],
            transcription_position=0,
            reference_positions={"src1": 0},
            confidence=1.0,
        )
        anchor2 = AnchorSequence(
            words=["world"],
            transcription_position=2,
            reference_positions={"src1": 3},  # Reference has "i am" instead of "im"
            confidence=1.0,
        )

        # Reference has expanded form "i am"
        ref_texts_clean = {"src1": ["hello", "i", "am", "world"]}

        result = finder._validate_gap_content(
            gap_words=["im"],  # Transcription has contracted
            preceding_anchor=anchor1,
            following_anchor=anchor2,
            ref_texts_clean=ref_texts_clean,
        )

        assert result is True


class TestExtendAnchorsIntoSingleWordGaps:
    """Tests for _extend_anchors_into_single_word_gaps method."""

    def test_extends_single_word_gap_forward(self, finder):
        """Single-word gap is extended into preceding anchor when valid."""
        # Setup: anchor1 at 0-1, gap at 2, anchor2 at 3-4
        # Reference allows forward extension
        anchor1 = create_scored_anchor(
            words=["hello", "world"],
            transcription_position=0,
            reference_positions={"src1": 0},
        )
        anchor2 = create_scored_anchor(
            words=["more", "words"],
            transcription_position=3,
            reference_positions={"src1": 3},
        )

        all_words = [
            create_word("w0", "hello", 0),
            create_word("w1", "world", 1),
            create_word("w2", "test", 2),  # Gap word
            create_word("w3", "more", 3),
            create_word("w4", "words", 4),
        ]

        ref_texts_clean = {"src1": ["hello", "world", "test", "more", "words"]}
        ref_words = {
            "src1": [
                create_word("r0", "hello", 0),
                create_word("r1", "world", 1),
                create_word("r2", "test", 2),
                create_word("r3", "more", 3),
                create_word("r4", "words", 4),
            ]
        }

        result = finder._extend_anchors_into_single_word_gaps(
            filtered_scored=[anchor1, anchor2],
            all_words=all_words,
            ref_texts_clean=ref_texts_clean,
            ref_words=ref_words,
        )

        assert len(result) == 2
        # First anchor should be extended
        assert result[0].anchor.length == 3
        assert result[0].anchor._words == ["hello", "world", "test"]
        # Second anchor unchanged
        assert result[1].anchor.length == 2

    def test_extends_single_word_gap_backward(self, finder):
        """Single-word gap is extended into following anchor when forward invalid."""
        # Setup: anchor1 at 0-1, gap at 2, anchor2 at 3-4
        # Reference only allows backward extension
        anchor1 = create_scored_anchor(
            words=["hello", "world"],
            transcription_position=0,
            reference_positions={"src1": 0},
        )
        anchor2 = create_scored_anchor(
            words=["more", "words"],
            transcription_position=3,
            reference_positions={"src1": 2},  # Reference position shifted
        )

        all_words = [
            create_word("w0", "hello", 0),
            create_word("w1", "world", 1),
            create_word("w2", "test", 2),  # Gap word
            create_word("w3", "more", 3),
            create_word("w4", "words", 4),
        ]

        # Forward: ref[0+2=2] = "test" vs gap="test" -> valid
        # But let's make forward invalid and backward valid
        ref_texts_clean = {"src1": ["hello", "world", "diff", "test", "more", "words"]}
        ref_words = {
            "src1": [
                create_word("r0", "hello", 0),
                create_word("r1", "world", 1),
                create_word("r2", "diff", 2),  # Different word - forward invalid
                create_word("r3", "test", 3),  # Backward valid
                create_word("r4", "more", 4),
                create_word("r5", "words", 5),
            ]
        }

        # Update anchor2 reference position for backward extension to work
        anchor2.anchor.reference_positions = {"src1": 4}
        anchor2.anchor.reference_word_ids = {"src1": ["r4", "r5"]}

        result = finder._extend_anchors_into_single_word_gaps(
            filtered_scored=[anchor1, anchor2],
            all_words=all_words,
            ref_texts_clean=ref_texts_clean,
            ref_words=ref_words,
        )

        assert len(result) == 2
        # First anchor unchanged (forward extension invalid)
        assert result[0].anchor.length == 2
        # Second anchor extended backward
        assert result[1].anchor.length == 3
        assert result[1].anchor.transcription_position == 2  # Starts at gap

    def test_skips_multi_word_gaps(self, finder):
        """Multi-word gaps are not processed."""
        anchor1 = create_scored_anchor(
            words=["hello", "world"],
            transcription_position=0,
            reference_positions={"src1": 0},
        )
        anchor2 = create_scored_anchor(
            words=["more", "words"],
            transcription_position=4,  # Gap of 2 words (positions 2, 3)
            reference_positions={"src1": 4},
        )

        all_words = [
            create_word("w0", "hello", 0),
            create_word("w1", "world", 1),
            create_word("w2", "gap1", 2),
            create_word("w3", "gap2", 3),
            create_word("w4", "more", 4),
            create_word("w5", "words", 5),
        ]

        ref_texts_clean = {"src1": ["hello", "world", "gap1", "gap2", "more", "words"]}
        ref_words = {
            "src1": [
                create_word("r0", "hello", 0),
                create_word("r1", "world", 1),
                create_word("r2", "gap1", 2),
                create_word("r3", "gap2", 3),
                create_word("r4", "more", 4),
                create_word("r5", "words", 5),
            ]
        }

        result = finder._extend_anchors_into_single_word_gaps(
            filtered_scored=[anchor1, anchor2],
            all_words=all_words,
            ref_texts_clean=ref_texts_clean,
            ref_words=ref_words,
        )

        # Both anchors unchanged - multi-word gap not processed
        assert result[0].anchor.length == 2
        assert result[1].anchor.length == 2

    def test_neither_direction_valid(self, finder):
        """Gap is left as-is when neither direction validates."""
        anchor1 = create_scored_anchor(
            words=["hello", "world"],
            transcription_position=0,
            reference_positions={"src1": 0},
        )
        anchor2 = create_scored_anchor(
            words=["more", "words"],
            transcription_position=3,
            reference_positions={"src1": 4},  # Gap in reference too
        )

        all_words = [
            create_word("w0", "hello", 0),
            create_word("w1", "world", 1),
            create_word("w2", "unique", 2),  # Gap word not in reference at right position
            create_word("w3", "more", 3),
            create_word("w4", "words", 4),
        ]

        ref_texts_clean = {"src1": ["hello", "world", "diff", "also", "more", "words"]}
        ref_words = {
            "src1": [
                create_word("r0", "hello", 0),
                create_word("r1", "world", 1),
                create_word("r2", "diff", 2),
                create_word("r3", "also", 3),
                create_word("r4", "more", 4),
                create_word("r5", "words", 5),
            ]
        }

        result = finder._extend_anchors_into_single_word_gaps(
            filtered_scored=[anchor1, anchor2],
            all_words=all_words,
            ref_texts_clean=ref_texts_clean,
            ref_words=ref_words,
        )

        # Both anchors unchanged
        assert result[0].anchor.length == 2
        assert result[1].anchor.length == 2

    def test_single_anchor_unchanged(self, finder):
        """Single anchor is returned unchanged (no adjacent anchors to check)."""
        anchor = create_scored_anchor(
            words=["hello", "world"],
            transcription_position=0,
            reference_positions={"src1": 0},
        )

        all_words = [create_word("w0", "hello", 0), create_word("w1", "world", 1)]
        ref_texts_clean = {"src1": ["hello", "world"]}
        ref_words = {
            "src1": [
                create_word("r0", "hello", 0),
                create_word("r1", "world", 1),
            ]
        }

        result = finder._extend_anchors_into_single_word_gaps(
            filtered_scored=[anchor],
            all_words=all_words,
            ref_texts_clean=ref_texts_clean,
            ref_words=ref_words,
        )

        assert len(result) == 1
        assert result[0].anchor.length == 2

    def test_empty_list_unchanged(self, finder):
        """Empty anchor list returns empty."""
        result = finder._extend_anchors_into_single_word_gaps(
            filtered_scored=[],
            all_words=[],
            ref_texts_clean={},
            ref_words={},
        )

        assert result == []

    def test_contiguous_anchors_no_gap(self, finder):
        """Contiguous anchors (no gap) are not processed."""
        anchor1 = create_scored_anchor(
            words=["hello", "world"],
            transcription_position=0,
            reference_positions={"src1": 0},
        )
        anchor2 = create_scored_anchor(
            words=["more", "words"],
            transcription_position=2,  # Immediately after anchor1
            reference_positions={"src1": 2},
        )

        all_words = [
            create_word("w0", "hello", 0),
            create_word("w1", "world", 1),
            create_word("w2", "more", 2),
            create_word("w3", "words", 3),
        ]

        ref_texts_clean = {"src1": ["hello", "world", "more", "words"]}
        ref_words = {
            "src1": [
                create_word("r0", "hello", 0),
                create_word("r1", "world", 1),
                create_word("r2", "more", 2),
                create_word("r3", "words", 3),
            ]
        }

        result = finder._extend_anchors_into_single_word_gaps(
            filtered_scored=[anchor1, anchor2],
            all_words=all_words,
            ref_texts_clean=ref_texts_clean,
            ref_words=ref_words,
        )

        # Both anchors unchanged - no gap
        assert result[0].anchor.length == 2
        assert result[1].anchor.length == 2

    def test_prefers_longer_anchor_when_both_valid(self, finder):
        """When both directions valid, extends the longer anchor."""
        # Longer anchor1 (3 words) vs shorter anchor2 (2 words)
        anchor1 = create_scored_anchor(
            words=["one", "two", "three"],
            transcription_position=0,
            reference_positions={"src1": 0},
        )
        anchor2 = create_scored_anchor(
            words=["five", "six"],
            transcription_position=4,
            reference_positions={"src1": 4},
        )

        all_words = [
            create_word("w0", "one", 0),
            create_word("w1", "two", 1),
            create_word("w2", "three", 2),
            create_word("w3", "four", 3),  # Gap word
            create_word("w4", "five", 4),
            create_word("w5", "six", 5),
        ]

        ref_texts_clean = {"src1": ["one", "two", "three", "four", "five", "six"]}
        ref_words = {
            "src1": [
                create_word("r0", "one", 0),
                create_word("r1", "two", 1),
                create_word("r2", "three", 2),
                create_word("r3", "four", 3),
                create_word("r4", "five", 4),
                create_word("r5", "six", 5),
            ]
        }

        result = finder._extend_anchors_into_single_word_gaps(
            filtered_scored=[anchor1, anchor2],
            all_words=all_words,
            ref_texts_clean=ref_texts_clean,
            ref_words=ref_words,
        )

        # First anchor (longer) should be extended
        assert result[0].anchor.length == 4
        assert result[0].anchor._words == ["one", "two", "three", "four"]
        # Second anchor unchanged
        assert result[1].anchor.length == 2
