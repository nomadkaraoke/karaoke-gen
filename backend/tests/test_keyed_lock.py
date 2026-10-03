"""Per-key locks restore per-instance serialization for threadpool route handlers."""
import threading
import time
from unittest.mock import MagicMock

from backend.utils.keyed_lock import KeyedLocks


def test_same_key_serializes_different_keys_dont():
    locks = KeyedLocks()
    order = []

    def worker(key, tag):
        with locks.hold(key):
            order.append(f"{tag}-in")
            time.sleep(0.1)
            order.append(f"{tag}-out")

    t1 = threading.Thread(target=worker, args=("job1", "a"))
    t2 = threading.Thread(target=worker, args=("job1", "b"))
    t1.start(); time.sleep(0.02); t2.start()
    t1.join(); t2.join()
    assert order == ["a-in", "a-out", "b-in", "b-out"]

    start = time.monotonic()
    t3 = threading.Thread(target=worker, args=("x", "c"))
    t4 = threading.Thread(target=worker, args=("y", "d"))
    t3.start(); t4.start(); t3.join(); t4.join()
    assert time.monotonic() - start < 0.18  # ran in parallel


def test_reentrant_and_cleans_up():
    locks = KeyedLocks()
    with locks.hold("k"):
        with locks.hold("k"):
            pass
    assert locks._locks == {} and locks._refs == {}


def test_concurrent_cancel_refunds_once():
    """Two threads cancelling the same job: the second must see CANCELLED and no-op."""
    from backend.models.job import JobStatus
    from backend.services import job_manager as jm_mod

    state = {"status": JobStatus.AWAITING_REVIEW}
    jm = jm_mod.JobManager.__new__(jm_mod.JobManager)

    def get_job(_id):
        time.sleep(0.05)  # widen the check-then-write window
        job = MagicMock()
        job.status = state["status"]
        return job

    def update_job_status(job_id, status, message):
        state["status"] = status

    jm.get_job = get_job
    jm.update_job_status = update_job_status
    jm._refund_credit_for_job = MagicMock()

    results = []
    threads = [threading.Thread(target=lambda: results.append(jm.cancel_job("j1"))) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [False, True]
    jm._refund_credit_for_job.assert_called_once()
