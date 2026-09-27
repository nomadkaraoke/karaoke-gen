"""
Tests for kn_data_sync/main.py — the kjbox song-identification index export.

Run: cd infrastructure/functions/kn_data_sync && python -m pytest test_main.py
(Cloud Function tests aren't in the repo-wide testpaths.)
"""
import json
import os
import sys
from unittest.mock import MagicMock

import pytest

_functions_framework = MagicMock()
_functions_framework.http = lambda fn: fn
for name, mod in (
    ("functions_framework", _functions_framework),
    ("google.cloud.bigquery", MagicMock()),
    ("google.cloud.storage", MagicMock()),
):
    sys.modules.setdefault(name, mod)

sys.path.insert(0, os.path.dirname(__file__))

import main  # noqa: E402


class _Blob:
    def __init__(self, name, store):
        self.name, self._store = name, store

    def delete(self):
        self._store.pop(self.name, None)


class FakeGCS:
    """In-memory bucket: list_blobs by prefix, upload, delete."""

    def __init__(self, names=()):
        self.objects = {n: b"" for n in names}
        self.uploaded = {}

    def list_blobs(self, bucket, prefix=""):
        return [_Blob(n, self.objects) for n in sorted(self.objects) if n.startswith(prefix)]

    def bucket(self, _name):
        gcs = self

        class B:
            def blob(self, name):
                blob = MagicMock()
                blob.upload_from_string.side_effect = lambda data, content_type=None: (
                    gcs.uploaded.__setitem__(name, data), gcs.objects.__setitem__(name, data))
                return blob
        return B()


@pytest.fixture
def clients(monkeypatch):
    bq = MagicMock()
    old_runs = [f"song-id/2026010{i}-000000/songs-000000000000.tsv.gz" for i in range(1, 5)]
    gcs = FakeGCS(old_runs)

    def run_export(sql):
        # The export writes shards into the run folder named in the URI.
        folder = sql.split(f"gs://{main.GCS_BUCKET}/")[1].split("/songs-")[0]
        for k in range(2):
            gcs.objects[f"{folder}/songs-00000000000{k}.tsv.gz"] = b""
        job = MagicMock()
        return job
    bq.query.side_effect = run_export
    monkeypatch.setattr(main.bigquery, "Client", lambda project=None: bq)
    monkeypatch.setattr(main.storage, "Client", lambda project=None: gcs)
    return bq, gcs


def test_export_writes_manifest_for_this_run_only(clients):
    bq, gcs = clients
    out = main._export_song_id_index()
    sql = bq.query.call_args[0][0]
    assert sql.startswith("EXPORT DATA OPTIONS(uri='gs://nomadkaraoke-kn-data/song-id/")
    assert "compression='GZIP'" in sql and "karaokenerds_raw" in sql and "spotify_tracks_normalized" in sql
    manifest = json.loads(gcs.uploaded["song-id/latest.json"])
    assert manifest["run"] == out["run"] and out["shards"] == 2
    assert all(f"/song-id/{out['run']}/" in s for s in manifest["shards"])
    assert manifest["columns"] == ["artist", "title", "popularity", "karaoke"]


def test_export_prunes_all_but_the_newest_runs(clients):
    _bq, gcs = clients
    main._export_song_id_index()
    runs = {n.split("/")[1] for n in gcs.objects if n.count("/") >= 2}
    assert len(runs) == main.SONG_ID_KEEP_RUNS
    assert "20260101-000000" not in runs


def test_export_with_no_output_raises(clients, monkeypatch):
    bq, _gcs = clients
    bq.query.side_effect = None
    with pytest.raises(RuntimeError, match="no files"):
        main._export_song_id_index()


def test_full_sync_survives_export_failure(monkeypatch):
    monkeypatch.setattr(main, "KARAOKENERDS_API_KEY", "k")
    monkeypatch.setattr(main, "_fetch_kn_data", lambda url: (b"[]", 0))
    monkeypatch.setattr(main, "_store_to_gcs", lambda raw, prefix: "full/x.json.gz")
    monkeypatch.setattr(main, "_load_songs_to_bigquery", lambda raw: 0)
    monkeypatch.setattr(main, "_export_song_id_index", MagicMock(side_effect=RuntimeError("bq down")))
    req = MagicMock()
    req.get_json.return_value = {"mode": "full"}
    body, status, _ = main.sync_kn_data(req)
    data = json.loads(body)
    assert status == 200 and data["status"] == "ok"
    assert data["song_id_index"] == {"error": "bq down"}


def test_community_sync_does_not_export(monkeypatch):
    monkeypatch.setattr(main, "KARAOKENERDS_API_KEY", "k")
    monkeypatch.setattr(main, "_fetch_kn_data", lambda url: (b"[]", 0))
    monkeypatch.setattr(main, "_store_to_gcs", lambda raw, prefix: "community/x.json.gz")
    monkeypatch.setattr(main, "_load_community_to_bigquery", lambda raw: 0)
    export = MagicMock()
    monkeypatch.setattr(main, "_export_song_id_index", export)
    req = MagicMock()
    req.get_json.return_value = {"mode": "community"}
    body, status, _ = main.sync_kn_data(req)
    assert status == 200 and json.loads(body)["song_id_index"] is None
    export.assert_not_called()
