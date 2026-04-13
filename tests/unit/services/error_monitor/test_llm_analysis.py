"""Tests for error_monitor.llm_analysis module.

All tests mock _call_llm to avoid real LLM calls.
"""

import json
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_PATTERNS = [
    {
        "service": "karaoke-backend",
        "normalized_message": "Connection refused to Firestore",
        "count": 15,
        "first_seen": "2026-04-10T10:00:00Z",
        "last_seen": "2026-04-10T10:30:00Z",
    },
    {
        "service": "audio-separation-job",
        "normalized_message": "CUDA out of memory error",
        "count": 8,
        "first_seen": "2026-04-10T10:05:00Z",
        "last_seen": "2026-04-10T10:25:00Z",
    },
    {
        "service": "audio-download-job",
        "normalized_message": "flacfetch-vm connection timeout",
        "count": 5,
        "first_seen": "2026-04-10T10:10:00Z",
        "last_seen": "2026-04-10T10:20:00Z",
    },
]

VALID_INCIDENTS_JSON = json.dumps(
    {
        "incidents": [
            {
                "title": "Firestore connectivity failure",
                "root_cause": "Firestore is unavailable or misconfigured",
                "severity": "P1",
                "suggested_fix": "Check Firestore service health and credentials",
                "primary_service": "karaoke-backend",
                "pattern_indices": [0],
            },
            {
                "title": "GPU audio separation failure",
                "root_cause": "GPU out of memory on audio-separation-job",
                "severity": "P2",
                "suggested_fix": "Check GPU memory limits and job queue depth",
                "primary_service": "audio-separation-job",
                "pattern_indices": [1, 2],
            },
        ]
    }
)

VALID_DUPLICATES_JSON = json.dumps(
    {
        "duplicates": [
            {
                "canonical_index": 0,
                "duplicate_indices": [1],
                "reason": "Both errors indicate Firestore connection issues",
            }
        ]
    }
)


# ---------------------------------------------------------------------------
# Tests for _parse_llm_response
# ---------------------------------------------------------------------------


class TestParseLlmResponse:
    """Unit tests for the _parse_llm_response helper."""

    def test_valid_json_returns_incident_analysis(self):
        from backend.services.error_monitor.llm_analysis import _parse_llm_response

        result = _parse_llm_response(VALID_INCIDENTS_JSON)
        assert result is not None
        assert len(result.incidents) == 2
        assert result.used_llm is True

    def test_valid_json_incident_fields(self):
        from backend.services.error_monitor.llm_analysis import _parse_llm_response

        result = _parse_llm_response(VALID_INCIDENTS_JSON)
        assert result is not None
        first = result.incidents[0]
        assert first.title == "Firestore connectivity failure"
        assert first.root_cause == "Firestore is unavailable or misconfigured"
        assert first.severity == "P1"
        assert first.primary_service == "karaoke-backend"
        assert first.pattern_indices == [0]

    def test_invalid_json_returns_none(self):
        from backend.services.error_monitor.llm_analysis import _parse_llm_response

        result = _parse_llm_response("not valid json {{{")
        assert result is None

    def test_missing_incidents_key_returns_none(self):
        from backend.services.error_monitor.llm_analysis import _parse_llm_response

        result = _parse_llm_response(json.dumps({"data": []}))
        assert result is None

    def test_markdown_code_block_stripped(self):
        from backend.services.error_monitor.llm_analysis import _parse_llm_response

        wrapped = f"```json\n{VALID_INCIDENTS_JSON}\n```"
        result = _parse_llm_response(wrapped)
        assert result is not None
        assert len(result.incidents) == 2

    def test_markdown_code_block_without_language_stripped(self):
        from backend.services.error_monitor.llm_analysis import _parse_llm_response

        wrapped = f"```\n{VALID_INCIDENTS_JSON}\n```"
        result = _parse_llm_response(wrapped)
        assert result is not None
        assert len(result.incidents) == 2

    def test_empty_string_returns_none(self):
        from backend.services.error_monitor.llm_analysis import _parse_llm_response

        result = _parse_llm_response("")
        assert result is None

    def test_incidents_is_list_of_incident_objects(self):
        from backend.services.error_monitor.llm_analysis import (
            _parse_llm_response,
            Incident,
        )

        result = _parse_llm_response(VALID_INCIDENTS_JSON)
        assert result is not None
        for incident in result.incidents:
            assert isinstance(incident, Incident)


# ---------------------------------------------------------------------------
# Tests for analyze_patterns
# ---------------------------------------------------------------------------


class TestAnalyzePatterns:
    """Unit tests for analyze_patterns."""

    def test_returns_none_for_fewer_than_min_patterns(self):
        from backend.services.error_monitor.llm_analysis import analyze_patterns
        from backend.services.error_monitor.config import MIN_PATTERNS_FOR_ANALYSIS

        # Build a list with fewer patterns than the minimum
        few_patterns = SAMPLE_PATTERNS[: MIN_PATTERNS_FOR_ANALYSIS - 1]

        with patch(
            "backend.services.error_monitor.llm_analysis._call_llm"
        ) as mock_call:
            result = analyze_patterns(few_patterns)

        assert result is None
        mock_call.assert_not_called()

    def test_groups_patterns_into_incidents(self):
        from backend.services.error_monitor.llm_analysis import analyze_patterns

        with patch(
            "backend.services.error_monitor.llm_analysis._call_llm",
            return_value=VALID_INCIDENTS_JSON,
        ):
            result = analyze_patterns(SAMPLE_PATTERNS)

        assert result is not None
        assert len(result.incidents) == 2
        assert result.used_llm is True

    def test_incident_has_correct_fields(self):
        from backend.services.error_monitor.llm_analysis import analyze_patterns

        with patch(
            "backend.services.error_monitor.llm_analysis._call_llm",
            return_value=VALID_INCIDENTS_JSON,
        ):
            result = analyze_patterns(SAMPLE_PATTERNS)

        assert result is not None
        incident = result.incidents[0]
        assert incident.title == "Firestore connectivity failure"
        assert incident.severity == "P1"
        assert incident.primary_service == "karaoke-backend"

    def test_falls_back_on_llm_exception(self):
        from backend.services.error_monitor.llm_analysis import analyze_patterns

        with patch(
            "backend.services.error_monitor.llm_analysis._call_llm",
            side_effect=Exception("LLM unavailable"),
        ):
            result = analyze_patterns(SAMPLE_PATTERNS)

        # Should fall back to service grouping, not None
        assert result is not None
        assert result.used_llm is False

    def test_fallback_groups_by_service(self):
        from backend.services.error_monitor.llm_analysis import analyze_patterns

        with patch(
            "backend.services.error_monitor.llm_analysis._call_llm",
            side_effect=Exception("LLM unavailable"),
        ):
            result = analyze_patterns(SAMPLE_PATTERNS)

        assert result is not None
        # Each unique service should map to one incident
        services = {inc.primary_service for inc in result.incidents}
        expected_services = {p["service"] for p in SAMPLE_PATTERNS}
        assert services == expected_services

    def test_falls_back_on_invalid_llm_response(self):
        from backend.services.error_monitor.llm_analysis import analyze_patterns

        with patch(
            "backend.services.error_monitor.llm_analysis._call_llm",
            return_value="invalid json",
        ):
            result = analyze_patterns(SAMPLE_PATTERNS)

        # Should fall back to service grouping on parse failure
        assert result is not None
        assert result.used_llm is False

    def test_exact_min_patterns_triggers_analysis(self):
        """Exactly MIN_PATTERNS_FOR_ANALYSIS patterns should trigger the LLM."""
        from backend.services.error_monitor.llm_analysis import analyze_patterns
        from backend.services.error_monitor.config import MIN_PATTERNS_FOR_ANALYSIS

        # Ensure we have exactly MIN_PATTERNS_FOR_ANALYSIS patterns
        exactly_min = SAMPLE_PATTERNS[:MIN_PATTERNS_FOR_ANALYSIS]

        with patch(
            "backend.services.error_monitor.llm_analysis._call_llm",
            return_value=VALID_INCIDENTS_JSON,
        ) as mock_call:
            result = analyze_patterns(exactly_min)

        mock_call.assert_called_once()


# ---------------------------------------------------------------------------
# Tests for _fallback_group_by_service
# ---------------------------------------------------------------------------


class TestFallbackGroupByService:
    """Unit tests for _fallback_group_by_service."""

    def test_groups_by_service(self):
        from backend.services.error_monitor.llm_analysis import _fallback_group_by_service

        result = _fallback_group_by_service(SAMPLE_PATTERNS)
        services = {inc.primary_service for inc in result.incidents}
        assert services == {"karaoke-backend", "audio-separation-job", "audio-download-job"}

    def test_used_llm_is_false(self):
        from backend.services.error_monitor.llm_analysis import _fallback_group_by_service

        result = _fallback_group_by_service(SAMPLE_PATTERNS)
        assert result.used_llm is False

    def test_severity_is_p2(self):
        from backend.services.error_monitor.llm_analysis import _fallback_group_by_service

        result = _fallback_group_by_service(SAMPLE_PATTERNS)
        for incident in result.incidents:
            assert incident.severity == "P2"

    def test_pattern_indices_are_populated(self):
        from backend.services.error_monitor.llm_analysis import _fallback_group_by_service

        # Two patterns for the same service
        two_patterns = [
            {
                "service": "karaoke-backend",
                "normalized_message": "Error A",
                "count": 3,
            },
            {
                "service": "karaoke-backend",
                "normalized_message": "Error B",
                "count": 2,
            },
        ]
        result = _fallback_group_by_service(two_patterns)
        assert len(result.incidents) == 1
        assert result.incidents[0].pattern_indices == [0, 1]

    def test_empty_patterns_returns_empty_incidents(self):
        from backend.services.error_monitor.llm_analysis import _fallback_group_by_service

        result = _fallback_group_by_service([])
        assert result.incidents == []
        assert result.used_llm is False


# ---------------------------------------------------------------------------
# Tests for find_duplicate_patterns
# ---------------------------------------------------------------------------


class TestFindDuplicatePatterns:
    """Unit tests for find_duplicate_patterns."""

    def test_returns_empty_for_empty_new_patterns(self):
        from backend.services.error_monitor.llm_analysis import find_duplicate_patterns

        with patch(
            "backend.services.error_monitor.llm_analysis._call_llm"
        ) as mock_call:
            result = find_duplicate_patterns([], SAMPLE_PATTERNS)

        assert result == []
        mock_call.assert_not_called()

    def test_returns_empty_for_empty_existing_patterns(self):
        from backend.services.error_monitor.llm_analysis import find_duplicate_patterns

        with patch(
            "backend.services.error_monitor.llm_analysis._call_llm"
        ) as mock_call:
            result = find_duplicate_patterns(SAMPLE_PATTERNS, [])

        assert result == []
        mock_call.assert_not_called()

    def test_identifies_duplicates(self):
        from backend.services.error_monitor.llm_analysis import find_duplicate_patterns

        new_patterns = [
            {
                "service": "karaoke-backend",
                "normalized_message": "Cannot reach Firestore endpoint",
            },
            {
                "service": "karaoke-backend",
                "normalized_message": "Firestore deadline exceeded",
            },
        ]
        existing_patterns = [
            {
                "service": "karaoke-backend",
                "normalized_message": "Connection refused to Firestore",
            }
        ]

        with patch(
            "backend.services.error_monitor.llm_analysis._call_llm",
            return_value=VALID_DUPLICATES_JSON,
        ):
            result = find_duplicate_patterns(new_patterns, existing_patterns)

        assert len(result) == 1
        assert result[0].canonical_index == 0
        assert result[0].duplicate_indices == [1]
        assert "Firestore" in result[0].reason

    def test_returns_empty_on_no_duplicates_in_response(self):
        from backend.services.error_monitor.llm_analysis import find_duplicate_patterns

        no_dups_json = json.dumps({"duplicates": []})

        with patch(
            "backend.services.error_monitor.llm_analysis._call_llm",
            return_value=no_dups_json,
        ):
            result = find_duplicate_patterns(SAMPLE_PATTERNS[:1], SAMPLE_PATTERNS[1:])

        assert result == []

    def test_returns_empty_on_llm_failure(self):
        from backend.services.error_monitor.llm_analysis import find_duplicate_patterns

        with patch(
            "backend.services.error_monitor.llm_analysis._call_llm",
            side_effect=Exception("LLM unavailable"),
        ):
            result = find_duplicate_patterns(SAMPLE_PATTERNS[:1], SAMPLE_PATTERNS[1:])

        assert result == []

    def test_returns_empty_on_invalid_response(self):
        from backend.services.error_monitor.llm_analysis import find_duplicate_patterns

        with patch(
            "backend.services.error_monitor.llm_analysis._call_llm",
            return_value="not json",
        ):
            result = find_duplicate_patterns(SAMPLE_PATTERNS[:1], SAMPLE_PATTERNS[1:])

        assert result == []

    def test_duplicate_group_fields(self):
        from backend.services.error_monitor.llm_analysis import (
            find_duplicate_patterns,
            DuplicateGroup,
        )

        with patch(
            "backend.services.error_monitor.llm_analysis._call_llm",
            return_value=VALID_DUPLICATES_JSON,
        ):
            result = find_duplicate_patterns(SAMPLE_PATTERNS[:2], SAMPLE_PATTERNS[2:])

        assert len(result) == 1
        assert isinstance(result[0], DuplicateGroup)

    def test_uses_low_temperature(self):
        """find_duplicate_patterns should call _call_llm with temperature=0.1."""
        from backend.services.error_monitor.llm_analysis import find_duplicate_patterns

        with patch(
            "backend.services.error_monitor.llm_analysis._call_llm",
            return_value=VALID_DUPLICATES_JSON,
        ) as mock_call:
            find_duplicate_patterns(SAMPLE_PATTERNS[:1], SAMPLE_PATTERNS[1:])

        # Check the temperature kwarg was 0.1
        call_kwargs = mock_call.call_args
        assert call_kwargs is not None
        # temperature is the 3rd positional arg or keyword arg
        if call_kwargs.kwargs.get("temperature") is not None:
            assert call_kwargs.kwargs["temperature"] == 0.1
        else:
            # positional: (system_prompt, user_prompt, temperature)
            assert call_kwargs.args[2] == 0.1


# ---------------------------------------------------------------------------
# Tests for dataclasses
# ---------------------------------------------------------------------------


class TestDataclasses:
    """Tests for the exported dataclasses."""

    def test_incident_dataclass(self):
        from backend.services.error_monitor.llm_analysis import Incident

        inc = Incident(
            title="Test",
            root_cause="Root cause",
            severity="P1",
            suggested_fix="Fix it",
            primary_service="karaoke-backend",
            pattern_indices=[0, 1],
        )
        assert inc.title == "Test"
        assert inc.severity == "P1"
        assert inc.pattern_indices == [0, 1]

    def test_incident_nullable_fields(self):
        from backend.services.error_monitor.llm_analysis import Incident

        inc = Incident(
            title="Test",
            root_cause=None,
            severity="P3",
            suggested_fix=None,
            primary_service="audio-separator",
            pattern_indices=[2],
        )
        assert inc.root_cause is None
        assert inc.suggested_fix is None

    def test_incident_analysis_dataclass(self):
        from backend.services.error_monitor.llm_analysis import IncidentAnalysis, Incident

        ia = IncidentAnalysis(incidents=[])
        assert ia.incidents == []
        assert ia.used_llm is True  # default

    def test_incident_analysis_used_llm_false(self):
        from backend.services.error_monitor.llm_analysis import IncidentAnalysis

        ia = IncidentAnalysis(incidents=[], used_llm=False)
        assert ia.used_llm is False

    def test_duplicate_group_dataclass(self):
        from backend.services.error_monitor.llm_analysis import DuplicateGroup

        dg = DuplicateGroup(
            canonical_index=0,
            duplicate_indices=[1, 2],
            reason="Same root cause",
        )
        assert dg.canonical_index == 0
        assert dg.duplicate_indices == [1, 2]
        assert dg.reason == "Same root cause"
