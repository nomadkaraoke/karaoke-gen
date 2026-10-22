"""Tests for Bulk Mode backend foundations.

Covers:
- batch_id summary projection regression (Task 1.1)
- pick_auto_selection tier-1 helper (Task 1.2), mirroring the frontend
  tier rule in frontend/lib/audio-search-utils.ts (getSearchConfidence tier 1).
"""


class TestBatchIdProjection:
    """Regression: batch_id must be projected so the dashboard can group/filter."""

    def test_batch_id_in_summary_field_paths(self):
        from backend.services.firestore_service import FirestoreService
        assert 'state_data.batch_id' in FirestoreService.SUMMARY_FIELD_PATHS

    def test_batch_id_in_summary_state_data_keys(self):
        from backend.api.routes.jobs import _SUMMARY_STATE_DATA_KEYS
        assert 'batch_id' in _SUMMARY_STATE_DATA_KEYS


def _lossless_best(index=0, seeders=80, title="Bohemian Rhapsody", target_file=None):
    """A confident lossless result -> BEST CHOICE category."""
    return {
        "index": index,
        "title": title,
        "provider": "RED",
        "is_lossless": True,
        "seeders": seeders,
        "release_type": "album",
        "target_file": target_file,
        "quality_data": {"format": "FLAC", "bit_depth": 16, "media": "CD"},
    }


def _lossy(index=0, title="Bohemian Rhapsody"):
    return {
        "index": index,
        "title": title,
        "provider": "YouTube",
        "is_lossless": False,
        "seeders": None,
        "release_type": "",
        "target_file": None,
        "quality_data": {"format": "OPUS", "bitrate": 160},
    }


def _vinyl_lossless(index=0, seeders=120, title="Bohemian Rhapsody"):
    return {
        "index": index,
        "title": title,
        "provider": "RED",
        "is_lossless": True,
        "seeders": seeders,
        "release_type": "album",
        "target_file": None,
        "quality_data": {"format": "FLAC", "bit_depth": 24, "media": "vinyl"},
    }


class TestPickAutoSelection:
    """pick_auto_selection returns an index ONLY for a confident lossless (tier-1) pick."""

    def test_confident_lossless_returns_its_index(self):
        from backend.services.audio_search_service import pick_auto_selection
        results = [_lossy(0), _lossless_best(1, seeders=80)]
        assert pick_auto_selection(results, "Bohemian Rhapsody") == 1

    def test_only_lossy_returns_none(self):
        from backend.services.audio_search_service import pick_auto_selection
        assert pick_auto_selection([_lossy(0), _lossy(1)], "Bohemian Rhapsody") is None

    def test_lossless_but_low_seeders_returns_none(self):
        # seeders < 10 -> STUDIO ALBUMS, not BEST CHOICE -> tier 2, no auto-pick
        from backend.services.audio_search_service import pick_auto_selection
        assert pick_auto_selection([_lossless_best(0, seeders=9)], "Bohemian Rhapsody") is None

    def test_lossless_with_ten_seeders_is_auto_picked(self):
        # A modestly-seeded lossless album torrent (e.g. 15-31 seeders) is still the right pick.
        from backend.services.audio_search_service import pick_auto_selection
        assert pick_auto_selection([_lossy(0), _lossless_best(1, seeders=10)], "Bohemian Rhapsody") == 1

    def test_filename_mismatch_returns_none(self):
        # best is lossless+well-seeded but the file is clearly a different track
        from backend.services.audio_search_service import pick_auto_selection
        r = _lossless_best(0, seeders=80, title="Bohemian Rhapsody",
                           target_file="03 - Love of My Life.flac")
        assert pick_auto_selection([r], "Bohemian Rhapsody") is None

    def test_only_vinyl_lossless_returns_none(self):
        from backend.services.audio_search_service import pick_auto_selection
        assert pick_auto_selection([_vinyl_lossless(0)], "Bohemian Rhapsody") is None

    def test_empty_returns_none(self):
        from backend.services.audio_search_service import pick_auto_selection
        assert pick_auto_selection([], "Bohemian Rhapsody") is None

    def test_picks_highest_seeders_among_best_choice(self):
        from backend.services.audio_search_service import pick_auto_selection
        results = [_lossless_best(0, seeders=60), _lossless_best(1, seeders=200)]
        assert pick_auto_selection(results, "Bohemian Rhapsody") == 1

    def test_short_title_is_not_auto_selected(self):
        # Too-short titles can't be verified against the filename -> defer to human.
        from backend.services.audio_search_service import pick_auto_selection
        assert pick_auto_selection([_lossless_best(0, seeders=80, title="Go")], "Go") is None

    def test_partial_word_filename_is_a_mismatch(self):
        # Title "One" must NOT match a file for "Someone" (whole-word matching).
        from backend.services.audio_search_service import pick_auto_selection
        r = _lossless_best(0, seeders=80, title="Someone", target_file="04 - Someone.flac")
        assert pick_auto_selection([r], "One") is None


def test_best_choice_min_seeders_matches_frontend():
    """The tier-1 seeder bar is duplicated in the frontend; keep the two in lockstep."""
    import re
    from pathlib import Path
    from backend.services.audio_search_service import BEST_CHOICE_MIN_SEEDERS

    ts = (Path(__file__).resolve().parents[2] / "frontend" / "lib" / "audio-search-utils.ts").read_text()
    m = re.search(r"export const BEST_CHOICE_MIN_SEEDERS = (\d+)", ts)
    assert m, "BEST_CHOICE_MIN_SEEDERS not found in frontend/lib/audio-search-utils.ts"
    assert int(m.group(1)) == BEST_CHOICE_MIN_SEEDERS


class TestArtistMatch:
    """Port of isArtistMatch (frontend tier 1 requires the right artist)."""

    def _m(self, want, have):
        from backend.services.audio_search_service import is_artist_match
        return is_artist_match(want, {"artist": have})

    def test_exact_and_the_prefix(self):
        assert self._m("The Killers", "Killers")
        assert self._m("Chase & Status", "Chase & Status")

    def test_featured_credit_contains(self):
        assert self._m("Chase & Status", "Chase & Status, Delilah")

    def test_small_typo(self):
        assert self._m("Radiohead", "Radiohed")

    def test_non_latin_names(self):
        assert self._m("宇多田ヒカル", "宇多田ヒカル")
        assert self._m("Би-2", "би-2")
        assert not self._m("宇多田ヒカル", "椎名林檎")

    def test_ampersand_and_accents(self):
        assert self._m("Simon & Garfunkel", "Simon and Garfunkel")
        assert self._m("Beyoncé", "Beyonce")

    def test_uncomparable_search_name_is_not_held_against_result(self):
        assert self._m("!!!", "Chk Chk Chk")

    def test_different_artist(self):
        assert not self._m("Daft Punk", "Tribute Band Collective")
        assert not self._m("ABBA", "ABBX")  # short names: no typo tolerance

    def test_pick_auto_selection_rejects_wrong_artist(self):
        from backend.services.audio_search_service import pick_auto_selection
        r = _lossless_best(0, seeders=80, title="Bohemian Rhapsody")
        r["artist"] = "Tribute Band Collective"
        assert pick_auto_selection([r], "Bohemian Rhapsody", "Queen") is None
        assert pick_auto_selection([r], "Bohemian Rhapsody") == 0  # no artist given: not checked
