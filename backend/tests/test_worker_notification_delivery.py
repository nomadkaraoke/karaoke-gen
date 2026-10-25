"""Notifications sent from Cloud Run Job workers must be delivered before the worker exits.

A worker runs its main coroutine with ``asyncio.run(...)``. JobManager used to send
pushes/emails with a bare ``loop.create_task(...)``; the worker then returned, and
``asyncio.run`` cancelled the pending task on exit, so the Web Push + Expo send
(and completion email / idle-reminder scheduling) never happened, silently
(CancelledError is not an Exception). Each worker's ``main()`` now drains the
pending notifications before the loop closes, with a bounded wait.

Reuses the ``triggers`` fixture from test_expo_push: real JobManager notification
code, real Expo + Web Push services over the in-memory Firestore fake; only Expo
HTTP and pywebpush are faked.
"""
import asyncio
import importlib
import logging
import sys
from unittest.mock import MagicMock, patch

import pytest

from backend.models.job import JobStatus
from backend.services import job_manager as jm_module
from backend.services.job_manager import JobManager, drain_notifications
from backend.tests.test_expo_push import A, db, triggers, user_service  # noqa: F401  (fixtures)


# (module, coroutine main() runs, argv, return value meaning "clean exit")
WORKERS = [
    ("backend.workers.audio_download_worker", "process_audio_download", ["--job-id", "job1"], True),
    ("backend.workers.audio_worker", "process_audio_separation", ["--job-id", "job1"], True),
    ("backend.workers.lyrics_worker", "process_lyrics_transcription", ["--job-id", "job1"], True),
    ("backend.workers.video_worker", "generate_video", ["--job-id", "job1"], True),
    ("backend.workers.render_video_worker", "process_render_video", ["--job-id", "job1"], True),
    ("backend.workers.bulk_search_worker", "process_batch", ["--batch-id", "b1"], 1),
]


def _run_worker_main(module_name, fn_name, argv, result, body):
    """Run the worker's real CLI ``main()`` with its job coroutine replaced by
    ``body()`` (sync, like the worker's own job_manager calls) + return ``result``."""
    module = importlib.import_module(module_name)

    async def fake_job(_id):
        body()
        return result  # returns at once: nothing else awaits after the notification

    with patch.object(module, fn_name, fake_job), \
         patch.object(sys, "argv", ["worker", *argv]), \
         pytest.raises(SystemExit) as exit_info:
        module.main()
    assert exit_info.value.code == 0


@pytest.mark.parametrize("module_name,fn_name,argv,result", WORKERS, ids=[w[0].rsplit(".", 1)[1] for w in WORKERS])
class TestWorkerFailurePushIsDelivered:
    def test_fail_job_push_delivered_before_exit(self, triggers, module_name, fn_name, argv, result):
        pushes, webpush = triggers(
            JobStatus.DOWNLOADING_AUDIO,
            action=lambda mgr: _run_worker_main(
                module_name, fn_name, argv, result,
                lambda: mgr.fail_job("job1", "boom", {"stage": "x"})),
        )
        assert [(p["kind"], p["url"]) for p in pushes] == [("problems", "/job/job1")]
        webpush.assert_called_once()

    def test_mark_job_failed_push_delivered_before_exit(self, triggers, module_name, fn_name, argv, result):
        pushes, webpush = triggers(
            JobStatus.RENDERING_VIDEO,
            action=lambda mgr: _run_worker_main(
                module_name, fn_name, argv, result,
                lambda: mgr.mark_job_failed("job1", "boom", {"stage": "x"})),
        )
        assert [p["kind"] for p in pushes] == ["problems"]
        webpush.assert_called_once()


class TestWorkerTransitionPushIsDelivered:
    @pytest.mark.parametrize("from_status,to_status,kind,url", [
        (JobStatus.GENERATING_SCREENS, JobStatus.AWAITING_REVIEW, "review", "/review/job1"),
        (JobStatus.DOWNLOADING, JobStatus.AWAITING_AUDIO_EDIT, "review", "/job/job1"),
        (JobStatus.ENCODING, JobStatus.COMPLETE, "ready", "/job/job1"),
    ])
    def test_transition_push_delivered_before_exit(self, triggers, from_status, to_status, kind, url):
        pushes, webpush = triggers(
            from_status,
            action=lambda mgr: _run_worker_main(
                "backend.workers.video_worker", "generate_video", ["--job-id", "job1"], True,
                lambda: mgr.transition_to_state("job1", new_status=to_status)),
        )
        assert [(p["kind"], p["url"]) for p in pushes] == [(kind, url)]
        webpush.assert_called_once()


def _network_call(done: list):
    """Stand-in for a provider call that records only once it COMPLETES, after
    yielding to the loop like real I/O. (An AsyncMock counts as awaited as soon
    as it starts, and asyncio.run still runs a fresh task's first step before
    cancelling it, which would hide the bug.)"""
    async def call(*args, **kwargs):
        await asyncio.sleep(0.01)
        done.append((args, kwargs))
    return call


def _manager_with_job(job):
    mgr = JobManager.__new__(JobManager)
    mgr.get_job = lambda job_id: job
    mgr.firestore = MagicMock()
    return mgr


class TestEmailAndReminderAreDelivered:
    """The completion email and idle-reminder scheduling use the same fire-and-forget
    path, so they were exposed to the same cancellation."""

    def test_completion_email_sent_before_worker_exits(self):
        from backend.tests.test_expo_push import _job
        job = _job(JobStatus.COMPLETE)
        mgr = _manager_with_job(job)
        notifier = MagicMock()
        sent = []
        notifier.send_job_completion_email = _network_call(sent)
        with patch("backend.services.job_notification_service.get_job_notification_service", return_value=notifier), \
             patch("backend.services.delivery_guard.completion_email_hold_reason", return_value=None):
            _run_worker_main("backend.workers.video_worker", "generate_video", ["--job-id", "job1"], True,
                             lambda: mgr._schedule_completion_email(job))
        assert len(sent) == 1
        assert sent[0][1]["job_id"] == "job1"

    def test_idle_reminder_scheduled_before_worker_exits(self):
        from backend.tests.test_expo_push import _job
        job = _job(JobStatus.AWAITING_AUDIO_EDIT)
        mgr = _manager_with_job(job)
        worker_service = MagicMock()
        scheduled = []
        worker_service.schedule_idle_reminder = _network_call(scheduled)
        with patch("backend.services.worker_service.get_worker_service", return_value=worker_service):
            _run_worker_main("backend.workers.audio_download_worker", "process_audio_download",
                             ["--job-id", "job1"], True,
                             lambda: mgr._schedule_idle_reminder(job, JobStatus.AWAITING_AUDIO_EDIT))
        assert scheduled == [(("job1",), {})]


class TestFailurePushSentFlag:
    def test_flag_written_only_after_the_push_was_attempted(self, triggers):
        seen = {}

        def action(mgr):
            async def worker():
                mgr.fail_job("job1", "boom", {"stage": "x"})
                seen["at_schedule"] = mgr.get_job("job1").state_data.get("failure_push_sent")
                await drain_notifications()
                seen["after_send"] = mgr.get_job("job1").state_data.get("failure_push_sent")
            asyncio.run(worker())

        pushes, _ = triggers(JobStatus.DOWNLOADING_AUDIO, action=action)
        assert len(pushes) == 1
        assert not seen["at_schedule"]
        assert seen["after_send"] is True

    def test_cancelled_send_leaves_flag_unset(self, triggers):
        """A run whose push never went out must not stop a later failure's push."""
        def action(mgr):
            async def worker():
                mgr.fail_job("job1", "boom", {"stage": "x"})
                # no drain: asyncio.run cancels the pending send on exit
            asyncio.run(worker())
            assert not mgr.get_job("job1").state_data.get("failure_push_sent")

        triggers(JobStatus.DOWNLOADING_AUDIO, action=action)

    def test_send_cancelled_before_its_first_step_clears_in_flight(self, triggers):
        """A task cancelled before it ever ran (its body never starts, so no finally
        inside it runs) must not leave the job marked in flight in this process,
        or every later failure push for the job would be skipped."""
        def action(mgr):
            async def worker():
                mgr.fail_job("job1", "first", {"stage": "x"})
                assert "job1" in jm_module._failure_push_in_flight
                for task in list(jm_module._pending_notification_tasks):
                    task.cancel()  # before the loop gave it a first step
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                assert "job1" not in jm_module._failure_push_in_flight
                assert not mgr.get_job("job1").state_data.get("failure_push_sent")
                # The next failure of this (still unnotified) run pushes.
                mgr.fail_job("job1", "second", {"stage": "x"})
                await drain_notifications()
            asyncio.run(worker())

        pushes, webpush = triggers(JobStatus.DOWNLOADING_AUDIO, action=action)
        assert len(pushes) == 1
        webpush.assert_called_once()
        assert "job1" not in jm_module._failure_push_in_flight

    def test_thread_path_clears_in_flight(self, triggers):
        """No running loop (sync worker helper): the send runs on a thread, which
        clears the in-flight mark when it ends."""
        pushes, _ = triggers(JobStatus.DOWNLOADING_AUDIO,
                             action=lambda mgr: mgr.fail_job("job1", "boom", {"stage": "x"}))
        assert len(pushes) == 1
        assert "job1" not in jm_module._failure_push_in_flight

    def test_two_failures_in_one_worker_push_once_even_before_the_send(self, triggers):
        def action(mgr):
            async def worker():
                mgr.fail_job("job1", "first", {"stage": "x"})
                mgr.mark_job_failed("job1", "second", {"stage": "x"})  # before the first send ran
                await drain_notifications()
            asyncio.run(worker())

        pushes, webpush = triggers(JobStatus.DOWNLOADING_AUDIO, action=action)
        assert len(pushes) == 1
        webpush.assert_called_once()

    def test_manual_retry_then_second_failure_pushes_again(self, triggers):
        def fail_in_worker(mgr, message):
            async def worker():
                mgr.fail_job("job1", message, {"stage": "audio_download"})
                await drain_notifications()
            asyncio.run(worker())

        def action(mgr):
            fail_in_worker(mgr, "first failure")
            assert mgr.get_job("job1").state_data.get("failure_push_sent") is True
            # Manual retry moves the job out of FAILED...
            assert mgr.transition_to_state("job1", new_status=JobStatus.DOWNLOADING_AUDIO, notify=False)
            # ...so the retried run failing again is a new problem worth a push.
            fail_in_worker(mgr, "second failure")

        pushes, webpush = triggers(JobStatus.DOWNLOADING_AUDIO, action=action)
        assert [p["kind"] for p in pushes] == ["problems", "problems"]
        assert webpush.call_count == 2


class TestDrainBounds:
    def test_hung_provider_cannot_keep_the_worker_alive(self, caplog):
        async def hang():
            await asyncio.sleep(3600)

        async def worker():
            jm_module._schedule_notification(hang, "hung test send")
            loop = asyncio.get_running_loop()
            t0 = loop.time()
            delivered = await drain_notifications(timeout_s=0.2)
            return delivered, loop.time() - t0

        with caplog.at_level(logging.WARNING, logger=jm_module.logger.name):
            delivered, waited = asyncio.run(worker())
        assert delivered is False
        assert waited < 2
        assert any(r.levelno == logging.WARNING and "notification" in r.getMessage().lower()
                   for r in caplog.records)

    def test_drain_with_nothing_pending_returns_at_once(self):
        assert asyncio.run(drain_notifications(timeout_s=0.01)) is True

    def test_failing_send_does_not_raise_from_drain(self):
        async def boom():
            raise RuntimeError("provider down")

        async def worker():
            jm_module._schedule_notification(boom, "failing test send")
            return await drain_notifications(timeout_s=1)

        assert asyncio.run(worker()) is True

    def test_worker_result_kept_when_drain_times_out(self):
        async def hang():
            await asyncio.sleep(3600)

        async def job():
            jm_module._schedule_notification(hang, "hung test send")
            return "result"

        with patch.object(jm_module, "NOTIFICATION_DRAIN_TIMEOUT_S", 0.1):
            assert asyncio.run(jm_module.with_notification_drain(job())) == "result"

    def test_drain_runs_when_the_worker_raises(self):
        sent = []

        async def send():
            await asyncio.sleep(0)
            sent.append(True)

        async def job():
            jm_module._schedule_notification(send, "test send")
            raise ValueError("worker crashed")

        with pytest.raises(ValueError):
            asyncio.run(jm_module.with_notification_drain(job()))
        assert sent == [True]


class TestApiServerStaysFireAndForget:
    def test_trigger_returns_before_the_send_runs(self, triggers):
        """In the API server the loop lives on: scheduling must not wait for the send."""
        seen = {}

        def action(mgr):
            async def handler():
                mgr._trigger_state_notifications("job1", JobStatus.COMPLETE)
                from backend.services.fake_workers import outbox
                seen["right_after"] = len(outbox.list_pushes(A))
                await drain_notifications()
            asyncio.run(handler())

        pushes, _ = triggers(JobStatus.COMPLETE, action=action)
        assert seen["right_after"] == 0
        assert len(pushes) == 1
