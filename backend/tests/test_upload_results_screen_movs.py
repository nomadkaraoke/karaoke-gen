"""Test that _upload_results registers the standalone screen MOVs in file_urls.

This makes (Title).mov / (End).mov appear in the admin files manifest and verifiable
by the daily E2E. Regression: the screen MOVs were absent from Dropbox track folders.
"""

import os
import tempfile

import pytest

from backend.workers.video_worker import _upload_results


@pytest.mark.asyncio
async def test_upload_results_registers_screen_movs():
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create local files the orchestrator would have downloaded into output_dir
        title_mov = os.path.join(temp_dir, "Artist - Title (Title).mov")
        end_mov = os.path.join(temp_dir, "Artist - Title (End).mov")
        lossless = os.path.join(temp_dir, "Artist - Title (Final Karaoke Lossless 4k).mp4")
        for p in (title_mov, end_mov, lossless):
            with open(p, "wb") as f:
                f.write(b"x")

        from unittest.mock import MagicMock
        job_manager = MagicMock()
        storage = MagicMock()
        storage.upload_file.side_effect = lambda local, gcs: f"gs://bucket/{gcs}"

        result = {
            "final_video": lossless,
            "title_mov": title_mov,
            "end_mov": end_mov,
        }

        await _upload_results("job-1", job_manager, storage, temp_dir, result)

        # The screen MOVs are uploaded under finals/ and recorded in file_urls
        registered = {
            (c.args[1], c.args[2]) for c in job_manager.update_file_url.call_args_list
        }
        assert ("finals", "title_mov") in registered
        assert ("finals", "end_mov") in registered

        uploaded_gcs = {c.args[1] for c in storage.upload_file.call_args_list}
        assert "jobs/job-1/finals/title_mov.mov" in uploaded_gcs
        assert "jobs/job-1/finals/end_mov.mov" in uploaded_gcs


@pytest.mark.asyncio
async def test_upload_results_skips_missing_screen_movs():
    """Jobs without screen MOVs (legacy/None) must not error or register them."""
    with tempfile.TemporaryDirectory() as temp_dir:
        lossless = os.path.join(temp_dir, "Artist - Title (Final Karaoke Lossless 4k).mp4")
        with open(lossless, "wb") as f:
            f.write(b"x")

        from unittest.mock import MagicMock
        job_manager = MagicMock()
        storage = MagicMock()
        storage.upload_file.side_effect = lambda local, gcs: f"gs://bucket/{gcs}"

        result = {"final_video": lossless, "title_mov": None}  # end_mov absent entirely

        await _upload_results("job-1", job_manager, storage, temp_dir, result)

        registered = {
            (c.args[1], c.args[2]) for c in job_manager.update_file_url.call_args_list
        }
        assert ("finals", "title_mov") not in registered
        assert ("finals", "end_mov") not in registered
