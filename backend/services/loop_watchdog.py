"""Event-loop stall watchdog.

The API runs ONE uvicorn process with ONE asyncio event loop per Cloud Run
instance. Any synchronous work executed on that loop (sync Firestore/GCS/HTTP
calls, PIL/ffmpeg, a first ``import matplotlib.pyplot``…) freezes *every*
request on the instance — including ``/api/health`` — which users see as the
"Reconnecting" pill / "temporarily unavailable" banner. (Analysis 2026-10-03:
docs/archive/2026-10-03-backend-loop-freezes-telemetry-plan.md.)

This module makes such freezes self-diagnosing:

* an asyncio heartbeat task stamps ``last_tick`` every ``tick_s``;
* a daemon thread notices when the stamp goes stale (> ``stall_threshold_s``)
  and captures the *event-loop thread's* Python stack via
  ``sys._current_frames()`` — i.e. the exact line that is blocking — resampling
  at ``sample_at_s`` so a long freeze shows where it actually spent its time;
* when the loop resumes it logs ``EVENT_LOOP_STALL ended duration_ms=…``
  (WARNING, or ERROR at ``error_threshold_s`` — the alerting log metric keys on
  that), and for stalls ≥ ``record_threshold_s`` hands a summary to
  ``recorder`` (Firestore ``client_events`` type ``server_loop_stall`` in prod),
  so server-side cause and user-visible impact live in one collection.

The thread only reads frames and a float; it never touches the loop, so it is
safe to run alongside it. Recording happens on the watchdog thread (sync
Firestore is fine there).
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Frames from these path fragments are "ours" — the innermost one is reported
# as the culprit (the stdlib/library frame below it is usually just the I/O).
_OWN_CODE_MARKERS = ("/backend/", "/karaoke_gen/")
_MAX_STACK_FRAMES = 30
_MAX_STACK_CHARS = 8000

Recorder = Callable[[dict], None]

# Innermost frames that mean "the loop is idle, waiting for I/O" — not blocked.
_IDLE_FUNCS = {"select", "poll", "epoll", "kqueue", "control", "_run_once"}
_IDLE_FILES = ("selectors.py", "base_events.py")


def _is_idle_frame(frame) -> bool:
    """True when the loop thread is parked in the selector (idle), not running code."""
    if frame is None:
        return False
    code = frame.f_code
    return code.co_name in _IDLE_FUNCS and code.co_filename.endswith(_IDLE_FILES)


def _format_stack(frame) -> tuple[str, str]:
    """Return (stack_text, culprit) for a frame; culprit = innermost own-code frame."""
    if frame is None:
        return "", ""
    summary = traceback.extract_stack(frame)[-_MAX_STACK_FRAMES:]
    culprit = ""
    for fs in reversed(summary):
        if any(m in fs.filename for m in _OWN_CODE_MARKERS) and "loop_watchdog" not in fs.filename:
            short = fs.filename.split("/app/")[-1]
            culprit = f"{short}:{fs.lineno} in {fs.name}"
            break
    if not culprit and summary:
        fs = summary[-1]
        culprit = f"{fs.filename}:{fs.lineno} in {fs.name}"
    text = "".join(traceback.format_list(summary))[-_MAX_STACK_CHARS:]
    return text, culprit


class LoopWatchdog:
    def __init__(
        self,
        *,
        tick_s: float = 0.25,
        stall_threshold_s: float = 1.0,
        error_threshold_s: float = 10.0,
        record_threshold_s: float = 5.0,
        sample_at_s: tuple[float, ...] = (1.0, 5.0, 15.0),
        recorder: Optional[Recorder] = None,
        clock: Callable[[], float] = time.monotonic,
        cpu_clock: Callable[[], float] = time.process_time,
        frozen_gap_s: float = 0.75,
        frozen_cpu_ratio: float = 0.25,
    ) -> None:
        self.tick_s = tick_s
        self.stall_threshold_s = stall_threshold_s
        self.error_threshold_s = error_threshold_s
        self.record_threshold_s = record_threshold_s
        self.sample_at_s = tuple(sorted(sample_at_s))
        self.recorder = recorder
        self._clock = clock
        self._cpu_clock = cpu_clock
        # Cloud Run --cpu-throttling freezes the WHOLE process between requests —
        # sometimes mid-callback, where the "loop parked in select()" check can't
        # see it (2026-10-08: a 30s "stall" in asyncio's get_debug on an instance
        # with no requests in flight). Signature: this watchdog thread itself
        # oversleeps by >= frozen_gap_s while the process burns almost no CPU
        # (a blocking I/O call lets this thread wake on time; GIL-hogging CPU
        # work burns CPU). That frozen time is discounted from the stall.
        # 0.75s = 3 missed ticks: measured wake gaps stay <= 0.27s under sync I/O,
        # pure-Python CPU and C-extension CPU on the loop, while partial throttling
        # gives short 1-2.5s freezes (126 of 128 asyncio-internal "stalls" on
        # 2026-10-08 had no request waiting on them).
        self.frozen_gap_s = frozen_gap_s
        self.frozen_cpu_ratio = frozen_cpu_ratio
        self._last_check: Optional[float] = None
        self._last_cpu = 0.0
        self._stall_frozen_s = 0.0

        self._last_tick = clock()
        self._loop_thread_id: Optional[int] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

        # Current-stall state (only touched by the watchdog thread).
        self._in_stall = False
        self._stall_started_tick = 0.0
        self._samples: list[dict[str, Any]] = []
        self._next_sample_idx = 0

    # ----- lifecycle -------------------------------------------------------
    def start(self) -> None:
        """Start heartbeat + watchdog thread. Must be called from inside the loop."""
        if self._thread is not None:
            return
        self._loop_thread_id = threading.get_ident()
        self._last_tick = self._clock()
        self._heartbeat_task = asyncio.get_running_loop().create_task(self._heartbeat())
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="loop-watchdog", daemon=True)
        self._thread.start()
        logger.info(
            "Event-loop watchdog started (stall>=%.1fs logged, >=%.1fs recorded, >=%.1fs error)",
            self.stall_threshold_s, self.record_threshold_s, self.error_threshold_s,
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except (asyncio.CancelledError, Exception):
                pass
            self._heartbeat_task = None
        self._thread = None

    async def _heartbeat(self) -> None:
        while True:
            self._last_tick = self._clock()
            await asyncio.sleep(self.tick_s)

    def _run(self) -> None:
        while not self._stop.wait(self.tick_s):
            try:
                self.check()
            except Exception:  # pragma: no cover — must never kill the thread
                logger.exception("loop watchdog check failed")

    # ----- detection (called from the watchdog thread; unit-testable) ------
    def _sample(self, age: float) -> None:
        frame = sys._current_frames().get(self._loop_thread_id) if self._loop_thread_id else None
        if _is_idle_frame(frame):
            # Loop is parked in select(): not blocked. This is what a CPU-throttled
            # idle Cloud Run instance looks like when CPU returns (the watchdog
            # thread can wake before the heartbeat does). Record it so _finish
            # can drop an all-idle "stall" as a false positive.
            self._samples.append({"at_s": round(age, 1), "culprit": "", "stack": "", "idle": True})
            return
        stack, culprit = _format_stack(frame)
        if self._samples and self._samples[-1]["stack"] == stack:
            return  # identical to the previous sample — nothing new to learn
        self._samples.append({"at_s": round(age, 1), "culprit": culprit, "stack": stack, "idle": False})
        level = logging.ERROR if age >= self.error_threshold_s else logging.WARNING
        logger.log(
            level,
            "EVENT_LOOP_STALL in progress age_ms=%d culprit=%s\n%s",
            int(age * 1000), culprit, stack,
            extra={"loop_stall_age_ms": int(age * 1000), "loop_stall_culprit": culprit},
        )

    def _frozen_gap(self, now: float) -> float:
        """Seconds since the previous check during which the whole process was frozen."""
        cpu = self._cpu_clock()
        prev_check, prev_cpu = self._last_check, self._last_cpu
        self._last_check, self._last_cpu = now, cpu
        if prev_check is None:
            return 0.0
        gap = now - prev_check
        if gap >= self.frozen_gap_s and (cpu - prev_cpu) < gap * self.frozen_cpu_ratio:
            return gap
        return 0.0

    def check(self, now: Optional[float] = None) -> None:
        now = self._clock() if now is None else now
        frozen = self._frozen_gap(now)
        last = self._last_tick
        age = now - last

        if not self._in_stall:
            if age >= self.stall_threshold_s:
                self._in_stall = True
                self._stall_started_tick = last
                self._samples = []
                self._next_sample_idx = 0
                self._stall_frozen_s = 0.0
            else:
                return
        # Only the part of the frozen gap after the last heartbeat belongs to this stall.
        self._stall_frozen_s += min(frozen, age)

        # In a stall: has the heartbeat resumed?
        if last > self._stall_started_tick:
            # The heartbeat that ended the stall fired one tick_s after the
            # previous one would have, so subtract the nominal sleep.
            duration = max(0.0, last - self._stall_started_tick - self.tick_s)
            self._finish(duration)
            return

        if frozen:
            # Just thawed: the loop's stack shows where the freeze caught it, not
            # what blocked it. Skip the samples due during the freeze.
            while (
                self._next_sample_idx < len(self.sample_at_s)
                and age >= self.sample_at_s[self._next_sample_idx]
            ):
                self._next_sample_idx += 1
            return

        while (
            self._next_sample_idx < len(self.sample_at_s)
            and age >= self.sample_at_s[self._next_sample_idx]
        ):
            self._next_sample_idx += 1
            self._sample(age)

    def _finish(self, duration: float) -> None:
        samples = self._samples
        frozen_s = self._stall_frozen_s
        self._in_stall = False
        self._samples = []
        self._stall_frozen_s = 0.0
        if frozen_s:
            logger.debug(
                "loop watchdog: discounted %dms of process freeze (CPU throttling) from a %dms gap",
                int(frozen_s * 1000), int(duration * 1000),
            )
            duration = max(0.0, duration - frozen_s)
        if duration < self.stall_threshold_s:
            return
        if samples and all(s.get("idle") for s in samples):
            # Every sample saw the loop idle in select() — a throttled/idle gap
            # (no CPU allocated between requests), not a blocking call.
            logger.debug("loop watchdog: ignored idle gap of %dms", int(duration * 1000))
            return
        samples = [s for s in samples if not s.get("idle")]
        culprits = [s["culprit"] for s in samples if s["culprit"]]
        # The longest-lived sample is the most representative of the freeze.
        main_culprit = culprits[-1] if culprits else ""
        level = logging.ERROR if duration >= self.error_threshold_s else logging.WARNING
        logger.log(
            level,
            "EVENT_LOOP_STALL ended duration_ms=%d culprit=%s",
            int(duration * 1000), main_culprit,
            extra={
                "loop_stall_duration_ms": int(duration * 1000),
                "loop_stall_culprit": main_culprit,
                "loop_stall_culprits": culprits,
            },
        )
        if self.recorder is not None and duration >= self.record_threshold_s:
            try:
                self.recorder({
                    "duration_ms": int(duration * 1000),
                    "culprit": main_culprit,
                    "culprits": culprits,
                    "stack": samples[-1]["stack"] if samples else "",
                })
            except Exception:
                logger.exception("Failed to record loop stall (non-fatal)")


# ----- production recorder ---------------------------------------------------
_instance_id: Optional[str] = None


def _cloud_run_instance_id() -> str:
    """Best-effort Cloud Run instance id from the metadata server (cached)."""
    global _instance_id
    if _instance_id is not None:
        return _instance_id
    _instance_id = ""
    try:
        import requests

        resp = requests.get(
            "http://metadata.google.internal/computeMetadata/v1/instance/id",
            headers={"Metadata-Flavor": "Google"},
            timeout=1,
        )
        if resp.ok:
            _instance_id = resp.text.strip()
    except Exception:
        pass
    return _instance_id


def firestore_stall_recorder(stall: dict) -> None:
    """Persist a stall as a ``client_events`` doc (type ``server_loop_stall``)."""
    from google.cloud import firestore  # type: ignore[import]

    from backend.version import VERSION

    doc = {
        "type": "server_loop_stall",
        "source": "server",
        "created_at": datetime.now(timezone.utc),
        "release": VERSION,
        "revision": os.getenv("K_REVISION", ""),
        "instance_id": _cloud_run_instance_id(),
        "detail": {
            "duration_ms": stall["duration_ms"],
            "culprit": stall["culprit"][:300],
        },
        "culprits": [c[:300] for c in stall.get("culprits", [])][:5],
        "stack": stall.get("stack", "")[:_MAX_STACK_CHARS],
    }
    firestore.Client(project="nomadkaraoke").collection("client_events").add(doc)
