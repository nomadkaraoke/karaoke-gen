"""Tests for redistribute_video (public -> private visibility change).

2026-10-25 #alerts: the redistribution ran ON the API event loop (39s stall, 13
requests incl. /api/health), and it uploaded every final to Dropbox twice because GCS
finals/ holds both the short canonical names and the readable encoder names.
"""
import asyncio
import os
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from backend.workers import video_worker
from backend.workers.video_worker import (
    _dedupe_redistribution_files,
    redistribute_video,
    redistribute_video_off_loop,
)

BASE = "Olivira - Black Forest 2"


def _touch(directory, name, content=b"x"):
    path = os.path.join(directory, name)
    with open(path, "wb") as f:
        f.write(content)
    return path


class TestDedupeRedistributionFiles:
    def test_short_name_dropped_when_readable_twin_present(self, tmp_path):
        d = str(tmp_path)
        files = {
            f"{BASE} (Final Karaoke Lossy 4k).mp4": _touch(d, f"{BASE} (Final Karaoke Lossy 4k).mp4"),
            "lossy_4k_mp4.mp4": _touch(d, "lossy_4k_mp4.mp4"),
            f"{BASE} (Title).mov": _touch(d, f"{BASE} (Title).mov"),
            "title_mov.mov": _touch(d, "title_mov.mov"),
        }
        result = _dedupe_redistribution_files(files, BASE)
        assert sorted(result) == sorted([f"{BASE} (Final Karaoke Lossy 4k).mp4", f"{BASE} (Title).mov"])
        assert sorted(os.listdir(d)) == sorted(result)  # the duplicate is gone from disk too

    def test_short_name_without_twin_renamed_to_readable(self, tmp_path):
        d = str(tmp_path)
        files = {
            "lossless_4k_mp4.mp4": _touch(d, "lossless_4k_mp4.mp4", b"lossless"),
            "cdg_zip.zip": _touch(d, "cdg_zip.zip", b"cdg"),
        }
        result = _dedupe_redistribution_files(files, BASE)
        lossless = f"{BASE} (Final Karaoke Lossless 4k).mp4"
        cdg = f"{BASE} (Final Karaoke CDG).zip"
        assert sorted(result) == sorted([lossless, cdg])
        assert result[lossless] == os.path.join(d, lossless)
        with open(result[lossless], "rb") as f:
            assert f.read() == b"lossless"
        assert sorted(os.listdir(d)) == sorted([lossless, cdg])

    def test_twin_matched_on_suffix_even_if_base_name_differs(self, tmp_path):
        d = str(tmp_path)
        encoder_name = "Olivira - Black Forest 2 (Remix) (Final Karaoke Lossy 720p).mp4"
        files = {
            encoder_name: _touch(d, encoder_name),
            "lossy_720p_mp4.mp4": _touch(d, "lossy_720p_mp4.mp4"),
        }
        assert list(_dedupe_redistribution_files(files, BASE)) == [encoder_name]

    def test_unrecognised_files_pass_through(self, tmp_path):
        d = str(tmp_path)
        files = {"notes.txt": _touch(d, "notes.txt")}
        assert _dedupe_redistribution_files(files, BASE) == files


def _redistribute_patches(tmp_path, blobs):
    """Mocks for one redistribute_video run; returns (patches, orchestrator_cls, uploaded)."""
    job = SimpleNamespace(
        job_id="job1", artist="Olivira", title="Black Forest 2", state_data={},
        enable_cdg=True, enable_txt=True, is_private=True,
    )
    job_manager = MagicMock()
    job_manager.get_job.return_value = job
    storage = MagicMock()
    storage.list_files.side_effect = lambda prefix: [b for b in blobs if b.startswith(prefix)]
    storage.download_file.side_effect = lambda blob, local: _touch(os.path.dirname(local), os.path.basename(local))

    dist = SimpleNamespace(
        enable_youtube_upload=False, brand_prefix="NOMADNP", discord_webhook_url=None,
        youtube_description=None, dropbox_path="/Tracks-NonPublished", gdrive_folder_id=None,
    )
    seen = {}

    def make_orchestrator(config, **kwargs):
        orch = MagicMock()
        orch.result = SimpleNamespace(
            brand_code=None, youtube_url=None, dropbox_link="https://dropbox/x",
            gdrive_files=None, distribution_warnings=[],
        )

        async def organize():
            orch.result.brand_code = "NOMADNP-0001"

        async def distribute():
            # What _upload_to_dropbox would upload: the whole output dir.
            seen["uploaded"] = sorted(os.listdir(config.output_dir))
            seen["thread"] = threading.get_ident()

        orch._run_organization = organize
        orch._run_distribution = distribute
        orch._run_notifications = AsyncMock()
        return orch

    patches = [
        patch.object(video_worker, "JobManager", return_value=job_manager),
        patch.object(video_worker, "StorageService", return_value=storage),
        patch.object(video_worker, "create_job_logger", return_value=MagicMock()),
        patch("backend.workers.video_worker_orchestrator.VideoWorkerOrchestrator", side_effect=make_orchestrator),
        patch("backend.services.job_defaults_service.get_effective_distribution_for_job", return_value=dist),
        patch.object(video_worker.tempfile, "mkdtemp", return_value=str(tmp_path)),
    ]
    return patches, seen, job_manager


class TestRedistributeVideo:
    @pytest.mark.asyncio
    async def test_uploads_each_final_once_under_its_readable_name(self, tmp_path):
        blobs = [
            f"jobs/job1/finals/{BASE} (Final Karaoke Lossy 4k).mp4",
            f"jobs/job1/finals/{BASE} (Final Karaoke Lossless 4k).mkv",
            f"jobs/job1/finals/{BASE} (Karaoke).mp4",
            "jobs/job1/finals/lossy_4k_mp4.mp4",
            "jobs/job1/finals/lossless_4k_mkv.mkv",
            "jobs/job1/finals/lossy_720p_mp4.mp4",  # no readable twin: renamed
            "jobs/job1/packages/cdg_zip.zip",
        ]
        patches, seen, job_manager = _redistribute_patches(tmp_path, blobs)
        for p in patches:
            p.start()
        try:
            assert await redistribute_video("job1") is True
        finally:
            for p in patches:
                p.stop()
        assert seen["uploaded"] == sorted([
            f"{BASE} (Final Karaoke Lossy 4k).mp4",
            f"{BASE} (Final Karaoke Lossless 4k).mkv",
            f"{BASE} (Karaoke).mp4",
            f"{BASE} (Final Karaoke Lossy 720p).mp4",
            f"{BASE} (Final Karaoke CDG).zip",
        ])
        update = job_manager.update_job.call_args.args[1]
        assert update["state_data.brand_code"] == "NOMADNP-0001"

    def test_off_loop_runner_uses_its_own_loop_on_the_calling_thread(self, tmp_path):
        patches, seen, _ = _redistribute_patches(tmp_path, ["jobs/job1/finals/lossy_4k_mp4.mp4"])
        for p in patches:
            p.start()
        try:
            result = {}
            t = threading.Thread(target=lambda: result.update(ok=redistribute_video_off_loop("job1")))
            t.start()
            t.join(10)
        finally:
            for p in patches:
                p.stop()
        assert result == {"ok": True}
        assert seen["thread"] == t.ident


class TestChangeToPrivateOffLoop:
    @pytest.mark.asyncio
    async def test_redistribution_runs_off_the_event_loop(self):
        from backend.services.visibility_change_service import VisibilityChangeService

        job_manager = MagicMock()
        job_manager.firestore.db.collection.return_value.document.return_value = MagicMock()
        job = SimpleNamespace(
            job_id="job1", state_data={}, dropbox_path=None, artist="A", title="T",
            renders_purged_at=None,
        )
        loop_thread = threading.get_ident()
        api_loop = asyncio.get_running_loop()
        where = {}

        async def fake_redistribute(job_id):
            where["thread"] = threading.get_ident()
            where["loop"] = asyncio.get_running_loop()
            return True

        with patch("backend.workers.video_worker.redistribute_video", side_effect=fake_redistribute), \
             patch.object(VisibilityChangeService, "_delete_public_outputs"):
            result = await VisibilityChangeService(job_manager).change_to_private("job1", job, "u@example.com")

        assert result["status"] == "success"
        assert where["thread"] != loop_thread
        assert where["loop"] is not api_loop

    @pytest.mark.asyncio
    async def test_cancelled_request_still_deletes_public_outputs(self):
        """Cancelling the caller mid-redistribution must not leave a committed private
        archive with the public outputs still live: redistribute + cleanup + commit
        run as one (uncancellable) worker-thread call."""
        from backend.services.visibility_change_service import VisibilityChangeService

        job_manager = MagicMock()
        job_ref = MagicMock()
        job_manager.firestore.db.collection.return_value.document.return_value = job_ref
        job = SimpleNamespace(
            job_id="job1", state_data={"youtube_url": "https://youtu.be/x"}, dropbox_path=None,
            artist="A", title="T", renders_purged_at=None,
        )
        started, release, deleted = threading.Event(), threading.Event(), threading.Event()

        async def slow_redistribute(job_id):
            started.set()
            await asyncio.to_thread(release.wait, 5)
            return True

        with patch("backend.workers.video_worker.redistribute_video", side_effect=slow_redistribute), \
             patch.object(VisibilityChangeService, "_delete_public_outputs",
                          side_effect=lambda *a: deleted.set()) as delete:
            task = asyncio.ensure_future(
                VisibilityChangeService(job_manager).change_to_private("job1", job, "u@example.com"))
            assert await asyncio.to_thread(started.wait, 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            release.set()
            assert await asyncio.to_thread(deleted.wait, 5)

        assert delete.call_args.args[1]["youtube_url"] == "https://youtu.be/x"
