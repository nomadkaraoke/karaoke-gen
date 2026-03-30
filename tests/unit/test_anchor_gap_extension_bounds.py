"""
Regression test for IndexError in anchor gap extension (job b7e8c96d).

Root cause: ref_texts_clean and ref_words have different lengths because
clean_text() normalizes text (merging/splitting words), but anchor positions
are computed against ref_texts_clean and then used to index ref_words.

When ref_texts_clean[source] has MORE words than ref_words[source],
anchor reference_positions can exceed ref_words bounds.
"""

import json
import logging
import tempfile
from pathlib import Path

import pytest

from karaoke_gen.lyrics_transcriber.correction.anchor_sequence import AnchorSequenceFinder
from karaoke_gen.lyrics_transcriber.types import (
    AnchorSequence,
    PhraseScore,
    PhraseType,
    ScoredAnchor,
    Word,
)

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "anchor_crash_b7e8c96d.json"


@pytest.fixture
def finder():
    with tempfile.TemporaryDirectory() as temp_dir:
        yield AnchorSequenceFinder(
            cache_dir=temp_dir,
            min_sequence_length=3,
            min_sources=1,
            timeout_seconds=60,
            logger=logging.getLogger(__name__),
        )


def _load_fixture():
    """Load the production crash fixture data."""
    with open(FIXTURE_PATH) as f:
        return json.load(f)


def _rebuild_scored_anchors(fixture_data):
    """Rebuild ScoredAnchor objects from fixture JSON."""
    scored_anchors = []
    for sa_data in fixture_data["filtered_scored"]:
        a = sa_data["anchor"]
        anchor = AnchorSequence(
            id=a["id"],
            transcribed_word_ids=a["transcribed_word_ids"],
            transcription_position=a["transcription_position"],
            reference_positions=a["reference_positions"],
            reference_word_ids=a["reference_word_ids"],
            confidence=a["confidence"],
            _words=a["words"],
        )
        phrase_score = PhraseScore(
            phrase_type=PhraseType(sa_data["phrase_score"]["phrase_type"]),
            natural_break_score=sa_data["phrase_score"]["natural_break_score"],
            length_score=sa_data["phrase_score"]["length_score"],
        )
        scored_anchors.append(ScoredAnchor(anchor=anchor, phrase_score=phrase_score))
    return scored_anchors


def _rebuild_words(word_dicts):
    """Rebuild Word objects from fixture JSON."""
    return [
        Word(id=w["id"], text=w["text"], start_time=w["start_time"], end_time=w["end_time"])
        for w in word_dicts
    ]


@pytest.mark.skipif(not FIXTURE_PATH.exists(), reason="Fixture not available")
class TestProductionCrashB7e8c96d:
    """Regression tests using production data from job b7e8c96d."""

    def test_gap_extension_does_not_crash(self, finder):
        """_extend_anchors_into_single_word_gaps must not IndexError on mismatched ref lengths."""
        data = _load_fixture()
        scored_anchors = _rebuild_scored_anchors(data)
        all_words = _rebuild_words(data["all_words"])
        ref_texts_clean = data["ref_texts_clean"]
        ref_words = {src: _rebuild_words(words) for src, words in data["ref_words"].items()}

        # This crashed with IndexError before the fix
        result = finder._extend_anchors_into_single_word_gaps(
            scored_anchors, all_words, ref_texts_clean, ref_words
        )

        assert len(result) == len(scored_anchors)

    def test_ref_texts_and_ref_words_length_mismatch(self, finder):
        """Verify the fixture actually has the length mismatch that causes the bug."""
        data = _load_fixture()
        mismatched = False
        for source in data["ref_texts_clean"]:
            clean_len = len(data["ref_texts_clean"][source])
            words_len = len(data["ref_words"].get(source, []))
            if clean_len != words_len:
                mismatched = True
        assert mismatched, "Fixture should have ref_texts_clean/ref_words length mismatch"


class TestBoundsCheckInExtension:
    """Unit tests for bounds checking in forward/backward extension."""

    def _make_anchor(self, words, trans_pos, ref_positions, ref_word_ids=None):
        anchor = AnchorSequence(
            words=words,
            transcription_position=trans_pos,
            reference_positions=ref_positions,
            confidence=1.0,
        )
        if ref_word_ids:
            anchor.reference_word_ids = ref_word_ids
        return ScoredAnchor(
            anchor=anchor,
            phrase_score=PhraseScore(
                phrase_type=PhraseType.COMPLETE,
                natural_break_score=1.0,
                length_score=1.0,
            ),
        )

    def _make_word(self, idx, text="word"):
        return Word(id=f"w{idx}", text=text, start_time=float(idx), end_time=float(idx + 1))

    def test_backward_extension_ref_position_exceeds_ref_words(self, finder):
        """Backward extension must not crash when ref position > len(ref_words)."""
        # ref_texts_clean has 10 words, but ref_words only has 5
        # Anchor at ref_position=8 would crash when doing ref_words[source][8-1]
        all_words = [self._make_word(i, f"w{i}") for i in range(10)]
        ref_words = {"src": [self._make_word(i, f"r{i}") for i in range(5)]}
        ref_texts_clean = {"src": [f"r{i}" for i in range(10)]}

        anchor1 = self._make_anchor(
            ["w5", "w6"], trans_pos=5, ref_positions={"src": 5},
            ref_word_ids={"src": ["r5_id", "r6_id"]},
        )
        anchor2 = self._make_anchor(
            ["w8", "w9"], trans_pos=8, ref_positions={"src": 8},
            ref_word_ids={"src": ["r8_id", "r9_id"]},
        )

        # gap at position 7, backward extension would try ref_words["src"][7]
        # which exceeds len(ref_words["src"]) = 5
        result = finder._extend_anchors_into_single_word_gaps(
            [anchor1, anchor2], all_words, ref_texts_clean, ref_words
        )
        assert len(result) == 2

    def test_forward_extension_ref_end_exceeds_ref_words(self, finder):
        """Forward extension must not crash when ref_end > len(ref_words)."""
        all_words = [self._make_word(i, f"w{i}") for i in range(10)]
        ref_words = {"src": [self._make_word(i, f"r{i}") for i in range(5)]}
        ref_texts_clean = {"src": [f"r{i}" for i in range(10)]}

        anchor1 = self._make_anchor(
            ["w3", "w4"], trans_pos=3, ref_positions={"src": 3},
            ref_word_ids={"src": ["r3_id", "r4_id"]},
        )
        anchor2 = self._make_anchor(
            ["w6", "w7"], trans_pos=6, ref_positions={"src": 6},
            ref_word_ids={"src": ["r6_id", "r7_id"]},
        )

        # gap at position 5, forward extension would try ref_words["src"][5]
        # which is exactly at the boundary (len=5, index 5 is out of bounds)
        result = finder._extend_anchors_into_single_word_gaps(
            [anchor1, anchor2], all_words, ref_texts_clean, ref_words
        )
        assert len(result) == 2
