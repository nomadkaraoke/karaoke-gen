"""
Tests for the karaokenerds service.

The service reads our OWN daily-refreshed copy of the KaraokeNerds community
catalog (a gzipped-JSON export in GCS, populated by the authorized `kn-data-sync`
job) into an in-process index — it never scrapes or live-queries karaokenerds.com.
"""

import gzip
import json

import pytest

from backend.services import karaokenerds_service as svc
from backend.services.karaokenerds_service import (
    _build_index,
    check_community_versions,
    check_community_versions_batch,
)


# Rows mirror the community export shape: Artist, Title, Brand, Watch (YouTube URL).
ROWS = [
    {"Artist": "Fleetwood Mac", "Title": "Dreams", "Brand": "Nomad Karaoke",
     "Watch": "https://www.youtube.com/watch?v=aaa"},
    {"Artist": "Fleetwood Mac", "Title": "Dreams", "Brand": "WTF Karaoke",
     "Watch": "https://www.youtube.com/watch?v=bbb"},
    # Exact duplicate (brand + url) — must collapse.
    {"Artist": "Fleetwood Mac", "Title": "Dreams", "Brand": "Nomad Karaoke",
     "Watch": "https://www.youtube.com/watch?v=aaa"},
    {"Artist": "ABBA", "Title": "Dancing Queen", "Brand": "SNDL Karaoke",
     "Watch": "https://youtu.be/dq"},
    # Accents + ampersand exercise the normalized match key.
    {"Artist": "Beyoncé", "Title": "Crazy in Love", "Brand": "Nomad Karaoke",
     "Watch": "https://youtu.be/cil"},
    {"Artist": "Hall & Oates", "Title": "Rich Girl", "Brand": "Nomad Karaoke",
     "Watch": "https://youtu.be/rg"},
    # Missing artist/title rows are skipped.
    {"Artist": "", "Title": "No Artist", "Brand": "X", "Watch": "https://youtu.be/z"},
]


@pytest.fixture(autouse=True)
def _seed_index(monkeypatch):
    """Isolate the module cache and back `_get_index` with the sample catalog."""
    svc._reset_index_for_tests()
    monkeypatch.setattr(svc, "_load_index", lambda: _build_index(ROWS))
    yield
    svc._reset_index_for_tests()


# --- Index building ---


def test_build_index_groups_and_dedupes():
    index = _build_index(ROWS)
    dreams = index[svc._match_key("Fleetwood Mac", "Dreams")]
    # Two distinct brands; the exact-duplicate row is collapsed.
    assert len(dreams["community_tracks"]) == 2
    brands = {t["brand_name"] for t in dreams["community_tracks"]}
    assert brands == {"Nomad Karaoke", "WTF Karaoke"}
    assert all(t["is_community"] is True for t in dreams["community_tracks"])


def test_build_index_skips_rows_without_artist_or_title():
    index = _build_index(ROWS)
    assert svc._match_key("", "No Artist") not in index


# --- check_community_versions ---


@pytest.mark.asyncio
async def test_match_returns_community_with_best_url():
    result = await check_community_versions("Fleetwood Mac", "Dreams")
    assert result["has_community"] is True
    assert result["best_youtube_url"] == "https://www.youtube.com/watch?v=aaa"
    assert len(result["songs"]) == 1
    assert result["songs"][0]["title"] == "Dreams"
    assert result["songs"][0]["artist"] == "Fleetwood Mac"


@pytest.mark.asyncio
async def test_match_is_case_and_whitespace_insensitive():
    result = await check_community_versions("  fleetwood   MAC ", "dreams")
    assert result["has_community"] is True


@pytest.mark.asyncio
async def test_match_folds_accents():
    result = await check_community_versions("Beyonce", "Crazy in Love")
    assert result["has_community"] is True


@pytest.mark.asyncio
async def test_match_treats_ampersand_as_and():
    # "&" folds to "and", so "Hall and Oates" matches "Hall & Oates".
    result = await check_community_versions("Hall and Oates", "Rich Girl")
    assert result["has_community"] is True


@pytest.mark.asyncio
async def test_no_match_returns_false():
    result = await check_community_versions("Nonexistent Artist", "No Such Song")
    assert result == {"has_community": False, "songs": [], "best_youtube_url": None}


@pytest.mark.asyncio
async def test_blank_input_returns_false_without_lookup():
    assert (await check_community_versions("", "Dreams"))["has_community"] is False
    assert (await check_community_versions("ABBA", ""))["has_community"] is False


# --- Batch (Bulk Mode) ---


@pytest.mark.asyncio
async def test_batch_returns_versions_deduped_by_brand():
    results = await check_community_versions_batch([
        {"artist": "Fleetwood Mac", "title": "Dreams"},
        {"artist": "Nope", "title": "Missing"},
    ])
    assert len(results) == 2

    dreams = results[0]
    assert dreams["available"] is True
    assert dreams["brands"] == ["Nomad Karaoke", "WTF Karaoke"]
    assert dreams["brand_count"] == 2
    assert dreams["versions"] == [
        {"brand": "Nomad Karaoke", "url": "https://www.youtube.com/watch?v=aaa"},
        {"brand": "WTF Karaoke", "url": "https://www.youtube.com/watch?v=bbb"},
    ]

    assert results[1]["available"] is False
    assert results[1]["versions"] == []


@pytest.mark.asyncio
async def test_batch_empty_song_has_empty_versions():
    results = await check_community_versions_batch([{"artist": "", "title": ""}])
    assert results[0]["available"] is False
    assert results[0]["versions"] == []


@pytest.mark.asyncio
async def test_batch_preserves_input_order():
    order = [
        {"artist": "ABBA", "title": "Dancing Queen"},
        {"artist": "Fleetwood Mac", "title": "Dreams"},
        {"artist": "Beyonce", "title": "Crazy in Love"},
    ]
    results = await check_community_versions_batch(order)
    assert [r["title"] for r in results] == ["Dancing Queen", "Dreams", "Crazy in Love"]
    assert all(r["available"] for r in results)


# --- Loading + resilience ---


@pytest.mark.asyncio
async def test_load_index_parses_gzipped_items(monkeypatch):
    """`_load_index` decompresses the GCS gzip and reads the `Items` array."""
    payload = gzip.compress(json.dumps({"Items": ROWS}).encode())

    class _Blob:
        def download_as_bytes(self):
            return payload

    class _Bucket:
        def blob(self, _name):
            return _Blob()

    class _Client:
        def __init__(self, *a, **k):
            pass

        def bucket(self, _name):
            return _Bucket()

    monkeypatch.setattr(svc.storage, "Client", _Client)
    # Restore the real loader (the autouse fixture stubbed it) so it runs against
    # the mocked GCS client above.
    monkeypatch.setattr(svc, "_load_index", _real_load_index)
    svc._reset_index_for_tests()

    result = await check_community_versions("Fleetwood Mac", "Dreams")
    assert result["has_community"] is True


@pytest.mark.asyncio
async def test_load_failure_is_fail_open(monkeypatch):
    """If the export can't be loaded, every song reads as no-community (fail-open)."""
    def _boom():
        raise RuntimeError("GCS unavailable")

    svc._reset_index_for_tests()
    monkeypatch.setattr(svc, "_load_index", _boom)
    result = await check_community_versions("Fleetwood Mac", "Dreams")
    assert result["has_community"] is False


# `_real_load_index` is the unpatched module function, captured at import time so
# the gzip-parsing test can call it after the autouse fixture stubs `_load_index`.
_real_load_index = svc._load_index
