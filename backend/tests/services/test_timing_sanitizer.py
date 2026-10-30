"""Tests for backend.services.timing_sanitizer."""
import pytest

from backend.services.timing_sanitizer import find_missing_timings, sanitize_corrections


def _sample_corrections():
    return {
        "corrected_segments": [
            {
                "id": "s1",
                "text": "A whiskey and a beer,",
                "start_time": 15.18,
                "end_time": 18.04,
                "words": [
                    {"id": "a", "text": "A", "start_time": 0, "end_time": -0.005},
                    {"id": "b", "text": "whiskey", "start_time": 0, "end_time": -0.005},
                    {"id": "c", "text": "and", "start_time": 0, "end_time": 0},
                    {"id": "d", "text": "a", "start_time": 0, "end_time": 0},
                    {"id": "e", "text": "beer,", "start_time": 16.1, "end_time": 16.42},
                ],
            }
        ]
    }


def test_clamps_out_of_bounds_words():
    cleaned, count = sanitize_corrections(_sample_corrections())
    words = cleaned["corrected_segments"][0]["words"]
    for w in words:
        assert w["start_time"] >= 15.18
        assert w["end_time"] >= w["start_time"]
        assert w["end_time"] <= 18.04 + 1e-9
    assert count >= 4


def test_valid_corrections_unchanged():
    payload = {
        "corrected_segments": [
            {
                "id": "s", "text": "Filling up", "start_time": 21.4, "end_time": 22.1,
                "words": [
                    {"id": "w0", "text": "Filling", "start_time": 21.4, "end_time": 21.82},
                    {"id": "w1", "text": "up", "start_time": 21.86, "end_time": 22.1},
                ],
            }
        ]
    }
    cleaned, count = sanitize_corrections(payload)
    assert count == 0
    assert cleaned == payload


def test_missing_segments_key_is_noop():
    cleaned, count = sanitize_corrections({"foo": "bar"})
    assert count == 0
    assert cleaned == {"foo": "bar"}


def test_missing_segment_bounds_come_from_its_words():
    payload = {"corrected_segments": [
        {"id": "s", "start_time": None, "end_time": None, "text": "x y",
         "words": [{"id": "w", "text": "x", "start_time": 3, "end_time": 5},
                   {"id": "v", "text": "y", "start_time": 5.5, "end_time": 6}]}
    ]}
    cleaned, count = sanitize_corrections(payload)
    seg = cleaned["corrected_segments"][0]
    assert (seg["start_time"], seg["end_time"]) == (3, 6)
    assert count == 2
    assert seg["words"][0]["start_time"] == 3


def test_segment_without_end_time_key_is_filled():
    """JSON.stringify drops undefined, so the key itself can be missing (job 16792df7)."""
    payload = {"corrected_segments": [
        {"id": "s", "start_time": 1.0, "text": "x",
         "words": [{"id": "w", "text": "x", "start_time": 1.0, "end_time": 1.4}]}
    ]}
    cleaned, _ = sanitize_corrections(payload)
    assert cleaned["corrected_segments"][0]["end_time"] == 1.4
    assert find_missing_timings(cleaned) is None


def test_untimed_segment_takes_its_neighbours_gap_and_words_get_filled():
    payload = {"corrected_segments": [
        {"id": "a", "start_time": 0.0, "end_time": 2.0, "text": "a",
         "words": [{"id": "a1", "text": "a", "start_time": 0.0, "end_time": 2.0}]},
        {"id": "b", "text": "new line",
         "words": [{"id": "b1", "text": "new"}, {"id": "b2", "text": "line"}]},
        {"id": "c", "start_time": 5.0, "end_time": 6.0, "text": "c",
         "words": [{"id": "c1", "text": "c", "start_time": 5.0, "end_time": 6.0}]},
    ]}
    cleaned, _ = sanitize_corrections(payload)
    seg = cleaned["corrected_segments"][1]
    assert (seg["start_time"], seg["end_time"]) == (2.0, 5.0)
    assert all(2.0 <= w["start_time"] <= w["end_time"] <= 5.0 for w in seg["words"])
    assert find_missing_timings(cleaned) is None


def test_line_with_no_timing_anywhere_is_reported():
    payload = {"corrected_segments": [
        {"id": "b", "text": "lonely line", "words": [{"id": "b1", "text": "lonely"}]},
    ]}
    cleaned, _ = sanitize_corrections(payload)
    assert find_missing_timings(cleaned) == "lonely line"


def test_start_past_segment_end_uses_prev_end():
    """A word whose start_time is far outside [seg_start, seg_end] must be repaired
    to prev_end (clamped into the segment window), not silently collapsed to seg_end."""
    payload = {"corrected_segments": [{
        "id": "s", "start_time": 10.0, "end_time": 12.0, "text": "hello world",
        "words": [
            {"id": "w0", "text": "hello", "start_time": 10.0, "end_time": 11.0},
            {"id": "w1", "text": "world", "start_time": 99.0, "end_time": 100.0},
        ],
    }]}
    cleaned, count = sanitize_corrections(payload)
    assert count >= 2  # both start and end of w1 were repaired
    w = cleaned["corrected_segments"][0]["words"][1]
    # start must fall inside the segment window
    assert 10.0 <= w["start_time"] <= 12.0
    # end must be >= start and within the segment
    assert w["end_time"] >= w["start_time"]
    assert w["end_time"] <= 12.0
    # start should be prev_end (11.0), not collapsed to seg_end (12.0)
    assert w["start_time"] == 11.0


@pytest.mark.parametrize("bad_value", [True, False, float("inf"), float("-inf"), float("nan"), None])
def test_non_finite_word_timing_is_clamped(bad_value):
    payload = {"corrected_segments": [{
        "id": "s", "start_time": 10.0, "end_time": 12.0, "text": "x",
        "words": [{"id": "w", "text": "x", "start_time": bad_value, "end_time": bad_value}]
    }]}
    cleaned, count = sanitize_corrections(payload)
    assert count >= 1
    w = cleaned["corrected_segments"][0]["words"][0]
    assert 10.0 <= w["start_time"] <= 12.0
    assert w["end_time"] >= w["start_time"]


def _partial_pass_line():
    """gen's fake-fixture line after a Tap To Sync pass that stopped before the last word:
    "ever" was re-tapped at 19.6 but "transcribed" kept its stale 18.74-19.5."""
    return {"corrected_segments": [{
        "id": "s", "text": "Nothing here was ever transcribed", "start_time": 16.0, "end_time": 20.1,
        "words": [
            {"id": "w0", "text": "Nothing", "start_time": 16.0, "end_time": 16.9},
            {"id": "w1", "text": "here", "start_time": 17.2, "end_time": 18.1},
            {"id": "w2", "text": "was", "start_time": 18.4, "end_time": 19.3},
            {"id": "w3", "text": "ever", "start_time": 19.6, "end_time": 20.1},
            {"id": "w4", "text": "transcribed", "start_time": 18.74, "end_time": 19.5},
        ],
    }]}


def test_word_starting_before_previous_word_end_is_moved_after_it(caplog):
    with caplog.at_level("WARNING", logger="backend.services.timing_sanitizer"):
        cleaned, count = sanitize_corrections(_partial_pass_line())
    words = cleaned["corrected_segments"][0]["words"]
    assert count >= 1
    # Untouched in-order words keep their timings.
    assert [w["start_time"] for w in words[:4]] == [16.0, 17.2, 18.4, 19.6]
    # "transcribed" now follows "ever" (placed at its end, like any other bad start) inside the line.
    transcribed = words[4]
    assert transcribed["start_time"] == 20.1
    assert transcribed["start_time"] <= transcribed["end_time"] <= 20.1
    for prev, cur in zip(words, words[1:]):
        assert cur["start_time"] >= prev["end_time"]
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1  # one aggregated warning per call, not one per word
    assert "transcribed" in warnings[0].getMessage()
    assert not [r for r in caplog.records if r.levelno >= 40]  # expected condition: never ERROR


def test_in_order_words_log_nothing(caplog):
    payload = {"corrected_segments": [{
        "id": "s", "text": "a b", "start_time": 1.0, "end_time": 3.0,
        "words": [{"id": "a", "text": "a", "start_time": 1.0, "end_time": 2.0},
                  {"id": "b", "text": "b", "start_time": 2.0, "end_time": 3.0}],
    }]}
    with caplog.at_level("WARNING", logger="backend.services.timing_sanitizer"):
        _, count = sanitize_corrections(payload)
    assert count == 0
    assert not caplog.records
