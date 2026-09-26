"""Tests for backend/services/tempo_label.py — tempo-change labeling of published outputs."""

import pytest

from backend.services.tempo_label import (
    apply_tempo_to_title,
    cumulative_tempo_factor,
    is_tempo_adjusted,
    strip_tempo_suffix,
    tempo_description_notice,
    tempo_percent,
    tempo_percent_from_title,
    tempo_suffix,
)
from backend.services.youtube_description import render_youtube_description


class TestCumulativeTempoFactor:
    def test_no_edits(self):
        assert cumulative_tempo_factor([]) == 1.0
        assert cumulative_tempo_factor(None) == 1.0

    def test_ignores_non_tempo_edits(self):
        stack = [
            {"operation": "trim_start", "params": {"end_seconds": 5}},
            {"operation": "tempo", "params": {"factor": 0.9}},
            {"operation": "fade_in", "params": {"start_seconds": 0, "end_seconds": 2}},
        ]
        assert cumulative_tempo_factor(stack) == pytest.approx(0.9)

    def test_compounds_multiple_tempo_edits(self):
        stack = [
            {"operation": "tempo", "params": {"factor": 0.9}},
            {"operation": "tempo", "params": {"factor": 0.9}},
        ]
        assert cumulative_tempo_factor(stack) == pytest.approx(0.81)

    def test_skips_malformed_entries(self):
        stack = [
            {"operation": "tempo", "params": {}},
            {"operation": "tempo", "params": {"factor": "abc"}},
            {"operation": "tempo", "params": {"factor": -1}},
            {"operation": "tempo"},
            {"operation": "tempo", "params": {"factor": 1.1}},
        ]
        assert cumulative_tempo_factor(stack) == pytest.approx(1.1)


class TestLabelFormatting:
    @pytest.mark.parametrize("factor,expected", [(0.9, 90), (0.81, 81), (1.1, 110), (0.8499, 85), (1.0, 100)])
    def test_tempo_percent(self, factor, expected):
        assert tempo_percent(factor) == expected

    @pytest.mark.parametrize("factor,expected", [
        (0.9, True), (1.2, True), (1.0, False), (1.004, False), (0.996, False), (None, False), (0, False),
    ])
    def test_is_tempo_adjusted(self, factor, expected):
        assert is_tempo_adjusted(factor) is expected

    def test_suffix(self):
        assert tempo_suffix(0.9) == "(90% Tempo)"

    def test_apply_labels_title(self):
        assert apply_tempo_to_title("Bohemian Rhapsody", 0.9) == "Bohemian Rhapsody (90% Tempo)"

    def test_apply_is_idempotent_and_replaces_existing_label(self):
        once = apply_tempo_to_title("Song", 0.9)
        assert apply_tempo_to_title(once, 0.9) == "Song (90% Tempo)"
        assert apply_tempo_to_title(once, 1.15) == "Song (115% Tempo)"

    def test_apply_with_unadjusted_factor_strips_label(self):
        assert apply_tempo_to_title("Song (90% Tempo)", 1.0) == "Song"
        assert apply_tempo_to_title("Song", None) == "Song"

    def test_apply_preserves_other_parentheticals(self):
        assert apply_tempo_to_title("Song (Live)", 0.95) == "Song (Live) (95% Tempo)"

    def test_apply_handles_empty_title(self):
        assert apply_tempo_to_title(None, 0.9) is None
        assert apply_tempo_to_title("", 0.9) == ""

    def test_strip_only_removes_trailing_tempo_label(self):
        assert strip_tempo_suffix("Song (90% Tempo)") == "Song"
        assert strip_tempo_suffix("Song (Remastered)") == "Song (Remastered)"
        assert strip_tempo_suffix("Tempo (90% Tempo) Song") == "Tempo (90% Tempo) Song"
        assert strip_tempo_suffix(None) is None

    def test_percent_from_title(self):
        assert tempo_percent_from_title("Song (85% Tempo)") == 85
        assert tempo_percent_from_title("Song") is None
        assert tempo_percent_from_title(None) is None


class TestDescriptionNotice:
    def test_slowed(self):
        notice = tempo_description_notice("Song (90% Tempo)")
        assert "slowed down to 90%" in notice
        assert "same key" in notice

    def test_sped_up(self):
        assert "sped up to 110%" in tempo_description_notice("Song (110% Tempo)")

    def test_no_label_no_notice(self):
        assert tempo_description_notice("Song") == ""

    def test_youtube_description_leads_with_notice(self):
        desc = render_youtube_description(
            artist="Queen", title="Bohemian Rhapsody (90% Tempo)",
            brand_code="NOMAD-1234", template="Karaoke of {artist} - {title}\nBrand Code: {brand_code}",
        )
        assert desc.startswith("Note: this karaoke version has been slowed down to 90%")
        assert "Karaoke of Queen - Bohemian Rhapsody (90% Tempo)" in desc
        assert "NOMAD-1234" in desc

    def test_youtube_description_unchanged_without_label(self):
        desc = render_youtube_description(
            artist="Queen", title="Bohemian Rhapsody", brand_code=None, template="Karaoke of {artist} - {title}",
        )
        assert desc == "Karaoke of Queen - Bohemian Rhapsody"


class TestRoundingParityWithFrontend:
    """tempo_percent must round exactly like frontend Math.round, or the editor's
    promised label differs from the published one (e.g. 90% x 105% = 94.5%)."""

    @pytest.mark.parametrize("factors,expected", [
        ((0.9, 1.05), 95),
        ((0.85, 0.9), 77),
        ((0.95, 1.1), 105),
        ((0.9, 0.9), 81),
    ])
    def test_compound_presets_round_half_up(self, factors, expected):
        stack = [{"operation": "tempo", "params": {"factor": f}} for f in factors]
        factor = cumulative_tempo_factor(stack)
        assert tempo_percent(factor) == expected
        assert apply_tempo_to_title("Song", factor) == f"Song ({expected}% Tempo)"
