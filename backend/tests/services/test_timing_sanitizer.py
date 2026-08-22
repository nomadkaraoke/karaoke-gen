"""Tests for backend.services.timing_sanitizer."""
import pytest

from backend.services.timing_sanitizer import sanitize_corrections


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


def test_non_finite_segment_bounds_left_untouched():
    payload = {"corrected_segments": [
        {"id": "s", "start_time": None, "end_time": None, "text": "x",
         "words": [{"id": "w", "text": "x", "start_time": 3, "end_time": 5}]}
    ]}
    cleaned, count = sanitize_corrections(payload)
    assert count == 0
    assert cleaned["corrected_segments"][0]["words"][0]["start_time"] == 3


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
