"""Tests for divebar_file_sync.py — focused on how failed downloads are
classified. Dead Drive links (404/410) must be marked with a sentinel gcs_path
so they stop counting as "pending" and aren't retried every nightly run, while
transient errors must stay NULL (retryable).

Google client libs aren't installed in the test env, so they're stubbed before
import. HttpError must be a *real* exception class (the module does
``except HttpError``), so we provide a concrete stub.
"""
import os
import sys
import types
from unittest.mock import MagicMock

import pytest


class _HttpError(Exception):
    def __init__(self, status):
        self.status_code = status
        self.resp = types.SimpleNamespace(status=status)
        super().__init__(f"HTTP {status}")


def _install_stubs():
    sys.modules.setdefault("google", types.ModuleType("google"))
    gauth = types.ModuleType("google.auth"); gauth.default = MagicMock()
    sys.modules["google.auth"] = gauth
    gcloud = types.ModuleType("google.cloud")
    gcloud.bigquery = MagicMock(); gcloud.storage = MagicMock()
    sys.modules["google.cloud"] = gcloud
    sys.modules.setdefault("googleapiclient", types.ModuleType("googleapiclient"))
    disc = types.ModuleType("googleapiclient.discovery"); disc.build = MagicMock()
    sys.modules["googleapiclient.discovery"] = disc
    errs = types.ModuleType("googleapiclient.errors"); errs.HttpError = _HttpError
    sys.modules["googleapiclient.errors"] = errs
    http = types.ModuleType("googleapiclient.http"); http.MediaIoBaseDownload = MagicMock()
    sys.modules["googleapiclient.http"] = http


_install_stubs()
sys.path.insert(0, os.path.dirname(__file__))
import divebar_file_sync as mod  # noqa: E402


class _Row:
    def __init__(self, file_id="fid", drive_path="Brand/Song.mp4", file_size=1000):
        self.file_id = file_id
        self.drive_path = drive_path
        self.file_size = file_size
        self.drive_md5 = "abc"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Retries back off with time.sleep — no-op it so tests stay fast."""
    monkeypatch.setattr(mod.time, "sleep", lambda *a, **k: None)


# ---- download_and_upload: classify the failure ----

def test_download_404_is_gone(monkeypatch):
    inst = MagicMock(); inst.next_chunk.side_effect = _HttpError(404)
    monkeypatch.setattr(mod, "MediaIoBaseDownload", MagicMock(return_value=inst))
    ok, size, gone = mod.download_and_upload(MagicMock(), MagicMock(), "fid", "files/x")
    assert (ok, size, gone) == (False, 0, True)


def test_download_410_is_gone(monkeypatch):
    inst = MagicMock(); inst.next_chunk.side_effect = _HttpError(410)
    monkeypatch.setattr(mod, "MediaIoBaseDownload", MagicMock(return_value=inst))
    _, _, gone = mod.download_and_upload(MagicMock(), MagicMock(), "fid", "files/x")
    assert gone is True


def test_download_500_is_not_gone(monkeypatch):
    inst = MagicMock(); inst.next_chunk.side_effect = _HttpError(500)
    monkeypatch.setattr(mod, "MediaIoBaseDownload", MagicMock(return_value=inst))
    ok, size, gone = mod.download_and_upload(MagicMock(), MagicMock(), "fid", "files/x")
    assert (ok, gone) == (False, False)  # transient — retry


def test_download_generic_error_is_not_gone(monkeypatch):
    inst = MagicMock(); inst.next_chunk.side_effect = RuntimeError("network blip")
    monkeypatch.setattr(mod, "MediaIoBaseDownload", MagicMock(return_value=inst))
    ok, size, gone = mod.download_and_upload(MagicMock(), MagicMock(), "fid", "files/x")
    assert (ok, gone) == (False, False)


# ---- download_and_upload: retry behaviour ----

def test_transient_failure_is_retried_then_succeeds(monkeypatch):
    # First attempt raises a 503, second attempt's downloader completes.
    bad = MagicMock(); bad.next_chunk.side_effect = _HttpError(503)
    good = MagicMock(); good.next_chunk.return_value = (None, True)  # done immediately
    ctor = MagicMock(side_effect=[bad, good])
    monkeypatch.setattr(mod, "MediaIoBaseDownload", ctor)
    bucket = MagicMock()
    ok, _, gone = mod.download_and_upload(MagicMock(), bucket, "fid", "files/x")
    assert (ok, gone) == (True, False)
    assert ctor.call_count == 2  # retried once
    bucket.blob.return_value.upload_from_file.assert_called_once()


def test_transient_failure_retries_then_gives_up(monkeypatch):
    inst = MagicMock(); inst.next_chunk.side_effect = _HttpError(503)
    ctor = MagicMock(return_value=inst)
    monkeypatch.setattr(mod, "MediaIoBaseDownload", ctor)
    ok, size, gone = mod.download_and_upload(MagicMock(), MagicMock(), "fid", "files/x", max_attempts=3)
    assert (ok, size, gone) == (False, 0, False)  # left NULL to retry next run
    assert ctor.call_count == 3


def test_404_is_not_retried(monkeypatch):
    inst = MagicMock(); inst.next_chunk.side_effect = _HttpError(404)
    ctor = MagicMock(return_value=inst)
    monkeypatch.setattr(mod, "MediaIoBaseDownload", ctor)
    _, _, gone = mod.download_and_upload(MagicMock(), MagicMock(), "fid", "files/x", max_attempts=3)
    assert gone is True
    assert ctor.call_count == 1  # permanent — returned immediately, no retry


# ---- sync_file: record the outcome in BigQuery ----

def _bucket_blob_missing():
    bucket = MagicMock()
    bucket.blob.return_value.exists.return_value = False
    return bucket


def test_sync_file_marks_dead_link_unsyncable(monkeypatch):
    monkeypatch.setattr(mod, "get_drive_service", MagicMock())
    monkeypatch.setattr(mod, "download_and_upload", MagicMock(return_value=(False, 0, True)))
    upd = MagicMock(); monkeypatch.setattr(mod, "update_gcs_path", upd)
    bq = MagicMock()
    ok, size, unavailable = mod.sync_file(_bucket_blob_missing(), bq, _Row())
    assert (ok, size, unavailable) == (False, 0, True)
    upd.assert_called_once_with(bq, "fid", "missing:drive_404")


def test_sync_file_transient_stays_null(monkeypatch):
    monkeypatch.setattr(mod, "get_drive_service", MagicMock())
    monkeypatch.setattr(mod, "download_and_upload", MagicMock(return_value=(False, 0, False)))
    upd = MagicMock(); monkeypatch.setattr(mod, "update_gcs_path", upd)
    ok, size, unavailable = mod.sync_file(_bucket_blob_missing(), MagicMock(), _Row())
    assert (ok, size, unavailable) == (False, 0, False)
    upd.assert_not_called()  # left NULL to retry next run


def test_sync_file_success_marks_gcs_path(monkeypatch):
    monkeypatch.setattr(mod, "get_drive_service", MagicMock())
    monkeypatch.setattr(mod, "download_and_upload", MagicMock(return_value=(True, 4242, False)))
    upd = MagicMock(); monkeypatch.setattr(mod, "update_gcs_path", upd)
    bq = MagicMock()
    ok, size, unavailable = mod.sync_file(_bucket_blob_missing(), bq, _Row(file_id="ok1"))
    assert (ok, size, unavailable) == (True, 4242, False)
    args = upd.call_args[0]
    assert args[0] is bq and args[1] == "ok1" and args[2].startswith("gs://")


def test_sync_file_too_large_is_unavailable(monkeypatch):
    upd = MagicMock(); monkeypatch.setattr(mod, "update_gcs_path", upd)
    bq = MagicMock()
    huge = _Row(file_id="big", file_size=mod.MAX_FILE_SIZE + 1)
    ok, size, unavailable = mod.sync_file(MagicMock(), bq, huge)
    assert (ok, size, unavailable) == (True, 0, True)
    upd.assert_called_once_with(bq, "big", "skipped:too_large")
