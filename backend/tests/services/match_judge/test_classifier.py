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

    def test_non_latin_scripts_are_preserved(self):
        # Regression: the ASCII-only filter erased every non-Latin character, so
        # all Cyrillic/CJK/etc. strings collapsed to "" and compared equal.
        assert normalize_for_match("Луна") == "луна"
        assert normalize_for_match("永活盼望") == "永活盼望"
        assert normalize_for_match("Луна") != normalize_for_match("从灵乐队")

    def test_non_latin_casing_punctuation_and_whitespace_fold(self):
        assert normalize_for_match("Золотые  Лепестки!") == normalize_for_match(
            "золотые лепестки"
        )

    def test_latin_accents_still_fold(self):
        assert normalize_for_match("Beyoncé") == "beyonce"
        assert normalize_for_match("Sigur Rós") == "sigur ros"

    def test_non_latin_combining_marks_are_kept(self):
        # Japanese voiced kana (が) must not collapse to unvoiced (か); Hindi
        # vowel signs must survive.
        assert normalize_for_match("が") != normalize_for_match("か")
        assert normalize_for_match("गीत") == "गीत"
        assert normalize_for_match("गति") != normalize_for_match("गत")

    def test_underscore_is_treated_as_punctuation(self):
        assert normalize_for_match("foo_bar") == "foo bar"


class TestClassifyCatalogMatch:
    def test_different_non_latin_songs_are_not_cosmetic_matches(self):
        verdict = classify_catalog_match(
            "Луна", "Золотые Лепестки", [_cand("从灵乐队", "永活盼望")]
        )
        assert verdict is None or verdict.kind != "cosmetic"

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
