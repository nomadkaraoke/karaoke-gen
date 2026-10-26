"""Unit tests for the unattended (daily community pick) audio-source choice."""
from backend.services.community_audio_pick import pick_confident_audio


def _r(index, provider, artist, track, **kw):
    return dict(index=index, provider=provider, artist=artist, target_file=track, **kw)


def test_exact_spotify_match_is_picked():
    results = [_r(0, "Spotify", "FIGHTMASTER", "Nobody\u2019s Baby", duration=211)]
    assert pick_confident_audio(results, "FIGHTMASTER", "Nobody's Baby") == 0


def test_never_defaults_to_first_result():
    # The 2026-10-24 bug: result #0 was used blindly. A different artist's track must not win.
    results = [_r(0, "Spotify", "Someone Else", "Lemon Drop - Orchestral Version", duration=200),
               _r(1, "Spotify", "Raynes", "Lemon Drop - Orchestral Version", duration=210)]
    assert pick_confident_audio(results, "Raynes", "Lemon Drop - Orchestral Version") == 1


def test_version_suffix_must_match():
    results = [_r(0, "Spotify", "Raynes", "Lemon Drop", duration=200)]
    assert pick_confident_audio(results, "Raynes", "Lemon Drop - Orchestral Version") is None


def test_cover_by_other_artist_is_not_confident():
    results = [_r(7, "Spotify", "Romans", "House Of The Rising Sun (JEREMY RENNER)", duration=215)]
    assert pick_confident_audio(results, "Jeremy Renner", "House of the Rising Sun") is None


def test_well_seeded_torrent_beats_spotify_and_track_number_is_ignored():
    results = [_r(0, "RED", "Taylor Hicks", "08 The Maze.flac", seeders=6),
               _r(4, "Spotify", "Taylor Hicks", "The Maze", duration=167)]
    assert pick_confident_audio(results, "Taylor Hicks", "The Maze") == 0


def test_thinly_seeded_torrent_falls_back_to_spotify():
    results = [_r(0, "RED", "Jeremy Renner", "05 - Sippy Cup.flac", seeders=1),
               _r(2, "Spotify", "Jeremy Renner", "Sippy Cup", duration=214)]
    assert pick_confident_audio(results, "Jeremy Renner", "Sippy Cup") == 2


def test_credited_co_artist_matches():
    results = [_r(3, "Spotify", "Ice Cube, Krayzie Bone", "Until We Rich", duration=250)]
    assert pick_confident_audio(results, "Krayzie Bone", "Until We Rich") == 3


def test_different_recordings_with_same_title_are_ambiguous():
    results = [_r(0, "Spotify", "X", "Live", duration=100), _r(1, "Spotify", "X", "Live", duration=300)]
    assert pick_confident_audio(results, "X", "Live") is None


def test_single_and_album_copies_of_one_recording_are_fine():
    results = [_r(2, "Spotify", "PEGGY", "FLIGHT RISK", duration=163),
               _r(3, "Spotify", "PEGGY", "FLIGHT RISK", duration=163)]
    assert pick_confident_audio(results, "PEGGY", "FLIGHT RISK") == 2


def test_youtube_and_no_results_are_never_auto_picked():
    results = [dict(index=0, provider="YouTube", artist="Raynes", title="Lemon Drop", target_file=None)]
    assert pick_confident_audio(results, "Raynes", "Lemon Drop") is None
    assert pick_confident_audio([], "Raynes", "Lemon Drop") is None
