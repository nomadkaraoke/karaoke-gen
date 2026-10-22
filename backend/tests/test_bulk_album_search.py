"""Album-mode search helpers: per-track results from one album search."""

from backend.services.bulk_album_search import (
    build_track_results,
    choose_batch_torrent,
    pick_album_auto_selection,
)


def _torrent(source_id, track_files, seeders=31, media="CD", lossless=True, artist="Daft Punk"):
    return {
        "title": "Discovery", "artist": artist, "provider": "RED", "source_id": source_id,
        "quality": "FLAC 16bit CD", "quality_data": {"format": "FLAC", "bit_depth": 16, "media": media},
        "is_lossless": lossless, "seeders": seeders, "release_type": "Album",
        "track_files": {k: {"target_file": f, "target_file_size": 10, "match_score": 1.0}
                        for k, f in track_files.items()},
    }


class TestChooseBatchTorrent:
    def test_first_best_choice_in_flacfetch_order(self):
        low = _torrent("low", {"a": "01 A.flac"}, seeders=3)
        ok = _torrent("ok", {"a": "01 A.flac"}, seeders=12)
        assert choose_batch_torrent([low, ok])["source_id"] == "ok"

    def test_vinyl_never_batch(self):
        assert choose_batch_torrent([_torrent("v", {"a": "A1 A.flac"}, seeders=900, media="Vinyl")]) is None

    def test_none_when_empty(self):
        assert choose_batch_torrent([]) is None


class TestBuildTrackResults:
    def test_batch_torrent_first_and_result_shape(self):
        other = _torrent("other", {"j1": "1-01 One More Time.flac"}, seeders=90)
        batch = _torrent("batch", {"j1": "01 One More Time.flac", "j2": "02 Aerodynamic.flac"})
        results = build_track_results([other, batch], "j1", batch)
        assert [r["source_id"] for r in results] == ["batch", "other"]
        first = results[0]
        assert first["index"] == 0 and results[1]["index"] == 1
        assert first["provider"] == "RED" and first["target_file"] == "01 One More Time.flac"
        assert first["is_lossless"] is True and first["seeders"] == 31
        assert first["quality_data"]["media"] == "CD"

    def test_only_torrents_covering_the_track(self):
        batch = _torrent("batch", {"j1": "01 X.flac"})
        assert build_track_results([batch], "j2", batch) == []


class TestPickAlbumAutoSelection:
    def _results(self, **kw):
        batch = _torrent("batch", {"j1": "01 One More Time.flac"}, **kw)
        return build_track_results([batch], "j1", batch), batch

    def test_confident(self):
        results, batch = self._results()
        assert pick_album_auto_selection(results, batch, "One More Time", "Daft Punk") == 0

    def test_low_seeders(self):
        results, batch = self._results(seeders=9)
        assert pick_album_auto_selection(results, batch, "One More Time", "Daft Punk") is None

    def test_wrong_artist(self):
        results, batch = self._results(artist="Tribute Band Collective")
        assert pick_album_auto_selection(results, batch, "One More Time", "Daft Punk") is None

    def test_filename_names_another_track(self):
        batch = _torrent("batch", {"j1": "05 Something Else.flac"})
        results = build_track_results([batch], "j1", batch)
        assert pick_album_auto_selection(results, batch, "One More Time", "Daft Punk") is None

    def test_track_only_in_another_torrent_is_not_auto_picked(self):
        batch = _torrent("batch", {"j2": "02 Aerodynamic.flac"})
        other = _torrent("other", {"j1": "01 One More Time.flac"}, seeders=200)
        results = build_track_results([batch, other], "j1", batch)
        assert pick_album_auto_selection(results, batch, "One More Time", "Daft Punk") is None

    def test_no_batch_torrent(self):
        results, _ = self._results()
        assert pick_album_auto_selection(results, None, "One More Time", "Daft Punk") is None
