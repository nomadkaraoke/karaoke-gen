"""
User-uploaded inputs must outlive the uploads/ lifecycle delete.

2026-10-08: randy-vild jobs that skipped the audio worker's copy (bring-your-own
instrumental) or sat in review for >7 days lost their mix and instrumental, so
re-renders failed with a 404 on uploads/... . Every pipeline worker now copies
uploads/{job_id}/** to jobs/{job_id}/input/ on entry and repoints the job.
"""
import ast
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.services.input_persistence import (
    ensure_job_inputs_persisted,
    persist_job_inputs,
    persisted_path,
)


class FakeBlob:
    def __init__(self, name, data):
        self.name = name
        self.data = data
        self.size = len(data)
        self.md5_hash = hashlib.md5(data).hexdigest()


class FakeBucket:
    def __init__(self, objects):
        self.objects = {n: FakeBlob(n, d) for n, d in objects.items()}
        self.copies = []

    def list_blobs(self, prefix=""):
        return [b for n, b in sorted(self.objects.items()) if n.startswith(prefix)]

    def get_blob(self, name):
        return self.objects.get(name)

    def copy_blob(self, blob, bucket, dest):
        self.copies.append((blob.name, dest))
        self.objects[dest] = FakeBlob(dest, blob.data)


def _job(**fields):
    base = dict(
        job_id="j1",
        input_media_gcs_path="uploads/j1/audio/01-Song Mix.wav",
        existing_instrumental_gcs_path="uploads/j1/conformed/existing_instrumental.flac",
        style_params_gcs_path=None,
        file_urls={"input": {"audio": "uploads/j1/audio/01-Song Mix.wav"}, "stems": {"x": "jobs/j1/stems/x.flac"}},
        state_data={"instrumental_conformed": {"original_gcs_path": "uploads/j1/audio/existing_instrumental.wav", "correlation": 0.8}},
        style_assets={},
    )
    base.update(fields)
    return SimpleNamespace(**base)


UPLOADS = {
    "uploads/j1/audio/01-Song Mix.wav": b"mix",
    "uploads/j1/audio/existing_instrumental.wav": b"inst",
    "uploads/j1/conformed/existing_instrumental.flac": b"conformed",
    "uploads/j1/lyrics/user_lyrics.txt": b"la la",
    "uploads/j10/audio/other.wav": b"another job",  # prefix-sibling must not be touched
}


def test_persisted_path_keeps_relative_layout_and_only_maps_this_job():
    assert persisted_path("j1", "uploads/j1/audio/a b.wav") == "jobs/j1/input/audio/a b.wav"
    assert persisted_path("j1", "uploads/j10/audio/x.wav") is None
    assert persisted_path("j1", "jobs/j1/input/x.wav") is None
    assert persisted_path("j1", "uploads/j1/") is None


def test_copies_every_upload_and_repoints_every_reference():
    bucket = FakeBucket(UPLOADS)
    jm = MagicMock()
    job = _job()
    copied, updates = persist_job_inputs(job, SimpleNamespace(bucket=bucket), jm)

    assert copied == 4
    assert sorted(dest for _src, dest in bucket.copies) == [
        "jobs/j1/input/audio/01-Song Mix.wav",
        "jobs/j1/input/audio/existing_instrumental.wav",
        "jobs/j1/input/conformed/existing_instrumental.flac",
        "jobs/j1/input/lyrics/user_lyrics.txt",
    ]
    assert updates == {
        "input_media_gcs_path": "jobs/j1/input/audio/01-Song Mix.wav",
        "existing_instrumental_gcs_path": "jobs/j1/input/conformed/existing_instrumental.flac",
        "file_urls.input.audio": "jobs/j1/input/audio/01-Song Mix.wav",
        "state_data.instrumental_conformed.original_gcs_path": "jobs/j1/input/audio/existing_instrumental.wav",
    }
    jm.update_job.assert_called_once_with("j1", updates)
    # in-memory job follows, so the calling worker downloads from the kept copy
    assert job.input_media_gcs_path == "jobs/j1/input/audio/01-Song Mix.wav"
    assert job.existing_instrumental_gcs_path.startswith("jobs/j1/input/")


def test_idempotent_second_run_copies_nothing():
    bucket = FakeBucket(UPLOADS)
    jm = MagicMock()
    persist_job_inputs(_job(), SimpleNamespace(bucket=bucket), jm)
    bucket.copies.clear()
    jm.reset_mock()
    # Job already repointed (as Firestore would now hold)
    job = _job(
        input_media_gcs_path="jobs/j1/input/audio/01-Song Mix.wav",
        existing_instrumental_gcs_path="jobs/j1/input/conformed/existing_instrumental.flac",
        file_urls={"input": {"audio": "jobs/j1/input/audio/01-Song Mix.wav"}},
        state_data={},
    )
    copied, updates = persist_job_inputs(job, SimpleNamespace(bucket=bucket), jm)
    assert (copied, updates) == (0, {})
    assert bucket.copies == []
    jm.update_job.assert_not_called()


def test_recopies_when_the_kept_copy_differs():
    """A re-upload under the same name (e.g. a new conformed instrumental) replaces the kept copy."""
    bucket = FakeBucket({**UPLOADS, "jobs/j1/input/conformed/existing_instrumental.flac": b"stale"})
    persist_job_inputs(_job(), SimpleNamespace(bucket=bucket), MagicMock())
    assert bucket.objects["jobs/j1/input/conformed/existing_instrumental.flac"].data == b"conformed"


def test_paths_to_already_expired_uploads_are_left_alone():
    bucket = FakeBucket({})  # nothing left in uploads/
    jm = MagicMock()
    copied, updates = persist_job_inputs(_job(), SimpleNamespace(bucket=bucket), jm)
    assert (copied, updates) == (0, {})
    jm.update_job.assert_not_called()


def test_style_assets_and_style_params_are_repointed():
    bucket = FakeBucket({"uploads/j1/style/style_params.json": b"{}", "uploads/j1/style/bg.png": b"png"})
    job = _job(
        input_media_gcs_path=None, existing_instrumental_gcs_path=None, file_urls={}, state_data={},
        style_params_gcs_path="uploads/j1/style/style_params.json",
        style_assets={"karaoke_background": "uploads/j1/style/bg.png", "font": "themes/nomad/assets/A.ttf"},
    )
    _copied, updates = persist_job_inputs(job, SimpleNamespace(bucket=bucket), MagicMock())
    assert updates == {
        "style_params_gcs_path": "jobs/j1/input/style/style_params.json",
        "style_assets.karaoke_background": "jobs/j1/input/style/bg.png",
    }


def test_worker_wrapper_never_raises_but_logs_an_error(caplog):
    storage = SimpleNamespace(bucket=MagicMock(list_blobs=MagicMock(side_effect=RuntimeError("gcs down"))))
    with caplog.at_level("ERROR"):
        ensure_job_inputs_persisted(_job(), storage, MagicMock(), "lyrics")
    assert any("failed to persist uploaded inputs" in r.message and "gcs down" in r.message for r in caplog.records)


@pytest.mark.parametrize(
    "module",
    ["audio_worker", "lyrics_worker", "screens_worker", "render_video_worker", "video_worker"],
)
def test_every_pipeline_worker_persists_inputs_off_the_event_loop(module):
    """Each worker entry calls the persistence step via asyncio.to_thread (blocking GCS/Firestore I/O)."""
    source = Path(__file__).resolve().parents[1].joinpath("workers", f"{module}.py").read_text()
    calls = [
        node for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and getattr(node.func, "attr", None) == "to_thread"
        and node.args and getattr(node.args[0], "id", None) == "ensure_job_inputs_persisted"
    ]
    assert calls, f"{module} must call ensure_job_inputs_persisted on entry"


# --- admin backfill -------------------------------------------------------------------


def _admin_client():
    from backend.api.dependencies import AuthResult, UserType, require_admin
    from backend.api.routes.admin import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[require_admin] = lambda: AuthResult(
        is_valid=True, user_type=UserType.ADMIN, remaining_uses=999, message="ok",
        user_email="admin@example.com", is_admin=True,
    )
    return TestClient(app)


def test_backfill_dry_run_reports_without_copying():
    bucket = FakeBucket({**UPLOADS, "uploads/gone/audio/x.wav": b"orphan"})
    jm = MagicMock()
    jm.get_job.side_effect = lambda jid: _job() if jid == "j1" else (_job(job_id="j10") if jid == "j10" else None)
    with patch("backend.api.routes.admin.StorageService", return_value=SimpleNamespace(bucket=bucket)), \
         patch("backend.api.routes.admin.JobManager", return_value=jm):
        resp = _admin_client().post("/api/admin/persist-uploads")
    assert resp.status_code == 200
    body = resp.json()
    assert body["dry_run"] is True
    assert body["jobs"]["gone"] == {"orphan": 1}
    assert body["jobs"]["j1"]["would_copy"]["uploads/j1/lyrics/user_lyrics.txt"] == "jobs/j1/input/lyrics/user_lyrics.txt"
    assert bucket.copies == []
    jm.update_job.assert_not_called()


def test_backfill_apply_persists_each_job():
    bucket = FakeBucket(UPLOADS)
    jm = MagicMock()
    jm.get_job.side_effect = lambda jid: _job(job_id=jid, input_media_gcs_path=f"uploads/{jid}/audio/other.wav",
                                               existing_instrumental_gcs_path=None, file_urls={}, state_data={}) \
        if jid == "j10" else _job()
    with patch("backend.api.routes.admin.StorageService", return_value=SimpleNamespace(bucket=bucket)), \
         patch("backend.api.routes.admin.JobManager", return_value=jm):
        body = _admin_client().post("/api/admin/persist-uploads?dry_run=false").json()
    assert body["jobs"]["j1"]["copied"] == 4
    assert body["jobs"]["j10"] == {"copied": 1, "updated": {"input_media_gcs_path": "jobs/j10/input/audio/other.wav"}}


# --- GCE encoder picks the configured instrumental, not a persisted raw copy -----------


def test_encoder_prefers_the_downloaded_existing_instrumental(tmp_path):
    from backend.services.gce_encoding.main import resolve_instrumental

    (tmp_path / "input" / "audio").mkdir(parents=True)
    (tmp_path / "input" / "conformed").mkdir(parents=True)
    (tmp_path / "input" / "audio" / "existing_instrumental.wav").write_bytes(b"raw, unaligned")
    (tmp_path / "input" / "conformed" / "existing_instrumental.flac").write_bytes(b"aligned")
    (tmp_path / "existing_instrumental.flac").write_bytes(b"configured")
    picked = resolve_instrumental(tmp_path, {"existing_instrumental": "jobs/j1/input/conformed/existing_instrumental.flac"})
    assert picked == tmp_path / "existing_instrumental.flac"
