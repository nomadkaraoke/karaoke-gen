"""Job-log Firestore writes must not run on the API event loop (log_write_offload)."""
import asyncio
import queue
import threading
from unittest.mock import MagicMock

import pytest

from backend.services import log_write_offload


@pytest.fixture(autouse=True)
def _not_in_cloud_run_job(monkeypatch):
    monkeypatch.delenv("CLOUD_RUN_JOB", raising=False)
    yield
    assert log_write_offload.drain(timeout=5)


def test_runs_inline_off_the_event_loop():
    ran_on = []
    log_write_offload.submit_or_run(lambda: ran_on.append(threading.get_ident()))
    assert ran_on == [threading.get_ident()]


def test_offloads_from_a_running_event_loop():
    release = threading.Event()
    ran_on = []

    def slow_write():
        release.wait(5)
        ran_on.append(threading.get_ident())

    async def on_loop():
        log_write_offload.submit_or_run(slow_write)
        # submit returned without waiting for the (blocked) write
        assert ran_on == []
        return threading.get_ident()

    loop_thread = asyncio.run(on_loop())
    release.set()
    assert log_write_offload.drain(timeout=5)
    assert len(ran_on) == 1 and ran_on[0] != loop_thread


def test_runs_inline_on_the_loop_inside_a_cloud_run_job(monkeypatch):
    monkeypatch.setenv("CLOUD_RUN_JOB", "video-encoding-job")
    ran_on = []

    async def on_loop():
        log_write_offload.submit_or_run(lambda: ran_on.append(threading.get_ident()))
        return threading.get_ident()

    assert ran_on == [asyncio.run(on_loop())]


def test_offloaded_writes_keep_submission_order():
    written = []

    async def on_loop():
        for i in range(50):
            log_write_offload.submit_or_run(lambda i=i: written.append(i))

    asyncio.run(on_loop())
    assert log_write_offload.drain(timeout=5)
    assert written == list(range(50))


def test_full_queue_drops_with_a_warning(monkeypatch, caplog):
    release = threading.Event()
    monkeypatch.setattr(log_write_offload, "_queue", queue.Queue(maxsize=1))
    monkeypatch.setattr(log_write_offload, "_worker", None)
    monkeypatch.setattr(log_write_offload, "_dropped", 0)
    monkeypatch.setattr(log_write_offload, "_last_drop_warning", 0.0)
    written = []

    async def on_loop():
        log_write_offload.submit_or_run(lambda: release.wait(5))  # occupies the writer
        await asyncio.sleep(0.05)
        log_write_offload.submit_or_run(lambda: written.append("queued"))  # fills the queue
        log_write_offload.submit_or_run(lambda: written.append("dropped"))  # no room

    with caplog.at_level("WARNING", logger=log_write_offload.__name__):
        asyncio.run(on_loop())
    release.set()
    assert log_write_offload.drain(timeout=5)
    assert written == ["queued"]
    assert log_write_offload._dropped == 1
    assert "dropped 1 job-log lines" in caplog.text


def test_firestore_append_log_does_not_write_on_the_loop_thread():
    from backend.models.worker_log import WorkerLogEntry
    from backend.services.firestore_service import FirestoreService

    service = FirestoreService.__new__(FirestoreService)
    service.collection = "jobs"
    service.db = MagicMock()
    doc_ref = service.db.collection.return_value.document.return_value.collection.return_value.document.return_value
    set_threads = []
    doc_ref.set.side_effect = lambda data: set_threads.append(threading.get_ident())

    async def on_loop():
        service.append_log_to_subcollection("job1", WorkerLogEntry.create(job_id="job1", worker="screens", level="INFO", message="hi"))
        return threading.get_ident()

    loop_thread = asyncio.run(on_loop())
    assert log_write_offload.drain(timeout=5)
    assert len(set_threads) == 1 and set_threads[0] != loop_thread
    assert doc_ref.set.call_args.args[0]["message"] == "hi"
