"""Unit tests for the Nomad master fast-sync mirror (Phase 1)."""
from unittest.mock import MagicMock

import pytest
from google.cloud.exceptions import NotFound

from backend.services.nomad_master_mirror import (
    NomadMasterMirror,
    is_nomad_public_brand,
)


@pytest.mark.parametrize(
    "brand_code, expected",
    [
        ("NOMAD-1500", True),
        ("NOMAD-0001", True),
        ("NOMAD-1500", True),
        ("NOMADNP-1234", False),   # private tracks must never enter the public mirror
        ("nomadnp-1", False),
        ("TRACK-0000", False),
        ("VOCALSTAR-12", False),
        ("", False),
        (None, False),
    ],
)
def test_is_nomad_public_brand(brand_code, expected):
    assert is_nomad_public_brand(brand_code) is expected


def _mirror_with_mock():
    """NomadMasterMirror wired to a mock GCS client; returns (mirror, blob)."""
    blob = MagicMock()
    bucket = MagicMock()
    bucket.blob.return_value = blob
    client = MagicMock()
    client.bucket.return_value = bucket
    mirror = NomadMasterMirror(client=client)
    return mirror, bucket, blob


def test_push_720p_uses_exact_divebar_object_name_and_uploads():
    mirror, bucket, blob = _mirror_with_mock()

    ok = mirror.push_720p(
        "/tmp/out/NOMAD-1500 - Rush - The Spirit of Radio.mp4",
        "NOMAD-1500 - Rush - The Spirit of Radio.mp4",
    )

    assert ok is True
    # Object name MUST match the nightly VM's layout exactly (idempotency).
    bucket.blob.assert_called_once_with(
        "files/Nomad Karaoke/MP4-720p/NOMAD-1500 - Rush - The Spirit of Radio.mp4"
    )
    blob.upload_from_filename.assert_called_once_with(
        "/tmp/out/NOMAD-1500 - Rush - The Spirit of Radio.mp4"
    )


def test_push_720p_is_non_fatal_on_error():
    mirror, _bucket, blob = _mirror_with_mock()
    blob.upload_from_filename.side_effect = RuntimeError("boom")

    # Must not raise; returns False so the pipeline continues.
    assert mirror.push_720p("/tmp/x.mp4", "NOMAD-1 - A - B.mp4") is False


def test_delete_720p_removes_blob():
    mirror, bucket, blob = _mirror_with_mock()

    assert mirror.delete_720p("NOMAD-1500 - Rush - The Spirit of Radio.mp4") is True
    bucket.blob.assert_called_once_with(
        "files/Nomad Karaoke/MP4-720p/NOMAD-1500 - Rush - The Spirit of Radio.mp4"
    )
    blob.delete.assert_called_once()


def test_delete_720p_missing_is_noop():
    mirror, _bucket, blob = _mirror_with_mock()
    blob.delete.side_effect = NotFound("gone")

    assert mirror.delete_720p("NOMAD-9 - X - Y.mp4") is False


def test_delete_720p_is_non_fatal_on_error():
    mirror, _bucket, blob = _mirror_with_mock()
    blob.delete.side_effect = RuntimeError("boom")

    assert mirror.delete_720p("NOMAD-9 - X - Y.mp4") is False


# --- delete_masters_by_brand (prefix delete; covers renames) ---

def _mirror_with_blobs(names):
    blobs = []
    for n in names:
        b = MagicMock()
        b.name = n
        blobs.append(b)
    bucket = MagicMock()
    bucket.list_blobs.return_value = blobs
    client = MagicMock()
    client.bucket.return_value = bucket
    return NomadMasterMirror(client=client), bucket, blobs


def test_delete_masters_by_brand_deletes_all_matching_by_prefix():
    # Two objects for the same release (e.g. an old + renamed title) both go.
    mirror, bucket, blobs = _mirror_with_blobs([
        "files/Nomad Karaoke/MP4-720p/NOMAD-1500 - Rush - Spirit of Radio.mp4",
        "files/Nomad Karaoke/MP4-720p/NOMAD-1500 - Rush - The Spirit of Radio.mp4",
    ])
    n = mirror.delete_masters_by_brand("NOMAD-1500")
    assert n == 2
    bucket.list_blobs.assert_called_once_with(
        prefix="files/Nomad Karaoke/MP4-720p/NOMAD-1500 - "
    )
    for b in blobs:
        b.delete.assert_called_once()


@pytest.mark.parametrize("brand", ["NOMADNP-1500", "VOCALSTAR-1", "", None, "NOMAD"])
def test_delete_masters_by_brand_refuses_non_nomad_or_bare(brand):
    # Must never list/delete for a non-Nomad or bare brand — a broad prefix could
    # wipe unrelated objects.
    mirror, bucket, _blobs = _mirror_with_blobs([])
    assert mirror.delete_masters_by_brand(brand) == 0
    bucket.list_blobs.assert_not_called()


def test_delete_masters_by_brand_is_non_fatal_on_list_error():
    mirror, bucket, _blobs = _mirror_with_blobs([])
    bucket.list_blobs.side_effect = RuntimeError("gcs down")
    assert mirror.delete_masters_by_brand("NOMAD-1500") == 0


# --- vocals guide push / delete (parallel to the master methods) ---

def test_push_vocals_guide_uses_vocals_prefix_and_uploads():
    mirror, bucket, blob = _mirror_with_mock()

    ok = mirror.push_vocals_guide(
        "/tmp/out/NOMAD-1500 - Rush - The Spirit of Radio.flac",
        "NOMAD-1500 - Rush - The Spirit of Radio.flac",
    )

    assert ok is True
    bucket.blob.assert_called_once_with(
        "files/Nomad Karaoke/vocals-padded/NOMAD-1500 - Rush - The Spirit of Radio.flac"
    )
    blob.upload_from_filename.assert_called_once_with(
        "/tmp/out/NOMAD-1500 - Rush - The Spirit of Radio.flac"
    )


def test_push_vocals_guide_is_non_fatal_on_error():
    mirror, _bucket, blob = _mirror_with_mock()
    blob.upload_from_filename.side_effect = RuntimeError("boom")
    assert mirror.push_vocals_guide("/tmp/x.flac", "NOMAD-1 - A - B.flac") is False


def test_delete_vocals_guides_by_brand_deletes_all_matching_by_prefix():
    mirror, bucket, blobs = _mirror_with_blobs([
        "files/Nomad Karaoke/vocals-padded/NOMAD-1500 - Rush - Spirit of Radio.flac",
        "files/Nomad Karaoke/vocals-padded/NOMAD-1500 - Rush - The Spirit of Radio.flac",
    ])
    n = mirror.delete_vocals_guides_by_brand("NOMAD-1500")
    assert n == 2
    bucket.list_blobs.assert_called_once_with(
        prefix="files/Nomad Karaoke/vocals-padded/NOMAD-1500 - "
    )
    for b in blobs:
        b.delete.assert_called_once()


@pytest.mark.parametrize("brand", ["NOMADNP-1500", "VOCALSTAR-1", "", None, "NOMAD"])
def test_delete_vocals_guides_by_brand_refuses_non_nomad_or_bare(brand):
    mirror, bucket, _blobs = _mirror_with_blobs([])
    assert mirror.delete_vocals_guides_by_brand(brand) == 0
    bucket.list_blobs.assert_not_called()


def test_delete_vocals_guides_by_brand_is_non_fatal_on_list_error():
    mirror, bucket, _blobs = _mirror_with_blobs([])
    bucket.list_blobs.side_effect = RuntimeError("gcs down")
    assert mirror.delete_vocals_guides_by_brand("NOMAD-1500") == 0


def test_cleanup_nomad_masters_delegates_for_nomad(monkeypatch):
    from backend.services import nomad_master_mirror as mod

    inst = MagicMock()
    inst.delete_masters_by_brand.return_value = 3
    monkeypatch.setattr(mod, "NomadMasterMirror", lambda: inst)

    assert mod.cleanup_nomad_masters("NOMAD-1500") == 3
    inst.delete_masters_by_brand.assert_called_once_with("NOMAD-1500")
    # Brand recycle must also clear the release's original-vocals guide(s).
    inst.delete_vocals_guides_by_brand.assert_called_once_with("NOMAD-1500")


@pytest.mark.parametrize("brand", ["NOMADNP-1", "OTHER-1", "", None])
def test_cleanup_nomad_masters_noop_for_non_nomad(brand, monkeypatch):
    from backend.services import nomad_master_mirror as mod

    # Must not even construct the mirror (no GCS client) for non-Nomad brands.
    monkeypatch.setattr(mod, "NomadMasterMirror", lambda: (_ for _ in ()).throw(AssertionError("should not construct")))
    assert mod.cleanup_nomad_masters(brand) == 0
