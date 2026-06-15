"""Tests for the deterministic + catalog match classifier.

The classifier decides, WITHOUT any AI/network call, whether a typed
artist/title cosmetically matches a catalog candidate (so we can silently
tidy formatting) or whether the decision must be escalated to the AI judge.
"""
from backend.services.match_judge.classifier import (
    normalize_for_match,
    classify_catalog_match,
)


def _cand(artist: str, title: str) -> dict:
    return {"artist_name": artist, "track_name": title}


class TestNormalizeForMatch:
    def test_casing_and_whitespace_collapse_to_same_key(self):
        assert normalize_for_match("Paramore") == normalize_for_match("  paramore ")

    def test_punctuation_is_ignored(self):
        assert normalize_for_match("Big Man, Little Dignity") == normalize_for_match(
            "big man little dignity"
        )

    def test_ampersand_and_and_are_equivalent(self):
        assert normalize_for_match("Jay-Z & Beyonce") == normalize_for_match(
            "Jay Z and Beyonce"
        )


class TestClassifyCatalogMatch:
    def test_casing_only_difference_is_cosmetic(self):
        verdict = classify_catalog_match(
            "paramore",
            "big man, little dignity",
            [_cand("Paramore", "Big Man, Little Dignity")],
        )
        assert verdict is not None
        assert verdict.kind == "cosmetic"
        assert verdict.confident is True
        assert verdict.canonical_artist == "Paramore"
        assert verdict.canonical_title == "Big Man, Little Dignity"
        assert verdict.engine == "catalog"

    def test_exact_match_needs_no_change(self):
        verdict = classify_catalog_match(
            "Paramore",
            "Big Man, Little Dignity",
            [_cand("Paramore", "Big Man, Little Dignity")],
        )
        assert verdict is not None
        assert verdict.kind == "none"
        assert verdict.confident is True

    def test_no_catalog_match_escalates_to_ai(self):
        # Returns None => the service should call the AI judge.
        verdict = classify_catalog_match(
            "queen",
            "bohemian rhapsody",
            [_cand("Paramore", "Big Man, Little Dignity")],
        )
        assert verdict is None

    def test_empty_candidates_escalates_to_ai(self):
        assert classify_catalog_match("queen", "bohemian rhapsody", []) is None
