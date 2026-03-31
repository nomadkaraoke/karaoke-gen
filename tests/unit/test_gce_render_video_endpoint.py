"""Tests for GCE encoding worker render-video endpoint and caching."""
import hashlib
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestDownloadWithCache:
    """Tests for the download_with_cache helper."""

    def test_cache_miss_downloads_and_caches(self, tmp_path):
        """On cache miss, downloads file and stores in cache."""
        from backend.services.gce_encoding.main import download_with_cache

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        dest = tmp_path / "output.ttf"
        gcs_uri = "gs://bucket/themes/nomad/font.ttf"

        # Mock download to write a file
        def fake_download(uri, path):
            Path(path).write_bytes(b"font-data")

        with patch("backend.services.gce_encoding.main.download_single_file_from_gcs", side_effect=fake_download):
            download_with_cache(gcs_uri, dest, cache_dir)

        assert dest.read_bytes() == b"font-data"
        # Verify cached copy exists
        cache_key = hashlib.sha256(gcs_uri.encode()).hexdigest()
        cached = cache_dir / cache_key
        assert cached.exists()
        assert cached.read_bytes() == b"font-data"

    def test_cache_hit_skips_download(self, tmp_path):
        """On cache hit, copies from cache without downloading."""
        from backend.services.gce_encoding.main import download_with_cache

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        dest = tmp_path / "output.ttf"
        gcs_uri = "gs://bucket/themes/nomad/font.ttf"

        # Pre-populate cache
        cache_key = hashlib.sha256(gcs_uri.encode()).hexdigest()
        cached = cache_dir / cache_key
        cached.write_bytes(b"cached-font-data")

        with patch("backend.services.gce_encoding.main.download_single_file_from_gcs") as mock_dl:
            download_with_cache(gcs_uri, dest, cache_dir)
            mock_dl.assert_not_called()

        assert dest.read_bytes() == b"cached-font-data"

    def test_cache_dir_none_downloads_directly(self, tmp_path):
        """When cache_dir is None, downloads without caching."""
        from backend.services.gce_encoding.main import download_with_cache

        dest = tmp_path / "output.ttf"
        gcs_uri = "gs://bucket/themes/nomad/font.ttf"

        def fake_download(uri, path):
            Path(path).write_bytes(b"font-data")

        with patch("backend.services.gce_encoding.main.download_single_file_from_gcs", side_effect=fake_download):
            download_with_cache(gcs_uri, dest, cache_dir=None)

        assert dest.read_bytes() == b"font-data"
