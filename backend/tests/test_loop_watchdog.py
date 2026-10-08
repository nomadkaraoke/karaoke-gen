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
