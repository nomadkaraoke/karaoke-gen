"""Budget invariants for the audio-download torrent stall handling."""
import pytest

from backend.services import audio_download_limits as limits
from backend.workers.audio_download_worker import DOWNLOAD_WAIT_TIMEOUT_SECONDS


@pytest.mark.parametrize("keep_trying", [False, True])
def test_task_timeout_exceeds_wait_exceeds_stall(keep_trying):
    """Cloud Run must not SIGKILL the worker before its wait (which must outlast
    flacfetch's stall ceiling) expires, so each budget nests inside the next."""
    stall = limits.stall_seconds(keep_trying)
    wait = limits.torrent_wait_timeout_seconds(keep_trying)
    task = limits.task_timeout_seconds(keep_trying)
    assert task > wait > stall > 0


def test_stall_budgets():
    assert limits.stall_seconds(False) == 1200
    # flacfetch caps max_stall_seconds at 3600 per request.
    assert limits.stall_seconds(True) == 3600


def test_keep_trying_budgets_are_longer():
    assert limits.task_timeout_seconds(True) > limits.task_timeout_seconds(False)
    assert limits.torrent_wait_timeout_seconds(True) > limits.torrent_wait_timeout_seconds(False)


def test_non_torrent_wait_below_default_task_timeout():
    """Non-torrent downloads still use DOWNLOAD_WAIT_TIMEOUT_SECONDS under the
    (default) per-run timeout override."""
    assert DOWNLOAD_WAIT_TIMEOUT_SECONDS < limits.task_timeout_seconds(False)


def test_cloud_run_max_timeout_not_exceeded():
    """Cloud Run Jobs cap a task timeout at 24h (168h preview); stay well under."""
    assert limits.task_timeout_seconds(True) <= 24 * 3600


def test_heartbeat_well_under_stuck_download_threshold():
    """Heartbeats bump updated_at; recover-stuck-jobs parks a downloading_audio
    job after 10 min without an update (job_health_service downloading_audio_stuck).
    Leave room for several missed/failed heartbeats."""
    stuck_threshold_seconds = 10 * 60
    assert 0 < limits.HEARTBEAT_INTERVAL_SECONDS * 3 <= stuck_threshold_seconds


def test_stuck_threshold_is_still_ten_minutes():
    """Guard the assumption above: if the stuck threshold changes, revisit the heartbeat."""
    import pathlib
    import re

    src = (pathlib.Path(__file__).resolve().parents[1] / "services" / "job_health_service.py").read_text()
    block = src[src.index("status == JobStatus.DOWNLOADING_AUDIO and job.updated_at"):]
    m = re.search(r"download_age > timedelta\(minutes=(\d+)\)", block)
    assert m and int(m.group(1)) == 10
