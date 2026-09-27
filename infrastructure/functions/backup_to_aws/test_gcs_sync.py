"""Tests for gcs_sync change detection."""

import datetime
from unittest.mock import MagicMock, patch

from gcs_sync import sync_gcs_to_staging

UTC = datetime.timezone.utc
LAST_SYNC = datetime.datetime(2026, 9, 20, tzinfo=UTC)


def _blob(name, created, updated):
    b = MagicMock()
    b.name = name
    b.time_created = created
    b.updated = updated
    return b


def _run(blobs):
    client = MagicMock()
    src, dst = MagicMock(), MagicMock()
    client.bucket.side_effect = lambda n: src if n == "src" else dst
    marker = MagicMock()
    marker.exists.return_value = True
    marker.download_as_text.return_value = LAST_SYNC.isoformat()
    dst.blob.return_value = marker
    src.list_blobs.return_value = blobs
    with patch("gcs_sync.storage.Client", return_value=client):
        summary = sync_gcs_to_staging("src", "dst", "gcs/job-files/", sync_prefixes=["jobs/"])
    return summary, src, marker


def test_metadata_only_change_is_not_recopied():
    """An Autoclass storage-class transition bumps `updated` but not
    `time_created`; it must not be treated as new content."""
    cooled = _blob("jobs/a/old.mp4", LAST_SYNC - datetime.timedelta(days=40), LAST_SYNC + datetime.timedelta(days=1))
    summary, src, marker = _run([cooled])
    src.copy_blob.assert_not_called()
    assert summary == "Synced 0 objects"


def test_new_generation_is_copied_and_marker_advances():
    created = LAST_SYNC + datetime.timedelta(hours=3)
    new = _blob("jobs/b/final.mp4", created, created)
    summary, src, marker = _run([new])
    src.copy_blob.assert_called_once()
    marker.upload_from_string.assert_called_with(created.isoformat())
    assert summary == "Synced 1 objects"
