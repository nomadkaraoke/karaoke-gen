"""Phase 2: the video worker (distribution) on the AWS encoding box.

Covers the same-box finals handoff, the worker's /run-worker endpoint, the
box's env builder (mirrors the live Cloud Run job), and the backend trigger's
AWS branch — which must fall back to the Cloud Run job on any doubt.
"""
import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from backend.services import local_encoded_outputs as leo

# --- local_encoded_outputs ------------------------------------------------------

BLOB = "jobs/abc123/finals/Artist - Title (Final Karaoke Lossless 4k).mp4"


def test_handoff_is_noop_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv(leo.ENV_VAR, raising=False)
    f = tmp_path / "x.mp4"
    f.write_bytes(b"v")
    assert leo.stash(f, BLOB) is False
    assert leo.take(BLOB, str(tmp_path / "out.mp4")) is False


def test_stash_then_take_moves_file(tmp_path, monkeypatch):
    monkeypatch.setenv(leo.ENV_VAR, str(tmp_path / "stash"))
    src = tmp_path / "encoded.mp4"
    src.write_bytes(b"video-bytes")
    assert leo.stash(src, BLOB) is True
    dest = tmp_path / "work" / "final.mp4"
    assert leo.take(BLOB, str(dest)) is True
    assert dest.read_bytes() == b"video-bytes"
    assert leo.take(BLOB, str(dest)) is False  # moved, so a second take falls back to GCS


def test_take_missing_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv(leo.ENV_VAR, str(tmp_path))
    assert leo.take(BLOB, str(tmp_path / "o.mp4")) is False


def test_blob_path_cannot_escape_stash_dir(tmp_path, monkeypatch):
    monkeypatch.setenv(leo.ENV_VAR, str(tmp_path / "stash"))
    f = tmp_path / "x"
    f.write_bytes(b"1")
    assert leo.stash(f, "../../etc/evil") is False


def test_purge_stale_removes_only_old_files(tmp_path, monkeypatch):
    import os
    import time
    monkeypatch.setenv(leo.ENV_VAR, str(tmp_path))
    old, new = tmp_path / "jobs/a/old.mp4", tmp_path / "jobs/b/new.mp4"
    for p in (old, new):
        p.parent.mkdir(parents=True)
        p.write_bytes(b"1")
    os.utime(old, (time.time() - 2 * 86400,) * 2)
    assert leo.purge_stale() == 1
    assert not old.exists() and new.exists()


# --- worker /run-worker -----------------------------------------------------------

@pytest.fixture
def worker(monkeypatch, tmp_path):
    from backend.services.gce_encoding import main
    env_file = tmp_path / "video-worker-env.json"
    monkeypatch.setattr(main, "RUN_WORKER_ENV_FILE", str(env_file))
    monkeypatch.setattr(main, "RUN_WORKER_LOG_DIR", tmp_path / "runs")
    monkeypatch.setattr(main, "_persist", lambda job_id: None)
    main.jobs.clear()
    yield main, env_file
    main.jobs.clear()


def _req(main, job_id="abc123", module="video_worker"):
    return main.RunWorkerRequest(job_id=job_id, module=module)


def test_run_worker_disabled_without_env_file(worker):
    main, _ = worker
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.submit_worker_run(_req(main)))
    assert exc.value.status_code == 503


def test_run_worker_rejects_unknown_module(worker):
    main, env_file = worker
    env_file.write_text("{}")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.submit_worker_run(_req(main, module="audio_worker")))
    assert exc.value.status_code == 400


def test_run_worker_dedupes_running_job(worker):
    main, env_file = worker
    env_file.write_text("{}")
    main.jobs[main.run_worker_id("video_worker", "abc123")] = {"status": "running"}
    out = asyncio.run(main.submit_worker_run(_req(main)))
    assert out["status"] == "in_progress"


def test_run_worker_counts_as_active_immediately(worker):
    main, env_file = worker
    env_file.write_text(json.dumps({"ENVIRONMENT": "production"}))
    with patch.object(main, "_start_worker_run"):
        out = asyncio.run(main.submit_worker_run(_req(main)))
    assert out["status"] == "accepted"
    assert main.jobs[out["job_id"]]["status"] == "running"  # /health active_jobs counts it


def test_start_worker_run_spawns_module_with_box_env(worker):
    main, env_file = worker
    env_file.write_text(json.dumps({"ENVIRONMENT": "production", "LOCAL_ENCODED_OUTPUTS_DIR": "/x"}))
    run_id = main.run_worker_id("video_worker", "abc123")
    main.jobs[run_id] = {"status": "running", "module": "video_worker"}
    proc = MagicMock(pid=4242)
    proc.wait.return_value = 0
    with patch.object(main, "ensure_latest_wheel", return_value=True), \
         patch.object(main.subprocess, "Popen", return_value=proc) as popen:
        main._start_worker_run(run_id, _req(main), main._run_worker_env())
        args, kwargs = popen.call_args
    assert args[0][1:] == ["-m", "backend.workers.video_worker", "--job-id", "abc123"]
    assert kwargs["env"]["LOCAL_ENCODED_OUTPUTS_DIR"] == "/x"
    assert "ENCODING_API_KEY" not in kwargs["env"]
    main._watch_worker_run(run_id, proc, Path(main.jobs[run_id]["log"]))
    assert main.jobs[run_id]["status"] == "complete"


def test_failed_run_reports_log_tail(worker, tmp_path):
    main, _ = worker
    run_id = main.run_worker_id("video_worker", "abc123")
    main.jobs[run_id] = {"status": "running", "module": "video_worker"}
    log = tmp_path / "run.log"
    log.write_text("Traceback ... Dropbox upload failed")
    proc = MagicMock()
    proc.wait.return_value = 1
    main._watch_worker_run(run_id, proc, log)
    assert main.jobs[run_id]["status"] == "failed"
    assert "Dropbox upload failed" in main.jobs[run_id]["error"]


def test_wheel_not_ready_fails_run_without_spawning(worker):
    main, env_file = worker
    env_file.write_text("{}")
    run_id = main.run_worker_id("video_worker", "abc123")
    main.jobs[run_id] = {"status": "running"}
    with patch.object(main, "ensure_latest_wheel", return_value=False), \
         patch.object(main.subprocess, "Popen") as popen:
        main._start_worker_run(run_id, _req(main), {})
    popen.assert_not_called()
    assert main.jobs[run_id]["status"] == "failed"


# --- video-worker-env.py ------------------------------------------------------------

_ENV_PATH = Path(__file__).resolve().parents[2] / "infrastructure" / "encoding-worker" / "aws" / "video-worker-env.py"
_spec = importlib.util.spec_from_file_location("video_worker_env", _ENV_PATH)
vwe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vwe)


def test_build_env_mirrors_job_and_resolves_secrets():
    job = {"template": {"template": {"containers": [{"env": [
        {"name": "ENVIRONMENT", "value": "production"},
        {"name": "POSTMARK_SERVER_TOKEN", "valueSource": {"secretKeyRef": {
            "secret": "projects/nomadkaraoke/secrets/postmark-server-token", "version": "latest"}}},
    ]}]}}}
    env = vwe.build_env(job, lambda secret, version: f"<{secret}@{version}>")
    assert env["ENVIRONMENT"] == "production"
    assert env["POSTMARK_SERVER_TOKEN"] == "<postmark-server-token@latest>"
    assert env["GOOGLE_APPLICATION_CREDENTIALS"].endswith("gcp-wif-backend.json")
    assert env["LOCAL_ENCODED_OUTPUTS_DIR"] and env["CLOUD_RUN_JOB"] == "video-encoding-job"


# --- backend trigger ------------------------------------------------------------------

def _candidate(provider, vm="aws-encoding-worker-1"):
    return SimpleNamespace(provider=provider, vm_name=vm, url="http://3.3.3.3:8080", zone="us-east-2b")


@pytest.fixture
def service():
    from backend.services.worker_service import WorkerService
    svc = WorkerService.__new__(WorkerService)
    svc.settings = MagicMock(google_cloud_project="nomadkaraoke")
    return svc


def _manager(aws_video_worker=True, top="aws", started="aws-encoding-worker-1"):
    m = MagicMock()
    m.get_config.return_value = SimpleNamespace(aws_video_worker=aws_video_worker)
    m.ranked_candidates.return_value = [_candidate(top, "aws-encoding-worker-1" if top == "aws" else "encoding-worker-a")]
    m.ensure_any_running.return_value = {"vm_name": started}
    m.wait_for_worker_ready = AsyncMock()
    return m


def _run(service, manager, response=None, post_exc=None):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(side_effect=post_exc, return_value=response)
    enc = MagicMock(_api_key="k")
    with patch.object(service, "_build_encoding_worker_manager", return_value=manager), \
         patch("backend.services.encoding_service.get_encoding_service", return_value=enc), \
         patch("backend.services.worker_service.httpx.AsyncClient", return_value=client):
        return asyncio.run(service._trigger_video_worker_on_aws("abc123")), client


def test_aws_dispatch_off_by_default(service):
    ok, client = _run(service, _manager(aws_video_worker=False))
    assert ok is False
    client.post.assert_not_called()


def test_aws_dispatch_requires_aws_to_rank_first(service):
    ok, client = _run(service, _manager(top=None))
    assert ok is False
    client.post.assert_not_called()


def test_aws_dispatch_posts_run_worker(service):
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"status": "accepted"}
    ok, client = _run(service, _manager(), response=resp)
    assert ok is True
    url = client.post.call_args.args[0]
    assert url == "http://3.3.3.3:8080/run-worker"
    assert client.post.call_args.kwargs["json"] == {"job_id": "abc123", "module": "video_worker"}


def test_aws_dispatch_falls_back_when_aws_did_not_start(service):
    ok, client = _run(service, _manager(started="encoding-worker-a"))
    assert ok is False
    client.post.assert_not_called()


@pytest.mark.parametrize("status,body", [(503, {"detail": "not enabled"}), (200, {"status": "weird"})])
def test_aws_dispatch_falls_back_on_refusal(service, status, body):
    resp = MagicMock(status_code=status, text=json.dumps(body))
    resp.json.return_value = body
    ok, _ = _run(service, _manager(), response=resp)
    assert ok is False


def test_aws_dispatch_falls_back_on_error(service):
    ok, _ = _run(service, _manager(), post_exc=ConnectionError("boom"))
    assert ok is False


def test_start_failure_never_leaves_run_stuck(worker):
    main, env_file = worker
    run_id = main.run_worker_id("video_worker", "abc123")
    main.jobs[run_id] = {"status": "running"}
    with patch.object(main, "_start_worker_run", side_effect=OSError("disk full")):
        main._start_worker_run_safe(run_id, _req(main), {})
    assert main.jobs[run_id]["status"] == "failed"
    assert "disk full" in main.jobs[run_id]["error"]


@pytest.mark.parametrize("bad", ["../../tmp/x", "-h", "", "a b", "x" * 80])
def test_run_worker_rejects_unsafe_job_ids(bad):
    from pydantic import ValidationError
    from backend.services.gce_encoding import main
    with pytest.raises(ValidationError):
        main.RunWorkerRequest(job_id=bad)


def test_gce_backend_requests_stash_only_on_the_box(monkeypatch):
    """The encode payload asks for local copies only where the video worker
    shares the box with the encoder (LOCAL_ENCODED_OUTPUTS_DIR set)."""
    from backend.services.encoding_interface import EncodingInput, GCEEncodingBackend

    async def capture(env_value):
        if env_value:
            monkeypatch.setenv("LOCAL_ENCODED_OUTPUTS_DIR", env_value)
        else:
            monkeypatch.delenv("LOCAL_ENCODED_OUTPUTS_DIR", raising=False)
        svc = MagicMock()
        svc.encode_videos = AsyncMock(return_value={"status": "complete", "output_files": []})
        backend = GCEEncodingBackend()
        backend._service = svc
        with patch.object(GCEEncodingBackend, "_get_service", return_value=svc):
            await backend.encode(EncodingInput(
                title_video_path="t", karaoke_video_path="k", instrumental_audio_path="i",
                artist="A", title="T", output_dir="/tmp",
                options={"job_id": "abc123_g1", "input_gcs_path": "gs://b/jobs/abc123/",
                         "output_gcs_path": "gs://b/jobs/abc123/finals/"},
            ))
        return svc.encode_videos.call_args

    on = asyncio.run(capture("/var/lib/encoding-worker/outputs"))
    off = asyncio.run(capture(None))
    on_cfg = on.kwargs.get("encoding_config") or on.args[-1]
    off_cfg = off.kwargs.get("encoding_config") or off.args[-1]
    assert on_cfg.get("stash_local_outputs") is True
    assert "stash_local_outputs" not in off_cfg
