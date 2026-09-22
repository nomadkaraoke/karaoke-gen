"""Tests for export_catalog.py — the divebar_catalog → GCS snapshot.

The export feeds the kjbox local catalog mirror, so the tests pin the two
things kjbox depends on: the NDJSON row shape (CF `search` column set,
including the in_gcs boolean) and the row_count/exported_at metadata.
"""
import gzip
import io
import json
import os
import sys
from unittest.mock import MagicMock

import pytest

# Stub Google client libs (not installed in the test env). The real modules
# are imported lazily inside export_catalog_to_gcs, so stubbing the module
# path is enough. test_main.py registers overlapping stubs via setdefault —
# re-fetch from sys.modules after registering so BOTH files always talk to
# the stub that actually won, regardless of collection order. (Deliberately
# no scheduler_v1 stub here: test_main.py's fixtures own that one, and
# nothing in this file triggers its lazy import.)
for name, mod in (
    ("functions_framework", MagicMock(http=lambda fn: fn)),
    ("google.cloud.bigquery", MagicMock()),
    ("google.cloud.storage", MagicMock()),
    ("drive_client", MagicMock()),
    ("filename_parser", MagicMock()),
    ("index_builder", MagicMock()),
):
    sys.modules.setdefault(name, mod)
_bigquery_mod = sys.modules["google.cloud.bigquery"]
_storage_mod = sys.modules["google.cloud.storage"]

sys.path.insert(0, os.path.dirname(__file__))

import export_catalog  # noqa: E402


@pytest.fixture
def _clients(monkeypatch):
    """Wire stub BQ rows + a capturing storage blob; returns the capture dict."""
    captured = {}

    rows = [
        {"file_id": "f1", "brand": "Sandell", "brand_code": "SDK",
         "artist": "José Feliciano", "title": "Feliz Navidad",
         "filename": "(SDK) José Feliciano - Feliz Navidad.cdg", "format": "cdg",
         "file_size": 12345, "drive_path": "SDK/x.cdg", "in_gcs": True},
        {"file_id": "f2", "brand": "Nomad Karaoke", "brand_code": "NOMAD",
         "artist": "Maxïmo Park", "title": "Books from Boxes",
         "filename": "NOMAD-0001.mp4", "format": "mp4",
         "file_size": 999, "drive_path": "NOMAD/y.mp4", "in_gcs": False},
    ]

    bq_client = MagicMock()
    # bigquery Row behaves like a mapping under dict(); plain dicts do too.
    bq_client.query.return_value.result.return_value = rows
    _bigquery_mod.Client = MagicMock(return_value=bq_client)

    blob = MagicMock()

    def _upload(data, content_type=None):
        captured["bytes"] = data
        captured["content_type"] = content_type
        captured["metadata"] = blob.metadata

    blob.upload_from_string.side_effect = _upload
    storage_client = MagicMock()
    storage_client.bucket.return_value.blob.return_value = blob
    captured["storage_client"] = storage_client
    _storage_mod.Client = MagicMock(return_value=storage_client)
    return captured


class TestExportCatalog:
    def test_writes_gzipped_ndjson_with_cf_column_set(self, _clients):
        result = export_catalog.export_catalog_to_gcs("nomadkaraoke")

        assert result["rows"] == 2
        assert result["gcs_uri"] == (
            "gs://nomadkaraoke-divebar-files/exports/divebar-catalog-latest.json.gz")

        lines = gzip.decompress(_clients["bytes"]).decode("utf-8").strip().split("\n")
        assert len(lines) == 2
        row = json.loads(lines[0])
        # kjbox mirror depends on exactly this shape (CF `search` column set).
        assert set(row) == {"file_id", "brand", "brand_code", "artist", "title",
                            "filename", "format", "file_size", "drive_path", "in_gcs"}
        assert row["artist"] == "José Feliciano"  # accents survive round-trip
        assert row["in_gcs"] is True
        assert json.loads(lines[1])["in_gcs"] is False

    def test_metadata_carries_row_count(self, _clients):
        export_catalog.export_catalog_to_gcs("nomadkaraoke")
        assert _clients["metadata"]["row_count"] == "2"
        assert "exported_at" in _clients["metadata"]
        assert _clients["content_type"] == "application/gzip"

    def test_uploads_to_public_files_bucket(self, _clients):
        export_catalog.export_catalog_to_gcs("nomadkaraoke")
        _clients["storage_client"].bucket.assert_called_once_with(
            "nomadkaraoke-divebar-files")

    def test_export_sql_orders_by_file_id(self):
        # Deterministic row order is required for the gzip determinism to
        # mean anything — without ORDER BY, BigQuery may reorder identical
        # rows and defeat the kjbox content-hash skip.
        assert "ORDER BY file_id" in export_catalog._EXPORT_SQL

    def test_deterministic_gzip_for_identical_content(self, _clients):
        export_catalog.export_catalog_to_gcs("nomadkaraoke")
        first = _clients["bytes"]
        export_catalog.export_catalog_to_gcs("nomadkaraoke")
        # mtime=0 → identical bytes for identical rows, so the kjbox sync's
        # content-hash check can skip rebuilds.
        assert _clients["bytes"] == first


class TestMainWiring:
    def test_export_failure_never_fails_the_index_build(self, monkeypatch):
        import main
        monkeypatch.setattr(main, "get_drive_service", MagicMock())
        monkeypatch.setattr(main, "list_divebar_recursive",
                            MagicMock(return_value=[{"name": "a.mp4"}]))
        monkeypatch.setattr(main, "should_index_file", MagicMock(return_value=True))
        monkeypatch.setattr(main, "build_rows", MagicMock(return_value=[{"x": 1}]))
        monkeypatch.setattr(main, "load_to_bigquery", MagicMock(return_value=1))
        monkeypatch.setattr(main, "export_catalog_to_gcs",
                            MagicMock(side_effect=RuntimeError("gcs down")))

        class Req:
            method = "POST"
            args = {}

            def get_json(self, silent=False):
                return {}

        body, status, _ = main.sync_divebar_index(Req())
        assert status == 200
        payload = json.loads(body)
        assert payload["status"] == "ok"
        assert payload["catalog_export"] == {"error": "gcs down"}

    def test_no_rows_skips_export_entirely(self, monkeypatch):
        # Empty Drive listing -> load_to_bigquery returns without MERGING;
        # exporting would re-snapshot a table this run didn't touch.
        import main
        monkeypatch.setattr(main, "get_drive_service", MagicMock())
        monkeypatch.setattr(main, "list_divebar_recursive",
                            MagicMock(return_value=[]))
        monkeypatch.setattr(main, "should_index_file", MagicMock(return_value=True))
        monkeypatch.setattr(main, "build_rows", MagicMock(return_value=[]))
        monkeypatch.setattr(main, "load_to_bigquery", MagicMock(return_value=0))
        export_mock = MagicMock()
        monkeypatch.setattr(main, "export_catalog_to_gcs", export_mock)

        class Req:
            method = "POST"
            args = {}

            def get_json(self, silent=False):
                return {}

        body, status, _ = main.sync_divebar_index(Req())
        assert status == 200
        export_mock.assert_not_called()
        assert "skipped" in json.loads(body)["catalog_export"]

    def test_export_result_included_on_success(self, monkeypatch):
        import main
        monkeypatch.setattr(main, "get_drive_service", MagicMock())
        monkeypatch.setattr(main, "list_divebar_recursive",
                            MagicMock(return_value=[{"name": "a.mp4"}]))
        monkeypatch.setattr(main, "should_index_file", MagicMock(return_value=True))
        monkeypatch.setattr(main, "build_rows", MagicMock(return_value=[{"x": 1}]))
        monkeypatch.setattr(main, "load_to_bigquery", MagicMock(return_value=1))
        monkeypatch.setattr(main, "export_catalog_to_gcs",
                            MagicMock(return_value={"rows": 1, "gcs_uri": "gs://b/o"}))

        class Req:
            method = "POST"
            args = {}

            def get_json(self, silent=False):
                return {}

        body, status, _ = main.sync_divebar_index(Req())
        assert status == 200
        assert json.loads(body)["catalog_export"] == {"rows": 1, "gcs_uri": "gs://b/o"}
