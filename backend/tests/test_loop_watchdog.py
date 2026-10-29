"""Tests for the event-loop stall watchdog (backend/services/loop_watchdog.py)."""
import asyncio
import logging
import time

import pytest
from unittest.mock import patch

from backend.services.loop_watchdog import LoopWatchdog, _format_stack


def _blocking_culprit_function(seconds: float) -> None:
    time.sleep(seconds)  # deliberately blocks the event loop


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class TestCheckStateMachine:
    """Drive check() with a fake clock — no threads, fully deterministic."""

    def _wd(self, **kw):
        clock = FakeClock()
        recorded = []
        # cpu_clock tracks wall time: the process was running (not throttled) between checks.
        kw.setdefault("cpu_clock", clock)
        wd = LoopWatchdog(clock=clock, recorder=recorded.append, **kw)
        wd._loop_thread_id = None  # no real frames in these tests
        return wd, clock, recorded

    def test_no_stall_when_ticking(self, caplog):
        wd, clock, recorded = self._wd()
        for _ in range(10):
            clock.t += 0.25
            wd._last_tick = clock.t
            wd.check()
        assert not wd._in_stall
        assert recorded == []
        assert "EVENT_LOOP_STALL" not in caplog.text

    def test_short_stall_logged_not_recorded(self, caplog):
        caplog.set_level(logging.WARNING)
        wd, clock, recorded = self._wd()
        wd._last_tick = clock.t
        clock.t += 2.0
        wd.check()
        assert wd._in_stall
        clock.t += 0.25
        wd._last_tick = clock.t  # heartbeat resumes
        wd.check()
        assert not wd._in_stall
        assert "EVENT_LOOP_STALL ended duration_ms=2000" in caplog.text
        assert recorded == []  # below 5s record threshold
        ended = [r for r in caplog.records if "ended" in r.getMessage()]
        assert ended[0].levelno == logging.WARNING

    def test_long_stall_recorded_and_error(self, caplog):
        caplog.set_level(logging.WARNING)
        wd, clock, recorded = self._wd()
        wd._last_tick = clock.t
        for step in (1.0, 5.0, 12.0):
            clock.t = 1000.0 + step
            wd.check()
        clock.t = 1000.0 + 12.25
        wd._last_tick = clock.t
        wd.check()
        assert len(recorded) == 1
        assert recorded[0]["duration_ms"] == 12000
        ended = [r for r in caplog.records if "ended" in r.getMessage()]
        assert ended[0].levelno == logging.ERROR

    def test_sub_threshold_gap_ignored(self, caplog):
        wd, clock, recorded = self._wd()
        wd._last_tick = clock.t
        clock.t += 0.9
        wd.check()
        assert not wd._in_stall


class TestRealLoop:
    """End-to-end: a real blocking call on the loop is detected with its stack."""

    @pytest.mark.asyncio
    async def test_detects_blocking_call_with_stack(self, caplog):
        caplog.set_level(logging.WARNING, logger="backend.services.loop_watchdog")
        recorded = []
        wd = LoopWatchdog(
            tick_s=0.05,
            stall_threshold_s=0.3,
            record_threshold_s=0.5,
            error_threshold_s=5.0,
            sample_at_s=(0.3,),
            recorder=recorded.append,
        )
        wd.start()
        try:
            await asyncio.sleep(0.15)  # let the heartbeat run
            _blocking_culprit_function(0.8)
            await asyncio.sleep(0.3)  # let the loop resume and the thread notice
        finally:
            await wd.stop()

        assert "_blocking_culprit_function" in caplog.text
        assert len(recorded) == 1
        assert recorded[0]["duration_ms"] >= 500
        assert "_blocking_culprit_function" in recorded[0]["culprit"]

    @pytest.mark.asyncio
    async def test_quiet_when_loop_stays_responsive(self, caplog):
        caplog.set_level(logging.WARNING, logger="backend.services.loop_watchdog")
        wd = LoopWatchdog(tick_s=0.05, stall_threshold_s=0.3)
        wd.start()
        try:
            await asyncio.to_thread(time.sleep, 0.6)  # offloaded — loop free
        finally:
            await wd.stop()
        assert "EVENT_LOOP_STALL" not in caplog.text


def test_format_stack_none():
    assert _format_stack(None) == ("", "")


class TestIdleGapSuppression:
    """CPU-throttled idle instances: the watchdog thread can wake before the
    heartbeat, see a big gap, but the loop is parked in select() — not blocked."""

    def _run_gap(self, idle: bool, caplog):
        caplog.set_level(logging.DEBUG, logger="backend.services.loop_watchdog")
        clock = FakeClock()
        recorded = []
        wd = LoopWatchdog(clock=clock, recorder=recorded.append, cpu_clock=clock)
        wd._loop_thread_id = 12345
        with patch("backend.services.loop_watchdog._is_idle_frame", return_value=idle), \
             patch("backend.services.loop_watchdog.sys._current_frames", return_value={12345: None}):
            wd._last_tick = clock.t
            for step in (1.0, 5.0, 15.0, 40.0):
                clock.t = 1000.0 + step
                wd.check()
            clock.t = 1000.0 + 40.25
            wd._last_tick = clock.t
            wd.check()
        return recorded

    def test_all_idle_samples_are_not_a_stall(self, caplog):
        recorded = self._run_gap(idle=True, caplog=caplog)
        assert recorded == []
        assert "EVENT_LOOP_STALL ended" not in caplog.text
        assert "ignored idle gap" in caplog.text

    def test_non_idle_samples_are_a_stall(self, caplog):
        recorded = self._run_gap(idle=False, caplog=caplog)
        assert len(recorded) == 1
        assert "EVENT_LOOP_STALL ended" in caplog.text


class TestThrottledProcessFreeze:
    """Cloud Run CPU throttling freezes every thread: the watchdog oversleeps and the
    process burns ~no CPU. That gap is not a loop stall (2026-10-08 false alert:
    30s "stall" in asyncio get_debug with no request in flight)."""

    def _wd(self):
        clock = FakeClock()
        cpu = FakeClock()  # advances only when the test says the process ran
        recorded = []
        wd = LoopWatchdog(clock=clock, cpu_clock=cpu, recorder=recorded.append)
        wd._loop_thread_id = 12345
        return wd, clock, cpu, recorded

    def _check(self, wd):
        with patch("backend.services.loop_watchdog._is_idle_frame", return_value=False), \
             patch("backend.services.loop_watchdog.sys._current_frames", return_value={12345: None}):
            wd.check()

    def test_frozen_gap_is_not_reported(self, caplog):
        caplog.set_level(logging.DEBUG, logger="backend.services.loop_watchdog")
        wd, clock, cpu, recorded = self._wd()
        wd._last_tick = clock.t
        self._check(wd)                 # baseline check
        clock.t += 30.0                 # whole process frozen 30s, no CPU used
        self._check(wd)
        clock.t += 0.25
        cpu.t += 0.01
        wd._last_tick = clock.t         # thawed: heartbeat resumes
        self._check(wd)
        assert recorded == []
        assert "EVENT_LOOP_STALL" not in caplog.text
        assert "discounted 30000ms of process freeze" in caplog.text

    def test_short_partial_throttle_freeze_is_not_reported(self, caplog):
        """Partial throttling: the watchdog gets a slice every ~1.5s, burning ~no CPU."""
        caplog.set_level(logging.WARNING)
        wd, clock, cpu, recorded = self._wd()
        wd._last_tick = clock.t
        self._check(wd)
        for _ in range(2):              # 2 x 1.5s sparse wake-ups, loop never ticks
            clock.t += 1.5
            cpu.t += 0.002
            self._check(wd)
        clock.t += 0.25
        cpu.t += 0.01
        wd._last_tick = clock.t
        self._check(wd)
        assert recorded == []
        assert "EVENT_LOOP_STALL" not in caplog.text

    def test_blocking_io_stall_still_reported(self, caplog):
        """Blocking I/O on the loop: the watchdog keeps waking on time (GIL released)."""
        caplog.set_level(logging.WARNING)
        wd, clock, cpu, recorded = self._wd()
        wd._last_tick = clock.t
        self._check(wd)
        for _ in range(int(12 / 0.25)):  # 12s stall, watchdog wakes every tick
            clock.t += 0.25
            cpu.t += 0.001
            self._check(wd)
        clock.t += 0.25
        wd._last_tick = clock.t
        self._check(wd)
        assert len(recorded) == 1
        assert "EVENT_LOOP_STALL ended duration_ms=12000" in caplog.text

    def test_cpu_bound_gil_hog_still_reported(self, caplog):
        """GIL-holding CPU work also starves the watchdog, but the process burns CPU."""
        caplog.set_level(logging.WARNING)
        wd, clock, cpu, recorded = self._wd()
        wd._last_tick = clock.t
        self._check(wd)
        clock.t += 20.0
        cpu.t += 19.5
        self._check(wd)
        clock.t += 0.25
        wd._last_tick = clock.t
        self._check(wd)
        assert len(recorded) == 1
        assert "EVENT_LOOP_STALL ended duration_ms=20000" in caplog.text

    def test_freeze_inside_real_stall_is_subtracted(self, caplog):
        """8s of real blocking then a 30s freeze reports ~8s, not 38s."""
        caplog.set_level(logging.WARNING)
        wd, clock, cpu, recorded = self._wd()
        wd._last_tick = clock.t
        self._check(wd)
        for _ in range(int(8 / 0.25)):
            clock.t += 0.25
            cpu.t += 0.001
            self._check(wd)
        clock.t += 30.0                 # frozen
        self._check(wd)
        clock.t += 0.25
        wd._last_tick = clock.t
        self._check(wd)
        assert "EVENT_LOOP_STALL ended duration_ms=8000" in caplog.text
        assert recorded[0]["duration_ms"] == 8000


class TestSampleAfterThaw:
    """A freeze that skips every sample point must not leave a real post-thaw
    block without a stack (2026-10-12: 10-40s ERROR stalls with culprit='')."""

    def _wd(self):
        clock, cpu = FakeClock(), FakeClock()
        recorded = []
        wd = LoopWatchdog(clock=clock, cpu_clock=cpu, recorder=recorded.append)
        wd._loop_thread_id = 12345
        return wd, clock, cpu, recorded

    def _check(self, wd):
        with patch("backend.services.loop_watchdog._is_idle_frame", return_value=False), \
             patch("backend.services.loop_watchdog._format_stack",
                   return_value=("stack", "backend/x.py:1 in blocker")), \
             patch("backend.services.loop_watchdog.sys._current_frames", return_value={12345: None}):
            wd.check()

    def test_block_after_freeze_gets_a_culprit(self, caplog):
        caplog.set_level(logging.WARNING)
        wd, clock, cpu, recorded = self._wd()
        wd._last_tick = clock.t
        self._check(wd)
        clock.t += 20.0                  # frozen past 1/5/15s sample points
        self._check(wd)
        for _ in range(int(12 / 0.25)):  # then really blocked for 12s
            clock.t += 0.25
            cpu.t += 0.001
            self._check(wd)
        clock.t += 0.25
        wd._last_tick = clock.t
        self._check(wd)
        assert "EVENT_LOOP_STALL in progress" in caplog.text
        assert recorded[0]["culprit"] == "backend/x.py:1 in blocker"
        assert "duration_ms=12000" in caplog.text

    def test_post_thaw_sample_level_ignores_frozen_time(self, caplog):
        """20s freeze + 2s real block with a request waiting: WARNING, not ERROR."""
        from backend.services.loop_watchdog import RequestTracker

        caplog.set_level(logging.WARNING)
        wd, clock, cpu, recorded = self._wd()
        wd.request_tracker = RequestTracker()
        wd.request_tracker.begin()
        wd._last_tick = clock.t
        self._check(wd)
        clock.t += 20.0
        self._check(wd)
        for _ in range(8):
            clock.t += 0.25
            cpu.t += 0.001
            self._check(wd)
        progress = [r for r in caplog.records if "in progress" in r.getMessage()]
        assert progress and all(r.levelno == logging.WARNING for r in progress)

    def test_pure_freeze_still_silent(self, caplog):
        caplog.set_level(logging.WARNING)
        wd, clock, cpu, recorded = self._wd()
        wd._last_tick = clock.t
        self._check(wd)
        clock.t += 20.0
        self._check(wd)
        clock.t += 0.25
        cpu.t += 0.01
        wd._last_tick = clock.t          # loop resumes on the first on-time check
        self._check(wd)
        assert "EVENT_LOOP_STALL" not in caplog.text
        assert recorded == []


def test_is_idle_frame_detects_parked_selector():
    """A thread blocked in selectors' select() is classified idle; a busy one isn't."""
    import selectors
    import socket
    import sys
    import threading

    from backend.services.loop_watchdog import _is_idle_frame

    a, b = socket.socketpair()
    sel = selectors.DefaultSelector()
    sel.register(a, selectors.EVENT_READ)
    started = threading.Event()

    def park():
        started.set()
        sel.select(timeout=2)

    t = threading.Thread(target=park)
    t.start()
    started.wait()
    time.sleep(0.2)
    try:
        assert _is_idle_frame(sys._current_frames()[t.ident]) is True
        assert _is_idle_frame(sys._current_frames()[threading.get_ident()]) is False
    finally:
        b.send(b"x")
        t.join()
        sel.close()
        a.close()
        b.close()


class TestRequestAwareSeverity:
    """Only stalls a request waited on reach ERROR (the alerting level).

    2026-10-12/13 flapping alert: autoscaled instances served one request, then sat
    idle and CPU-throttled during the 10-min SpaCy warmup; their 10-40s "stalls"
    had no request on them yet paged twice."""

    def _wd(self):
        from backend.services.loop_watchdog import RequestTracker

        clock = FakeClock()
        tracker = RequestTracker()
        recorded = []
        wd = LoopWatchdog(clock=clock, cpu_clock=clock, recorder=recorded.append,
                          request_tracker=tracker)
        wd._loop_thread_id = None
        return wd, clock, tracker, recorded

    def _stall(self, wd, clock, seconds, during=None, after_resume=None):
        wd._last_tick = clock.t
        start = clock.t
        t = 0.0
        while t < seconds:
            t = min(seconds, t + 0.25)
            clock.t = start + t
            wd.check()
            if during and t >= seconds / 2:
                during()
                during = None
        clock.t += 0.25
        wd._last_tick = clock.t          # heartbeat resumes
        wd.check()
        if after_resume:
            after_resume()
        for _ in range(3):               # grace window checks
            clock.t += 0.25
            wd._last_tick = clock.t
            wd.check()

    def _ended(self, caplog):
        return [r for r in caplog.records if "EVENT_LOOP_STALL ended" in r.getMessage()]

    def test_idle_instance_long_stall_is_warning(self, caplog):
        caplog.set_level(logging.WARNING)
        wd, clock, tracker, recorded = self._wd()
        self._stall(wd, clock, 21.0)
        ended = self._ended(caplog)
        assert len(ended) == 1
        assert ended[0].levelno == logging.WARNING
        assert "requests_affected=0" in ended[0].getMessage()
        assert recorded[0]["requests_affected"] == 0  # still recorded for forensics

    def test_idle_in_progress_samples_are_warning(self, caplog):
        caplog.set_level(logging.WARNING)
        wd, clock, tracker, recorded = self._wd()
        wd._loop_thread_id = 12345
        with patch("backend.services.loop_watchdog._is_idle_frame", return_value=False), \
             patch("backend.services.loop_watchdog._format_stack",
                   side_effect=lambda f, n=iter(range(99)): (f"stack{next(n)}", "x.py:1")), \
             patch("backend.services.loop_watchdog.sys._current_frames", return_value={12345: None}):
            self._stall(wd, clock, 16.0)
        progress = [r for r in caplog.records if "in progress" in r.getMessage()]
        assert len(progress) == 3  # 1s, 5s, 15s samples
        assert all(r.levelno == logging.WARNING for r in progress)

    def test_in_flight_in_progress_samples_stay_warning(self, caplog):
        """A freeze used to log ERROR twice (the 15s "in progress" sample AND the
        "ended" line), so the error monitor raised two alert patterns per freeze.
        "in progress" is now always WARNING; "ended" alone carries the ERROR."""
        caplog.set_level(logging.WARNING)
        wd, clock, tracker, recorded = self._wd()
        wd._loop_thread_id = 12345
        tracker.begin()
        with patch("backend.services.loop_watchdog._is_idle_frame", return_value=False), \
             patch("backend.services.loop_watchdog._format_stack",
                   side_effect=lambda f, n=iter(range(99)): (f"stack{next(n)}", "x.py:1")), \
             patch("backend.services.loop_watchdog.sys._current_frames", return_value={12345: None}):
            self._stall(wd, clock, 16.0)
        progress = [r for r in caplog.records if "in progress" in r.getMessage()]
        assert [r.levelno for r in progress] == [logging.WARNING, logging.WARNING, logging.WARNING]
        ended = self._ended(caplog)
        assert len(ended) == 1 and ended[0].levelno == logging.ERROR
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(errors) == 1  # one freeze -> one ERROR line -> one alert pattern

    def test_in_flight_request_makes_it_error(self, caplog):
        caplog.set_level(logging.WARNING)
        wd, clock, tracker, recorded = self._wd()
        tracker.begin()
        self._stall(wd, clock, 12.0)
        ended = self._ended(caplog)
        assert ended[0].levelno == logging.ERROR
        assert "requests_affected=1" in ended[0].getMessage()

    def test_request_queued_during_stall_accepted_after_resume_is_error(self, caplog):
        caplog.set_level(logging.WARNING)
        wd, clock, tracker, recorded = self._wd()
        self._stall(wd, clock, 12.0, after_resume=tracker.begin)
        ended = self._ended(caplog)
        assert len(ended) == 1
        assert ended[0].levelno == logging.ERROR

    def _unsampled_stall(self, freeze_s, request_in_flight=False, request_arrives=True, sample_at_s=None):
        """A freeze of ``freeze_s`` (process idle, no CPU) skips the sample points,
        then the watchdog is starved until the loop has already resumed, so no
        sample is ever taken. Returns the ended records' levels."""
        from backend.services.loop_watchdog import RequestTracker

        clock, cpu = FakeClock(), FakeClock()
        tracker = RequestTracker()
        kw = {} if sample_at_s is None else {"sample_at_s": sample_at_s}
        wd = LoopWatchdog(clock=clock, cpu_clock=cpu, request_tracker=tracker, **kw)
        wd._loop_thread_id = None
        if request_in_flight:
            tracker.begin()
        wd._last_tick = clock.t
        wd.check()                       # baseline
        clock.t += 3.0
        if not freeze_s:
            cpu.t += 2.9                 # process ran: not a freeze
        wd.check()                       # stall noticed (after a freeze, samples skipped)
        clock.t += 12.0
        cpu.t += 11.9                    # busy, not frozen
        if request_arrives:
            tracker.begin()              # arrived while the instance was stuck
        wd._last_tick = clock.t          # loop already resumed when we wake
        wd.check()
        for _ in range(3):
            clock.t += 0.25
            cpu.t += 0.25
            wd._last_tick = clock.t
            wd.check()
        return wd

    def test_unsampled_frozen_stall_with_only_late_arrivals_is_warning(self, caplog):
        """2026-10-23 03:15: a 20.7s stall on an idle, CPU-throttled instance took
        no sample (empty culprit); its one "affected" request arrived 0.3s before
        the loop resumed. Nothing was waiting when it began, so it must not page."""
        caplog.set_level(logging.WARNING)
        self._unsampled_stall(freeze_s=3.0)
        ended = self._ended(caplog)
        assert len(ended) == 1
        assert "culprit= " in ended[0].getMessage()
        assert "requests_affected=1" in ended[0].getMessage()
        assert ended[0].levelno == logging.WARNING

    def test_unsampled_frozen_stall_with_request_in_flight_at_start_is_error(self, caplog):
        caplog.set_level(logging.WARNING)
        self._unsampled_stall(freeze_s=3.0, request_in_flight=True, request_arrives=False)
        assert self._ended(caplog)[0].levelno == logging.ERROR

    def test_unsampled_stall_without_observed_freeze_is_error(self, caplog):
        """No sample but no freeze seen either: no positive evidence it was harmless."""
        caplog.set_level(logging.WARNING)
        self._unsampled_stall(freeze_s=0, sample_at_s=())
        ended = self._ended(caplog)
        assert "culprit= " in ended[0].getMessage()
        assert ended[0].levelno == logging.ERROR

    def test_sampled_stall_with_late_arrival_is_still_error(self, caplog):
        """A culprit was caught blocking: a request queued behind it is real impact."""
        caplog.set_level(logging.WARNING)
        wd, clock, tracker, recorded = self._wd()
        wd._loop_thread_id = 12345
        with patch("backend.services.loop_watchdog._is_idle_frame", return_value=False), \
             patch("backend.services.loop_watchdog._format_stack",
                   return_value=("stack", "backend/x.py:1 in f")), \
             patch("backend.services.loop_watchdog.sys._current_frames", return_value={12345: None}):
            self._stall(wd, clock, 12.0, during=tracker.begin)
        assert self._ended(caplog)[0].levelno == logging.ERROR

    def test_request_finished_mid_stall_still_counts(self, caplog):
        caplog.set_level(logging.WARNING)
        wd, clock, tracker, recorded = self._wd()
        tracker.begin()
        self._stall(wd, clock, 12.0, during=tracker.end)
        assert self._ended(caplog)[0].levelno == logging.ERROR

    def test_short_idle_stall_logged_once_after_grace(self, caplog):
        caplog.set_level(logging.WARNING)
        wd, clock, tracker, recorded = self._wd()
        self._stall(wd, clock, 2.0)
        ended = self._ended(caplog)
        assert len(ended) == 1
        assert "duration_ms=2000" in ended[0].getMessage()
        assert not wd._in_stall

    def test_without_tracker_severity_unchanged(self, caplog):
        caplog.set_level(logging.WARNING)
        clock = FakeClock()
        wd = LoopWatchdog(clock=clock, cpu_clock=clock)
        wd._loop_thread_id = None
        self._stall(wd, clock, 12.0)
        ended = self._ended(caplog)
        assert ended[0].levelno == logging.ERROR
        assert "requests_affected=unknown" in ended[0].getMessage()


class TestRequestTrackingMiddleware:
    @pytest.mark.asyncio
    async def test_counts_http_requests_including_errors(self):
        from backend.services.loop_watchdog import RequestTracker, RequestTrackingMiddleware

        tracker = RequestTracker()
        seen = []

        async def app(scope, receive, send):
            seen.append(tracker.in_flight)
            if scope.get("path") == "/boom":
                raise RuntimeError("boom")

        mw = RequestTrackingMiddleware(app, tracker=tracker)
        await mw({"type": "http", "path": "/"}, None, None)
        with pytest.raises(RuntimeError):
            await mw({"type": "http", "path": "/boom"}, None, None)
        await mw({"type": "lifespan"}, None, None)
        assert seen == [1, 1, 0]
        assert tracker.in_flight == 0
        assert tracker.started == 2

    def test_main_app_installs_middleware(self):
        from backend.main import app
        from backend.services.loop_watchdog import RequestTrackingMiddleware

        classes = [m.cls for m in app.user_middleware]
        # add_middleware inserts at index 0 → outermost is first.
        assert classes[0] is RequestTrackingMiddleware


class TestRecorderFailureIsBestEffort:
    """A failed stall-record write is best-effort: WARNING, never ERROR (ERROR pages #alerts)."""

    def test_recorder_failure_logs_warning(self, caplog):
        caplog.set_level(logging.DEBUG)
        clock = FakeClock()

        def failing_recorder(_record):
            raise RuntimeError("firestore unavailable")

        wd = LoopWatchdog(clock=clock, cpu_clock=clock, recorder=failing_recorder)
        wd._loop_thread_id = None
        wd._last_tick = clock.t
        for step in (1.0, 5.0, 12.0):
            clock.t = 1000.0 + step
            wd.check()
        clock.t = 1000.0 + 12.25
        wd._last_tick = clock.t
        wd.check()  # must not raise

        failed = [r for r in caplog.records if "Failed to record loop stall" in r.getMessage()]
        assert len(failed) == 1
        assert failed[0].levelno == logging.WARNING
        assert failed[0].exc_info is not None
