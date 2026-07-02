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
