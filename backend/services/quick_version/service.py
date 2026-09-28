"""Quick version for kjbox make-it jobs — orchestration inside the audio worker.

The kjbox singer "make it" flow creates a normal gen job, which takes ~30 min
(ensemble separation, transcription, lyrics review, render). For those jobs we
*also* produce a rough scrolling-lyrics video within a few minutes of the audio
landing, so the singer can choose to sing it right away:

1. ``separate_quick`` — one fast single-model pass on the (already warm) L4,
   run by the audio worker BEFORE the ensemble.
2. ``QuickVersionRender`` — a background thread renders the video (CPU: PIL +
   ffmpeg) while the GPU runs the ensemble, then uploads it and records
   ``file_urls.quick.video_mp4`` + ``state_data.quick_version``.

kjbox's GenPoller already reads the job doc and downloads ``file_urls`` entries
through ``/api/jobs/{id}/download/{category}/{key}``, so nothing else is needed
to deliver it. Every failure here is non-fatal to the full job.
"""

from __future__ import annotations

import gc
import glob
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

from backend.services.job_notification_service import is_kjbox_job
from backend.services.quick_version.renderer import render_quick_video

logger = logging.getLogger(__name__)

STATE_KEY = "quick_version"
FILE_CATEGORY = "quick"
FILE_KEY = "video_mp4"

# fastgen's model: a light MDX-Net ONNX (~60 MB), ~10x less compute than a
# roformer. The baked roformer (instrumental_clean preset's first model) took
# 14 s to load + 124 s to separate a 3:15 song on the L4 (prod, 2026-09-28).
# If the ONNX isn't in /models yet, audio-separator downloads it on load
# (baked by download_models.py on the next GPU base rebuild). Override with
# QUICK_VERSION_MODEL.
DEFAULT_QUICK_MODEL = "UVR-MDX-NET-Inst_HQ_4.onnx"
# Always baked into the GPU image — used if the default can't be loaded
# (e.g. the model download fails).
FALLBACK_QUICK_MODEL = "mel_band_roformer_instrumental_fv7z_gabox.ckpt"
# Roformer (MDXC) overlap: the library default is 8; 2 is ~4x less compute and
# plenty for a draft.
QUICK_MDXC_OVERLAP = 2

STATUS_SEPARATING = "separating"
STATUS_RENDERING = "rendering"
STATUS_READY = "ready"
STATUS_FAILED = "failed"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def quick_version_enabled() -> bool:
    return os.environ.get("KJBOX_QUICK_VERSION_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


def should_build_quick_version(job: Any) -> bool:
    """Only for kjbox jobs, when enabled, and not already delivered (job retries)."""
    if not quick_version_enabled():
        return False
    if not is_kjbox_job(getattr(job, "request_metadata", None)):
        return False
    existing = (getattr(job, "state_data", None) or {}).get(STATE_KEY) or {}
    return existing.get("status") != STATUS_READY


def _classify_output(path: str) -> Optional[str]:
    """Map a separator output filename to 'instrumental' / 'vocals' / None."""
    base = os.path.basename(path).lower()
    if "quick_instrumental" in base:
        return "instrumental"
    if "quick_vocals" in base:
        return "vocals"
    tag_start = base.rfind("_(")
    tag_end = base.find(")", tag_start) if tag_start >= 0 else -1
    tag = base[tag_start + 2:tag_end] if tag_start >= 0 and tag_end > tag_start else ""
    if tag in ("instrumental", "no vocal", "no_vocal", "no vocals"):
        return "instrumental"
    if tag in ("vocals", "vocal"):
        return "vocals"
    return None


def separate_quick(
    audio_path: str,
    workdir: str,
    model_dir: Optional[str],
    model: Optional[str] = None,
    separator_factory: Optional[Callable[..., Any]] = None,
) -> Tuple[str, Optional[str]]:
    """Single-model separation → (instrumental_path, vocals_path|None)."""
    model = model or os.environ.get("QUICK_VERSION_MODEL") or DEFAULT_QUICK_MODEL
    if separator_factory is None:
        from audio_separator.separator import Separator as separator_factory  # heavy; lazy

    out_dir = os.path.join(workdir, "quick_stems")
    os.makedirs(out_dir, exist_ok=True)
    kwargs: Dict[str, Any] = {
        "output_dir": out_dir,
        "output_format": "FLAC",
        "mdxc_params": {"segment_size": 256, "override_model_segment_size": False,
                        "batch_size": 1, "overlap": QUICK_MDXC_OVERLAP, "pitch_shift": 0},
    }
    if model_dir:
        kwargs["model_file_dir"] = model_dir
    candidates = [model] + ([FALLBACK_QUICK_MODEL] if model != FALLBACK_QUICK_MODEL else [])
    sep = None
    for i, name in enumerate(candidates):
        try:
            sep = separator_factory(**kwargs)
            sep.load_model(model_filename=name)
            break
        except Exception as exc:
            if i == len(candidates) - 1:
                raise
            logger.warning(f"Quick model {name} failed to load ({exc}); falling back to {candidates[i + 1]}")
    outputs = sep.separate(audio_path, {
        "Instrumental": "quick_instrumental", "Vocals": "quick_vocals",
        "instrumental": "quick_instrumental", "vocals": "quick_vocals",
    }) or []

    produced = [o if os.path.isabs(o) else os.path.join(out_dir, o) for o in outputs]
    produced += glob.glob(os.path.join(out_dir, "*"))
    inst = vocals = None
    for p in produced:
        if not os.path.exists(p):
            continue
        kind = _classify_output(p)
        if kind == "instrumental" and inst is None:
            inst = p
        elif kind == "vocals" and vocals is None:
            vocals = p

    # Free GPU memory before the ensemble loads its models.
    del sep
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # pragma: no cover - torch optional in tests
        pass

    if inst is None:
        raise RuntimeError(f"Quick separation produced no instrumental (outputs: {outputs!r})")
    return inst, vocals


class QuickVersionRender:
    """Renders + uploads the quick video on a background thread."""

    def __init__(self, job_id: str, artist: str, title: str, instrumental: str, vocals: Optional[str],
                 workdir: str, job_manager: Any, storage: Any, job_log: Any = None,
                 started_monotonic: Optional[float] = None, separation_seconds: Optional[float] = None):
        self.job_id = job_id
        self.artist = artist or "Unknown"
        self.title = title or "Unknown"
        self.instrumental = instrumental
        self.vocals = vocals
        self.workdir = workdir
        self.job_manager = job_manager
        self.storage = storage
        self.job_log = job_log or logger
        self.started = started_monotonic or time.monotonic()
        self.separation_seconds = separation_seconds
        self.ok: Optional[bool] = None
        self._thread = threading.Thread(target=self._run, name=f"quick-version-{job_id}", daemon=True)

    def start(self) -> "QuickVersionRender":
        self._thread.start()
        return self

    def join(self, timeout: float) -> bool:
        """Wait for the render; True if it finished (ok or failed) in time."""
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def _run(self) -> None:
        try:
            set_quick_state(self.job_manager, self.job_id, STATUS_RENDERING)
            render_dir = os.path.join(self.workdir, "quick_render")
            os.makedirs(render_dir, exist_ok=True)
            out_path = os.path.join(render_dir, "quick.mp4")
            t0 = time.monotonic()
            result = render_quick_video(
                instrumental_path=self.instrumental, vocals_path=self.vocals,
                artist=self.artist, title=self.title, out_path=out_path, workdir=render_dir,
            )
            render_seconds = time.monotonic() - t0
            gcs_path = f"jobs/{self.job_id}/quick/quick.mp4"
            self.storage.upload_file(out_path, gcs_path)
            self.job_manager.update_file_url(self.job_id, FILE_CATEGORY, FILE_KEY, gcs_path)
            set_quick_state(
                self.job_manager, self.job_id, STATUS_READY,
                lyrics_tier=result.lyrics_tier,
                line_count=result.line_count,
                duration_seconds=round(result.duration_seconds, 1),
                size_bytes=os.path.getsize(out_path),
                separation_seconds=round(self.separation_seconds, 1) if self.separation_seconds else None,
                render_seconds=round(render_seconds, 1),
                worker_seconds=round(time.monotonic() - self.started, 1),
                ready_at=_now_iso(),
            )
            self.ok = True
            self.job_log.info(
                f"Quick version ready: tier={result.lyrics_tier} lines={result.line_count} "
                f"render={render_seconds:.1f}s total={time.monotonic() - self.started:.1f}s"
            )
        except Exception as exc:
            self.ok = False
            logger.warning(f"[job:{self.job_id}] Quick version render failed (non-fatal): {exc}", exc_info=True)
            try:
                self.job_log.warning(f"Quick version render failed (non-fatal): {exc}")
            except Exception:
                pass
            set_quick_state(self.job_manager, self.job_id, STATUS_FAILED, error=str(exc)[:500])


def set_quick_state(job_manager: Any, job_id: str, status: str, **extra: Any) -> None:
    """Write ``state_data.quick_version`` (never raises)."""
    value: Dict[str, Any] = {"status": status, "updated_at": _now_iso()}
    value.update({k: v for k, v in extra.items() if v is not None})
    try:
        job_manager.update_state_data(job_id, STATE_KEY, value)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"[job:{job_id}] Could not record quick_version state: {exc}")


def start_quick_version(
    job: Any, audio_path: str, workdir: str, model_dir: Optional[str],
    job_manager: Any, storage: Any, job_log: Any = None,
    separator_factory: Optional[Callable[..., Any]] = None,
) -> Optional[QuickVersionRender]:
    """Run the quick separation (blocking, GPU) and start the render thread.

    Returns the running render (join it before the worker exits) or None when
    skipped/failed. Never raises.
    """
    job_id = job.job_id
    try:
        if not should_build_quick_version(job):
            return None
        started = time.monotonic()
        set_quick_state(job_manager, job_id, STATUS_SEPARATING, started_at=_now_iso())
        inst, vocals = separate_quick(audio_path, workdir, model_dir, separator_factory=separator_factory)
        sep_seconds = time.monotonic() - started
        if job_log:
            job_log.info(f"Quick separation done in {sep_seconds:.1f}s")
        return QuickVersionRender(
            job_id, job.artist, job.title, inst, vocals, workdir, job_manager, storage, job_log,
            started_monotonic=started, separation_seconds=sep_seconds,
        ).start()
    except Exception as exc:
        logger.warning(f"[job:{job_id}] Quick version skipped after error (non-fatal): {exc}", exc_info=True)
        if job_log:
            try:
                job_log.warning(f"Quick version failed (non-fatal): {exc}")
            except Exception:
                pass
        set_quick_state(job_manager, job_id, STATUS_FAILED, error=str(exc)[:500])
        return None
